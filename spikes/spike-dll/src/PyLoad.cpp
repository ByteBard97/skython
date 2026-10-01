// Throwaway M0 spike code (S8). No Python headers in this file: the spike DLL must not import python314.dll.
#include "PyLoad.h"

#include <windows.h>

#include <filesystem>
#include <format>
#include <mutex>
#include <string>
#include <system_error>
#include <vector>

namespace spike {
void Log(const std::string& line);  // main.cpp
}

namespace spike::py {
namespace {
using SetLogFn = void (*)(void (*)(const char*));
using SetCallerFn = void (*)(CallerFn);
using StartFn = int (*)(const wchar_t*, const wchar_t* const*, int);
using StatusFn = int (*)(char*, int);
using RunFn = int (*)(const char*, char*, int);
using ModuleFn = int (*)(wchar_t*, int, void**);

std::mutex g_mx;
HMODULE g_py = nullptr;
HMODULE g_core = nullptr;
SetCallerFn g_setCaller = nullptr;
StartFn g_start = nullptr;
StatusFn g_status = nullptr;
RunFn g_run = nullptr;
CallerFn g_pendingCaller = nullptr;

void LogFromCore(const char* s) { spike::Log(s ? s : ""); }

std::filesystem::path ModulePath(HMODULE h)
{
    std::wstring buf(32768, L'\0');
    const DWORD n = GetModuleFileNameW(h, buf.data(), static_cast<DWORD>(buf.size()));
    buf.resize(n);
    return std::filesystem::path(buf);
}
std::filesystem::path OwnDir()  // <Data>/SKSE/Plugins/SkyrimPython, next to this DLL's folder
{
    HMODULE self = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&LogFromCore), &self);
    return ModulePath(self).parent_path() / L"SkyrimPython";
}
std::string U8(const std::filesystem::path& p)
{
    const auto s = p.u8string();
    return std::string(s.begin(), s.end());
}
bool SamePath(const std::filesystem::path& a, const std::filesystem::path& b)
{
    std::error_code ec;
    const bool eq = std::filesystem::equivalent(a, b, ec);
    return !ec && eq;
}
std::string OneLine(std::string s)  // the harness is line based
{
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        if (c == '\r') continue;
        if (c == '\n') out += " | ";
        else out += c;
    }
    return out;
}
template <class F>
F Proc(const char* name) { return reinterpret_cast<F>(GetProcAddress(g_core, name)); }
}  // namespace

std::string Load()
{
    std::lock_guard l(g_mx);
    if (g_core) return "PY_LOAD ALREADY_LOADED";
    const auto own = OwnDir();
    const auto pyPath = own / L"python" / L"python314.dll";
    const auto corePath = own / L"SpikePyCore.dll";
    if (HMODULE existing = GetModuleHandleW(L"python314.dll")) {
        const auto found = ModulePath(existing);
        if (!SamePath(found, pyPath))
            return "PY_LOAD COLLISION another python314.dll is already loaded: " + U8(found) + " (ours: " + U8(pyPath) + "); Python stays disabled";
    }
    g_py = LoadLibraryExW(pyPath.wstring().c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!g_py) return std::format("PY_LOAD FAIL python314.dll error={} path={}", GetLastError(), U8(pyPath));
    HMODULE core = LoadLibraryExW(corePath.wstring().c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!core) return std::format("PY_LOAD FAIL SpikePyCore.dll error={} path={}", GetLastError(), U8(corePath));
    g_core = core;
    auto setLog = Proc<SetLogFn>("SpikePy_SetLog");
    auto module = Proc<ModuleFn>("SpikePy_PythonModule");
    g_setCaller = Proc<SetCallerFn>("SpikePy_SetCaller");
    g_start = Proc<StartFn>("SpikePy_Start");
    g_status = Proc<StatusFn>("SpikePy_Status");
    g_run = Proc<RunFn>("SpikePy_Run");
    if (!setLog || !module || !g_setCaller || !g_start || !g_status || !g_run) {
        g_core = nullptr;  // never unloaded; a rebuilt core is needed
        return "PY_LOAD FAIL SpikePyCore.dll lacks an export (SpikePy_SetLog/PythonModule/SetCaller/Start/Status/Run)";
    }
    setLog(&LogFromCore);
    if (g_pendingCaller) g_setCaller(g_pendingCaller);
    std::wstring seen(32768, L'\0');
    void* seenModule = nullptr;
    const int n = module(seen.data(), static_cast<int>(seen.size()), &seenModule);
    seen.resize(n > 0 ? static_cast<std::size_t>(n) : 0);
    const bool same = seenModule == static_cast<void*>(g_py);
    auto r = std::format("PY_LOAD {} same_module={} ours={} core_imports={} ours_handle={} core_import_handle={} core={}",
                         same ? "OK" : "MISMATCH", same, U8(ModulePath(g_py)), U8(std::filesystem::path(seen)),
                         static_cast<const void*>(g_py), static_cast<const void*>(seenModule), U8(ModulePath(g_core)));
    spike::Log("spike: " + r);
    return r;
}

std::string Init(bool dataLoaded)
{
    std::lock_guard l(g_mx);
    if (!dataLoaded) return "PY_INIT REFUSED before kDataLoaded";
    if (!g_start) return "PY_INIT REFUSED: run py_load first";
    const auto own = OwnDir();
    const std::wstring home = (own / L"python").wstring();
    const std::vector<std::wstring> paths = {
        (own / L"python" / L"python314.zip").wstring(),  // the stdlib, as .pyc files
        home,                                             // the .pyd extension modules
        (own / L"site-packages").wstring(),
        (own / L"lib").wstring(),
    };
    std::vector<const wchar_t*> raw;
    for (const auto& p : paths) raw.push_back(p.c_str());
    const int started = g_start(home.c_str(), raw.data(), static_cast<int>(raw.size()));  // copies the strings
    return started ? "PY_INIT STARTED (poll py_status)" : "PY_INIT ALREADY_STARTED";
}

std::string Status()
{
    std::lock_guard l(g_mx);
    if (!g_status) return "PY_STATUS not_loaded";
    char buf[4096];
    g_status(buf, sizeof buf);
    return std::string("PY_STATUS ") + buf;
}

std::string Exec(const std::string& code)
{
    RunFn run = nullptr;
    {
        std::lock_guard l(g_mx);
        run = g_run;
    }
    if (!run) return "ERR python is not loaded (py_load)";
    std::string out(1 << 20, '\0');
    const int n = run(code.c_str(), out.data(), static_cast<int>(out.size()));
    out.resize(n > 0 ? static_cast<std::size_t>(n) : 0);
    return OneLine(out);
}

void SetCaller(CallerFn fn)
{
    std::lock_guard l(g_mx);
    g_pendingCaller = fn;
    if (g_setCaller) g_setCaller(fn);
}

bool AutostartRequested()
{
    std::error_code ec;
    return std::filesystem::exists(OwnDir() / L"spike-autostart", ec);
}
}  // namespace spike::py
