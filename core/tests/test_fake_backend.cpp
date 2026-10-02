#include "doctest/doctest.h"
#include "spy/dispatcher.h"
#include "spy/fake_backend.h"
#include <atomic>
#include <chrono>
#include <fstream>
#include <map>
#include <mutex>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
using spy::json;

namespace {
std::string fake_call(std::uint64_t id, const std::string& function, json args = json::array(), json extra = json::object())
{
    json a = {{"script", "Fake"}, {"function", function}, {"args", std::move(args)}};
    for (auto& [k, v] : extra.items()) a[k] = v;
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "papyrus.call_static"}, {"args", a}});
}
json run(spy::Dispatcher& d, const std::string& text, std::chrono::milliseconds ms = 2000ms)
{
    auto r = d.Wait(d.Submit(text), ms);
    REQUIRE(r.has_value());
    return json::parse(*r);
}
std::string code_of(const json& r) { return r["ok"].get<bool>() ? "ok" : r["error"]["code"].get<std::string>(); }
json load_fixture(const std::string& name)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/" + name);
    REQUIRE(f.good());
    std::stringstream ss;
    ss << f.rdbuf();
    return json::parse(ss.str());
}
}

TEST_CASE("Fake.Echo returns its argument, also a form reference")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    CHECK(run(d, fake_call(1, "Echo", json::array({"hello"})))["result"] == "hello");
    json form = {{"kind", "form"}, {"form_id", 20}};
    CHECK(run(d, fake_call(2, "Echo", json::array({form})))["result"] == form);
    CHECK(run(d, fake_call(3, "Echo"))["result"] == nullptr);
}

TEST_CASE("Fake.Sleep answers after the delay without blocking other calls")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    auto start = std::chrono::steady_clock::now();
    auto slow = d.Submit(fake_call(1, "Sleep", json::array({300})));
    CHECK(run(d, fake_call(2, "Echo", json::array({7})))["result"] == 7);
    CHECK(std::chrono::steady_clock::now() - start < 250ms);  // Echo did not wait behind Sleep
    auto r = d.Wait(slow, 2000ms);
    REQUIRE(r);
    CHECK(json::parse(*r)["result"] == 300);
    CHECK(std::chrono::steady_clock::now() - start >= 300ms);
}

TEST_CASE("Fake.Fail, Fake.Throw, Fake.Count, unknown functions, scripts and ops")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    auto f = run(d, fake_call(1, "Fail", json::array({"vm_error", "the VM said no"})));
    CHECK(code_of(f) == "vm_error");
    CHECK(f["error"]["message"] == "the VM said no");
    CHECK(code_of(run(d, fake_call(2, "Fail", json::array({"no_such_code", "x"})))) == "internal");
    CHECK(code_of(run(d, fake_call(3, "Throw"))) == "internal");
    auto before = fake.RunCount();
    CHECK(run(d, fake_call(4, "Count"))["result"] == before + 1);
    CHECK(code_of(run(d, fake_call(5, "NoSuchFunction"))) == "unknown_function");
    auto other = spy::dump_json({{"type", "request"}, {"id", 6}, {"op", "papyrus.call_static"},
                                 {"args", {{"script", "Utility"}, {"function", "RandomInt"}, {"args", json::array({1, 6})}}}});
    CHECK(code_of(run(d, other)) == "unsupported");
    auto resolve = spy::dump_json({{"type", "request"}, {"id", 7}, {"op", "form.resolve"}, {"args", {{"form_id", 20}}}});
    CHECK(code_of(run(d, resolve)) == "unsupported");
}

TEST_CASE("Fake.Never never answers: the dispatcher times it out")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    d.StartReaper(5ms);
    auto r = run(d, fake_call(1, "Never", json::array(), {{"timeout_ms", 30}}));
    CHECK(code_of(r) == "timeout");
}

TEST_CASE("game.state is answered inside Run with the base state and backend fake")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    auto r = run(d, spy::dump_json({{"type", "request"}, {"id", 1}, {"op", "game.state"}, {"args", json::object()}}));
    CHECK(r["result"]["backend"] == "fake");
    for (const char* k : {"data_ready", "game_loaded", "loading", "interactive", "paused", "menus", "epoch"})
        CHECK(r["result"].contains(k));
    CHECK(r["result"]["interactive"] == true);
}

TEST_CASE("the controls: not ready, paused and a load starting")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    d.StartReaper(5ms);
    fake.SetNotReady("no_game");
    auto r = run(d, fake_call(1, "Echo", json::array({1})));
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "no_game");
    CHECK(fake.BaseState()["interactive"] == false);
    fake.SetNotReady(std::nullopt);
    fake.SetPaused(true);
    CHECK(code_of(run(d, fake_call(2, "Echo", json::array({1}), {{"require_unpaused", true}}))) == "not_ready");
    CHECK(run(d, fake_call(3, "Echo", json::array({1})))["result"] == 1);
    CHECK(fake.BaseState()["paused"] == true);
    fake.SetPaused(false);
    auto t = d.Submit(fake_call(4, "Never"));
    fake.BumpLoads();
    auto lr = d.Wait(t, 2000ms);
    REQUIRE(lr);
    CHECK(json::parse(*lr)["error"]["detail"]["reason"] == "loading");
}

TEST_CASE("fixture replay: every valid request is accepted, every invalid one gets its fixture error code")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    auto fx = load_fixture("requests.json");
    for (const auto& m : fx["valid"]) {
        CAPTURE(m.dump());
        auto r = run(d, spy::dump_json(m));
        CHECK(r["id"] == m["id"]);
        // The fake answers game.state and refuses every real Papyrus op; what matters is that the request got past
        // parsing and readiness to the backend.
        // game.save creates its save (ok); game.load names a save the fake does not have (not_found); events.* need a
        // hub, and this Dispatcher has none (unsupported).
        const std::map<std::string, std::string> expected = {{"game.state", "ok"}, {"game.save", "ok"}, {"game.load", "not_found"}};
        const auto it = expected.find(m["op"].get<std::string>());
        CHECK(code_of(r) == (it != expected.end() ? it->second : "unsupported"));
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        auto r = run(d, spy::dump_json(c["message"]));
        CHECK(code_of(r) == c["error"].get<std::string>());
    }
    CHECK(d.InFlight() == 0);
}

TEST_CASE("fixture replay while a save loads: every needs_game request is not_ready like responses.json says")
{
    spy::FakeBackend fake;
    fake.SetNotReady("loading");
    spy::Dispatcher d(fake, {});
    auto expected = load_fixture("responses.json")["valid"][1]["error"];  // the not_ready response fixture
    auto fx = load_fixture("requests.json");
    for (const auto& m : fx["valid"]) {
        CAPTURE(m.dump());
        auto r = run(d, spy::dump_json(m));
        if (!spy::find_op(m["op"].get<std::string>())->needs_game) continue;  // answered with no game (game.state, events.*, game.load)
        CHECK(r["error"]["code"] == expected["code"]);
        CHECK(r["error"]["message"] == expected["message"]);
        CHECK(r["error"]["detail"]["reason"] == expected["detail"]["reason"]);
    }
}

TEST_CASE("many threads against the fake backend (run under ThreadSanitizer in Step 6)")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    d.StartReaper(1ms);
    std::atomic<int> ok{0};
    std::vector<std::thread> ts;
    for (int i = 0; i < 8; ++i)
        ts.emplace_back([&, i] {
            for (int k = 0; k < 100; ++k) {
                int v = i * 1000 + k;
                auto r = d.Wait(d.Submit(fake_call(static_cast<std::uint64_t>(v), "Echo", json::array({v}))), 2000ms);
                if (r && json::parse(*r)["result"] == v) ok++;
            }
        });
    for (auto& t : ts) t.join();
    CHECK(ok == 800);
}

TEST_CASE("stress: Wait, WaitAny, cancels, timeouts and loads racing on the same tickets (run under ThreadSanitizer in Step 6)")
{
    spy::FakeBackend fake;
    spy::Dispatcher d(fake, {});
    d.StartReaper(1ms);
    const auto runs_before = fake.RunCount();

    struct Pending { spy::Ticket ticket; std::uint64_t id; std::chrono::steady_clock::time_point at; };
    std::mutex mx;
    std::vector<Pending> to_cancel;   // filled by the callers, drained by the canceller
    std::set<spy::Ticket> returned;   // every ticket that returned a response
    std::atomic<int> bad{0}, twice{0}, oks{0}, answered{0}, submitted{0};
    std::atomic<bool> callers_done{false}, stop_loads{false};

    // One response for `ticket`: right id, an expected code, and never a second one.
    auto check = [&](spy::Ticket ticket, std::uint64_t id, const std::string& text) {
        const json r = json::parse(text);
        const std::string c = code_of(r);
        if (r["id"] != id || (c != "ok" && c != "timeout" && c != "not_ready")) bad++;
        if (c == "ok") oks++;
        answered++;
        std::lock_guard lk(mx);
        if (!returned.insert(ticket).second) twice++;
    };

    std::thread loads([&] {
        while (!stop_loads) {
            std::this_thread::sleep_for(20ms);
            fake.BumpLoads();
        }
    });
    std::thread canceller([&] {
        std::mt19937 rng(99);
        for (;;) {
            std::vector<Pending> batch;
            {
                std::lock_guard lk(mx);
                batch.swap(to_cancel);
            }
            if (batch.empty()) {
                if (callers_done) break;
                std::this_thread::sleep_for(1ms);
                continue;
            }
            for (auto& p : batch) {
                std::this_thread::sleep_until(p.at + std::chrono::milliseconds(rng() % 4));
                if (auto text = d.CancelOrTake(p.ticket)) check(p.ticket, p.id, *text);
                else answered++;  // cancelled before any answer
            }
        }
    });
    std::vector<std::thread> callers;
    for (int i = 0; i < 8; ++i)
        callers.emplace_back([&, i] {
            std::mt19937 rng(static_cast<unsigned>(i));
            for (int k = 0; k < 200; ++k) {
                const auto id = static_cast<std::uint64_t>(i * 1000 + k);
                const json sleep_ms = static_cast<int>(rng() % 6);
                const json timeout_ms = static_cast<int>(1 + rng() % 5);
                const auto t = d.Submit(fake_call(id, "Sleep", json::array({sleep_ms}), {{"timeout_ms", timeout_ms}}));
                submitted++;
                switch (k % 4) {
                case 0:
                case 1:
                    if (auto r = d.Wait(t, 2000ms)) check(t, id, *r); else bad++;
                    if (d.Wait(t, 0ms)) twice++;
                    break;
                case 2: {
                    auto got = d.WaitAny({t, 999999999}, 2000ms);
                    if (got.size() == 1 && got[0].first == t) check(t, id, got[0].second); else bad++;
                    if (!d.WaitAny({t}, 0ms).empty()) twice++;
                    break;
                }
                default: {
                    std::lock_guard lk(mx);
                    to_cancel.push_back({t, id, std::chrono::steady_clock::now()});
                }
                }
            }
        });
    for (auto& t : callers) t.join();
    callers_done = true;
    canceller.join();
    stop_loads = true;
    loads.join();

    CHECK(bad == 0);
    CHECK(twice == 0);
    CHECK(answered == submitted);
    CHECK(d.InFlight() == 0);
    // Every backend run ends in exactly one done: it either won (an ok response) or lost (a late result). The fake's
    // worker may still hold a few queued answers, so wait for them.
    const auto deadline = std::chrono::steady_clock::now() + 5s;
    while (fake.RunCount() - runs_before != oks + d.LateResults() && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(5ms);
    CHECK(fake.RunCount() - runs_before == oks + d.LateResults());
    CHECK(d.Abandoned() == 0);
}

// ---- game.save and game.load on the fake ----------------------------------------------------------------
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
