"""skython.events and the Game event API over _skython: always against the fake, and against the real Linux module
(C++ EventHub + Dispatcher) when SKYTHON_REAL_MODULE_DIR names its build folder. A slow callback, close races,
and the "no lost first event" rule, are pinned here for the in-process transport."""
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
    # A slow callback: its queue fills (drop-oldest), the drops arrive on its next event, another subscription of the
    # same Game keeps every event in order once the slow callback returns, and another Game is not held up at all.
    started = threading.Event()
    slow_seen, fast_seen, other_seen = [], [], []

    def slow(e):
        slow_seen.append(e)
        if e.seq == 1:
            started.set()
            time.sleep(2.0)

    game.on("channel", {"channel": ["slow"]}, slow, max_queue=4)  # on_channel has no max_queue
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
    # The loading rule, Python side: death is deliver_while_loading false, channel is true.
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


# -- close races ----------------------------------------------------------------------------------------
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
    # request() returns only once the dispatch thread has fetched that event, i.e. while on() still holds the
    # dispatcher's lock: exactly the window the lock closes. Without the lock (send() outside it) the dispatch thread
    # would look the sub up, find nothing and drop the event, and this test fails at once.
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
    # An on() that got the dispatcher just before Game.close() stopped it: refused, nothing sent.
    gm2 = connect_in_process(module=fake)
    d = gm2._event_dispatcher()
    d.close()
    with pytest.raises(errors.ConnectionLost):
        d.subscribe(lambda: pytest.fail("nothing may be sent"), "channel", None, lambda e: None, 16)
    gm2.close()


def test_a_server_without_the_events_ops_is_unsupported():
    # A protocol 1.0 server: its welcome lists no events.subscribe, so on() refuses before sending.
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
    # An older plugin build has no ModuleHost.events: open_consumer raises RuntimeError.
    fake.hub = None
    with connect_in_process(module=fake) as gm:
        with pytest.raises(errors.Unsupported, match="no event hub"):
            gm.on_channel("x", lambda e: None)
        assert gm.call("Fake", "Echo", 3) == 3  # everything else still works


def test_the_dispatcher_tracks_and_untracks_each_subscription(fake_game):
    # The remote stream bounds each subscription by these calls; in-process they are no-ops but still made.
    stream = fake_game.transport.events()
    calls = []
    stream.track = lambda sub, max_queue: calls.append(("track", sub, max_queue))
    stream.untrack = lambda sub: calls.append(("untrack", sub))
    a = fake_game.on_channel("a", lambda e: None)
    b = fake_game.on("channel", {"channel": ["b"]}, lambda e: None, max_queue=16)
    a.close()
    a.close()  # idempotent: no second untrack
    assert calls == [("track", a.sub, g.DEFAULT_EVENT_QUEUE), ("track", b.sub, 16), ("untrack", a.sub)]


# -- close and re-entrancy ------------------------------------------------------------------------------
def _dispatch_threads() -> set:
    return {t for t in threading.enumerate() if t.name == "skython-events"}


def test_game_close_from_inside_a_callback(module):
    before = _dispatch_threads()
    gm = connect_in_process(module=module)
    calls, holder = [], {}

    def cb(e):
        calls.append(e.seq)
        if e.seq == 1:
            gm.close()  # must not join its own thread
            holder["returned"] = True

    gm.on_channel("c", cb)
    (thread,) = _dispatch_threads() - before
    for i in range(3):
        push(module, "channel", chan("c", str(i)))
    wait_until(lambda: holder.get("returned"), what="Game.close() inside the callback to return")
    wait_until(lambda: not thread.is_alive(), what="the dispatch thread to stop")
    push(module, "channel", chan("c", "late"))
    time.sleep(0.1)
    assert calls == [1] and gm.transport.closed


def test_a_callback_closing_another_subscription(game, module):
    a_calls, b_calls, holder = [], [], {}

    def a_cb(e):
        a_calls.append(e.seq)
        holder["b"].close()  # on the dispatch thread: returns at once
        holder["returned"] = True

    game.on_channel("a", a_cb)
    holder["b"] = game.on_channel("b", lambda e: b_calls.append(e.seq))
    push(module, "channel", chan("a"))
    push(module, "channel", chan("b", "1"))
    push(module, "channel", chan("b", "2"))
    wait_until(lambda: holder.get("returned"), what="the other subscription's close() to return")
    push(module, "channel", chan("b", "3"))
    fence(game, module)
    assert a_calls == [1] and b_calls == [] and holder["b"].closed


def test_a_callback_can_call_game_state(game, module):
    got = []
    game.on_channel("s", lambda e: got.append(game.state()))
    push(module, "channel", chan("s"))
    wait_until(lambda: got, what="the callback's state()")
    assert isinstance(got[0], dict) and "epoch" in got[0]


def _blocking_sub(game, module, channel="c"):
    inside, release = threading.Event(), threading.Event()

    def cb(e):
        inside.set()
        release.wait(5)

    sub = game.on_channel(channel, cb)
    push(module, "channel", chan(channel))
    assert inside.wait(5)
    return sub, release


def _closer(fn):
    done = threading.Event()
    t = threading.Thread(target=lambda: (fn(), done.set()))
    t.start()
    return t, done


def test_a_second_overlapping_close_also_waits_for_the_callback(game, module):
    sub, release = _blocking_sub(game, module)
    t1, done1 = _closer(sub.close)
    wait_until(lambda: sub.closed, what="the first close() to mark the subscription")
    t2, done2 = _closer(sub.close)
    assert not done2.wait(0.3), "the second close() returned while the callback was still running"
    assert not done1.is_set()
    release.set()
    assert done1.wait(5) and done2.wait(5)
    t1.join(5)
    t2.join(5)


def test_close_after_game_close_still_waits_for_the_callback(module):
    gm = connect_in_process(module=module)
    sub, release = _blocking_sub(gm, module)
    tg, done_game = _closer(gm.close)
    wait_until(lambda: sub.closed, what="Game.close() to mark the subscription")
    ts, done_sub = _closer(sub.close)
    assert not done_sub.wait(0.3), "close() after Game.close() returned while the callback was still running"
    release.set()
    assert done_sub.wait(5) and done_game.wait(5)
    tg.join(5)
    ts.join(5)


def test_transport_close_stops_the_dispatch_thread(module):
    before = _dispatch_threads()
    gm = connect_in_process(module=module)
    gm.on_channel("c", lambda e: None)
    (thread,) = _dispatch_threads() - before
    gm.transport.close()
    wait_until(lambda: not thread.is_alive(), what="the dispatch thread to stop after transport.close()")
    with pytest.raises(errors.ConnectionLost):
        gm.on_channel("d", lambda e: None)
    gm.close()


def test_a_failing_delivery_is_logged_and_the_thread_goes_on(fake_game, fake, caplog):
    d = fake_game._event_dispatcher()
    real, failed = d._deliver, []

    def flaky(raw):
        if not failed:
            failed.append(raw)
            raise RuntimeError("boom")
        real(raw)

    d._deliver = flaky
    seen = []
    fake_game.on_channel("c", seen.append)
    with caplog.at_level(logging.ERROR, logger="skython.events"):
        push(fake, "channel", chan("c", "1"))
        push(fake, "channel", chan("c", "2"))
        wait_until(lambda: seen, what="the event after the failed delivery")
    assert [e.data["payload"] for e in seen] == ["2"]
    assert any("delivering an event failed" in r.getMessage() for r in caplog.records)


def test_filter_values_may_be_a_list_tuple_set_or_frozenset(fake_game, fake):
    noop = lambda e: None  # noqa: E731
    for vals in (["x"], ("x",), {"x"}, frozenset({"x"}), "x"):
        fake_game.on("channel", {"channel": vals}, noop)
    sent = [r["args"]["filter"] for r in fake.requests if r["op"] == g.OP_EVENTS_SUBSCRIBE]
    assert sent == [{"channel": ["x"]}] * 5


def test_a_failed_track_unsubscribes_the_hub_subscription(fake_game, fake):
    stream = fake_game.transport.events()
    base = fake.subscriptions(stream.consumer)

    def bad_track(sub, max_queue):
        raise RuntimeError("track failed")

    stream.track = bad_track
    with pytest.raises(RuntimeError, match="track failed"):
        fake_game.on_channel("c", lambda e: None)
    assert fake.subscriptions(stream.consumer) == base
    assert [r["op"] for r in fake.requests][-2:] == [g.OP_EVENTS_SUBSCRIBE, g.OP_EVENTS_UNSUBSCRIBE]


def test_a_non_integer_sub_is_unsubscribed_before_raising(fake_game, fake):
    d = fake_game._event_dispatcher()
    sent, real = [], fake_game._request
    fake_game._request = lambda op, args, timeout=None: (sent.append((op, args)), real(op, args, timeout))[1]
    with pytest.raises(errors.ProtocolViolation):
        d.subscribe(lambda: {"sub": "7"}, "channel", None, lambda e: None, 16)  # unsubscribe fails: logged only
    assert sent == [(g.OP_EVENTS_UNSUBSCRIBE, {"sub": "7"})]
    assert d._subs == {}
