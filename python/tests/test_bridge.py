"""The dev bridge, end to end on Linux: a real BridgeServer over the fake _skyrim, driven by the real remote
transport and by raw sockets for everything a well-behaved client would never send."""
import json
import logging
import socket
import struct
import threading
import time

import pytest

from support import load_fixture, wait_until
from skyrim import _framing
from skyrim import _protocol_generated as g
from skyrim import errors
from skyrim.bridge import nesting_within, serve
from skyrim.discovery import find_session
from skyrim.remote import connect_remote

INFO = {"plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1}


@pytest.fixture
def start(fake, tmp_path):
    """start(**serve_kwargs) -> a running bridge over `fake` on a free port; all are closed afterwards."""
    started = []

    def _start(**kw):
        kw.setdefault("welcome_info", INFO)
        kw.setdefault("session_file", tmp_path / "session.json")
        b = serve(fake, port=0, **kw)
        started.append(b)
        return b

    yield _start
    for b in started:
        b.close()


@pytest.fixture
def bridge(start):
    return start()


def remote(b, **kw):
    return connect_remote(port=b.port, token=b.token, **kw)


def raw(b, hello=None):
    """A handshaken raw socket (or, with `hello`, one that sent that hello and read the answer)."""
    s = socket.create_connection(("127.0.0.1", b.port), timeout=5)
    s.sendall(_framing.encode(hello or {"type": "hello", "protocol": [1, 0], "token": b.token, "client": "raw"}))
    first = _framing.read_frame(s, time.monotonic() + 5)
    if hello is None:
        assert first["type"] == "welcome", first
        return s
    return s, first


def send(s, req_id, function, *a, **args):
    s.sendall(_framing.encode({"type": "request", "id": req_id, "op": "papyrus.call_static",
                               "args": {"script": "Fake", "function": function, "args": list(a), **args}}))


def recv(s, timeout=5.0):
    return _framing.read_frame(s, time.monotonic() + timeout)


def closed_by_server(s, timeout=3.0):
    """True once the server closed `s` (EOF or reset) within `timeout`; data it sends first is skipped."""
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            s.settimeout(max(0.01, deadline - time.monotonic()))
            if not s.recv(65536):
                return True
    except (ConnectionResetError, BrokenPipeError):
        return True
    except TimeoutError:
        return False
    return False


# -- the happy path ----------------------------------------------------------------------------------------
def test_calls_batches_and_state_go_through_the_bridge(bridge, fake):
    with remote(bridge) as game:
        assert game.call("Fake", "Echo", 5) == 5
        out = game.batch().call("Fake", "Echo", "a").call("Fake", "Fail", "not_found", "gone").run(stop_on_error=False)
        assert out[0] == "a" and isinstance(out[1], errors.NotFound)
        assert game.state()["backend"] == "fake"
    assert all(r["type"] == "request" for r in fake.requests)


def test_the_welcome_and_the_session_file(bridge, tmp_path):
    with connect_remote(tmp_path / "session.json") as game:  # discovery reads the session file
        w = game.welcome
    assert w["capabilities"] == list(g.OPS)
    assert w["protocol"] == list(g.PROTOCOL_VERSION)
    assert {k: w[k] for k in INFO} == INFO
    s = find_session(tmp_path / "session.json", env={})
    assert (s.port, s.token) == (bridge.port, bridge.token)
    assert len(bridge.token) == g.TOKEN_LENGTH


def test_welcome_info_may_be_a_function_for_the_current_epoch(start):
    epoch = {"n": 1}
    b = start(welcome_info=lambda: {**INFO, "epoch": epoch["n"]})
    epoch["n"] = 4
    with remote(b) as game:
        assert game.epoch == 4


def test_tcp_nodelay_on_the_server_side(bridge):
    s = raw(bridge)
    wait_until(lambda: bridge.connections() == 1)
    conn = next(iter(bridge._conns))
    assert conn.sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0
    s.close()


# -- dispatcher answers pass through unchanged -------------------------------------------------------------
def test_not_ready_and_state_while_loading(bridge, fake):
    fake._fake_set_not_ready("loading")
    with remote(bridge) as game:
        with pytest.raises(errors.NotReady) as ei:
            game.call("Fake", "Echo", 1)
        assert ei.value.detail["reason"] == "loading"
        assert game.state()["loading"] is True


def test_a_call_past_its_timeout_is_timeout_and_nothing_stays_in_flight(bridge, fake):
    with remote(bridge) as game:
        with pytest.raises(errors.Timeout):
            game.call("Fake", "Never", timeout=0.2)
    wait_until(lambda: fake.in_flight() == 0, what="the timed-out call to leave the fake")


# -- per-connection rules ----------------------------------------------------------------------------------
def test_busy_above_the_per_connection_cap(start, fake):
    b = start(max_inflight_per_connection=2)
    s = raw(b)
    send(s, 1, "Never")
    send(s, 2, "Never")
    send(s, 3, "Never")
    reply = recv(s)
    assert (reply["id"], reply["ok"], reply["error"]["code"]) == (3, False, "busy")
    with remote(b) as other:  # the cap is per connection
        assert other.call("Fake", "Echo", 1) == 1
    s.close()


def test_a_request_id_already_in_flight_is_bad_request(bridge, fake):
    s = raw(bridge)
    send(s, 7, "Never")
    send(s, 7, "Echo", 1)
    reply = recv(s)
    assert (reply["id"], reply["error"]["code"]) == (7, "bad_request")
    assert "already in flight" in reply["error"]["message"]
    send(s, 8, "Echo", 2)
    assert recv(s)["result"] == 2  # the connection is still fine
    s.close()


def test_an_id_can_be_reused_once_its_answer_was_sent(bridge):
    s = raw(bridge)
    for v in (1, 2):
        send(s, 5, "Echo", v)
        assert recv(s) == {"type": "response", "id": 5, "ok": True, "result": v}
    s.close()


def test_a_disconnect_cancels_the_connections_calls(bridge, fake):
    s = raw(bridge)
    for i in range(3):
        send(s, i + 1, "Never")
    wait_until(lambda: fake.in_flight() == 3, what="three calls in flight")
    s.close()
    wait_until(lambda: fake.in_flight() == 0, what="the calls to be cancelled")
    wait_until(lambda: bridge.connections() == 0, what="the connection to be forgotten")


@pytest.mark.parametrize("payload", [
    struct.pack("<I", g.MAX_FRAME_BYTES + 1),                                  # an oversized header
    struct.pack("<I", 9) + b"not json!",                                       # not JSON
    struct.pack("<I", 2) + b"[]",                                              # not an object
    struct.pack("<I", 3) + b"\xff\xfe{",                                       # not UTF-8
    (lambda t: struct.pack("<I", len(t)) + t)(b'{"a":' * 65 + b"1" + b"}" * 65),  # nested 65 deep
], ids=["oversize", "not-json", "not-object", "not-utf8", "too-deep"])
def test_a_broken_frame_closes_only_that_connection(bridge, fake, payload):
    with remote(bridge) as bystander:
        s = raw(bridge)
        s.sendall(payload)
        assert closed_by_server(s)
        assert bystander.call("Fake", "Echo", "still here") == "still here"
    wait_until(lambda: bridge.connections() == 0)


def test_depth_64_is_allowed_and_brackets_in_strings_do_not_count():
    assert nesting_within("[" * 64 + "]" * 64, 64)
    assert not nesting_within("[" * 65 + "]" * 65, 64)
    assert nesting_within('{"s":"' + "[" * 500 + '"}', 64)
    assert nesting_within('["\\"[[[[", 1]', 1)


def test_a_hostile_hello_is_refused_at_once_and_python_keeps_running(bridge):
    # An unterminated string of escaped quotes, under the pre-auth limit: a backtracking scan of it would hold the GIL
    # for seconds before the token is even checked, freezing every Python thread in the game.
    with remote(bridge) as bystander:
        text = ('"' + '\\"' * 32000).encode()
        s = socket.create_connection(("127.0.0.1", bridge.port), timeout=5)
        t0 = time.monotonic()
        s.sendall(struct.pack("<I", len(text)) + text)
        assert closed_by_server(s)
        assert time.monotonic() - t0 < 0.2
        assert bystander.call("Fake", "Echo", "still here") == "still here"


def test_a_slow_call_does_not_hold_up_the_next_ones_on_the_same_connection(bridge, fake):
    s = raw(bridge)
    send(s, 1, "Never", timeout_ms=10000)
    wait_until(lambda: fake.in_flight() == 1, what="the slow call")
    t0 = time.monotonic()
    for i in range(2, 22):
        send(s, i, "Echo", i)
        assert recv(s)["id"] == i
    assert time.monotonic() - t0 < 0.5  # 20 calls; a 50 ms poll behind the slow call would take about 1 s
    s.close()


def test_an_accept_error_is_logged_and_the_bridge_keeps_accepting(bridge, caplog):
    real = bridge._lsock

    class FailsOnce:  # socket objects cannot be monkeypatched, so the listener is wrapped
        failed = False

        def accept(self):
            if not FailsOnce.failed:
                FailsOnce.failed = True
                raise ConnectionResetError("a client reset before it was accepted")
            return real.accept()

        def __getattr__(self, name):
            return getattr(real, name)

    bridge._lsock = FailsOnce()
    with remote(bridge) as g:  # served by the accept already waiting; the next accept() fails once
        assert g.call("Fake", "Echo", 1) == 1
    with remote(bridge, connect_timeout=2) as g:
        assert g.call("Fake", "Echo", 2) == 2
    assert FailsOnce.failed
    assert "accept failed" in caplog.text


def test_a_client_that_stops_reading_is_closed(start, fake, caplog):
    caplog.set_level(logging.INFO, logger="skyrim.bridge")
    b = start(max_queued_bytes=256 * 1024)
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    s.connect(("127.0.0.1", b.port))
    s.sendall(_framing.encode({"type": "hello", "protocol": [1, 0], "token": b.token, "client": "lazy"}))
    big = "x" * (64 * 1024)
    try:
        for i in range(200):  # 12.5 MiB of replies: far more than the socket buffers and the cap
            send(s, i + 1, "Echo", big)
    except OSError:
        pass  # the bridge may close us while we are still sending: that is the point
    wait_until(lambda: b.connections() == 0, timeout=15, what="the stuck reader to be closed")
    assert "stopped reading" in caplog.text
    with remote(b) as game:
        assert game.call("Fake", "Echo", 1) == 1
    s.close()


def test_eight_clients_at_once_each_get_their_own_answers(bridge):
    failures = []

    def client(n):
        try:
            with remote(bridge) as game:
                for i in range(50):
                    v = n * 1000 + i
                    if game.call("Fake", "Echo", v) != v:
                        failures.append((n, i))
        except Exception as e:  # noqa: BLE001
            failures.append((n, repr(e)))

    threads = [threading.Thread(target=client, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads)
    assert failures == []


# -- the handshake -----------------------------------------------------------------------------------------
HELLO = load_fixture("hello.json")


@pytest.mark.parametrize("case", HELLO["check"], ids=lambda c: c["name"])
def test_token_then_major(start, case):
    b = start(token=case["expected_token"])
    s, first = raw(b, hello=case["hello"])
    if case["result"] is None:
        assert first["type"] == "welcome"
    else:
        assert (first["type"], first["code"]) == ("handshake_error", case["result"])
        assert closed_by_server(s)
    s.close()


@pytest.mark.parametrize("case", HELLO["invalid"], ids=lambda c: json.dumps(c["message"])[:40])
def test_a_malformed_hello_is_bad_request(bridge, case):
    s, first = raw(bridge, hello=case["message"])
    assert (first["type"], first["code"]) == ("handshake_error", case["error"])
    assert closed_by_server(s)
    s.close()


def test_wrong_token_through_the_client_is_unauthorized(bridge):
    with pytest.raises(errors.Unauthorized):
        connect_remote(port=bridge.port, token="f" * 32)


def test_no_hello_in_time_is_closed(start):
    b = start(hello_timeout=0.3)
    s = socket.create_connection(("127.0.0.1", b.port))
    t0 = time.monotonic()
    assert closed_by_server(s, timeout=3)
    assert time.monotonic() - t0 < 2
    s.close()


def test_a_first_frame_over_the_pre_auth_limit_is_closed_without_a_reply(bridge):
    s = socket.create_connection(("127.0.0.1", bridge.port))
    s.sendall(struct.pack("<I", g.PRE_AUTH_FRAME_BYTES + 1))
    s.settimeout(3)
    assert s.recv(65536) == b""  # EOF, no handshake_error
    s.close()


def test_the_connection_cap(start):
    b = start(max_connections=2)
    a, c = raw(b), raw(b)
    extra = socket.create_connection(("127.0.0.1", b.port))
    assert closed_by_server(extra)
    a.close()
    wait_until(lambda: b.connections() == 1)
    with remote(b) as game:
        assert game.call("Fake", "Echo", 1) == 1
    c.close()
    extra.close()


# -- starting and stopping ---------------------------------------------------------------------------------
def test_close_disconnects_clients_and_marks_the_session_file_inactive(start, tmp_path):
    b = start()
    game = remote(b)
    b.close()
    with pytest.raises(errors.ConnectionLost):
        game.call("Fake", "Echo", 1)
    with pytest.raises(errors.NotRunning, match="not running: the bridge was closed"):
        find_session(tmp_path / "session.json", env={})


def test_a_port_in_use_raises_and_writes_an_inactive_session_file(bridge, fake, tmp_path):
    other = tmp_path / "other.json"
    with pytest.raises(OSError):
        serve(fake, port=bridge.port, session_file=other, welcome_info=INFO)
    with pytest.raises(errors.NotRunning, match="could not listen"):
        find_session(other, env={})


@pytest.mark.parametrize("kw", [{"host": "0.0.0.0"}, {"token": "ABC"}, {"token": "0" * 31}])
def test_non_loopback_hosts_and_bad_tokens_are_refused(fake, kw):
    with pytest.raises(ValueError):
        serve(fake, port=0, welcome_info=INFO, **kw)
