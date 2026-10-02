#include "Win.h"
#include <windows.h>
#include <filesystem>

namespace spy::loader {
namespace fs = std::filesystem;
namespace {
fs::path ModulePath(HMODULE m)
{
    std::wstring buf(MAX_PATH, L'\0');
    for (;;) {
        const DWORD n = GetModuleFileNameW(m, buf.data(), static_cast<DWORD>(buf.size()));
        if (n == 0) return {};
        if (n < buf.size()) { buf.resize(n); return fs::path(buf); }
        buf.resize(buf.size() * 2);
    }
}

fs::path OwnPath()
{
    HMODULE self = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&OwnPath), &self))
        return {};
    return ModulePath(self);
}

std::string U8(const fs::path& p)
{
    const auto s = p.u8string();
    return std::string(s.begin(), s.end());
}

bool SamePath(const fs::path& a, const fs::path& b)
{
    std::error_code ec;
    if (fs::equivalent(a, b, ec)) return true;
    return _wcsicmp(a.lexically_normal().c_str(), b.lexically_normal().c_str()) == 0;
}

std::string WinError(const std::string& what) { return what + " (Windows error " + std::to_string(GetLastError()) + ")"; }
}

bool TestUnverifiedRuntime(LogFn log) noexcept
{
#ifdef SPY_TEST_HOOKS
    try {
        wchar_t v[16];
        const DWORD n = GetEnvironmentVariableW(L"SKYTHON_TEST_UNVERIFIED_RUNTIME", v, 16);
        const bool on = n > 0 && n < 16 && std::wstring(v, n) == L"1";
        log(on ? "TEST HOOK: SKYTHON_TEST_UNVERIFIED_RUNTIME=1: treating this runtime as unverified"
               : "TEST HOOK: SKYTHON_TEST_UNVERIFIED_RUNTIME not set");
        return on;
    } catch (...) {
        return false;
    }
#else
    (void)log;
    return false;
#endif
}

std::string StartCore(const void* skse, LogFn log) noexcept
{
    try {
        const fs::path plugins = OwnPath().parent_path();
        if (plugins.empty()) return "cannot find the loader's own folder";
        const fs::path own = plugins / L"Skython";
        const fs::path pyDll = own / L"python" / L"python314.dll";
        const fs::path core = own / L"SkythonCore.dll";

#ifdef SPY_TEST_HOOKS
        // Test-only (Task 26, collision variant): stand in for another mod that loaded its own CPython first.
        wchar_t pre[4096];
        const DWORD n = GetEnvironmentVariableW(L"SKYTHON_TEST_PRELOAD", pre, 4096);
        if (n > 0 && n < 4096) {
            HMODULE h = LoadLibraryExW(pre, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
            log("TEST HOOK: preloaded " + U8(pre) + (h ? "" : " (failed)"));
        } else {
            log("TEST HOOK: SKYTHON_TEST_PRELOAD not set");
        }
#endif
        if (HMODULE existing = GetModuleHandleW(L"python314.dll")) {
            const fs::path other = ModulePath(existing);
            if (!SamePath(other, pyDll))
                return "another python314.dll is already loaded from " + U8(other) +
                       "; two copies of CPython 3.14 in one process are not safe";
        }
        if (!fs::exists(pyDll)) return "missing " + U8(pyDll);
        HMODULE py = LoadLibraryExW(pyDll.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!py) return WinError("cannot load " + U8(pyDll));
        log("loaded python314.dll from " + U8(ModulePath(py)));

        if (!fs::exists(core)) return "missing " + U8(core);
        HMODULE c = LoadLibraryExW(core.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!c) return WinError("cannot load " + U8(core));
        const fs::path inUse = ModulePath(GetModuleHandleW(L"python314.dll"));
        log("core loaded from " + U8(ModulePath(c)) + "; python314.dll in use: " + U8(inUse));
        if (!SamePath(inUse, pyDll)) return "the core resolved python314.dll to " + U8(inUse);

        using LoadFn = bool (*)(const void*);
        auto fn = reinterpret_cast<LoadFn>(GetProcAddress(c, "SpyCore_Load"));
        if (!fn) return "SkythonCore.dll has no SpyCore_Load export";
        if (!fn(skse)) return "SpyCore_Load returned false (see Skython.log)";
        log("SpyCore_Load returned true");
        return "";
    } catch (const std::exception& e) {
        return std::string("loader exception: ") + e.what();
    } catch (...) {
        return "loader exception";
    }
}
}
