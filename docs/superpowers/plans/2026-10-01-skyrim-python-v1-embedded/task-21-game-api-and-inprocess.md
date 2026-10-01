# Task 21: `Game` API and the in-process transport

Adapted from: branch `plan-out-of-process`, task 14 (`task-14-python-discovery-and-the-synchronous-client.md`): its `Game` and `Batch` classes, their timeout rules and their tests. The socket client, discovery and the fake socket server from that task are Task 22's.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2, 4.5 and 4.7). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 20. Contract-wise also on Task 19 (the `_skyrim` API), but no code from it: the tests run against `python/tests/fake_skyrim.py`, a pure-Python stand-in with the same API, so this task needs neither the C++ build nor the game. Tasks 22 (remote transport), 23 (bridge) and 24 (`skyrim._host`) build on it and reuse `fake_skyrim.py` and the `fake` fixture.

**What changed from the out-of-process plan.** The old `Game` owned a socket. Now `Game` (`skyrim/game.py`) owns no I/O: it builds request `args`, validates timeouts, retries `NotReady`, interprets `ok` and decodes values, and hands each request to a **Transport** (`request(op, args, wait_s) -> response dict`, `close()`, `welcome`, `closed`). The same `Game` therefore runs in the game over `InProcessTransport` (this task: JSON text through `_skyrim.call`, which releases the GIL while it waits) and on Linux over Task 22's `RemoteTransport`. Two behaviours differ by transport and are pinned here for the in-process side: a call whose Python-side wait runs out is `Timeout` with `detail.late_cancel = true` (the Contract for `_skyrim.call`), where the remote transport raises `CallTimeout`; and `retry_not_ready` moved from `connect()` into `Game`.

**Files:**
- Create: `python/src/skyrim/game.py`, `python/src/skyrim/inprocess.py`
- Modify: `python/src/skyrim/__init__.py` (full replacement below), `python/tests/support.py` (full replacement below: adds `wait_until`)
- Create: `python/tests/fake_skyrim.py`, `python/tests/conftest.py`, `python/tests/test_inprocess.py`, `python/tests/test_game.py`

**Interfaces:**
- Consumes: Task 20 (`_framing.dumps`, `errors`, `values`), `skyrim._protocol_generated` (Task 12: `OP_*`, `OPS`, `BATCHABLE_OPS`, `PROTOCOL_MAJOR/MINOR`, `PROTOCOL_VERSION`, `MIN_TIMEOUT_MS`, `MAX_TIMEOUT_MS`, `CONFIG_KEYS`, `CODE_*`, `DEFAULT_ALIAS_TYPE`, and the client defaults `CALL_TIMEOUT_S`, `RETRY_NOT_READY_S`, `READY_TIMEOUT_S`, `GRACE_S`), the golden `requests.json` (Task 11), and the `_skyrim` API from the index Contracts (`BACKEND`, `PROTOCOL`, `call`, `config`, `state`; the fake implements all of it).
- Produces:
  - `skyrim.game`: `class Transport(Protocol)` with `welcome: dict`, `closed: bool`, `request(op: str, args: dict, wait_s: float) -> dict` (sends `{"type": "request", "id", "op", "args"}` with an id the transport chooses; returns the decoded response object; raises `EncodeError` with nothing sent, `ConnectionLost`, `CallTimeout` or `ProtocolViolation`; never interprets `ok`), `close() -> None`. `class Game(transport, *, call_timeout=CALL_TIMEOUT_S, retry_not_ready: bool | float = False)` with `call(script, function, *args, timeout=None, require_unpaused=False)`, `call_method(obj, script, function, *args, timeout=None, require_unpaused=False)`, `get_property(obj, script, prop)`, `set_property(obj, script, prop, value)`, `batch() -> Batch`, `resolve(*, form_key=None, form_id=None, editor_id=None) -> Form`, `form(form_key) -> Form`, `alias(quest, alias_id, alias_type="ReferenceAlias") -> Alias`, `state() -> dict` (decodes `player`), `wait_ready(timeout=READY_TIMEOUT_S) -> dict` (polls `state()`; when `timeout` passes first it raises `NotReady` with `detail` = the last state plus `reason: "wait_ready timed out"`, in both modes, never `CallTimeout`), `close()`; attributes `transport`, `welcome`, `epoch` (from the welcome, fixed), property `closed` (the transport's); context manager. `retry_not_ready=True` means `RETRY_NOT_READY_S`; a `NotReady` whose `detail.dispatched` is true (a world reset ended the call after it reached the game, so its effect may have happened) is raised at once, never resubmitted. `call_timeout` must be a positive finite number (`ValueError`). `Batch` with `call`, `call_method`, `get_property`, `set_property` (each returns the batch and takes `timeout=`) and `run(stop_on_error=True, timeout=None) -> list`. Timeout rules (unchanged from branch task 14): a call's `timeout` is seconds, positive and finite, sent as whole milliseconds rounded up (`0.0004` is `1`) and at most `MAX_TIMEOUT_MS`, else `ValueError` before anything is sent; the Game waits `timeout + GRACE_S` for a call with a timeout and `call_timeout` for one without; a batch always sends `timeout_ms`: the explicit `run(timeout=)` (over `MAX_TIMEOUT_MS` is `ValueError`), else the sum of its step timeouts (the plugin's 5 s default for a step with none) capped at `MAX_TIMEOUT_MS`, and waits that plus `GRACE_S`.
  - `skyrim.inprocess`: `class InProcessTransport(module=None)` (imports `_skyrim` when `module` is not given; `ProtocolMismatch` when `module.PROTOCOL`'s major differs; `welcome = {"type": "welcome", "protocol", "plugin_version", "game_version", "runtime"}` from `_skyrim.config()`, `"epoch"` from `_skyrim.state()`, `"capabilities"` = the op names in `OPS`, `"backend"` = `_skyrim.BACKEND`; request ids from a lock-protected counter starting at 1; a response that is not a JSON object or carries another id is `ProtocolViolation`; after `close()` every request is `ConnectionLost`); `connect_in_process(*, module=None, call_timeout=CALL_TIMEOUT_S, retry_not_ready=False) -> Game`. Task 22's `skyrim.connect()` calls `connect_in_process` when `_skyrim.BACKEND == "game"`.
  - `skyrim` (package): exports `Game`, `Batch`, `Transport`, `InProcessTransport`, `connect_in_process`, the errors and the value types.
  - `python/tests/fake_skyrim.py` (for Tasks 21-24): `class FakeSkyrim(handler=None, *, root=None, config=None, default_timeout_ms=5000, max_inflight_total=1024)`, a module-like object with the Contract's `_skyrim` API: `BACKEND = "fake"`, `PROTOCOL`, `submit`, `wait`, `wait_any`, `cancel`, `call`, `state`, `config` (the generated ini defaults plus `python_dir`, `scripts_dir`, `site_packages_dir`, `log_dir`, `session_file` under `root`, `plugin_version "0.0.0-fake"`, `game_version "0.0.0"`, `runtime "fake"`, then `config` overrides), `log`, `in_flight`, `_fake_set_not_ready`, `_fake_bump_loads`, `_fake_set_paused`; plus for tests `requests` (every well-formed request object), `logs` (`(level, message)` pairs), `handler_calls`, `late_results`, `epoch`, `menus`, `base_state()`, `close()` (stops every waiting handler). Dispatcher rules it follows: bad request text → `bad_request` at once (id peeked, else 0); a `needs_game` op while not ready, or with `require_unpaused` while paused → `not_ready` at once with `detail {reason, paused, menus}`; more than `max_inflight_total` in flight → `busy`; `timeout_ms` and `require_unpaused` are read only when the op's `OPS[op]["args"]` lists them; deadline `args.timeout_ms` (valid range) else `default_timeout_ms` → `timeout` with `detail {paused, menus}` (a batch's detail also carries `completed` and `results` from the last `ctx.progress`); `_fake_bump_loads()` (a world reset) → every in-flight `needs_game` request `not_ready` with `detail {reason, paused, menus, dispatched: true}`, reason the current not-ready reason, else `loading`; a handler exception → `internal`; `cancel` erases the ticket (a later `wait` returns `None` at once) and the late result is counted in `late_results`; `call` that outlives its wait cancels (returning the response instead if it has just arrived) and answers `timeout` with `detail {paused, menus, late_cancel: true}`. A handler is `handler(op, args, ctx) -> result` run on its own daemon thread; it raises `FakeRemoteError(code, message="", detail=None)` to answer an error; `ctx.cancelled()`, `ctx.wait(seconds | None) -> bool` (True when stopped early), `ctx.progress(results)`, `ctx.fake`. The default handler `fake_handler` serves `game.state` (`base_state()` plus `"backend": "fake"`), `papyrus.batch` (runs its steps through itself, honouring `stop_on_error`) and `papyrus.call_static` on script `Fake`: `Echo(x)`, `Sleep(ms)`, `Fail(code, message)`, `Throw()`, `Never()`, `Count()`, the same functions as core's `FakeBackend` (Task 18); another `Fake` function is `unknown_function` and any other op or script is `unsupported`, as in `FakeBackend`.
  - `python/tests/conftest.py`: fixture `fake` (a `FakeSkyrim(root=tmp_path)` installed as `sys.modules["_skyrim"]`, closed afterwards) and fixture `game` (`connect_in_process()` over it). Task 22 appends its socket fixtures to this file.
  - `python/tests/support.py`: adds `wait_until(predicate, timeout=5.0, what="the condition")` (polls with a deadline, raises `AssertionError`).

- [ ] **Step 1: Test helpers and the fake `_skyrim`**

Replace the test helpers with:
`python/tests/support.py`:
```python
import json
import pathlib
import time

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def wait_until(predicate, timeout=5.0, what="the condition"):
    """Poll with a deadline; never spin forever."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(0.01)
```

The fake `_skyrim`. It mirrors core's `Dispatcher` rules (Task 18) closely enough that the bridge (Task 23) and `skyrim._host` (Task 24) can be tested on it; `test_inprocess.py` pins those rules.
`python/tests/fake_skyrim.py`:
```python
"""A pure-Python stand-in for the built-in `_skyrim` module (plan index, Contracts), for tests.

It speaks the same text as the real module: a request is the JSON text {"type": "request", "id", "op", "args"}
and a response is the JSON text {"type": "response", "id", "ok", "result" | "error"}. It behaves like core's
Dispatcher (Task 18) over FakeBackend:
- an op whose table entry has needs_game answers not_ready at submit while the fake is "not ready"
  (_fake_set_not_ready), or while it is paused and the request has require_unpaused;
- more than max_inflight_total requests in flight answers busy;
- every request has a deadline (args.timeout_ms, else default_timeout_ms) after which it completes with
  timeout (a batch's detail carries "completed" and "results" from its last progress report);
- timeout_ms and require_unpaused are read only when the op's table entry lists them as arguments;
- _fake_bump_loads() (a world reset: a save load or a quit to the main menu) fails every in-flight needs_game
  request with not_ready, reason the not-ready reason of that moment (else "loading"), detail.dispatched = true;
- every timeout and not_ready detail carries paused and menus;
- cancel() erases a ticket, a later wait on it returns None, and its late result is dropped and counted in
  late_results;
- call() that outlives its wait cancels the ticket (or returns the result that has just arrived) and answers
  timeout with detail.late_cancel = true, plus paused and menus.
Handlers run on their own daemon threads, so blocking handlers behave like slow game calls.

Use the `fake` fixture in conftest.py: it installs an instance as sys.modules["_skyrim"] and closes it
afterwards. Tasks 23 (bridge) and 24 (skyrim._host) reuse it.
"""
import itertools
import json
import pathlib
import tempfile
import threading
import time

from skyrim import _protocol_generated as _g

BACKEND = "fake"
_U64_MAX = 2**64 - 1


class FakeRemoteError(Exception):
    """Raise from a handler to answer with this error code."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail or {}


class Ctx:
    """What a handler gets besides op and args."""

    def __init__(self, fake: "FakeSkyrim", entry: "_Entry"):
        self.fake = fake
        self._entry = entry

    def cancelled(self) -> bool:
        """True once the request was cancelled, timed out, or failed by a load."""
        return self._entry.stop.is_set()

    def wait(self, seconds: float | None) -> bool:
        """Sleep up to `seconds` (None: until stopped). True if the request was stopped meanwhile."""
        return self._entry.stop.wait(seconds)

    def progress(self, results: list) -> None:
        """A batch reports the step results so far (used for a batch timeout's detail)."""
        self._entry.progress = list(results)


class _Entry:
    def __init__(self, req_id: int, op: str, needs_game: bool, deadline: float, loads: int):
        self.req_id, self.op, self.needs_game, self.deadline, self.loads = req_id, op, needs_game, deadline, loads
        self.dispatched = False  # the handler has started
        self.response: str | None = None
        self.progress: list | None = None
        self.stop = threading.Event()


def _response(req_id: int, ok: bool, payload, detail: dict | None = None, message: str = "") -> str:
    if ok:
        msg = {"type": "response", "id": req_id, "ok": True, "result": payload}
    else:
        msg = {"type": "response", "id": req_id, "ok": False,
               "error": {"code": payload, "message": message, "detail": detail or {}}}
    return json.dumps(msg, separators=(",", ":"), ensure_ascii=False)


def _peek_id(obj) -> int:
    i = obj.get("id") if isinstance(obj, dict) else None
    return i if type(i) is int and 0 <= i <= _U64_MAX else 0


def fake_handler(op: str, args: dict, ctx: Ctx):
    """The default handler: the same "Fake" script functions as core's FakeBackend, plus game.state and batch."""
    fake = ctx.fake
    if op == _g.OP_GAME_STATE:
        return {**fake.base_state(), "backend": BACKEND}
    if op == _g.OP_PAPYRUS_BATCH:
        results = []
        for step in args.get("calls", []):
            try:
                results.append({"ok": True, "result": fake_handler(step["op"], step["args"], ctx)})
            except FakeRemoteError as e:
                results.append({"ok": False, "error": {"code": e.code, "message": e.message, "detail": e.detail}})
                if args.get("stop_on_error", True):
                    break
            ctx.progress(results)
        return {"results": results}
    if op == _g.OP_PAPYRUS_CALL_STATIC and args.get("script") == "Fake":
        fn, a = args.get("function"), args.get("args", [])
        if fn == "Echo":
            return a[0] if a else None
        if fn == "Sleep":
            ctx.wait(a[0] / 1000)
            return a[0]
        if fn == "Fail":
            raise FakeRemoteError(a[0], a[1] if len(a) > 1 else "")
        if fn == "Throw":
            raise RuntimeError("Fake.Throw")
        if fn == "Never":
            ctx.wait(None)
            return None
        if fn == "Count":
            return fake.handler_calls
        raise FakeRemoteError(_g.CODE_UNKNOWN_FUNCTION, f"Fake has no function {fn}")
    raise FakeRemoteError(_g.CODE_UNSUPPORTED, f"the fake backend has no {op} {args.get('script')}.{args.get('function')}")


class FakeSkyrim:
    """Module-like: put an instance in sys.modules["_skyrim"] and `import _skyrim` returns it."""

    BACKEND = BACKEND
    PROTOCOL = _g.PROTOCOL_VERSION

    def __init__(self, handler=None, *, root: str | pathlib.Path | None = None, config: dict | None = None,
                 default_timeout_ms: int = _g.CONFIG_KEYS["default_timeout_ms"]["default"],
                 max_inflight_total: int = _g.CONFIG_KEYS["max_inflight_total"]["default"]):
        self.handler = handler or fake_handler
        self.root = pathlib.Path(root) if root is not None else pathlib.Path(tempfile.gettempdir()) / "skyrim-fake"
        self.config_overrides = dict(config or {})
        self.default_timeout_ms = default_timeout_ms
        self.max_inflight_total = max_inflight_total
        self.requests: list[dict] = []  # every well-formed request, in submit order
        self.logs: list[tuple[str, str]] = []
        self.handler_calls = 0
        self.late_results = 0
        self._epoch_base = 0
        self.menus: list[str] = []
        self._not_ready: str | None = None
        self._paused = False
        self._loads = 0
        self._tickets = itertools.count(1)
        self._entries: dict[int, _Entry] = {}
        self._cond = threading.Condition()

    @property
    def epoch(self) -> int:
        """Like FakeBackend::BaseState: the number of loads so far (0 at the start); tests may set it."""
        return self._epoch_base + self._loads

    @epoch.setter
    def epoch(self, value: int) -> None:
        self._epoch_base = value - self._loads

    # -- the _skyrim API ------------------------------------------------------------------------
    def submit(self, request: str) -> int:
        ticket = next(self._tickets)
        try:
            obj = json.loads(request)
        except (TypeError, ValueError):
            obj = None
        req_id = _peek_id(obj)
        problem = self._problem(obj)
        with self._cond:
            self._tick()
            if problem is not None:
                self._finish_now(ticket, req_id, _response(req_id, False, _g.CODE_BAD_REQUEST, message=problem))
                return ticket
            self.requests.append(obj)
            op, args = obj["op"], obj["args"]
            needs_game = _g.OPS[op]["needs_game"]
            declared = {name for name, _type, _required in _g.OPS[op]["args"]}  # other keys in args are ignored
            reason = self._not_ready
            if reason is None and self._paused and "require_unpaused" in declared and args.get("require_unpaused") is True:
                reason = "paused"
            if needs_game and reason is not None:
                detail = {"reason": reason, "paused": self._paused, "menus": list(self.menus)}
                self._finish_now(ticket, req_id, _response(req_id, False, _g.CODE_NOT_READY, detail, f"not ready: {reason}"))
                return ticket
            if self._in_flight() >= self.max_inflight_total:
                self._finish_now(ticket, req_id, _response(req_id, False, _g.CODE_BUSY, message="too many requests in flight"))
                return ticket
            timeout_ms = args.get("timeout_ms") if "timeout_ms" in declared else None
            if timeout_ms is None:  # an invalid declared timeout_ms was refused above as bad_request
                timeout_ms = self.default_timeout_ms
            entry = _Entry(req_id, op, needs_game, time.monotonic() + timeout_ms / 1000, self._loads)
            self._entries[ticket] = entry
        threading.Thread(target=self._run, args=(ticket, entry, op, args), daemon=True).start()
        return ticket

    def wait(self, ticket: int, timeout: float) -> str | None:
        done = self._wait([ticket], timeout)
        return done[0][1] if done else None

    def wait_any(self, tickets: list[int], timeout: float) -> list[tuple[int, str]]:
        return self._wait(list(tickets), timeout)

    def cancel(self, ticket: int) -> None:
        with self._cond:
            entry = self._entries.pop(ticket, None)
            if entry is not None:
                entry.stop.set()
            self._cond.notify_all()

    def call(self, request: str, timeout: float) -> str:
        ticket = self.submit(request)
        text = self.wait(ticket, timeout)
        if text is not None:
            return text
        with self._cond:  # like Dispatcher::CancelOrTake: a result that has just arrived is returned
            entry = self._entries.pop(ticket, None)
            if entry is not None:
                entry.stop.set()
                self._cond.notify_all()
                if entry.response is not None:
                    return entry.response
            detail = {"paused": self._paused, "menus": list(self.menus), "late_cancel": True}
        try:
            req_id = _peek_id(json.loads(request))
        except (TypeError, ValueError):
            req_id = 0
        return _response(req_id, False, _g.CODE_TIMEOUT, detail, f"no response within {timeout:g} s; the call was cancelled")

    def state(self) -> str:
        return json.dumps(self.base_state())

    def config(self) -> str:
        cfg = {name: spec["default"] for name, spec in _g.CONFIG_KEYS.items()}
        cfg.update({"python_dir": str(self.root / "python"), "scripts_dir": str(self.root / "scripts"),
                    "site_packages_dir": str(self.root / "site-packages"), "log_dir": str(self.root / "logs"),
                    "session_file": str(self.root / "session.json"), "plugin_version": "0.0.0-fake",
                    "game_version": "0.0.0", "runtime": "fake"})
        cfg.update(self.config_overrides)
        return json.dumps(cfg)

    def log(self, level: str, message: str) -> None:
        with self._cond:
            self.logs.append((level, message))

    def in_flight(self) -> int:
        with self._cond:
            self._tick()
            return self._in_flight()

    # -- test controls (the real fake build has the same three) ---------------------------------
    def _fake_set_not_ready(self, reason: str | None) -> None:
        with self._cond:
            self._not_ready = reason

    def _fake_bump_loads(self) -> None:
        with self._cond:
            self._loads += 1
            self._tick()
            self._cond.notify_all()

    def _fake_set_paused(self, flag: bool) -> None:
        with self._cond:
            self._paused = bool(flag)

    def base_state(self) -> dict:
        reason = self._not_ready
        return {"data_ready": reason != "starting", "game_loaded": reason is None,
                "loading": reason == "loading", "interactive": reason is None, "paused": self._paused,
                "menus": list(self.menus), "epoch": self.epoch}

    def close(self) -> None:
        """Stop every handler still waiting (tests call this from the fixture's teardown)."""
        with self._cond:
            for entry in self._entries.values():
                entry.stop.set()
            self._entries.clear()
            self._cond.notify_all()

    # -- internals ------------------------------------------------------------------------------
    @staticmethod
    def _problem(obj) -> str | None:
        if not isinstance(obj, dict):
            return "a request must be a JSON object"
        if obj.get("type") != "request":
            return "a request must have \"type\": \"request\""
        if type(obj.get("id")) is not int or not 0 <= obj["id"] <= _U64_MAX:
            return "a request id must be an integer in 0..2^64-1"
        if obj.get("op") not in _g.OPS:
            return f"unknown op {obj.get('op')!r}"
        if not isinstance(obj.get("args"), dict):
            return "args must be an object"
        return FakeSkyrim._op_problem(obj["op"], obj["args"])

    @staticmethod
    def _op_problem(op: str, args: dict) -> str | None:
        """The argument checks of core's validate_op_args / parse_batch_args that need no value codec."""
        declared = {name for name, _type, _required in _g.OPS[op]["args"]}
        if "timeout_ms" in declared and "timeout_ms" in args:
            t = args["timeout_ms"]
            if type(t) is not int or not _g.MIN_TIMEOUT_MS <= t <= _g.MAX_TIMEOUT_MS:
                return f"timeout_ms must be an integer from {_g.MIN_TIMEOUT_MS} to {_g.MAX_TIMEOUT_MS}"
        if "require_unpaused" in declared and "require_unpaused" in args and type(args["require_unpaused"]) is not bool:
            return "require_unpaused must be a boolean"
        names = ["script", "function"] if op in (_g.OP_PAPYRUS_CALL_STATIC, _g.OP_PAPYRUS_CALL_METHOD) else []
        if op in (_g.OP_PAPYRUS_GET_PROPERTY, _g.OP_PAPYRUS_SET_PROPERTY):
            names = ["script", "property"]
        for name in names:
            if not isinstance(args.get(name), str) or not args[name]:
                return f"missing or non-string field {name!r}"
        if op == _g.OP_PAPYRUS_BATCH:
            calls = args.get("calls")
            if not isinstance(calls, list) or not calls:
                return "batch needs a non-empty 'calls' array"
            if len(calls) > _g.MAX_BATCH_CALLS:
                return f"a batch may hold at most {_g.MAX_BATCH_CALLS} calls, got {len(calls)}"
            for step in calls:
                if not isinstance(step, dict) or step.get("op") not in _g.BATCHABLE_OPS:
                    return "a batch step must be a batchable papyrus.* op"
                step_args = step.get("args", {})
                if not isinstance(step_args, dict):
                    return "args must be an object"
                problem = FakeSkyrim._op_problem(step["op"], step_args)
                if problem is not None:
                    return problem
            if "stop_on_error" in args and type(args["stop_on_error"]) is not bool:
                return "stop_on_error must be a boolean"
        return None

    def _finish_now(self, ticket: int, req_id: int, text: str) -> None:
        entry = _Entry(req_id, "", False, 0.0, self._loads)
        entry.response = text
        entry.stop.set()
        self._entries[ticket] = entry
        self._cond.notify_all()

    def _in_flight(self) -> int:
        return sum(1 for e in self._entries.values() if e.response is None)

    def _complete(self, ticket: int, text: str) -> None:
        with self._cond:
            entry = self._entries.get(ticket)
            if entry is None or entry.response is not None:
                self.late_results += 1
                return
            entry.response = text
            entry.stop.set()
            self._cond.notify_all()

    def _run(self, ticket: int, entry: _Entry, op: str, args: dict) -> None:
        with self._cond:
            if entry.stop.is_set():  # it ended (timeout, load, cancel) before its thread got going: never run it
                return
            entry.dispatched = True
            self.handler_calls += 1
        try:
            result = self.handler(op, args, Ctx(self, entry))
            text = _response(entry.req_id, True, result)
        except FakeRemoteError as e:
            text = _response(entry.req_id, False, e.code, e.detail, e.message)
        except Exception as e:  # noqa: BLE001 - like the Dispatcher: an exception in the backend is internal
            text = _response(entry.req_id, False, _g.CODE_INTERNAL, message=f"{type(e).__name__}: {e}")
        self._complete(ticket, text)

    def _tick(self) -> None:
        """Deadlines and load cancellation; called with the lock held."""
        now = time.monotonic()
        for entry in self._entries.values():
            if entry.response is not None:
                continue
            detail = {"paused": self._paused, "menus": list(self.menus)}
            if entry.needs_game and entry.loads != self._loads:
                reason = self._not_ready or "loading"
                detail.update(reason=reason)
                if entry.dispatched:  # the handler had started: its effect may have happened
                    detail["dispatched"] = True
                entry.response = _response(entry.req_id, False, _g.CODE_NOT_READY, detail, f"not ready: {reason}")
            elif now >= entry.deadline:
                if entry.op == _g.OP_PAPYRUS_BATCH:
                    progress = entry.progress or []
                    detail.update(completed=len(progress), results=progress)
                entry.response = _response(entry.req_id, False, _g.CODE_TIMEOUT, detail, "the call did not finish in time")
            else:
                continue
            entry.stop.set()

    def _wait(self, tickets: list[int], timeout: float) -> list[tuple[int, str]]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                self._tick()
                done = []
                for t in tickets:
                    entry = self._entries.get(t)
                    if entry is not None and entry.response is not None:
                        del self._entries[t]
                        done.append((t, entry.response))
                remaining = deadline - time.monotonic()
                if done or remaining <= 0 or not any(t in self._entries for t in tickets):
                    return done  # an unknown or cancelled ticket never completes: do not wait for it
                self._cond.wait(min(remaining, 0.01))
```

`python/tests/conftest.py`:
```python
import sys

import pytest

from fake_skyrim import FakeSkyrim


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A FakeSkyrim installed as the `_skyrim` module; its directories live under tmp_path."""
    f = FakeSkyrim(root=tmp_path)
    monkeypatch.setitem(sys.modules, "_skyrim", f)
    yield f
    f.close()


@pytest.fixture
def game(fake):
    """A Game over the in-process transport, talking to `fake`."""
    from skyrim.inprocess import connect_in_process
    with connect_in_process() as g:
        yield g
```

- [ ] **Step 2: Write the failing tests**

The transport, and the fake's own rules:
`python/tests/test_inprocess.py`:
```python
"""The in-process transport, and the fake `_skyrim` it runs against in these tests (Tasks 23 and 24 rely on
the fake behaving like core's Dispatcher, so its rules are pinned here too)."""
import json
import threading
import time

import pytest

from fake_skyrim import FakeRemoteError, FakeSkyrim
from support import wait_until
from skyrim import _protocol_generated as g
from skyrim import errors
from skyrim.inprocess import InProcessTransport, connect_in_process


def req(op="papyrus.call_static", req_id=1, **args):
    return json.dumps({"type": "request", "id": req_id, "op": op, "args": args})


def fake_call(function, *a, req_id=1, **extra):
    return req("papyrus.call_static", req_id, script="Fake", function=function, args=list(a), **extra)


# -- the transport ---------------------------------------------------------------------------------------
def test_the_welcome_is_built_from_config_and_state(fake):
    fake.epoch = 4
    t = InProcessTransport()  # imports _skyrim, which the fixture installed
    assert t.welcome["protocol"] == [1, 0] and t.welcome["epoch"] == 4
    assert (t.welcome["plugin_version"], t.welcome["game_version"], t.welcome["runtime"]) == ("0.0.0-fake", "0.0.0", "fake")
    assert t.welcome["capabilities"] == list(g.OPS) and t.welcome["backend"] == "fake"
    assert t.closed is False


def test_another_protocol_major_is_refused(fake):
    fake.PROTOCOL = (2, 0)
    with pytest.raises(errors.ProtocolMismatch, match="2.0"):
        InProcessTransport()


def test_the_request_text_has_the_fixture_shape(fake):
    t = InProcessTransport()
    resp = t.request("game.state", {}, 5.0)
    assert resp["ok"] is True and resp["result"]["backend"] == "fake"
    sent = fake.requests[0]
    assert set(sent) == {"type", "id", "op", "args"} and sent["type"] == "request" and sent["id"] == resp["id"]
    assert t.request("game.state", {}, 5.0)["id"] == resp["id"] + 1


@pytest.mark.parametrize("bad", ["not json", "[1, 2]", '{"type": "response", "id": 999, "ok": true, "result": 1}'],
                         ids=["not-json", "not-object", "wrong-id"])
def test_a_malformed_response_is_a_protocol_violation(fake, bad):
    fake.call = lambda text, timeout: bad
    with pytest.raises(errors.ProtocolViolation):
        InProcessTransport().request("game.state", {}, 5.0)


def test_a_closed_transport_raises_connection_lost(fake):
    t = InProcessTransport()
    t.close()
    with pytest.raises(errors.ConnectionLost):
        t.request("game.state", {}, 5.0)
    assert fake.requests == []


def test_an_expired_wait_is_timeout_with_late_cancel_and_nothing_is_left_in_flight(fake):
    with connect_in_process(call_timeout=0.2) as game:
        t0 = time.monotonic()
        with pytest.raises(errors.Timeout) as ei:
            game.call("Fake", "Never")  # no timeout_ms: the fake's 5 s default would fire later
        assert time.monotonic() - t0 < 2
    assert ei.value.detail["late_cancel"] is True
    assert {"paused", "menus"} <= set(ei.value.detail)  # like the real module's late-cancel reply
    assert fake.in_flight() == 0


# -- the fake's Dispatcher rules -------------------------------------------------------------------------
def test_a_bad_request_is_answered_at_once(fake):
    for text, req_id in (("{nope", 0), ('{"type": "request", "id": 7, "op": "no.such.op", "args": {}}', 7),
                         ('{"id": 8, "op": "game.state", "args": {}}', 8)):
        resp = json.loads(fake.wait(fake.submit(text), 1.0))
        assert (resp["id"], resp["ok"], resp["error"]["code"]) == (req_id, False, "bad_request")


def test_not_ready_only_for_ops_that_need_a_game(fake):
    fake._fake_set_not_ready("loading")
    resp = json.loads(fake.call(fake_call("Echo", 1), 1.0))
    assert resp["error"]["code"] == "not_ready"
    assert resp["error"]["detail"] == {"reason": "loading", "paused": False, "menus": []}
    assert json.loads(fake.call(req("game.state"), 1.0))["result"]["interactive"] is False
    assert fake.handler_calls == 1  # only game.state reached the backend


def test_require_unpaused_while_paused_is_not_ready_paused(fake):
    fake._fake_set_paused(True)
    resp = json.loads(fake.call(fake_call("Echo", 1, require_unpaused=True), 1.0))
    assert resp["error"]["detail"]["reason"] == "paused"
    assert json.loads(fake.call(fake_call("Echo", 2), 1.0))["result"] == 2


def test_timeout_ms_is_the_deadline(fake):
    t0 = time.monotonic()
    resp = json.loads(fake.call(fake_call("Never", timeout_ms=100), 5.0))
    assert resp["error"]["code"] == "timeout" and time.monotonic() - t0 < 2
    assert {"paused", "menus"} <= set(resp["error"]["detail"])


def test_timeout_ms_and_require_unpaused_count_only_where_the_op_declares_them(fake):
    fake._fake_set_paused(True)
    fake.handler = lambda op, args, ctx: ctx.wait(0.3) or "done"
    text = req("papyrus.get_property", object={"kind": "form", "form_id": 20}, script="ObjectReference", property="X",
               timeout_ms=50, require_unpaused=True)
    resp = json.loads(fake.call(text, 5.0))
    assert resp["ok"] is True and resp["result"] == "done"  # neither refused as paused nor timed out after 50 ms


def test_a_batch_timeout_reports_its_progress(fake):
    calls = [{"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Echo", "args": [1]}},
             {"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Never", "args": []}}]
    resp = json.loads(fake.call(req("papyrus.batch", calls=calls, timeout_ms=200), 5.0))
    assert resp["error"]["code"] == "timeout"
    detail = resp["error"]["detail"]
    assert detail["completed"] == 1 and detail["results"] == [{"ok": True, "result": 1}]
    assert {"paused", "menus"} <= set(detail)


def test_busy_at_the_in_flight_cap(tmp_path):
    fake = FakeSkyrim(root=tmp_path, max_inflight_total=2)
    try:
        held = [fake.submit(fake_call("Never", req_id=i)) for i in (1, 2)]
        assert json.loads(fake.call(fake_call("Echo", 3, req_id=3), 1.0))["error"]["code"] == "busy"
        fake.cancel(held[0])
        assert json.loads(fake.call(fake_call("Echo", 4, req_id=4), 1.0))["result"] == 4
    finally:
        fake.close()


def test_a_load_fails_calls_in_flight_with_not_ready_loading(fake):
    ticket = fake.submit(fake_call("Never"))
    wait_until(lambda: fake.handler_calls == 1, what="the call to start")
    fake._fake_bump_loads()
    resp = json.loads(fake.wait(ticket, 1.0))
    assert resp["error"]["code"] == "not_ready" and resp["error"]["detail"]["reason"] == "loading"
    assert resp["error"]["detail"]["dispatched"] is True  # it had reached the backend
    assert {"paused", "menus"} <= set(resp["error"]["detail"])
    # A quit to the main menu also counts: the reason is the one of that moment.
    ticket = fake.submit(fake_call("Never", req_id=2))
    wait_until(lambda: fake.handler_calls == 2, what="the second call to start")
    fake._fake_set_not_ready("no_game")
    fake._fake_bump_loads()
    assert json.loads(fake.wait(ticket, 1.0))["error"]["detail"]["reason"] == "no_game"


def test_cancel_erases_the_ticket_and_the_late_result_is_counted(fake):
    ticket = fake.submit(fake_call("Sleep", 100))
    fake.cancel(ticket)
    t0 = time.monotonic()
    assert fake.wait(ticket, 2.0) is None and time.monotonic() - t0 < 0.5  # unknown tickets are not waited for
    wait_until(lambda: fake.late_results == 1, what="the late result")


def test_wait_any_returns_the_done_ones_as_soon_as_one_is_done(fake):
    slow = fake.submit(fake_call("Sleep", 300, req_id=1))
    quick = fake.submit(fake_call("Echo", "q", req_id=2))
    done = fake.wait_any([slow, quick], 5.0)
    assert [t for t, _ in done] == [quick] and json.loads(done[0][1])["result"] == "q"
    rest = fake.wait_any([slow], 5.0)
    assert [t for t, _ in rest] == [slow]
    assert fake.wait_any([slow, quick], 0.05) == []  # both consumed


def test_handler_errors_map_to_codes(fake):
    err = json.loads(fake.call(fake_call("Throw"), 1.0))["error"]
    assert err["code"] == "internal" and "Fake.Throw" in err["message"]
    assert json.loads(fake.call(fake_call("Nope"), 1.0))["error"]["code"] == "unknown_function"  # as FakeBackend

    def refuse(op, args, ctx):
        raise FakeRemoteError("vm_error", "refused", {"x": 1})

    fake.handler = refuse
    err = json.loads(fake.call(req("game.state"), 1.0))["error"]
    assert err == {"code": "vm_error", "message": "refused", "detail": {"x": 1}}


def test_config_state_and_log(fake, tmp_path):
    cfg = json.loads(fake.config())
    assert cfg["bridge_enabled"] is False and cfg["port"] == 45977 and cfg["log_dir"] == str(tmp_path / "logs")
    assert set(g.CONFIG_KEYS) <= set(cfg)
    assert json.loads(fake.state())["interactive"] is True
    fake.log("info", "hello")
    assert fake.logs == [("info", "hello")]


# -- many threads at once --------------------------------------------------------------------------------
def test_sixteen_threads_calling_at_once_get_their_own_answers(game, fake):
    results, failures = {}, []

    def worker(n):
        try:
            for i in range(25):
                assert game.call("Fake", "Echo", n * 1000 + i) == n * 1000 + i
            results[n] = True
        except Exception as e:  # noqa: BLE001 - reported below
            failures.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not failures and len(results) == 16
    assert fake.handler_calls == 16 * 25 and fake.in_flight() == 0


# -- argument checks and details that follow core's parser and Dispatcher --------------------------------
def test_a_batch_timeout_with_no_finished_step_still_has_completed_and_results(fake):
    calls = [{"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Never", "args": []}}]
    resp = json.loads(fake.call(req("papyrus.batch", calls=calls, timeout_ms=100), 5.0))
    detail = resp["error"]["detail"]
    assert resp["error"]["code"] == "timeout" and detail["completed"] == 0 and detail["results"] == []
    assert {"paused", "menus"} <= set(detail)
    resp = json.loads(fake.call(fake_call("Never", timeout_ms=100), 5.0))  # a single call has neither
    assert "completed" not in resp["error"]["detail"]


STEP = {"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Echo", "args": [1]}}


@pytest.mark.parametrize("text", [
    fake_call("Echo", 1, timeout_ms=0),
    fake_call("Echo", 1, timeout_ms=g.MAX_TIMEOUT_MS + 1),
    fake_call("Echo", 1, timeout_ms=1.5),
    fake_call("Echo", 1, timeout_ms="5"),
    fake_call("Echo", 1, timeout_ms=True),
    fake_call("Echo", 1, require_unpaused="yes"),
    req("papyrus.call_static", function="Echo"),
    req("papyrus.call_static", script="Fake"),
    req("papyrus.batch", calls=[]),
    req("papyrus.batch", calls=[STEP] * (g.MAX_BATCH_CALLS + 1)),
    req("papyrus.batch", calls=[{"op": "game.state", "args": {}}]),
    req("papyrus.batch", calls=[STEP], timeout_ms=0),
    req("papyrus.batch", calls=[STEP], stop_on_error="no"),
], ids=["t0", "tmax+1", "tfloat", "tstr", "tbool", "unpaused-str", "no-script", "no-function", "empty-batch",
        "big-batch", "unbatchable-step", "batch-t0", "stop-str"])
def test_the_parsers_argument_checks_answer_bad_request_at_submit(fake, text):
    resp = json.loads(fake.wait(fake.submit(text), 1.0))
    assert resp["error"]["code"] == "bad_request"
    assert fake.handler_calls == 0 and fake.requests == []


def test_a_call_that_ended_before_its_thread_started_never_runs_the_handler(fake):
    fake.handler = lambda op, args, ctx: 1 / 0
    ticket = fake.submit(fake_call("Echo", 1))
    fake.cancel(ticket)  # may or may not beat the thread; either way no result is returned
    fake._fake_bump_loads()
    time.sleep(0.1)
    assert fake.wait(ticket, 0.05) is None


def test_dispatched_only_when_the_handler_had_started(fake):
    started = threading.Event()
    gate = threading.Event()

    def handler(op, args, ctx):
        started.set()
        gate.wait(5)

    real_run = fake._run
    fake._run = lambda *a: (gate.wait(5), real_run(*a))  # hold the thread back before it starts
    ticket = fake.submit(fake_call("Echo", 1))
    fake.handler = handler
    fake._fake_bump_loads()
    resp = json.loads(fake.wait(ticket, 1.0))
    gate.set()
    assert resp["error"]["code"] == "not_ready" and "dispatched" not in resp["error"]["detail"]
    time.sleep(0.1)
    assert fake.handler_calls == 0 and not started.is_set()


def test_base_state_follows_the_loads_like_fake_backend(fake):
    assert fake.base_state()["epoch"] == 0
    fake._fake_set_not_ready("loading")
    st = fake.base_state()
    assert st["loading"] is True and st["game_loaded"] is False and st["interactive"] is False
    fake._fake_set_not_ready(None)
    fake._fake_bump_loads()
    assert fake.base_state()["epoch"] == 1 and fake.base_state()["game_loaded"] is True
    fake.epoch = 7
    assert json.loads(fake.state())["epoch"] == 7
```

The `Game` API, over the in-process transport and the fake. The request-shape checks replay the golden `requests.json`, so every op in the generated table must have a `Game` method that produces its fixture:
`python/tests/test_game.py`:
```python
"""The Game API (both modes share it), exercised over the in-process transport and the fake `_skyrim`.
The requests the Game builds are checked against the golden fixtures; transport-specific behaviour
(sockets, handshake) is Task 22's."""
import contextlib
import inspect
import pickle
import threading
import time

import pytest

from fake_skyrim import FakeRemoteError
from support import load_fixture, wait_until
from skyrim import _protocol_generated as g
from skyrim import errors
from skyrim.game import Game
from skyrim.inprocess import connect_in_process
from skyrim.values import INT_MAX, INT_MIN, Form, from_wire

REQUESTS = load_fixture("requests.json")


def echo(op, args, ctx):
    return {"op": op, "args": args}


def results(value):
    return lambda op, args, ctx: value


# -- calls -----------------------------------------------------------------------------------------------
def test_call_sends_a_request_and_returns_the_result(game, fake):
    fake.handler = echo
    r = game.call("Utility", "RandomInt", 1, 6)
    assert r == {"op": "papyrus.call_static", "args": {
        "script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": False}}
    assert fake.requests[0]["type"] == "request" and type(fake.requests[0]["id"]) is int


def test_call_method_sends_reference_and_timeout(game, fake):
    fake.handler = echo
    game.call_method(Form(form_id=20), "Actor", "GetActorValue", "Health", timeout=2.0)
    args = fake.requests[0]["args"]
    assert args["object"] == {"kind": "form", "form_id": 20}
    assert args["timeout_ms"] == 2000


def test_int_overflow_raises_before_sending(game, fake):
    with pytest.raises(errors.TypeMismatch):
        game.call("Utility", "RandomInt", 1, 2**31)
    assert fake.requests == []


def test_float_array_with_big_ints_is_sent(game, fake):
    fake.handler = echo
    game.call("X", "Y", [3_000_000_000, 1.5])
    assert fake.requests[0]["args"]["args"] == [{"array": "Float", "items": [3e9, 1.5]}]


def test_remote_errors_are_typed(game, fake):
    def handler(op, args, ctx):
        raise FakeRemoteError("not_ready", "loading", {"reason": "loading"})

    fake.handler = handler
    with pytest.raises(errors.NotReady) as ei:
        game.call("Game", "GetPlayer")
    assert ei.value.detail["reason"] == "loading"
    clone = pickle.loads(pickle.dumps(ei.value))
    assert isinstance(clone, errors.NotReady) and clone.detail == {"reason": "loading"}


def test_the_fakes_own_errors_reach_the_caller_typed(game, fake):
    fake._fake_set_not_ready("no_game")
    with pytest.raises(errors.NotReady) as ei:
        game.call("Fake", "Echo", 1)
    assert ei.value.detail["reason"] == "no_game"
    assert game.state()["interactive"] is False  # game.state does not need a game
    fake._fake_set_not_ready(None)
    with pytest.raises(errors.InternalError, match="Fake.Throw"):
        game.call("Fake", "Throw")
    with pytest.raises(errors.Unsupported):
        game.call("Nope", "Nope")
    t0 = time.monotonic()
    with pytest.raises(errors.Timeout):
        game.call("Fake", "Never", timeout=0.2)
    assert time.monotonic() - t0 < 2


def test_retry_not_ready_until_ok(fake):
    count = {"n": 0}

    def handler(op, args, ctx):
        count["n"] += 1
        if count["n"] < 3:
            raise FakeRemoteError("not_ready", "loading")
        return 7

    fake.handler = handler
    with connect_in_process(retry_not_ready=5.0) as game:
        assert game.call("Utility", "RandomInt", 1, 6) == 7
    assert count["n"] == 3


def test_retry_not_ready_never_resubmits_a_call_a_load_ended_after_dispatch(fake):
    # The call may already have had its effect (an AddItem ran) when the load cancelled it: retrying could repeat it.
    out = {}

    def caller():
        with connect_in_process(retry_not_ready=5.0) as game:
            try:
                game.call("Fake", "Never", timeout=3)
            except Exception as e:  # noqa: BLE001 - checked below
                out["e"] = e

    t = threading.Thread(target=caller)
    t.start()
    wait_until(lambda: fake.handler_calls == 1, what="the call to reach the backend")
    fake._fake_bump_loads()
    t.join(10)
    assert isinstance(out.get("e"), errors.NotReady) and out["e"].detail["dispatched"] is True
    assert len(fake.requests) == 1  # not resubmitted


def test_answers_that_finish_out_of_order_reach_the_right_callers(game, fake):
    def handler(op, args, ctx):
        n = args["args"][0]
        ctx.wait(0.3 if n == 1 else 0.0)  # the first call finishes last
        return n

    fake.handler = handler
    got = {}
    threads = [threading.Thread(target=lambda i=i: got.__setitem__(i, game.call("X", "Y", i))) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert got == {1: 1, 2: 2}


def test_oversize_request_is_a_typed_error_and_nothing_is_sent(game, fake):
    with pytest.raises(errors.EncodeError, match="limit"):
        game.call("X", "Y", "a" * (16 * 1024 * 1024))
    fake.handler = echo
    assert game.call("X", "Y", 1)["args"]["args"] == [1]
    assert len(fake.requests) == 1


def test_unencodable_string_is_a_typed_error_and_nothing_is_sent(game, fake):
    with pytest.raises(errors.EncodeError, match="Unicode"):
        game.call("X", "Y", "bad \ud800 text")
    assert fake.requests == []


def test_malformed_results_are_violations_not_crashes(game, fake):
    for bad in ({"results": "nope"}, {"results": [1]}, {"no": "results"}):
        fake.handler = results(bad)
        with pytest.raises(errors.ProtocolViolation):
            game.batch().call("X", "Y").run()
    fake.handler = results([1, 2])
    with pytest.raises(errors.ProtocolViolation):
        game.state()
    fake.handler = results({"results": []})
    assert game.batch().call("X", "Y").run() == []


def test_a_response_without_a_boolean_ok_is_a_violation(fake):
    fake.call = lambda text, timeout: '{"type": "response", "id": 1, "result": 1}'
    with connect_in_process() as game:
        with pytest.raises(errors.ProtocolViolation, match="ok"):
            game.call("X", "Y")


def test_close_and_closed(fake):
    game = connect_in_process()
    assert game.closed is False
    with game:
        pass
    assert game.closed is True
    with pytest.raises(errors.ConnectionLost):
        game.call("Fake", "Echo", 1)


# -- timeout arithmetic ----------------------------------------------------------------------------------
def test_tiny_timeout_is_one_millisecond_not_zero(game, fake):
    fake.handler = echo
    for t in (0.0004, 1.001, 0.3):
        with contextlib.suppress(errors.Timeout):  # 1 ms is a real deadline for the fake; only what is sent matters
            game.call("X", "Y", timeout=t)
    assert [r["args"]["timeout_ms"] for r in fake.requests] == [1, 1001, 300]


@pytest.mark.parametrize("bad", [0, -1, 0.0, float("nan"), float("inf"), "5", True, 5_000_000, 3600.001])
def test_invalid_timeouts_raise_before_sending(game, fake, bad):
    with pytest.raises(ValueError):
        game.call("X", "Y", timeout=bad)
    assert fake.requests == []


def test_the_timeout_range_is_the_protocols_one_hour(game, fake):
    fake.handler = echo
    game.call("X", "Y", timeout=3600)
    with contextlib.suppress(errors.Timeout):
        game.call("X", "Y", timeout=0.001)
    with pytest.raises(ValueError, match="3600000"):
        game.call("X", "Y", timeout=3600.001)
    with pytest.raises(ValueError):  # an explicit batch timeout over the hour; step timeouts that add up to more are capped
        game.batch().call("A", "b").run(timeout=3600.001)
    assert [r["args"]["timeout_ms"] for r in fake.requests] == [3_600_000, 1]


@pytest.mark.parametrize("bad", [0, -1, float("nan"), None, "5"])
def test_bad_call_timeout_is_a_value_error(fake, bad):
    with pytest.raises(ValueError):
        connect_in_process(call_timeout=bad)


def test_defaults_are_the_tables_and_the_v1_values():
    p = inspect.signature(Game).parameters
    assert p["call_timeout"].default == g.CALL_TIMEOUT_S == 30.0
    assert p["retry_not_ready"].default is False
    assert inspect.signature(Game.wait_ready).parameters["timeout"].default == g.READY_TIMEOUT_S == 120.0
    assert (g.RETRY_NOT_READY_S, g.GRACE_S) == (30.0, 2.0)
    assert inspect.signature(connect_in_process).parameters["call_timeout"].default == g.CALL_TIMEOUT_S


def test_batch_wait_covers_the_default_step_timeouts(fake):
    # call_timeout is 0.3 s but one batch step may legitimately take the plugin's 5 s default
    def handler(op, args, ctx):
        ctx.wait(0.6)
        return {"results": [{"ok": True, "result": 3}]}

    fake.handler = handler
    with connect_in_process(call_timeout=0.3) as game:
        assert game.batch().call("Utility", "RandomInt", 1, 6).run() == [3]
        with pytest.raises(errors.Timeout) as ei:  # single calls still use call_timeout
            game.call("Utility", "RandomInt", 1, 6)
        assert ei.value.detail["late_cancel"] is True


def test_batch_timeout_is_the_sum_of_the_step_timeouts(game, fake):
    fake.handler = results({"results": []})
    game.batch().call("A", "b", timeout=2).call("C", "d").call_method(Form(form_id=1), "E", "f", timeout=0.5).run()
    game.batch().call("A", "b", timeout=2).run(timeout=9)
    game.batch().call("A", "b").call("C", "d").run()
    game.batch().call("A", "b", timeout=3000).call("C", "d", timeout=3000).run()
    with pytest.raises(ValueError):
        game.batch().call("A", "b", timeout=0)
    sent = [r["args"] for r in fake.requests]
    assert sent[0]["timeout_ms"] == 2000 + 5000 + 500
    assert all("timeout_ms" not in c["args"] for c in sent[0]["calls"])
    assert sent[1]["timeout_ms"] == 9000
    assert sent[2]["timeout_ms"] == 2 * 5000  # no timeouts at all: still the sum, so the plugin waits as long as we do
    assert sent[3]["timeout_ms"] == g.MAX_TIMEOUT_MS  # the sum is capped at the protocol maximum


# -- results ---------------------------------------------------------------------------------------------
def test_batch_results_mix_values_and_errors(game, fake):
    b = game.batch().call("Fake", "Echo", 3).call("Nope", "Nope")
    out = b.run(stop_on_error=False)
    assert out[0] == 3 and isinstance(out[1], errors.Unsupported)
    sent = fake.requests[0]["args"]
    assert sent["stop_on_error"] is False
    assert [c["op"] for c in sent["calls"]] == ["papyrus.call_static", "papyrus.call_static"]


def test_form_decodes_result(game, fake):
    fake.handler = results({"kind": "form", "form_id": 0x12EB7, "form_key": "Skyrim.esm|0x012EB7", "type": "Weapon", "epoch": 1})
    f = game.form("Skyrim.esm|0x012EB7")
    assert f.form_id == 0x12EB7 and f.type == "Weapon"
    assert fake.requests[0]["args"] == {"form_key": "Skyrim.esm|0x012EB7"}
    with pytest.raises(TypeError):
        game.resolve(form_key="Skyrim.esm|0x1", form_id=1)


def test_state_decodes_the_player(game, fake):
    fake.handler = results({"interactive": True, "epoch": 1, "player": {"kind": "form", "form_id": 20, "type": "Actor"}})
    assert game.state()["player"] == Form(form_id=20)


def test_wait_ready_polls_state(game, fake):
    states = iter([False, False, True])
    fake.handler = lambda op, args, ctx: {"interactive": next(states), "player": None, "epoch": 1}
    assert game.wait_ready(timeout=5)["interactive"] is True


def test_wait_ready_gives_up_with_not_ready_not_call_timeout(game, fake):
    fake._fake_set_not_ready("no_game")
    with pytest.raises(errors.NotReady) as ei:  # CallTimeout means the bridge did not answer; this is the game
        game.wait_ready(timeout=0.3)
    assert ei.value.detail["reason"] == "wait_ready timed out"
    assert ei.value.detail["interactive"] is False


def test_game_epoch_is_the_welcome_value_only(game, fake):
    fake.handler = results({"interactive": True, "epoch": 5})
    assert game.epoch == 0
    assert game.state()["epoch"] == 5  # the game has loaded a save since the Game was made
    assert game.epoch == 0  # documented: Game.epoch never follows


def test_alias_takes_the_quests_epoch(game):
    q = Form(form_id=0x800, epoch=3)
    a = game.alias(q, 2, "LocationAlias")
    assert (a.quest, a.alias_id, a.alias_type, a.epoch) == (q, 2, "LocationAlias", 3)


# -- the golden request fixtures ------------------------------------------------------------------------
OPTIONAL_KEYS = {"timeout_ms", "require_unpaused"}  # the Game may add these to an args object


def covers(actual, expected, path="args"):
    """`actual` (what the Game sent) equals `expected` (the fixture), allowing the optional keys."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        for k in expected:
            assert k in actual, f"{path}: missing {k!r}"
            covers(actual[k], expected[k], f"{path}.{k}")
        assert set(actual) - set(expected) <= OPTIONAL_KEYS, f"{path}: unexpected keys {set(actual) - set(expected)}"
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected)):
            covers(a, e, f"{path}[{i}]")
    else:
        assert type(actual) is type(expected) and actual == expected, f"{path}: {actual!r} != {expected!r}"


def _values(a):
    return [from_wire(x) for x in a.get("args", [])]


def _secs(a):
    return a["timeout_ms"] / 1000 if "timeout_ms" in a else None


def _call_static(game, a):
    return game.call(a["script"], a["function"], *_values(a), timeout=_secs(a), require_unpaused=a.get("require_unpaused", False))


def _call_method(game, a):
    return game.call_method(from_wire(a["object"]), a["script"], a["function"], *_values(a), timeout=_secs(a),
                            require_unpaused=a.get("require_unpaused", False))


STEPS = {
    "papyrus.call_static": lambda b, a: b.call(a["script"], a["function"], *_values(a), require_unpaused=a.get("require_unpaused", False)),
    "papyrus.call_method": lambda b, a: b.call_method(from_wire(a["object"]), a["script"], a["function"], *_values(a),
                                                      require_unpaused=a.get("require_unpaused", False)),
    "papyrus.get_property": lambda b, a: b.get_property(from_wire(a["object"]), a["script"], a["property"]),
    "papyrus.set_property": lambda b, a: b.set_property(from_wire(a["object"]), a["script"], a["property"], from_wire(a["value"])),
}


def _batch(game, a):
    b = game.batch()
    for step in a["calls"]:
        STEPS[step["op"]](b, step["args"])
    return b.run(stop_on_error=a["stop_on_error"], timeout=_secs(a))


CALLERS = {
    "game.state": lambda game, a: game.state(),
    "form.resolve": lambda game, a: game.resolve(**a),
    "papyrus.call_static": _call_static,
    "papyrus.call_method": _call_method,
    "papyrus.get_property": lambda game, a: game.get_property(from_wire(a["object"]), a["script"], a["property"]),
    "papyrus.set_property": lambda game, a: game.set_property(from_wire(a["object"]), a["script"], a["property"], from_wire(a["value"])),
    "papyrus.batch": _batch,
}


def replay_handler(op, args, ctx):
    return {"results": []} if op == "papyrus.batch" else {}


@pytest.mark.parametrize("fx", REQUESTS["valid"], ids=lambda r: f"{r['id']}-{r['op']}")
def test_valid_request_fixtures_replay(game, fake, fx):
    fake.handler = replay_handler
    CALLERS[fx["op"]](game, fx["args"])
    sent = fake.requests[0]
    assert set(sent) == set(fx) == {"type", "id", "op", "args"}
    assert sent["type"] == "request" and sent["op"] == fx["op"]
    assert type(sent["id"]) is int and sent["id"] >= 0
    covers(sent["args"], fx["args"])
    assert set(sent["args"]) <= {a[0] for a in g.OPS[sent["op"]]["args"]}, "the Game sent an argument the table does not know"


def test_the_game_speaks_exactly_the_ops_in_the_table():
    assert set(CALLERS) == set(g.OPS)
    assert set(STEPS) == set(g.BATCHABLE_OPS)
    assert {fx["op"] for fx in REQUESTS["valid"]} == set(g.OPS)


def has_out_of_range_int(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return not INT_MIN <= v <= INT_MAX
    if isinstance(v, dict):
        return any(has_out_of_range_int(x) for x in v.values())
    if isinstance(v, list):
        return any(has_out_of_range_int(x) for x in v)
    return False


# Only the type_mismatch cases the Game can spot without a signature table (an Int out of 32-bit range);
# other type_mismatch fixtures are the server's to reject.
TYPE_MISMATCH = [c["message"] for c in REQUESTS["invalid"]
                 if c["error"] == "type_mismatch" and c["message"].get("op") in CALLERS and has_out_of_range_int(c["message"]["args"])]


@pytest.mark.parametrize("msg", TYPE_MISMATCH, ids=lambda m: f"{m['id']}-{m['op']}")
def test_type_mismatch_fixtures_are_refused_before_sending(game, fake, msg):
    fake.handler = replay_handler
    with pytest.raises(errors.TypeMismatch):
        CALLERS[msg["op"]](game, msg["args"])
    assert fake.requests == []


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), "5", None, [], -0.5])
def test_bad_retry_not_ready_is_a_value_error(fake, bad):
    with pytest.raises(ValueError):
        connect_in_process(retry_not_ready=bad)


@pytest.mark.parametrize("ok", [False, 0, 0.0, True, 2.5])
def test_good_retry_not_ready_is_accepted(fake, ok):
    connect_in_process(retry_not_ready=ok).close()
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run --python 3.14 pytest -q tests/test_game.py tests/test_inprocess.py`
Expected: FAIL with `2 errors during collection`: `tests/test_game.py` raises `ModuleNotFoundError: No module named 'skyrim.game'` and `tests/test_inprocess.py` raises `ModuleNotFoundError: No module named 'skyrim.inprocess'`.

- [ ] **Step 4: Implement**

`python/src/skyrim/game.py`:
```python
"""The Game API, the same in both modes. A Game talks to the game through a Transport:
skyrim.inprocess.InProcessTransport inside the game (over the built-in _skyrim module), or
skyrim.remote.RemoteTransport from another process (over the dev bridge, Task 22)."""
import math
import time
from typing import Any, Protocol

from . import _protocol_generated as _g
from . import errors
from .values import Alias, Form, from_wire, to_wire

_GRACE = _g.GRACE_S  # seconds the Game waits beyond a server-side timeout
_STEP_TIMEOUT = _g.CONFIG_KEYS["default_timeout_ms"]["default"] / 1000  # the plugin's default, to size a batch's wait


class Transport(Protocol):
    """How a Game reaches the game. request() sends {"type": "request", "id", "op", "args"} with an id the
    transport chooses and returns the decoded response object {"type": "response", "id", "ok", ...}. It raises
    EncodeError (nothing sent), ConnectionLost, CallTimeout or ProtocolViolation; it never interprets "ok"."""

    welcome: dict
    closed: bool

    def request(self, op: str, args: dict, wait_s: float) -> dict: ...

    def close(self) -> None: ...


def _positive_seconds(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {value!r}")
    return float(value)


def _timeout_ms(timeout: float) -> int:
    """Seconds -> whole milliseconds, rounded UP so that 0.0004 s is 1 ms, never the 0 that means nothing."""
    _positive_seconds(timeout, "timeout")
    ms = max(_g.MIN_TIMEOUT_MS, math.ceil(round(timeout * 1000, 6)))  # round() first: 1.001 * 1000 is 1000.9999999999999
    if ms > _g.MAX_TIMEOUT_MS:
        raise ValueError(f"timeout of {timeout} s is over the protocol maximum of {_g.MAX_TIMEOUT_MS} ms")
    return ms


def _expect_dict(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise errors.ProtocolViolation(f"{what} from the server is not an object: {value!r}")
    return value


def _result(resp: dict, op: str) -> Any:
    ok = resp.get("ok")
    if ok is True:
        return resp.get("result")
    if ok is False:
        raise errors.remote_error_from_wire(resp.get("error"))
    raise errors.ProtocolViolation(f"{op}: response has no boolean \"ok\": {resp!r}")


class Game:
    """The running game. Thread-safe: any thread may call. Use as a context manager or call close()."""

    def __init__(self, transport: Transport, *, call_timeout: float = _g.CALL_TIMEOUT_S,
                 retry_not_ready: bool | float = False):
        self._call_timeout = _positive_seconds(call_timeout, "call_timeout")
        if retry_not_ready is True:
            self._retry_not_ready = _g.RETRY_NOT_READY_S
        elif retry_not_ready is False or retry_not_ready == 0 and not isinstance(retry_not_ready, str):
            self._retry_not_ready = 0.0
        else:
            self._retry_not_ready = _positive_seconds(retry_not_ready, "retry_not_ready")
        self.transport = transport
        self.welcome: dict = transport.welcome
        #: The epoch when this Game was made, fixed for its life. It does NOT follow save loads: read
        #: state()["epoch"] for the current one.
        self.epoch: int = self.welcome.get("epoch", 0)

    @property
    def closed(self) -> bool:
        return self.transport.closed

    # -- plumbing -------------------------------------------------------------------------------
    def _request(self, op: str, args: dict, timeout: float | None = None) -> Any:
        wait = self._call_timeout if timeout is None else timeout + _GRACE
        deadline = time.monotonic() + self._retry_not_ready
        while True:
            try:
                return _result(self.transport.request(op, args, wait), op)
            except errors.NotReady as e:
                # A call a world reset ended after it reached the game (detail.dispatched) may have had its effect
                # already: never resubmit it. A refusal before dispatch is safe to retry.
                if e.detail.get("dispatched") or time.monotonic() >= deadline:
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
            a["timeout_ms"] = _timeout_ms(timeout)
        return a

    # -- public API -----------------------------------------------------------------------------
    def call(self, script: str, function: str, *args, timeout: float | None = None, require_unpaused: bool = False):
        """Call a global Papyrus function, e.g. game.call("Utility", "RandomInt", 1, 6).

        A Python int is sent as a Papyrus Int: pass 3.0, not 3, where the parameter is a Float (see to_wire)."""
        a = self._call_args(script, function, args, timeout, require_unpaused)
        return from_wire(self._request(_g.OP_PAPYRUS_CALL_STATIC, a, timeout))

    def call_method(self, obj: Form | Alias, script: str, function: str, *args, timeout: float | None = None,
                    require_unpaused: bool = False):
        """Call a member function on a form or alias, e.g. game.call_method(player, "Actor", "GetActorValue", "Health")."""
        a = self._call_args(script, function, args, timeout, require_unpaused, obj=obj)
        return from_wire(self._request(_g.OP_PAPYRUS_CALL_METHOD, a, timeout))

    def get_property(self, obj: Form | Alias, script: str, prop: str):
        return from_wire(self._request(_g.OP_PAPYRUS_GET_PROPERTY, {"object": to_wire(obj), "script": script, "property": prop}))

    def set_property(self, obj: Form | Alias, script: str, prop: str, value) -> None:
        self._request(_g.OP_PAPYRUS_SET_PROPERTY,
                      {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)})

    def batch(self) -> "Batch":
        return Batch(self)

    def resolve(self, *, form_key: str | None = None, form_id: int | None = None, editor_id: str | None = None) -> Form:
        given = {k: v for k, v in (("form_key", form_key), ("form_id", form_id), ("editor_id", editor_id)) if v is not None}
        if len(given) != 1:
            raise TypeError("resolve() takes exactly one of form_key, form_id, editor_id")
        return from_wire(self._request(_g.OP_FORM_RESOLVE, given))

    def form(self, form_key: str) -> Form:
        """game.form("Skyrim.esm|0x012EB7") -> Form with the live form_id and type."""
        return self.resolve(form_key=form_key)

    def alias(self, quest: Form, alias_id: int, alias_type: str = _g.DEFAULT_ALIAS_TYPE) -> Alias:
        return Alias(quest, alias_id, alias_type, quest.epoch)

    def state(self) -> dict:
        st = _expect_dict(self._request(_g.OP_GAME_STATE, {}), "game.state result")
        if st.get("player") is not None:
            st["player"] = from_wire(st["player"])
        return st

    def wait_ready(self, timeout: float = _g.READY_TIMEOUT_S) -> dict:
        """Block until a save is loaded and no loading screen is up. If that takes longer than `timeout`, raise
        NotReady with detail reason "wait_ready timed out" plus the last state (in both modes; CallTimeout means
        the bridge itself did not answer)."""
        deadline = time.monotonic() + timeout
        while True:
            st = self.state()
            if st.get("interactive"):
                return st
            if time.monotonic() >= deadline:
                raise errors.NotReady(_g.CODE_NOT_READY, f"game not interactive after {timeout:g} s",
                                      {**st, "reason": "wait_ready timed out"})
            time.sleep(0.25)

    def close(self) -> None:
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Batch:
    """Several papyrus.* steps in one request, run in order. Not a transaction (spec 4.3).

    Each step method takes an optional `timeout` (seconds). The plugin applies one timeout to the whole
    batch, so run() always sends one: run(timeout=...) if given, else the SUM of the step timeouts (5 s for
    a step with none, the plugin's default), capped at MAX_TIMEOUT_MS; it waits that long plus GRACE_S."""

    def __init__(self, game: Game):
        self._game = game
        self._calls: list[dict] = []
        self._step_timeouts: list[float | None] = []

    def _add(self, op: str, args: dict, timeout: float | None):
        if timeout is not None:
            _timeout_ms(timeout)  # validate now, not at run()
        self._calls.append({"op": op, "args": args})
        self._step_timeouts.append(timeout)
        return self

    def call(self, script, function, *args, timeout=None, require_unpaused=False):
        return self._add(_g.OP_PAPYRUS_CALL_STATIC, Game._call_args(script, function, args, None, require_unpaused), timeout)

    def call_method(self, obj, script, function, *args, timeout=None, require_unpaused=False):
        return self._add(_g.OP_PAPYRUS_CALL_METHOD,
                         Game._call_args(script, function, args, None, require_unpaused, obj=obj), timeout)

    def get_property(self, obj, script, prop, *, timeout=None):
        return self._add(_g.OP_PAPYRUS_GET_PROPERTY, {"object": to_wire(obj), "script": script, "property": prop}, timeout)

    def set_property(self, obj, script, prop, value, *, timeout=None):
        return self._add(_g.OP_PAPYRUS_SET_PROPERTY,
                         {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)}, timeout)

    def run(self, stop_on_error: bool = True, timeout: float | None = None) -> list:
        if not self._calls:
            return []
        args: dict[str, Any] = {"calls": self._calls, "stop_on_error": stop_on_error}
        if timeout is None:  # without one the plugin would give the whole batch a single step's 5 s default
            timeout = min(sum(_STEP_TIMEOUT if t is None else t for t in self._step_timeouts), _g.MAX_TIMEOUT_MS / 1000)
        args["timeout_ms"] = _timeout_ms(timeout)
        out = _expect_dict(self._game._request(_g.OP_PAPYRUS_BATCH, args, timeout), "papyrus.batch result")
        results = out.get("results")
        if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
            raise errors.ProtocolViolation(f"papyrus.batch result has no list of result objects: {out!r}")
        return [from_wire(r.get("result")) if r.get("ok") is True else errors.remote_error_from_wire(r.get("error"))
                for r in results]
```

`python/src/skyrim/inprocess.py`:
```python
"""The in-process transport: the Game API inside the game, over the built-in _skyrim module.

Requests and responses are the same JSON text as on the dev bridge (protocol/PROTOCOL.md). _skyrim.call
releases the GIL while it waits, so any number of Python threads may call at once; the game's own threads
never run Python."""
import itertools
import json
import threading

from . import _framing
from . import _protocol_generated as _g
from . import errors
from .game import Game


def _object(text, what: str) -> dict:
    try:
        obj = json.loads(text)
    except (TypeError, ValueError) as e:
        raise errors.ProtocolViolation(f"{what} is not JSON: {e}") from None
    if not isinstance(obj, dict):
        raise errors.ProtocolViolation(f"{what} is not a JSON object: {obj!r}")
    return obj


class InProcessTransport:
    """A Transport over `module` (the _skyrim module; imported when not given)."""

    def __init__(self, module=None):
        if module is None:
            import _skyrim as module  # built into SkyrimPythonCore.dll; a fake in tests
        self._m = module
        proto = tuple(module.PROTOCOL)
        if len(proto) != 2 or proto[0] != _g.PROTOCOL_MAJOR:
            raise errors.ProtocolMismatch(_g.CODE_PROTOCOL_MISMATCH, f"_skyrim speaks protocol {'.'.join(map(str, proto))}, "
                                          f"this package speaks {_g.PROTOCOL_MAJOR}.{_g.PROTOCOL_MINOR}")
        cfg = _object(module.config(), "_skyrim.config()")
        state = _object(module.state(), "_skyrim.state()")
        self.welcome = {"type": "welcome", "protocol": list(proto), "plugin_version": cfg.get("plugin_version"),
                        "game_version": cfg.get("game_version"), "runtime": cfg.get("runtime"),
                        "epoch": state.get("epoch", 0), "capabilities": list(_g.OPS), "backend": module.BACKEND}
        self.closed = False
        self._ids = itertools.count(1)
        self._ids_lock = threading.Lock()

    def request(self, op: str, args: dict, wait_s: float) -> dict:
        if self.closed:
            raise errors.ConnectionLost("the in-process transport is closed")
        with self._ids_lock:
            req_id = next(self._ids)
        text = _framing.dumps({"type": "request", "id": req_id, "op": op, "args": args})  # EncodeError: nothing sent
        resp = _object(self._m.call(text, wait_s), f"the response to {op}")
        if type(resp.get("id")) is not int or resp["id"] != req_id:
            raise errors.ProtocolViolation(f"the response to {op} has id {resp.get('id')!r}, not {req_id}")
        return resp

    def close(self) -> None:
        self.closed = True


def connect_in_process(*, module=None, call_timeout: float = _g.CALL_TIMEOUT_S,
                       retry_not_ready: bool | float = False) -> Game:
    """A Game over _skyrim. skyrim.connect() calls this inside the game (Task 22)."""
    return Game(InProcessTransport(module), call_timeout=call_timeout, retry_not_ready=retry_not_ready)
```

Replace the package's `__init__.py` with:
`python/src/skyrim/__init__.py`:
```python
"""Call Papyrus in a running Skyrim from Python."""
from .errors import *  # noqa: F401,F403
from .game import Batch, Game, Transport  # noqa: F401
from .inprocess import InProcessTransport, connect_in_process  # noqa: F401
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass, on both Python versions**

Run: `cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q`
Expected: `253 passed` twice, each in about 5 s (Task 20's 151 plus this task's 102; the request-fixture replay adds one test per entry in `requests.json`). No test depends on wall-clock luck: every wait is bounded by a deadline, and the one call with a 1 ms timeout (`test_tiny_timeout_is_one_millisecond_not_zero`) checks only what was sent. To check for flakiness on a slow machine, run the two new files on one CPU a few times: `for i in 1 2 3; do taskset -c 0 uv run --python 3.14 pytest -q tests/test_game.py tests/test_inprocess.py; done` prints `102 passed` three times.

Design notes the tests pin: `Game` owns no I/O and never sees request ids (the transport chooses them); an oversize or unencodable request is `EncodeError` before `_skyrim` sees anything (`_framing.dumps` runs first); `InProcessTransport.request` blocks in `_skyrim.call`, which in the real module releases the GIL, so 16 threads can wait at once (`test_sixteen_threads_calling_at_once_get_their_own_answers`); a call whose wait runs out leaves nothing in flight (`_skyrim.call` cancels its ticket).

- [ ] **Step 6: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`. `game.py` and `inprocess.py` must not spell out an op name, an error code, a limit or a default timeout: they take them from `skyrim._protocol_generated`.

- [ ] **Step 7: Commit**

```bash
git add python/src python/tests
git commit -m "feat(python): Game API over a transport, the in-process transport over _skyrim, and a fake _skyrim for tests"
```
