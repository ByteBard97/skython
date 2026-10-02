"""A pure-Python stand-in for the built-in `_skython` module (plan index, Contracts), for tests.

It speaks the same text as the real module: a request is the JSON text {"type": "request", "id", "op", "args"}
and a response is the JSON text {"type": "response", "id", "ok", "result" | "error"}. It behaves like core's
Dispatcher (Task 18) over FakeBackend:
- an op whose table entry has needs_game answers not_ready at submit while the fake is "not ready"
  (_fake_set_not_ready), or while it is paused and the request has require_unpaused;
- more than max_inflight_total requests in flight answers busy;
- every request has a deadline (args.timeout_ms, else default_timeout_ms) after which it completes with
  timeout (a batch's detail carries "completed" and "results" from its last progress report);
- timeout_ms and require_unpaused are read only when the op's table entry lists them as arguments;
- the protocol 1.1 ops' arguments are checked like core's check_typed_args (event names, queue sizes, u64, save names; a filter only has to be an object or null);
- _fake_bump_loads() (a world reset: a save load or a quit to the main menu) fails every in-flight needs_game
  request with not_ready, reason the not-ready reason of that moment (else "loading"), detail.dispatched = true;
- every timeout and not_ready detail carries paused and menus;
- cancel() erases a ticket, a later wait on it returns None, and its late result is dropped and counted in
  late_results;
- call() that outlives its wait cancels the ticket (or returns the result that has just arrived) and answers
  timeout with detail.late_cancel = true, plus paused and menus.
Handlers run on their own daemon threads, so blocking handlers behave like slow game calls.

Use the `fake` fixture in conftest.py: it installs an instance as sys.modules["_skython"] and closes it
afterwards. Tasks 23 (bridge) and 24 (skython._host) reuse it.

Events (Task 36a): `hub` is a FakeHub, a Python copy of core's EventHub (Task 34). events.subscribe and
events.unsubscribe are answered at submit time through the hub, with the consumer passed beside the request text
(0 -> bad_request "no event consumer", as the Dispatcher; an unknown or closed consumer -> the hub's own texts);
open_consumer, close_consumer, next_events and _fake_push_event are the Task 35 module API; consumers() and
subscriptions() are test-only counters. game.save and game.load follow FakeBackend (Task 35): save names are kept in
`saves`; a load bumps the loads counter (so the epoch); the error texts are responses.json 64-65.
"""
import collections
import itertools
import json
import pathlib
import re
import string
import tempfile
import threading
import time

from skython import _protocol_generated as _g
from skython.refs import parse_form_key
from skython.values import Form

BACKEND = "fake"
_U64_MAX = 2**64 - 1


class FakeRemoteError(Exception):
    """Raise from a handler to answer with this error code."""

    def __init__(self, code: str, message: str = "", detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail or {}


class Ctx:
    """What a handler gets besides op and args."""

    def __init__(self, fake: "FakeSkython", entry: "_Entry"):
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


_ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)  # the hub folds ASCII case only
_EVENT_OPS = (_g.OP_EVENTS_SUBSCRIBE, _g.OP_EVENTS_UNSUBSCRIBE)
_INT_MIN, _INT_MAX = -2**31, 2**31 - 1  # Papyrus Int
_FRAME_OVERHEAD_BYTES = 96  # core's kEventFrameOverheadBytes
_NOT_READY_TEXT = {"starting": "the game is still starting", "loading": "a game is loading"}  # core's not_ready_message
_NO_HUB = "this build has no event hub"


def _event_bytes(data) -> int:
    """core's byte-budget measure of one queued event: dump_json(data).size() + kEventFrameOverheadBytes."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return len(text.encode("utf-8", "replace")) + _FRAME_OVERHEAD_BYTES


def _nesting_within(text: str, max_depth: int) -> bool:
    """core's nesting_within: brackets outside strings never nest deeper than max_depth (checked before parsing)."""
    depth, in_string, escaped = 0, False, False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif ch in "]}" and depth > 0:
            depth -= 1
    return True


def _clip_text(s: str, limit: int) -> str:
    """core's clip_text: at most `limit` UTF-8 bytes plus "...", never cutting inside a UTF-8 sequence."""
    b = s.encode("utf-8", "replace")
    if len(b) <= limit:
        return s
    cut = limit
    if b[cut] & 0xC0 == 0x80:
        for back in range(1, min(3, cut) + 1):
            lead = b[cut - back]
            if lead & 0xC0 == 0x80:
                continue
            seq = 2 if lead & 0xE0 == 0xC0 else 3 if lead & 0xF0 == 0xE0 else 4 if lead & 0xF8 == 0xF0 else 0
            if seq > back:
                cut -= back
            break
    return b[:cut].decode("utf-8", "replace") + "..."


def _dump(v) -> str:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _describe(v) -> str:
    """core's describe(): a clipped string, "an array of <n> items", "an object", or the JSON value."""
    if isinstance(v, str):
        return _dump(_clip_text(v, 64))
    if isinstance(v, list):
        return f"an array of {len(v)} items"
    if isinstance(v, dict):
        return "an object"
    return _dump(v)


def _is_uint(v, limit: int) -> bool:
    return type(v) is int and 0 <= v <= limit


def _no_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _bad(message: str) -> FakeRemoteError:
    return FakeRemoteError(_g.CODE_BAD_REQUEST, message)


def _join(names, last_sep: str) -> str:
    """core's join(): "a", "a or b", "a, b or c"."""
    names = list(names)
    return "".join(n if i == 0 else (last_sep if i + 1 == len(names) else ", ") + n for i, n in enumerate(names))


def _form_ref_problem(v: dict) -> str | None:
    """core's form_ref_from_json checks, in its order, with its (bare) texts; None if v is a valid form reference."""
    kind = v.get("kind")
    if kind is not None and not isinstance(kind, str):
        return "field 'kind' must be a string"
    if kind != "form":
        return "expected a form reference"
    fid, key = v.get("form_id"), v.get("form_key")
    if fid is not None and not _is_uint(fid, 0xFFFFFFFF):
        return "form_id must be an unsigned 32-bit integer"
    if key is not None:
        try:
            parse_form_key(key)
        except (TypeError, ValueError):
            return "form_key must look like Plugin.esp|0x0012AB"
    if fid is None and key is None:
        return "a form reference needs form_id or form_key"
    if fid is not None and key is not None:
        try:
            Form(form_id=fid, form_key=key)
        except ValueError:
            return "form_id and form_key do not name the same form"
    if v.get("type") is not None and not isinstance(v["type"], str):
        return "field 'type' must be a string"
    if v.get("epoch") is not None and not _is_uint(v["epoch"], 0xFFFFFFFF):
        return "epoch must be an unsigned 32-bit integer"
    f = v.get("fingerprint")
    if f is not None and not (isinstance(f, dict) and isinstance(f.get("base"), str) and isinstance(f.get("type"), str)):
        return "fingerprint needs string base and type"
    return None


def _normalise(event: str, field: str, ftype: str, v):
    """core's normalise(): the key a filter value is compared by, or bad_request with core's text. ftype is the
    protocol type name from EVENTS (form, int, string; float and bool fields are never filter fields)."""
    where = f"filter value for {event}.{field}"
    if ftype == "form":
        if not isinstance(v, dict):
            raise _bad(f"{where} must be a form reference, got {_describe(v)}")
        problem = _form_ref_problem(v)  # core's form_ref_from_json: its texts carry no "filter value" prefix
        if problem is not None:
            raise _bad(problem)
        if v.get("form_id") is None:
            raise _bad(f"{where} has no form_id: resolve the form first")
        return v["form_id"]
    if ftype == "int":
        if type(v) is not int or not _INT_MIN <= v <= _INT_MAX:
            raise _bad(f"{where} must be an integer (a Papyrus Int), got {_describe(v)}")
        return v
    if ftype == "string":
        if not isinstance(v, str):
            raise _bad(f"{where} must be a string, got {_describe(v)}")
        return v.translate(_ASCII_LOWER)
    raise _bad(f"{where}: this field cannot be filtered")


def _event_key(ftype: str, value):
    """The key an event's field value is compared by (core's filter_matches); None never matches."""
    if value is None:
        return None
    if ftype == "form":
        fid = value.get("form_id") if isinstance(value, dict) else None
        return fid if _is_uint(fid, 0xFFFFFFFF) else None
    if ftype == "string":
        return value.translate(_ASCII_LOWER) if isinstance(value, str) else None
    return value if type(value) is int and _INT_MIN <= value <= _INT_MAX else None


def _parse_filter(event: str, spec: dict, flt) -> dict:
    """core's parse_event_filter: field -> (type, set of keys); {} matches everything."""
    types = {name: ftype for name, ftype, _nullable in spec["fields"]}
    out = {}
    if flt is not None:
        if not isinstance(flt, dict):
            raise _bad("filter must be an object mapping filter fields to lists of values")
        for name, values in flt.items():
            if name not in spec["filter_fields"]:
                tail = (" (it cannot be filtered)" if not spec["filter_fields"]
                        else f" (filter fields: {_join(spec['filter_fields'], ', ')})")
                raise _bad(f"event {event} has no filter field '{_clip_text(name, 64)}'{tail}")
            if not isinstance(values, list) or not values:
                raise _bad(f"filter field '{name}' needs a non-empty array of values")
            if len(values) > _g.MAX_FILTER_VALUES:
                raise _bad(f"filter field '{name}' has {len(values)} values; at most {_g.MAX_FILTER_VALUES}")
            out[name] = (types[name], {_normalise(event, name, types[name], v) for v in values})
    required = spec["filter_required_any_of"]
    if required and not any(f in out for f in required):
        raise _bad(f"{event} needs a filter on {_join(required, ' or ')}")
    return out


class _FakeSub:
    def __init__(self, sub: int, consumer: int, event: str, flt: dict, max_queue: int):
        self.sub, self.consumer, self.event, self.filter, self.max_queue = sub, consumer, event, flt, max_queue
        self.queue: collections.deque = collections.deque()  # (push number, event object, budget bytes)
        self.seq = 0
        self.dropped = 0     # since the previous delivered event
        self.suppressed = 0  # since the previous delivered event

    def matches(self, data: dict) -> bool:
        return all(_event_key(ftype, data.get(f)) in keys for f, (ftype, keys) in self.filter.items())


class FakeHub:
    """core's EventHub (Task 34) in Python: consumers, a bounded drop-oldest queue per subscription, seq from 1 per
    subscription (gaps equal drops), the epoch stamp, equality-set filters, the loading rule (an event whose
    table entry has deliver_while_loading false is not queued while loading() is true; it counts as suppressed), and
    R61's bounds: max_subscriptions_per_consumer and a byte budget per consumer."""

    def __init__(self, epoch, loading, max_queued_bytes_per_consumer: int = _g.MAX_QUEUED_EVENT_BYTES_PER_CONSUMER):
        self._epoch, self._loading = epoch, loading  # callables
        self.max_queued_bytes_per_consumer = max_queued_bytes_per_consumer
        self._cond = threading.Condition()
        self._consumers: dict[int, int] = {}  # open consumer -> the budget bytes it holds queued
        self._subs: dict[int, _FakeSub] = {}
        self._consumer_ids = itertools.count(1)
        self._sub_ids = itertools.count(1)
        self._pushes = itertools.count(1)
        self.dropped_total = 0
        self.suppressed_total = 0

    def open_consumer(self) -> int:
        with self._cond:
            c = next(self._consumer_ids)
            self._consumers[c] = 0
            return c

    def close_consumer(self, consumer: int) -> None:
        with self._cond:
            self._consumers.pop(consumer, None)
            for sid in [s.sub for s in self._subs.values() if s.consumer == consumer]:
                del self._subs[sid]
            self._cond.notify_all()

    def close_all(self) -> None:
        with self._cond:
            self._consumers.clear()
            self._subs.clear()
            self._cond.notify_all()

    def subscribe(self, consumer: int, args: dict) -> dict:
        event = args.get("event")
        spec = _g.EVENTS.get(event) if isinstance(event, str) else None
        if spec is None:  # _op_problem (Task 33) refuses unknown events first; this is core's own check
            raise _bad(f"unknown event '{_clip_text(str(event), 64)}'")
        max_queue = args.get("max_queue", _g.DEFAULT_EVENT_QUEUE)
        if type(max_queue) is not int or not 1 <= max_queue <= _g.MAX_EVENT_QUEUE:
            raise _bad(f"max_queue must be an integer from 1 to {_g.MAX_EVENT_QUEUE}")
        flt = _parse_filter(event, spec, args.get("filter"))
        with self._cond:
            if consumer not in self._consumers:
                raise _bad(f"no event consumer {consumer} (never opened, or closed)")
            if sum(1 for s in self._subs.values() if s.consumer == consumer) >= _g.MAX_SUBSCRIPTIONS_PER_CONSUMER:
                raise FakeRemoteError(_g.CODE_BUSY, f"consumer {consumer} has {_g.MAX_SUBSCRIPTIONS_PER_CONSUMER} "
                                                    "subscriptions (max_subscriptions_per_consumer)")
            if len(self._subs) >= _g.MAX_SUBSCRIPTIONS:
                raise FakeRemoteError(_g.CODE_BUSY, f"too many event subscriptions ({_g.MAX_SUBSCRIPTIONS})")
            sid = next(self._sub_ids)
            self._subs[sid] = _FakeSub(sid, consumer, event, flt, max_queue)
        return {"sub": sid, "event": event, "max_queue": max_queue}

    def unsubscribe(self, consumer: int, args: dict) -> None:
        sid = args.get("sub")
        with self._cond:
            s = self._subs.get(sid) if type(sid) is int else None
            if consumer not in self._consumers or s is None or s.consumer != consumer:
                raise FakeRemoteError(_g.CODE_NOT_FOUND, f"no subscription {sid} for this consumer")
            self._consumers[consumer] -= sum(b for _n, _ev, b in s.queue)
            del self._subs[sid]
            self._cond.notify_all()

    def _drop_oldest(self, s: _FakeSub) -> None:  # with _cond held
        _n, _ev, b = s.queue.popleft()
        self._consumers[s.consumer] -= b
        s.dropped += 1
        self.dropped_total += 1

    def push(self, event: str, data: dict) -> None:
        spec = _g.EVENTS.get(event)
        held = spec is not None and not spec["deliver_while_loading"] and self._loading()  # read once, outside the lock
        epoch = self._epoch()
        size = _event_bytes(data)  # measured once, outside the lock, as core does
        with self._cond:
            n = next(self._pushes)
            for s in self._subs.values():
                if s.event != event or not s.matches(data):
                    continue
                if held:
                    s.suppressed += 1
                    self.suppressed_total += 1
                    continue
                s.seq += 1
                if len(s.queue) >= s.max_queue:
                    self._drop_oldest(s)
                s.queue.append((n, {"type": "event", "event": event, "sub": s.sub, "seq": s.seq, "epoch": epoch,
                                    "data": data}, size))
                self._consumers[s.consumer] += size
                while self._consumers[s.consumer] > self.max_queued_bytes_per_consumer and s.queue:
                    self._drop_oldest(s)  # R61: the pushed subscription's oldest first, the new event last
            self._cond.notify_all()

    def next(self, consumer: int, timeout: float, max_events: int = _g.MAX_EVENTS_PER_NEXT) -> list[dict]:
        """Events in push order across the consumer's subscriptions; [] on timeout, [] at once for an unknown consumer."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                if consumer not in self._consumers or max_events <= 0:
                    return []
                mine = [s for s in self._subs.values() if s.consumer == consumer and s.queue]
                if mine:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)
            out = []
            while len(out) < max_events:
                s = min((s for s in mine if s.queue), key=lambda s: s.queue[0][0], default=None)
                if s is None:
                    break
                _n, ev, b = s.queue.popleft()
                self._consumers[consumer] -= b
                ev = dict(ev)
                if s.dropped:
                    ev["dropped"], s.dropped = s.dropped, 0
                if s.suppressed:
                    ev["suppressed"], s.suppressed = s.suppressed, 0
                out.append(ev)
            return out

    def consumers(self) -> int:
        with self._cond:
            return len(self._consumers)

    def subscriptions(self, consumer: int | None = None) -> int:
        with self._cond:
            return sum(1 for s in self._subs.values() if consumer is None or s.consumer == consumer)


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
    if op == _g.OP_GAME_SAVE:  # FakeBackend (Task 35): never overwrites; texts are responses.json 64
        with fake._cond:
            if args["name"] in fake.saves:
                raise FakeRemoteError(_g.CODE_BAD_REQUEST, f"a save named {args['name']} already exists",
                                      {"reason": "save_exists"})
            fake.saves.add(args["name"])
        return {"name": args["name"]}
    if op == _g.OP_GAME_LOAD:  # needs_game false, so the readiness check is the handler's own (index Contracts)
        reason = fake._not_ready
        if reason in ("starting", "loading"):  # FakeBackend's texts and detail (core's not_ready_message)
            raise FakeRemoteError(_g.CODE_NOT_READY, _NOT_READY_TEXT[reason],
                                  {"reason": reason, "paused": fake._paused, "menus": list(fake.menus)})
        if args["save"] not in fake.saves:
            raise FakeRemoteError(_g.CODE_NOT_FOUND, f"no save named {args['save']}")  # responses.json 65
        fake._fake_bump_loads()
        return {"save": args["save"], "started": True}
    raise FakeRemoteError(_g.CODE_UNSUPPORTED, f"the fake backend has no {op} {args.get('script')}.{args.get('function')}")


class FakeSkython:
    """Module-like: put an instance in sys.modules["_skython"] and `import _skython` returns it."""

    BACKEND = BACKEND
    PROTOCOL = _g.PROTOCOL_VERSION

    def __init__(self, handler=None, *, root: str | pathlib.Path | None = None, config: dict | None = None,
                 default_timeout_ms: int = _g.CONFIG_KEYS["default_timeout_ms"]["default"],
                 max_inflight_total: int = _g.CONFIG_KEYS["max_inflight_total"]["default"]):
        self.handler = handler or fake_handler
        self.root = pathlib.Path(root) if root is not None else pathlib.Path(tempfile.gettempdir()) / "skython-fake"
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
        self.saves: set[str] = set()
        self.hub = FakeHub(lambda: self.epoch, lambda: self._not_ready == "loading")

    @property
    def epoch(self) -> int:
        """Like FakeBackend::BaseState: the number of loads so far (0 at the start); tests may set it."""
        return self._epoch_base + self._loads

    @epoch.setter
    def epoch(self, value: int) -> None:
        self._epoch_base = value - self._loads

    # -- the _skython API ------------------------------------------------------------------------
    def submit(self, request: str, consumer: int = 0) -> int:
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
            if op in _EVENT_OPS:  # answered by the hub at once: no backend call, no deadline (Task 35's Dispatcher)
                self._finish_now(ticket, req_id, self._hub_answer(op, args, consumer, req_id))
                return ticket
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

    def call(self, request: str, timeout: float, consumer: int = 0) -> str:
        ticket = self.submit(request, consumer)
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

    # -- events (the Task 35 module API) ---------------------------------------------------------
    # A test may set self.hub = None: the fake then acts like a build with no event hub (ModuleHost.events null).
    def open_consumer(self) -> int:
        if self.hub is None:
            raise RuntimeError("_skython: " + _NO_HUB)
        return self.hub.open_consumer()

    def close_consumer(self, consumer: int) -> None:
        if self.hub is not None:
            self.hub.close_consumer(consumer)

    def next_events(self, consumer: int, timeout: float) -> str:
        if self.hub is None:
            return "[]"
        return json.dumps(self.hub.next(consumer, timeout), separators=(",", ":"), ensure_ascii=False)

    def _fake_push_event(self, event: str, data_json: str) -> None:
        """As the real module, in its order and with its texts: an unknown event, data nested too deeply (checked on
        the text), invalid JSON or a non-object is ValueError."""
        if self.hub is None:
            raise RuntimeError("_skython: " + _NO_HUB)
        if event not in _g.EVENTS:
            raise ValueError(f"unknown event '{_clip_text(event, 64)}'")
        if not _nesting_within(data_json, _g.MAX_JSON_DEPTH):
            raise ValueError("data_json is nested too deeply")
        try:
            data = json.loads(data_json, parse_constant=_no_constant)  # core's parser has no NaN or Infinity
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ValueError("data_json must be a JSON object")
        self.hub.push(event, data)

    def consumers(self) -> int:
        """Test-only (the real module has no counters): open consumers."""
        return 0 if self.hub is None else self.hub.consumers()

    def subscriptions(self, consumer: int | None = None) -> int:
        """Test-only: live subscriptions, of one consumer or of all."""
        return 0 if self.hub is None else self.hub.subscriptions(consumer)

    def _hub_answer(self, op: str, args: dict, consumer: int, req_id: int) -> str:
        if self.hub is None:  # the Dispatcher with DispatcherOptions::events null (Task 35)
            return _response(req_id, False, _g.CODE_UNSUPPORTED, message=_NO_HUB)
        if consumer == 0:  # the Dispatcher's own check, before the hub (Task 35)
            return _response(req_id, False, _g.CODE_BAD_REQUEST, message="no event consumer")
        try:
            if op == _g.OP_EVENTS_SUBSCRIBE:
                return _response(req_id, True, self.hub.subscribe(consumer, args))
            self.hub.unsubscribe(consumer, args)
            return _response(req_id, True, None)
        except FakeRemoteError as e:
            return _response(req_id, False, e.code, e.detail, e.message)

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
        if self.hub is not None:
            self.hub.close_all()

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
        if "args" not in obj:  # core accepts a request without args as an empty object (requests.json valid_without_args)
            obj["args"] = {}
        if not isinstance(obj["args"], dict):
            return "args must be an object"
        return FakeSkython._op_problem(obj["op"], obj["args"])

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
        for name, typ, required in _g.OPS[op]["args"]:  # the protocol 1.1 argument types, as core's check_typed_args
            if typ not in ("event_name", "event_filter", "queue_size", "u64", "save_name"):
                continue
            if name not in args:
                if required:
                    return f"missing field {name!r}"
                continue
            v = args[name]
            if typ == "event_name" and not isinstance(v, str):  # core's check_typed_args texts
                return f"'{name}' must be a string naming an event"
            if typ == "event_name" and v not in _g.EVENTS:
                return f"unknown event '{_clip_text(v, 64)}'"
            if typ == "event_filter" and v is not None and not isinstance(v, dict):
                return f"{name!r} must be an object mapping filter fields to lists of values"
            if typ == "queue_size" and (type(v) is not int or not 1 <= v <= _g.MAX_EVENT_QUEUE):
                return f"{name!r} must be an integer from 1 to {_g.MAX_EVENT_QUEUE}"
            if typ == "u64" and (type(v) is not int or not 0 <= v <= _U64_MAX):
                return f"{name!r} must be a non-negative integer"
            if typ == "save_name" and (not isinstance(v, str) or re.fullmatch(_g.SAVE_NAME_PATTERN, v) is None):
                return f"{name!r} must be 1 to 64 letters, digits or underscores"
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
                problem = FakeSkython._op_problem(step["op"], step_args)
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
