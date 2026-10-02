# Task 36: Python event dispatch: `Event`, `Subscription`, `Game.on` and helpers, `wait_for`, `save`/`load`

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Review Focus**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.6 and 4.7). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 35 (the real Linux `_skython` with `open_consumer`, `close_consumer`, `next_events`, `submit`/`call` with a consumer, `_fake_push_event`, fake `game.save`/`game.load`), Task 36a (the same API in the Python fake `fake_skython.py`, with `FakeHub` and the test-only counters) and Task 33 (the generated names below). Linux only: no game, no Gate 0, no ESP.

**What this task builds.** The Python side of events for a `Game` in either mode. `skython/events.py` holds the `Event` value, the `EventStream` protocol and its in-process implementation over one `_skython` consumer, `Subscription`, and `EventDispatcher`: one daemon thread per `Game`, named `skython-events`, that pulls event objects from the `Game`'s stream and runs each subscription's callback in event order. `Game` gains `on(...)` and six helpers, `wait_for`, `save` and `load`, and `Game` callers for the four new ops replace Task 33's `PENDING_OPS` in `test_game.py`. `tests/test_events.py` runs each behaviour test against Task 36a's fake **and** against the real Linux `_skython` when `SKYTHON_REAL_MODULE_DIR` names its build folder. The `EventStream` protocol carries `track(sub, max_queue)` / `untrack(sub)`: the dispatcher tells the stream each subscription's bound and when it ends; the in-process stream ignores both (the hub bounds each subscription), Task 37's remote stream uses them for its client-side bounded queues. Task 37 adds the remote stream and reuses `EventDispatcher` unchanged.

**The two locking rules that carry Review Focus 4 and the "no lost first event" rule** (index Contracts, `events.py`):
1. `Game.on()` holds the dispatcher's `threading.RLock` across the `events.subscribe` request, `stream.track(sub, max_queue)` **and** the `sub -> Subscription` registration. The dispatch thread takes the same lock before it looks up each event's `sub`. An event that the hub queued between its `Subscribe` and our registration therefore waits for the lock instead of being dropped as "unknown sub". No deadlock: the subscribe response arrives synchronously (in-process) or on the reader thread (remote), never on the dispatch thread; and it is an `RLock`, so `on()` from inside a callback works. The price, documented in `Game.on`'s docstring: for the length of one subscribe round trip (about 90 ms remotely, two frame drains) the Game's callbacks wait. `subscribe()` checks the dispatcher's stop flag under the lock first, so an `on()` that races `Game.close()` raises `ConnectionLost` instead of registering into a closed dispatcher (Review A#10).
2. A callback runs **without** the lock. `Subscription.close()` sets `closed` under the lock (so no later callback of it starts: the dispatch thread checks `closed` under the same lock right before it calls) and calls `stream.untrack(sub)`, sends `events.unsubscribe`, then, unless it runs on the dispatch thread itself, waits on a `Condition` over the same lock until that subscription's running callback (if any) has returned. From inside its own callback it returns at once.

**Files:**
- Create: `python/src/skython/events.py`, `python/tests/test_events.py`
- Modify: `python/src/skython/game.py` (Transport gains `events()`; `Game.on` (refused with `Unsupported` when the welcome's `capabilities` lack `events.subscribe`, i.e. a protocol 1.0 server), helpers, `wait_for`, `save`, `load`, `close`), `python/src/skython/inprocess.py` (`InProcessTransport.events()` (a module without `open_consumer`, or whose `open_consumer` raises `RuntimeError` because it has no event hub, is `Unsupported`), consumer passed to `_skython.call`, `close()` closes the stream), `python/src/skython/errors.py` (`WaitTimeout`), `python/src/skython/__init__.py` (exports `Event`, `Subscription`)
- Modify: `python/tests/test_host.py` (one test: `reload` drops the old script's subscriptions), `python/tests/test_game.py` (`CALLERS` gains the four new ops; Task 33's `PENDING_OPS` set and its two uses are deleted)
- Not changed here: `python/tests/fake_skython.py` and `python/tests/test_fixtures.py` (Task 36a)
- Unchanged: `python/src/skython/_host.py`. `reload()` already closes a script's previous `Game`; with this task `Game.close()` closes that `Game`'s subscriptions, stream and dispatch thread, so the index's "`_host` drops a script's subscriptions" needs no host code, only the test below.

**Interfaces:**
- Consumes:
  - Task 36a `python/tests/fake_skython.py`: `FakeSkython` with `hub`, `saves`, `submit(request, consumer=0)`, `call(request, timeout, consumer=0)`, `open_consumer()`, `close_consumer(consumer)`, `next_events(consumer, timeout)`, `_fake_push_event(event, data_json)`, the fake `game.save`/`game.load`, test-only `consumers()` and `subscriptions(consumer=None)`.
  - Task 35 `_skython` (index Contracts): `open_consumer() -> int`, `close_consumer(consumer) -> None` (idempotent, wakes a waiting `next_events`), `next_events(consumer, timeout) -> str` (JSON list of event objects `{"type":"event","event","sub","seq","epoch","data"[,"dropped"][,"suppressed"]}`, `"[]"` on timeout, `"[]"` at once for an unknown or closed consumer, GIL released), `call(request, timeout, consumer=0) -> str`, `submit(request, consumer=0) -> int`, fake only `_fake_push_event(event, data_json)`, `_fake_set_not_ready("loading")` also drives the hub's loading rule, the hub's epoch is the fake backend's epoch; `game.save {name}` / `game.load {save}` on the fake backend.
  - Task 33 generated (`skython._protocol_generated`): `EVENTS` (name -> `{fields, filter_fields, filter_required_any_of, deliver_while_loading}`), `DEFAULT_EVENT_QUEUE` (1024), `MAX_EVENT_QUEUE` (65536), `MAX_FILTER_VALUES` (128), `MAX_EVENTS_PER_NEXT` (256), `SAVE_NAME_PATTERN` (`^[A-Za-z0-9_]{1,64}$`), and by the generator's existing patterns `MAX_SUBSCRIPTIONS` (1024, every `limits` entry is emitted) and `OP_EVENTS_SUBSCRIBE`, `OP_EVENTS_UNSUBSCRIBE`, `OP_GAME_SAVE`, `OP_GAME_LOAD` (every op is emitted as `OP_<NAME>`). If one of these names is missing, STOP and report it (Global Constraints: one owner for `protocol.json`).
  - v1: `Game._request`, `_timeout_ms`, `_positive_seconds`, `_expect_dict` (`game.py`), `values.to_wire` / `from_wire`, `errors.*`, `tests/support.wait_until`, the `fake` fixture, `test_host.py`'s `host` fixture and `script()` helper.
- Produces (index Contracts, `skython` package):
  - `skython.events`: `Event` (frozen dataclass `name, sub, seq, epoch, data, dropped=0, suppressed=0`; `Event.from_wire(raw) -> Event`, `data` values decoded with `from_wire`, a malformed object is `ProtocolViolation`); `EventStream` (Protocol: `consumer: int | None`, `next(timeout) -> list[dict]`, `close()`, `track(sub, max_queue)`, `untrack(sub)`); `InProcessEventStream(module)` (`track`/`untrack` are no-ops); `Subscription` (`sub`, `event`, `filter`, `closed`, `close()`, context manager); `EventDispatcher(stream, game)` (`lock`, `subscribe(send, event, filter, fn, max_queue) -> Subscription` (calls `stream.track(sub, result["max_queue"])` under the lock before registering), `start()`, `close()`). Task 37 reuses `EventDispatcher` and the `EventStream` protocol as they are.
  - `skython.game.Transport` gains `events() -> EventStream` (one per transport, made on first use). `Game.on(event, filter, fn, *, max_queue=DEFAULT_EVENT_QUEUE) -> Subscription`; `on_death(actor, fn)`, `on_dying(actor, fn)`, `on_mod_event(name, fn)`, `on_channel(channel, fn)`, `on_quest_stage(quest, fn, *, stage=None)`, `on_game_loaded(fn)`; `wait_for(predicate, timeout, *, interval=0.25)`; `save(name, *, timeout=60.0) -> str`; `load(name, *, timeout=120.0, wait=True) -> dict`; `close()` closes every subscription, joins the dispatch thread (at most 2 s, never from the dispatch thread itself) and closes the stream. A transport without `events()`, a welcome whose `capabilities` list lacks `events.subscribe` (a protocol 1.0 server), or an in-process module with no event hub makes `on()` raise `Unsupported`; `on()` racing `Game.close()` raises `ConnectionLost`.
  - `skython.inprocess.InProcessTransport.events() -> InProcessEventStream` (`errors.Unsupported` when the module has no `open_consumer` or it raises `RuntimeError`: no event hub, Review A#1); `request()` passes the stream's consumer to `_skython.call` (as a third argument only when a stream exists, so v1 stubs with `call(text, timeout)` keep working).
  - `skython.errors.WaitTimeout(SkythonError)`.
  - `Game` callers for `events.subscribe`, `events.unsubscribe`, `game.save`, `game.load` in `python/tests/test_game.py` (`set(CALLERS) == set(g.OPS)` again); `Game.load(..., wait=False)` sends no `game.state` first.

- [ ] **Step 1: Write the failing tests**

`python/tests/test_events.py`:
```python
"""skython.events and the Game event API over _skython: always against the fake, and against the REAL Linux module
(C++ EventHub + Dispatcher, Task 35) when SKYTHON_REAL_MODULE_DIR names its build folder. Review Focus 2 (a slow
callback) and 4 (close races), and the "no lost first event" rule, are pinned here for the in-process transport."""
import importlib
import json
import logging
import os
import sys
import threading
import time

import pytest

from support import wait_until
from skython import _protocol_generated as g
from skython import errors
from skython.events import Event
from skython.game import Game
from skython.inprocess import connect_in_process
from skython.values import Form

MODULE_DIR = os.environ.get("SKYTHON_REAL_MODULE_DIR")


@pytest.fixture(scope="module")
def real_module(tmp_path_factory):
    """The real module, imported for this file only (the same pattern as test_real_module.py)."""
    before = sys.modules.pop("_skython", None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SKYTHON_FAKE_DIR", str(tmp_path_factory.mktemp("real-events")))
        sys.path.insert(0, MODULE_DIR)
        try:
            mod = importlib.import_module("_skython")
        finally:
            sys.path.remove(MODULE_DIR)
        try:
            assert mod.BACKEND == "fake"
            yield mod
        finally:
            mod._fake_set_not_ready(None)
            sys.modules.pop("_skython", None)
            if before is not None:
                sys.modules["_skython"] = before


@pytest.fixture(params=["fake", pytest.param("real", marks=pytest.mark.skipif(
    not MODULE_DIR, reason="SKYTHON_REAL_MODULE_DIR not set"))])
def module(request, fake):
    if request.param == "fake":
        return fake
    m = request.getfixturevalue("real_module")
    m._fake_set_not_ready(None)
    return m


@pytest.fixture
def game(module):
    with connect_in_process(module=module) as gm:
        yield gm


@pytest.fixture
def fake_game(fake):
    with connect_in_process(module=fake) as gm:
        yield gm


def push(module, event: str, data: dict) -> None:
    module._fake_push_event(event, json.dumps(data))


def chan(name: str, payload: str = "") -> dict:
    return {"channel": name, "payload": payload, "truncated": False}


def fence(game, module) -> None:
    """Callbacks run in event order on one thread: once a fence event pushed now has been delivered, every event
    pushed before it has been delivered (or skipped) too. Replaces sleeping for luck."""
    seen = []
    sub = game.on_channel("fence", seen.append)
    push(module, "channel", chan("fence"))
    wait_until(lambda: seen, what="the fence event")
    sub.close()


def form_ref(form_id: int) -> dict:
    return {"kind": "form", "form_id": form_id, "type": "Actor"}


# -- delivery ----------------------------------------------------------------------------------------------
def test_callbacks_run_in_event_order_with_forms_decoded(game, module):
    seen, threads = [], set()

    def cb(e):
        threads.add(threading.current_thread().name)
        seen.append(e)

    sub = game.on_death(None, cb)
    for i in range(1, 51):
        push(module, "death", {"victim": form_ref(0xFF000000 + i), "killer": None})
    wait_until(lambda: len(seen) == 50, what="50 death events")
    assert [e.seq for e in seen] == list(range(1, 51))
    e = seen[0]
    assert isinstance(e, Event) and e.name == "death" and e.sub == sub.sub
    assert (e.dropped, e.suppressed) == (0, 0)
    assert isinstance(e.data["victim"], Form) and e.data["victim"].form_id == 0xFF000001 and e.data["killer"] is None
    assert e.epoch == game.state()["epoch"]
    assert threads == {"skython-events"}


def test_filters_select_events_and_strings_ignore_ascii_case(game, module):
    deaths, msgs = [], []
    game.on("death", {"victim": [Form(form_id=0x100)]}, deaths.append)
    game.on_channel("My_Mod", msgs.append)
    for victim in (0x100, 0x200, 0x100):
        push(module, "death", {"victim": form_ref(victim), "killer": None})
    push(module, "channel", chan("my_mod", "a"))
    push(module, "channel", chan("MY_MOD", "b"))
    push(module, "channel", chan("other", "c"))
    fence(game, module)
    assert [e.data["victim"].form_id for e in deaths] == [0x100, 0x100]
    assert [e.data["payload"] for e in msgs] == ["a", "b"]


def test_bad_subscriptions_are_bad_request(game):
    noop = lambda e: None  # noqa: E731
    with pytest.raises(errors.BadRequest):
        game.on("no_such_event", None, noop)
    with pytest.raises(errors.BadRequest, match="hit needs a filter"):
        game.on("hit", None, noop)
    with pytest.raises(errors.BadRequest):
        game.on("death", {"victim": [Form(form_key="Skyrim.esm|0x000007")]}, noop)  # no form_id: resolve it first
    with pytest.raises(errors.BadRequest):
        game.on("death", {"no_such_field": [1]}, noop)
    with pytest.raises(errors.BadRequest):
        game.on("death", {"victim": []}, noop)
    with pytest.raises(errors.BadRequest):
        game.on("death", None, noop, max_queue=0)
    with pytest.raises(TypeError):
        game.on("death", None, "not callable")


def test_a_raising_callback_is_logged_and_later_events_still_arrive(game, module, caplog):
    caplog.set_level(logging.ERROR, logger="skython.events")
    seen = []

    def cb(e):
        seen.append(e.seq)
        if e.seq == 1:
            raise ValueError("boom in a callback")

    game.on_channel("c", cb)
    for i in range(3):
        push(module, "channel", chan("c", str(i)))
    wait_until(lambda: len(seen) == 3, what="three events")
    assert seen == [1, 2, 3]
    assert "boom in a callback" in caplog.text and "Traceback" in caplog.text


def test_a_slow_callback_drops_only_its_own_events(game, module):
    # Review Focus 2: its queue fills (drop-oldest), the drops arrive on its next event, another subscription of the
    # same Game keeps every event in order once the slow callback returns, and another Game is not held up at all.
    started = threading.Event()
    slow_seen, fast_seen, other_seen = [], [], []

    def slow(e):
        slow_seen.append(e)
        if e.seq == 1:
            started.set()
            time.sleep(2.0)

    game.on("channel", {"channel": ["slow"]}, slow, max_queue=4)  # on_channel has no max_queue (Contracts)
    game.on_channel("fast", fast_seen.append)
    with connect_in_process(module=module) as other:
        other.on_channel("slow", other_seen.append)
        push(module, "channel", chan("slow", "0"))
        assert started.wait(5), "the slow callback never started"
        t0 = time.monotonic()
        for i in range(1, 21):
            push(module, "channel", chan("slow", str(i)))
            push(module, "channel", chan("fast", str(i)))
        wait_until(lambda: len(other_seen) == 21, timeout=1.5, what="the other Game's events")
        assert time.monotonic() - t0 < 1.9, "the other Game waited for this Game's slow callback"
        wait_until(lambda: len(slow_seen) == 5 and len(fast_seen) == 20, timeout=10, what="the held-back events")
    assert [e.seq for e in slow_seen] == [1, 18, 19, 20, 21]
    assert slow_seen[1].dropped == 16 and all(e.dropped == 0 for e in slow_seen[2:])
    assert [e.data["payload"] for e in fast_seen] == [str(i) for i in range(1, 21)]
    assert [e.seq for e in fast_seen] == list(range(1, 21)) and all(e.dropped == 0 for e in fast_seen)


def test_events_during_a_load_are_suppressed_and_counted(game, module):
    # Review Focus 3, Python side: death is deliver_while_loading false, channel is true.
    deaths, msgs = [], []
    game.on_death(None, deaths.append)
    game.on_channel("c", msgs.append)
    module._fake_set_not_ready("loading")
    try:
        for i in range(3):
            push(module, "death", {"victim": form_ref(0x100 + i), "killer": None})
        push(module, "channel", chan("c", "during the load"))
        wait_until(lambda: msgs, what="the channel event during the load")
    finally:
        module._fake_set_not_ready(None)
    push(module, "death", {"victim": form_ref(0x200), "killer": None})
    wait_until(lambda: deaths, what="the death event after the load")
    fence(game, module)
    assert len(deaths) == 1 and deaths[0].suppressed == 3 and deaths[0].seq == 1
    assert msgs[0].data["payload"] == "during the load" and msgs[0].suppressed == 0


# -- close races (Review Focus 4) --------------------------------------------------------------------------
def test_close_waits_for_a_running_callback_and_no_later_callback_starts(game, module):
    inside, release, closed = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def cb(e):
        calls.append(e.seq)
        inside.set()
        release.wait(5)

    sub = game.on_channel("c", cb)
    push(module, "channel", chan("c", "1"))
    assert inside.wait(5)
    push(module, "channel", chan("c", "2"))
    push(module, "channel", chan("c", "3"))
    closer = threading.Thread(target=lambda: (sub.close(), closed.set()))
    closer.start()
    assert not closed.wait(0.3), "close() returned while the subscription's callback was still running"
    release.set()
    assert closed.wait(5)
    closer.join(5)
    push(module, "channel", chan("c", "4"))
    fence(game, module)
    assert calls == [1] and sub.closed


def test_close_from_inside_its_own_callback_returns_at_once(game, module):
    calls, holder = [], {}

    def cb(e):
        calls.append(e.seq)
        if e.seq == 1:
            holder["sub"].close()  # must not wait for itself
            holder["returned"] = True

    holder["sub"] = game.on_channel("c", cb)
    for i in range(3):
        push(module, "channel", chan("c", str(i)))
    wait_until(lambda: holder.get("returned"), what="close() inside the callback to return")
    fence(game, module)
    assert calls == [1]


def test_game_close_with_8_subscriptions_frees_the_consumer(module, fake):
    before_threads = {t for t in threading.enumerate() if t.name == "skython-events"}
    base = (fake.consumers(), fake.subscriptions())
    gm = connect_in_process(module=module)
    subs = [gm.on_channel(f"c{i}", lambda e: None) for i in range(8)]
    consumer = gm.transport.events().consumer
    mine = [t for t in threading.enumerate() if t.name == "skython-events" and t not in before_threads]
    assert len(mine) == 1
    gm.close()
    assert all(s.closed for s in subs)
    assert not mine[0].is_alive(), "Game.close() did not join its dispatch thread"
    t0 = time.monotonic()
    assert module.next_events(consumer, 5.0) == "[]"  # a closed consumer answers at once
    assert time.monotonic() - t0 < 1.0
    if module is fake:  # the real module has no counters; the fake's hub must be back where it started
        assert fake.subscriptions(consumer) == 0
        assert (fake.consumers(), fake.subscriptions()) == base


# -- no lost first event -----------------------------------------------------------------------------------
def test_subscribe_then_immediate_push_loses_no_first_event(game, module):
    # The push happens after the hub accepted the subscription and before Game.on() registered it, and the patched
    # request() returns only once the dispatch thread has FETCHED that event, i.e. while on() still holds the
    # dispatcher's lock: exactly the window the lock closes. Without the lock (send() outside it) the dispatch thread
    # would look the sub up, find nothing and drop the event, and this test fails at once. Review A#4.
    stream = game.transport.events()
    fetched = threading.Event()
    real_next = stream.next

    def next_(timeout):
        batch = real_next(timeout)
        if batch:
            fetched.set()
        return batch

    stream.next = next_  # patched before the dispatch thread exists, so every fetch goes through it
    warm = game.on_channel("warm-up", lambda e: None)  # starts the dispatch thread before the first iteration
    real_request = game.transport.request

    def request(op, args, wait_s):
        resp = real_request(op, args, wait_s)
        if op == g.OP_EVENTS_SUBSCRIBE and resp.get("ok"):
            fetched.clear()
            push(module, "channel", chan(args["filter"]["channel"][0], "first"))
            assert fetched.wait(5), "the dispatch thread never fetched the first event"
        return resp

    game.transport.request = request
    for i in range(50):
        seen = []
        sub = game.on_channel(f"race{i}", seen.append)
        wait_until(lambda: seen, what=f"the first event of subscription {i}")
        assert seen[0].seq == 1 and seen[0].sub == sub.sub and seen[0].data["payload"] == "first"
        sub.close()
    warm.close()


def test_on_from_inside_a_callback(game, module):
    second, holder = [], {}

    def first(e):
        if "sub" not in holder:
            holder["sub"] = game.on_channel("second", second.append)

    game.on_channel("first", first)
    push(module, "channel", chan("first"))
    wait_until(lambda: "sub" in holder, what="on() inside a callback to return")
    push(module, "channel", chan("second", "x"))
    wait_until(lambda: second, what="the event of the subscription made inside a callback")
    assert second[0].sub == holder["sub"].sub


# -- save / load -------------------------------------------------------------------------------------------
def test_save_and_load(game, module):
    name = f"skython_t36_{time.time_ns()}"  # the real module's fake saves live for the whole test session
    assert game.save(name) == name
    with pytest.raises(errors.BadRequest) as e:
        game.save(name)
    assert e.value.detail["reason"] == "save_exists"
    with pytest.raises(errors.BadRequest):
        game.save("not a valid name!")
    before = game.state()["epoch"]
    st = game.load(name)
    assert st["interactive"] is True and st["epoch"] == before + 1
    assert game.load(name, wait=False) == {"save": name, "started": True}
    with pytest.raises(errors.NotFound):
        game.load("skython_no_such_save")
    module._fake_set_not_ready("loading")
    try:
        with pytest.raises(errors.NotReady) as e:
            game.load(name)
        assert e.value.detail["reason"] == "loading"
        module._fake_set_not_ready("no_game")  # the main menu: loading is allowed, but the game never gets ready
        with pytest.raises(errors.NotReady) as e:
            game.load(name, timeout=0.5)
        assert e.value.detail["reason"] == "load timed out"
    finally:
        module._fake_set_not_ready(None)


# -- fake only ---------------------------------------------------------------------------------------------
def test_helpers_send_the_right_filters(fake_game, fake):
    npc, quest = Form(form_id=0x0001A677), Form(form_id=0x0010FDD5)
    noop = lambda e: None  # noqa: E731
    fake_game.on_death(npc, noop)
    fake_game.on_dying(None, noop)
    fake_game.on_mod_event("my_event", noop)
    fake_game.on_channel("my_mod", noop)
    fake_game.on_quest_stage(quest, noop, stage=20)
    fake_game.on_quest_stage(quest, noop)
    fake_game.on_game_loaded(noop)
    sent = [r["args"] for r in fake.requests if r["op"] == g.OP_EVENTS_SUBSCRIBE]
    q = g.DEFAULT_EVENT_QUEUE
    assert sent == [
        {"event": "death", "max_queue": q, "filter": {"victim": [{"kind": "form", "form_id": 0x1A677}]}},
        {"event": "dying", "max_queue": q},
        {"event": "mod_event", "max_queue": q, "filter": {"name": ["my_event"]}},
        {"event": "channel", "max_queue": q, "filter": {"channel": ["my_mod"]}},
        {"event": "quest_stage", "max_queue": q, "filter": {"quest": [{"kind": "form", "form_id": 0x10FDD5}], "stage": [20]}},
        {"event": "quest_stage", "max_queue": q, "filter": {"quest": [{"kind": "form", "form_id": 0x10FDD5}]}},
        {"event": "game_loaded", "max_queue": q},
    ]


def test_wait_for_returns_the_first_truthy_value_and_times_out(fake_game, fake):
    seen = []
    fake_game.on_channel("w", seen.append)
    push(fake, "channel", chan("w", "ready"))
    assert fake_game.wait_for(lambda: seen and seen[0].data["payload"], 5) == "ready"
    t0 = time.monotonic()
    with pytest.raises(errors.WaitTimeout):
        fake_game.wait_for(lambda: None, 0.3, interval=0.05)
    assert 0.3 <= time.monotonic() - t0 < 2.0
    with pytest.raises(ValueError):
        fake_game.wait_for(lambda: True, 0)
    assert issubclass(errors.WaitTimeout, errors.SkythonError)


def test_a_transport_without_events_is_unsupported():
    class NoEvents:
        welcome, closed = {"epoch": 0}, False

        def request(self, op, args, wait_s):
            raise AssertionError("nothing may be sent")

        def close(self):
            self.closed = True

    with pytest.raises(errors.Unsupported):
        Game(NoEvents()).on_channel("x", lambda e: None)


def test_on_after_close_is_connection_lost(fake):
    gm = connect_in_process(module=fake)
    gm.close()
    with pytest.raises(errors.ConnectionLost):
        gm.on_channel("x", lambda e: None)
    # An on() that got the dispatcher just before Game.close() stopped it (Review A#10): refused, nothing sent.
    gm2 = connect_in_process(module=fake)
    d = gm2._event_dispatcher()
    d.close()
    with pytest.raises(errors.ConnectionLost):
        d.subscribe(lambda: pytest.fail("nothing may be sent"), "channel", None, lambda e: None, 16)
    gm2.close()


def test_a_server_without_the_events_ops_is_unsupported():
    # A protocol 1.0 server (Review A#12): its welcome lists no events.subscribe, so on() refuses before sending.
    class OldServer:
        welcome, closed = {"epoch": 0, "protocol": [1, 0], "capabilities": ["game.state"]}, False

        def request(self, op, args, wait_s):
            raise AssertionError("nothing may be sent")

        def events(self):
            raise AssertionError("no stream may be made")

        def close(self):
            self.closed = True

    with pytest.raises(errors.Unsupported, match="events.subscribe"):
        Game(OldServer()).on_channel("x", lambda e: None)


def test_a_module_without_an_event_hub_is_unsupported(fake):
    # The game build before Task 39 fills ModuleHost.events: open_consumer raises RuntimeError (Review A#1).
    fake.hub = None
    with connect_in_process(module=fake) as gm:
        with pytest.raises(errors.Unsupported, match="no event hub"):
            gm.on_channel("x", lambda e: None)
        assert gm.call("Fake", "Echo", 3) == 3  # everything else still works


def test_the_dispatcher_tracks_and_untracks_each_subscription(fake_game):
    # Task 37's remote stream bounds each subscription by these calls; in-process they are no-ops but still made.
    stream = fake_game.transport.events()
    calls = []
    stream.track = lambda sub, max_queue: calls.append(("track", sub, max_queue))
    stream.untrack = lambda sub: calls.append(("untrack", sub))
    a = fake_game.on_channel("a", lambda e: None)
    b = fake_game.on("channel", {"channel": ["b"]}, lambda e: None, max_queue=16)
    a.close()
    a.close()  # idempotent: no second untrack
    assert calls == [("track", a.sub, g.DEFAULT_EVENT_QUEUE), ("track", b.sub, 16), ("untrack", a.sub)]
```

Edit `python/tests/test_game.py` (Task 33 left the four new ops out of the replay with `PENDING_OPS`). Add `from skython.events import Subscription` to the imports and these helpers above `CALLERS`:
```python
def _subscribe(game, a):
    flt = {f: [from_wire(v) for v in vals] for f, vals in a["filter"].items()} if a.get("filter") is not None else None
    return game.on(a["event"], flt, lambda e: None, **({"max_queue": a["max_queue"]} if "max_queue" in a else {}))


def _unsubscribe(game, a):
    # events.unsubscribe has no Game method of its own: it is Subscription.close(). The sub in the fixture is not one
    # this Game made, so the hub answers not_found, which close() ignores; the replay checks only what was sent.
    d = game._event_dispatcher()
    d.close_subscription(Subscription(d, a["sub"], "death", None, lambda e: None))
```
add four entries to `CALLERS`:
```python
    "events.subscribe": _subscribe,
    "events.unsubscribe": _unsubscribe,
    "game.save": lambda game, a: game.save(a["name"], timeout=a["timeout_ms"] / 1000),
    "game.load": lambda game, a: game.load(a["save"], timeout=a["timeout_ms"] / 1000, wait=False),
```
then delete the `PENDING_OPS` set and its comment, restore the replay's parametrisation to `@pytest.mark.parametrize("fx", REQUESTS["valid"], ids=lambda r: f"{r['id']}-{r['op']}")`, and restore the first assertion of `test_the_game_speaks_exactly_the_ops_in_the_table` to `assert set(CALLERS) == set(g.OPS)`. (The two `events.*` replays are answered by the fake's hub, not `replay_handler`; `game.save`/`game.load` reach `replay_handler`, whose `{}` result both methods accept.)

Append to `python/tests/test_host.py`:
```python
def test_reload_drops_the_old_scripts_subscriptions(host, fake, tmp_path):
    # The old Game is closed by reload(); with Task 36 that also closes its consumer and subscriptions.
    script(tmp_path, "t24_events", """
        seen = []

        def setup(game):
            game.on_channel("t24", seen.append)
    """)
    host.main()
    assert host.status()["scripts"] == {"t24_events": "ok"}
    assert (fake.consumers(), fake.subscriptions()) == (1, 1)
    old = sys.modules["t24_events"]
    assert host.reload("t24_events") == "ok"
    assert (fake.consumers(), fake.subscriptions()) == (1, 1)  # the new setup's; the old Game's are gone
    new = sys.modules["t24_events"]
    fake._fake_push_event("channel", json.dumps({"channel": "t24", "payload": "x", "truncated": False}))
    wait_until(lambda: new.seen, what="the reloaded script's event")
    assert old.seen == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_events.py tests/test_host.py::test_reload_drops_the_old_scripts_subscriptions tests/test_game.py 2>&1 | tail -3`
Expected: collection errors `ModuleNotFoundError: No module named 'skython.events'` (`test_events.py`, and `test_game.py` through its new import).

- [ ] **Step 3: `skython/events.py` and `WaitTimeout`**

`python/src/skython/events.py`:
```python
"""Events (spec 4.6): subscriptions, their callbacks, and the one dispatch thread per Game that runs them.

A Game subscribes with events.subscribe; the plugin's EventHub queues each matching event per subscription (bounded,
drop-oldest, with a `dropped` count on the next delivered event) and the Game's EventStream hands the event objects
over: in-process from the Game's own _skython consumer, remotely from the bridge's event frames (skython.remote).
One daemon thread per Game, "skython-events", runs every callback of that Game in event order; a callback that raises
is logged (logger skython.events, with the traceback) and the thread goes on. A slow callback holds back only this
Game's later callbacks; its own queue overflows and the drops are reported, nothing else waits.

Locking: Game.on() holds EventDispatcher.lock (an RLock) across the subscribe request, stream.track() and the
registration, and the dispatch thread takes it before looking up each event's subscription, so no first event is
lost. Callbacks run without the lock. Subscription.close() marks the subscription closed under the lock (no later
callback of it starts), calls stream.untrack(), unsubscribes, and waits for a callback of it that is running, unless
it is called from that callback's own thread.

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
    at once once closed. consumer is the _skython consumer in-process, None remotely (the bridge owns it). track()
    gives a subscription's max_queue (called under the dispatcher's lock, before the subscription is registered);
    untrack() says it ended."""

    consumer: int | None

    def next(self, timeout: float) -> list[dict]: ...

    def close(self) -> None: ...

    def track(self, sub: int, max_queue: int) -> None: ...

    def untrack(self, sub: int) -> None: ...


class InProcessEventStream:
    """An EventStream over one _skython consumer (open_consumer / next_events / close_consumer)."""

    def __init__(self, module):
        self._m = module
        self.consumer: int | None = module.open_consumer()
        self._closed = False
        self._lock = threading.Lock()

    def next(self, timeout: float) -> list[dict]:
        if self._closed:
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
            if self._closed:
                return
            self._closed = True
        self._m.close_consumer(self.consumer)  # drops its subscriptions and queued events, wakes a waiting next

    def track(self, sub: int, max_queue: int) -> None:
        pass  # the hub bounds each subscription in-process

    def untrack(self, sub: int) -> None:
        pass


class Subscription:
    """One subscription of a Game. close() is idempotent; after it returns no callback of this subscription starts."""

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
                raise errors.ProtocolViolation(f"events.subscribe result has no integer sub: {result!r}")
            granted = result.get("max_queue", max_queue)
            self.stream.track(sub_id, granted if type(granted) is int else max_queue)
            sub = Subscription(self, sub_id, event, filter, fn)
            self._subs[sub_id] = sub
        self.start()
        return sub

    def close_subscription(self, sub: Subscription) -> None:
        with self.lock:
            if sub.closed:
                return
            sub.closed = True  # checked under this lock right before each callback: no later callback starts
            self._subs.pop(sub.sub, None)
            self.stream.untrack(sub.sub)  # remotely: its queued frames go, later ones are ignored
        try:
            self._game._request(_g.OP_EVENTS_UNSUBSCRIBE, {"sub": sub.sub})
        except (errors.ConnectionLost, errors.NotFound):
            pass  # the Game or connection is closed (its consumer went with it), or the hub already dropped it
        except errors.SkythonError as e:
            log.warning("events: unsubscribing sub %d failed: %s", sub.sub, e)
        with self.lock:
            if threading.current_thread() is not self._thread:  # from its own callback: never wait for ourselves
                while self._running is sub:
                    self._idle.wait()

    # -- the thread -------------------------------------------------------------------------------------
    def start(self) -> None:
        with self.lock:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._loop, name="skython-events", daemon=True)
                self._thread.start()

    def close(self) -> None:
        """Close every subscription (no unsubscribe requests: closing the stream drops them), stop the thread and
        join it (at most _JOIN_S, never from the dispatch thread itself), close the stream."""
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
                self._deliver(raw)

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
```

In `python/src/skython/errors.py`, after `class EncodeError(...)`:
```python
class WaitTimeout(SkythonError):
    """Game.wait_for gave up: the predicate returned nothing truthy within its timeout."""
```
and add `"WaitTimeout"` to `__all__` after `"EncodeError"`.

In `python/src/skython/__init__.py`, after the `.errors` import line:
```python
from .events import Event, Subscription  # noqa: F401
```

- [ ] **Step 4: `Game` additions**

In `python/src/skython/game.py`: add `import threading` and `from typing import Any, Callable, Protocol` (replacing the `typing` import), and `from .events import EventDispatcher, EventStream, Subscription` after the `values` import. Extend the `Transport` protocol:
```python
    def events(self) -> EventStream: ...  # one stream per transport, made on first use (Task 36)
```
At the end of `Game.__init__` add:
```python
        self._events_lock = threading.Lock()
        self._dispatcher: EventDispatcher | None = None
        self._closing = False
```
Replace `Game.close` with the block below, and insert the event methods before it (after `wait_ready`):
```python
    # -- events (spec 4.6) ----------------------------------------------------------------------------
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

        `filter` maps a field of the event's filter_fields to the values to accept (a list, or one value): every
        listed field must match one of its values. Forms must carry form_id (resolve them first); strings match
        ignoring ASCII case. At most `max_queue` events wait per subscription; beyond that the oldest are dropped
        and the next delivered Event's `dropped` says how many.

        on() holds the Game's dispatcher lock across the subscribe round trip (about 90 ms through the bridge), so
        this Game's callbacks wait that long; that is what guarantees the first event is never lost. Raises
        Unsupported when the server or module has no events, ConnectionLost once the Game is closed."""
        if not callable(fn):
            raise TypeError(f"fn must be callable, not {type(fn).__name__}")
        args: dict[str, Any] = {"event": event, "max_queue": max_queue}
        if filter is not None:
            if not isinstance(filter, dict):
                raise TypeError(f"filter must be a dict or None, not {type(filter).__name__}")
            args["filter"] = {field: [to_wire(v) for v in (vals if isinstance(vals, (list, tuple)) else [vals])]
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
        """Call predicate() every `interval` seconds until it returns something truthy, and return that (spec 4.7).
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
        the load starts ends NotReady (spec 4.5); subscriptions stay."""
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
        """Close every subscription, stop the event thread (joined at most 2 s), close the stream and the transport."""
        with self._events_lock:
            self._closing = True
            d, self._dispatcher = self._dispatcher, None
        if d is not None:
            d.close()
        self.transport.close()
```

- [ ] **Step 5: `InProcessTransport.events()`**

In `python/src/skython/inprocess.py`: add `from .events import InProcessEventStream` after the `.game` import (`_g` and `errors` are imported there since v1). At the end of `__init__` add:
```python
        self._stream: InProcessEventStream | None = None
        self._stream_lock = threading.Lock()
```
Add the method and replace `request`'s `call` line and `close`:
```python
    def events(self) -> InProcessEventStream:
        """This transport's event stream (one _skython consumer), opened on first use."""
        with self._stream_lock:
            if self.closed:
                raise errors.ConnectionLost("the in-process transport is closed")
            if self._stream is None:
                if not hasattr(self._m, "open_consumer"):
                    raise errors.Unsupported(_g.CODE_UNSUPPORTED, "this _skython module has no event API")
                try:
                    self._stream = InProcessEventStream(self._m)
                except RuntimeError as e:  # ModuleHost.events is null: the game build before Task 39 (Review A#1)
                    raise errors.Unsupported(_g.CODE_UNSUPPORTED, f"no event hub: {e}") from None
            return self._stream
```
In `request`, replace `resp = _object(self._m.call(text, wait_s), f"the response to {op}")` with:
```python
        stream = self._stream
        consumer = stream.consumer if stream is not None else 0
        # The consumer travels beside the request text (index Contracts), only when there is one: 0 is the default.
        raw = self._m.call(text, wait_s, consumer) if consumer else self._m.call(text, wait_s)
        resp = _object(raw, f"the response to {op}")
```
```python
    def close(self) -> None:
        self.closed = True
        with self._stream_lock:
            stream = self._stream
        if stream is not None:
            stream.close()
```

- [ ] **Step 6: Run the new tests**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_events.py tests/test_host.py tests/test_game.py 2>&1 | tail -2`
Expected: `NN passed, 12 skipped` where NN is the existing `test_host.py` and `test_game.py` counts plus 24 (19 in `test_events.py`, 1 in `test_host.py`, 4 more fixture replays in `test_game.py` now that `PENDING_OPS` is gone); the 12 skips are the `real` parameters (`SKYTHON_REAL_MODULE_DIR not set`). The run takes a few seconds (the slow-callback test sleeps 2 s; the 50-iteration race test well under 1 s).

Against the real module (Task 35 built it; the real module is built for 3.14):
Run: `cmake --build pyglue/build -j 2>&1 | tail -1 && cd python && SKYTHON_REAL_MODULE_DIR="$(cd ../pyglue/build && pwd)" uv run --managed-python --python 3.14 pytest -q tests/test_events.py tests/test_real_module.py 2>&1 | tail -2`
Expected: `NN passed` with no skips: the 31 cases of `test_events.py` (12 behaviour tests on both backends, 7 fake-only) plus `test_real_module.py`'s. A case that passes on the fake and fails on the real module means Task 36a's fake drifted from core (its own `test_fake_hub.py` should have caught it): fix the fake, never the assertion.

- [ ] **Step 7: Run the whole suite on both Pythons, then the races on one CPU**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected twice: Task 36a's total plus 24 passed, plus Task 36a's skips plus 12; no failures (the v1 tests that stub `call(text, timeout)` are unchanged by Step 5: the consumer is passed only once a stream exists).

Run: `cd python && for i in 1 2 3; do uv run --managed-python --python 3.14 pytest -q tests/test_events.py 2>&1 | tail -1; done; taskset -c 0 uv run --managed-python --python 3.12 pytest -q tests/test_events.py 2>&1 | tail -1`
Expected: `19 passed, 12 skipped` four times. Every wait is `wait_until` or a fence with a deadline; the one negative wait (`closed.wait(0.3)`) only checks that `close()` is still blocked.

Run: `python3 tools/gen_protocol.py --check`
Expected: the generated files are up to date (this task edits none of them).

- [ ] **Step 8: Commit**

```bash
git add python/src/skython/events.py python/src/skython/game.py python/src/skython/inprocess.py \
        python/src/skython/errors.py python/src/skython/__init__.py \
        python/tests/test_events.py python/tests/test_host.py python/tests/test_game.py
git commit -m "feat(python): event subscriptions with one dispatch thread per Game, wait_for, save and load"
```
