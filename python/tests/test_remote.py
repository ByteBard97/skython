"""The remote transport against a fake socket server: the handshake, every way a peer can misbehave, and
the bookkeeping (pending calls, unmatched responses, closed sockets). The Game API on top is Task 21's and is
tested there; the real bridge is Task 23's."""
import inspect
import re
import socket
import sys
import threading
import time

import pytest

from fake_skyrim import FakeSkyrim
from support import TOKEN, FakeServer, err, load_fixture, ok, wait_until
import skyrim
from skyrim import _protocol_generated as g
from skyrim import errors
from skyrim.remote import RemoteTransport, connect_remote


def connect(server, **kw):
    return connect_remote(port=server.port, token=TOKEN, **kw)


def test_handshake_and_call(server):
    with connect(server) as game:
        assert isinstance(game.transport, RemoteTransport)
        assert game.epoch == 1
        r = game.call("Utility", "RandomInt", 1, 6)
    assert r == {"op": "papyrus.call_static", "args": {
        "script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": False}}
    assert server.requests[0]["type"] == "request"


def test_the_socket_has_tcp_nodelay(server):
    with connect(server) as game:
        assert game.transport._sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) != 0


def test_wrong_token_is_unauthorized(server, sockets):
    with pytest.raises(errors.Unauthorized):
        connect_remote(port=server.port, token="ffffffffffffffffffffffffffffffff")
    assert sockets[0].fileno() == -1


def test_protocol_mismatch(sockets):
    with FakeServer(mode="mismatch") as s:
        with pytest.raises(errors.ProtocolMismatch):
            connect_remote(port=s.port, token=TOKEN)
    assert sockets[0].fileno() == -1


def test_welcome_with_another_protocol_major_is_protocol_mismatch(sockets):
    with FakeServer(welcome={"protocol": [2, 0]}) as s:
        with pytest.raises(errors.ProtocolMismatch, match="2.0"):
            connect_remote(port=s.port, token=TOKEN)
    assert sockets[0].fileno() == -1


def test_a_newer_minor_is_accepted():
    with FakeServer(welcome={"protocol": [1, 7]}) as s:
        with connect(s) as game:
            assert game.welcome["protocol"] == [1, 7]


@pytest.mark.parametrize("bad", [None, "1.0", [1], [1, "0"], [True, 0]])
def test_welcome_without_a_usable_protocol_is_a_violation(bad, sockets):
    with FakeServer(welcome={"protocol": bad}) as s:
        with pytest.raises(errors.ProtocolViolation):
            connect_remote(port=s.port, token=TOKEN)
    assert sockets[0].fileno() == -1


def test_a_non_protocol_peer_is_a_violation_and_the_socket_is_closed(sockets):
    with FakeServer(mode="garbage") as s:
        with pytest.raises(errors.ProtocolViolation):
            connect_remote(port=s.port, token=TOKEN)
    assert sockets[0].fileno() == -1


@pytest.mark.parametrize("mode", ["hang", "drip", "partial"])
def test_a_slow_or_stuck_handshake_is_bounded_by_connect_timeout_as_a_whole(mode, sockets):
    # "drip": one byte every 50 ms of a ~150 byte welcome is ~7 s, though each recv alone finishes in 0.5 s
    with FakeServer(mode=mode, drip_interval=0.05) as s:
        t0 = time.monotonic()
        with pytest.raises(errors.NotRunning, match="no welcome"):
            connect_remote(port=s.port, token=TOKEN, connect_timeout=0.5)
        assert time.monotonic() - t0 < 2.5
    assert sockets[0].fileno() == -1


def test_nothing_listening_is_not_running():
    with socket.create_server(("127.0.0.1", 0)) as tmp:
        port = tmp.getsockname()[1]
    with pytest.raises(errors.NotRunning):
        connect_remote(port=port, token=TOKEN, connect_timeout=0.5)


def test_bad_timeouts_are_value_errors_not_hangs(server):
    for kw in ({"connect_timeout": 0}, {"connect_timeout": float("nan")}, {"send_timeout": -1},
               {"send_timeout": None}, {"call_timeout": 0}):
        with pytest.raises(ValueError):
            connect(server, **kw)


def test_retry_not_ready_until_ok():
    count = {"n": 0}

    def handler(req, reply):
        count["n"] += 1
        reply(err(req["id"], "not_ready", "loading") if count["n"] < 3 else ok(req["id"], 7))

    with FakeServer(handler=handler) as s:
        with connect(s, retry_not_ready=5.0) as game:
            assert game.call("Utility", "RandomInt", 1, 6) == 7
        assert count["n"] == 3


def test_out_of_order_responses_reach_the_right_callers():
    held = []

    def handler(req, reply):
        held.append((req, reply))
        if len(held) == 2:
            for r, rep in reversed(held):
                rep(ok(r["id"], r["args"]["args"][0]))

    with FakeServer(handler=handler) as s:
        with connect(s) as game:
            results = {}
            threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, game.call("X", "Y", i)))
                       for i in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
            assert results == {1: 1, 2: 2}


def test_connection_drop_fails_inflight_and_later_calls():
    with FakeServer(handler=lambda req, reply: None) as s:  # never answers
        game = connect(s)
        caught = []

        def worker():
            try:
                game.call("Utility", "Wait", 10.0)
            except errors.ConnectionLost as e:
                caught.append(e)

        t = threading.Thread(target=worker)
        t.start()
        wait_until(lambda: s.requests, what="the request to reach the server")
        s.drop_all()
        t.join(5)
        assert caught, "in-flight call did not fail with ConnectionLost"
        with pytest.raises(errors.ConnectionLost):
            game.call("Utility", "RandomInt", 1, 2)
        assert game.closed
        assert game.transport._sock.fileno() == -1, "a dead connection must close its socket"
        assert game.transport._pending == {}


def test_bridge_silence_is_call_timeout_then_the_late_reply_is_ignored():
    first = {"reply": None}

    def handler(req, reply):
        if first["reply"] is None:
            first["reply"] = (req, reply)  # hold the first request
        else:
            held_req, held_reply = first["reply"]
            held_reply(ok(held_req["id"], "late"))
            reply(ok(req["id"], "second"))

    with FakeServer(handler=handler) as s:
        with connect(s, call_timeout=0.3) as game:
            with pytest.raises(errors.CallTimeout):
                game.call("Utility", "Wait", 10.0)
            assert game.transport._pending == {}
            assert game.call("Utility", "RandomInt", 1, 2) == "second"
            assert game.transport.unmatched_responses == 0  # a late answer to a known id is not "unmatched"


def test_stalled_send_closes_the_connection_and_does_not_block_other_callers():
    with FakeServer(mode="stall") as s:
        game = connect(s, send_timeout=0.5)
        big = "x" * (15 * 1024 * 1024)  # far more than the socket buffers can hold
        outcomes = []

        def worker():
            try:
                game.call("X", "Y", big)
            except errors.ConnectionLost as e:
                outcomes.append(e)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        t0 = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not any(t.is_alive() for t in threads), "a caller is still stuck behind the send lock"
        assert time.monotonic() - t0 < 8
        assert len(outcomes) == 3
        assert any("did not read" in str(e) for e in outcomes)
        assert game.closed and game.transport._sock.fileno() == -1 and game.transport._pending == {}
        with pytest.raises(errors.ConnectionLost):
            game.call("X", "Y")


def test_oversize_request_is_a_typed_error_and_leaves_nothing_behind(server):
    with connect(server) as game:
        with pytest.raises(errors.EncodeError, match="limit"):
            game.call("X", "Y", "a" * (16 * 1024 * 1024))
        assert game.transport._pending == {}
        assert game.call("X", "Y", 1)["args"]["args"] == [1]  # the connection is fine
    assert len(server.requests) == 1


def test_error_that_is_not_an_object_is_a_typed_error_and_the_connection_survives():
    def handler(req, reply):
        if req["args"]["function"] == "Bad":
            reply({"type": "response", "id": req["id"], "ok": False, "error": "boom"})
        elif req["args"]["function"] == "NoOk":
            reply({"type": "response", "id": req["id"], "result": 1})
        else:
            reply(ok(req["id"], "fine"))

    with FakeServer(handler=handler) as s:
        with connect(s) as game:
            with pytest.raises(errors.InternalError, match="malformed"):
                game.call("X", "Bad")
            with pytest.raises(errors.ProtocolViolation, match="ok"):
                game.call("X", "NoOk")
            assert game.call("X", "Good") == "fine"
            assert not game.closed


def test_responses_with_null_or_unhashable_ids_are_ignored_and_counted():
    def handler(req, reply):
        for bad in (None, [1, 2], {"a": 1}, "7", True, 1.5):
            reply({"type": "response", "id": bad, "ok": True, "result": "stray"})
        reply(ok(req["id"], "real"))

    with FakeServer(handler=handler) as s:
        with connect(s) as game:
            assert game.call("X", "Y") == "real"
            assert game.call("X", "Y") == "real"
            assert not game.closed
            assert game.transport.unmatched_responses == 12


def test_close_closes_the_socket(server, sockets):
    game = connect(server)
    game.close()
    assert game.closed and sockets[0].fileno() == -1
    with pytest.raises(errors.ConnectionLost):
        game.call("X", "Y")


# -- the fixtures ------------------------------------------------------------------------------------------
HELLO = load_fixture("hello.json")


def test_client_hello_has_the_fixture_shape():
    with FakeServer() as s:
        connect_remote(port=s.port, token=TOKEN, client_name="test").close()
        hello = s.hellos[0]
    assert set(hello) <= {"type", "protocol", "token", "client"}
    for fx in HELLO["valid"]:  # "client" is optional in the protocol, so a fixture may omit it
        shape = {k: type(v).__name__ for k, v in hello.items() if k in fx}
        assert shape == {k: type(v).__name__ for k, v in fx.items()}
    assert hello["protocol"] == list(g.PROTOCOL_VERSION)
    assert re.fullmatch(r"[0-9a-f]{32}", hello["token"])


def test_welcome_fixture_is_accepted():
    fx = next(r for r in load_fixture("responses.json")["valid"] if r["type"] == "welcome")
    with FakeServer(welcome={k: v for k, v in fx.items() if k != "type"}) as s:
        with connect(s) as game:
            assert game.epoch == fx["epoch"]
            assert game.welcome["capabilities"] == fx["capabilities"]


def test_connect_defaults_are_the_tables_and_the_v1_values():
    p = inspect.signature(connect_remote).parameters
    assert (p["host"].default, p["client_name"].default) == (g.HOST, g.CLIENT_NAME) == ("127.0.0.1", "skyrim-python")
    assert (p["connect_timeout"].default, p["call_timeout"].default, p["send_timeout"].default) == \
        (g.CONNECT_TIMEOUT_S, g.CALL_TIMEOUT_S, g.SEND_TIMEOUT_S) == (5.0, 30.0, 30.0)


# -- skyrim.connect: one entry point for both modes --------------------------------------------------------
def test_connect_is_remote_without_a_game_module(server, monkeypatch):
    monkeypatch.delitem(sys.modules, "_skyrim", raising=False)
    with skyrim.connect(port=server.port, token=TOKEN) as game:
        assert isinstance(game.transport, RemoteTransport)


def test_connect_is_remote_next_to_the_fake_module(server, fake):
    assert fake.BACKEND == "fake"  # the Linux test build is not "the game"
    with skyrim.connect(port=server.port, token=TOKEN) as game:
        assert isinstance(game.transport, RemoteTransport)


def test_connect_is_in_process_inside_the_game(tmp_path, monkeypatch):
    f = FakeSkyrim(root=tmp_path)
    f.BACKEND = "game"  # what the module built into SkyrimPythonCore.dll reports
    monkeypatch.setitem(sys.modules, "_skyrim", f)
    try:
        with skyrim.connect() as game:
            assert type(game.transport).__name__ == "InProcessTransport"
            assert game.call("Fake", "Echo", 5) == 5
    finally:
        f.close()


def test_connect_with_an_explicit_session_is_remote_even_inside_the_game(server, tmp_path, monkeypatch):
    f = FakeSkyrim(root=tmp_path)
    f.BACKEND = "game"
    monkeypatch.setitem(sys.modules, "_skyrim", f)
    try:
        with skyrim.connect(port=server.port, token=TOKEN) as game:
            assert isinstance(game.transport, RemoteTransport)
    finally:
        f.close()
