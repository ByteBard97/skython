# Task 14: Python discovery and the synchronous client

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `python/src/skyrim/discovery.py`, `python/src/skyrim/client.py`
- Modify: `python/src/skyrim/__init__.py`, `python/tests/support.py`
- Create: `python/tests/conftest.py`, `python/tests/test_discovery.py`, `python/tests/test_client.py`

**Interfaces:**
- Consumes: everything from Task 13.
- Produces:
  - `skyrim.discovery`: `Session(host: str, port: int, token: str, pid: int | None = None, plugin_version: str | None = None)`; `find_session(session: str | os.PathLike | None = None, *, host="127.0.0.1", port=None, token=None, env=os.environ) -> Session`. Precedence: explicit `port`+`token` → explicit `session` path → `SKYRIM_PYTHON_SESSION` → `SKYRIM_PYTHON_PORT`+`SKYRIM_PYTHON_TOKEN`. Raises `NotRunning` naming what it tried.
  - `skyrim.client`: `connect(session=None, *, host="127.0.0.1", port=None, token=None, client_name="skyrim-python", connect_timeout=5.0, call_timeout=30.0, retry_not_ready: bool | float = False) -> Game`
  - `Game` methods: `call(script, function, *args, timeout=None, require_unpaused=False)`, `call_method(obj, script, function, *args, timeout=None, require_unpaused=False)`, `get_property(obj, script, prop)`, `set_property(obj, script, prop, value)`, `batch() -> Batch`, `resolve(*, form_key=None, form_id=None, editor_id=None) -> Form`, `form(form_key: str) -> Form`, `alias(quest: Form, alias_id: int, alias_type="ReferenceAlias") -> Alias`, `state() -> dict`, `wait_ready(timeout=120.0) -> dict`, `close()`; attributes `welcome: dict`, `epoch: int`, `closed: bool`; context manager.
  - `Batch` methods: `call(...)`, `call_method(...)`, `get_property(...)`, `set_property(...)` (each returns the batch), `run(stop_on_error=True, timeout=None) -> list` (each entry is a decoded value or a `RemoteError` instance; shorter than the step count if it stopped early).
  - `python/tests/support.py`: `TOKEN`, `ok(id, result)`, `err(id, code, message, detail)`, `FakeServer(handler=None, token=TOKEN, mode="normal")` with `.port`, `.requests`, `.close()`; handler signature `handler(request: dict, reply: Callable[[dict], None])`; modes `"normal"`, `"hang"` (accept, never answer the hello), `"mismatch"`. `python/tests/conftest.py`: fixture `server`.

- [ ] **Step 1: Add the fake server to `python/tests/support.py` and a fixture in `conftest.py`**

Append to `python/tests/support.py`:
```python
import socket
import threading

from skyrim import _framing

TOKEN = "00112233445566778899aabbccddeeff"


def ok(req_id, result):
    return {"type": "response", "id": req_id, "ok": True, "result": result}


def err(req_id, code, message="", detail=None):
    return {"type": "response", "id": req_id, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def echo_handler(req, reply):
    reply(ok(req["id"], {"op": req["op"], "args": req["args"]}))


class FakeServer:
    """A protocol-v1 server on a random loopback port. One thread per connection."""

    def __init__(self, handler=None, token=TOKEN, mode="normal"):
        self.handler = handler or echo_handler
        self.token = token
        self.mode = mode
        self.requests = []
        self.conns = []
        self._lsock = socket.create_server(("127.0.0.1", 0))
        self.port = self._lsock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                return
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
            if self.mode == "hang":
                self._stop.wait()
                return
            if self.mode == "mismatch":
                reply({"type": "handshake_error", "code": "protocol_mismatch", "message": "server speaks 2.0"})
                return
            if hello.get("token") != self.token:
                reply({"type": "handshake_error", "code": "unauthorized", "message": "bad token"})
                return
            reply({"type": "welcome", "protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0",
                   "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]})
            while True:
                req = _framing.read_frame(conn)
                self.requests.append(req)
                self.handler(req, reply)
        except Exception:
            pass
        finally:
            conn.close()

    def drop_all(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        self._lsock.close()
        self.drop_all()
```

`python/tests/conftest.py`:
```python
import pytest

from support import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()
```

- [ ] **Step 2: Write the failing tests**

`python/tests/test_discovery.py`:
```python
import json

import pytest

from skyrim.discovery import find_session
from skyrim.errors import NotRunning

TOK = "00112233445566778899aabbccddeeff"


def write_session(path, port=45977, token=TOK):
    path.write_text(json.dumps({"port": port, "token": token, "pid": 42, "plugin_version": "0.1.0", "protocol": [1, 0]}))
    return path


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
```

`python/tests/test_client.py`:
```python
import socket
import threading
import time

import pytest

from support import TOKEN, FakeServer, err, ok
import skyrim
from skyrim import errors
from skyrim.values import Form


def connect(server, **kw):
    return skyrim.connect(port=server.port, token=TOKEN, **kw)


def test_handshake_and_call(server):
    with connect(server) as game:
        assert game.epoch == 1
        r = game.call("Utility", "RandomInt", 1, 6)
    assert r == {"op": "papyrus.call_static", "args": {
        "script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": False}}
    assert server.requests[0]["type"] == "request"


def test_call_method_sends_reference_and_timeout(server):
    with connect(server) as game:
        game.call_method(Form(form_id=20), "Actor", "GetActorValue", "Health", timeout=2.0)
    args = server.requests[0]["args"]
    assert args["object"] == {"kind": "form", "form_id": 20}
    assert args["timeout_ms"] == 2000


def test_int_overflow_raises_before_sending(server):
    with connect(server) as game:
        with pytest.raises(errors.TypeMismatch):
            game.call("Utility", "RandomInt", 1, 2**31)
    assert server.requests == []


def test_wrong_token_is_unauthorized(server):
    with pytest.raises(errors.Unauthorized):
        skyrim.connect(port=server.port, token="ffffffffffffffffffffffffffffffff")


def test_protocol_mismatch():
    s = FakeServer(mode="mismatch")
    try:
        with pytest.raises(errors.ProtocolMismatch):
            skyrim.connect(port=s.port, token=TOKEN)
    finally:
        s.close()


def test_hung_server_fails_within_connect_timeout():
    s = FakeServer(mode="hang")
    try:
        t0 = time.monotonic()
        with pytest.raises(errors.NotRunning, match="no welcome"):
            skyrim.connect(port=s.port, token=TOKEN, connect_timeout=0.5)
        assert time.monotonic() - t0 < 3
    finally:
        s.close()


def test_nothing_listening_is_not_running():
    with socket.create_server(("127.0.0.1", 0)) as tmp:
        port = tmp.getsockname()[1]
    with pytest.raises(errors.NotRunning):
        skyrim.connect(port=port, token=TOKEN, connect_timeout=0.5)


def test_remote_errors_are_typed():
    s = FakeServer(handler=lambda req, reply: reply(err(req["id"], "not_ready", "loading", {"reason": "loading"})))
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            with pytest.raises(errors.NotReady) as ei:
                game.call("Game", "GetPlayer")
            assert ei.value.detail["reason"] == "loading"
    finally:
        s.close()


def test_retry_not_ready_until_ok():
    count = {"n": 0}

    def handler(req, reply):
        count["n"] += 1
        reply(err(req["id"], "not_ready", "loading") if count["n"] < 3 else ok(req["id"], 7))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN, retry_not_ready=5.0) as game:
            assert game.call("Utility", "RandomInt", 1, 6) == 7
        assert count["n"] == 3
    finally:
        s.close()


def test_out_of_order_responses_reach_the_right_callers():
    held = []

    def handler(req, reply):
        held.append((req, reply))
        if len(held) == 2:
            for r, rep in reversed(held):
                rep(ok(r["id"], r["args"]["args"][0]))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            results = {}
            threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, game.call("X", "Y", i)))
                       for i in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
            assert results == {1: 1, 2: 2}
    finally:
        s.close()


def test_connection_drop_fails_inflight_and_later_calls():
    s = FakeServer(handler=lambda req, reply: None)  # never answers
    try:
        game = skyrim.connect(port=s.port, token=TOKEN)
        caught = []

        def worker():
            try:
                game.call("Utility", "Wait", 10.0)
            except errors.ConnectionLost as e:
                caught.append(e)

        t = threading.Thread(target=worker)
        t.start()
        while not s.requests:
            time.sleep(0.01)
        s.drop_all()
        t.join(5)
        assert caught, "in-flight call did not fail with ConnectionLost"
        with pytest.raises(errors.ConnectionLost):
            game.call("Utility", "RandomInt", 1, 2)
        assert game.closed
    finally:
        s.close()


def test_client_timeout_then_late_reply_is_ignored():
    first = {"reply": None}

    def handler(req, reply):
        if first["reply"] is None:
            first["reply"] = (req, reply)  # hold the first request
        else:
            held_req, held_reply = first["reply"]
            held_reply(ok(held_req["id"], "late"))
            reply(ok(req["id"], "second"))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN, call_timeout=0.3) as game:
            with pytest.raises(errors.CallTimeout):
                game.call("Utility", "Wait", 10.0)
            assert game.call("Utility", "RandomInt", 1, 2) == "second"
    finally:
        s.close()


def test_batch_results_mix_values_and_errors():
    def handler(req, reply):
        reply(ok(req["id"], {"results": [{"ok": True, "result": 3},
                                         {"ok": False, "error": {"code": "unknown_function", "message": "no"}}]}))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            b = game.batch().call("Utility", "RandomInt", 1, 6).call("Nope", "Nope")
            results = b.run(stop_on_error=False)
        assert results[0] == 3
        assert isinstance(results[1], errors.UnknownFunction)
        sent = s.requests[0]["args"]
        assert sent["stop_on_error"] is False
        assert [c["op"] for c in sent["calls"]] == ["papyrus.call_static", "papyrus.call_static"]
    finally:
        s.close()


def test_form_decodes_result():
    s = FakeServer(handler=lambda req, reply: reply(ok(req["id"], {
        "kind": "form", "form_id": 0x12EB7, "form_key": "Skyrim.esm|0x012EB7", "type": "Weapon", "epoch": 1})))
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            f = game.form("Skyrim.esm|0x012EB7")
        assert f.form_id == 0x12EB7 and f.type == "Weapon"
        assert s.requests[0]["args"] == {"form_key": "Skyrim.esm|0x012EB7"}
    finally:
        s.close()


def test_wait_ready_polls_state():
    states = iter([False, False, True])

    def handler(req, reply):
        reply(ok(req["id"], {"interactive": next(states), "player": None, "epoch": 1}))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            st = game.wait_ready(timeout=5)
        assert st["interactive"] is True
    finally:
        s.close()
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run pytest -q tests/test_discovery.py tests/test_client.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'skyrim.discovery'` / `AttributeError: module 'skyrim' has no attribute 'connect'`.

- [ ] **Step 4: Implement**

`python/src/skyrim/discovery.py`:
```python
"""Find the running plugin: explicit port+token, a session file, or environment variables."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import NotRunning


@dataclass(frozen=True)
class Session:
    host: str
    port: int
    token: str
    pid: int | None = None
    plugin_version: str | None = None


def _read(path: Path, host: str) -> Session:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRunning(f"no session file at {path}; is the game running with SkyrimPython.dll?") from None
    except (OSError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: {e}") from None
    try:
        return Session(host, int(data["port"]), str(data["token"]), data.get("pid"), data.get("plugin_version"))
    except (KeyError, TypeError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: missing {e}") from None


def find_session(session=None, *, host: str = "127.0.0.1", port: int | None = None, token: str | None = None,
                 env=os.environ) -> Session:
    if port is not None and token is not None:
        return Session(host, int(port), token)
    if session is not None:
        return _read(Path(session), host)
    if env.get("SKYRIM_PYTHON_SESSION"):
        return _read(Path(env["SKYRIM_PYTHON_SESSION"]), host)
    if env.get("SKYRIM_PYTHON_PORT") and env.get("SKYRIM_PYTHON_TOKEN"):
        return Session(host, int(env["SKYRIM_PYTHON_PORT"]), env["SKYRIM_PYTHON_TOKEN"])
    raise NotRunning("no session given: pass session=/port=+token=, or set SKYRIM_PYTHON_SESSION "
                     "(or SKYRIM_PYTHON_PORT and SKYRIM_PYTHON_TOKEN)")
```

`python/src/skyrim/client.py`:
```python
"""Synchronous client. One reader thread per connection; any thread may call."""
import itertools
import socket
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from . import _framing, errors
from .discovery import find_session
from .values import Alias, Form, from_wire, to_wire

PROTOCOL = [1, 0]
_GRACE = 2.0  # seconds the client waits beyond a server-side timeout


class Game:
    def __init__(self, sock: socket.socket, welcome: dict, *, call_timeout: float, retry_not_ready: float):
        self.welcome = welcome
        self.epoch: int = welcome.get("epoch", 0)
        self.closed = False
        self._sock = sock
        self._call_timeout = call_timeout
        self._retry_not_ready = retry_not_ready
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, name="skyrim-python-reader", daemon=True)
        self._reader.start()

    # -- plumbing -------------------------------------------------------------------------------
    def _read_loop(self):
        try:
            while True:
                msg = _framing.read_frame(self._sock)
                if msg.get("type") == "response":
                    with self._lock:
                        fut = self._pending.pop(msg.get("id"), None)
                    if fut is not None:
                        fut.set_result(msg)
                # "event" messages arrive in M2; M1 has no subscriptions, so none are sent.
        except Exception as e:  # noqa: BLE001 - any read failure ends the connection
            self._fail_all(errors.ConnectionLost(str(e) or type(e).__name__))

    def _fail_all(self, exc: Exception):
        with self._lock:
            self.closed = True
            pending, self._pending = self._pending, {}
        for fut in pending.values():
            fut.set_exception(exc)

    def _send_once(self, op: str, args: dict, wait: float) -> Any:
        fut: Future = Future()
        with self._lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            req_id = next(self._ids)
            self._pending[req_id] = fut
        try:
            with self._send_lock:
                self._sock.sendall(_framing.encode({"type": "request", "id": req_id, "op": op, "args": args}))
        except OSError as e:
            self._fail_all(errors.ConnectionLost(str(e)))
            raise errors.ConnectionLost(str(e)) from e
        try:
            resp = fut.result(timeout=wait)
        except FutureTimeout:
            with self._lock:
                self._pending.pop(req_id, None)
            raise errors.CallTimeout(f"{op}: no response after {wait:.1f}s (the call may still run in the game)") from None
        if resp.get("ok"):
            return resp.get("result")
        raise errors.remote_error_from_wire(resp.get("error", {}))

    def _request(self, op: str, args: dict, timeout: float | None = None) -> Any:
        wait = self._call_timeout if timeout is None else timeout + _GRACE
        deadline = time.monotonic() + self._retry_not_ready
        while True:
            try:
                return self._send_once(op, args, wait)
            except errors.NotReady:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    @staticmethod
    def _call_args(script, function, args, timeout, require_unpaused, obj=None) -> dict:
        a: dict[str, Any] = {}
        if obj is not None:
            a["object"] = to_wire(obj)
        a.update({"script": script, "function": function, "args": [to_wire(x) for x in args],
                  "require_unpaused": require_unpaused})
        if timeout is not None:
            a["timeout_ms"] = int(timeout * 1000)
        return a

    # -- public API -----------------------------------------------------------------------------
    def call(self, script: str, function: str, *args, timeout: float | None = None, require_unpaused: bool = False):
        """Call a global Papyrus function, e.g. game.call("Utility", "RandomInt", 1, 6)."""
        a = self._call_args(script, function, args, timeout, require_unpaused)
        return from_wire(self._request("papyrus.call_static", a, timeout))

    def call_method(self, obj: Form | Alias, script: str, function: str, *args, timeout: float | None = None,
                    require_unpaused: bool = False):
        """Call a member function on a form or alias, e.g. game.call_method(player, "Actor", "GetActorValue", "Health")."""
        a = self._call_args(script, function, args, timeout, require_unpaused, obj=obj)
        return from_wire(self._request("papyrus.call_method", a, timeout))

    def get_property(self, obj: Form | Alias, script: str, prop: str):
        return from_wire(self._request("papyrus.get_property", {"object": to_wire(obj), "script": script, "property": prop}))

    def set_property(self, obj: Form | Alias, script: str, prop: str, value) -> None:
        self._request("papyrus.set_property",
                      {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)})

    def batch(self) -> "Batch":
        return Batch(self)

    def resolve(self, *, form_key: str | None = None, form_id: int | None = None, editor_id: str | None = None) -> Form:
        given = {k: v for k, v in (("form_key", form_key), ("form_id", form_id), ("editor_id", editor_id)) if v is not None}
        if len(given) != 1:
            raise TypeError("resolve() takes exactly one of form_key, form_id, editor_id")
        return from_wire(self._request("form.resolve", given))

    def form(self, form_key: str) -> Form:
        """game.form("Skyrim.esm|0x012EB7") -> Form with the live form_id and type."""
        return self.resolve(form_key=form_key)

    def alias(self, quest: Form, alias_id: int, alias_type: str = "ReferenceAlias") -> Alias:
        return Alias(quest, alias_id, alias_type, quest.epoch)

    def state(self) -> dict:
        st = self._request("game.state", {})
        if st.get("player") is not None:
            st["player"] = from_wire(st["player"])
        return st

    def wait_ready(self, timeout: float = 120.0) -> dict:
        """Block until a save is loaded and no loading screen is up."""
        deadline = time.monotonic() + timeout
        while True:
            st = self.state()
            if st.get("interactive"):
                return st
            if time.monotonic() >= deadline:
                raise errors.CallTimeout(f"game not interactive after {timeout:.0f}s (last state: {st})")
            time.sleep(0.25)

    def close(self) -> None:
        with self._lock:
            self.closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._reader.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Batch:
    """Several papyrus.* steps in one round trip, run in order. Not a transaction (spec 4.3)."""

    def __init__(self, game: Game):
        self._game = game
        self._calls: list[dict] = []

    def call(self, script, function, *args, require_unpaused=False):
        self._calls.append({"op": "papyrus.call_static",
                            "args": Game._call_args(script, function, args, None, require_unpaused)})
        return self

    def call_method(self, obj, script, function, *args, require_unpaused=False):
        self._calls.append({"op": "papyrus.call_method",
                            "args": Game._call_args(script, function, args, None, require_unpaused, obj=obj)})
        return self

    def get_property(self, obj, script, prop):
        self._calls.append({"op": "papyrus.get_property",
                            "args": {"object": to_wire(obj), "script": script, "property": prop}})
        return self

    def set_property(self, obj, script, prop, value):
        self._calls.append({"op": "papyrus.set_property",
                            "args": {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)}})
        return self

    def run(self, stop_on_error: bool = True, timeout: float | None = None) -> list:
        if not self._calls:
            return []
        args: dict[str, Any] = {"calls": self._calls, "stop_on_error": stop_on_error}
        if timeout is not None:
            args["timeout_ms"] = int(timeout * 1000)
        out = self._game._request("papyrus.batch", args, timeout)
        return [from_wire(r.get("result")) if r.get("ok") else errors.remote_error_from_wire(r.get("error", {}))
                for r in out["results"]]


def connect(session=None, *, host: str = "127.0.0.1", port: int | None = None, token: str | None = None,
            client_name: str = "skyrim-python", connect_timeout: float = 5.0, call_timeout: float = 30.0,
            retry_not_ready: bool | float = False) -> Game:
    """Connect and handshake. Raises NotRunning, Unauthorized or ProtocolMismatch."""
    s = find_session(session, host=host, port=port, token=token)
    try:
        sock = socket.create_connection((s.host, s.port), timeout=connect_timeout)
    except OSError as e:
        raise errors.NotRunning(f"cannot connect to {s.host}:{s.port}: {e}") from None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(_framing.encode({"type": "hello", "protocol": PROTOCOL, "token": s.token, "client": client_name}))
        msg = _framing.read_frame(sock)
    except (socket.timeout, TimeoutError):
        sock.close()
        raise errors.NotRunning(f"{s.host}:{s.port} accepted but sent no welcome within {connect_timeout}s") from None
    except (OSError, errors.ConnectionLost) as e:
        sock.close()
        raise errors.NotRunning(f"{s.host}:{s.port} closed during the handshake: {e}") from None
    if msg.get("type") == "handshake_error":
        sock.close()
        raise errors.handshake_error_from_wire(msg)
    if msg.get("type") != "welcome":
        sock.close()
        raise errors.ProtocolViolation(f"expected welcome, got {msg.get('type')!r}")
    sock.settimeout(None)
    retry = 30.0 if retry_not_ready is True else float(retry_not_ready or 0)
    return Game(sock, msg, call_timeout=call_timeout, retry_not_ready=retry)
```

Replace `python/src/skyrim/__init__.py` with:
```python
"""Call Papyrus in a running Skyrim from Python."""
from .client import Batch, Game, connect  # noqa: F401
from .discovery import Session, find_session  # noqa: F401
from .errors import *  # noqa: F401,F403
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd python && uv run pytest -q`
Expected: all pass, in under 10 s.

- [ ] **Step 6: Commit**

```bash
git add python && git commit -m "feat(python): session discovery and synchronous client"
```
