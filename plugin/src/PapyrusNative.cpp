#include "PapyrusNative.h"
#include "Events.h"
#include "Log.h"
#include "spy/channel.h"
#include "spy/protocol_generated.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <string>
#include <utility>

using namespace std::literals;

namespace spy::native {
namespace {
std::atomic_bool g_badChannelLogged{false};

// Runs on a Papyrus VM thread (or inside an SKSE task: never assume either, R49). Copies, pushes, returns.
void Send(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    try {
        const char* c = channel.c_str();
        const std::string_view ch = c ? std::string_view(c) : ""sv;
        // A bad channel is a script bug: warn once whether or not anyone is subscribed, so it is seen during
        // development even before a subscriber exists. The check is a length test: no copy, no JSON.
        if (ch.empty() || ch.size() > spy::kMaxChannelBytes) {
            if (!g_badChannelLogged.exchange(true))
                spy::log::Warn("native: Skython.Send dropped a call whose channel is empty or longer than " +
                          std::to_string(spy::kMaxChannelBytes) + " bytes (logged once)");
            return;
        }
        if (!spy::events::Hub().Wants("channel")) return;  // nobody subscribed: no copy, no JSON
        const char* p = payload.c_str();
        auto data = spy::make_channel_event(ch, p ? std::string_view(p) : ""sv);
        if (!data) return;  // unreachable after the check above (make_channel_event applies the same rule)
        spy::events::Push("channel", std::move(*data));
    } catch (...) {
        // nothing may escape into the VM (Global Constraints); Push itself never throws
    }
}

bool RegisterFunctions(RE::BSScript::IVirtualMachine* vm)
{
    try {
        // IVirtualMachine::RegisterFunction(std::string_view fn, std::string_view cls, F callback,
        //                                   bool callableFromTasklets = false)  (pinned clone, IVirtualMachine.h:147)
        vm->RegisterFunction("Send"sv, "Skython"sv, Send);
        spy::log::Info("native: Skython.Send registered");
        return true;
    } catch (...) {
        spy::log::Error("native: registering Skython.Send threw");
        return false;
    }
}
}

void Register()
{
    auto* papyrus = SKSE::GetPapyrusInterface();
    if (!papyrus || !papyrus->Register(RegisterFunctions))
        spy::log::Error("native: SKSE Papyrus interface refused the registration; Skython.Send is unavailable");
}
}
