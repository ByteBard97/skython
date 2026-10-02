# Task 34: Core `EventHub`: consumers, bounded queues, seq, epoch, filters, loading rule

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Contracts** ("C++ core", "Event frame", "Filters", "Loading rule", "The v1 events table") and the Part D brief for Task 34. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 33 (the generated `kEvents`, `find_event`, `EventSpec`, `EventField`, `EventFieldType`, the limits `kDefaultEventQueue`, `kMaxEventQueue`, `kMaxSubscriptions`, `kMaxFilterValues`, `kMaxEventsPerNext`, `kIntMin`/`kIntMax`, and the fixture `protocol/v1/fixtures/events.json`). Linux only; no game.

**What this task builds.** `spy::EventHub`, a pure C++ object with no game and no Python in it. Game sinks (Tasks 39-40) and the `Skython.Send` native (Task 46) call `Wants(event)` and, when it is true, `Push(event, data)`; Python threads drain it through `Next` (via `_skython.next_events`, Task 35). It owns:
- **consumers** (one per `skython.Game`, one per bridge connection), from a counter that starts at 1 and is never reused; 0 means "none";
- **subscriptions**, each owned by one consumer, with an id from a second counter (starts at 1, never reused), a validated filter, and its own bounded queue (`max_queue`, default `kDefaultEventQueue`, at most `kMaxEventQueue`). A full queue drops its **oldest** event and counts it; at most `kMaxSubscriptions` subscriptions exist across all consumers (beyond: `busy`);
- **per-subscription `seq`**: +1 for every event accepted for that subscription (queued, whether later delivered or dropped), so gaps equal drops; an event suppressed by the loading rule takes no `seq`;
- **pending counts** `dropped` and `suppressed` per subscription, reported on its next delivered event and then reset (the frame carries each only when non-zero);
- **the epoch stamp** (`EventHubOptions::epoch`, read when the event is pushed) and **the loading rule** (`EventHubOptions::loading`: while it is true, an event whose table entry has `deliver_while_loading: false` is not queued; each matching subscription's `suppressed` goes up by one).

**Locking (the contract leaves the layout to this task; this is the choice).** One hub mutex guards every queue and table, and **one condition variable is shared by all consumers** (`Next` waits for "my consumer has an event, or is gone"; `Push` notifies only when it queued something). In addition each consumer has a **drain mutex**: `Next` holds it from the moment it takes events off the queues until its frame texts are built and returned, and `Unsubscribe` and `CloseConsumer` take it before they purge. So once `Unsubscribe` or `CloseConsumer` returns, no `Next` call still holds an event of that subscription, while frame building (`dump_json`, up to `kMaxEventsPerNext` events) never runs under the hub mutex and never lengthens `Push`'s one short critical section. Lock order is always drain, then hub; nobody waits on the condition variable while holding a drain mutex. `Push` reads `loading()` (only for events not delivered while loading) and `epoch()` once per call, **outside** the hub mutex (in the plugin they take the lifecycle's own lock), makes one shared copy of the data, and inside the lock only filters, stamps and moves pointers. `Wants` is lock-free: one atomic subscriber count per table entry. `Push` never throws (an allocation failure loses that one event) and never waits for a reader.

**Filters.** `parse_event_filter(event, filter)` validates a filter against the event's table entry and normalises it once, so matching under the lock is a flat compare: a form field keeps form ids (from form references that carry `form_id`; a form key alone is refused with "resolve the form first"), an int field keeps Papyrus Int values, a string field keeps ASCII-lowercased strings. `null` (or an absent filter) means "every event". `filter_matches(filter, event, data)` is AND across fields, OR within one; a missing or `null` event field never matches; strings compare ASCII-case-insensitively (bytes above 0x7F are compared as they are). Error messages are the substrings `events.json`'s `invalid_filters` entries name, for example `hit needs a filter on target or aggressor`.

**Files:**
- Create: `core/include/spy/events.h`, `core/src/events.cpp`
- Create: `core/tests/test_events.cpp`
- `core/CMakeLists.txt` needs no change: it globs `src/*.cpp` and `tests/*.cpp`.

**Interfaces:**
- Consumes: `spy::kEvents`, `spy::find_event`, `spy::EventSpec`, `spy::EventField`, `spy::EventFieldType`, `spy::kDefaultEventQueue`, `spy::kMaxEventQueue`, `spy::kMaxSubscriptions`, `spy::kMaxFilterValues`, `spy::kMaxEventsPerNext`, `spy::kIntMin`, `spy::kIntMax` (Task 33, generated); `spy::ProtocolError`, `spy::dump_json`, `spy::describe`, `spy::clip_text`, `spy::ascii_lower`, `spy::is_uint`, `spy::form_ref_from_json` (v1 core); `protocol/v1/fixtures/events.json` (Task 33).
- Produces (exactly the index Contracts, `core/include/spy/events.h`):
```cpp
using SubId = std::uint64_t;       // from a counter, never reused, starts at 1
using ConsumerId = std::uint64_t;  // from a counter, never reused, starts at 1; 0 means "none"
struct EventFilter { std::vector<std::pair<std::string, std::vector<json>>> any_of; };  // validated; empty = match all
EventFilter parse_event_filter(std::string_view event, const json& filter);  // throws ProtocolError(bad_request)
bool filter_matches(const EventFilter&, std::string_view event, const json& data);
struct EventHubOptions {
    std::function<std::uint32_t()> epoch;   // LifecycleState::epoch in the plugin; a counter in tests
    std::function<bool()> loading;          // LifecycleState::loading in the plugin; a switch in tests
};
class EventHub {
 public:
    explicit EventHub(EventHubOptions);
    ConsumerId OpenConsumer();
    void CloseConsumer(ConsumerId);                  // drops its subscriptions and queued events; wakes a waiting Next; idempotent
    json Subscribe(ConsumerId, const json& args);    // {sub, event, max_queue}; throws ProtocolError (bad_request, busy)
    void Unsubscribe(ConsumerId, SubId);             // purges that subscription's queue; throws ProtocolError(not_found)
    bool Wants(std::string_view event) const;        // lock-free (per-event atomic subscriber count)
    void Push(std::string_view event, json data);    // filters, stamps epoch/seq, drop-oldest; never throws, never waits
    std::vector<std::string> Next(ConsumerId, std::chrono::milliseconds max_wait,
                                  std::size_t max_events = kMaxEventsPerNext);  // frame texts in push order across
                                                     // the consumer's subscriptions; empty on timeout or unknown consumer
    std::size_t Consumers() const;
    std::size_t Subscriptions() const;
    std::uint64_t DroppedTotal() const;
    std::uint64_t SuppressedTotal() const;
};
```
  Behaviour details later tasks rely on: an empty `EventHubOptions::epoch` stamps 0 and an empty `loading` means "never loading"; `Subscribe` on consumer 0, an unknown or a closed consumer is `bad_request`; `Unsubscribe` of an unknown sub and of another consumer's sub are both `not_found` with the same message; `Next` on an unknown or closed consumer returns empty at once; `Next` with `max_events == 0` returns empty; frame texts are `{"type":"event","event","sub","seq","epoch","data"}` plus `dropped`/`suppressed` when non-zero, made with `dump_json`.

---

- [ ] **Step 1: Write the failing tests**

`core/tests/test_events.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/events.h"
#include <atomic>
#include <chrono>
#include <fstream>
#include <optional>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
using spy::json;

namespace {
json events_fixture()
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/events.json");
    REQUIRE(f.good());
    return json::parse(f);
}
json form(std::uint32_t id) { return {{"kind", "form"}, {"form_id", id}}; }
json death(std::uint32_t victim, std::optional<std::uint32_t> killer = std::nullopt)
{
    return {{"victim", form(victim)}, {"killer", killer ? form(*killer) : json(nullptr)}};
}
json channel(const std::string& name, const std::string& payload = "p")
{
    return {{"channel", name}, {"payload", payload}, {"truncated", false}};
}
// The plugin's epoch and loading sources, as switches the test flips.
struct Lifecycle { std::atomic<std::uint32_t> epoch{0}; std::atomic<bool> loading{false}; std::atomic<int> loading_reads{0}; };
spy::EventHubOptions options(Lifecycle& l)
{
    return {[&l] { return l.epoch.load(); }, [&l] { ++l.loading_reads; return l.loading.load(); }};
}
std::vector<json> next(spy::EventHub& hub, spy::ConsumerId c, std::chrono::milliseconds wait = 0ms, std::size_t max = spy::kMaxEventsPerNext)
{
    std::vector<json> out;
    for (const auto& text : hub.Next(c, wait, max)) out.push_back(json::parse(text));
    return out;
}
spy::SubId subscribe(spy::EventHub& hub, spy::ConsumerId c, const std::string& event, json filter = nullptr,
                     std::optional<std::size_t> max_queue = std::nullopt)
{
    json args = {{"event", event}};
    if (!filter.is_null()) args["filter"] = std::move(filter);
    if (max_queue) args["max_queue"] = *max_queue;
    return hub.Subscribe(c, args)["sub"].get<spy::SubId>();
}
struct Thrown { std::optional<spy::ErrorCode> code; std::string message; };
Thrown thrown(auto&& fn)
{
    try {
        fn();
    } catch (const spy::ProtocolError& e) {
        return {e.code, e.what()};
    }
    return {};
}
}

TEST_CASE("event filter: every invalid filter in events.json is bad_request with its message, also through Subscribe")
{
    const json fx = events_fixture();
    REQUIRE(fx["invalid_filters"].size() >= 10);
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    for (const auto& f : fx["invalid_filters"]) {
        const std::string event = f["event"].get<std::string>();
        CAPTURE(event);
        CAPTURE(f["message"].get<std::string>());
        const auto t = thrown([&] { spy::parse_event_filter(event, f["filter"]); });
        REQUIRE(t.code.has_value());
        CHECK(std::string(spy::to_string(*t.code)) == f["error"].get<std::string>());
        CHECK_MESSAGE(t.message.find(f["message"].get<std::string>()) != std::string::npos, t.message);
        CHECK(thrown([&] { hub.Subscribe(c, {{"event", event}, {"filter", f["filter"]}}); }).code == spy::ErrorCode::bad_request);
    }
    CHECK(hub.Subscriptions() == 0);
    CHECK(thrown([] { spy::parse_event_filter("no_such_event", nullptr); }).code == spy::ErrorCode::bad_request);
}

TEST_CASE("event filter: values are normalised once, and every subscribe fixture is accepted")
{
    auto s = spy::parse_event_filter("channel", {{"channel", json::array({"My_Mod", "x"})}});
    REQUIRE(s.any_of.size() == 1);
    CHECK(s.any_of[0].first == "channel");
    CHECK(s.any_of[0].second == std::vector<json>{"my_mod", "x"});
    auto f = spy::parse_event_filter("death", {{"victim", json::array({{{"kind", "form"}, {"form_id", 20}, {"form_key", "Skyrim.esm|0x000014"}}})}});
    CHECK(f.any_of[0].second.at(0).get<std::uint64_t>() == 20u);
    CHECK(spy::parse_event_filter("death", nullptr).any_of.empty());
    CHECK(spy::parse_event_filter("death", json::object()).any_of.empty());

    const json fx = events_fixture();
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    for (const auto& sub : fx["subscribe"]) {
        CAPTURE(sub.dump());
        const json r = hub.Subscribe(c, sub["request"]["args"]);
        CHECK(r["event"] == sub["result"]["event"]);
        CHECK(r["max_queue"] == sub["result"]["max_queue"]);
    }
}

TEST_CASE("event filter: AND across fields, OR within one; forms by id; strings ignore ASCII case; null never matches")
{
    const auto f = spy::parse_event_filter("death", {{"victim", json::array({form(20), form(21)})}, {"killer", json::array({form(7)})}});
    CHECK(spy::filter_matches(f, "death", death(20, 7)));
    CHECK(spy::filter_matches(f, "death", death(21, 7)));
    CHECK_FALSE(spy::filter_matches(f, "death", death(22, 7)));  // victim not listed
    CHECK_FALSE(spy::filter_matches(f, "death", death(20, 8)));  // killer not listed
    CHECK_FALSE(spy::filter_matches(f, "death", death(20)));     // a null killer never matches
    json keyed = death(20, 7);
    keyed["victim"]["form_key"] = "Skyrim.esm|0x000014";
    keyed["victim"]["type"] = "Actor";
    CHECK(spy::filter_matches(f, "death", keyed));
    CHECK_FALSE(spy::filter_matches(f, "death", json::object()));  // missing fields never match

    const auto menu = spy::parse_event_filter("menu_open", {{"menu", json::array({"inventory menu"})}});
    CHECK(spy::filter_matches(menu, "menu_open", {{"menu", "Inventory Menu"}}));
    CHECK(spy::filter_matches(menu, "menu_open", {{"menu", "INVENTORY MENU"}}));
    CHECK_FALSE(spy::filter_matches(menu, "menu_open", {{"menu", "Inventory Menu2"}}));
    const auto umlaut = spy::parse_event_filter("channel", {{"channel", json::array({"\xC3\x84"})}});  // only ASCII folds
    CHECK_FALSE(spy::filter_matches(umlaut, "channel", channel("\xC3\xA4")));
    CHECK(spy::filter_matches(umlaut, "channel", channel("\xC3\x84")));

    const auto stage = spy::parse_event_filter("quest_stage", {{"stage", json::array({10, 20})}});
    CHECK(spy::filter_matches(stage, "quest_stage", {{"quest", form(1)}, {"stage", 20}}));
    CHECK_FALSE(spy::filter_matches(stage, "quest_stage", {{"quest", form(1)}, {"stage", 30}}));
    CHECK(spy::filter_matches(spy::EventFilter{}, "death", json::object()));  // no filter: everything matches
}

TEST_CASE("EventHub: Subscribe answers sub, event and max_queue; ids start at 1 and are never reused")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    CHECK(c == 1u);
    CHECK(hub.Subscribe(c, {{"event", "death"}}) == json{{"sub", 1}, {"event", "death"}, {"max_queue", spy::kDefaultEventQueue}});
    hub.Unsubscribe(c, 1);
    CHECK(hub.Subscribe(c, {{"event", "death"}, {"max_queue", 16}}) == json{{"sub", 2}, {"event", "death"}, {"max_queue", 16}});
    CHECK(hub.OpenConsumer() == 2u);
    for (const json& bad : {json{{"event", "nope"}}, json::object(), json{{"event", 5}}, json{{"event", "death"}, {"max_queue", 0}},
                            json{{"event", "death"}, {"max_queue", spy::kMaxEventQueue + 1}}, json{{"event", "death"}, {"max_queue", "16"}},
                            json::array()})
        CHECK(thrown([&] { hub.Subscribe(c, bad); }).code == spy::ErrorCode::bad_request);
    CHECK(thrown([&] { hub.Subscribe(0, {{"event", "death"}}); }).code == spy::ErrorCode::bad_request);
    CHECK(thrown([&] { hub.Subscribe(999, {{"event", "death"}}); }).code == spy::ErrorCode::bad_request);
    hub.CloseConsumer(c);
    CHECK(thrown([&] { hub.Subscribe(c, {{"event", "death"}}); }).code == spy::ErrorCode::bad_request);
}

TEST_CASE("EventHub: seq starts at 1 per subscription; a full queue drops its oldest and the next delivered event carries dropped")
{
    Lifecycle life;
    spy::EventHub hub(options(life));
    const auto c = hub.OpenConsumer();
    const auto a = subscribe(hub, c, "channel", nullptr, 3);
    for (int i = 1; i <= 5; ++i) hub.Push("channel", channel("x", std::to_string(i)));
    auto got = next(hub, c);
    REQUIRE(got.size() == 3);
    CHECK(got[0] == json{{"type", "event"}, {"event", "channel"}, {"sub", a}, {"seq", 3}, {"epoch", 0}, {"data", channel("x", "3")}, {"dropped", 2}});
    CHECK(got[1]["seq"] == 4);
    CHECK_FALSE(got[1].contains("dropped"));
    CHECK(got[2]["seq"] == 5);
    CHECK_FALSE(got[2].contains("suppressed"));
    CHECK(hub.DroppedTotal() == 2u);

    const auto b = subscribe(hub, c, "channel");
    hub.Push("channel", channel("x", "6"));
    got = next(hub, c);
    REQUIRE(got.size() == 2);
    CHECK((got[0]["sub"] == a && got[0]["seq"] == 6 && !got[0].contains("dropped")));
    CHECK((got[1]["sub"] == b && got[1]["seq"] == 1));
}

TEST_CASE("EventHub: a million pushes into a 16-slot queue nobody drains stay at 16 and report 999984 dropped (Review Focus 1)")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    subscribe(hub, c, "channel", nullptr, 16);
    const json data = channel("flood");
    for (int i = 0; i < 1000000; ++i) hub.Push("channel", data);
    CHECK(hub.DroppedTotal() == 999984u);
    const auto got = next(hub, c);
    REQUIRE(got.size() == 16);
    CHECK(got[0]["dropped"] == 999984);
    CHECK(got[0]["seq"] == 999985);
    CHECK(got[15]["seq"] == 1000000);
    for (std::size_t i = 1; i < got.size(); ++i) CHECK_FALSE(got[i].contains("dropped"));
    CHECK(next(hub, c).empty());
}

TEST_CASE("EventHub: while loading, events not delivered while loading are suppressed; the rest still arrive (Review Focus 3)")
{
    Lifecycle life;
    spy::EventHub hub(options(life));
    const auto c = hub.OpenConsumer();
    const auto cell = subscribe(hub, c, "cell_attach");
    subscribe(hub, c, "menu_open");
    subscribe(hub, c, "game_loaded");
    life.loading = true;
    hub.Push("cell_attach", {{"reference", form(20)}});
    hub.Push("cell_attach", {{"reference", form(21)}});
    hub.Push("menu_open", {{"menu", "Loading Menu"}});
    auto got = next(hub, c);
    REQUIRE(got.size() == 1);
    CHECK(got[0]["event"] == "menu_open");
    CHECK_FALSE(got[0].contains("suppressed"));
    CHECK(hub.SuppressedTotal() == 2u);

    life.loading = false;
    life.epoch = 1;
    hub.Push("game_loaded", {{"new_game", false}});
    hub.Push("cell_attach", {{"reference", form(22)}});
    got = next(hub, c);
    REQUIRE(got.size() == 2);
    CHECK(got[0]["event"] == "game_loaded");
    CHECK(got[0]["epoch"] == 1);
    CHECK(got[1]["sub"] == cell);
    CHECK(got[1]["seq"] == 1);  // suppressed events take no seq
    CHECK(got[1]["suppressed"] == 2);
    CHECK(got[1]["data"]["reference"]["form_id"] == 22);

    // A filtered-out event is not "suppressed" for that subscription.
    const auto only20 = subscribe(hub, c, "cell_attach", {{"reference", json::array({form(20)})}});
    life.loading = true;
    hub.Push("cell_attach", {{"reference", form(99)}});
    life.loading = false;
    hub.Push("cell_attach", {{"reference", form(20)}});
    got = next(hub, c);
    REQUIRE(got.size() == 2);
    for (const auto& e : got) {
        if (e["sub"] == cell) CHECK(e["suppressed"] == 1);
        if (e["sub"] == only20) CHECK_FALSE(e.contains("suppressed"));
    }
}

TEST_CASE("EventHub: loading() is read once per push, only for events it can suppress, and outside the hub's lock")
{
    Lifecycle life;
    spy::EventHub* self = nullptr;
    // Both sources call back into the hub: under the hub's (non-recursive) lock this would deadlock.
    spy::EventHub hub({[&] { (void)self->Subscriptions(); return life.epoch.load(); },
                       [&] { (void)self->Consumers(); ++life.loading_reads; return life.loading.load(); }});
    self = &hub;
    const auto c = hub.OpenConsumer();
    for (int i = 0; i < 3; ++i) subscribe(hub, c, "cell_attach");
    subscribe(hub, c, "menu_open");
    hub.Push("cell_attach", {{"reference", form(1)}});
    CHECK(life.loading_reads == 1);  // three subscriptions, one read
    hub.Push("menu_open", {{"menu", "Journal Menu"}});
    CHECK(life.loading_reads == 1);  // delivered while loading: not read at all
    hub.Push("death", death(1));     // nobody subscribed: returns before reading anything
    CHECK(life.loading_reads == 1);
    CHECK(next(hub, c).size() == 4);
}

TEST_CASE("EventHub: Wants is false with no subscriber, true while one exists, false after the last unsubscribe or close")
{
    spy::EventHub hub({});
    CHECK_FALSE(hub.Wants("death"));
    CHECK_FALSE(hub.Wants("no_such_event"));
    const auto c = hub.OpenConsumer();
    const auto a = subscribe(hub, c, "death");
    const auto b = subscribe(hub, c, "death");
    CHECK(hub.Wants("death"));
    CHECK_FALSE(hub.Wants("dying"));
    hub.Unsubscribe(c, a);
    CHECK(hub.Wants("death"));
    hub.Unsubscribe(c, b);
    CHECK_FALSE(hub.Wants("death"));
    subscribe(hub, c, "death");
    hub.CloseConsumer(c);
    CHECK_FALSE(hub.Wants("death"));
}

TEST_CASE("EventHub: Unsubscribe and CloseConsumer purge queued events; others' subs are not_found; close is idempotent")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    const auto d = hub.OpenConsumer();
    const auto a = subscribe(hub, c, "channel");
    const auto b = subscribe(hub, c, "death");
    for (int i = 0; i < 3; ++i) hub.Push("channel", channel("x"));
    hub.Push("death", death(1));
    hub.Push("death", death(2));
    hub.Unsubscribe(c, a);
    auto got = next(hub, c);
    REQUIRE(got.size() == 2);
    for (const auto& e : got) CHECK(e["sub"] == b);
    hub.Push("channel", channel("x"));  // nobody wants it any more
    CHECK(next(hub, c).empty());

    const auto again = thrown([&] { hub.Unsubscribe(c, a); });
    const auto other = thrown([&] { hub.Unsubscribe(d, b); });
    CHECK(again.code == spy::ErrorCode::not_found);
    CHECK(other.code == spy::ErrorCode::not_found);
    CHECK(thrown([&] { hub.Unsubscribe(c, 12345); }).code == spy::ErrorCode::not_found);
    CHECK(other.message.find(std::to_string(b)) != std::string::npos);  // the same wording whoever owns the sub

    hub.Push("death", death(3));
    CHECK(hub.Consumers() == 2u);
    hub.CloseConsumer(c);
    CHECK(next(hub, c).empty());
    CHECK(hub.Subscriptions() == 0u);
    CHECK(hub.Consumers() == 1u);
    hub.CloseConsumer(c);  // idempotent
    hub.CloseConsumer(424242);
    CHECK(hub.Consumers() == 1u);
}

TEST_CASE("EventHub: two consumers never see each other's events")
{
    spy::EventHub hub({});
    const auto c1 = hub.OpenConsumer();
    const auto c2 = hub.OpenConsumer();
    const auto s1 = subscribe(hub, c1, "channel", {{"channel", json::array({"one"})}});
    const auto s2 = subscribe(hub, c2, "channel");
    hub.Push("channel", channel("one"));
    hub.Push("channel", channel("two"));
    const auto g1 = next(hub, c1);
    const auto g2 = next(hub, c2);
    REQUIRE(g1.size() == 1);
    REQUIRE(g2.size() == 2);
    CHECK(g1[0]["sub"] == s1);
    for (const auto& e : g2) CHECK(e["sub"] == s2);
}

TEST_CASE("EventHub: Next returns push order across a consumer's subscriptions, at most max_events per call")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    subscribe(hub, c, "channel");
    subscribe(hub, c, "death");
    subscribe(hub, c, "quest_stage");
    for (int i = 0; i < 300; ++i) {
        if (i % 3 == 0) hub.Push("channel", channel("x", std::to_string(i)));
        if (i % 3 == 1) hub.Push("death", death(static_cast<std::uint32_t>(i)));
        if (i % 3 == 2) hub.Push("quest_stage", {{"quest", form(1)}, {"stage", i}});
    }
    auto index = [](const json& e) {
        if (e["event"] == "channel") return std::stoi(e["data"]["payload"].get<std::string>());
        if (e["event"] == "death") return e["data"]["victim"]["form_id"].get<int>();
        return e["data"]["stage"].get<int>();
    };
    auto first = next(hub, c);
    REQUIRE(first.size() == spy::kMaxEventsPerNext);
    auto rest = next(hub, c);
    REQUIRE(rest.size() == 300 - spy::kMaxEventsPerNext);
    first.insert(first.end(), rest.begin(), rest.end());
    for (int i = 0; i < 300; ++i) CHECK(index(first[static_cast<std::size_t>(i)]) == i);
    hub.Push("death", death(1));
    hub.Push("death", death(2));
    CHECK(next(hub, c, 0ms, 1).size() == 1);
    CHECK(next(hub, c, 0ms, 0).empty());
    CHECK(next(hub, c).size() == 1);
}

TEST_CASE("EventHub: max_subscriptions counts every consumer; beyond it Subscribe is busy")
{
    spy::EventHub hub({});
    const auto c1 = hub.OpenConsumer();
    const auto c2 = hub.OpenConsumer();
    std::vector<spy::SubId> mine;
    for (std::size_t i = 0; i < spy::kMaxSubscriptions - 24; ++i) mine.push_back(subscribe(hub, c1, "death"));
    for (int i = 0; i < 24; ++i) subscribe(hub, c2, "death");
    CHECK(hub.Subscriptions() == spy::kMaxSubscriptions);
    CHECK(thrown([&] { hub.Subscribe(c2, {{"event", "death"}}); }).code == spy::ErrorCode::busy);
    hub.Unsubscribe(c1, mine.back());
    CHECK_NOTHROW(hub.Subscribe(c2, {{"event", "death"}}));
    hub.CloseConsumer(c1);
    CHECK(hub.Subscriptions() == 25u);
}

TEST_CASE("EventHub: Next waits for an event, times out empty, returns at once for an unknown consumer, and CloseConsumer wakes it")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    subscribe(hub, c, "channel");
    auto t0 = std::chrono::steady_clock::now();
    CHECK(hub.Next(c, 50ms).empty());
    CHECK(std::chrono::steady_clock::now() - t0 >= 45ms);
    t0 = std::chrono::steady_clock::now();
    CHECK(hub.Next(9999, 5s).empty());
    CHECK(std::chrono::steady_clock::now() - t0 < 200ms);

    std::vector<std::string> got;
    std::thread waiter([&] { got = hub.Next(c, 5s); });
    std::this_thread::sleep_for(50ms);
    t0 = std::chrono::steady_clock::now();
    hub.Push("channel", channel("wake"));
    waiter.join();
    CHECK(std::chrono::steady_clock::now() - t0 < 1s);
    CHECK(got.size() == 1);

    std::thread closed([&] { got = hub.Next(c, 5s); });
    std::this_thread::sleep_for(50ms);
    t0 = std::chrono::steady_clock::now();
    hub.CloseConsumer(c);
    closed.join();
    CHECK(std::chrono::steady_clock::now() - t0 < 1s);
    CHECK(got.empty());
}

TEST_CASE("EventHub: a Next that starts after Unsubscribe returned never sees that subscription")
{
    spy::EventHub hub({});
    const auto c = hub.OpenConsumer();
    const auto a = subscribe(hub, c, "channel", {{"channel", json::array({"a"})}});
    subscribe(hub, c, "channel", {{"channel", json::array({"b"})}});
    std::atomic<bool> stop{false}, unsubscribed{false};
    std::atomic<int> violations{0}, b_after{0};
    std::thread pusher([&] {
        while (!stop) {
            hub.Push("channel", channel("a"));
            hub.Push("channel", channel("b"));
        }
    });
    std::thread reader([&] {
        while (!stop) {
            const bool after = unsubscribed.load();
            for (const auto& text : hub.Next(c, 1ms)) {
                const json e = json::parse(text);
                if (after && e["sub"] == a) ++violations;
                if (after && e["data"]["channel"] == "b") ++b_after;
            }
        }
    });
    std::this_thread::sleep_for(30ms);
    hub.Unsubscribe(c, a);
    unsubscribed = true;
    std::this_thread::sleep_for(50ms);
    stop = true;
    pusher.join();
    reader.join();
    CHECK(violations == 0);
    CHECK(b_after > 0);  // the other subscription kept delivering
}

TEST_CASE("EventHub: 8 pushers x 100000 with 2 consumers draining: no deadlock, delivered + dropped = accepted")
{
    spy::EventHub hub({});
    const spy::ConsumerId consumers[2] = {hub.OpenConsumer(), hub.OpenConsumer()};
    for (auto c : consumers) subscribe(hub, c, "channel", nullptr, 64);
    constexpr int kPushers = 8, kEach = 100000;
    std::atomic<int> pushers_done{0};
    struct Tally { std::uint64_t delivered = 0, dropped = 0, last_seq = 0; bool gaps_ok = true; };
    Tally tally[2];
    std::vector<std::thread> threads;
    for (int k = 0; k < 2; ++k)
        threads.emplace_back([&, k] {
            for (;;) {
                const bool done = pushers_done.load() == kPushers;
                const auto batch = hub.Next(consumers[k], 5ms);
                for (const auto& text : batch) {
                    const json e = json::parse(text);
                    const auto seq = e["seq"].get<std::uint64_t>();
                    const auto dropped = e.value("dropped", std::uint64_t{0});
                    if (seq != tally[k].last_seq + 1 + dropped) tally[k].gaps_ok = false;  // gaps equal drops
                    tally[k].last_seq = seq;
                    tally[k].delivered++;
                    tally[k].dropped += dropped;
                }
                if (done && batch.empty()) return;
            }
        });
    const json data = channel("load");
    for (int p = 0; p < kPushers; ++p)
        threads.emplace_back([&] {
            for (int i = 0; i < kEach; ++i) hub.Push("channel", data);
            ++pushers_done;
        });
    for (auto& t : threads) t.join();
    for (const auto& t : tally) {
        CHECK(t.gaps_ok);
        CHECK(t.last_seq == std::uint64_t{kPushers} * kEach);      // the newest event is never the one dropped
        CHECK(t.delivered + t.dropped == std::uint64_t{kPushers} * kEach);
    }
    CHECK(hub.DroppedTotal() == tally[0].dropped + tally[1].dropped);
}

TEST_CASE("EventHub: epoch is read at push time; unknown events and bad data are no-ops; invalid UTF-8 never throws")
{
    Lifecycle life;
    spy::EventHub hub(options(life));
    const auto c = hub.OpenConsumer();
    subscribe(hub, c, "channel");
    subscribe(hub, c, "death", {{"victim", json::array({form(1)})}});
    life.epoch = 7;
    hub.Push("channel", channel("x"));
    life.epoch = 8;
    CHECK(next(hub, c).at(0)["epoch"] == 7);
    CHECK_NOTHROW(hub.Push("no_such_event", json::object()));
    CHECK_NOTHROW(hub.Push("death", json("not an object")));  // a filtered subscription never matches it
    CHECK(next(hub, c).empty());
    hub.Push("channel", channel("\xFF\xFE"));
    const auto got = next(hub, c);
    REQUIRE(got.size() == 1);
    CHECK(got[0]["data"]["channel"].get<std::string>().find("\xEF\xBF\xBD") != std::string::npos);  // U+FFFD
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cmake --build core/build -j 2>&1 | grep -m1 "fatal error"`
Expected: `fatal error: spy/events.h: No such file or directory`.

- [ ] **Step 3: Write `core/include/spy/events.h`**

```cpp
#pragma once
#include "spy/errors.h"              // json, ProtocolError, dump_json
#include "spy/protocol_generated.h"  // kEvents, find_event, kDefaultEventQueue, kMaxEventQueue, kMaxSubscriptions, ...
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace spy {
using SubId = std::uint64_t;       // from a counter, never reused, starts at 1
using ConsumerId = std::uint64_t;  // from a counter, never reused, starts at 1; 0 means "none"

// A validated filter (PROTOCOL.md, Events): every listed field's value must be one of its values. Values are
// normalised by parse_event_filter: form fields hold form ids, int fields Papyrus Ints, string fields ASCII-lowercased text.
struct EventFilter { std::vector<std::pair<std::string, std::vector<json>>> any_of; };  // validated; empty = match all
// `filter` null means no filter. Checks field names against the event's table entry, value types, at most
// kMaxFilterValues values per field, and filter_required_any_of.
EventFilter parse_event_filter(std::string_view event, const json& filter);  // throws ProtocolError(bad_request)
// AND across fields, OR within one; a missing or null event field never matches; strings ignore ASCII case.
bool filter_matches(const EventFilter&, std::string_view event, const json& data);

struct EventHubOptions
{
    std::function<std::uint32_t()> epoch;   // LifecycleState::epoch in the plugin; a counter in tests (empty: 0)
    std::function<bool()> loading;          // LifecycleState::loading in the plugin; a switch in tests (empty: false)
};

// Event consumers, subscriptions and their bounded queues (spec 4.6). Pure C++: no game, no Python. Thread-safe.
// Locking: one hub mutex for every queue and table plus one condition variable shared by all consumers; and one
// drain mutex per consumer, held by Next while it takes events and builds their frames, and taken by Unsubscribe and
// CloseConsumer before they purge (lock order: drain, then hub). Push reads epoch() and loading() once, outside the
// hub mutex, and holds the hub mutex only to filter, stamp and queue.
class EventHub
{
public:
    explicit EventHub(EventHubOptions);
    EventHub(const EventHub&) = delete;
    EventHub& operator=(const EventHub&) = delete;

    ConsumerId OpenConsumer();
    void CloseConsumer(ConsumerId);                  // drops its subscriptions and queued events; wakes a waiting Next; idempotent
    json Subscribe(ConsumerId, const json& args);    // {sub, event, max_queue}; throws ProtocolError (bad_request, busy)
    void Unsubscribe(ConsumerId, SubId);             // purges that subscription's queue; throws ProtocolError(not_found)
    bool Wants(std::string_view event) const;        // lock-free (per-event atomic subscriber count)
    void Push(std::string_view event, json data);    // filters, stamps epoch/seq, drop-oldest; never throws, never waits
    std::vector<std::string> Next(ConsumerId, std::chrono::milliseconds max_wait,
                                  std::size_t max_events = kMaxEventsPerNext);  // frame texts in push order across
                                                     // the consumer's subscriptions; empty on timeout or unknown consumer
    std::size_t Consumers() const;
    std::size_t Subscriptions() const;
    std::uint64_t DroppedTotal() const;
    std::uint64_t SuppressedTotal() const;

private:
    struct Queued { std::uint64_t order; std::uint64_t seq; std::uint32_t epoch; std::shared_ptr<const json> data; };
    struct Sub
    {
        SubId id = 0;
        ConsumerId consumer = 0;
        std::size_t event_index = 0;  // index into kEvents
        EventFilter filter;
        std::size_t max_queue = kDefaultEventQueue;
        std::uint64_t seq = 0;         // accepted events so far
        std::uint64_t dropped = 0;     // since the last delivered event
        std::uint64_t suppressed = 0;  // since the last delivered event
        std::deque<Queued> queue;
    };
    struct Consumer
    {
        std::vector<SubId> subs;
        std::size_t queued = 0;  // events in all of its subscriptions' queues
        std::shared_ptr<std::mutex> drain = std::make_shared<std::mutex>();
    };
    void RemoveSubLocked(SubId id);  // hub mutex held

    EventHubOptions options_;
    mutable std::mutex mx_;
    std::condition_variable cv_;
    std::map<ConsumerId, Consumer> consumers_;
    std::map<SubId, Sub> subs_;
    std::array<std::vector<SubId>, kEvents.size()> by_event_;
    std::array<std::atomic<std::uint32_t>, kEvents.size()> wanted_{};
    ConsumerId next_consumer_ = 1;
    SubId next_sub_ = 1;
    std::uint64_t order_ = 0;  // global push order, for Next's merge across subscriptions
    std::uint64_t dropped_total_ = 0;
    std::uint64_t suppressed_total_ = 0;
};
}
```

- [ ] **Step 4: Write `core/src/events.cpp`**

```cpp
#include "spy/events.h"
#include "spy/values.h"  // form_ref_from_json, is_uint
#include <algorithm>
#include <span>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

const EventSpec& spec_of(std::string_view event)
{
    const EventSpec* s = find_event(event);
    if (s == nullptr) bad("unknown event '" + clip_text(event, 64) + "'");
    return *s;
}

std::size_t index_of(const EventSpec& s) { return static_cast<std::size_t>(&s - kEvents.data()); }

const EventField* field_of(const EventSpec& s, std::string_view name)
{
    for (const auto& f : s.fields)
        if (f.name == name) return &f;
    return nullptr;
}

bool contains(std::span<const std::string_view> names, std::string_view name)
{
    return std::find(names.begin(), names.end(), name) != names.end();
}

std::string lower(std::string_view s)
{
    std::string out(s);
    for (auto& ch : out) ch = ascii_lower(ch);
    return out;
}

std::string join(std::span<const std::string_view> names, std::string_view last_sep)
{
    std::string out;
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (i > 0) out += i + 1 == names.size() ? std::string(last_sep) : std::string(", ");
        out += names[i];
    }
    return out;
}

bool is_papyrus_int(const json& v)
{
    if (v.is_number_unsigned()) return v.get<std::uint64_t>() <= static_cast<std::uint64_t>(kIntMax);
    return v.is_number_integer() && v.get<std::int64_t>() >= kIntMin && v.get<std::int64_t>() <= kIntMax;
}

json normalise(const EventSpec& s, const EventField& f, const json& v)
{
    const std::string where = "filter value for " + std::string(s.name) + "." + std::string(f.name);
    switch (f.type) {
    case EventFieldType::form: {
        if (!v.is_object()) bad(where + " must be a form reference, got " + describe(v));
        const FormRef ref = form_ref_from_json(v);  // bad_request for a malformed reference
        if (!ref.form_id) bad(where + " has no form_id: resolve the form first");
        return json(*ref.form_id);
    }
    case EventFieldType::integer:
        if (!is_papyrus_int(v)) bad(where + " must be an integer (a Papyrus Int), got " + describe(v));
        return json(v.get<std::int64_t>());
    case EventFieldType::string:
        if (!v.is_string()) bad(where + " must be a string, got " + describe(v));
        return json(lower(v.get_ref<const std::string&>()));
    default:
        bad(where + ": this field cannot be filtered");
    }
}
}

EventFilter parse_event_filter(std::string_view event, const json& filter)
{
    const EventSpec& s = spec_of(event);
    EventFilter out;
    if (!filter.is_null()) {
        if (!filter.is_object()) bad("filter must be an object mapping filter fields to lists of values");
        for (const auto& [field, values] : filter.items()) {
            if (!contains(s.filter_fields, field))
                bad("event " + std::string(s.name) + " has no filter field '" + clip_text(field, 64) + "'" +
                    (s.filter_fields.empty() ? std::string(" (it cannot be filtered)") : " (filter fields: " + join(s.filter_fields, ", ") + ")"));
            if (!values.is_array() || values.empty()) bad("filter field '" + field + "' needs a non-empty array of values");
            if (values.size() > kMaxFilterValues)
                bad("filter field '" + field + "' has " + std::to_string(values.size()) + " values; at most " + std::to_string(kMaxFilterValues));
            const EventField* f = field_of(s, field);  // the generator checked that every filter field is a field
            std::vector<json> norm;
            norm.reserve(values.size());
            for (const auto& v : values) norm.push_back(normalise(s, *f, v));
            out.any_of.emplace_back(field, std::move(norm));
        }
    }
    if (!s.filter_required_any_of.empty()) {
        const bool named = std::any_of(out.any_of.begin(), out.any_of.end(),
                                       [&](const auto& p) { return contains(s.filter_required_any_of, p.first); });
        if (!named) bad(std::string(s.name) + " needs a filter on " + join(s.filter_required_any_of, " or "));
    }
    return out;
}

bool filter_matches(const EventFilter& filter, std::string_view event, const json& data)
{
    if (filter.any_of.empty()) return true;
    const EventSpec* s = find_event(event);
    if (s == nullptr || !data.is_object()) return false;
    for (const auto& [field, values] : filter.any_of) {
        const EventField* f = field_of(*s, field);
        const auto it = data.find(field);
        if (f == nullptr || it == data.end() || it->is_null()) return false;
        bool hit = false;
        switch (f->type) {
        case EventFieldType::form:
            if (it->is_object() && it->contains("form_id") && is_uint((*it)["form_id"], 0xFFFFFFFFull)) {
                const auto id = (*it)["form_id"].get<std::uint64_t>();
                hit = std::any_of(values.begin(), values.end(), [&](const json& v) { return v.get<std::uint64_t>() == id; });
            }
            break;
        case EventFieldType::integer:
            if (is_papyrus_int(*it)) {
                const auto n = it->get<std::int64_t>();
                hit = std::any_of(values.begin(), values.end(), [&](const json& v) { return v.get<std::int64_t>() == n; });
            }
            break;
        case EventFieldType::string:
            if (it->is_string()) {
                const std::string text = lower(it->get_ref<const std::string&>());
                hit = std::any_of(values.begin(), values.end(), [&](const json& v) { return v.get_ref<const std::string&>() == text; });
            }
            break;
        default:
            break;
        }
        if (!hit) return false;
    }
    return true;
}

EventHub::EventHub(EventHubOptions options) : options_(std::move(options)) {}

ConsumerId EventHub::OpenConsumer()
{
    std::lock_guard lk(mx_);
    const ConsumerId id = next_consumer_++;
    consumers_.emplace(id, Consumer{});
    return id;
}

void EventHub::RemoveSubLocked(SubId id)
{
    const auto it = subs_.find(id);
    if (it == subs_.end()) return;
    Sub& s = it->second;
    auto& list = by_event_[s.event_index];
    list.erase(std::remove(list.begin(), list.end(), id), list.end());
    wanted_[s.event_index].fetch_sub(1, std::memory_order_relaxed);
    const auto c = consumers_.find(s.consumer);
    if (c != consumers_.end()) {
        c->second.queued -= s.queue.size();
        auto& owned = c->second.subs;
        owned.erase(std::remove(owned.begin(), owned.end(), id), owned.end());
    }
    subs_.erase(it);
}

void EventHub::CloseConsumer(ConsumerId consumer)
{
    std::shared_ptr<std::mutex> drain;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return;
        drain = c->second.drain;
    }
    {
        std::lock_guard dl(*drain);  // a Next of this consumer finishes building its frames first
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return;  // another thread closed it meanwhile
        const std::vector<SubId> owned = c->second.subs;
        for (SubId id : owned) RemoveSubLocked(id);
        consumers_.erase(consumer);
    }
    cv_.notify_all();  // a Next waiting for this consumer returns empty
}

json EventHub::Subscribe(ConsumerId consumer, const json& args)
{
    if (!args.is_object()) bad("events.subscribe args must be an object");
    if (!args.contains("event") || !args["event"].is_string()) bad("missing or non-string field 'event'");
    const std::string event = args["event"].get<std::string>();
    const EventSpec& spec = spec_of(event);
    const json none;
    EventFilter filter = parse_event_filter(event, args.contains("filter") ? args["filter"] : none);
    std::size_t max_queue = kDefaultEventQueue;
    if (args.contains("max_queue")) {
        const json& q = args["max_queue"];
        if (!is_uint(q, kMaxEventQueue) || q.get<std::uint64_t>() < 1)
            bad("max_queue must be an integer from 1 to " + std::to_string(kMaxEventQueue));
        max_queue = q.get<std::size_t>();
    }
    const std::size_t index = index_of(spec);

    std::lock_guard lk(mx_);
    const auto c = consumers_.find(consumer);
    if (c == consumers_.end()) bad("no event consumer " + std::to_string(consumer) + " (never opened, or closed)");
    if (subs_.size() >= kMaxSubscriptions)
        throw ProtocolError(ErrorCode::busy, "too many event subscriptions (" + std::to_string(kMaxSubscriptions) + ")");
    const SubId id = next_sub_++;
    Sub s;
    s.id = id;
    s.consumer = consumer;
    s.event_index = index;
    s.filter = std::move(filter);
    s.max_queue = max_queue;
    subs_.emplace(id, std::move(s));
    by_event_[index].push_back(id);
    c->second.subs.push_back(id);
    wanted_[index].fetch_add(1, std::memory_order_relaxed);
    return {{"sub", id}, {"event", event}, {"max_queue", max_queue}};
}

void EventHub::Unsubscribe(ConsumerId consumer, SubId sub)
{
    const auto missing = [&] { return ProtocolError(ErrorCode::not_found, "no subscription " + std::to_string(sub) + " for this consumer"); };
    std::shared_ptr<std::mutex> drain;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        const auto s = subs_.find(sub);
        if (c == consumers_.end() || s == subs_.end() || s->second.consumer != consumer) throw missing();
        drain = c->second.drain;
    }
    std::lock_guard dl(*drain);
    std::lock_guard lk(mx_);
    const auto s = subs_.find(sub);
    if (s == subs_.end() || s->second.consumer != consumer) throw missing();  // a racing Unsubscribe or CloseConsumer won
    RemoveSubLocked(sub);
}

bool EventHub::Wants(std::string_view event) const
{
    const EventSpec* s = find_event(event);
    return s != nullptr && wanted_[index_of(*s)].load(std::memory_order_relaxed) > 0;
}

void EventHub::Push(std::string_view event, json data)
{
    try {
        const EventSpec* spec = find_event(event);
        if (spec == nullptr) return;
        const std::size_t index = index_of(*spec);
        if (wanted_[index].load(std::memory_order_relaxed) == 0) return;
        // Read once, outside the hub mutex: in the plugin both take the lifecycle's own lock.
        const bool suppress = !spec->deliver_while_loading && options_.loading && options_.loading();
        const std::uint32_t epoch = options_.epoch ? options_.epoch() : 0;
        const auto shared = std::make_shared<const json>(std::move(data));
        bool queued = false;
        {
            std::lock_guard lk(mx_);
            const std::uint64_t order = ++order_;
            for (SubId id : by_event_[index]) {
                const auto it = subs_.find(id);
                if (it == subs_.end()) continue;
                Sub& s = it->second;
                if (!filter_matches(s.filter, event, *shared)) continue;
                if (suppress) {
                    ++s.suppressed;
                    ++suppressed_total_;
                    continue;
                }
                Consumer& c = consumers_.at(s.consumer);
                ++s.seq;
                if (s.queue.size() >= s.max_queue) {  // drop-oldest
                    s.queue.pop_front();
                    ++s.dropped;
                    ++dropped_total_;
                    --c.queued;
                }
                s.queue.push_back({order, s.seq, epoch, shared});
                ++c.queued;
                queued = true;
            }
        }
        if (queued) cv_.notify_all();
    } catch (...) {
        // Never throws: a sink calls this on a game thread. An allocation failure loses this one event.
    }
}

std::vector<std::string> EventHub::Next(ConsumerId consumer, std::chrono::milliseconds max_wait, std::size_t max_events)
{
    struct Item
    {
        SubId sub;
        std::size_t event_index;
        std::uint64_t seq;
        std::uint32_t epoch;
        std::uint64_t dropped;
        std::uint64_t suppressed;
        std::shared_ptr<const json> data;
    };
    if (max_events == 0) return {};
    std::shared_ptr<std::mutex> drain;
    {
        std::unique_lock lk(mx_);
        if (consumers_.find(consumer) == consumers_.end()) return {};
        cv_.wait_for(lk, max_wait, [&] {
            const auto c = consumers_.find(consumer);
            return c == consumers_.end() || c->second.queued > 0;
        });
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end() || c->second.queued == 0) return {};
        drain = c->second.drain;
    }
    std::vector<std::string> frames;
    std::lock_guard dl(*drain);  // held until the frames are returned: Unsubscribe/CloseConsumer wait for them
    std::vector<Item> items;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return frames;
        while (items.size() < max_events && c->second.queued > 0) {
            Sub* oldest = nullptr;  // the subscription whose front event was pushed first
            for (SubId id : c->second.subs) {
                const auto it = subs_.find(id);
                if (it == subs_.end() || it->second.queue.empty()) continue;
                if (oldest == nullptr || it->second.queue.front().order < oldest->queue.front().order) oldest = &it->second;
            }
            if (oldest == nullptr) break;
            Queued q = std::move(oldest->queue.front());
            oldest->queue.pop_front();
            --c->second.queued;
            items.push_back({oldest->id, oldest->event_index, q.seq, q.epoch, oldest->dropped, oldest->suppressed, std::move(q.data)});
            oldest->dropped = 0;
            oldest->suppressed = 0;
        }
    }
    frames.reserve(items.size());
    for (const auto& it : items) {
        json f = {{"type", "event"}, {"event", std::string(kEvents[it.event_index].name)}, {"sub", it.sub},
                  {"seq", it.seq}, {"epoch", it.epoch}, {"data", *it.data}};
        if (it.dropped > 0) f["dropped"] = it.dropped;
        if (it.suppressed > 0) f["suppressed"] = it.suppressed;
        frames.push_back(dump_json(f));
    }
    return frames;
}

std::size_t EventHub::Consumers() const
{
    std::lock_guard lk(mx_);
    return consumers_.size();
}

std::size_t EventHub::Subscriptions() const
{
    std::lock_guard lk(mx_);
    return subs_.size();
}

std::uint64_t EventHub::DroppedTotal() const
{
    std::lock_guard lk(mx_);
    return dropped_total_;
}

std::uint64_t EventHub::SuppressedTotal() const
{
    std::lock_guard lk(mx_);
    return suppressed_total_;
}
}
```

- [ ] **Step 5: Run the tests**

Run: `cmake --build core/build -j 2>&1 | grep -E "error:|warning:" ; ctest --test-dir core/build --output-on-failure 2>&1 | tail -3`
Expected: no output from the first command, then `100% tests passed, 0 tests failed out of 1`. (The Debug build takes a few seconds for the million-push and 8×100000 cases; neither should take more than about 15 s.)

Run: `cmake --build pyglue/build -j 2>&1 | tail -1`
Expected: `[100%] Built target _skython` (`events.cpp` is now part of the static library the Linux module links; nothing calls it there until Task 35).

- [ ] **Step 6: Run the event tests under AddressSanitizer and ThreadSanitizer**

Run: `cmake -S core -B core/build/asan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-Wall -Wextra -Wconversion -fsanitize=address,undefined -fno-sanitize-recover=undefined" >/dev/null && cmake --build core/build/asan -j 2>&1 | grep -c "warning:" ; core/build/asan/spy_core_tests -tc="*event*" | tail -2`
Expected: `0`, then `[doctest] Status: SUCCESS!`.

Run: `cmake -S core -B core/build/tsan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-fsanitize=thread -include $PWD/core/tests/support/tsan_gcc11.h" >/dev/null && cmake --build core/build/tsan -j >/dev/null && setarch "$(uname -m)" -R core/build/tsan/spy_core_tests -tc="*EventHub*" 2>&1 | grep -E "WARNING|test cases"`
Expected: no `WARNING: ThreadSanitizer` line, and `[doctest] test cases:  14 |  14 passed | 0 failed | ...` (the count of other skipped cases varies). Under TSan the whole run takes about half a minute; that is expected.

- [ ] **Step 7: Commit**

```bash
git add core/include/spy/events.h core/src/events.cpp core/tests/test_events.cpp
git commit -m "feat(core): EventHub with per-subscription bounded queues, seq, epoch, filters and the loading rule"
```
