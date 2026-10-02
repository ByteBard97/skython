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
}
