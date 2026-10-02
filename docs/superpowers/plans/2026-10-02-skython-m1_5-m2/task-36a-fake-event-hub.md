# Task 36a: Python fake event hub: `FakeHub`, consumers, `next_events`, fake `game.save`/`game.load`

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Contracts** (Protocol additions: the consumer beside the request, filters, the loading rule, the save/load error texts; C++ core: `EventHub` rules; `_skython` module additions; Python: `fake_skython.py`) and the Part D brief for Task 36a; they apply to this task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 35 (the real Linux `_skython` with `open_consumer`, `close_consumer`, `next_events`, `submit`/`call` with a consumer, `_fake_push_event`, `FakeBackend` `game.save`/`game.load`) and Task 33 (generated names, `events.json`, `responses.json` 64-65, the fake's parse-level checks in `_op_problem`). Linux only: no game, no Gate 0, no ESP.

**What this task builds.** The Python test fake (`python/tests/fake_skython.py`) gains the Task 35 module API so every Python test of Tasks 36, 37 and 43 can run without the C++ build: `FakeHub`, a Python copy of Task 34's `EventHub` (consumers, a bounded drop-oldest queue per subscription, `seq` from 1 per subscription, the epoch stamp, equality-set filters with core's exact error texts, the loading rule), `submit`/`call` with a consumer beside the request text, `open_consumer`, `close_consumer`, `next_events`, `_fake_push_event`, the fake `game.save`/`game.load` handlers (texts exactly as `responses.json` 64-65), and two test-only counters. A new test file runs every behaviour against the fake **and** against the real Linux `_skython` when `SKYTHON_REAL_MODULE_DIR` names its build folder, so the fake cannot drift from core. Task 33's fake test in `test_fixtures.py` now passes a consumer for the `events.*` requests (consumer 0 is `bad_request` "no event consumer", as in core).

**Error texts the fake copies from core** (Task 34 `parse_event_filter` / `EventHub`, Task 35 `Dispatcher`, Task 35 `FakeBackend`; the substrings in `events.json`'s `invalid_filters` are a subset):

| case | code | message |
|---|---|---|
| consumer 0 (Dispatcher) | `bad_request` | `no event consumer` |
| subscribe on an unknown or closed consumer (hub) | `bad_request` | `no event consumer <n> (never opened, or closed)` |
| unsubscribe of an unknown sub, another consumer's sub, or on a closed consumer (hub) | `not_found` | `no subscription <sub> for this consumer` |
| filter not an object | `bad_request` | `filter must be an object mapping filter fields to lists of values` |
| field not in `filter_fields` | `bad_request` | `event <e> has no filter field '<f>' (filter fields: <a, b>)` or `... (it cannot be filtered)` |
| empty or non-array values | `bad_request` | `filter field '<f>' needs a non-empty array of values` |
| more than 128 values | `bad_request` | `filter field '<f>' has <n> values; at most 128` |
| form field value not an object / no `form_id` | `bad_request` | `filter value for <e>.<f> must be a form reference, got <v>` / `... has no form_id: resolve the form first` |
| int field value | `bad_request` | `filter value for <e>.<f> must be an integer (a Papyrus Int), got <v>` |
| string field value | `bad_request` | `filter value for <e>.<f> must be a string, got <v>` |
| `filter_required_any_of` not named | `bad_request` | `<e> needs a filter on <a or b>` / `<a, b or c>` |
| a consumer's 257th subscription (hub, R61) | `busy` | `consumer <n> has 256 subscriptions (max_subscriptions_per_consumer)` |
| too many subscriptions of all consumers | `busy` | `too many event subscriptions (1024)` |
| form field value whose `form_id` is not a u32, or whose `form_key` is malformed or names another form | `bad_request` | `filter value for <e>.<f>: form_id must be an unsigned 32-bit integer` / `...: form_key must look like Plugin.esp\|0x0012AB` / `...: form_id and form_key do not name the same form` |
| `game.save` of an existing name | `bad_request` | `a save named <name> already exists`, `detail.reason = "save_exists"` |
| `game.load` of an unknown name | `not_found` | `no save named <name>` |
| `game.load` while starting / loading (FakeBackend) | `not_ready` | `the game is still starting` / `a game is loading`, detail `reason`, `paused`, `menus` |
| `_fake_push_event` of an unknown event, invalid JSON, a non-object or data nested deeper than 64 | `ValueError` (raised, not a response) | |

**Byte budget (R61), copied from core:** each queued event counts its data's compact JSON length (`json.dumps(data, separators=(",", ":"), ensure_ascii=False)`, UTF-8 bytes, the same length as core's `dump_json`) plus 96 (core's `kEventFrameOverheadBytes`); when a push takes the consumer over `max_queued_bytes_per_consumer`, the oldest events of the pushed subscription are dropped (counted in `dropped`) until it is under again, the new event itself last.

**No hub (Review A#1):** a test may set `fake.hub = None`; the fake then behaves like a build whose `ModuleHost.events` is null (the game build before Task 39): `open_consumer` raises `RuntimeError("_skython: this build has no event hub")`, `close_consumer` does nothing, `next_events` returns `"[]"`, `events.*` requests answer `unsupported` `this build has no event hub` (before the consumer-0 check, as the Dispatcher does), `_fake_push_event` raises `RuntimeError`, `consumers()` and `subscriptions()` return 0.

**Files:**
- Modify: `python/tests/fake_skython.py` (appended: `FakeHub` and its helpers, the module API methods, the `game.save`/`game.load` handlers; `submit`/`call` take a consumer; never recreated)
- Modify: `python/tests/test_fixtures.py` (Task 33's `test_the_fake_accepts_valid_requests_of_the_new_ops` passes a consumer)
- Create: `python/tests/test_fake_hub.py`

**Interfaces:**
- Consumes: Task 35 `_skython` (index Contracts): `open_consumer() -> int`, `close_consumer(consumer) -> None`, `next_events(consumer, timeout) -> str`, `call(request, timeout, consumer=0) -> str`, `submit(request, consumer=0) -> int`, `_fake_push_event(event, data_json)`, `_fake_set_not_ready("loading")` drives the hub's loading rule, the hub's epoch is the backend's epoch, `state() -> str`; Task 33 generated `EVENTS` (field types are the protocol names `form`, `int`, `float`, `bool`, `string`), `DEFAULT_EVENT_QUEUE`, `MAX_EVENT_QUEUE`, `MAX_SUBSCRIPTIONS`, `MAX_SUBSCRIPTIONS_PER_CONSUMER`, `MAX_QUEUED_EVENT_BYTES_PER_CONSUMER`, `MAX_JSON_DEPTH`, `MAX_FILTER_VALUES`, `MAX_EVENTS_PER_NEXT`, `OP_EVENTS_SUBSCRIBE`, `OP_EVENTS_UNSUBSCRIBE`, `OP_GAME_SAVE`, `OP_GAME_LOAD`, `CODE_*`; `protocol/v1/fixtures/events.json` (`invalid_filters`); v1 `fake_skython.py` (`FakeSkython`, `FakeRemoteError`, `_response`, `_finish_now`, `_fake_bump_loads`, `epoch`, `_not_ready`, `fake_handler`), `tests/support.load_fixture`, the `fake` fixture.
- Produces (index Contracts, `python/tests/fake_skython.py`): `FakeHub(epoch, loading, max_queued_bytes_per_consumer=MAX_QUEUED_EVENT_BYTES_PER_CONSUMER)` (fields `dropped_total`, `suppressed_total`, `max_queued_bytes_per_consumer`; methods `open_consumer`, `close_consumer`, `close_all`, `subscribe(consumer, args) -> dict`, `unsubscribe(consumer, args)`, `push(event, data)`, `next(consumer, timeout, max_events=MAX_EVENTS_PER_NEXT) -> list[dict]`, `consumers()`, `subscriptions(consumer=None)`), and on `FakeSkython`: `hub` (a test may set it to `None`: no event hub, as above), `saves` (set of names), `submit(request, consumer=0)`, `call(request, timeout, consumer=0)`, `open_consumer()`, `close_consumer(consumer)`, `next_events(consumer, timeout)`, `_fake_push_event(event, data_json)`, the fake `game.save` / `game.load` handlers (a load bumps the loads counter, hence the epoch), test-only `consumers() -> int` and `subscriptions(consumer=None) -> int`; `close()` also closes every consumer. Tasks 36, 37, 38 and 43 use exactly these.

- [ ] **Step 1: Write the failing tests**

`python/tests/test_fake_hub.py`:
```python
"""The Python fake's event hub and save/load (Task 36a). Every behaviour test runs against the fake and against the
REAL Linux _skython (C++ EventHub + Dispatcher + FakeBackend, Tasks 34-35) when SKYTHON_REAL_MODULE_DIR names its
build folder, so the fake cannot drift from core. The fake-only tests at the end cover the test-only counters."""
import importlib
import itertools
import json
import os
import sys
import time

import pytest

from support import load_fixture
from skython import _protocol_generated as g

MODULE_DIR = os.environ.get("SKYTHON_REAL_MODULE_DIR")
EVENTS_FX = load_fixture("events.json")
_ids = itertools.count(1000)


@pytest.fixture(scope="module")
def real_module(tmp_path_factory):
    """The real module, imported for this file only (the same pattern as test_real_module.py)."""
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
def module(request, fake):
    if request.param == "fake":
        return fake
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
    # R61: 300 events of about 60 KB into a max_queue 1024 subscription nobody drains: the 16 MiB byte budget, not
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


@pytest.mark.parametrize("ref", [{"kind": "form", "form_id": 2**32}, {"kind": "form", "form_id": 20, "form_key": "not a key"},
                                 {"kind": "form", "form_id": 21, "form_key": "Skyrim.esm|0x000014"}])
def test_malformed_form_references_in_a_filter_are_refused_like_core(module, ref):
    c = module.open_consumer()
    code, _ = err(rpc(module, g.OP_EVENTS_SUBSCRIBE, {"event": "death", "filter": {"victim": [ref]}}, c))
    assert code == "bad_request"
    module.close_consumer(c)


@pytest.mark.parametrize("event, data", [("no_such_event", "{}"), ("death", "not json"), ("death", "[1]"),
                                         ("death", '{"a":' * 70 + "1" + "}" * 70)])
def test_fake_push_event_refuses_what_the_real_module_refuses(module, event, data):
    with pytest.raises(ValueError):
        module._fake_push_event(event, data)


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
    fake.hub = None  # the game build before Task 39 sets ModuleHost.events
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
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_fake_hub.py 2>&1 | tail -3`
Expected: FAIL: `TypeError: call() takes 3 positional arguments but 4 were given` in the `fake` cases (the v1 fake has no consumer argument), `AttributeError: 'FakeSkython' object has no attribute 'open_consumer'` in others; the `real` cases skip (`SKYTHON_REAL_MODULE_DIR not set`).

- [ ] **Step 3: The fake hub in `fake_skython.py` (appended; the file is not recreated)**

Add `import collections` and `import string` to the imports (`re` is there since Task 33), and `from skython.refs import parse_form_key` and `from skython.values import Form` beside the existing `skython` imports. Append to the module docstring:
```
Events (Task 36a): `hub` is a FakeHub, a Python copy of core's EventHub (Task 34). events.subscribe and
events.unsubscribe are answered at submit time through the hub, with the consumer passed beside the request text
(0 -> bad_request "no event consumer", as the Dispatcher; an unknown or closed consumer -> the hub's own texts);
open_consumer, close_consumer, next_events and _fake_push_event are the Task 35 module API; consumers() and
subscriptions() are test-only counters. game.save and game.load follow FakeBackend (Task 35): save names are kept in
`saves`; a load bumps the loads counter (so the epoch); the error texts are responses.json 64-65.
```
Append after `class _Entry` (before `def _response`):
```python
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


def _depth(v) -> int:
    if isinstance(v, dict):
        return 1 + max((_depth(x) for x in v.values()), default=0)
    if isinstance(v, list):
        return 1 + max((_depth(x) for x in v), default=0)
    return 0


def _bad(message: str) -> FakeRemoteError:
    return FakeRemoteError(_g.CODE_BAD_REQUEST, message)


def _join(names, last_sep: str) -> str:
    """core's join(): "a", "a or b", "a, b or c"."""
    names = list(names)
    return "".join(n if i == 0 else (last_sep if i + 1 == len(names) else ", ") + n for i, n in enumerate(names))


def _normalise(event: str, field: str, ftype: str, v):
    """core's normalise(): the key a filter value is compared by, or bad_request with core's text. ftype is the
    protocol type name from EVENTS (form, int, string; float and bool fields are never filter fields)."""
    where = f"filter value for {event}.{field}"
    if ftype == "form":
        if not isinstance(v, dict) or v.get("kind") != "form":
            raise _bad(f"{where} must be a form reference, got {json.dumps(v)}")
        fid, key = v.get("form_id"), v.get("form_key")
        if fid is not None and (type(fid) is not int or not 0 <= fid <= 0xFFFFFFFF):  # core's form_ref_from_json
            raise _bad(f"{where}: form_id must be an unsigned 32-bit integer")
        if key is not None:
            try:
                parse_form_key(key)
            except (TypeError, ValueError):
                raise _bad(f"{where}: form_key must look like Plugin.esp|0x0012AB") from None
            if fid is not None:
                try:
                    Form(form_id=fid, form_key=key)
                except ValueError:
                    raise _bad(f"{where}: form_id and form_key do not name the same form") from None
        if fid is None:
            raise _bad(f"{where} has no form_id: resolve the form first")
        return fid
    if ftype == "int":
        if type(v) is not int or not _INT_MIN <= v <= _INT_MAX:
            raise _bad(f"{where} must be an integer (a Papyrus Int), got {json.dumps(v)}")
        return v
    if ftype == "string":
        if not isinstance(v, str):
            raise _bad(f"{where} must be a string, got {json.dumps(v)}")
        return v.translate(_ASCII_LOWER)
    raise _bad(f"{where}: this field cannot be filtered")


def _event_key(ftype: str, value):
    """The key an event's field value is compared by; None never matches."""
    if value is None:
        return None
    if ftype == "form":
        return value.get("form_id") if isinstance(value, dict) else None
    if ftype == "string":
        return value.translate(_ASCII_LOWER) if isinstance(value, str) else None
    return value if type(value) is int else None


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
                raise _bad(f"event {event} has no filter field '{name[:64]}'{tail}")
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
            raise _bad(f"unknown event '{str(event)[:64]}'")
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
```

In `fake_handler`, before the final `raise FakeRemoteError(_g.CODE_UNSUPPORTED, ...)`:
```python
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
```
(`fake` is the v1 handler's local `fake = ctx.fake`.)

In `FakeSkython.__init__`, after its last two lines `self._entries: dict[int, _Entry] = {}` and `self._cond = threading.Condition()` (not the same-looking line in `FakeHub.__init__` above, which comes first in the file):
```python
        self.saves: set[str] = set()
        self.hub = FakeHub(lambda: self.epoch, lambda: self._not_ready == "loading")
```

Change `submit`'s signature to `def submit(self, request: str, consumer: int = 0) -> int:` and insert, right after `op, args = obj["op"], obj["args"]`:
```python
            if op in _EVENT_OPS:  # answered by the hub at once: no backend call, no deadline (Task 35's Dispatcher)
                self._finish_now(ticket, req_id, self._hub_answer(op, args, consumer, req_id))
                return ticket
```
Change `call`'s signature to `def call(self, request: str, timeout: float, consumer: int = 0) -> str:` and its first line to `ticket = self.submit(request, consumer)`. In `close()`, add `if self.hub is not None: self.hub.close_all()` as the last line (two lines, as usual). (`_op_problem` already checks the 1.1 argument types, including save names and `queue_size`, since Task 33; the handlers above rely on that.) Add these methods after `in_flight`:
```python
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
        """As the real module: an unknown event, invalid JSON, a non-object or data nested too deeply is ValueError."""
        if self.hub is None:
            raise RuntimeError("_skython: " + _NO_HUB)
        if event not in _g.EVENTS:
            raise ValueError(f"unknown event {event[:64]!r}")
        data = json.loads(data_json)  # json.JSONDecodeError is a ValueError
        if not isinstance(data, dict):
            raise ValueError("event data must be a JSON object")
        if _depth(data) > _g.MAX_JSON_DEPTH:
            raise ValueError("data_json is nested too deeply")
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
```
(`submit` calls `_hub_answer` with `self._cond` held; the hub takes only its own lock and never `self._cond`, so the lock order is always `_cond` then the hub's.)

- [ ] **Step 4: Task 33's fake test passes a consumer**

`python/tests/test_fixtures.py`: Task 33's fake test sent the `events.*` fixtures without a consumer; they now need one, as in core. Replace its body:
```python
@pytest.mark.parametrize("req", [r for r in REQUESTS["valid"] if r["op"] in NEW_OPS], ids=lambda r: r["op"])
def test_the_fake_accepts_valid_requests_of_the_new_ops(fake, req):
    consumer = fake.open_consumer()  # events.* travel with a consumer beside the text (Task 36a); other ops ignore it
    resp = json.loads(fake.call(json.dumps(req), 5.0, consumer))
    assert resp["ok"] is True or resp["error"]["code"] != "bad_request", resp
    noconsumer = json.loads(fake.call(json.dumps(req), 5.0))
    if req["op"] in ("events.subscribe", "events.unsubscribe"):
        assert noconsumer["error"]["code"] == "bad_request" and noconsumer["error"]["message"] == "no event consumer"
```

- [ ] **Step 5: Run the new tests, on the fake and against the real module**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_fake_hub.py tests/test_fixtures.py 2>&1 | tail -2`
Expected: `test_fake_hub.py` has 10 behaviour tests, 14 `invalid_filters` cases, 3 malformed-form-reference cases and 4 `_fake_push_event` cases (31 parametrized on `module`), plus 1 plain test and 3 fake-only tests: `35 passed, 31 skipped` from it (the skips are the `real` parameters), plus `test_fixtures.py`'s count, no failures.

Against the real module (Task 35 built it for 3.14):
Run: `cmake --build pyglue/build -j 2>&1 | tail -1 && cd python && SKYTHON_REAL_MODULE_DIR="$(cd ../pyglue/build && pwd)" uv run --managed-python --python 3.14 pytest -q tests/test_fake_hub.py 2>&1 | tail -2`
Expected: `66 passed`, no skips (the byte-budget case pushes about 18 MB through each backend and takes a second or two). A case that passes on the fake and fails on the real module means the fake drifted from core: fix the fake, never the assertion (and never core: core is Tasks 34-35's).

- [ ] **Step 6: The whole suite on both Pythons**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected twice: Task 35's total plus 35 passed, plus Task 35's skips plus 31; no failures (the v1 tests that build `FakeSkython` or call `call(text, timeout)` are unchanged: the consumer argument defaults to 0).

Run: `python3 tools/gen_protocol.py --check`
Expected: the generated files are up to date (this task edits none of them).

- [ ] **Step 7: Commit**

```bash
git add python/tests/fake_skython.py python/tests/test_fake_hub.py python/tests/test_fixtures.py
git commit -m "test(python): fake event hub, consumers and save/load in the Python fake, checked against core"
```
