# Task 22: Discovery and the remote transport

Adapted from: branch `plan-out-of-process`, task 14 (`task-14-python-discovery-and-the-synchronous-client.md`): its session discovery, its socket client internals (now `RemoteTransport`), its fake socket server and the transport-level hardening tests. Its `Game`, `Batch`, timeout arithmetic and request-fixture replay moved to Task 21 and are not repeated here.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2 and 4.7). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 21 (`Game`, `Transport`, `connect_in_process`, `python/tests/conftest.py`, `fake_skyrim.py`, `support.wait_until`). Linux only; no game, no C++ build.

**What changed from the out-of-process plan.** The old `client.Game` owned the socket. Now the socket lives in `RemoteTransport` (`skyrim/remote.py`), which implements Task 21's `Transport`; `connect_remote()` handshakes and wraps it in the shared `Game`. So `game._sock`, `game._pending` and `game.unmatched_responses` from the old tests are now `game.transport._sock`, `game.transport._pending` and `game.transport.unmatched_responses`. Two additions: the socket gets `TCP_NODELAY` (split writes otherwise stall about 40 ms on Linux loopback), and discovery learns the session files the Python bridge writes (Task 23): `write_session_file`, `write_inactive_session_file`, and `find_session` reporting the reason in an inactive file instead of failing on a missing port. `skyrim.connect()` is the one entry point for both modes: in-process inside the game, remote elsewhere. A call that the bridge itself never answers (within the call's wait) is `CallTimeout`; a call the game timed out is the Dispatcher's `Timeout`, as in-process.

**Files:**
- Create: `python/src/skyrim/discovery.py`, `python/src/skyrim/remote.py`, `python/tests/test_discovery.py`, `python/tests/test_remote.py`
- Modify: `python/src/skyrim/__init__.py` (full replacement below), `python/tests/support.py` (append), `python/tests/conftest.py` (append)

**Interfaces:**
- Consumes: Task 21 (`skyrim.game.Game`, `skyrim.inprocess.connect_in_process`, the `fake` fixture, `fake_skyrim.FakeSkyrim`, `support.wait_until`, `support.load_fixture`), Task 20 (`_framing.encode`, `_framing.read_frame`, `errors`), `skyrim._protocol_generated` (Task 12: `PROTOCOL_VERSION`, `HOST`, `ENV_SESSION`, `ENV_PORT`, `ENV_TOKEN`, `SESSION_FILE_FIELDS`, `CONFIG_KEYS["port"]`, `CODE_PROTOCOL_MISMATCH`, `CLIENT_NAME`, `CONNECT_TIMEOUT_S`, `CALL_TIMEOUT_S`, `SEND_TIMEOUT_S`), the golden `hello.json` and `responses.json` (Task 11).
- Produces:
  - `skyrim.discovery`: `Session(host, port, token, pid=None, plugin_version=None)` (frozen dataclass); `find_session(session=None, *, host=HOST, port=None, token=None, env=os.environ) -> Session` (precedence: explicit `port`+`token`, explicit `session` path, `SKYRIM_PYTHON_SESSION`, the `SKYRIM_PYTHON_PORT`+`SKYRIM_PYTHON_TOKEN` pair; `NotRunning` naming what it tried, for a bad port anywhere, for half a pair, and for an inactive session file, whose `reason` it quotes); `write_session_file(path, session: Session) -> None` (exactly `SESSION_FILE_FIELDS`, `protocol` from the generated version; atomic replace; creates the folder); `write_inactive_session_file(path, reason, *, pid=None, plugin_version=None) -> None` (`{"active": false, "reason", "pid", "plugin_version"}`, no port or token).
  - `skyrim.remote`: `class RemoteTransport(sock, welcome, *, send_timeout=SEND_TIMEOUT_S)` implementing `Transport` (`welcome`, `closed`, `request(op, args, wait_s) -> dict`, `close()`), plus `unmatched_responses: int`; `connect_remote(session=None, *, host=HOST, port=None, token=None, client_name=CLIENT_NAME, connect_timeout=CONNECT_TIMEOUT_S, call_timeout=CALL_TIMEOUT_S, retry_not_ready=False, send_timeout=SEND_TIMEOUT_S) -> Game`. `connect_timeout` bounds the whole connect, hello and welcome exchange (one deadline); `send_timeout` bounds a stalled `sendall` (the connection is then closed, `ConnectionLost`); the three timeouts must be positive and finite (`ValueError`); the welcome's protocol major is checked (`ProtocolMismatch`; a missing or malformed `protocol` or a non-integer `epoch` is `ProtocolViolation`); a failed handshake closes the socket. Responses with a null, non-integer or unhashable id are ignored and counted in `unmatched_responses`; a late answer to an id the caller gave up on is dropped silently.
  - `skyrim.connect(session=None, **kw) -> Game` (in `skyrim/__init__.py`): `connect_in_process(module=_skyrim, **kw)` when no bridge is named (no `session`, `host`, `port`, `token`, `client_name`, `connect_timeout` or `send_timeout`) and `_skyrim` imports with `BACKEND == "game"`; otherwise `connect_remote(session, **kw)`. The package also exports `Session`, `find_session`, `RemoteTransport`, `connect_remote`.
  - `python/tests/support.py` additions: `TOKEN`, `WELCOME`, `ok(id, result)`, `err(id, code, message="", detail=None)`, `echo_handler`, `hello_problems(hello) -> list[str]`, `FakeServer(handler=None, token=TOKEN, mode="normal", welcome=None, drip_interval=0.05)` with `.port`, `.requests`, `.hellos`, `.errors`, `.conns`, `.drop_all()`, `.close()`, `.check()`, context manager (modes `"normal"`, `"hang"`, `"mismatch"`, `"garbage"`, `"drip"`, `"partial"`, `"stall"`; handler signature `handler(request: dict, reply: Callable[[dict], None])`). `python/tests/conftest.py` additions: fixtures `server` (a `FakeServer`) and `sockets` (every client socket made through `socket.create_connection`, to assert it was closed).

- [ ] **Step 1: Add the fake socket server and its fixtures**

Append this to the end of `python/tests/support.py`, after two blank lines:
`python/tests/support.py (appended)`:
```python
# ---- Task 22: a protocol-v1 socket server for the remote transport's tests -------------------------------
import socket  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402

from skyrim import _framing  # noqa: E402
from skyrim.errors import ConnectionLost, ProtocolViolation  # noqa: E402

TOKEN = "00112233445566778899aabbccddeeff"
WELCOME = {"protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0",
           "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]}


def ok(req_id, result):
    return {"type": "response", "id": req_id, "ok": True, "result": result}


def err(req_id, code, message="", detail=None):
    return {"type": "response", "id": req_id, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def echo_handler(req, reply):
    reply(ok(req["id"], {"op": req["op"], "args": req["args"]}))


def hello_problems(hello):
    """What is wrong with a client hello, per protocol/PROTOCOL.md (empty list = fine)."""
    problems = []
    if hello.get("type") != "hello":
        problems.append("type is not 'hello'")
    proto = hello.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2 and all(type(n) is int for n in proto)):
        problems.append(f"protocol is not [major, minor]: {proto!r}")
    if not isinstance(hello.get("token"), str):
        problems.append("token is not a string")
    if not isinstance(hello.get("client"), str):
        problems.append("client is not a string")
    return problems


class FakeServer:
    """A protocol-v1 server on a random loopback port, one thread per connection: it stands in for the
    dev bridge (Task 23) so the remote transport can be driven into every failure a real peer could cause.

    modes: "normal"; "hang" (accept, read the hello, never answer); "mismatch" (handshake_error
    protocol_mismatch); "garbage" (answer with a non-protocol byte stream); "drip" (send the welcome one
    byte at a time); "partial" (send part of the welcome frame, then stop); "stall" (complete the handshake,
    then never read another byte, with a tiny receive buffer, so the client's sends back up).
    `welcome` overrides fields of the welcome message. Exceptions in the handler, and a malformed hello, are
    recorded in `.errors`; use the server as a context manager (or call check()) so they fail the test.
    """

    def __init__(self, handler=None, token=TOKEN, mode="normal", welcome=None, drip_interval=0.05):
        self.handler = handler or echo_handler
        self.token = token
        self.mode = mode
        self.welcome = {**WELCOME, **(welcome or {})}
        self.drip_interval = drip_interval
        self.hellos = []
        self.requests = []
        self.errors = []
        self.conns = []
        self._conns_lock = threading.Lock()
        self._stop = threading.Event()
        self._lsock = socket.socket()
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if mode == "stall":
            self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # accepted sockets inherit it
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen()
        self.port = self._lsock.getsockname()[1]
        self._accepter = threading.Thread(target=self._accept, daemon=True)
        self._accepter.start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                return
            if self._stop.is_set():  # close() poked us awake
                conn.close()
                return
            with self._conns_lock:
                self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        lock = threading.Lock()

        def reply(obj):
            with lock:
                try:
                    conn.sendall(_framing.encode(obj))
                except OSError:
                    pass

        try:
            hello = _framing.read_frame(conn)
            self.hellos.append(hello)
            problems = hello_problems(hello)
            if problems:
                self.errors.append(f"malformed hello {hello!r}: {problems}")
                reply({"type": "handshake_error", "code": "bad_request", "message": "; ".join(problems)})
                return
            if self.mode == "hang":
                self._stop.wait()
                return
            if self.mode == "garbage":
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            if self.mode == "mismatch":
                reply({"type": "handshake_error", "code": "protocol_mismatch", "message": "server speaks 2.0"})
                return
            if hello.get("token") != self.token:
                reply({"type": "handshake_error", "code": "unauthorized", "message": "bad token"})
                return
            welcome = _framing.encode({"type": "welcome", **self.welcome})
            if self.mode == "drip":
                for i in range(len(welcome)):
                    conn.sendall(welcome[i:i + 1])
                    if self._stop.wait(self.drip_interval):
                        return
                return
            if self.mode == "partial":
                conn.sendall(welcome[:len(welcome) // 2])
                self._stop.wait()
                return
            conn.sendall(welcome)
            if self.mode == "stall":
                self._stop.wait()
                return
            while True:
                req = _framing.read_frame(conn)
                self.requests.append(req)
                self.handler(req, reply)
        except (ConnectionLost, ProtocolViolation, OSError):
            pass  # the client went away or hung up: the normal end of a connection
        except Exception:  # noqa: BLE001 - a bug in a test handler: report it, do not swallow it
            self.errors.append(traceback.format_exc())
        finally:
            conn.close()

    def drop_all(self):
        with self._conns_lock:
            conns = list(self.conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:  # closing a listener does not wake a blocked accept(); a throwaway connection does
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self._accepter.join(2)
        self._lsock.close()
        self.drop_all()

    def check(self):
        """Raise if a handler crashed or a client sent a malformed hello."""
        if self.errors:
            raise AssertionError("FakeServer recorded errors:\n" + "\n".join(self.errors))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        if exc_type is None:
            self.check()
```

Append this to the end of `python/tests/conftest.py`, after two blank lines:
`python/tests/conftest.py (appended)`:
```python
# ---- Task 22: socket fixtures for the remote transport's tests --------------------------------------------
import socket  # noqa: E402

from support import FakeServer  # noqa: E402


@pytest.fixture
def server():
    with FakeServer() as s:
        yield s


@pytest.fixture
def sockets(monkeypatch):
    """Every client socket created through socket.create_connection, so a test can check it was closed."""
    made = []
    real = socket.create_connection

    def spy(*args, **kwargs):
        sock = real(*args, **kwargs)
        made.append(sock)
        return sock

    monkeypatch.setattr(socket, "create_connection", spy)
    return made
```

- [ ] **Step 2: Write the failing tests**

`python/tests/test_discovery.py`:
```python
import json
import os

import pytest

from skyrim import _protocol_generated as g
from skyrim.discovery import Session, find_session, write_inactive_session_file, write_session_file
from skyrim.errors import NotRunning

TOK = "00112233445566778899aabbccddeeff"


def write_session(path, port=45977, token=TOK):
    path.write_text(json.dumps({"port": port, "token": token, "pid": 42, "plugin_version": "0.1.0", "protocol": [1, 0]}))
    return path


def test_the_session_file_fields_are_the_tables(tmp_path):
    assert set(json.loads(write_session(tmp_path / "s.json").read_text())) == set(g.SESSION_FILE_FIELDS)
    assert (g.ENV_SESSION, g.ENV_PORT, g.ENV_TOKEN) == ("SKYRIM_PYTHON_SESSION", "SKYRIM_PYTHON_PORT", "SKYRIM_PYTHON_TOKEN")


def test_explicit_port_and_token_win(tmp_path):
    s = find_session(write_session(tmp_path / "s.json", port=1), port=2, token=TOK, env={})
    assert s.port == 2


def test_explicit_session_path(tmp_path):
    s = find_session(write_session(tmp_path / "s.json"), env={})
    assert (s.host, s.port, s.token, s.pid) == ("127.0.0.1", 45977, TOK, 42)


def test_env_session_then_env_port(tmp_path):
    p = write_session(tmp_path / "s.json", port=3)
    assert find_session(env={"SKYRIM_PYTHON_SESSION": str(p)}).port == 3
    assert find_session(env={"SKYRIM_PYTHON_PORT": "4", "SKYRIM_PYTHON_TOKEN": TOK}).port == 4


def test_missing_session_file_is_not_running(tmp_path):
    with pytest.raises(NotRunning, match="no session file"):
        find_session(tmp_path / "absent.json", env={})


def test_nothing_configured_is_not_running():
    with pytest.raises(NotRunning, match="SKYRIM_PYTHON_SESSION"):
        find_session(env={})


def test_corrupt_session_file_is_not_running(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    with pytest.raises(NotRunning, match="unreadable"):
        find_session(p, env={})


@pytest.mark.parametrize("bad", ["abc", "4x", "0", "65536", "-1", "1.5"])
def test_bad_env_port_is_not_running_not_value_error(bad):
    with pytest.raises(NotRunning, match="SKYRIM_PYTHON_PORT is not a port number"):
        find_session(env={"SKYRIM_PYTHON_PORT": bad, "SKYRIM_PYTHON_TOKEN": TOK})


def test_only_half_of_a_pair_is_a_clear_error():
    with pytest.raises(NotRunning, match="SKYRIM_PYTHON_PORT is set but SKYRIM_PYTHON_TOKEN is not"):
        find_session(env={"SKYRIM_PYTHON_PORT": "4"})
    with pytest.raises(NotRunning, match="SKYRIM_PYTHON_TOKEN is set but SKYRIM_PYTHON_PORT is not"):
        find_session(env={"SKYRIM_PYTHON_TOKEN": TOK})
    with pytest.raises(NotRunning, match="together.*only port"):
        find_session(port=5, env={})
    with pytest.raises(NotRunning, match="together.*only token"):
        find_session(token=TOK, env={})


def test_a_session_source_beats_a_lone_env_variable(tmp_path):
    p = write_session(tmp_path / "s.json", port=3)
    assert find_session(env={"SKYRIM_PYTHON_SESSION": str(p), "SKYRIM_PYTHON_PORT": "9"}).port == 3


@pytest.mark.parametrize("port", ["abc", 0, 70000, None, True])
def test_bad_port_in_session_file(tmp_path, port):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"port": port, "token": TOK}))
    with pytest.raises(NotRunning, match="port"):
        find_session(p, env={})


def test_bad_explicit_port_is_not_running():
    with pytest.raises(NotRunning, match="port="):
        find_session(port="abc", token=TOK, env={})


# -- writing session files (the bridge, Task 23) ------------------------------------------------------------
def test_write_session_file_round_trips_and_has_exactly_the_table_fields(tmp_path):
    p = tmp_path / "deeper" / "session.json"  # the folder is created
    write_session_file(p, Session("127.0.0.1", 45999, TOK, pid=7, plugin_version="0.1.0"))
    data = json.loads(p.read_text(encoding="utf-8"))
    assert set(data) == set(g.SESSION_FILE_FIELDS)
    assert data["protocol"] == list(g.PROTOCOL_VERSION)
    s = find_session(p, env={})
    assert (s.port, s.token, s.pid, s.plugin_version) == (45999, TOK, 7, "0.1.0")


def test_write_session_file_replaces_atomically_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "session.json"
    write_session(p, port=1)
    write_session_file(p, Session("127.0.0.1", 2, TOK))
    assert find_session(p, env={}).port == 2
    assert sorted(os.listdir(tmp_path)) == ["session.json"]


def test_an_inactive_session_file_names_its_reason(tmp_path):
    p = tmp_path / "session.json"
    write_session(p)  # a stale active file from an earlier run is overwritten
    write_inactive_session_file(p, "bridge_enabled is false", pid=9, plugin_version="0.1.0")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"active": False, "reason": "bridge_enabled is false", "pid": 9, "plugin_version": "0.1.0"}
    assert "port" not in data and "token" not in data
    with pytest.raises(NotRunning, match="not running: bridge_enabled is false"):
        find_session(p, env={})
```

`python/tests/test_remote.py`:
```python
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
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run --python 3.14 pytest -q tests/test_discovery.py tests/test_remote.py`
Expected: FAIL with `2 errors during collection`: `tests/test_discovery.py` raises `ModuleNotFoundError: No module named 'skyrim.discovery'` and `tests/test_remote.py` raises `ModuleNotFoundError: No module named 'skyrim.remote'`.

- [ ] **Step 4: Implement discovery, the remote transport and `skyrim.connect`**

`python/src/skyrim/discovery.py`:
```python
"""Find the dev bridge: explicit port+token, a session file, or environment variables. The bridge (Task 23)
writes the session file with write_session_file, or write_inactive_session_file when it is not serving."""
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import _protocol_generated as _g
from .errors import NotRunning

_PORT_MAX = _g.CONFIG_KEYS["port"]["max"]


@dataclass(frozen=True)
class Session:
    host: str
    port: int
    token: str
    pid: int | None = None
    plugin_version: str | None = None


def _port(value, source: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise NotRunning(f"{source} is not a port number: {value!r}") from None
    if isinstance(value, bool) or not 1 <= port <= _PORT_MAX:
        raise NotRunning(f"{source} is not a port number (1-{_PORT_MAX}): {value!r}")
    return port


def _read(path: Path, host: str) -> Session:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRunning(f"no session file at {path}; is the game running with the dev bridge enabled?") from None
    except (OSError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: {e}") from None
    if isinstance(data, dict) and data.get("active") is False:
        raise NotRunning(f"session file {path} says the bridge is not running: {data.get('reason', 'no reason given')}")
    try:
        port = _port(data["port"], f"the port in session file {path}")
        return Session(host, port, str(data["token"]), data.get("pid"), data.get("plugin_version"))
    except (KeyError, TypeError, AttributeError) as e:
        raise NotRunning(f"session file {path} is unreadable: missing {e}") from None


def find_session(session=None, *, host: str = _g.HOST, port: int | None = None, token: str | None = None,
                 env=os.environ) -> Session:
    if (port is None) != (token is None):
        raise NotRunning(f"port= and token= must be given together (got only {'port' if token is None else 'token'})")
    if port is not None:
        return Session(host, _port(port, "port="), token)
    if session is not None:
        return _read(Path(session), host)
    if env.get(_g.ENV_SESSION):
        return _read(Path(env[_g.ENV_SESSION]), host)
    env_port, env_token = env.get(_g.ENV_PORT), env.get(_g.ENV_TOKEN)
    if env_port and env_token:
        return Session(host, _port(env_port, _g.ENV_PORT), env_token)
    if env_port or env_token:
        have, missing = (_g.ENV_PORT, _g.ENV_TOKEN) if env_port else (_g.ENV_TOKEN, _g.ENV_PORT)
        raise NotRunning(f"{have} is set but {missing} is not; the pair must be given together "
                         f"(or set {_g.ENV_SESSION} instead)")
    raise NotRunning(f"no session given: pass session=/port=+token=, or set {_g.ENV_SESSION} "
                     f"(or {_g.ENV_PORT} and {_g.ENV_TOKEN})")


def _write_json(path, data: dict) -> None:
    """Write `data` to `path` atomically: a reader sees the old file or the new one, never half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)  # plain str/int/None values: only an OSError (disk full) can stop it
    os.replace(tmp, path)


def write_session_file(path, session: Session) -> None:
    """The active session file: exactly the generated SESSION_FILE_FIELDS."""
    data = {"port": session.port, "token": session.token, "pid": session.pid,
            "plugin_version": session.plugin_version, "protocol": list(_g.PROTOCOL_VERSION)}
    assert set(data) == set(_g.SESSION_FILE_FIELDS), "SESSION_FILE_FIELDS changed: update write_session_file"
    _write_json(path, data)


def write_inactive_session_file(path, reason: str, *, pid: int | None = None, plugin_version: str | None = None) -> None:
    """The bridge is not serving: no port, no token, and why, so a client never follows a stale port."""
    _write_json(path, {"active": False, "reason": reason, "pid": pid, "plugin_version": plugin_version})
```

`python/src/skyrim/remote.py`:
```python
"""The remote transport: the Game API from another process, over the dev bridge (Task 23).

One socket and one reader thread per connection; any thread may call. Responses are matched to callers by
request id. A send that times out may have put half a frame on the wire, so the connection is then closed,
never reused."""
import itertools
import math
import socket
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from . import _framing, errors
from . import _protocol_generated as _g
from .discovery import find_session
from .game import Game

PROTOCOL = list(_g.PROTOCOL_VERSION)


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {value!r}")
    return float(value)


class RemoteTransport:
    """A Transport over one handshaken socket (see connect_remote)."""

    def __init__(self, sock: socket.socket, welcome: dict, *, send_timeout: float = _g.SEND_TIMEOUT_S):
        self.welcome = welcome
        self.closed = False
        #: Responses that could not be matched to a request (null, non-integer or unhashable id).
        self.unmatched_responses = 0
        self._sock = sock
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._send_timeout = send_timeout
        sock.settimeout(send_timeout)  # bounds sendall; the reader treats it as a wake-up tick only
        self._reader = threading.Thread(target=self._read_loop, name="skyrim-remote-reader", daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            while True:
                msg = _framing.read_frame(self._sock)
                if msg.get("type") != "response":
                    continue  # "event" messages arrive in M2; M1 has no subscriptions, so none are sent
                req_id = msg.get("id")
                if type(req_id) is not int:  # null, a list (unhashable), a bool, a string: nobody to give it to
                    self.unmatched_responses += 1
                    continue
                with self._lock:
                    fut = self._pending.pop(req_id, None)
                if fut is not None:
                    fut.set_result(msg)
        except Exception as e:  # noqa: BLE001 - any read failure ends the connection
            self._fail_all(str(e) or type(e).__name__)

    def _close_socket(self):
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def _fail_all(self, reason: str):
        """The connection is unusable: mark it closed, close the socket, fail every waiting caller."""
        with self._lock:
            self.closed = True
            pending, self._pending = self._pending, {}
        self._close_socket()
        for fut in pending.values():
            fut.set_exception(errors.ConnectionLost(reason))

    def _send_frame(self, frame: bytes):
        with self._send_lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            try:
                self._sock.sendall(frame)
            except TimeoutError:
                # Part of the frame may be on the wire, so the stream is unusable: close, never retry.
                reason = f"the game did not read our request within {self._send_timeout:g}s; connection closed"
            except OSError as e:
                reason = str(e) or type(e).__name__
            else:
                return
        self._fail_all(reason)  # outside the send lock, so other senders wake up and see `closed`
        raise errors.ConnectionLost(reason)

    def request(self, op: str, args: dict, wait_s: float) -> dict:
        req_id = next(self._ids)
        # Encode BEFORE registering the future: an oversize or unencodable request raises EncodeError
        # and leaves nothing behind in _pending.
        frame = _framing.encode({"type": "request", "id": req_id, "op": op, "args": args})
        fut: Future = Future()
        with self._lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            self._pending[req_id] = fut
        try:
            self._send_frame(frame)
            return fut.result(timeout=wait_s)
        except BaseException as e:
            with self._lock:
                self._pending.pop(req_id, None)
            if isinstance(e, FutureTimeout):
                raise errors.CallTimeout(
                    f"{op}: the bridge sent no response within {wait_s:.1f}s (the call may still run in the game)") from None
            raise

    def close(self) -> None:
        with self._lock:
            self.closed = True
        self._close_socket()
        self._reader.join(timeout=2)  # the reader then fails any caller still waiting with ConnectionLost


def _handshake(sock: socket.socket, token: str, client_name: str, deadline: float) -> dict:
    """Send hello, read welcome. The WHOLE exchange is bounded by `deadline`, not each recv."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("connect_timeout used up before the hello was sent")
    sock.settimeout(remaining)
    sock.sendall(_framing.encode({"type": "hello", "protocol": PROTOCOL, "token": token, "client": client_name}))
    msg = _framing.read_frame(sock, deadline)
    if msg.get("type") == "handshake_error":
        raise errors.handshake_error_from_wire(msg)
    if msg.get("type") != "welcome":
        raise errors.ProtocolViolation(f"expected welcome, got {msg.get('type')!r}")
    proto = msg.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2 and all(type(n) is int for n in proto)):
        raise errors.ProtocolViolation(f"welcome has no [major, minor] protocol: {proto!r}")
    if proto[0] != PROTOCOL[0]:
        raise errors.ProtocolMismatch(_g.CODE_PROTOCOL_MISMATCH, f"the game speaks protocol {proto[0]}.{proto[1]}, "
                                      f"this client speaks {PROTOCOL[0]}.{PROTOCOL[1]}")
    if "epoch" in msg and type(msg["epoch"]) is not int:
        raise errors.ProtocolViolation(f"welcome epoch is not an integer: {msg['epoch']!r}")
    return msg


def connect_remote(session=None, *, host: str = _g.HOST, port: int | None = None, token: str | None = None,
                   client_name: str = _g.CLIENT_NAME, connect_timeout: float = _g.CONNECT_TIMEOUT_S,
                   call_timeout: float = _g.CALL_TIMEOUT_S, retry_not_ready: bool | float = False,
                   send_timeout: float = _g.SEND_TIMEOUT_S) -> Game:
    """Connect to the dev bridge and handshake. Raises NotRunning, Unauthorized, ProtocolMismatch or
    ProtocolViolation; a failed handshake never leaves the socket open.

    `connect_timeout` bounds the whole connect-hello-welcome exchange. `send_timeout` bounds how long a
    request may sit unread by the game before the connection is closed (ConnectionLost)."""
    _positive(connect_timeout, "connect_timeout")
    _positive(send_timeout, "send_timeout")
    _positive(call_timeout, "call_timeout")
    s = find_session(session, host=host, port=port, token=token)
    deadline = time.monotonic() + connect_timeout
    try:
        sock = socket.create_connection((s.host, s.port), timeout=connect_timeout)
    except OSError as e:
        raise errors.NotRunning(f"cannot connect to {s.host}:{s.port}: {e}") from None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # split writes otherwise stall ~40 ms
        try:
            welcome = _handshake(sock, s.token, client_name, deadline)
        except TimeoutError:
            raise errors.NotRunning(
                f"{s.host}:{s.port} accepted but sent no welcome within {connect_timeout}s") from None
        except (OSError, errors.ConnectionLost) as e:
            raise errors.NotRunning(f"{s.host}:{s.port} closed during the handshake: {e}") from None
        transport = RemoteTransport(sock, welcome, send_timeout=send_timeout)
    except BaseException:
        sock.close()
        raise
    return Game(transport, call_timeout=call_timeout, retry_not_ready=retry_not_ready)
```

Replace the package's `__init__.py` with:
`python/src/skyrim/__init__.py`:
```python
"""Call Papyrus in a running Skyrim from Python."""
from .discovery import Session, find_session  # noqa: F401
from .errors import *  # noqa: F401,F403
from .game import Batch, Game, Transport  # noqa: F401
from .inprocess import InProcessTransport, connect_in_process  # noqa: F401
from .remote import RemoteTransport, connect_remote  # noqa: F401
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"

_REMOTE_ONLY = ("host", "port", "token", "client_name", "connect_timeout", "send_timeout")


def connect(session=None, **kw) -> Game:
    """The running game, from anywhere.

    Inside the game (the built-in `_skyrim` module reports BACKEND == "game") this is the in-process
    transport. Anywhere else, or when the caller names a bridge (session, host, port, token, ...), it is the
    dev bridge through connect_remote(); keyword arguments are connect_remote()'s. `call_timeout` and
    `retry_not_ready` apply in both modes."""
    if session is None and not any(k in kw for k in _REMOTE_ONLY):
        try:
            import _skyrim
        except ImportError:
            _skyrim = None
        if _skyrim is not None and getattr(_skyrim, "BACKEND", None) == "game":
            return connect_in_process(module=_skyrim, **kw)
    return connect_remote(session, **kw)
```

- [ ] **Step 5: Run to verify they pass, on both Python versions**

Run: `cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q`
Expected: `273 passed` twice, each in about 8 s (Task 21's 216 plus this task's 57). Every wait is bounded by a deadline; the slow-handshake tests take about 0.5 s each by design. To check for flakiness, run the new files a few times, once on a single CPU: `for i in 1 2 3; do uv run --python 3.14 pytest -q tests/test_discovery.py tests/test_remote.py; done; taskset -c 0 uv run --python 3.14 pytest -q tests/test_remote.py` prints `57 passed` three times, then `33 passed`.

Design notes the tests pin (from the 2026-09-30 review, unchanged): the handshake and every later `recv` share one deadline model (`_framing.read_frame(sock, deadline)`); the reader thread never uses a deadline, and the socket timeout set after the handshake (`send_timeout`) bounds `sendall` only; a send that times out may have written half a frame, so the connection is closed and never reused; `_fail_all` and `close()` both close the socket; a request is encoded before its future is registered, so `EncodeError` leaves nothing behind.

- [ ] **Step 6: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`. `discovery.py` and `remote.py` must not spell out an environment variable name, the default host, a port limit, an error code or a default timeout: they take them from `skyrim._protocol_generated`.

- [ ] **Step 7: Commit**

```bash
git add python/src python/tests
git commit -m "feat(python): session discovery, the remote transport over the dev bridge, and skyrim.connect for both modes"
```
