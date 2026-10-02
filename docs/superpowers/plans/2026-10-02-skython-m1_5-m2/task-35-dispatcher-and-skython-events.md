# Task 35: Dispatcher routing, fake save/load, `_skython` consumers and `next_events`

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Contracts** ("Protocol additions", "C++ core" including the `dispatcher.h` changes, "`_skython` module additions") and the Part D brief for Task 35. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 34 (`spy::EventHub`, `ConsumerId`, `SubId`) and Task 33 (the four ops in the generated table, the golden responses 62-65 in `responses.json`, `is_valid_save_name`). Linux only; no game.

**What this task does.**
- **Dispatcher.** `DispatcherOptions` gains `EventHub* events = nullptr;` (appended last, so the positional `DispatcherOptions{timeout, cap}` in `pyglue/linux/fake_module.cpp` and the plugin still compiles), and `Submit` gains a second parameter, `ConsumerId consumer = 0`. After parsing, `events.subscribe` and `events.unsubscribe` are answered by the hub synchronously: the ticket is done at once, there is no backend call, no deadline and no in-flight slot. The checks run in this order: no hub → `unsupported` ("this build has no event hub"); consumer 0 → `bad_request` ("no event consumer"); then the hub (`Subscribe` / `Unsubscribe`), whose `ProtocolError` becomes the error response. Readiness does not apply (both ops are `needs_game: false`). Every other op ignores `consumer`.
- **FakeBackend.** `game.save {name}` records the name in a set; an existing name is `bad_request` with `detail.reason = "save_exists"` and message `a save named <name> already exists`; otherwise `{"name": name}`. `game.load {save}` checks `NotReadyReason(false)` itself (the Dispatcher skips readiness for this `needs_game: false` op): `"starting"` or `"loading"` → `not_ready` (detail `reason`, `paused`, `menus`; message as the Dispatcher's), a `"no_game"` main menu is allowed; an unknown name → `not_found` with message `no save named <name>`; else `BumpLoads()` then `{"save": name, "started": true}`. Both answer inside `Run`, like `game.state`. Controls `AddFakeSave(std::string)` and `FakeSaves()`. Its texts equal the golden responses 62-65.
- **`_skython`.** `submit(request, consumer=0)` and `call(request, timeout, consumer=0)` pass the consumer beside the text; `open_consumer()`, `close_consumer(consumer)` (idempotent), `next_events(consumer, timeout)` (a JSON list of event objects, `"[]"` on timeout; the GIL is released while it waits; an unknown or closed consumer returns `"[]"` at once). `ModuleHost` gains `EventHub* events = nullptr;` (appended last). With no hub installed, `open_consumer` raises `RuntimeError`, `close_consumer` does nothing and `next_events` returns `"[]"`. (In the game build `ModuleHost.events` stays null until Task 39 fills it; Task 37's bridge catches that `RuntimeError` and serves the connection with consumer 0, so `events.*` answer `unsupported` there, and Task 36's `InProcessTransport.events()` turns it into `errors.Unsupported`. Review A#1.)
- **Fake build.** `pyglue/linux/fake_module.cpp` builds one immortal `EventHub` whose `epoch` is the fake backend's epoch (`LoadsStarted()`, what `BaseState()["epoch"]` reports) and whose `loading` is true while `_fake_set_not_ready("loading")` is in force; installs it in both `DispatcherOptions::events` and `ModuleHost::events` (the `ModuleHost` is now built with named fields, so later fields never shift positions); and adds `_fake_push_event(event, data_json)`, which calls `EventHub::Push` (an unknown event name, invalid JSON, a non-object or too-deep data → `ValueError`).

Review Focus lines this task pins: **2** (a consumer nobody drains fills to `max_queue`, drops are counted and reported, another consumer of the same events is unaffected) and **3** (with the fake loading, `cell_attach` is suppressed and `menu_open` delivered; the next `cell_attach` carries `suppressed`).

**Files:**
- Modify: `core/include/spy/dispatcher.h`, `core/src/dispatcher.cpp`
- Modify: `core/include/spy/fake_backend.h`, `core/src/fake_backend.cpp`
- Modify: `pyglue/include/spy/pymodule.h`, `pyglue/src/pymodule.cpp`, `pyglue/linux/fake_module.cpp`
- Test: `core/tests/test_dispatcher.cpp` (appended), `core/tests/test_fake_backend.cpp` (appended; the fixture-replay expectation updated)
- Create: `pyglue/tests/test_events.py`

**Interfaces:**
- Consumes: `spy::EventHub` with `OpenConsumer`, `CloseConsumer`, `Subscribe`, `Unsubscribe`, `Push`, `Next`, `Subscriptions`; `spy::ConsumerId`, `spy::SubId`, `spy::EventHubOptions` (Task 34); `spy::ops::kEventsSubscribe`, `kEventsUnsubscribe`, `kGameSave`, `kGameLoad`, `spy::find_event`, `spy::kMaxEventsPerNext`, `spy::kMaxJsonDepth` (Task 33, generated); `responses.json` ids 62-65 (Task 33); `spy::nesting_within`, `spy::clip_text`, `Dispatcher`, `FakeBackend` (v1).
- Produces (exactly the index Contracts):
  - `struct DispatcherOptions` gains `EventHub* events = nullptr;` (null → `events.*` answer `unsupported`; consumer 0 → `bad_request` `no event consumer`; an unknown or closed consumer → the hub's `bad_request` starting `no event consumer`; `filter` absent or `null` = no filter); `Ticket Dispatcher::Submit(std::string_view request_text, ConsumerId consumer = 0);`
  - `FakeBackend`: `game.save` / `game.load` as above (errors exactly `bad_request` `a save named <name> already exists` with `detail.reason` `save_exists`, and `not_found` `no save named <name>`: `responses.json` 64-65); `void AddFakeSave(std::string name);`, `std::vector<std::string> FakeSaves() const;` (sorted).
  - `ModuleHost` gains `EventHub* events = nullptr;` (appended last, with its default). `pyglue/linux/fake_module.cpp` builds `ModuleHost` with named fields (designated initializers); in the game build `plugin/src/PythonHost.cpp` fills `events` (Task 39; it already sets fields by name on a `ModuleHost host;` local). Later fields, each appended with a default: `console_echo` (Task 43), `probe_sink_cycles` (Task 44).
  - `_skython.submit(request: str, consumer: int = 0) -> int`, `_skython.call(request: str, timeout: float, consumer: int = 0) -> str`, `_skython.open_consumer() -> int`, `_skython.close_consumer(consumer: int) -> None`, `_skython.next_events(consumer: int, timeout: float) -> str`; fake build only: `_skython._fake_push_event(event: str, data_json: str) -> None`.

---

- [ ] **Step 1: Write the failing C++ tests**

Append to `core/tests/test_dispatcher.cpp`:
```cpp
// ---- Task 35: events.* answered by the EventHub, the consumer beside the request text ----------------------------
namespace {
std::string sub_text(std::uint64_t id, json args)
{
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "events.subscribe"}, {"args", std::move(args)}});
}
std::string unsub_text(std::uint64_t id, spy::SubId sub)
{
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "events.unsubscribe"}, {"args", {{"sub", sub}}}});
}
spy::DispatcherOptions with_hub(spy::EventHub& hub)
{
    spy::DispatcherOptions o;
    o.events = &hub;
    return o;
}
}

TEST_CASE("events.subscribe and events.unsubscribe are answered by the hub at once, without the backend or an in-flight slot")
{
    ManualBackend b;
    spy::EventHub hub({});
    spy::Dispatcher d(b, with_hub(hub));
    const auto c = hub.OpenConsumer();
    const auto t = d.Submit(sub_text(1, {{"event", "death"}, {"max_queue", 8}}), c);
    CHECK(d.InFlight() == 0);
    auto r = wait_json(d, t, 0ms);  // already done: no wait
    CHECK(r == json::parse(R"({"type":"response","id":1,"ok":true,"result":{"sub":1,"event":"death","max_queue":8}})"));
    CHECK(hub.Subscriptions() == 1);
    r = wait_json(d, d.Submit(unsub_text(2, 1), c), 0ms);
    CHECK(r == json::parse(R"({"type":"response","id":2,"ok":true,"result":null})"));
    CHECK(hub.Subscriptions() == 0);
    CHECK(b.count() == 0);
}

TEST_CASE("events.* without a consumer is bad_request, with no hub unsupported, and readiness does not apply")
{
    ManualBackend b;
    b.not_ready = "loading";  // events.* are answered with no game
    spy::EventHub hub({});
    spy::Dispatcher d(b, with_hub(hub));
    auto r = wait_json(d, d.Submit(sub_text(3, {{"event", "death"}})));
    CHECK(code_of(r) == "bad_request");
    CHECK(r["error"]["message"] == "no event consumer");
    CHECK(r["id"] == 3);
    CHECK(code_of(wait_json(d, d.Submit(unsub_text(4, 1)))) == "bad_request");
    const auto c = hub.OpenConsumer();
    CHECK(code_of(wait_json(d, d.Submit(sub_text(5, {{"event", "death"}}), c))) == "ok");

    spy::Dispatcher plain(b, {});
    CHECK(code_of(wait_json(plain, plain.Submit(sub_text(6, {{"event", "death"}}), c))) == "unsupported");
    CHECK(code_of(wait_json(plain, plain.Submit(unsub_text(7, 1), c))) == "unsupported");
    // Consumer 0 with no hub (Task 37's bridge fallback, Review A#1): unsupported is checked before the consumer.
    CHECK(code_of(wait_json(plain, plain.Submit(sub_text(8, {{"event", "death"}})))) == "unsupported");
    CHECK(code_of(wait_json(plain, plain.Submit(unsub_text(9, 1)))) == "unsupported");
    CHECK(b.count() == 0);
}

TEST_CASE("a consumer cannot unsubscribe another consumer's subscription; hub and parse errors become error responses")
{
    ManualBackend b;
    spy::EventHub hub({});
    spy::Dispatcher d(b, with_hub(hub));
    const auto c1 = hub.OpenConsumer();
    const auto c2 = hub.OpenConsumer();
    const auto sub = wait_json(d, d.Submit(sub_text(1, {{"event", "death"}}), c1))["result"]["sub"].get<spy::SubId>();
    auto r = wait_json(d, d.Submit(unsub_text(2, sub), c2));
    CHECK(code_of(r) == "not_found");
    CHECK(hub.Subscriptions() == 1);
    CHECK(code_of(wait_json(d, d.Submit(unsub_text(3, sub), c1))) == "ok");

    r = wait_json(d, d.Submit(sub_text(4, {{"event", "hit"}}), c1));
    CHECK(code_of(r) == "bad_request");
    CHECK(r["error"]["message"] == "hit needs a filter on target or aggressor");
    r = wait_json(d, d.Submit(sub_text(5, {{"event", "no_such_event"}}), c1));  // refused by parse_request, before the hub
    CHECK(code_of(r) == "bad_request");
    CHECK(r["id"] == 5);
    hub.CloseConsumer(c1);
    CHECK(code_of(wait_json(d, d.Submit(sub_text(6, {{"event", "death"}}), c1))) == "bad_request");  // a closed consumer
}

TEST_CASE("every other op ignores the consumer")
{
    ManualBackend b;
    spy::EventHub hub({});
    spy::Dispatcher d(b, with_hub(hub));
    const auto t = d.Submit(call_text(9), 12345);
    REQUIRE(b.count() == 1);
    b.take(0).done(OpResult::Ok(4));
    CHECK(wait_json(d, t)["result"] == 4);
    b.answer_inline = true;
    CHECK(code_of(wait_json(d, d.Submit(state_text(10), 7))) == "ok");
    CHECK(hub.Subscriptions() == 0);
}
```

Append to `core/tests/test_fake_backend.cpp`:
```cpp
// ---- Task 35: game.save and game.load on the fake ----------------------------------------------------------------
namespace {
std::string op_text(std::uint64_t id, const std::string& op, json args)
{
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", op}, {"args", std::move(args)}});
}
json golden_response(std::uint64_t id)
{
    const json fx = load_fixture("responses.json");
    for (const auto& r : fx["valid"])
        if (r["type"] == "response" && r.value("id", std::uint64_t{0}) == id) return r;
    FAIL("no golden response with id " << id);
    return {};
}
json valid_request(std::uint64_t id)
{
    const json fx = load_fixture("requests.json");
    for (const auto& r : fx["valid"])
        if (r["id"] == id) return r;
    FAIL("no valid request with id " << id);
    return {};
}
}

TEST_CASE("FakeBackend game.save records the name and refuses an existing one with save_exists")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    CHECK(run(d, op_text(1, "game.save", {{"name", "skython_a"}}))["result"] == json{{"name", "skython_a"}});
    auto r = run(d, op_text(2, "game.save", {{"name", "skython_a"}}));
    CHECK(code_of(r) == "bad_request");
    CHECK(r["error"]["detail"]["reason"] == "save_exists");
    fake.AddFakeSave("skython_b");
    CHECK(fake.FakeSaves() == std::vector<std::string>{"skython_a", "skython_b"});
    CHECK(code_of(run(d, op_text(3, "game.save", {{"name", "skython_b"}}))) == "bad_request");
    fake.SetNotReady(std::string("no_game"));  // game.save needs a game: the Dispatcher refuses it first
    CHECK(code_of(run(d, op_text(4, "game.save", {{"name", "skython_c"}}))) == "not_ready");
    CHECK(fake.FakeSaves().size() == 2);
}

TEST_CASE("FakeBackend game.load: not_found, not_ready while starting or loading, allowed at the main menu, bumps loads")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    CHECK(code_of(run(d, op_text(1, "game.load", {{"save", "nope"}}))) == "not_found");
    CHECK(fake.LoadsStarted() == 0);
    fake.AddFakeSave("skython_s");
    for (const char* reason : {"starting", "loading"}) {
        CAPTURE(reason);
        fake.SetNotReady(std::string(reason));
        auto r = run(d, op_text(2, "game.load", {{"save", "skython_s"}}));
        CHECK(code_of(r) == "not_ready");
        CHECK(r["error"]["detail"]["reason"] == reason);
        CHECK(r["error"]["detail"].contains("paused"));
        CHECK(r["error"]["detail"].contains("menus"));
    }
    CHECK(fake.LoadsStarted() == 0);
    fake.SetNotReady(std::string("no_game"));  // the main menu: loading from there works
    CHECK(run(d, op_text(3, "game.load", {{"save", "skython_s"}}))["result"] == json{{"save", "skython_s"}, {"started", true}});
    CHECK(fake.LoadsStarted() == 1);
    CHECK(fake.BaseState()["epoch"] == 1);
}

TEST_CASE("FakeBackend game.load ends an in-flight Fake.Sleep with not_ready and dispatched")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    d.StartReaper(5ms);
    fake.AddFakeSave("skython_s");
    const auto slow = d.Submit(fake_call(1, "Sleep", json::array({5000})));
    CHECK(run(d, op_text(2, "game.load", {{"save", "skython_s"}}))["result"]["started"] == true);
    const auto text = d.Wait(slow, 2000ms);
    REQUIRE(text);
    const auto r = json::parse(*text);
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["dispatched"] == true);
    CHECK(r["error"]["detail"]["reason"] == "loading");
}

TEST_CASE("golden: game.save and game.load responses from the fake (responses.json ids 62 to 65)")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    CHECK(spy::dump_json(run(d, spy::dump_json(valid_request(62)))) == spy::dump_json(golden_response(62)));
    fake.AddFakeSave("skython_t41");
    CHECK(spy::dump_json(run(d, spy::dump_json(valid_request(63)))) == spy::dump_json(golden_response(63)));
    CHECK(spy::dump_json(run(d, op_text(64, "game.save", {{"name", "skython_t41"}}))) == spy::dump_json(golden_response(64)));
    CHECK(spy::dump_json(run(d, op_text(65, "game.load", {{"save", "no_such_save_skython"}}))) == spy::dump_json(golden_response(65)));
}
```

In the same file, in "fixture replay: every valid request is accepted, every invalid one gets its fixture error code", replace
```cpp
        CHECK(code_of(r) == (m["op"] == "game.state" ? "ok" : "unsupported"));
```
with
```cpp
        // game.save creates its save (ok); game.load names a save the fake does not have (not_found); events.* need a
        // hub, and this Dispatcher has none (unsupported).
        const std::map<std::string, std::string> expected = {{"game.state", "ok"}, {"game.save", "ok"}, {"game.load", "not_found"}};
        const auto it = expected.find(m["op"].get<std::string>());
        CHECK(code_of(r) == (it != expected.end() ? it->second : "unsupported"));
```
and add `#include <map>` to the file's includes.

- [ ] **Step 2: Run the C++ tests to verify they fail**

Run: `cmake --build core/build -j 2>&1 | grep -m3 "error:"`
Expected: compile errors such as `'struct spy::DispatcherOptions' has no member named 'events'`, `no matching function for call to 'spy::Dispatcher::Submit(std::string, int)'` and `'class spy::FakeBackend' has no member named 'AddFakeSave'`.

- [ ] **Step 3: Route events.* to the hub in the Dispatcher**

`core/include/spy/dispatcher.h`: add `#include "spy/events.h"  // EventHub, ConsumerId, SubId` after the `requests.h` include. Append to `struct DispatcherOptions`, after `on_late`:
```cpp
    EventHub* events = nullptr;  // answers events.subscribe / events.unsubscribe; null -> those ops are unsupported
```
Replace the `Submit` declaration with:
```cpp
    // Never throws; a refused request yields a ticket that is already done. `consumer` travels beside the request text
    // (never inside it) and is used only by events.subscribe / events.unsubscribe, which the hub answers at once: no
    // backend call, no deadline, no in-flight slot, no readiness check. 0 means "no consumer" (those ops: bad_request).
    Ticket Submit(std::string_view request_text, ConsumerId consumer = 0);
```
and add to the rules comment above `class Dispatcher`: `events.* -> EventHub (unsupported without one; bad_request without a consumer).`

`core/src/dispatcher.cpp`: in the anonymous namespace, after `error_text`, add:
```cpp
// events.subscribe / events.unsubscribe, answered here by the hub (index Contracts, dispatcher.h changes).
std::string event_op_text(EventHub* hub, const Request& req, ConsumerId consumer)
{
    if (hub == nullptr) return error_text(req.id, ErrorCode::unsupported, "this build has no event hub");
    if (consumer == 0) return error_text(req.id, ErrorCode::bad_request, "no event consumer");
    try {
        if (req.op == ops::kEventsSubscribe) return dump_json(make_ok(req.id, hub->Subscribe(consumer, req.args)));
        hub->Unsubscribe(consumer, req.args.at("sub").get<SubId>());
        return dump_json(make_ok(req.id, nullptr));
    } catch (const ProtocolError& e) {
        return error_text(req.id, e.code, e.what());
    } catch (const std::exception& e) {
        return error_text(req.id, ErrorCode::internal, std::string("event hub error: ") + e.what());
    }
}
```
Change the definition line to `Ticket Dispatcher::Submit(std::string_view request_text, ConsumerId consumer)` and insert, right after the parse `try`/`catch` block and before `const OpSpec* spec = find_op(req.op);`:
```cpp
    if (req.op == ops::kEventsSubscribe || req.op == ops::kEventsUnsubscribe)
        return shared_->AddDone(event_op_text(options_.events, req, consumer));
```

- [ ] **Step 4: Add game.save and game.load to FakeBackend**

`core/include/spy/fake_backend.h`: add `#include <set>`, `#include <string>` (already there) and `#include <vector>`. In the class comment, replace the line `// Any other function -> unknown_function; any other script or op -> unsupported.` with:
```cpp
// game.save {name} -> {"name": name}, recorded; an existing name -> bad_request, detail.reason "save_exists".
// game.load {save} -> not_ready while NotReadyReason(false) is "starting" or "loading" (the Dispatcher skips readiness
//                     for this needs_game: false op); an unknown name -> not_found; else BumpLoads(), then
//                     {"save": name, "started": true}. Both answer inside Run.
// Any other function -> unknown_function; any other script or op -> unsupported.
```
Add to the public controls, after `RunCount()`:
```cpp
    void AddFakeSave(std::string name);       // a save game.load can find
    std::vector<std::string> FakeSaves() const;  // sorted
```
and to the private members, after `paused_`:
```cpp
    std::set<std::string> saves_;
```

`core/src/fake_backend.cpp`: in `Run`, after the `game.state` block and before `const std::string script = ...`, add:
```cpp
    if (op == ops::kGameSave) {
        const std::string name = args.at("name").get<std::string>();  // parse_request checked it is a save name
        bool inserted = false;
        {
            std::lock_guard lk(mx_);
            inserted = saves_.insert(name).second;
        }
        if (inserted) done(OpResult::Ok({{"name", name}}));
        else done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " already exists", {{"reason", "save_exists"}}));
        return;
    }
    if (op == ops::kGameLoad) {
        const std::string name = args.at("save").get<std::string>();
        const std::optional<std::string> reason = NotReadyReason(false);
        if (reason == "starting" || reason == "loading") {
            const json base = BaseState();
            done(OpResult::Err(ErrorCode::not_ready, *reason == "starting" ? "the game is still starting" : "a game is loading",
                               {{"reason", *reason}, {"paused", base["paused"]}, {"menus", base["menus"]}}));
            return;
        }
        bool known = false;
        {
            std::lock_guard lk(mx_);
            known = saves_.count(name) == 1;
        }
        if (!known) {
            done(OpResult::Err(ErrorCode::not_found, "no save named " + name));
            return;
        }
        BumpLoads();  // the load is a world reset: in-flight needs_game calls end not_ready at the next Tick
        done(OpResult::Ok({{"save", name}, {"started", true}}));
        return;
    }
```
and at the end of the file, before the closing `}` of the namespace:
```cpp
void FakeBackend::AddFakeSave(std::string name)
{
    std::lock_guard lk(mx_);
    saves_.insert(std::move(name));
}

std::vector<std::string> FakeBackend::FakeSaves() const
{
    std::lock_guard lk(mx_);
    return {saves_.begin(), saves_.end()};
}
```

- [ ] **Step 5: Run the C++ tests**

Run: `cmake --build core/build -j 2>&1 | grep -E "error:|warning:" ; ctest --test-dir core/build --output-on-failure 2>&1 | tail -3`
Expected: no output from the first command, then `100% tests passed, 0 tests failed out of 1`.

Run: `cmake --build core/build/tsan -j >/dev/null && setarch "$(uname -m)" -R core/build/tsan/spy_core_tests -tc="*events*,*game.load*,*game.save*" 2>&1 | grep -E "WARNING|test cases"`
Expected: no `WARNING: ThreadSanitizer` line; `[doctest] test cases: ... | 0 failed | ...`.

- [ ] **Step 6: Write the failing module tests**

`pyglue/tests/test_events.py`:
```python
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
    push("cell_attach", reference=form(20))
    push("cell_attach", reference=form(21))
    push("menu_open", menu="Loading Menu")
    assert [e["event"] for e in drain(c)] == ["menu_open"]
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
```

- [ ] **Step 7: Run the module tests to verify they fail**

Run: `cmake --build pyglue/build -j 2>&1 | tail -1 && uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests/test_events.py -q 2>&1 | tail -2`
Expected: the build succeeds (the module code has not changed yet), then FAIL with `AttributeError: module '_skython' has no attribute 'open_consumer'`.

- [ ] **Step 8: Add the event functions to `_skython`**

`pyglue/include/spy/pymodule.h`, append to `struct ModuleHost` after `fake`:
```cpp
    EventHub* events = nullptr;  // open_consumer / close_consumer / next_events; null: no events in this build
```

`pyglue/src/pymodule.cpp`: add `#include "spy/json_depth.h"` after `#include "spy/pymodule.h"`. In the anonymous namespace, after `LateCancelReply`, add:
```cpp
// The texts EventHub::Next made (each one JSON object from dump_json) as one JSON list.
std::string JsonList(const std::vector<std::string>& items)
{
    std::string out = "[";
    for (std::size_t i = 0; i < items.size(); ++i) {
        if (i > 0) out += ',';
        out += items[i];
    }
    out += ']';
    return out;
}
```
Replace the `submit` and `call` definitions with:
```cpp
    m.def("submit", [](const std::string& request, ConsumerId consumer) { return Disp().Submit(request, consumer); },
          py::arg("request"), py::arg("consumer") = 0, release(),
          "Submit request text; returns a ticket. Never blocks on the game. consumer: the event consumer that owns an "
          "events.* request (0: none); it travels beside the text, never inside it.");
```
```cpp
    m.def("call",
          [](const std::string& request, double timeout, ConsumerId consumer) -> std::string {
              const auto ms = ToMs(timeout);
              Dispatcher& d = Disp();
              const Ticket t = d.Submit(request, consumer);
              if (auto text = d.Wait(t, ms)) return std::move(*text);
              // A result that landed between the end of Wait and now is returned, not thrown away.
              if (auto text = d.CancelOrTake(t)) return std::move(*text);
              return LateCancelReply(request, timeout);
          },
          py::arg("request"), py::arg("timeout"), py::arg("consumer") = 0, release(),
          "submit + wait. If timeout seconds pass first, the call is cancelled (unless its result has just arrived) and a "
          "timeout response with detail.late_cancel = true is returned.");
```
After the `in_flight` definition, add:
```cpp
    m.def("open_consumer",
          [] {
              if (Host().events == nullptr) throw std::runtime_error("_skython: this build has no event hub");
              return Host().events->OpenConsumer();
          },
          release(), "A new event consumer id (never 0, never reused).");
    m.def("close_consumer", [](ConsumerId consumer) { if (Host().events != nullptr) Host().events->CloseConsumer(consumer); },
          py::arg("consumer"), release(), "Close a consumer: its subscriptions and queued events are dropped. Idempotent.");
    m.def("next_events",
          [](ConsumerId consumer, double timeout) -> std::string {
              const auto ms = ToMs(timeout);
              if (Host().events == nullptr) return "[]";
              return JsonList(Host().events->Next(consumer, ms, kMaxEventsPerNext));
          },
          py::arg("consumer"), py::arg("timeout"), release(),
          "A JSON list of event objects (at most max_events_per_next), or \"[]\" after timeout seconds with none. An unknown "
          "or closed consumer returns \"[]\" at once.");
```
Inside the existing `if (h.fake != nullptr) { ... }` block, after `_fake_set_paused`, add:
```cpp
        if (h.events != nullptr) {
            EventHub* hub = h.events;
            m.def("_fake_push_event",
                  [hub](const std::string& event, const std::string& data_json) {
                      if (find_event(event) == nullptr) throw std::invalid_argument("unknown event '" + clip_text(event, 64) + "'");
                      if (!nesting_within(data_json, kMaxJsonDepth)) throw std::invalid_argument("data_json is nested too deeply");
                      json data = json::parse(data_json, nullptr, /*allow_exceptions=*/false);
                      if (data.is_discarded() || !data.is_object()) throw std::invalid_argument("data_json must be a JSON object");
                      hub->Push(event, std::move(data));
                  },
                  py::arg("event"), py::arg("data_json"), release(), "Fake build only: push one event, as a game sink would.");
        }
```

`pyglue/linux/fake_module.cpp`: replace the body of `PYBIND11_MODULE(_skython, m)` with:
```cpp
    // Leaked on purpose: the fake's worker and the dispatcher's reaper live until the process exits, as in the game,
    // which never finalises Python. Destroying them during interpreter shutdown would join threads at a bad time.
    static spy::FakeBackend* fake = new spy::FakeBackend();
    // The hub's epoch is the fake's (BaseState()["epoch"] counts loads); it is "loading" while _fake_set_not_ready("loading").
    static spy::EventHub* hub = new spy::EventHub({[] { return fake->LoadsStarted(); },
                                                   [] { return fake->NotReadyReason(false).value_or("") == "loading"; }});
    static spy::Dispatcher* dispatcher = [] {
        const spy::Config cfg = FakeConfig();
        spy::DispatcherOptions options{cfg.default_timeout_ms, cfg.max_inflight_total};
        options.events = hub;
        auto* d = new spy::Dispatcher(*fake, options);
        d->StartReaper();
        return d;
    }();
    // Named fields (C++20 designated initializers), never positional: later tasks append ModuleHost fields
    // (console_echo in Task 43, probe_sink_cycles in Task 44), each with a default.
    spy::InstallModuleHost({.dispatcher = dispatcher, .backend = "fake", .config_json = ConfigJson, .log = Log,
                            .fake = fake, .events = hub});
    spy::DefineModule(m);
```

- [ ] **Step 9: Build and run every module test**

Run: `cmake --build pyglue/build -j 2>&1 | grep -E "error:|warning:" ; uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q 2>&1 | tail -1`
Expected: no output from the first command, then `... passed` with no failures (the v1 module tests still pass: `submit` and `call` without a consumer behave as before).

Run: `cmake -S pyglue/linux -B pyglue/build/warn -DCMAKE_BUILD_TYPE=Debug -DPython_EXECUTABLE="$(uv python find 3.14)" -DCMAKE_CXX_FLAGS="-Wall -Wextra" >/dev/null && cmake --build pyglue/build/warn -j 2>&1 | grep -c "warning:"`
Expected: `0`.

- [ ] **Step 10: Run the core and Python suites**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure 2>&1 | tail -1`
Expected: `100% tests passed, 0 tests failed out of 1`.

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected: two `... passed` lines (the Python package does not use the new functions yet; Task 36 does).

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date` (this task changes no protocol file).

- [ ] **Step 11: Commit**

```bash
git add core/include/spy/dispatcher.h core/src/dispatcher.cpp core/include/spy/fake_backend.h core/src/fake_backend.cpp \
        core/tests/test_dispatcher.cpp core/tests/test_fake_backend.cpp \
        pyglue/include/spy/pymodule.h pyglue/src/pymodule.cpp pyglue/linux/fake_module.cpp pyglue/tests/test_events.py
git commit -m "feat(core,pyglue): events.* through the EventHub, fake game.save/game.load, _skython consumers and next_events"
```
