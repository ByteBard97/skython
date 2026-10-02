"""Events (spec 4.6): subscriptions, their callbacks, and the one dispatch thread per Game that runs them.

A Game subscribes with events.subscribe; the plugin's EventHub queues each matching event per subscription (bounded,
drop-oldest, with a `dropped` count on the next delivered event) and the Game's EventStream hands the event objects
over: in-process from the Game's own _skython consumer, remotely from the bridge's event frames (skython.remote).
One daemon thread per Game, "skython-events", runs every callback of that Game in event order; a callback that raises
is logged (logger skython.events, with the traceback) and the thread goes on. A slow callback holds back only this
Game's later callbacks; its own queue overflows and the drops are reported, nothing else waits.

Locking: Game.on() holds EventDispatcher.lock (an RLock) across the subscribe request, stream.track() and the
registration, and the dispatch thread takes it before looking up each event's subscription, so no first event is
lost. The price: for the length of one subscribe round trip (in-process a few microseconds, through the bridge about
90 ms, two frame drains) every callback of that Game waits. subscribe() checks the dispatcher's stop flag under the
same lock before sending, so an on() that races Game.close() raises ConnectionLost instead of registering into a
closed dispatcher. Callbacks run without the lock. Subscription.close() marks the subscription closed under the lock
(no later callback of it starts), calls stream.untrack(), unsubscribes, and waits for a callback of it that is
running, unless it is called from the dispatch thread (that callback, or any other callback of this Game). Every
close() waits so, the second of two overlapping ones and one after Game.close() too. A callback that needs a lock the
closing thread holds therefore deadlocks it: never close a subscription while holding a lock its callback takes.

Bounds: the plugin's hub bounds each subscription in-process. Remotely the bridge drains the hub continuously, so the
remote stream keeps its own bounded queue per subscription (skython.remote.RemoteEventStream); stream.track(sub,
max_queue) tells it each bound, stream.untrack(sub) that the subscription ended."""
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from . import _protocol_generated as _g
from . import errors
from .values import from_wire

log = logging.getLogger("skython.events")

_POLL_S = 0.25  # how long one stream.next() waits
_JOIN_S = 2.0   # how long Game.close() waits for the dispatch thread


@dataclass(frozen=True)
class Event:
    """One delivered event. `data` holds the event's fields (form fields are Form objects or None). `dropped` and
    `suppressed` count this subscription's events lost to overflow, or not queued during a load, since its previous
    delivered event; seq gaps equal drops."""
    name: str
    sub: int
    seq: int
    epoch: int
    data: dict
    dropped: int = 0
    suppressed: int = 0

    @classmethod
    def from_wire(cls, raw: Any) -> "Event":
        try:
            if not isinstance(raw, dict) or not isinstance(raw.get("event"), str) or not isinstance(raw.get("data"), dict):
                raise ValueError("an event needs a string event and an object data")
            ints = (raw["sub"], raw["seq"], raw["epoch"], raw.get("dropped", 0), raw.get("suppressed", 0))
            if not all(type(i) is int and i >= 0 for i in ints):
                raise ValueError("sub, seq, epoch, dropped and suppressed must be non-negative integers")
            data = {k: from_wire(v) for k, v in raw["data"].items()}
        except (KeyError, ValueError) as e:
            raise errors.ProtocolViolation(f"malformed event from the server: {e}: {raw!r}") from None
        return cls(raw["event"], raw["sub"], raw["seq"], raw["epoch"], data, raw.get("dropped", 0), raw.get("suppressed", 0))


class EventStream(Protocol):
    """Where a Game's events come from. next() returns up to MAX_EVENTS_PER_NEXT event objects, [] on timeout and []
    at once once closed; `closed` is True once close() was called (by the Game or by its transport), and the
    dispatch thread then stops. consumer is the _skython consumer in-process, None remotely (the bridge owns it). track()
    gives a subscription's max_queue (called under the dispatcher's lock, before the subscription is registered);
    untrack() says it ended."""

    consumer: int | None
    closed: bool

    def next(self, timeout: float) -> list[dict]: ...

    def close(self) -> None: ...

    def track(self, sub: int, max_queue: int) -> None: ...

    def untrack(self, sub: int) -> None: ...


class InProcessEventStream:
    """An EventStream over one _skython consumer (open_consumer / next_events / close_consumer)."""

    def __init__(self, module):
        self._m = module
        self.consumer: int | None = module.open_consumer()
        self.closed = False
        self._lock = threading.Lock()

    def next(self, timeout: float) -> list[dict]:
        if self.closed:
            return []
        text = self._m.next_events(self.consumer, timeout)  # releases the GIL while it waits
        try:
            events = json.loads(text)
        except (TypeError, ValueError) as e:
            raise errors.ProtocolViolation(f"_skython.next_events returned text that is not JSON: {e}") from None
        if not isinstance(events, list):
            raise errors.ProtocolViolation(f"_skython.next_events returned {type(events).__name__}, not a list")
        return events

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
        self._m.close_consumer(self.consumer)  # drops its subscriptions and queued events, wakes a waiting next

    def track(self, sub: int, max_queue: int) -> None:
        pass  # the hub bounds each subscription in-process

    def untrack(self, sub: int) -> None:
        pass


class Subscription:
    """One subscription of a Game.

    close() is idempotent. After it returns no callback of this subscription starts, and none is running: close()
    blocks while this subscription's callback runs (every call, including a second overlapping one and one after
    Game.close()), except when called on the Game's dispatch thread, i.e. from inside a callback, where it returns
    at once. So a callback that waits for a lock the closing thread holds deadlocks: do not close a subscription
    from a thread holding a lock its callback needs."""

    def __init__(self, dispatcher: "EventDispatcher", sub: int, event: str, filter: dict | None, fn: Callable):
        self.sub = sub
        self.event = event
        self.filter = filter
        self.closed = False
        self._fn = fn
        self._d = dispatcher

    def close(self) -> None:
        self._d.close_subscription(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        return f"Subscription(sub={self.sub}, event={self.event!r}, closed={self.closed})"


class EventDispatcher:
    """The dispatch thread of one Game and its table of subscriptions."""

    def __init__(self, stream: EventStream, game):
        self.stream = stream
        self.lock = threading.RLock()
        self._game = game
        self._idle = threading.Condition(self.lock)  # notified whenever a callback returns
        self._subs: dict[int, Subscription] = {}
        self._running: Subscription | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- subscriptions ----------------------------------------------------------------------------------
    def subscribe(self, send: Callable[[], dict], event: str, filter: dict | None, fn: Callable,
                  max_queue: int) -> Subscription:
        """send() makes the events.subscribe request and returns its result. Request, stream.track() and registration
        happen under self.lock, so an event the hub queues in between waits for the registration instead of being
        dropped. The result's max_queue (what the hub granted) wins over the requested one."""
        with self.lock:
            if self._stop.is_set():  # Game.close() won the race: never register into a closed dispatcher
                raise errors.ConnectionLost("the Game is closed")
            result = send()
            sub_id = result.get("sub")
            if type(sub_id) is not int:
                if sub_id is not None:  # the hub may hold a subscription under it: free it rather than leak it
                    self._unsubscribe_quietly(sub_id)
                raise errors.ProtocolViolation(f"events.subscribe result has no integer sub: {result!r}")
            granted = result.get("max_queue", max_queue)
            try:
                self.stream.track(sub_id, granted if type(granted) is int else max_queue)
            except BaseException:
                try:
                    self.stream.untrack(sub_id)
                except Exception:  # noqa: BLE001 - the track() failure is the one to raise
                    log.exception("events: untracking sub %r after a failed track failed", sub_id)
                self._unsubscribe_quietly(sub_id)
                raise
            sub = Subscription(self, sub_id, event, filter, fn)
            self._subs[sub_id] = sub
        self.start()
        return sub

    def _unsubscribe_quietly(self, sub_id) -> None:
        """Send events.unsubscribe for sub_id; a failure is logged (or ignored when the Game or the hub's
        subscription is already gone), never raised."""
        try:
            self._game._request(_g.OP_EVENTS_UNSUBSCRIBE, {"sub": sub_id})
        except (errors.ConnectionLost, errors.NotFound):
            pass  # the Game or connection is closed (its consumer went with it), or the hub already dropped it
        except Exception as e:  # noqa: BLE001 - best effort
            log.warning("events: unsubscribing sub %r failed: %s", sub_id, e)

    def close_subscription(self, sub: Subscription) -> None:
        """See Subscription.close: every call waits for a running callback of `sub`, unless it is made on the
        dispatch thread (inside a callback), where waiting could only deadlock."""
        with self.lock:
            first = not sub.closed
            if first:
                sub.closed = True  # checked under this lock right before each callback: no later callback starts
                self._subs.pop(sub.sub, None)
                self.stream.untrack(sub.sub)  # remotely: its queued frames go, later ones are ignored
        if first:
            self._unsubscribe_quietly(sub.sub)
        with self.lock:
            if threading.current_thread() is not self._thread:  # on the dispatch thread: never wait for ourselves
                while self._running is sub:
                    self._idle.wait()

    # -- the thread -------------------------------------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._loop, name="skython-events", daemon=True)
                self._thread.start()

    def close(self) -> None:
        """Close every subscription (no unsubscribe requests: closing the stream drops them), close the stream, stop
        the thread and join it, never from the dispatch thread itself. The join waits at most _JOIN_S (2 s): a
        callback still running after that keeps running and may finish after close() returns (spec 4.6 allows it);
        no further callback starts."""
        with self.lock:
            for sub in self._subs.values():
                sub.closed = True
            self._subs.clear()
            self._stop.set()
            thread = self._thread
        try:
            self.stream.close()  # wakes a waiting next()
        except Exception:  # noqa: BLE001 - a failed close must not stop the Game closing
            log.exception("events: closing the event stream failed")
        if thread is not None and thread is not threading.current_thread():
            thread.join(_JOIN_S)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if getattr(self.stream, "closed", False):  # closed under us (transport.close()): stop polling it
                self._stop.set()
                return
            t0 = time.monotonic()
            try:
                batch = self.stream.next(_POLL_S)
            except Exception:  # noqa: BLE001 - the thread never dies on a bad read; it logs and tries again
                if self._stop.is_set():
                    return
                log.exception("events: reading the event stream failed; retrying")
                self._stop.wait(_POLL_S)
                continue
            if not batch and time.monotonic() - t0 < _POLL_S / 2:
                self._stop.wait(_POLL_S)  # a closed or unknown consumer answers at once: never spin on it
            for raw in batch:
                if self._stop.is_set():
                    return
                try:
                    self._deliver(raw)
                except Exception:  # noqa: BLE001 - the thread never dies silently
                    log.exception("events: delivering an event failed: %r", raw)

    def _deliver(self, raw: dict) -> None:
        try:
            event = Event.from_wire(raw)
        except errors.ProtocolViolation as e:
            log.warning("events: %s", e)
            return
        with self.lock:  # the lock Game.on() holds across subscribe + registration
            sub = self._subs.get(event.sub)
            if sub is None or sub.closed:
                return  # a subscription already closed: its leftover events are dropped silently
            self._running = sub
        try:
            sub._fn(event)
        except BaseException:  # noqa: BLE001 - nothing a callback raises may stop the thread
            log.exception("events: the callback of %s (sub %d, seq %d) raised", event.name, event.sub, event.seq)
        finally:
            with self.lock:
                self._running = None
                self._idle.notify_all()
