# Task 23: The dev bridge server

Adapted from: branch `plan-out-of-process`, task 16 (`task-16-loopback-server-io-thread-handshake-session-file.md`): the server rules of its C++ Winsock IO thread (loopback only, handshake order, hello timeout, pre-auth limit, connection cap, frame checks, the drain after a handshake error, the stuck-reader cap, the session file), now in Python on top of `_skyrim`.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 4.2). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 22 (`connect_remote`, `find_session`, `write_session_file`, `write_inactive_session_file`) and Task 21 (`fake_skyrim.py`, the `fake` fixture). Linux only: the tests run a real bridge over the fake `_skyrim` and drive it with the real remote transport, so the whole dev path is tested end to end without the game.

**What the bridge is.** A Python server that runs inside the game (Task 24 starts it when `bridge_enabled` is set) and lets another process use the same API as an in-process script. It forwards each request **text unchanged** to `backend.submit` and writes each response text back unchanged in a frame, so the C++ request parser and the Dispatcher's rules (not ready, timeouts, load cancellation, the global in-flight cap) apply exactly as they do in-process. What only a socket needs is done here: the handshake and token, framing limits, a per-connection in-flight cap (`busy`), duplicate request ids on one connection (`bad_request`; `PROTOCOL.md` puts that check here), cancelling a connection's calls when it goes away, and closing a client that stops reading. Three threads per connection: a reader (handshake, then requests), a completion thread (`backend.wait_any` over that connection's tickets, 5 ms at a time while it has any, with the GIL released in the real module; a request submitted meanwhile is not in that wait's list, so a longer poll would hold every new call behind a slow one) and a writer (the only thread that sends after the welcome). The Dispatcher's reaper enforces each request's `timeout_ms`; the bridge never times a call out itself.

**Files:**
- Create: `python/src/skyrim/bridge.py`, `python/tests/test_bridge.py`

**Interfaces:**
- Consumes: Task 22 (`skyrim.discovery.Session`, `write_session_file`, `write_inactive_session_file`, `find_session`; `skyrim.remote.connect_remote` in the tests), Task 21 (the `fake` fixture: a `FakeSkyrim` with `submit`, `wait_any`, `cancel`, `in_flight`, `_fake_set_not_ready`), Task 20 (`_framing` in the tests), `skyrim._protocol_generated` (Task 12: `PROTOCOL_VERSION`, `PROTOCOL_MAJOR`, `PROTOCOL_MINOR`, `OPS`, `HOST`, `TOKEN_LENGTH`, `INT_MAX`, `MAX_FRAME_BYTES`, `PRE_AUTH_FRAME_BYTES`, `MAX_JSON_DEPTH`, `MAX_CONNECTIONS`, `HELLO_TIMEOUT_MS`, `MAX_QUEUED_BYTES_PER_CONNECTION`, `MAX_CLIENT_NAME_BYTES`, `CONFIG_KEYS`, `CODE_*`), the golden `hello.json` (Task 11: its `invalid` and `check` cases are replayed against the bridge).
- Produces (`skyrim.bridge`):
  - `serve(backend, *, host=HOST, port=45977, token=None, session_file=None, welcome_info, max_connections=MAX_CONNECTIONS, max_inflight_per_connection=256, hello_timeout=HELLO_TIMEOUT_MS / 1000, max_queued_bytes=MAX_QUEUED_BYTES_PER_CONNECTION) -> BridgeServer`. `backend` is anything with `submit(text) -> int`, `wait_any(tickets, timeout) -> list[tuple[int, str]]` and `cancel(ticket)`. `welcome_info` is a dict, or a function returning one, with `plugin_version`, `game_version`, `runtime` and `epoch` (a function gives each client the current epoch). Port 0 picks a free port. A token is `secrets.token_hex(16)` unless given; a given token must be 32 lowercase hex characters and `host` must be a loopback address (`ValueError` otherwise). With `session_file`, the active session file is written once listening; if the bridge cannot listen, an inactive one with the reason is written and the `OSError` is raised. The last two keyword arguments exist for tests and are not in the index Contract.
  - `class BridgeServer` with `.host`, `.port`, `.token`, `.connections() -> int`, `.close()` (stop accepting, close every connection and cancel its calls, mark the session file inactive with reason `"the bridge was closed"`), context manager.
  - `check_hello(hello: dict, token: str) -> tuple[str | None, str]` (error code or `None`, and a message) and `nesting_within(text: str, max_depth: int) -> bool` (a raw-text scan; brackets inside strings do not count).
  - Rules: the welcome is `{**welcome_info, "type": "welcome", "protocol": [1, 0], "capabilities": <op names from OPS>}`; a hello is checked for shape (`bad_request`), then the token in constant time (`unauthorized`), then the protocol major (`protocol_mismatch`), and a refused client gets `handshake_error`, a half-close and a drain of up to 0.5 s; the first frame must arrive within the hello timeout and be at most `PRE_AUTH_FRAME_BYTES`; after the welcome a frame over `MAX_FRAME_BYTES`, not UTF-8, not a JSON object or nested deeper than `MAX_JSON_DEPTH` closes that connection only; a request id already in flight on the connection is `bad_request`, more than `max_inflight_per_connection` in flight is `busy` (both answered by the bridge in the plugin's response shape, with the request's id or 0); replies waiting to be sent over `max_queued_bytes` close the connection; every socket has `TCP_NODELAY`; non-loopback peers and connections over `max_connections` are closed at accept; an error from `accept()` itself (a client that reset before it was accepted, `EMFILE`) is logged and accepting goes on after 50 ms. The depth scan (`nesting_within`) is linear in the frame size (a possessive pattern), so a hostile pre-auth frame cannot hold the GIL. The bridge logs to the `skyrim.bridge` logger (Task 24 sends it to `python.log`).

- [ ] **Step 1: Write the failing tests**

`python/tests/test_bridge.py`:
```python
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
from skyrim import bridge as bridge_mod
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
        assert time.monotonic() - t0 < 1.0  # a backtracking scan takes seconds
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


def test_a_connection_that_fails_to_start_gives_its_slot_back(start, monkeypatch):
    b = start(max_connections=2)
    real = bridge_mod._Conn.start
    fails = {"n": 2}

    def flaky(self):
        if fails["n"]:
            fails["n"] -= 1
            raise RuntimeError("can't start new thread")
        real(self)

    monkeypatch.setattr(bridge_mod._Conn, "start", flaky)
    for _ in range(2):  # more failures than slots: a leak would refuse everyone from here on
        s = socket.create_connection(("127.0.0.1", b.port), timeout=5)
        assert closed_by_server(s)
        s.close()
    wait_until(lambda: b.connections() == 0, what="the failed connections to give their slots back")
    with remote(b) as game:
        assert game.call("Fake", "Echo", 1) == 1


def test_a_failure_after_listening_closes_the_listener_and_says_why(fake, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, so nothing can be created under it")
    with pytest.raises(OSError):  # the session file cannot be written; the inactive one cannot either
        serve(fake, port=port, session_file=blocker / "session.json", welcome_info=INFO)
    with serve(fake, port=port, welcome_info=INFO) as b:  # the listener was closed, so the port is free again
        assert b.port == port

    def broken():
        raise KeyError("epoch")

    log_file = tmp_path / "s.json"
    with pytest.raises(KeyError):
        serve(fake, port=0, session_file=log_file, welcome_info=broken)
    with pytest.raises(errors.NotRunning, match="could not start"):
        find_session(log_file, env={})
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd python && uv run --python 3.14 pytest -q tests/test_bridge.py`
Expected: FAIL with `1 error during collection`: `ModuleNotFoundError: No module named 'skyrim.bridge'`.

- [ ] **Step 3: Implement the bridge**

`python/src/skyrim/bridge.py`:
```python
"""The dev bridge: the same requests as in-process, from another process, over loopback TCP.

It runs inside the game (skyrim._host starts it when bridge_enabled is set) and forwards each request text
unchanged to `backend` (the _skyrim module; a fake in tests); the response text goes back unchanged in a frame.
Rules (protocol/PROTOCOL.md, "Dev bridge transport" and "Handshake"):
- 127.0.0.1 only; a non-loopback peer is closed at accept; at most `max_connections` connections.
- The first frame must be a valid hello within the hello timeout, and at most PRE_AUTH_FRAME_BYTES long.
  A malformed hello is `bad_request`, then the token is checked in constant time (`unauthorized`), then
  the protocol major (`protocol_mismatch`); the server answers handshake_error and closes.
- After the welcome, a frame over MAX_FRAME_BYTES, not UTF-8, not a JSON object, or nested deeper than
  MAX_JSON_DEPTH closes that connection only. A request whose id is already in flight on the connection is
  `bad_request`; more than `max_inflight_per_connection` in flight is `busy`; both answered here, in the
  same response shape the plugin uses.
- Replies waiting to be sent above `max_queued_bytes` mean the client stopped reading: the connection closes.
- When a connection ends, every request it still has in flight is cancelled.
Three threads per connection: a reader (handshake, then requests), a completion thread (wait_any on the
connection's tickets, 5 ms at a time, so a request submitted meanwhile is picked up quickly even when the
others are slow) and a writer (the only thread that sends after the welcome). The Dispatcher's reaper enforces
each request's timeout_ms: the bridge never times a call out itself. An error from accept() (a client that
reset before it was accepted, no descriptors left) is logged and the bridge keeps accepting."""
import collections
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import struct
import threading
import time

from . import _protocol_generated as _g
from .discovery import Session, write_inactive_session_file, write_session_file

log = logging.getLogger("skyrim.bridge")

_U64_MAX = 2**64 - 1
_POLL_S = 0.005  # how long the completion thread waits on its tickets per round (it releases the GIL meanwhile)
_IDLE_S = 0.05  # how long it sleeps with no tickets (a new ticket wakes it at once)
_ACCEPT_RETRY_S = 0.05  # after a failed accept()
_DRAIN_S = 0.5  # after a handshake_error, how long to read and discard so the error is not lost to a reset
_TOKEN = re.compile(r"[0-9a-f]{%d}" % _g.TOKEN_LENGTH)
# Strings (skipped whole) and brackets. Possessive, so the scan is linear: an unterminated string is consumed to the
# end in one pass instead of being retried from every later quote (a pre-auth frame could otherwise hold the GIL
# for seconds).
_STRUCTURE = re.compile(r'"(?:[^"\\]++|\\.)*+"?|[\[\]{}]', re.S)


class _Violation(Exception):
    """The peer broke the framing rules: close the connection without a reply."""


class _Ended(Exception):
    """The peer went away (EOF, reset) or never said hello."""


def nesting_within(text: str, max_depth: int) -> bool:
    """True if no array/object in `text` is nested deeper than `max_depth`; brackets inside strings do
    not count. A raw-text scan, so a hostile frame is refused before json.loads can recurse on it."""
    depth = 0
    for m in _STRUCTURE.finditer(text):
        c = m.group()
        if c in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif c in "]}":
            depth -= 1
    return True


def _recv_exact(sock: socket.socket, n: int, deadline: float | None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline passed")
            sock.settimeout(remaining)
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise _Ended("the client closed the connection")
        buf += chunk
    return bytes(buf)


def _read_text(sock: socket.socket, limit: int, deadline: float | None = None) -> str:
    (n,) = struct.unpack("<I", _recv_exact(sock, 4, deadline))
    if n > limit:
        raise _Violation(f"a frame of {n} bytes is over the limit of {limit}")
    try:
        return _recv_exact(sock, n, deadline).decode("utf-8")
    except UnicodeDecodeError:
        raise _Violation("a frame is not UTF-8") from None


def _parse_object(text: str) -> dict:
    if not nesting_within(text, _g.MAX_JSON_DEPTH):
        raise _Violation(f"a frame is nested deeper than {_g.MAX_JSON_DEPTH} levels")
    try:
        obj = json.loads(text)
    except ValueError:
        raise _Violation("a frame is not JSON") from None
    if not isinstance(obj, dict):
        raise _Violation("a frame is not a JSON object")
    return obj


def _frame(text: str) -> bytes:
    body = text.encode("utf-8")
    return struct.pack("<I", len(body)) + body


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _error_text(req_id: int, code: str, message: str) -> str:
    return _dumps({"type": "response", "id": req_id, "ok": False,
                   "error": {"code": code, "message": message, "detail": {}}})


def _request_id(obj: dict) -> int | None:
    i = obj.get("id")
    return i if type(i) is int and 0 <= i <= _U64_MAX else None


def check_hello(hello: dict, token: str) -> tuple[str | None, str]:
    """(error code or None, message), in the protocol's order: shape, then token, then protocol major."""
    if hello.get("type") != "hello":
        return _g.CODE_BAD_REQUEST, "the first message must be a hello"
    proto = hello.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2
            and all(type(n) is int and 0 <= n <= _g.INT_MAX for n in proto)):
        return _g.CODE_BAD_REQUEST, "protocol must be [major, minor], two integers"
    their = hello.get("token")
    if not (isinstance(their, str) and _TOKEN.fullmatch(their)):
        return _g.CODE_BAD_REQUEST, f"token must be {_g.TOKEN_LENGTH} lowercase hex characters"
    if "client" in hello and not isinstance(hello["client"], str):
        return _g.CODE_BAD_REQUEST, "client must be a string"
    if not hmac.compare_digest(their.encode("ascii"), token.encode("ascii")):
        return _g.CODE_UNAUTHORIZED, "wrong token (is the session file from an earlier game run?)"
    if proto[0] != _g.PROTOCOL_MAJOR:
        return _g.CODE_PROTOCOL_MISMATCH, (f"the client speaks protocol {proto[0]}.{proto[1]}, "
                                           f"the game {_g.PROTOCOL_MAJOR}.{_g.PROTOCOL_MINOR}")
    return None, ""


class _Conn:
    def __init__(self, server: "BridgeServer", sock: socket.socket, peer):
        self.server = server
        self.sock = sock
        self.peer = peer
        self.client = "?"
        self.cond = threading.Condition()
        self.tickets: dict[int, int | None] = {}  # ticket -> request id (None when the request had no usable id)
        self.ids: set[int] = set()
        self.out: collections.deque[bytes] = collections.deque()
        self.out_bytes = 0
        self.closed = False

    def start(self) -> None:
        threading.Thread(target=self._read_loop, name=f"bridge-read-{self.peer[1]}", daemon=True).start()

    # -- reader ---------------------------------------------------------------------------------------
    def _read_loop(self) -> None:
        reason = "the client closed the connection"
        try:
            if not self._handshake():
                return
            self.sock.settimeout(None)
            threading.Thread(target=self._write_loop, name=f"bridge-write-{self.peer[1]}", daemon=True).start()
            threading.Thread(target=self._complete_loop, name=f"bridge-done-{self.peer[1]}", daemon=True).start()
            while True:
                text = _read_text(self.sock, _g.MAX_FRAME_BYTES)
                self._on_request(text, _parse_object(text))
        except (_Ended, _Violation) as e:
            reason = str(e)
        except TimeoutError:
            reason = f"no hello within {self.server.hello_timeout:g} s"
        except OSError as e:
            reason = str(e) or type(e).__name__
        except Exception:  # noqa: BLE001 - a bug here must close one connection, never the bridge
            log.exception("bridge: unexpected error on a connection from %s", self.client)
            reason = "internal error"
        finally:
            self.close(reason)

    def _handshake(self) -> bool:
        deadline = time.monotonic() + self.server.hello_timeout
        hello = _parse_object(_read_text(self.sock, _g.PRE_AUTH_FRAME_BYTES, deadline))
        code, message = check_hello(hello, self.server.token)
        if code is not None:
            self._refuse(code, message)
            return False
        if isinstance(hello.get("client"), str):
            self.client = hello["client"].encode("utf-8")[:_g.MAX_CLIENT_NAME_BYTES].decode("utf-8", "ignore")
        info = self.server.welcome_info() if callable(self.server.welcome_info) else self.server.welcome_info
        welcome = {**info, "type": "welcome", "protocol": list(_g.PROTOCOL_VERSION), "capabilities": list(_g.OPS)}
        self.sock.sendall(_frame(_dumps(welcome)))
        log.info("bridge: %s connected", self.client)
        return True

    def _refuse(self, code: str, message: str) -> None:
        """Answer handshake_error, stop sending, and drain briefly so the error is read, not reset away."""
        try:
            self.sock.sendall(_frame(_dumps({"type": "handshake_error", "code": code, "message": message})))
            self.sock.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + _DRAIN_S
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                if not self.sock.recv(65536):
                    break
        except OSError:
            pass
        log.info("bridge: refused a client: %s", code)

    def _on_request(self, text: str, obj: dict) -> None:
        rid = _request_id(obj)
        refusal = None
        with self.cond:
            if self.closed:
                return
            if rid is not None and rid in self.ids:
                refusal = _error_text(rid, _g.CODE_BAD_REQUEST, f"request id {rid} is already in flight on this connection")
            elif len(self.tickets) >= self.server.max_inflight_per_connection:
                refusal = _error_text(rid or 0, _g.CODE_BUSY,
                                      f"{self.server.max_inflight_per_connection} requests are already in flight on this connection")
            elif rid is not None:
                self.ids.add(rid)
        if refusal is not None:
            self._enqueue(refusal)
            return
        ticket = self.server.backend.submit(text)
        with self.cond:
            if not self.closed:
                self.tickets[ticket] = rid
                self.cond.notify_all()
                return
        self.server.backend.cancel(ticket)  # the connection closed while the request was being submitted

    # -- completion -----------------------------------------------------------------------------------
    def _complete_loop(self) -> None:
        backend = self.server.backend
        try:
            while True:
                with self.cond:
                    while not self.closed and not self.tickets:
                        self.cond.wait(_IDLE_S)
                    if self.closed:
                        return
                    tickets = list(self.tickets)
                for ticket, text in backend.wait_any(tickets, _POLL_S):
                    with self.cond:
                        if ticket not in self.tickets:
                            continue
                        rid = self.tickets.pop(ticket)
                        self.ids.discard(rid)
                    self._enqueue(text)
        except Exception:  # noqa: BLE001
            log.exception("bridge: waiting for results failed")
            self.close("internal error")

    # -- writer ---------------------------------------------------------------------------------------
    def _enqueue(self, text: str) -> None:
        data = _frame(text)
        if len(data) - 4 > _g.MAX_FRAME_BYTES:
            try:
                rid = _request_id(json.loads(text)) or 0
            except ValueError:
                rid = 0
            data = _frame(_error_text(rid, _g.CODE_INTERNAL, "the response is over the frame limit"))
        with self.cond:
            if self.closed:
                return
            if self.out_bytes + len(data) <= self.server.max_queued_bytes:
                self.out.append(data)
                self.out_bytes += len(data)
                self.cond.notify_all()
                return
        self.close(f"the client stopped reading: more than {self.server.max_queued_bytes} bytes of replies are waiting")

    def _write_loop(self) -> None:
        while True:
            with self.cond:
                while not self.closed and not self.out:
                    self.cond.wait()
                if self.closed:
                    return
                data = self.out.popleft()
            try:
                self.sock.sendall(data)
            except OSError as e:
                self.close(str(e) or type(e).__name__)
                return
            with self.cond:
                self.out_bytes -= len(data)

    # -- end ------------------------------------------------------------------------------------------
    def close(self, reason: str) -> None:
        with self.cond:
            if self.closed:
                return
            self.closed = True
            tickets = list(self.tickets)
            self.tickets.clear()
            self.ids.clear()
            self.out.clear()
            self.cond.notify_all()
        for ticket in tickets:
            try:
                self.server.backend.cancel(ticket)
            except Exception:  # noqa: BLE001
                log.exception("bridge: cancel failed")
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.server._forget(self)
        log.info("bridge: %s disconnected (%s); %d call(s) cancelled", self.client, reason, len(tickets))


class BridgeServer:
    """A listening dev bridge. Use serve() to start one."""

    def __init__(self, backend, *, host: str, port: int, token: str | None, session_file, welcome_info,
                 max_connections: int, max_inflight_per_connection: int, hello_timeout: float,
                 max_queued_bytes: int):
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError(f"the dev bridge binds loopback only, not {host!r}")
        if token is None:
            token = secrets.token_hex(_g.TOKEN_LENGTH // 2)
        if not (isinstance(token, str) and _TOKEN.fullmatch(token)):
            raise ValueError(f"token must be {_g.TOKEN_LENGTH} lowercase hex characters")
        self.backend = backend
        self.token = token
        self.session_file = session_file
        self.welcome_info = welcome_info
        self.max_connections = max_connections
        self.max_inflight_per_connection = max_inflight_per_connection
        self.hello_timeout = hello_timeout
        self.max_queued_bytes = max_queued_bytes
        self._conns: set[_Conn] = set()
        self._lock = threading.Lock()
        self._closing = False
        self._lsock = socket.socket()
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: nobody else may bind our port
                self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._lsock.bind((host, port))
            self._lsock.listen()
        except OSError as e:
            self._lsock.close()
            self._mark_inactive(f"the bridge could not listen on {host}:{port}: {e}")
            raise
        try:
            self.host = host
            self.port = self._lsock.getsockname()[1]
            if session_file:
                info = welcome_info() if callable(welcome_info) else welcome_info
                write_session_file(session_file, Session(host, self.port, token, os.getpid(), info.get("plugin_version")))
            self._accepter = threading.Thread(target=self._accept_loop, name="bridge-accept", daemon=True)
            self._accepter.start()
        except BaseException as e:  # listening worked but the rest of the start did not: do not keep the port
            self._closing = True
            self._lsock.close()
            self._mark_inactive(f"the bridge could not start after listening: {e!r}")
            raise
        log.info("bridge: listening on %s:%d", host, self.port)

    def _mark_inactive(self, reason: str) -> None:
        """Record why there is no bridge. A failure to write the record is logged, never raised: it must not hide the
        error that is being reported."""
        if not self.session_file:
            return
        try:
            write_inactive_session_file(self.session_file, reason, pid=os.getpid())
        except Exception:  # noqa: BLE001
            log.exception("bridge: could not write the inactive session file")

    def _accept_loop(self) -> None:
        while True:
            try:
                sock, peer = self._lsock.accept()
            except OSError as e:
                if self._closing:
                    return
                log.warning("bridge: accept failed: %s; still accepting", e)
                time.sleep(_ACCEPT_RETRY_S)
                continue
            if self._closing:
                sock.close()
                return
            conn = None
            try:
                if not ipaddress.ip_address(peer[0]).is_loopback:
                    log.warning("bridge: refused a non-loopback peer %s", peer[0])
                    sock.close()
                    continue
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    full = len(self._conns) >= self.max_connections
                    if not full:
                        conn = _Conn(self, sock, peer)
                        self._conns.add(conn)
                if full:
                    log.warning("bridge: %d connections already open; refused one more", self.max_connections)
                    sock.close()
                    continue
                conn.start()
            except Exception:  # noqa: BLE001 - one bad accept must not stop the bridge
                log.exception("bridge: accept failed")
                if conn is not None:
                    self._forget(conn)  # it was registered: give its slot back
                sock.close()

    def _forget(self, conn: _Conn) -> None:
        with self._lock:
            self._conns.discard(conn)

    def connections(self) -> int:
        with self._lock:
            return len(self._conns)

    def close(self) -> None:
        """Stop accepting, close every connection (their calls are cancelled), mark the session file inactive."""
        if self._closing:
            return
        self._closing = True
        try:  # closing a listener does not wake a blocked accept(); a throwaway connection does
            socket.create_connection((self.host, self.port), timeout=1).close()
        except OSError:
            pass
        self._accepter.join(2)
        self._lsock.close()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            c.close("the bridge was closed")
        if self.session_file:
            write_inactive_session_file(self.session_file, "the bridge was closed", pid=os.getpid())
        log.info("bridge: closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve(backend, *, host: str = _g.HOST, port: int = _g.CONFIG_KEYS["port"]["default"], token: str | None = None,
          session_file=None, welcome_info, max_connections: int = _g.MAX_CONNECTIONS,
          max_inflight_per_connection: int = _g.CONFIG_KEYS["max_inflight_per_connection"]["default"],
          hello_timeout: float = _g.HELLO_TIMEOUT_MS / 1000,
          max_queued_bytes: int = _g.MAX_QUEUED_BYTES_PER_CONNECTION) -> BridgeServer:
    """Start a dev bridge over `backend` (anything with submit, wait_any and cancel: the _skyrim module).

    `welcome_info` is a dict, or a function returning one, with plugin_version, game_version, runtime and
    epoch (a function gives every client the current epoch). Port 0 picks a free port (see .port). The
    session file, when given, gets the port and token; if the bridge cannot start, it gets an inactive
    record with the reason instead, and the error is raised."""
    return BridgeServer(backend, host=host, port=port, token=token, session_file=session_file,
                        welcome_info=welcome_info, max_connections=max_connections,
                        max_inflight_per_connection=max_inflight_per_connection,
                        hello_timeout=hello_timeout, max_queued_bytes=max_queued_bytes)
```

- [ ] **Step 4: Run to verify they pass, repeatedly**

Run: `cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q`
Expected: `373 passed` twice, each in about 9 s (Task 22's 273 plus this task's 46; the hello fixtures add one test per `invalid` and `check` entry).

Run the bridge tests five times and once on a single CPU, to catch races:
`for i in 1 2 3 4 5; do uv run --python 3.14 pytest -q tests/test_bridge.py; done; taskset -c 0 uv run --python 3.12 pytest -q tests/test_bridge.py`
Expected: `48 passed` six times, each in about 1 s. No test sleeps for luck: every wait is `wait_until` with a deadline, and the stuck-reader test confirms from the log that the cap, not some other error, closed the connection.

- [ ] **Step 5: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`. `bridge.py` must not spell out a limit, an error code, the token length, the default port or the host: they come from `skyrim._protocol_generated`.

- [ ] **Step 6: Commit**

```bash
git add python/src/skyrim/bridge.py python/tests/test_bridge.py
git commit -m "feat(python): the dev bridge server: handshake, per-connection caps, cancel on disconnect, session file"
```
