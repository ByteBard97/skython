"""The Python fake's event hub and save/load. Every behaviour test runs against the fake and against the
real Linux _skython (C++ EventHub + Dispatcher + FakeBackend) when SKYTHON_REAL_MODULE_DIR names its
build folder, so the fake cannot drift from core. The fake-only tests at the end cover the test-only counters."""
import importlib
import itertools
import json
import os
import sys
import threading
import time

import pytest

from fake_skython import FakeSkython
from support import load_fixture
from skython import _protocol_generated as g

MODULE_DIR = os.environ.get("SKYTHON_REAL_MODULE_DIR")
EVENTS_FX = load_fixture("events.json")
_ids = itertools.count(1000)


@pytest.fixture(scope="module")
def real_module(tmp_path_factory):
    """The real module, imported for this file only (the same pattern as test_real_module.py). `module` requests it
    without the function-scoped `fake`, so `before` is never a fake that is torn down by the time this is restored."""
    before = sys.modules.pop("_skython", None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SKYTHON_FAKE_DIR", str(tmp_path_factory.mktemp("real-hub")))
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
def module(request):
    if request.param == "fake":
        return request.getfixturevalue("fake")
    m = request.getfixturevalue("real_module")
    m._fake_set_not_ready(None)
    return m


def rpc(m, op, args, consumer=0):
    req = {"type": "request", "id": next(_ids), "op": op, "args": args}
    return json.loads(m.call(json.dumps(req), 5.0, consumer))


def err(resp):
    assert resp["ok"] is False, resp
    return resp["error"]["code"], resp["error"]["message"]


def push(m, event, data):
    m._fake_push_event(event, json.dumps(data))


def chan(name, payload=""):
    return {"channel": name, "payload": payload, "truncated": False}


def drain(m, consumer, n, timeout=5.0):
    out, deadline = [], time.monotonic() + timeout
    while len(out) < n and time.monotonic() < deadline:
        out += json.loads(m.next_events(consumer, 0.2))
    return out


def subscribe(m, consumer, event="channel", flt=None, max_queue=None):
    args = {"event": event}
    if flt is not None:
        args["filter"] = flt
    if max_queue is not None:
        args["max_queue"] = max_queue
    resp = rpc(m, g.OP_EVENTS_SUBSCRIBE, args, consumer)
    assert resp["ok"] is True, resp
    return resp["result"]


def test_subscribe_answers_sub_event_and_max_queue(module):
    c = module.open_consumer()
    a = subscribe(module, c, "death")
    b = subscribe(module, c, "channel", {"channel": ["x"]}, max_queue=16)
    assert (a["event"], a["max_queue"]) == ("death", g.DEFAULT_EVENT_QUEUE)
    assert (b["event"], b["max_queue"]) == ("channel", 16)
    assert type(a["sub"]) is int and b["sub"] > a["sub"] >= 1
    assert subscribe(module, c, "game_loaded", None)["event"] == "game_loaded"  # "filter": absent = no filter
    module.close_consumer(c)


def test_frames_carry_seq_epoch_and_only_the_table_keys(module):
    c = module.open_consumer()
    s = subscribe(module, c, "channel", {"channel": ["c"]})["sub"]
    for i in range(3):
        push(module, "channel", chan("c", str(i)))
    frames = drain(module, c, 3)
    epoch = json.loads(module.state())["epoch"]
    assert [f["seq"] for f in frames] == [1, 2, 3]
    assert all(set(f) == {"type", "event", "sub", "seq", "epoch", "data"} for f in frames)
    assert all(f["type"] == "event" and f["sub"] == s and f["epoch"] == epoch for f in frames)
    assert frames[0]["data"] == chan("c", "0")
    module.close_consumer(c)


def test_drop_oldest_reports_dropped_on_the_next_delivered_frame(module):
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["q"]}, max_queue=4)
    for i in range(10):
        push(module, "channel", chan("q", str(i)))
    frames = drain(module, c, 4)
    assert [f["seq"] for f in frames] == [7, 8, 9, 10]
    assert frames[0]["dropped"] == 6 and all("dropped" not in f for f in frames[1:])
    assert [f["data"]["payload"] for f in frames] == ["6", "7", "8", "9"]
    module.close_consumer(c)


def test_events_during_a_load_are_suppressed_unless_delivered_while_loading(module):
    c = module.open_consumer()
    subscribe(module, c, "death")
    subscribe(module, c, "channel", {"channel": ["c"]})
    module._fake_set_not_ready("loading")
    try:
        for i in range(3):
            push(module, "death", {"victim": {"kind": "form", "form_id": 0x100 + i}, "killer": None})
        push(module, "channel", chan("c", "during"))
    finally:
        module._fake_set_not_ready(None)
    push(module, "death", {"victim": {"kind": "form", "form_id": 0x200}, "killer": None})
    frames = drain(module, c, 2)
    assert [f["event"] for f in frames] == ["channel", "death"]
    assert "suppressed" not in frames[0]
    assert frames[1]["seq"] == 1 and frames[1]["suppressed"] == 3
    module.close_consumer(c)


def test_filters_match_forms_by_id_ints_and_strings_ignoring_ascii_case(module):
    c = module.open_consumer()
    subscribe(module, c, "death", {"victim": [{"kind": "form", "form_id": 0x100}]})
    subscribe(module, c, "quest_stage", {"quest": [{"kind": "form", "form_id": 7}], "stage": [10, 20]})
    subscribe(module, c, "channel", {"channel": ["My_Mod"]})
    for victim in (0x100, 0x200):
        push(module, "death", {"victim": {"kind": "form", "form_id": victim}, "killer": None})
    push(module, "death", {"victim": None, "killer": None})  # a null event field never matches
    for stage in (10, 15, 20):
        push(module, "quest_stage", {"quest": {"kind": "form", "form_id": 7}, "stage": stage})
    for name in ("my_mod", "MY_MOD", "other"):
        push(module, "channel", chan(name))
    frames = drain(module, c, 5)
    assert [(f["event"], f["data"].get("stage", f["data"].get("channel"))) for f in frames[1:]] == [
        ("quest_stage", 10), ("quest_stage", 20), ("channel", "my_mod"), ("channel", "MY_MOD")]
    assert frames[0]["data"]["victim"]["form_id"] == 0x100
    assert json.loads(module.next_events(c, 0.2)) == []
    module.close_consumer(c)


@pytest.mark.parametrize("case", EVENTS_FX["invalid_filters"], ids=lambda c: f"{c['event']}-{c['message'][:24]}")
def test_invalid_filters_are_refused_with_cores_text(module, case):
    c = module.open_consumer()
    resp = rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": case["event"], "filter": case["filter"]}, c)
    code, message = err(resp)
    assert code == case["error"] and case["message"] in message, message
    module.close_consumer(c)


def test_consumer_zero_unknown_and_closed_consumers(module):
    for op, args in ((g.OP_EVENTS_SUBSCRIBE, {"event": "death"}), (g.OP_EVENTS_UNSUBSCRIBE, {"sub": 1})):
        assert err(rpc(module, op, args)) == ("bad_request", "no event consumer")
    c = module.open_consumer()
    s = subscribe(module, c, "death")["sub"]
    module.close_consumer(c)
    module.close_consumer(c)  # idempotent
    code, message = err(rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "death"}, c))
    assert code == "bad_request" and message.startswith("no event consumer")
    assert err(rpc(module, g.OP_EVENTS_UNSUBSCRIBE, {"sub": s}, c)) == ("not_found", f"no subscription {s} for this consumer")
    t0 = time.monotonic()
    assert module.next_events(c, 5.0) == "[]" and time.monotonic() - t0 < 1.0  # a closed consumer answers at once


def test_another_consumers_sub_is_not_found_and_consumers_are_isolated(module):
    a, b = module.open_consumer(), module.open_consumer()
    sa = subscribe(module, a, "channel", {"channel": ["a"]})["sub"]
    subscribe(module, b, "channel", {"channel": ["b"]})
    assert err(rpc(module, g.OP_EVENTS_UNSUBSCRIBE, {"sub": sa}, b)) == ("not_found", f"no subscription {sa} for this consumer")
    unknown = 18446744073709551615
    assert err(rpc(module, g.OP_EVENTS_UNSUBSCRIBE, {"sub": unknown}, b)) == ("not_found", f"no subscription {unknown} for this consumer")
    push(module, "channel", chan("a"))
    push(module, "channel", chan("b"))
    assert [f["data"]["channel"] for f in drain(module, a, 1)] == ["a"]
    assert [f["data"]["channel"] for f in drain(module, b, 1)] == ["b"]
    done = rpc(module, g.OP_EVENTS_UNSUBSCRIBE, {"sub": sa}, a)  # its owner can
    assert done["ok"] is True and done["result"] is None
    push(module, "channel", chan("a"))
    assert json.loads(module.next_events(a, 0.2)) == []  # unsubscribed: nothing more for it
    module.close_consumer(a)
    module.close_consumer(b)


def test_save_and_load_texts_and_the_epoch(module):
    name = f"skython_t36a_{time.time_ns()}"  # the real module's fake saves live for the whole test session
    ok = rpc(module, g.OP_GAME_SAVE, {"name": name})
    assert ok["ok"] is True and ok["result"] == {"name": name}
    again = rpc(module, g.OP_GAME_SAVE, {"name": name})
    assert err(again) == ("bad_request", f"a save named {name} already exists")
    assert again["error"]["detail"]["reason"] == "save_exists"
    assert err(rpc(module, g.OP_GAME_LOAD, {"save": "skython_no_such_save"})) == ("not_found", "no save named skython_no_such_save")
    before = json.loads(module.state())["epoch"]
    loaded = rpc(module, g.OP_GAME_LOAD, {"save": name})
    assert loaded["ok"] is True and loaded["result"] == {"save": name, "started": True}
    assert json.loads(module.state())["epoch"] == before + 1
    for reason, text in (("loading", "a game is loading"), ("starting", "the game is still starting")):
        module._fake_set_not_ready(reason)
        try:
            resp = rpc(module, g.OP_GAME_LOAD, {"save": name})
            assert err(resp) == ("not_ready", text)
            assert set(resp["error"]["detail"]) >= {"reason", "paused", "menus"} and resp["error"]["detail"]["reason"] == reason
        finally:
            module._fake_set_not_ready(None)


def test_one_consumer_holds_at_most_max_subscriptions_per_consumer(module):
    a, b = module.open_consumer(), module.open_consumer()
    for i in range(g.MAX_SUBSCRIPTIONS_PER_CONSUMER):
        subscribe(module, a, "channel", {"channel": [f"cap{i}"]})
    code, message = err(rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "death"}, a))
    assert (code, message) == ("busy", f"consumer {a} has {g.MAX_SUBSCRIPTIONS_PER_CONSUMER} subscriptions (max_subscriptions_per_consumer)")
    assert subscribe(module, b, "death")["event"] == "death"  # another consumer is not affected
    module.close_consumer(a)
    module.close_consumer(b)


def test_a_consumers_queued_bytes_are_bounded(module):
    # Byte budget: 300 events of about 60 KB into a max_queue 1024 subscription nobody drains: the 16 MiB byte budget, not
    # max_queue, decides how many stay, and the rest are counted in dropped on the first delivered frame.
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["bytes"]}, max_queue=1024)
    data = chan("bytes", "z" * 60000)
    frame = len(json.dumps(data, separators=(",", ":")).encode()) + 96  # core's kEventFrameOverheadBytes
    for _ in range(300):
        push(module, "channel", data)
    kept = g.MAX_QUEUED_EVENT_BYTES_PER_CONSUMER // frame
    frames = drain(module, c, kept)
    assert len(frames) == kept and json.loads(module.next_events(c, 0.1)) == []
    assert frames[0]["dropped"] == 300 - kept and frames[0]["seq"] == 300 - kept + 1 and frames[-1]["seq"] == 300
    module.close_consumer(c)


# Core's normalise() hands a form value to form_ref_from_json, whose texts are bare (no "filter value for" prefix).
@pytest.mark.parametrize("ref, message", [
    ({"kind": "form", "form_id": 2**32}, "form_id must be an unsigned 32-bit integer"),
    ({"kind": "form", "form_id": 20, "form_key": "not a key"}, "form_key must look like Plugin.esp|0x0012AB"),
    ({"kind": "form", "form_id": 21, "form_key": "Skyrim.esm|0x000014"}, "form_id and form_key do not name the same form"),
    ({"kind": "form"}, "a form reference needs form_id or form_key"),
    ({"kind": "alias", "form_id": 20}, "expected a form reference"),
    ([1, 2], "filter value for death.victim must be a form reference, got an array of 2 items"),
    ("x" * 70, "filter value for death.victim must be a form reference, got \"" + "x" * 64 + "...\""),
], ids=["id-over-u32", "bad-key", "id-key-mismatch", "no-id-no-key", "not-kind-form", "array", "long-string"])
def test_malformed_form_references_in_a_filter_are_refused_like_core(module, ref, message):
    c = module.open_consumer()
    assert err(rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "death", "filter": {"victim": [ref]}}, c)) == ("bad_request", message)
    module.close_consumer(c)


@pytest.mark.parametrize("event, message", [
    (5, "'event' must be a string naming an event"),
    ("x" * 100, "unknown event '" + "x" * 64 + "...'"),
    ("a" + "\u00e9" * 40, "unknown event 'a" + "\u00e9" * 31 + "...'"),  # clipped at 64 bytes, never inside a UTF-8 sequence
], ids=["not-a-string", "clipped", "clipped-utf8"])
def test_bad_event_names_get_cores_text(module, event, message):
    c = module.open_consumer()
    assert err(rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": event}, c)) == ("bad_request", message)
    module.close_consumer(c)


@pytest.mark.parametrize("event, data, message", [
    ("no_such_event", "{}", "unknown event 'no_such_event'"),
    ("x" * 80, "{}", "unknown event '" + "x" * 64 + "...'"),
    ("death", "not json", "data_json must be a JSON object"),
    ("death", "[1]", "data_json must be a JSON object"),
    ("death", '{"a":' * 70 + "1" + "}" * 70, "data_json is nested too deeply"),
    ("death", '{"a":' * 70, "data_json is nested too deeply"),  # the depth is checked on the text, before parsing
], ids=["unknown", "unknown-clipped", "not-json", "not-object", "too-deep", "too-deep-unparsed"])
def test_fake_push_event_refuses_what_the_real_module_refuses(module, event, data, message):
    with pytest.raises(ValueError) as info:
        module._fake_push_event(event, data)
    assert str(info.value) == message


def test_next_events_refuses_a_negative_or_nan_timeout_with_the_real_text(module):
    c = module.open_consumer()
    for bad in (-1, -0.001, float("nan")):
        with pytest.raises(ValueError) as info:
            module.next_events(c, bad)
        assert str(info.value) == "timeout must be a number of seconds, 0 or more"
    module.close_consumer(c)


def test_next_events_accepts_a_huge_timeout_and_returns_a_queued_event_at_once(module):
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["huge"]})
    push(module, "channel", chan("huge"))
    t0 = time.monotonic()
    frames = json.loads(module.next_events(c, 1e10))  # clamped (a week, then an hour per wait), not an overflow
    assert time.monotonic() - t0 < 1.0 and [f["data"]["channel"] for f in frames] == ["huge"]
    module.close_consumer(c)


def _blocked_next(m, consumer, timeout=10.0):
    """next_events on a thread; returns (thread, box) where box gets (text, seconds taken)."""
    box = []

    def run():
        t0 = time.monotonic()
        text = m.next_events(consumer, timeout)
        box.append((text, time.monotonic() - t0))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.2)  # let it block
    assert t.is_alive() and not box
    return t, box


def test_a_blocked_next_events_returns_empty_when_its_consumer_is_closed(module):
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["closing"]})
    t, box = _blocked_next(module, c)
    module.close_consumer(c)
    t.join(3.0)
    assert not t.is_alive() and box[0][0] == "[]" and box[0][1] < 3.0


def test_a_blocked_next_events_returns_when_a_matching_event_is_pushed(module):
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["wake"]})
    t, box = _blocked_next(module, c)
    push(module, "channel", chan("other"))  # does not match: it keeps waiting
    time.sleep(0.2)
    assert t.is_alive() and not box
    push(module, "channel", chan("wake", "now"))
    t.join(3.0)
    assert not t.is_alive() and box[0][1] < 3.0
    assert [f["data"]["payload"] for f in json.loads(box[0][0])] == ["now"]
    module.close_consumer(c)


def test_a_bool_int_filter_value_is_refused_with_cores_text(module):
    c = module.open_consumer()
    resp = rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "quest_stage", "filter": {"stage": [True]}}, c)
    assert err(resp) == ("bad_request", "filter value for quest_stage.stage must be an integer (a Papyrus Int), got true")
    module.close_consumer(c)


def test_int_filters_match_only_papyrus_ints(module):
    c = module.open_consumer()
    subscribe(module, c, "quest_stage", {"stage": [1, 10, -2**31]})
    quest = {"kind": "form", "form_id": 7}
    # true is not 1, 10.0 is not 10, and out-of-range ints never wrap onto -2**31 or 1.
    for stage in (True, 10.0, 2**31, 2**32 + 1, -2**31 - 1, 2**64 - 2**31):
        push(module, "quest_stage", {"quest": quest, "stage": stage})
    push(module, "quest_stage", {"quest": quest, "stage": 10})
    frames = drain(module, c, 1)
    assert [f["data"]["stage"] for f in frames] == [10] and frames[0]["seq"] == 1
    assert json.loads(module.next_events(c, 0.2)) == []
    module.close_consumer(c)


def test_form_filters_match_only_u32_integer_form_ids(module):
    c = module.open_consumer()
    subscribe(module, c, "death", {"victim": [{"kind": "form", "form_id": 0}, {"kind": "form", "form_id": 7}]})
    for fid in (2**32, 2**32 + 7, 7.0, 0.0, -1, True, "7"):
        push(module, "death", {"victim": {"kind": "form", "form_id": fid}, "killer": None})
    push(module, "death", {"victim": {"kind": "form", "form_id": 7}, "killer": None})
    frames = drain(module, c, 1)
    assert [f["data"]["victim"]["form_id"] for f in frames] == [7] and frames[0]["seq"] == 1
    assert json.loads(module.next_events(c, 0.2)) == []
    module.close_consumer(c)


def test_an_event_bigger_than_the_whole_byte_budget_drops_itself(module):
    c = module.open_consumer()
    subscribe(module, c, "channel", {"channel": ["big"]})
    push(module, "channel", chan("big", "first"))
    push(module, "channel", chan("big", "z" * (g.MAX_QUEUED_EVENT_BYTES_PER_CONSUMER + 1)))  # its oldest go, then it
    push(module, "channel", chan("big", "last"))
    frames = drain(module, c, 1)
    assert [f["data"]["payload"] for f in frames] == ["last"]
    assert frames[0]["seq"] == 3 and frames[0]["dropped"] == 2  # the seq gap equals dropped
    assert json.loads(module.next_events(c, 0.1)) == []
    module.close_consumer(c)


def test_the_global_subscription_cap_has_cores_text(module):
    # Consumers of at most max_subscriptions_per_consumer each, until the hub-wide cap answers busy. The real
    # module's hub is process-wide, so subscriptions another test leaked would make it come earlier, never later.
    consumers, made, refused = [], 0, None
    try:
        while refused is None and made <= g.MAX_SUBSCRIPTIONS:
            c = module.open_consumer()
            consumers.append(c)
            for i in range(g.MAX_SUBSCRIPTIONS_PER_CONSUMER):
                resp = rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": [f"g{i}"]}}, c)
                if not resp["ok"]:
                    refused = err(resp)
                    break
                made += 1
        assert refused == ("busy", f"too many event subscriptions ({g.MAX_SUBSCRIPTIONS})")
        assert made <= g.MAX_SUBSCRIPTIONS
        if isinstance(module, FakeSkython):
            assert made == g.MAX_SUBSCRIPTIONS
    finally:
        for c in consumers:
            module.close_consumer(c)
    c = module.open_consumer()
    assert subscribe(module, c, "death")["event"] == "death"  # closing them frees the cap
    module.close_consumer(c)


def test_event_field_types_are_the_protocol_names():
    # The generated Python table uses protocol type names (int, not C++'s integer): the fake's filter code keys on them.
    assert {t for spec in g.EVENTS.values() for _n, t, _null in spec["fields"]} == {"form", "int", "float", "bool", "string"}


# -- fake only: the test-only counters ------------------------------------------------------------------------
def test_counters_follow_consumers_and_subscriptions(fake):
    assert (fake.consumers(), fake.subscriptions()) == (0, 0)
    a, b = fake.open_consumer(), fake.open_consumer()
    for c, n in ((a, 3), (b, 2)):
        for i in range(n):
            subscribe(fake, c, "channel", {"channel": [f"c{i}"]})
    assert (fake.consumers(), fake.subscriptions(), fake.subscriptions(a)) == (2, 5, 3)
    fake.close_consumer(a)
    assert (fake.consumers(), fake.subscriptions(), fake.subscriptions(a)) == (1, 2, 0)
    fake.close()
    assert (fake.consumers(), fake.subscriptions()) == (0, 0)


def test_without_a_hub_events_are_unsupported_and_open_consumer_raises(fake):
    fake.hub = None  # an older plugin build with no ModuleHost.events
    with pytest.raises(RuntimeError, match="no event hub"):
        fake.open_consumer()
    assert fake.next_events(1, 1.0) == "[]" and (fake.consumers(), fake.subscriptions()) == (0, 0)
    fake.close_consumer(1)  # nothing to close
    for op, args in ((g.OP_EVENTS_SUBSCRIBE, {"event": "death"}), (g.OP_EVENTS_UNSUBSCRIBE, {"sub": 1})):
        assert err(rpc(fake, op, args)) == ("unsupported", "this build has no event hub")
    assert rpc(fake, g.OP_GAME_STATE, {})["ok"] is True  # everything else still works


def test_totals_count_drops_and_suppressions(fake):
    c = fake.open_consumer()
    subscribe(fake, c, "death", max_queue=1)
    for i in range(3):
        push(fake, "death", {"victim": {"kind": "form", "form_id": i + 1}, "killer": None})
    fake._fake_set_not_ready("loading")
    push(fake, "death", {"victim": {"kind": "form", "form_id": 9}, "killer": None})
    fake._fake_set_not_ready(None)
    assert (fake.hub.dropped_total, fake.hub.suppressed_total) == (2, 1)
