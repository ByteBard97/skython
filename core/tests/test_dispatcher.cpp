#include "doctest/doctest.h"
#include "spy/clock.h"
#include "spy/dispatcher.h"
#include <atomic>
#include <fstream>
#include <chrono>
#include <functional>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;
using spy::json;
using spy::OpResult;

namespace {
// A backend the test drives by hand: Run records the call and its callbacks; the test decides when (and whether)
// to call done. Readiness, paused/menus and the load counter are plain fields.
struct ManualBackend : spy::Backend
{
    struct Call { std::string op; json args; spy::Done done; spy::Cancelled cancelled; spy::Progress progress; };
    std::mutex mx;
    std::vector<Call> calls;
    std::optional<std::string> not_ready;
    bool paused = false;
    std::atomic<std::uint32_t> loads{0};
    bool throw_in_run = false;
    bool throw_in_ready = false;  // NotReadyReason throws something that is not a std::exception
    bool answer_inline = false;   // call done(Ok) from inside Run (what a game.state answer does)
    std::function<void()> on_base_state;  // runs once, inside the next BaseState() call

    void Run(const std::string& op, const json& args, spy::Done done, spy::Cancelled cancelled, spy::Progress progress) override
    {
        if (throw_in_run) throw std::runtime_error("backend exploded");
        if (answer_inline) { done(OpResult::Ok({{"inline", op}})); return; }
        std::lock_guard lk(mx);
        calls.push_back({op, args, std::move(done), std::move(cancelled), std::move(progress)});
    }
    json BaseState() override
    {
        if (on_base_state) {
            auto f = std::move(on_base_state);
            on_base_state = nullptr;
            f();
        }
        return {{"data_ready", true}, {"game_loaded", true}, {"loading", false}, {"interactive", true},
                {"paused", paused}, {"menus", paused ? json::array({"Journal Menu"}) : json::array()}, {"epoch", 1}};
    }
    std::optional<std::string> NotReadyReason(bool require_unpaused) override
    {
        if (throw_in_ready) throw 42;
        if (not_ready) return not_ready;
        if (require_unpaused && paused) return std::string("paused");
        return std::nullopt;
    }
    std::uint32_t LoadsStarted() override { return loads.load(); }

    std::size_t count() { std::lock_guard lk(mx); return calls.size(); }
    Call take(std::size_t i) { std::lock_guard lk(mx); return calls.at(i); }
};

std::string call_text(std::uint64_t id, const std::string& function = "RandomInt", json extra = json::object())
{
    json args = {{"script", "Utility"}, {"function", function}, {"args", json::array({1, 6})}};
    for (auto& [k, v] : extra.items()) args[k] = v;
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "papyrus.call_static"}, {"args", args}});
}
std::string state_text(std::uint64_t id) { return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "game.state"}, {"args", json::object()}}); }

json wait_json(spy::Dispatcher& d, spy::Ticket t, std::chrono::milliseconds ms = 1000ms)
{
    auto text = d.Wait(t, ms);
    REQUIRE(text.has_value());
    return json::parse(*text);
}
std::string code_of(const json& r) { return r["ok"].get<bool>() ? "ok" : r["error"]["code"].get<std::string>(); }
}

TEST_CASE("a call reaches the backend once and its result comes back as response text")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(7));
    REQUIRE(b.count() == 1);
    auto c = b.take(0);
    CHECK(c.op == "papyrus.call_static");
    CHECK(c.args["function"] == "RandomInt");
    CHECK(d.InFlight() == 1);
    CHECK_FALSE(d.Wait(t, 10ms).has_value());  // not done yet; the ticket stays
    c.done(OpResult::Ok(4));
    auto r = wait_json(d, t);
    CHECK(r == json::parse(R"({"type":"response","id":7,"ok":true,"result":4})"));
    CHECK(d.InFlight() == 0);
    CHECK_FALSE(d.Wait(t, 10ms).has_value());  // a returned ticket is consumed
}

TEST_CASE("an error result keeps its code, message and detail")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(8));
    b.take(0).done(OpResult::Err(spy::ErrorCode::type_mismatch, "argument 1 (aiMin) must be Int", {{"param", "aiMin"}}));
    auto r = wait_json(d, t);
    CHECK(code_of(r) == "type_mismatch");
    CHECK(r["error"]["message"] == "argument 1 (aiMin) must be Int");
    CHECK(r["error"]["detail"]["param"] == "aiMin");
}

TEST_CASE("bad request text is answered at once with bad_request and the best id we can find")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto r1 = wait_json(d, d.Submit("not json at all"));
    CHECK(code_of(r1) == "bad_request");
    CHECK(r1["id"] == 0);
    auto r2 = wait_json(d, d.Submit(R"({"type":"request","id":41,"op":"no.such.op","args":{}})"));
    CHECK(code_of(r2) == "bad_request");
    CHECK(r2["id"] == 41);
    std::string deep(200, '[');
    deep += std::string(200, ']');
    auto r3 = wait_json(d, d.Submit(deep));
    CHECK(code_of(r3) == "bad_request");
    auto r4 = wait_json(d, d.Submit(call_text(42, "RandomInt", {{"args", json::array({json(5000000000LL)})}})));
    CHECK(code_of(r4) == "type_mismatch");
    CHECK(r4["id"] == 42);
    CHECK(b.count() == 0);  // none of them reached the backend
    CHECK(d.InFlight() == 0);
}

TEST_CASE("needs_game ops get not_ready at Submit, with reason, paused and menus, without reaching the backend")
{
    ManualBackend b;
    b.not_ready = "loading";
    b.paused = true;
    spy::Dispatcher d(b, {});
    auto r = wait_json(d, d.Submit(call_text(9)));
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "loading");
    CHECK(r["error"]["detail"]["paused"] == true);
    CHECK(r["error"]["detail"]["menus"] == json::array({"Journal Menu"}));
    CHECK_FALSE(r["error"]["detail"].contains("dispatched"));  // refused before the backend: safe to retry
    CHECK(b.count() == 0);
}

TEST_CASE("game.state is not a needs_game op: it reaches the backend while the game is not ready")
{
    ManualBackend b;
    b.not_ready = "no_game";
    spy::Dispatcher d(b, {});
    auto t = d.Submit(state_text(10));
    REQUIRE(b.count() == 1);
    b.take(0).done(OpResult::Ok({{"interactive", false}}));
    CHECK(code_of(wait_json(d, t)) == "ok");
}

TEST_CASE("require_unpaused turns a paused game into not_ready reason paused; without it the call runs")
{
    ManualBackend b;
    b.paused = true;
    spy::Dispatcher d(b, {});
    auto r = wait_json(d, d.Submit(call_text(11, "RandomInt", {{"require_unpaused", true}})));
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "paused");
    d.Submit(call_text(12));
    CHECK(b.count() == 1);
}

TEST_CASE("timeout_ms and require_unpaused count only for ops that declare them")
{
    ManualBackend b;
    b.paused = true;
    spy::Dispatcher d(b, {});
    json object = {{"kind", "form"}, {"form_id", 20}};
    auto prop = d.Submit(spy::dump_json({{"type", "request"}, {"id", 40}, {"op", "papyrus.get_property"},
                                         {"args", {{"object", object}, {"script", "ObjectReference"}, {"property", "X"},
                                                   {"timeout_ms", 10}, {"require_unpaused", true}}}}));
    CHECK(b.count() == 1);  // get_property has no require_unpaused argument: the paused game does not refuse it
    std::this_thread::sleep_for(30ms);
    d.Tick();
    CHECK_FALSE(d.Wait(prop, 0ms).has_value());  // nor a timeout_ms: the 5 s default applies, not 10 ms
    CHECK(d.InFlight() == 1);
    // Where timeout_ms is an argument, under kMinTimeoutMs is refused, never an instant timeout.
    CHECK(code_of(wait_json(d, d.Submit(call_text(41, "RandomInt", {{"timeout_ms", 0}})))) == "bad_request");
}

TEST_CASE("over max_inflight_total the answer is busy; a finished call frees its slot")
{
    ManualBackend b;
    spy::Dispatcher d(b, {.default_timeout_ms = 5000, .max_inflight_total = 2});
    auto t1 = d.Submit(call_text(1));
    d.Submit(call_text(2));
    auto r = wait_json(d, d.Submit(call_text(3)));
    CHECK(code_of(r) == "busy");
    CHECK(b.count() == 2);
    b.take(0).done(OpResult::Ok(1));
    wait_json(d, t1);
    d.Submit(call_text(4));
    CHECK(b.count() == 3);
}

TEST_CASE("Tick times out a call past its deadline, with paused and menus; its late result is dropped and counted")
{
    ManualBackend b;
    b.paused = true;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(13, "RandomInt", {{"timeout_ms", 20}}));
    auto c = b.take(0);
    CHECK_FALSE(c.cancelled());
    d.Tick();
    CHECK_FALSE(d.Wait(t, 0ms).has_value());  // not yet past the deadline
    std::this_thread::sleep_for(40ms);
    d.Tick();
    auto r = wait_json(d, t);
    CHECK(code_of(r) == "timeout");
    CHECK(r["error"]["detail"]["paused"] == true);
    CHECK(r["error"]["detail"]["menus"] == json::array({"Journal Menu"}));
    CHECK(c.cancelled());
    CHECK(d.InFlight() == 0);
    c.done(OpResult::Ok(5));
    CHECK(d.LateResults() == 1);
}

TEST_CASE("without timeout_ms the default timeout applies")
{
    ManualBackend b;
    spy::Dispatcher d(b, {.default_timeout_ms = 20, .max_inflight_total = 1024});
    auto t = d.Submit(state_text(14));
    std::this_thread::sleep_for(40ms);
    d.Tick();
    CHECK(code_of(wait_json(d, t)) == "timeout");
}

TEST_CASE("a batch that times out reports completed and results from its last Progress")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    json step = {{"op", "papyrus.call_static"}, {"args", {{"script", "Utility"}, {"function", "RandomInt"}, {"args", json::array({1, 6})}}}};
    auto t = d.Submit(spy::dump_json({{"type", "request"}, {"id", 15}, {"op", "papyrus.batch"},
                                      {"args", {{"calls", json::array({step, step, step})}, {"timeout_ms", 20}}}}));
    auto c = b.take(0);
    c.progress({{"completed", 1}, {"results", json::array({{{"ok", true}, {"result", 3}}})}});
    std::this_thread::sleep_for(40ms);
    d.Tick();
    auto r = wait_json(d, t);
    CHECK(code_of(r) == "timeout");
    CHECK(r["error"]["detail"]["completed"] == 1);
    CHECK(r["error"]["detail"]["results"].size() == 1);
    CHECK(r["error"]["detail"].contains("paused"));
}

TEST_CASE("a batch that times out before any Progress reports zero completed")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    json step = {{"op", "papyrus.call_static"}, {"args", {{"script", "Utility"}, {"function", "RandomInt"}, {"args", json::array({1, 6})}}}};
    auto t = d.Submit(spy::dump_json({{"type", "request"}, {"id", 16}, {"op", "papyrus.batch"},
                                      {"args", {{"calls", json::array({step})}, {"timeout_ms", 10}}}}));
    std::this_thread::sleep_for(30ms);
    d.Tick();
    auto r = wait_json(d, t);
    CHECK(r["error"]["detail"]["completed"] == 0);
    CHECK(r["error"]["detail"]["results"] == json::array());
}

TEST_CASE("when a save starts loading, in-flight game calls end not_ready reason loading; game.state is left alone")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto call = d.Submit(call_text(17));
    auto state = d.Submit(state_text(18));
    d.Tick();  // nothing changed yet
    CHECK_FALSE(d.Wait(call, 0ms).has_value());
    b.loads++;
    CHECK(b.take(0).cancelled());  // at once, before the next Tick
    d.Tick();
    auto r = wait_json(d, call);
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["message"] == "a game is loading");  // the same message as the readiness answer
    CHECK(r["error"]["detail"]["reason"] == "loading");
    CHECK(r["error"]["detail"]["dispatched"] == true);  // it reached Backend::Run: a retry could repeat its effect
    CHECK(r["error"]["detail"].contains("paused"));
    CHECK_FALSE(d.Wait(state, 0ms).has_value());
    b.take(1).done(OpResult::Ok(json::object()));
    CHECK(code_of(wait_json(d, state)) == "ok");
}

TEST_CASE("a quit to the main menu ends in-flight game calls not_ready, with the reason of that moment")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto call = d.Submit(call_text(19));
    b.loads++;              // LifecycleState counts a quit to the Main Menu as a world reset
    b.not_ready = "no_game";
    d.Tick();
    auto r = wait_json(d, call);
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "no_game");
    CHECK(b.take(0).cancelled());
}

TEST_CASE("a load that starts while Submit checks readiness still ends the call")
{
    // NotReadyReason is the last thing Submit asks before the ticket exists: a load that starts there, with a Tick
    // that runs before the ticket is in the table, must still end the call.
    struct RacyBackend : ManualBackend
    {
        spy::Dispatcher* d = nullptr;
        bool fired = false;
        std::optional<std::string> NotReadyReason(bool require_unpaused) override
        {
            if (!fired) {
                fired = true;
                loads++;
                d->Tick();
            }
            return ManualBackend::NotReadyReason(require_unpaused);
        }
    };
    RacyBackend b;
    spy::Dispatcher d(b, {});
    b.d = &d;
    auto t = d.Submit(call_text(43));
    REQUIRE(b.count() == 1);
    CHECK(b.take(0).cancelled());
    d.Tick();
    auto r = wait_json(d, t, 0ms);
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "loading");
    CHECK(d.InFlight() == 0);
}

TEST_CASE("an exception thrown by Backend::Run becomes internal")
{
    ManualBackend b;
    b.throw_in_run = true;
    spy::Dispatcher d(b, {});
    auto r = wait_json(d, d.Submit(call_text(19)));
    CHECK(code_of(r) == "internal");
    CHECK(r["id"] == 19);
    CHECK(d.InFlight() == 0);
}

TEST_CASE("an exception of any type from the readiness query becomes internal; Submit never throws")
{
    ManualBackend b;
    b.throw_in_ready = true;
    spy::Dispatcher d(b, {});
    auto r = wait_json(d, d.Submit(call_text(44)));
    CHECK(code_of(r) == "internal");
    CHECK(r["id"] == 44);
    CHECK(b.count() == 0);
}

TEST_CASE("a backend that calls done inside Run does not deadlock (Run and done are never under the lock)")
{
    ManualBackend b;
    b.answer_inline = true;
    spy::Dispatcher d(b, {});
    auto r = wait_json(d, d.Submit(state_text(20)));
    CHECK(r["result"]["inline"] == "game.state");
}

TEST_CASE("Cancel erases the ticket: Wait returns nullopt and the late result is counted")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(21));
    auto c = b.take(0);
    d.Cancel(t);
    CHECK(c.cancelled());
    CHECK(d.InFlight() == 0);
    CHECK_FALSE(d.Wait(t, 10ms).has_value());
    c.done(OpResult::Ok(1));
    CHECK(d.LateResults() == 1);
    d.Cancel(t);        // cancelling twice is harmless
    d.Cancel(999999);   // so is an unknown ticket
}

TEST_CASE("CancelOrTake returns a result that is already there, and otherwise cancels")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t1 = d.Submit(call_text(46));
    b.take(0).done(OpResult::Ok(9));
    auto text = d.CancelOrTake(t1);
    REQUIRE(text.has_value());
    CHECK(json::parse(*text)["result"] == 9);
    CHECK_FALSE(d.Wait(t1, 0ms).has_value());  // consumed
    auto t2 = d.Submit(call_text(47));
    CHECK_FALSE(d.CancelOrTake(t2).has_value());
    CHECK(b.take(1).cancelled());
    CHECK(d.InFlight() == 0);
    CHECK_FALSE(d.CancelOrTake(t2).has_value());  // gone now
    CHECK(d.LateResults() == 0);
}

TEST_CASE("on_late hears about every dropped result, outside the lock")
{
    ManualBackend b;
    std::vector<std::string> heard;
    spy::Dispatcher* self = nullptr;
    spy::Dispatcher d(b, {.on_late = [&](const std::string& line) {
                              heard.push_back(line);
                              CHECK(self->InFlight() == 0);  // takes the lock: would deadlock if on_late ran under it
                          }});
    self = &d;
    auto t = d.Submit(call_text(48));
    auto c = b.take(0);
    d.Cancel(t);
    c.done(OpResult::Ok(1));
    REQUIRE(heard.size() == 1);
    CHECK(heard[0].find("request 48") != std::string::npos);
    CHECK(d.LateResults() == 1);
}

TEST_CASE("Tick drops finished results nobody collected after abandon_after_ms, and counts them")
{
    ManualBackend b;
    spy::Dispatcher d(b, {.abandon_after_ms = 20});
    auto done = d.Submit(call_text(49));
    b.take(0).done(OpResult::Ok(1));
    auto refused = d.Submit("not json");
    auto running = d.Submit(call_text(50));
    d.Tick();
    CHECK(d.Abandoned() == 0);
    std::this_thread::sleep_for(40ms);
    d.Tick();
    CHECK(d.Abandoned() == 2);
    CHECK_FALSE(d.Wait(done, 0ms).has_value());
    CHECK_FALSE(d.Wait(refused, 0ms).has_value());
    CHECK(d.InFlight() == 1);  // a call still running is never abandoned
    b.take(1).done(OpResult::Ok(2));
    CHECK(code_of(wait_json(d, running)) == "ok");
}

TEST_CASE("done is idempotent: the first result wins and a second call counts as late")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(22));
    auto c = b.take(0);
    c.done(OpResult::Ok(1));
    c.done(OpResult::Ok(2));
    CHECK(wait_json(d, t)["result"] == 1);
    CHECK(d.LateResults() == 1);
}

TEST_CASE("a backend result that lands while Tick is ending the call wins and is not counted as late")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(45, "RandomInt", {{"timeout_ms", 10}}));
    auto c = b.take(0);
    b.on_base_state = [&c] { c.done(OpResult::Ok(1)); };  // Tick asks BaseState between its scan and its Finish
    std::this_thread::sleep_for(30ms);
    d.Tick();
    CHECK(code_of(wait_json(d, t)) == "ok");
    CHECK(d.LateResults() == 0);  // only a backend result that loses is a dropped result; the Dispatcher losing is not
}

TEST_CASE("tickets are never reused, also after they are consumed or cancelled")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    std::set<spy::Ticket> seen;
    for (std::size_t i = 0; i < 50; ++i) {
        auto t = d.Submit(call_text(i));
        seen.insert(t);
        if (i % 2) {
            d.Cancel(t);
        } else {
            b.take(i).done(OpResult::Ok(1));
            REQUIRE(d.Wait(t, 0ms).has_value());  // consumed
        }
    }
    CHECK(seen.size() == 50);
}

TEST_CASE("WaitAny returns the finished tickets among those asked for, and only those")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t1 = d.Submit(call_text(31));
    auto t2 = d.Submit(call_text(32));
    auto t3 = d.Submit(call_text(33));
    CHECK(d.WaitAny({t1, t2, t3}, 10ms).empty());
    b.take(1).done(OpResult::Ok(2));
    b.take(2).done(OpResult::Ok(3));
    auto got = d.WaitAny({t1, t2}, 1000ms);
    REQUIRE(got.size() == 1);
    CHECK(got[0].first == t2);
    CHECK(json::parse(got[0].second)["id"] == 32);
    CHECK(d.WaitAny({t2}, 10ms).empty());  // consumed
    auto rest = d.WaitAny({t1, t3}, 1000ms);
    REQUIRE(rest.size() == 1);
    CHECK(rest[0].first == t3);
    CHECK(d.WaitAny({}, 10ms).empty());
    CHECK(d.WaitAny({123456}, 10ms).empty());
}

TEST_CASE("WaitAny wakes as soon as one of its tickets finishes on another thread")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(call_text(34));
    std::thread later([&] { std::this_thread::sleep_for(30ms); b.take(0).done(OpResult::Ok(1)); });
    auto start = std::chrono::steady_clock::now();
    auto got = d.WaitAny({t}, 5000ms);
    later.join();
    CHECK(got.size() == 1);
    CHECK(std::chrono::steady_clock::now() - start < 2000ms);
}

TEST_CASE("the reaper thread runs Tick on its own")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    d.StartReaper(5ms);
    d.StartReaper(5ms);  // a second start is a no-op
    auto t = d.Submit(call_text(35, "RandomInt", {{"timeout_ms", 10}}));
    CHECK(code_of(wait_json(d, t, 2000ms)) == "timeout");
}

TEST_CASE("a result that arrives after the Dispatcher is gone is harmless")
{
    ManualBackend b;
    spy::Done late;
    {
        spy::Dispatcher d(b, {});
        d.StartReaper(5ms);
        d.Submit(call_text(36));
        late = b.take(0).done;
    }
    late(OpResult::Ok(1));  // must not touch freed memory (ASan run, Step 6)
}

TEST_CASE("many threads submitting and waiting at once (run under ThreadSanitizer in Step 6)")
{
    ManualBackend b;
    b.answer_inline = true;
    spy::Dispatcher d(b, {});
    d.StartReaper(1ms);
    std::atomic<int> ok{0};
    std::vector<std::thread> ts;
    for (int i = 0; i < 8; ++i)
        ts.emplace_back([&, i] {
            for (int k = 0; k < 200; ++k) {
                auto t = d.Submit(call_text(static_cast<std::uint64_t>(i * 1000 + k)));
                auto r = d.Wait(t, 2000ms);
                if (r && json::parse(*r)["id"] == i * 1000 + k) ok++;
            }
        });
    for (auto& t : ts) t.join();
    CHECK(ok == 1600);
    CHECK(d.InFlight() == 0);
}

// Golden responses (protocol/v1/fixtures/responses.json): the dispatcher's own text, byte for byte after a canonical
// re-dump. Late cancel (id 42) is built by _skython (pyglue/tests/test_module.py checks it against the fixture).
namespace {
json golden(std::uint64_t id)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/responses.json");
    REQUIRE(f.good());
    const json fx = json::parse(f);  // named: a range-for over json::parse(f)["valid"] would dangle
    for (const auto& r : fx["valid"])
        if (r.value("id", std::uint64_t{0}) == id && r["type"] == "response") return r;
    FAIL("no golden response with id " << id);
    return {};
}
std::string canon(const std::string& text) { return spy::dump_json(json::parse(text)); }
json one_step() { return {{"op", "papyrus.call_static"}, {"args", {{"script", "Utility"}, {"function", "RandomInt"}, {"args", json::array({1, 6})}}}}; }
std::string batch_text(std::uint64_t id, json extra = json::object())
{
    json args = {{"calls", json::array({one_step(), one_step()})}};
    for (auto& [k, v] : extra.items()) args[k] = v;
    return spy::dump_json({{"type", "request"}, {"id", id}, {"op", "papyrus.batch"}, {"args", args}});
}
const json kStepResults = json::array({{{"ok", true}, {"result", 4}},
    {{"ok", false}, {"error", {{"code", "unknown_function"}, {"message", "no script named Nope is loaded"}, {"detail", json::object()}}}}});
}

TEST_CASE("golden: a batch result")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(batch_text(40));
    b.take(0).done(OpResult::Ok({{"results", kStepResults}}));
    auto text = d.Wait(t, 1000ms);
    REQUIRE(text);
    CHECK(canon(*text) == spy::dump_json(golden(40)));
}

TEST_CASE("golden: a batch timeout carries completed and results")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto t = d.Submit(batch_text(41, {{"timeout_ms", 20}}));
    b.take(0).progress({{"completed", 1}, {"results", json::array({kStepResults[0]})}});
    std::this_thread::sleep_for(40ms);
    d.Tick();
    auto text = d.Wait(t, 1000ms);
    REQUIRE(text);
    CHECK(canon(*text) == spy::dump_json(golden(41)));
}

TEST_CASE("golden: not_ready with dispatched, after a save load and after a quit to the main menu")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    auto load = d.Submit(call_text(43));
    b.loads++;  // a save load starts; NotReadyReason says nothing more, so the reason is "loading"
    d.Tick();
    auto text = d.Wait(load, 1000ms);
    REQUIRE(text);
    CHECK(canon(*text) == spy::dump_json(golden(43)));

    auto quit = d.Submit(call_text(44));
    b.loads++;  // a quit to the main menu; the state of that moment is "no_game"
    b.not_ready = "no_game";
    d.Tick();
    text = d.Wait(quit, 1000ms);
    REQUIRE(text);
    CHECK(canon(*text) == spy::dump_json(golden(44)));
}

// ---- events.* answered by the EventHub, the consumer beside the request text ----------------------------
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
    // Consumer 0 with no hub (the bridge's fallback for a build without events): unsupported comes before the consumer.
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

TEST_CASE("CurrentRequestId is the request's id inside Backend::Run and 0 outside it")
{
    struct IdBackend : spy::Backend
    {
        std::uint64_t seen = 99;
        void Run(const std::string&, const json&, spy::Done done, spy::Cancelled, spy::Progress) override
        {
            seen = spy::CurrentRequestId();
            done(OpResult::Ok(nullptr));
        }
        json BaseState() override { return json::object(); }
        std::optional<std::string> NotReadyReason(bool) override { return std::nullopt; }
        std::uint32_t LoadsStarted() override { return 0; }
    } backend;
    spy::Dispatcher d(backend, {});
    CHECK(spy::CurrentRequestId() == 0);
    const auto t = d.Submit(call_text(4242));
    CHECK(backend.seen == 4242);
    CHECK(spy::CurrentRequestId() == 0);  // reset when Run returns
    CHECK(code_of(wait_json(d, t)) == "ok");
    CHECK(spy::NowUs() > 0);
}
