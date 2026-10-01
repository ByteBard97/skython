// The Linux build of _skyrim: the real module code (pyglue/src/pymodule.cpp) over core's FakeBackend.
// Environment, read by the tests:
//   SKYRIM_FAKE_DIR  folder for config()'s paths and the log (default: <temp>/skyrim-fake)
//   SKYRIM_FAKE_INI  ini text parsed like SkyrimPython.ini; read by config() on every call, and once at import for the
//                    dispatcher's default_timeout_ms and max_inflight_total
#include "spy/config.h"
#include "spy/pymodule.h"
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>

namespace fs = std::filesystem;

namespace {
std::string Env(const char* name)
{
    const char* v = std::getenv(name);
    return v != nullptr ? std::string(v) : std::string();
}

fs::path BaseDir()
{
    const std::string d = Env("SKYRIM_FAKE_DIR");
    return d.empty() ? fs::temp_directory_path() / "skyrim-fake" : fs::path(d);
}

spy::Config FakeConfig() { return spy::parse_config(Env("SKYRIM_FAKE_INI")); }

std::string ConfigJson()
{
    const spy::Config cfg = FakeConfig();
    spy::json j = spy::config_to_json(cfg);
    const fs::path base = BaseDir();
    const fs::path logs = base / "logs";  // holds python.log and session.json, like .../SKSE/SkyrimPython/ in the game
    j["python_dir"] = (base / "python").string();
    j["scripts_dir"] = (base / "scripts").string();
    j["site_packages_dir"] = (base / "site-packages").string();
    j["log_dir"] = logs.string();
    j["session_file"] = cfg.session_file.empty() ? (logs / "session.json").string() : cfg.session_file;
    j["plugin_version"] = "0.0.0-fake";
    j["game_version"] = "0.0.0";
    j["runtime"] = "fake";
    return spy::dump_json(j);
}

void Log(const std::string& level, const std::string& message)
{
    static std::mutex mx;
    std::lock_guard lk(mx);
    const fs::path dir = BaseDir() / "logs";
    std::error_code ec;
    fs::create_directories(dir, ec);
    std::ofstream(dir / "plugin.log", std::ios::app) << level << ' ' << message << '\n';
}
}

PYBIND11_MODULE(_skyrim, m)
{
    // Leaked on purpose: the fake's worker and the dispatcher's reaper live until the process exits, as in the game,
    // which never finalises Python. Destroying them during interpreter shutdown would join threads at a bad time.
    static spy::FakeBackend* fake = new spy::FakeBackend();
    static spy::Dispatcher* dispatcher = [] {
        const spy::Config cfg = FakeConfig();
        auto* d = new spy::Dispatcher(*fake, {cfg.default_timeout_ms, cfg.max_inflight_total});
        d->StartReaper();
        return d;
    }();
    spy::InstallModuleHost({dispatcher, "fake", ConfigJson, Log, fake});
    spy::DefineModule(m);
}
