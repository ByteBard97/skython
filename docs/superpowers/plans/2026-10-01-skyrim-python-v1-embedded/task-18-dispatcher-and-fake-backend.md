# Task 18: Dispatcher and fake backend

New in the embedded design (no counterpart on branch `plan-out-of-process`). It takes over the request rules that the old plan's IO thread and `plugin/src/Dispatch.cpp` (old Task 17a) applied: busy caps, readiness answers, deadlines, load cancellation, exception containment. They now live in the Linux-tested core, behind the `Backend` interface, so the game (Task 27) and the fake (this task) share them.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts** (the `dispatcher.h` and `fake_backend.h` entries are this task's interface). Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2, 4.5). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Tasks 16 (`parse_request_text`, `peek_request_id`, `make_ok`, `make_error`, `is_uint`) and 17 (only for the shape of `base_state()`, which `ManualBackend` and `FakeBackend` imitate), and **spike S8 PASS** (Task 3; the index Gates). Linux only; no game.

**What the Dispatcher does.** `Submit(text)` parses the request once (`parse_request_text`); a request that fails gets a ticket that is already done, with `bad_request` (or `type_mismatch`) and the id `peek_request_id` found (0 if none). An op whose generated `OpSpec` has `needs_game` is answered `not_ready` at once, with `reason`, `paused` and `menus` in the detail, whenever `Backend::NotReadyReason(require_unpaused)` has a value, so a call made at the main menu or during a load never waits (Review Focus 2). Over `max_inflight_total` calls in flight the answer is `busy`. Otherwise the request gets a deadline (its `timeout_ms`, else `default_timeout_ms`) and goes to `Backend::Run` with three callbacks: `done` (first call wins; later ones, and any for a cancelled or consumed ticket, are counted in `LateResults()`), `cancelled` and `progress` (a batch's `{"completed", "results"}` so far). `Tick()`, called every 50 ms by the reaper thread, ends calls past their deadline with `timeout` (detail `paused`, `menus`, and for a batch `completed`/`results` from the last progress) and, when `LoadsStarted()` has changed since the last tick, every in-flight `needs_game` call with `not_ready` reason `loading`. `Backend::Run` and `done` never run under the Dispatcher's lock, so a backend may answer inside `Run` (the fake and the game answer `game.state` that way). The ticket table lives in a `Shared` object that `done` reaches through a `weak_ptr`, so a result that arrives after the Dispatcher is destroyed is dropped safely.

**What `FakeBackend` is for.** It is the backend of the Linux `_skyrim` build (Task 19), and through that of the Python tests above it (Tasks 21-24). Its `Fake` script functions cover the cases those tests need: a value round trip (`Echo`), a slow call that does not hold up others (`Sleep`), every error code (`Fail`), an exception (`Throw`), a call that never answers (`Never`) and a call counter (`Count`). Its controls stand in for the lifecycle: `SetNotReady`, `SetPaused`, `BumpLoads`.

**Files:**
- Create: `core/include/spy/dispatcher.h`, `core/src/dispatcher.cpp`, `core/include/spy/fake_backend.h`, `core/src/fake_backend.cpp`
- Create: `core/tests/test_dispatcher.cpp`, `core/tests/test_fake_backend.cpp`, `core/tests/support/tsan_gcc11.h` (ThreadSanitizer build only)
- `core/CMakeLists.txt` needs no change: it globs `src/*.cpp` and `tests/*.cpp`, builds position-independent code and links `Threads::Threads` (Task 13).

**Interfaces:**
- Consumes: `spy::parse_request_text`, `spy::peek_request_id`, `spy::make_ok`, `spy::make_error`, `spy::dump_json`, `spy::is_uint`, `spy::ProtocolError`, `spy::error_code_from_string` (Tasks 14, 16); the generated `spy::find_op`, `OpSpec::needs_game`, `spy::ops::kGameState`, `kPapyrusCallStatic`, `kPapyrusBatch`, `spy::kMaxTimeoutMs` (Task 12); the fixtures `requests.json` and `responses.json` (Task 11).
- Produces (exactly the index Contracts, plus one addition marked below):
  - `struct spy::OpResult` with `Ok(json)` and `Err(ErrorCode, std::string, json detail = object)`; `spy::Done`, `spy::Cancelled`, `spy::Progress`
  - `class spy::Backend` with `Run`, `BaseState`, `NotReadyReason(bool)`, `LoadsStarted`
  - `using spy::Ticket = std::uint64_t`; `struct spy::DispatcherOptions { default_timeout_ms = 5000; max_inflight_total = 1024; }`
  - `class spy::Dispatcher(Backend&, DispatcherOptions)`: `Submit`, `Wait`, `WaitAny`, `Cancel`, `Tick`, `StartReaper(period = 50 ms)`, `InFlight`, `LateResults`, and **`json BaseState()`** (added: the backend's `BaseState()`, which `_skyrim.state()` and `_skyrim.call()`'s late-cancel reply need; `ModuleHost` holds only the Dispatcher)
  - `class spy::FakeBackend : Backend` with `SetNotReady(std::optional<std::string>)`, `BumpLoads()`, `SetPaused(bool)` and `RunCount()` (added for the `Count` test)
  - Response texts are `{"type":"response","id":...,"ok":...,"result"|"error"}` as built by `make_ok` / `make_error`. Not-ready messages: `starting` "the game is still starting", `loading` "a game is loading" (the `responses.json` fixture), `no_game` "no game is loaded", `paused` "the game is paused".

- [ ] **Step 1: Write the failing tests**

`ManualBackend` lets the test decide when each call finishes, so every Dispatcher rule is checked without timing guesses; the fake backend's own tests and the fixture replay use `FakeBackend`.

`core/tests/test_dispatcher.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/dispatcher.h"
#include <atomic>
#include <chrono>
#include <mutex>
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
    bool answer_inline = false;  // call done(Ok) from inside Run (what a game.state answer does)

    void Run(const std::string& op, const json& args, spy::Done done, spy::Cancelled cancelled, spy::Progress progress) override
    {
        if (throw_in_run) throw std::runtime_error("backend exploded");
        if (answer_inline) { done(OpResult::Ok({{"inline", op}})); return; }
        std::lock_guard lk(mx);
        calls.push_back({op, args, std::move(done), std::move(cancelled), std::move(progress)});
    }
    json BaseState() override
    {
        return {{"data_ready", true}, {"game_loaded", true}, {"loading", false}, {"interactive", true},
                {"paused", paused}, {"menus", paused ? json::array({"Journal Menu"}) : json::array()}, {"epoch", 1}};
    }
    std::optional<std::string> NotReadyReason(bool require_unpaused) override
    {
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
    d.Tick();
    auto r = wait_json(d, call);
    CHECK(code_of(r) == "not_ready");
    CHECK(r["error"]["detail"]["reason"] == "loading");
    CHECK(b.take(0).cancelled());
    CHECK_FALSE(d.Wait(state, 0ms).has_value());
    b.take(1).done(OpResult::Ok(json::object()));
    CHECK(code_of(wait_json(d, state)) == "ok");
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

TEST_CASE("tickets are never reused, also after they are consumed or cancelled")
{
    ManualBackend b;
    spy::Dispatcher d(b, {});
    std::vector<spy::Ticket> seen;
    for (int i = 0; i < 50; ++i) {
        auto t = d.Submit(call_text(static_cast<std::uint64_t>(i)));
        for (auto s : seen) CHECK(s != t);
        seen.push_back(t);
        if (i % 2) d.Cancel(t);
    }
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
    late(OpResult::Ok(1));  // must not touch freed memory (ASan run, Step 5)
}

TEST_CASE("many threads submitting and waiting at once (run under ThreadSanitizer in Step 5)")
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
```

`core/tests/test_fake_backend.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/dispatcher.h"
#include "spy/fake_backend.h"
#include <atomic>
#include <chrono>
#include <fstream>
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
        CHECK(code_of(r) == (m["op"] == "game.state" ? "ok" : "unsupported"));
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
        if (m["op"] == "game.state") { CHECK(code_of(r) == "ok"); continue; }
        CHECK(r["error"]["code"] == expected["code"]);
        CHECK(r["error"]["message"] == expected["message"]);
        CHECK(r["error"]["detail"]["reason"] == expected["detail"]["reason"]);
    }
}

TEST_CASE("many threads against the fake backend (run under ThreadSanitizer in Step 5)")
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | grep -m1 "fatal error"`
Expected: FAIL, `.../core/tests/test_dispatcher.cpp:2:10: fatal error: spy/dispatcher.h: No such file or directory`.

- [ ] **Step 3: Implement the Dispatcher**

`core/include/spy/dispatcher.h`:
```cpp
#pragma once
#include "spy/requests.h"  // json, ErrorCode, make_ok, make_error, parse_request_text
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace spy {
// The result of one op, as a backend reports it.
struct OpResult
{
    bool ok = true;
    json result;
    ErrorCode code = ErrorCode::internal;
    std::string message;
    json detail = json::object();
    static OpResult Ok(json result) { OpResult r; r.result = std::move(result); return r; }
    static OpResult Err(ErrorCode code, std::string message, json detail = json::object())
    {
        OpResult r;
        r.ok = false;
        r.code = code;
        r.message = std::move(message);
        r.detail = std::move(detail);
        return r;
    }
};
using Done = std::function<void(OpResult)>;  // call exactly once, from any thread; later calls are counted as late
using Cancelled = std::function<bool()>;     // true once the request timed out, was cancelled, or a load started
using Progress = std::function<void(json)>;  // batch: {"completed": n, "results": [...]} so far, any thread

// What runs ops: the game (plugin, Task 27) or the fake (Linux tests, below in fake_backend.h).
class Backend
{
public:
    virtual ~Backend() = default;
    // Must not block the caller. May call done synchronously (game.state does).
    virtual void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) = 0;
    virtual json BaseState() = 0;                                               // any thread, never blocks
    virtual std::optional<std::string> NotReadyReason(bool require_unpaused) = 0;  // any thread
    virtual std::uint32_t LoadsStarted() = 0;                                   // any thread
};

using Ticket = std::uint64_t;
struct DispatcherOptions
{
    std::uint32_t default_timeout_ms = 5000;
    std::uint32_t max_inflight_total = 1024;
};

// Turns request text into backend calls and backend results into response text (spec 4.2, 4.5). Thread-safe.
// Rules (index Contracts): bad text -> bad_request at once; a needs_game op while NotReadyReason() has a value ->
// not_ready at once; more than max_inflight_total in flight -> busy; past its deadline -> timeout; a load starting
// -> not_ready "loading" for every in-flight needs_game call; an exception from Run -> internal. Backend::Run and
// done callbacks never run while the dispatcher's lock is held.
class Dispatcher
{
public:
    Dispatcher(Backend& backend, DispatcherOptions options);
    ~Dispatcher();  // stops the reaper; results that arrive later are dropped safely
    Dispatcher(const Dispatcher&) = delete;
    Dispatcher& operator=(const Dispatcher&) = delete;

    Ticket Submit(std::string_view request_text);  // never throws; a refused request yields a ticket that is already done
    // Response text {type, id, ok, result|error}; nullopt if not done within max_wait, or unknown. A returned ticket is consumed.
    std::optional<std::string> Wait(Ticket ticket, std::chrono::milliseconds max_wait);
    // The finished ones among `tickets` (consumed), as soon as at least one is finished or max_wait passes.
    std::vector<std::pair<Ticket, std::string>> WaitAny(const std::vector<Ticket>& tickets, std::chrono::milliseconds max_wait);
    void Cancel(Ticket ticket);  // erases the ticket; a later Wait returns nullopt; a late result is dropped and counted
    void Tick();                 // deadlines and load cancellation; the reaper calls it, tests call it directly
    void StartReaper(std::chrono::milliseconds period = std::chrono::milliseconds(50));  // a second call is a no-op
    json BaseState();            // the backend's BaseState(): _skyrim.state() and late_cancel replies use it
    std::size_t InFlight() const;
    std::uint64_t LateResults() const;

    struct Shared;  // the ticket table; results hold it by shared_ptr so a late done never touches a dead Dispatcher

private:
    Backend& backend_;
    DispatcherOptions options_;
    std::shared_ptr<Shared> shared_;
    std::uint32_t loads_seen_;
    std::mutex tick_mx_;  // one Tick at a time (reaper and tests)

    std::mutex reaper_mx_;
    std::condition_variable reaper_cv_;
    bool reaper_stop_ = false;
    std::thread reaper_;
};
}
```

`core/src/dispatcher.cpp`:
```cpp
#include "spy/dispatcher.h"
#include <atomic>
#include <unordered_map>

namespace spy {
using Clock = std::chrono::steady_clock;

namespace {
struct Entry
{
    std::uint64_t id = 0;
    bool needs_game = true;
    bool batch = false;
    std::uint32_t timeout_ms = 0;
    Clock::time_point deadline;
    bool done = false;
    std::string text;                                                  // the response, once done
    json progress = json::object();                                    // batch: the last Progress payload
    std::shared_ptr<std::atomic<bool>> cancelled = std::make_shared<std::atomic<bool>>(false);
};

std::string not_ready_message(const std::string& reason)
{
    if (reason == "starting") return "the game is still starting";
    if (reason == "loading") return "a game is loading";
    if (reason == "no_game") return "no game is loaded";
    if (reason == "paused") return "the game is paused";
    return "the game is not ready";
}

// paused and menus from a BaseState() snapshot, for every not_ready and timeout detail (spec 4.1).
json game_detail(const json& base)
{
    json d = json::object();
    d["paused"] = base.is_object() && base.contains("paused") ? base["paused"] : json(false);
    d["menus"] = base.is_object() && base.contains("menus") ? base["menus"] : json::array();
    return d;
}

std::string error_text(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object())
{
    return dump_json(make_error(id, code, message, std::move(detail)));
}
}

struct Dispatcher::Shared
{
    mutable std::mutex mx;
    std::condition_variable cv;
    std::unordered_map<Ticket, Entry> entries;
    Ticket next = 1;
    std::size_t in_flight = 0;
    std::uint64_t late = 0;

    // A finished ticket that never reached the backend (bad request, not_ready, busy).
    Ticket AddDone(std::string text)
    {
        std::lock_guard lk(mx);
        Ticket t = next++;
        Entry e;
        e.done = true;
        e.text = std::move(text);
        entries.emplace(t, std::move(e));
        cv.notify_all();
        return t;
    }

    // Marks a ticket done with `text` unless it is gone or already done (then the result is late). Returns true if it won.
    bool Finish(Ticket t, std::string text)
    {
        std::lock_guard lk(mx);
        auto it = entries.find(t);
        if (it == entries.end() || it->second.done) { ++late; return false; }
        it->second.done = true;
        it->second.text = std::move(text);
        it->second.cancelled->store(true);  // nothing more should run for it
        --in_flight;
        cv.notify_all();
        return true;
    }
};

Dispatcher::Dispatcher(Backend& backend, DispatcherOptions options)
    : backend_(backend), options_(options), shared_(std::make_shared<Shared>()), loads_seen_(backend.LoadsStarted())
{
}

Dispatcher::~Dispatcher()
{
    {
        std::lock_guard lk(reaper_mx_);
        reaper_stop_ = true;
    }
    reaper_cv_.notify_all();
    if (reaper_.joinable()) reaper_.join();
}

Ticket Dispatcher::Submit(std::string_view request_text)
{
    Request req;
    try {
        req = parse_request_text(request_text);
    } catch (const ProtocolError& e) {
        return shared_->AddDone(error_text(peek_request_id(request_text).value_or(0), e.code, e.what()));
    } catch (const std::exception& e) {
        return shared_->AddDone(error_text(peek_request_id(request_text).value_or(0), ErrorCode::internal, e.what()));
    }

    const OpSpec* spec = find_op(req.op);  // parse_request_text accepted it, so it exists
    const bool needs_game = spec == nullptr || spec->needs_game;
    if (needs_game) {
        const bool require_unpaused = req.args.contains("require_unpaused") && req.args["require_unpaused"].is_boolean() &&
                                      req.args["require_unpaused"].get<bool>();
        std::optional<std::string> reason;
        json base;
        try {
            reason = backend_.NotReadyReason(require_unpaused);
            if (reason) base = backend_.BaseState();
        } catch (const std::exception& e) {
            return shared_->AddDone(error_text(req.id, ErrorCode::internal, e.what()));
        }
        if (reason) {
            json detail = game_detail(base);
            detail["reason"] = *reason;
            return shared_->AddDone(error_text(req.id, ErrorCode::not_ready, not_ready_message(*reason), std::move(detail)));
        }
    }

    std::uint32_t timeout_ms = options_.default_timeout_ms;
    if (req.args.contains("timeout_ms") && is_uint(req.args["timeout_ms"], kMaxTimeoutMs))
        timeout_ms = req.args["timeout_ms"].get<std::uint32_t>();

    Ticket ticket;
    std::shared_ptr<std::atomic<bool>> cancelled;
    {
        std::lock_guard lk(shared_->mx);
        if (shared_->in_flight >= options_.max_inflight_total) {
            ticket = shared_->next++;
            Entry e;
            e.done = true;
            e.text = error_text(req.id, ErrorCode::busy,
                                "too many calls in flight (" + std::to_string(options_.max_inflight_total) + ")");
            shared_->entries.emplace(ticket, std::move(e));
            shared_->cv.notify_all();
            return ticket;
        }
        ticket = shared_->next++;
        Entry e;
        e.id = req.id;
        e.needs_game = needs_game;
        e.batch = req.op == ops::kPapyrusBatch;
        e.timeout_ms = timeout_ms;
        e.deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
        cancelled = e.cancelled;
        shared_->entries.emplace(ticket, std::move(e));
        ++shared_->in_flight;
    }

    // Everything below runs without the lock: the backend may call done (or progress) synchronously.
    std::weak_ptr<Shared> weak = shared_;
    const std::uint64_t id = req.id;
    Done done = [weak, ticket, id](OpResult r) {
        auto s = weak.lock();
        if (!s) return;  // the Dispatcher is gone
        std::string text;
        try {
            text = dump_json(r.ok ? make_ok(id, std::move(r.result)) : make_error(id, r.code, r.message, std::move(r.detail)));
        } catch (const std::exception& e) {
            text = error_text(id, ErrorCode::internal, std::string("could not encode the result: ") + e.what());
        }
        s->Finish(ticket, std::move(text));
    };
    Cancelled is_cancelled = [cancelled] { return cancelled->load(); };
    Progress progress = [weak, ticket](json p) {
        auto s = weak.lock();
        if (!s) return;
        std::lock_guard lk(s->mx);
        auto it = s->entries.find(ticket);
        if (it != s->entries.end() && !it->second.done) it->second.progress = std::move(p);
    };
    try {
        backend_.Run(req.op, req.args, done, is_cancelled, progress);
    } catch (const std::exception& e) {
        shared_->Finish(ticket, error_text(id, ErrorCode::internal, std::string("backend error: ") + e.what()));
    } catch (...) {
        shared_->Finish(ticket, error_text(id, ErrorCode::internal, "backend error (unknown exception)"));
    }
    return ticket;
}

std::optional<std::string> Dispatcher::Wait(Ticket ticket, std::chrono::milliseconds max_wait)
{
    std::unique_lock lk(shared_->mx);
    auto ready = [&] {
        auto it = shared_->entries.find(ticket);
        return it == shared_->entries.end() || it->second.done;
    };
    shared_->cv.wait_for(lk, max_wait, ready);
    auto it = shared_->entries.find(ticket);
    if (it == shared_->entries.end() || !it->second.done) return std::nullopt;
    std::string text = std::move(it->second.text);
    shared_->entries.erase(it);
    return text;
}

std::vector<std::pair<Ticket, std::string>> Dispatcher::WaitAny(const std::vector<Ticket>& tickets,
                                                               std::chrono::milliseconds max_wait)
{
    std::vector<std::pair<Ticket, std::string>> out;
    std::unique_lock lk(shared_->mx);
    auto ready = [&] {
        bool any_known = false;
        for (Ticket t : tickets) {
            auto it = shared_->entries.find(t);
            if (it == shared_->entries.end()) continue;
            if (it->second.done) return true;
            any_known = true;
        }
        return !any_known;  // nothing left to wait for
    };
    shared_->cv.wait_for(lk, max_wait, ready);
    for (Ticket t : tickets) {
        auto it = shared_->entries.find(t);
        if (it == shared_->entries.end() || !it->second.done) continue;
        out.emplace_back(t, std::move(it->second.text));
        shared_->entries.erase(it);
    }
    return out;
}

void Dispatcher::Cancel(Ticket ticket)
{
    std::lock_guard lk(shared_->mx);
    auto it = shared_->entries.find(ticket);
    if (it == shared_->entries.end()) return;
    if (!it->second.done) {
        it->second.cancelled->store(true);
        --shared_->in_flight;
    }
    shared_->entries.erase(it);
    shared_->cv.notify_all();
}

void Dispatcher::Tick()
{
    std::lock_guard tick_lk(tick_mx_);
    const std::uint32_t loads = backend_.LoadsStarted();
    const bool load_started = loads != loads_seen_;
    loads_seen_ = loads;

    struct Expired { Ticket ticket; std::uint64_t id; bool load; bool batch; std::uint32_t timeout_ms; json progress; };
    std::vector<Expired> expired;
    {
        std::lock_guard lk(shared_->mx);
        const auto now = Clock::now();
        for (auto& [t, e] : shared_->entries) {
            if (e.done) continue;
            if (load_started && e.needs_game) expired.push_back({t, e.id, true, e.batch, e.timeout_ms, json()});
            else if (now >= e.deadline) expired.push_back({t, e.id, false, e.batch, e.timeout_ms, e.progress});
        }
    }
    if (expired.empty()) return;

    json base;
    try {
        base = backend_.BaseState();
    } catch (...) {
        base = json::object();
    }
    for (auto& x : expired) {
        json detail = game_detail(base);
        std::string text;
        if (x.load) {
            detail["reason"] = "loading";
            text = error_text(x.id, ErrorCode::not_ready, "a save started loading while the call was in flight", std::move(detail));
        } else {
            if (x.batch) {
                const bool has = x.progress.is_object() && x.progress.contains("completed");
                detail["completed"] = has ? x.progress["completed"] : json(0);
                detail["results"] = has && x.progress.contains("results") ? x.progress["results"] : json::array();
            }
            text = error_text(x.id, ErrorCode::timeout, "no result within " + std::to_string(x.timeout_ms) + " ms", std::move(detail));
        }
        shared_->Finish(x.ticket, std::move(text));
    }
}

void Dispatcher::StartReaper(std::chrono::milliseconds period)
{
    std::lock_guard lk(reaper_mx_);
    if (reaper_.joinable() || reaper_stop_) return;
    reaper_ = std::thread([this, period] {
        std::unique_lock rl(reaper_mx_);
        while (!reaper_stop_) {
            reaper_cv_.wait_for(rl, period, [this] { return reaper_stop_; });
            if (reaper_stop_) break;
            rl.unlock();
            try {
                Tick();
            } catch (...) {
                // Never let an exception end the process from this thread (Global Constraints).
            }
            rl.lock();
        }
    });
}

json Dispatcher::BaseState()
{
    return backend_.BaseState();
}

std::size_t Dispatcher::InFlight() const
{
    std::lock_guard lk(shared_->mx);
    return shared_->in_flight;
}

std::uint64_t Dispatcher::LateResults() const
{
    std::lock_guard lk(shared_->mx);
    return shared_->late;
}
}
```

- [ ] **Step 4: Implement the fake backend**

`Sleep` schedules its answer on the worker instead of sleeping on it, so a slow fake call never holds up a fast one (the 16-thread tests in Task 19 depend on that).

`core/include/spy/fake_backend.h`:
```cpp
#pragma once
#include "spy/dispatcher.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <string>
#include <thread>

namespace spy {
// A Backend with no game, for Linux tests of the Dispatcher, the _skyrim module (Task 19) and everything above it.
// papyrus.call_static on script "Fake":
//   Echo(x) -> x          Sleep(ms) -> ms, after ms milliseconds (other calls are not held up)
//   Fail(code, message) -> that error (an unknown code is internal)
//   Throw() -> Run throws std::runtime_error      Never() -> never answers      Count() -> number of Run calls so far
// game.state -> BaseState() plus "backend": "fake", answered inside Run.
// Any other function -> unknown_function; any other script or op -> unsupported.
class FakeBackend : public Backend
{
public:
    FakeBackend();
    ~FakeBackend() override;  // stops the worker; queued answers are dropped
    FakeBackend(const FakeBackend&) = delete;
    FakeBackend& operator=(const FakeBackend&) = delete;

    void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) override;
    json BaseState() override;
    std::optional<std::string> NotReadyReason(bool require_unpaused) override;
    std::uint32_t LoadsStarted() override;

    void SetNotReady(std::optional<std::string> reason);  // "starting", "loading", "no_game", or nullopt for ready
    void BumpLoads();                                      // a save started loading
    void SetPaused(bool paused);
    std::uint64_t RunCount() const;

private:
    void Schedule(std::chrono::steady_clock::time_point when, std::function<void()> job);
    void Worker();

    mutable std::mutex mx_;
    std::condition_variable cv_;
    std::multimap<std::chrono::steady_clock::time_point, std::function<void()>> jobs_;
    bool stop_ = false;
    std::optional<std::string> not_ready_;
    bool paused_ = false;
    std::atomic<std::uint32_t> loads_{0};
    std::atomic<std::uint64_t> runs_{0};
    std::thread worker_;
};
}
```

`core/src/fake_backend.cpp`:
```cpp
#include "spy/fake_backend.h"
#include <stdexcept>

namespace spy {
using Clock = std::chrono::steady_clock;

FakeBackend::FakeBackend() : worker_([this] { Worker(); }) {}

FakeBackend::~FakeBackend()
{
    {
        std::lock_guard lk(mx_);
        stop_ = true;
    }
    cv_.notify_all();
    worker_.join();
}

void FakeBackend::Schedule(Clock::time_point when, std::function<void()> job)
{
    {
        std::lock_guard lk(mx_);
        jobs_.emplace(when, std::move(job));
    }
    cv_.notify_all();
}

// Runs each job when it is due, outside the lock, so a job may call back into the Dispatcher.
void FakeBackend::Worker()
{
    std::unique_lock lk(mx_);
    while (!stop_) {
        if (jobs_.empty()) {
            cv_.wait(lk, [this] { return stop_ || !jobs_.empty(); });
            continue;
        }
        auto first = jobs_.begin();
        if (first->first > Clock::now()) {
            cv_.wait_until(lk, first->first);
            continue;
        }
        auto job = std::move(first->second);
        jobs_.erase(first);
        lk.unlock();
        try {
            job();
        } catch (...) {
            // a job must never end the worker
        }
        lk.lock();
    }
}

void FakeBackend::Run(const std::string& op, const json& args, Done done, Cancelled /*cancelled*/, Progress /*progress*/)
{
    const auto n = ++runs_;
    if (op == ops::kGameState) {
        json state = BaseState();
        state["backend"] = "fake";
        done(OpResult::Ok(std::move(state)));
        return;
    }
    const std::string script = args.contains("script") && args["script"].is_string() ? args["script"].get<std::string>() : "";
    if (op != ops::kPapyrusCallStatic || script != "Fake") {
        Schedule(Clock::now(), [done, op] { done(OpResult::Err(ErrorCode::unsupported, "fake backend: " + op + " is not supported")); });
        return;
    }
    const std::string fn = args["function"].get<std::string>();
    const json a = args.contains("args") ? args["args"] : json::array();
    auto arg = [&](std::size_t i) { return i < a.size() ? a[i] : json(); };

    if (fn == "Throw") throw std::runtime_error("Fake.Throw");
    if (fn == "Never") return;
    if (fn == "Echo") {
        Schedule(Clock::now(), [done, v = arg(0)] { done(OpResult::Ok(v)); });
    } else if (fn == "Sleep") {
        const json ms = arg(0);
        const auto delay = ms.is_number_integer() && ms.get<std::int64_t>() > 0 ? ms.get<std::int64_t>() : 0;
        Schedule(Clock::now() + std::chrono::milliseconds(delay), [done, ms] { done(OpResult::Ok(ms)); });
    } else if (fn == "Fail") {
        const json c = arg(0), m = arg(1);
        auto code = c.is_string() ? error_code_from_string(c.get<std::string>()) : std::nullopt;
        std::string message = m.is_string() ? m.get<std::string>() : "Fake.Fail";
        Schedule(Clock::now(), [done, code, message] { done(OpResult::Err(code.value_or(ErrorCode::internal), message)); });
    } else if (fn == "Count") {
        Schedule(Clock::now(), [done, n] { done(OpResult::Ok(n)); });
    } else {
        Schedule(Clock::now(), [done, fn] { done(OpResult::Err(ErrorCode::unknown_function, "Fake has no function " + fn)); });
    }
}

json FakeBackend::BaseState()
{
    std::lock_guard lk(mx_);
    const bool ready = !not_ready_.has_value();
    return {{"data_ready", not_ready_ != "starting"},
            {"game_loaded", ready},
            {"loading", not_ready_ == "loading"},
            {"interactive", ready},
            {"paused", paused_},
            {"menus", json::array()},
            {"epoch", loads_.load()}};
}

std::optional<std::string> FakeBackend::NotReadyReason(bool require_unpaused)
{
    std::lock_guard lk(mx_);
    if (not_ready_) return not_ready_;
    if (require_unpaused && paused_) return std::string("paused");
    return std::nullopt;
}

std::uint32_t FakeBackend::LoadsStarted() { return loads_.load(); }

void FakeBackend::SetNotReady(std::optional<std::string> reason)
{
    std::lock_guard lk(mx_);
    not_ready_ = std::move(reason);
}

void FakeBackend::BumpLoads() { ++loads_; }

void FakeBackend::SetPaused(bool paused)
{
    std::lock_guard lk(mx_);
    paused_ = paused;
}

std::uint64_t FakeBackend::RunCount() const { return runs_.load(); }
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `cmake --build core/build -j && core/build/spy_core_tests | tail -3`
Expected: PASS, `[doctest] test cases:  106 |  106 passed | 0 failed | 0 skipped` and `[doctest] assertions: 2339 | 2339 passed | 0 failed |` (Tasks 14-18 together).

- [ ] **Step 6: Warnings and sanitizers, then ThreadSanitizer**

Run: `cmake --build core/build/asan -j 2>&1 | grep -c "warning:" ; core/build/asan/spy_core_tests | tail -3`
Expected: `0`, then the counts of Step 5 with no AddressSanitizer or UndefinedBehaviorSanitizer report (the "result after the Dispatcher is gone" test is the use-after-free check). If `core/build/asan` does not exist yet, configure it as Task 16 Step 5 does.

GCC 11's libstdc++ implements a `condition_variable` wait with a `steady_clock` deadline (every `wait_for` here) with `pthread_cond_clockwait`, and GCC 11's ThreadSanitizer does not intercept that function: without a fix it reports a false "double lock of a mutex" and "data race" for every timed wait. The fix is a header force-included into the ThreadSanitizer build only, which makes libstdc++ use the intercepted `pthread_cond_timedwait`:

`core/tests/support/tsan_gcc11.h`:
```cpp
// Force-included (-include) into the ThreadSanitizer build only (Task 18 Step 5).
// GCC 11's libstdc++ waits on a condition_variable with a steady_clock deadline through pthread_cond_clockwait, which
// GCC 11's libtsan does not intercept. TSan then misses the unlock inside the wait and reports false "double lock of a
// mutex" and "data race" warnings for every timed wait. Turning the macro off makes libstdc++ use
// pthread_cond_timedwait, which libtsan does intercept. Production builds never include this file.
#pragma once
#include <bits/c++config.h>
#undef _GLIBCXX_USE_PTHREAD_COND_CLOCKWAIT
```

Run: `cmake -S core -B core/build/tsan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-fsanitize=thread -include $PWD/core/tests/support/tsan_gcc11.h" >/dev/null && cmake --build core/build/tsan -j >/dev/null && setarch "$(uname -m)" -R core/build/tsan/spy_core_tests 2>&1 | grep -E "WARNING|test cases"`
Expected: only `[doctest] test cases:  106 |  106 passed | 0 failed | 0 skipped`; no `WARNING: ThreadSanitizer` line. (Run from the repository root; `setarch -R` as in Task 17.) If you see `double lock of a mutex`, the `-include` flag did not reach the compiler: run the configure line again exactly as written (Task 17 configured the same build directory without it).

- [ ] **Step 7: Commit**

```bash
git add core/include/spy/dispatcher.h core/src/dispatcher.cpp core/include/spy/fake_backend.h core/src/fake_backend.cpp \
        core/tests/test_dispatcher.cpp core/tests/test_fake_backend.cpp core/tests/support/tsan_gcc11.h
git commit -m "feat(core): request dispatcher with readiness, caps, deadlines and load cancellation; fake backend"
```
