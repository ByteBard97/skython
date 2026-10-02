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
    static_assert(spy::kMaxSubscriptions % spy::kMaxSubscriptionsPerConsumer == 0);
    constexpr std::size_t kFull = spy::kMaxSubscriptions / spy::kMaxSubscriptionsPerConsumer;  // consumers, each at its cap
    std::vector<spy::ConsumerId> cs;
    std::vector<spy::SubId> first;
    for (std::size_t k = 0; k < kFull; ++k) {
        cs.push_back(hub.OpenConsumer());
        for (std::size_t i = 0; i < spy::kMaxSubscriptionsPerConsumer; ++i) {
            const auto s = subscribe(hub, cs.back(), "death");
            if (i == 0) first.push_back(s);
        }
    }
    CHECK(hub.Subscriptions() == spy::kMaxSubscriptions);
    const auto extra = hub.OpenConsumer();
    const auto t = thrown([&] { hub.Subscribe(extra, {{"event", "death"}}); });
    CHECK(t.code == spy::ErrorCode::busy);
    CHECK(t.message == "too many event subscriptions (" + std::to_string(spy::kMaxSubscriptions) + ")");
    hub.Unsubscribe(cs[0], first[0]);
    CHECK_NOTHROW(hub.Subscribe(extra, {{"event", "death"}}));
    hub.CloseConsumer(cs[0]);
    CHECK(hub.Subscriptions() == spy::kMaxSubscriptions - spy::kMaxSubscriptionsPerConsumer + 1);
}

TEST_CASE("EventHub: one consumer holds at most max_subscriptions_per_consumer; other consumers still subscribe (R61)")
{
    spy::EventHub hub({});
    const auto c1 = hub.OpenConsumer();
    const auto c2 = hub.OpenConsumer();
    std::vector<spy::SubId> mine;
    for (std::size_t i = 0; i < spy::kMaxSubscriptionsPerConsumer; ++i) mine.push_back(subscribe(hub, c1, "death"));
    const auto t = thrown([&] { hub.Subscribe(c1, {{"event", "death"}}); });
    CHECK(t.code == spy::ErrorCode::busy);
    CHECK(t.message == "consumer " + std::to_string(c1) + " has " + std::to_string(spy::kMaxSubscriptionsPerConsumer) +
                           " subscriptions (max_subscriptions_per_consumer)");
    CHECK_NOTHROW(hub.Subscribe(c2, {{"event", "death"}}));
    hub.Unsubscribe(c1, mine.back());
    CHECK_NOTHROW(hub.Subscribe(c1, {{"event", "death"}}));
    CHECK(hub.Subscriptions() == spy::kMaxSubscriptionsPerConsumer + 1);
}

TEST_CASE("EventHub: a consumer's queued bytes stay within its budget; the pushed subscription's oldest go first (R61)")
{
    const json big_a = channel("a", std::string(1000, 'x'));
    const json big_b = channel("b", std::string(1000, 'x'));
    const std::size_t frame = spy::dump_json(big_a).size() + spy::kEventFrameOverheadBytes;
    REQUIRE(frame == spy::dump_json(big_b).size() + spy::kEventFrameOverheadBytes);
    spy::EventHub hub(spy::EventHubOptions{.epoch = {}, .loading = {}, .max_queued_bytes_per_consumer = 3 * frame + frame / 2});
    const auto c = hub.OpenConsumer();
    const auto a = subscribe(hub, c, "channel", {{"channel", json::array({"a"})}}, 1000);
    for (int i = 0; i < 10; ++i) hub.Push("channel", big_a);
    auto got = next(hub, c);
    REQUIRE(got.size() == 3);  // max_queue 1000 never bound it: the budget did
    CHECK((got[0]["sub"] == a && got[0]["seq"] == 8 && got[0]["dropped"] == 7));
    CHECK(hub.DroppedTotal() == 7u);

    // Another subscription's bytes count too: when the pushed subscription has nothing older, the new event goes.
    const auto b = subscribe(hub, c, "channel", {{"channel", json::array({"b"})}}, 1000);
    for (int i = 0; i < 3; ++i) hub.Push("channel", big_b);  // b fills the budget
    hub.Push("channel", big_a);                              // a has nothing older: this event itself is dropped
    got = next(hub, c);
    REQUIRE(got.size() == 3);
    for (const auto& e : got) CHECK(e["sub"] == b);
    hub.Push("channel", big_a);
    got = next(hub, c);
    REQUIRE(got.size() == 1);
    CHECK((got[0]["sub"] == a && got[0]["seq"] == 12 && got[0]["dropped"] == 1));  // seq 11 was the dropped one
    CHECK(hub.DroppedTotal() == 8u);

    // A second consumer has its own budget.
    const auto d = hub.OpenConsumer();
    subscribe(hub, d, "channel", {{"channel", json::array({"a"})}}, 1000);
    for (int i = 0; i < 3; ++i) hub.Push("channel", big_b);
    for (int i = 0; i < 3; ++i) hub.Push("channel", big_a);
    CHECK(next(hub, d).size() == 3);
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
