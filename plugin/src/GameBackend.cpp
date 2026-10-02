#include "GameBackend.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Settings.h"

namespace spy::game {
namespace {
// INTERIM (Task 26): game.state from the lifecycle, `unsupported` for everything else. Replaced in Task 27.
class InterimBackend final : public spy::Backend
{
public:
    void Run(const std::string& op, const json&, Done done, Cancelled, Progress) override
    {
        if (op == "game.state") return done(OpResult::Ok(life::BaseState()));
        done(OpResult::Err(ErrorCode::unsupported, op + " is not served yet (the game backend arrives in Task 27)"));
    }
    json BaseState() override { return life::BaseState(); }
    std::optional<std::string> NotReadyReason(bool require_unpaused) override { return life::NotReadyReason(require_unpaused); }
    std::uint32_t LoadsStarted() override { return life::LoadsStarted(); }
};
}

spy::Backend& Backend()
{
    static InterimBackend& b = *new InterimBackend();
    return b;
}

spy::Dispatcher& GetDispatcher()
{
    static Dispatcher& d = []() -> Dispatcher& {
        const auto& c = settings::Get();
        auto* p = new Dispatcher(Backend(), DispatcherOptions{ c.default_timeout_ms, c.max_inflight_total });
        p->StartReaper();
        log::Info("game backend: interim dispatcher ready (game.state only)");
        return *p;
    }();
    return d;
}
}
