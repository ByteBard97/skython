"""_skython's event functions (index Contracts, Task 35) against the fake backend and core's EventHub."""
import json
import threading
import time
import uuid

import pytest

import _skython
from conftest import code, request, response


def op(req_id, op_name, **args):
    return json.dumps({"type": "request", "id": req_id, "op": op_name, "args": args})


@pytest.fixture
def consumer():
    """Opens consumers for a test and closes them after it (the fake build's hub is shared by every test)."""
    opened = []

    def open_one():
        c = _skython.open_consumer()
        opened.append(c)
        return c

    yield open_one
    for c in opened:
        _skython.close_consumer(c)


def subscribe(c, event, req_id=1, **args):
    r = response(_skython.call(op(req_id, "events.subscribe", event=event, **args), 5.0, c))
    assert r["ok"], r
    return r["result"]["sub"]


def push(event, **data):
    _skython._fake_push_event(event, json.dumps(data))


def drain(c, timeout=0.5):
    return json.loads(_skython.next_events(c, timeout))


def form(i):
    return {"kind": "form", "form_id": i}


def chan(name, payload="p"):
    return {"channel": name, "payload": payload, "truncated": False}


def epoch():
    return json.loads(_skython.state())["epoch"]


def test_surface():
    for name in ("open_consumer", "close_consumer", "next_events", "_fake_push_event"):
        assert callable(getattr(_skython, name)), name
    a, b = _skython.open_consumer(), _skython.open_consumer()
    assert a > 0 and b > a  # never 0, never reused
    _skython.close_consumer(a)
    _skython.close_consumer(b)


def test_subscribe_push_and_next_events_round_trip(consumer):
    c = consumer()
    sub = subscribe(c, "channel", filter={"channel": ["My_Mod"]})
    push("channel", **chan("my_mod", "hello"))  # the filter ignores ASCII case
    push("channel", **chan("other"))
    assert drain(c) == [{"type": "event", "event": "channel", "sub": sub, "seq": 1, "epoch": epoch(), "data": chan("my_mod", "hello")}]
    assert drain(c, 0.0) == []


def test_events_requests_without_a_consumer_are_bad_request():
    r = response(_skython.call(op(2, "events.subscribe", event="death"), 5.0))
    assert code(r) == "bad_request" and r["error"]["message"] == "no event consumer"
    r = response(_skython.wait(_skython.submit(op(3, "events.unsubscribe", sub=1)), 5.0))
    assert code(r) == "bad_request"


def test_one_consumer_cannot_unsubscribe_anothers_subscription(consumer):
    a, b = consumer(), consumer()
    sub = subscribe(a, "death")
    assert code(response(_skython.call(op(4, "events.unsubscribe", sub=sub), 5.0, b))) == "not_found"
    r = response(_skython.call(op(5, "events.unsubscribe", sub=sub), 5.0, a))
    assert r["ok"] and r["result"] is None


def test_next_events_releases_the_gil_while_it_waits(consumer):
    c = consumer()
    subscribe(c, "channel")
    done = threading.Event()
    result = {}

    def waiter():
        result["events"] = drain(c, 2.0)
        done.set()

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)  # let the waiter get into next_events
    count = 0
    start = time.monotonic()
    while not done.is_set():
        count += 1
    elapsed = time.monotonic() - start
    t.join()
    assert result["events"] == [] and elapsed > 1.5
    # Holding the GIL for 2 s would have stopped this loop; a free loop does millions of iterations in that time.
    assert count > 10_000, count


def test_next_events_on_a_closed_or_unknown_consumer_returns_at_once(consumer):
    c = consumer()
    subscribe(c, "channel")
    push("channel", **chan("x"))
    _skython.close_consumer(c)
    t0 = time.monotonic()
    assert _skython.next_events(c, 5.0) == "[]"
    assert _skython.next_events(2**63, 5.0) == "[]"
    assert time.monotonic() - t0 < 0.5
    _skython.close_consumer(c)  # idempotent
    assert code(response(_skython.call(op(6, "events.subscribe", event="death"), 5.0, c))) == "bad_request"


def test_a_consumer_nobody_drains_keeps_max_queue_and_reports_drops(consumer):
    """Review Focus 2: the slow consumer's queue stays bounded, its drops are reported, the other one sees everything."""
    slow, fast = consumer(), consumer()
    subscribe(slow, "channel", max_queue=4)
    subscribe(fast, "channel")
    for i in range(10):
        push("channel", **chan("x", str(i)))
    assert [e["data"]["payload"] for e in drain(fast)] == [str(i) for i in range(10)]
    got = drain(slow)
    assert [e["seq"] for e in got] == [7, 8, 9, 10]
    assert got[0]["dropped"] == 6 and all("dropped" not in e for e in got[1:])


def test_loading_suppresses_world_events_but_delivers_menus(consumer):
    """Review Focus 3, on the fake: _fake_set_not_ready("loading") is the hub's loading source."""
    c = consumer()
    cell = subscribe(c, "cell_attach")
    subscribe(c, "menu_open", req_id=2)
    _skython._fake_set_not_ready("loading")
    try:
        push("cell_attach", reference=form(20))
        push("cell_attach", reference=form(21))
        push("menu_open", menu="Loading Menu")
        assert [e["event"] for e in drain(c)] == ["menu_open"]
    finally:
        _skython._fake_set_not_ready(None)
    push("cell_attach", reference=form(22))
    got = drain(c)
    assert len(got) == 1 and got[0]["sub"] == cell and got[0]["seq"] == 1 and got[0]["suppressed"] == 2


def test_the_epoch_stamp_is_the_fake_epoch(consumer):
    c = consumer()
    subscribe(c, "channel")
    before = epoch()
    push("channel", **chan("x"))
    _skython._fake_bump_loads()
    push("channel", **chan("x"))
    assert [e["epoch"] for e in drain(c)] == [before, before + 1]


def test_sixteen_threads_each_with_a_consumer_see_only_their_own_events(consumer):
    n, per = 16, 625  # 10^4 events in all; each queue (default 1024) is large enough that nothing drops
    cs = [consumer() for _ in range(n)]
    subs = [subscribe(cs[i], "channel", req_id=100 + i, filter={"channel": [f"ch{i}"]}) for i in range(n)]
    results = [None] * n

    def reader(i):
        got, deadline = [], time.monotonic() + 30
        while len(got) < per and time.monotonic() < deadline:
            got += json.loads(_skython.next_events(cs[i], 0.25))
        results[i] = got

    def pusher():
        for k in range(n * per):
            push("channel", **chan(f"ch{k % n}", str(k)))

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(n)] + [threading.Thread(target=pusher)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(40)
    assert not any(t.is_alive() for t in threads), "a reader or the pusher is stuck"
    for i in range(n):
        got = results[i]
        assert got is not None and len(got) == per, (i, None if got is None else len(got))
        assert all(e["sub"] == subs[i] and e["data"]["channel"] == f"ch{i}" for e in got)
        assert [e["seq"] for e in got] == list(range(1, per + 1))


def test_game_load_bumps_loads_and_ends_an_in_flight_call_with_not_ready_dispatched():
    name = f"skython_t35_{uuid.uuid4().hex[:8]}"
    assert response(_skython.call(op(7, "game.save", name=name), 5.0))["result"] == {"name": name}
    again = response(_skython.call(op(8, "game.save", name=name), 5.0))
    assert code(again) == "bad_request" and again["error"]["detail"]["reason"] == "save_exists"
    before = epoch()
    slow = _skython.submit(request(9, "Sleep", 5000))
    r = response(_skython.call(op(10, "game.load", save=name), 5.0))
    assert r["result"] == {"save": name, "started": True}
    ended = response(_skython.wait(slow, 3.0))  # the reaper's next tick ends it
    assert code(ended) == "not_ready" and ended["error"]["detail"]["dispatched"] is True
    assert epoch() == before + 1
    assert code(response(_skython.call(op(11, "game.load", save="skython_never_saved"), 5.0))) == "not_found"


@pytest.mark.parametrize("event, data", [("no_such_event", "{}"), ("death", "not json"), ("death", "[1]"), ("death", "[" * 100 + "]" * 100)])
def test_fake_push_event_refuses_unknown_events_and_bad_data(event, data):
    with pytest.raises(ValueError):
        _skython._fake_push_event(event, data)
