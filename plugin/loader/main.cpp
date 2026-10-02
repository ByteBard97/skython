#include "Win.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <filesystem>
#include <format>
#include <fstream>

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "Skython"sv,
    .Author = "Skython"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace {
std::ofstream* g_log = nullptr;  // leaked on purpose; nullptr when no log file could be opened

void Log(const std::string& line)
{
    if (g_log && *g_log) { *g_log << "loader: " << line << '\n'; g_log->flush(); }
}

void OpenLog()
{
    const auto dir = SKSE::log::log_directory();  // std::optional<std::filesystem::path>
    std::filesystem::path p = dir ? *dir : std::filesystem::path{};
    p /= "SkythonLoader.log"sv;
    g_log = new std::ofstream(p, std::ios::trunc);
}

// Spec 6: on a runtime that has not been verified, nothing starts. Add a version only after a live run on it.
bool RuntimeSupported()
{
    if (spy::loader::TestUnverifiedRuntime(&Log)) return false;  // SPY_TEST_HOOKS builds only
    static const REL::Version kVerified[] = { REL::Version{ 1, 6, 1170, 0 } };
    const auto v = REL::Module::get().version();
    for (const auto& ok : kVerified)
        if (v == ok) return true;
    return false;
}
}

SKSE_PLUGIN_LOAD(const SKSE::LoadInterface* skse)
{
    try {
        SKSE::Init(skse, { .log = false });
        OpenLog();
        const auto ver = REL::Module::get().version().string(".");
        Log(std::format("Skython loader {} on Skyrim {} ({})", SPY_VERSION, ver, REL::Module::IsAE() ? "AE" : "SE"));
        if (!RuntimeSupported()) {
            Log(std::format("runtime check: Skyrim {} is not a verified runtime; Python and Skython stay off", ver));
            return true;
        }
        // Address Library for SKSE Plugins: CommonLib's REL::ID needs it and stops the game with a message box if it
        // is missing, so the core is not started without it. For 1.6.1170 the file is versionlib-1-6-1170-0.bin.
        if (const auto lib = "versionlib-" + REL::Module::get().version().string("-") + ".bin";
            !spy::loader::PluginsFileExists(lib)) {
            Log("Address Library for SKSE Plugins not found (" + lib + "); Python stays off");
            return true;
        }
        if (const std::string why = spy::loader::StartCore(skse, &Log); !why.empty())
            Log("Python disabled: " + why);
    } catch (const std::exception& e) {
        Log(std::string("Python disabled: loader exception: ") + e.what());
    } catch (...) {
        Log("Python disabled: loader exception");
    }
    return true;  // never fail the game's plugin load
}
