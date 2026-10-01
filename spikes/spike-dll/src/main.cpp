#include "Net.h"
#include "PyLoad.h"

#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <format>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

// windows.h (pulled in by RE/Skyrim.h) defines GetObject as a macro for GetObjectA, which turns every
// RE::BSScript::Variable::GetObject() call into "GetObjectA is not a member" (compile error C2039). Undo it
// in every translation unit that includes RE/Skyrim.h and calls GetObject().
#undef GetObject

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 0, 1, 0 },
    .Name = "SkyrimPythonSpike"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace spike {
void Log(const std::string& line) { spdlog::info("{}", line); }

// Never call std::stoi on client input: it throws, and an exception out of the listener thread kills the game.
int ParseInt(const std::string& s, int fallback)
{
    try {
        std::size_t used = 0;
        const int v = std::stoi(s, &used);
        return used == s.size() ? v : fallback;
    } catch (...) {
        return fallback;
    }
}
}

namespace spike {
std::atomic_bool g_dataLoaded{ false };  // set in OnMessage at kDataLoaded; py_init refuses before it
}

// Handle has a namespace block of its own: Tasks 3-9 add their code as new `namespace spike { ... }` blocks
// directly above this one, after Log and ParseInt, which they use.
namespace spike {
std::string Handle(const std::string& line)
{
    if (line.rfind("echo ", 0) == 0) return line.substr(5);
    // Tasks 3-9 add their commands here, before the fallback.
    if (line == "py_load") return py::Load();
    if (line == "py_init") return py::Init(g_dataLoaded.load());
    if (line == "py_status") return py::Status();
    if (line.rfind("py_exec ", 0) == 0) return py::Exec(line.substr(8));
    return "ERR unknown command";
}
}

namespace {
void SetupLog()
{
    try {
        auto dir = SKSE::log::log_directory();
        if (!dir) return;  // Documents not resolvable: keep spdlog's default logger
        auto path = *dir;
        path /= "SkyrimPythonSpike.log"sv;
        auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path.string(), true);
        auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
        logger->set_level(spdlog::level::info);
        logger->flush_on(spdlog::level::info);
        spdlog::set_default_logger(std::move(logger));
    } catch (...) {
        // no log file; the spike still runs
    }
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kPostLoad:
        spike::StartListener(45978, spike::Handle);
        break;
    case SKSE::MessagingInterface::kDataLoaded:
        spike::Log("kDataLoaded");
        spike::g_dataLoaded.store(true);
        if (spike::py::AutostartRequested())  // run B (Step 7): the real plugin's timing; py::Load ran in SKSEPluginLoad
            spike::Log("spike: autostart at kDataLoaded " + spike::py::Init(true));
        break;
    default:
        break;
    }
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse, { .log = false });  // InitInfo aggregate (SKSE/API.h); the old Init(skse, bool) is [[deprecated]]
    SetupLog();
    spike::Log(std::format("SkyrimPythonSpike loading on Skyrim {}", REL::Module::get().version().string(".")));  // string() alone joins with '-' (1-6-1170-0)
    if (spike::py::AutostartRequested())  // run B (Step 7): python314.dll + SpikePyCore.dll at plugin load
        spike::Log("spike: autostart at plugin load " + spike::py::Load());
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
