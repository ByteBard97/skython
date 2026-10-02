#include "GameBackend.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Settings.h"
#include <SKSE/SKSE.h>
#include <mutex>

namespace spy::game {
namespace {
OpResult Internal() { return OpResult::Err(ErrorCode::internal, "internal error"); }

class GameBackend final : public spy::Backend
{
public:
    void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) override
    {
        if (op == "game.state") return State(std::move(done), std::move(cancelled));
        // Exactly one AddTask; never block the submitting Python thread.
        SKSE::GetTaskInterface()->AddTask([op, args, done, cancelled, progress] {
            ops::InTask in;
            ops::Measured measured;  // Task 31 drain meter: before the cancelled() check, a cancelled body still ran
            if (cancelled()) return;  // timed out, cancelled or a load started while queued
            try {
                ops::Run(op, args, done, cancelled, progress);
            } catch (const std::exception& e) {
                log::Error("game backend: " + op + " threw out of Run: " + e.what());
                done(Internal());
            } catch (...) {
                log::Error("game backend: " + op + " threw an unknown exception out of Run");
                done(Internal());
            }
        });
    }

    // No player / game_days_passed here: BaseState never blocks, so it cannot queue a task and wait (index Contracts).
    json BaseState() override { return WithDiagnostics(life::BaseState()); }

    std::optional<std::string> NotReadyReason(bool require_unpaused) override { return life::NotReadyReason(require_unpaused); }

    std::uint32_t LoadsStarted() override { return life::LoadsStarted(); }

private:
    static json WithDiagnostics(json s)
    {
        auto& d = GetDispatcher();
        s["diagnostics"] = {{"late_results_dropped", d.LateResults()}, {"in_flight", d.InFlight()},
                            {"max_task_drain_ms", ops::Meter().MaxMs()}, {"max_task_drain_count", ops::Meter().MaxCount()}};
        return s;
    }

    // The game.state op: BaseState() plus player and game_days_passed (null until AddStateExtras fills them).
    void State(Done done, Cancelled cancelled)
    {
        json s = WithDiagnostics(life::BaseState());
        s["player"] = nullptr;
        s["game_days_passed"] = nullptr;
        // Answer at once at the main menu or while loading; queue a task only when it can answer.
        if (!life::GameLoaded() || life::Loading()) return done(OpResult::Ok(std::move(s)));
        SKSE::GetTaskInterface()->AddTask([done, cancelled, s = std::move(s)]() mutable {
            ops::InTask in;
            ops::Measured measured;  // Task 31 drain meter: before the cancelled() check, a cancelled body still ran
            if (cancelled()) return;
            try {
                ops::AddStateExtras(s);
                done(OpResult::Ok(std::move(s)));
            } catch (const std::exception& e) {
                log::Error(std::string("game backend: game.state threw: ") + e.what());
                done(Internal());
            } catch (...) {
                done(Internal());
            }
        });
    }
};
}

spy::Backend& Backend()
{
    static auto* backend = new GameBackend();  // immortal
    return *backend;
}

spy::Dispatcher& GetDispatcher()
{
    static spy::Dispatcher* dispatcher = [] {
        const auto& cfg = settings::Get();
        spy::DispatcherOptions opts;
        opts.default_timeout_ms = cfg.default_timeout_ms;
        opts.max_inflight_total = cfg.max_inflight_total;
        opts.on_late = [](const std::string& line) { log::Warn("game backend: " + line); };  // a result nobody waits for
        auto* d = new spy::Dispatcher(Backend(), opts);
        d->StartReaper();
        log::Info("game backend: dispatcher ready (default timeout " + std::to_string(cfg.default_timeout_ms) +
                  " ms, max in flight " + std::to_string(cfg.max_inflight_total) + ")");
        return d;
    }();
    return *dispatcher;
}
}
