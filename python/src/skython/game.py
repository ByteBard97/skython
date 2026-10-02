"""The Game API, the same in both modes. A Game talks to the game through a Transport:
skython.inprocess.InProcessTransport inside the game (over the built-in _skython module), or
skython.remote.RemoteTransport from another process (over the dev bridge)."""
import math
import threading
import time
import types
from collections.abc import Mapping
from typing import Any, Callable, Protocol

from . import _protocol_generated as _g
from . import errors
from .values import Alias, Form, from_wire, to_wire
from .events import EventDispatcher, EventStream, Subscription

_GRACE = _g.GRACE_S  # seconds the Game waits beyond a server-side timeout
_STEP_TIMEOUT = _g.CONFIG_KEYS["default_timeout_ms"]["default"] / 1000  # the plugin's default, to size a batch's wait
_EMPTY_SETTINGS: Mapping[str, Any] = types.MappingProxyType({})


class Transport(Protocol):
    """How a Game reaches the game. request() sends {"type": "request", "id", "op", "args"} with an id the
    transport chooses and returns the decoded response object {"type": "response", "id", "ok", ...}. It raises
    EncodeError (nothing sent), ConnectionLost, CallTimeout or ProtocolViolation; it never interprets "ok"."""

    welcome: dict
    closed: bool

    def request(self, op: str, args: dict, wait_s: float) -> dict: ...

    def close(self) -> None: ...

    def events(self) -> EventStream: ...  # one stream per transport, made on first use


def _positive_seconds(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {value!r}")
    return float(value)


def _timeout_ms(timeout: float) -> int:
    """Seconds -> whole milliseconds, rounded up so that 0.0004 s is 1 ms, never the 0 that means nothing."""
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
        #: The epoch when this Game was made, fixed for its life. It does not follow save loads; read
        #: state()["epoch"] for the current one.
        self.epoch: int = self.welcome.get("epoch", 0)
        self._events_lock = threading.Lock()
        self._dispatcher: EventDispatcher | None = None
        self._closing = False
        self._settings: Mapping[str, Any] = _EMPTY_SETTINGS

    @property
    def closed(self) -> bool:
        return self.transport.closed

    @property
    def settings(self) -> Mapping[str, Any]:
        """This player script's settings file (scripts/<name>.toml or scripts/<name>/settings.toml), read-only:
        tables are MappingProxyType, arrays tuples. Empty for a Game that is not a player script's."""
        return self._settings

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

        A Python int is sent as a Papyrus Int (checked to 32 bits); a Float parameter accepts it, so 3 works
        where the parameter is a Float. Use float(n) only for an n beyond the 32-bit range (see to_wire)."""
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

    # -- events ----------------------------------------------------------------------------------
    def _event_dispatcher(self) -> EventDispatcher:
        with self._events_lock:
            if self._closing or self.closed:
                raise errors.ConnectionLost("the Game is closed")
            if self._dispatcher is None:
                caps = self.welcome.get("capabilities")
                if isinstance(caps, list) and _g.OP_EVENTS_SUBSCRIBE not in caps:  # a protocol 1.0 server
                    raise errors.Unsupported(_g.CODE_UNSUPPORTED, f"the server does not offer {_g.OP_EVENTS_SUBSCRIBE} "
                                             f"(protocol {self.welcome.get('protocol')})")
                events = getattr(self.transport, "events", None)
                if events is None:
                    raise errors.Unsupported(_g.CODE_UNSUPPORTED, f"{type(self.transport).__name__} has no event stream")
                self._dispatcher = EventDispatcher(events(), self)  # the stream first: it owns the consumer
            return self._dispatcher

    def on(self, event: str, filter: dict | None, fn: Callable[[Any], None], *,
           max_queue: int = _g.DEFAULT_EVENT_QUEUE) -> Subscription:
        """Call fn(Event) on the Game's event thread for every `event` that matches `filter`.

        `filter` maps a field of the event's filter_fields to the values to accept: a list, tuple, set or
        frozenset, or one value. Every listed field must match one of its values. Forms must carry form_id
        (resolve them first); strings match ignoring ASCII case. At most `max_queue` events wait per
        subscription; beyond that the oldest are dropped and the next delivered Event's `dropped` says how many.

        on() holds the Game's dispatcher lock across the subscribe round trip (about 90 ms through the bridge),
        so this Game's callbacks wait that long. That is what guarantees the first event is never lost.

        Subscription.close() blocks while that subscription's callback runs (unless called from inside a
        callback), so a callback that waits on a lock held by the closing thread deadlocks. Do not close from a
        thread holding a lock your callback needs.

        Raises Unsupported when the server or module has no events, ConnectionLost once the Game is closed."""
        if not callable(fn):
            raise TypeError(f"fn must be callable, not {type(fn).__name__}")
        args: dict[str, Any] = {"event": event, "max_queue": max_queue}
        if filter is not None:
            if not isinstance(filter, dict):
                raise TypeError(f"filter must be a dict or None, not {type(filter).__name__}")
            args["filter"] = {field: [to_wire(v) for v in (list(vals) if isinstance(vals, (list, tuple, set, frozenset)) else [vals])]
                              for field, vals in filter.items()}
        d = self._event_dispatcher()
        return d.subscribe(lambda: _expect_dict(self._request(_g.OP_EVENTS_SUBSCRIBE, args), "events.subscribe result"),
                           event, filter, fn, max_queue)

    def on_death(self, actor: Form | None, fn) -> Subscription:
        return self.on("death", None if actor is None else {"victim": [actor]}, fn)

    def on_dying(self, actor: Form | None, fn) -> Subscription:
        return self.on("dying", None if actor is None else {"victim": [actor]}, fn)

    def on_mod_event(self, name: str, fn) -> Subscription:
        return self.on("mod_event", {"name": [name]}, fn)

    def on_channel(self, channel: str, fn) -> Subscription:
        """Skython.Send(channel, payload) from Papyrus arrives here as an Event with data channel, payload, truncated."""
        return self.on("channel", {"channel": [channel]}, fn)

    def on_quest_stage(self, quest: Form, fn, *, stage: int | None = None) -> Subscription:
        flt: dict[str, list] = {"quest": [quest]}
        if stage is not None:
            flt["stage"] = [stage]
        return self.on("quest_stage", flt, fn)

    def on_game_loaded(self, fn) -> Subscription:
        return self.on("game_loaded", None, fn)

    def wait_for(self, predicate: Callable[[], Any], timeout: float, *, interval: float = 0.25) -> Any:
        """Call predicate() every `interval` seconds until it returns something truthy, and return that.
        Raises WaitTimeout when `timeout` seconds pass first."""
        _positive_seconds(timeout, "timeout")
        _positive_seconds(interval, "interval")
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise errors.WaitTimeout(f"the condition was not met within {timeout:g} s")
            time.sleep(min(interval, remaining))

    # -- save and load ----------------------------------------------------------------------------------
    def save(self, name: str, *, timeout: float = 60.0) -> str:
        """Save the game as `name` (letters, digits, _; at most 64). Never overwrites: an existing save is BadRequest
        with detail reason "save_exists". Returns once the engine has started the save: the file can appear a
        moment later, and a load of it meanwhile is NotReady with detail reason "saving" (retry briefly)."""
        result = _expect_dict(self._request(_g.OP_GAME_SAVE, {"name": name, "timeout_ms": _timeout_ms(timeout)}, timeout),
                              "game.save result")
        return result.get("name", name)

    def load(self, name: str, *, timeout: float = 120.0, wait: bool = True) -> dict:
        """Load the save `name`. Without `wait`, returns {"save", "started"} once the load has started. With `wait`,
        returns the first interactive state() whose epoch is above the one before the call; NotReady with detail
        reason "load timed out" (plus the last state) if that takes longer than `timeout`. Every call in flight when
        the load starts ends NotReady; subscriptions stay."""
        deadline = time.monotonic() + _positive_seconds(timeout, "timeout")
        before = self.state().get("epoch", 0) if wait else 0  # without wait, game.load is the only request sent
        started = _expect_dict(self._request(_g.OP_GAME_LOAD, {"save": name, "timeout_ms": _timeout_ms(timeout)}, timeout),
                               "game.load result")
        if not wait:
            return started
        while True:
            st = self.state()
            if st.get("interactive") and st.get("epoch", 0) > before:
                return st
            if time.monotonic() >= deadline:
                raise errors.NotReady(_g.CODE_NOT_READY, f"save {name!r} was not loaded and interactive after {timeout:g} s",
                                      {**st, "reason": "load timed out"})
            time.sleep(0.25)

    def close(self) -> None:
        """Close every subscription, close the event stream and the transport, and stop the event thread. The thread
        is joined for at most 2 s: a callback still running after that may finish after close() returns,
        but no further callback starts. Safe to call from inside a callback (it then does not join)."""
        with self._events_lock:
            self._closing = True
            d, self._dispatcher = self._dispatcher, None
        if d is not None:
            d.close()
        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Batch:
    """Several papyrus.* steps in one request, run in order. Not a transaction.

    Each step method takes an optional `timeout` (seconds). The plugin applies one timeout to the whole
    batch, so run() always sends one: run(timeout=...) if given, else the sum of the step timeouts (5 s for
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
