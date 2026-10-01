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
