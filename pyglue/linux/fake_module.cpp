// The Linux build of _skython: the real module code (pyglue/src/pymodule.cpp) over core's FakeBackend.
// Environment, read by the tests:
//   SKYTHON_FAKE_DIR  folder for config()'s paths and the log (default: <temp>/skython-fake)
//   SKYTHON_FAKE_INI  ini text parsed like Skython.ini; read by config() on every call, and once at import for the
//                    dispatcher's default_timeout_ms and max_inflight_total
#include "spy/config.h"
#include "spy/console_echo.h"
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
    const std::string d = Env("SKYTHON_FAKE_DIR");
    return d.empty() ? fs::temp_directory_path() / "skython-fake" : fs::path(d);
}

spy::Config FakeConfig() { return spy::parse_config(Env("SKYTHON_FAKE_INI")); }

std::string ConfigJson()
{
    const spy::Config cfg = FakeConfig();
    spy::json j = spy::config_to_json(cfg);
    const fs::path base = BaseDir();
    const fs::path logs = base / "logs";  // holds python.log and session.json, like .../SKSE/Skython/ in the game
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
    static std::mutex* mx = new std::mutex;  // leaked on purpose: a log call during exit must not find it destroyed
    std::lock_guard lk(*mx);
    const fs::path dir = BaseDir() / "logs";
    std::error_code ec;
    fs::create_directories(dir, ec);
    std::ofstream(dir / "plugin.log", std::ios::app) << level << ' ' << message << '\n';
}
}

PYBIND11_MODULE(_skython, m)
{
    // Leaked on purpose: the fake's worker and the dispatcher's reaper live until the process exits, as in the game,
    // which never finalises Python. Destroying them during interpreter shutdown would join threads at a bad time.
    static spy::FakeBackend* fake = new spy::FakeBackend();
    // The hub's epoch is the fake's (BaseState()["epoch"] counts loads); it is "loading" while _fake_set_not_ready("loading").
    static spy::EventHub* hub = new spy::EventHub({[] { return fake->LoadsStarted(); },
                                                   [] { return fake->NotReadyReason(false).value_or("") == "loading"; }});
    static spy::Dispatcher* dispatcher = [] {
        const spy::Config cfg = FakeConfig();
        spy::DispatcherOptions options{cfg.default_timeout_ms, cfg.max_inflight_total};
        options.events = hub;
        auto* d = new spy::Dispatcher(*fake, options);
        d->StartReaper();
        return d;
    }();
    // Named fields (C++20 designated initializers), never positional: later tasks append ModuleHost fields
    // (console_echo in Task 43, probe_sink_cycles in Task 44), each with a default.
    static spy::ConsoleEcho* echo = new spy::ConsoleEcho([](const std::string& line) { Log("info", "console: " + line); });  // leaked like the fake
    spy::InstallModuleHost({.dispatcher = dispatcher, .backend = "fake", .config_json = ConfigJson, .log = Log,
                            .fake = fake, .events = hub,
                            .console_echo = [](const std::string& m) { echo->Echo(m); }});
    spy::DefineModule(m);
}
