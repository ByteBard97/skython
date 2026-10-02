#include "Settings.h"
#include "Log.h"
#include <windows.h>
#include <fstream>
#include <sstream>

namespace spy::settings {
namespace {
Config& Cfg() { static Config& c = *new Config(); return c; }      // immortal
Runtime& Rt() { static Runtime& r = *new Runtime(); return r; }

// Log text only. path::string() converts through the ANSI code page and throws for characters it cannot hold.
std::string U8(const std::filesystem::path& p) { const auto s = p.u8string(); return std::string(s.begin(), s.end()); }

std::filesystem::path CoreDllPath()
{
    HMODULE self = nullptr;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&CoreDllPath), &self)) {
        std::wstring buf(MAX_PATH, L'\0');
        for (;;) {
            const DWORD n = GetModuleFileNameW(self, buf.data(), static_cast<DWORD>(buf.size()));
            if (n == 0) break;
            if (n < buf.size()) { buf.resize(n); return std::filesystem::path(buf); }
            buf.resize(buf.size() * 2);
        }
    }
    return std::filesystem::path("Data/SKSE/Plugins/Skython/SkythonCore.dll");  // last resort
}
}

std::filesystem::path OwnDir() { return CoreDllPath().parent_path(); }
std::filesystem::path PluginsDir() { return OwnDir().parent_path(); }

void Load()
{
    const auto path = PluginsDir() / "Skython.ini";
    std::ifstream f(path);
    if (!f) {
        log::Info("settings: " + U8(path) + " not found; using defaults");
        Cfg() = Config{};
        return;
    }
    std::stringstream ss;
    ss << f.rdbuf();
    try {
        Cfg() = parse_config(ss.str());
    } catch (const std::exception& e) {
        log::Error("settings: " + U8(path) + ": " + e.what() + "; using defaults");
        Cfg() = Config{};
    }
    const auto& c = Cfg();
    for (const auto& k : c.ignored_keys) log::Warn("settings: unknown key '" + k + "' ignored");
    log::Info("settings: " + U8(path) + " python_enabled=" + (c.python_enabled ? "true" : "false") +
              " scripts_enabled=" + (c.scripts_enabled ? "true" : "false") +
              " bridge_enabled=" + (c.bridge_enabled ? "true" : "false") + " port=" + std::to_string(c.port) +
              " default_timeout_ms=" + std::to_string(c.default_timeout_ms) +
              " fixed_token=" + (c.fixed_token.empty() ? "no" : "yes"));
}

const Config& Get() { return Cfg(); }

std::optional<std::filesystem::path> SessionFile(const std::optional<std::filesystem::path>& skseDir)
{
    if (!Cfg().session_file.empty()) return std::filesystem::path(Cfg().session_file);
    if (!skseDir) return std::nullopt;
    return *skseDir / "Skython" / "session.json";
}

void SetRuntime(Runtime r) { Rt() = std::move(r); }
const Runtime& GetRuntime() { return Rt(); }
}
