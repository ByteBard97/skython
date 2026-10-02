# Task 25: Plugin skeleton: loader and core DLLs, logging, settings, lifecycle

Adapted from: branch `plan-out-of-process`, task 15 (`task-15-plugin-skeleton-build-logging-settings-lifecycle.md`). The lifecycle state machine moved to core (Task 17); the RPC server is gone; the plugin is now **two DLLs** (spec 4.1, index Global Constraints): a loader that SKSE loads and a core that links CPython.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.1, 6, 7. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after `spikes/RESULTS.md` shows S2, S4, S8 and S9 PASS (Tasks 3-6). S8's record says whether the two-DLL load resolved `python314.dll` to the copy loaded by full path; if it did not, STOP and ask the owner.
>
> **Depends on:** Tasks 16 (`Config`, `parse_config`), 17 (`LifecycleState`), 19 (`pyglue/` sources). **Runs on:** Step 1 on Linux; Steps 2-10 on the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); everything is driven from Linux.
>
> **STOP and ask the owner (do not improvise) if:** a gating spike is FAIL; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; a CommonLibSSE-NG name below does not compile and `plugin/extern/CommonLibSSE-NG` shows no obvious equivalent; `tools/fetch_python.sh --check` fails; **the first live launch (Step 9) is a gate:** if the loader log says `SpyCore_Load returned true` but the core log has no `SkyrimPython core: data loaded` line, `SKSE::Init` called from the core or the core's messaging listener does not work. Do not work around it (for example by moving the listener into the loader): STOP and ask the owner.

**Files:**
- Create: `plugin/CMakeLists.txt`, `plugin/vcpkg.json`, `plugin/build.ps1`, `plugin/config/SkyrimPython.ini`, `plugin/config/SkyrimPython.dev.ini`, `plugin/config/variants/python-off.ini`
- Create: `plugin/loader/main.cpp`, `plugin/loader/Win.h`, `plugin/loader/Win.cpp` (loader DLL `SkyrimPython.dll`)
- Create: `plugin/src/main.cpp`, `plugin/src/Log.{h,cpp}`, `plugin/src/Settings.{h,cpp}`, `plugin/src/Lifecycle.{h,cpp}`, `plugin/src/PythonHost.{h,cpp}` (core DLL `SkyrimPythonCore.dll`; `PythonHost` holds only `LinkedPythonVersion()` until Task 26)
- Modify: `.gitignore` (append `plugin/build-options.local`), `docs/PINS.md` (append the Step 2 notes)

**Interfaces:**
- Consumes: `spy::Config`, `spy::parse_config`, `spy::config_to_json` (Task 16); `spy::LifecycleState` with `set_paused`, `not_ready_reason(bool)`, `base_state()` (Task 17); `pyglue/src/pymodule.cpp` and `pyglue/include` (Task 19, compiled into the core); `tools/remote_build.sh` (Task 1); the box harness `tools/box/` (Task 1b: `box_deploy.sh`, `box_launch.sh [--env]`, `box_wait_log.sh`, `box_files.sh`, `box_session.sh`, `box_console.sh`, `box_quit.sh`, `box_stop.sh`) through [Task 1c](task-01c-part-c-on-the-box.md); `third_party/cpython/{embed,nuget}` (Task 0).
- Produces:
  - Loader export `SKSEPlugin_Load` (plus `SKSEPlugin_Version`/`SKSEPlugin_Query` from `SKSEPluginInfo`); loader log `SKSE/SkyrimPythonLoader.log` with the lines listed in Step 3.
  - Core export `extern "C" __declspec(dllexport) bool SpyCore_Load(const SKSE::LoadInterface*)`.
  - `spy::log::Set/Info/Warn/Error/Debug` (core log `SKSE/SkyrimPython.log`).
  - `spy::settings::Load()`, `Get() -> const Config&`, `PluginsDir()`, `OwnDir()`, `SessionFile(skseDir) -> std::optional<path>`, and **`struct Runtime { std::string plugin_version, game_version, runtime; std::optional<std::filesystem::path> skse_dir; }`, `SetRuntime(Runtime)`, `GetRuntime() -> const Runtime&`** (filled by `main.cpp`, read by Task 26's `_skyrim.config()`, so `PythonHost.cpp` needs no CommonLib header).
  - `spy::life::OnMessage(Message*)`, `DataReady()`, `GameLoaded()`, `Loading()`, `Interactive()`, `Paused()`, `Epoch()`, `LoadsStarted()`, `NotReadyReason(bool require_unpaused = false) -> std::optional<std::string>`, `OpenMenus()`, `BaseState() -> json`.
  - `spy::host::LinkedPythonVersion() -> std::string` (Task 26 adds `Start`/`State` to the same files).
  - Deploying uses Task 1b's `tools/box/box_deploy.sh out/SkyrimPython.dll [ini]` (loader to `Plugins\`, `out/SkyrimPythonCore.dll`, required and not older than the loader, to `Plugins\SkyrimPython\`, the ini to `Plugins\SkyrimPython.ini`) and `--python`; the game runs on the Windows box ([Task 1c](task-01c-part-c-on-the-box.md), ruling R42). `tools/deploy.sh` is not changed.
  - Test hooks (only in a loader built with `SPY_TEST_HOOKS=ON`): `SKYRIM_PYTHON_TEST_PRELOAD=<path>` (Task 26's collision variant) and `SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1` (Step 10). Such a loader logs one `TEST HOOK:` line per hook, also when its variable is not set.
  - Build: run `tools/remote_build.sh plugin SkyrimPython.dll` **and** `tools/remote_build.sh plugin SkyrimPythonCore.dll` (the script fetches one DLL per call; the second call is an incremental no-op build plus the copy).

Rules (spec 4.1, 6):
- The loader has **no Python headers and no import of `python314.dll`**. Its order: runtime check → collision check (`GetModuleHandleW(L"python314.dll")` from another path disables Python) → `LoadLibraryExW` of `SkyrimPython\python\python314.dll` → `LoadLibraryExW` of `SkyrimPython\SkyrimPythonCore.dll` (both full paths, `LOAD_WITH_ALTERED_SEARCH_PATH`) → `SpyCore_Load`. It logs the path of the `python314.dll` the process uses after the core loaded. Every failure is one `Python disabled: <why>` line, and the loader still returns `true`.
- The core also carries `SKSEPluginInfo` (name `SkyrimPythonCore`). Nothing depends on it: CommonLib's `SKSE::Init` reads `PluginVersionData` only for the plugin's name, author and version and falls back to the module file name when the export is missing (Step 2). It is kept because it is harmless and names the core in CommonLib's own messages; SKSE never sees it (it loads only DLLs directly in `Plugins/`).
- **The loader calls nothing in SKSE beyond `SKSE::Init`**: no `RegisterListener`, no other interface. Every registration happens in the core. The core's `MessagingInterface::RegisterListener` passes `GetPluginHandle()`, which is the **loader's** handle (captured during the loader's load call), and SKSE may drop a second listener for the same handle and sender, so a loader-side listener could silently swallow the core's `kDataLoaded`. Step 2 records this in `docs/PINS.md`.
- Paths are passed to the file APIs as `std::filesystem::path` (wide on Windows) and put into log text through `U8()`. `path::string()` converts through the ANSI code page and throws for characters it cannot represent (for example a CJK user name on a Western code page), so it is never used on a game or Documents path.
- `paused` comes from `RE::UI::GameIsPaused()`, read in the menu sink after each menu event and stored with `LifecycleState::set_paused`. Task 31's menu test checks it is not stale.

- [ ] **Step 1: Linux precheck**

```bash
tools/fetch_python.sh --check
cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build --output-on-failure | tail -3
ls pyglue/src/pymodule.cpp pyglue/include/spy/pymodule.h third_party/pybind11/include/pybind11/pybind11.h third_party/cpython/nuget/tools/libs/python314.lib
```
Expected: the checksum check passes, all core tests pass, and the four files exist.

- [ ] **Step 2: Verify two CommonLib assumptions in the pinned checkout**

```bash
grep -rn "SKSEPlugin_Version\|PluginVersionData::GetSingleton" plugin/extern/CommonLibSSE-NG/src/SKSE/API.cpp | head
grep -n "RegisterListener\|GetPluginHandle" plugin/extern/CommonLibSSE-NG/src/SKSE/Interfaces.cpp | head
grep -n "GameIsPaused\|IsMenuOpen\|MENU_NAME" plugin/extern/CommonLibSSE-NG/include/RE/U/UI.h plugin/extern/CommonLibSSE-NG/include/RE/L/LoadingMenu.h plugin/extern/CommonLibSSE-NG/include/RE/M/MainMenu.h
```
Expected: `SKSE::Impl::API::Init` (`src/SKSE/API.cpp`) reads `PluginVersionData::GetSingleton()` only for the plugin name, author and version, and falls back to the module file name when the export is missing; nothing fails without it. `MessagingInterface::RegisterListener` (`src/SKSE/Interfaces.cpp`) passes `GetPluginHandle()`, the handle captured by the first `SKSE::Init` in the process's load call, which is the loader's. `UI::GameIsPaused()`, `UI::IsMenuOpen(const std::string_view&)` and both `MENU_NAME`s exist. Append to `docs/PINS.md`:
```bash
cat >> docs/PINS.md <<'EOF'

## Plugin notes (Task 25)
- The core DLL's SKSEPluginInfo is not needed by CommonLib: SKSE::Init reads PluginVersionData only for name/author/version and falls back to the module file name. Kept as harmless.
- The core's RegisterListener uses the loader's plugin handle (GetPluginHandle() is captured during the loader's load call). SKSE may drop a second listener for the same handle and sender, so the loader registers nothing beyond SKSE::Init; every registration happens in the core.
EOF
```

- [ ] **Step 3: Build files**

`plugin/CMakeLists.txt` (sources listed explicitly; Tasks 26-30 add theirs to `SPY_CORE_SOURCES`):
```cmake
cmake_minimum_required(VERSION 3.21)
project(SkyrimPython VERSION 0.1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(SPY_TEST_HOOKS "Loader honours SKYRIM_PYTHON_TEST_PRELOAD and SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME (live tests only)" OFF)

set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/extern/CommonLibSSE-NG commonlibsse-ng)
set(SPY_CORE_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../core spy_core)

set(SPY_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(SPY_CPYTHON ${SPY_ROOT}/third_party/cpython/nuget/tools)
if(NOT EXISTS ${SPY_CPYTHON}/libs/python314.lib)
  message(FATAL_ERROR "${SPY_CPYTHON}/libs/python314.lib is missing: run tools/fetch_python.sh on Linux, then sync again")
endif()
set(SPY_DEFS SPY_VERSION="${PROJECT_VERSION}" WIN32_LEAN_AND_MEAN NOMINMAX)

# Loader: SKSE-facing, no Python dependency at all.
add_library(SkyrimPython SHARED loader/main.cpp loader/Win.cpp)
target_compile_definitions(SkyrimPython PRIVATE ${SPY_DEFS} $<$<BOOL:${SPY_TEST_HOOKS}>:SPY_TEST_HOOKS>)
target_link_libraries(SkyrimPython PRIVATE CommonLibSSE::CommonLibSSE)

# Core: links python314.lib normally; everything that touches Python, pybind11 or the game lives here.
set(SPY_CORE_SOURCES
  src/main.cpp src/Log.cpp src/Settings.cpp src/Lifecycle.cpp src/PythonHost.cpp
  ${SPY_ROOT}/pyglue/src/pymodule.cpp)
add_library(SkyrimPythonCore SHARED ${SPY_CORE_SOURCES})
target_compile_definitions(SkyrimPythonCore PRIVATE ${SPY_DEFS})
target_include_directories(SkyrimPythonCore PRIVATE
  ${SPY_ROOT}/pyglue/include ${SPY_ROOT}/third_party/pybind11/include ${SPY_CPYTHON}/include)
target_link_directories(SkyrimPythonCore PRIVATE ${SPY_CPYTHON}/libs)   # pyconfig.h's #pragma comment(lib, ...)
target_link_libraries(SkyrimPythonCore PRIVATE CommonLibSSE::CommonLibSSE spy_core ${SPY_CPYTHON}/libs/python314.lib)
```
(`core/CMakeLists.txt` from Task 13 must honour `SPY_CORE_TESTS`; if it uses another option name, use that.)

`plugin/vcpkg.json` (the three packages CommonLib's CMake looks up; see `API-VERIFICATION.md` Part 2):
```json
{
  "name": "skyrim-python",
  "version-string": "0.1.0",
  "dependencies": [ "spdlog", "rapidcsv", "directxtk" ]
}
```

`plugin/build.ps1` (Release only: `pyconfig.h` asks for `python314_d.lib` in Debug builds, which the NuGet package does not have):
```powershell
param([string]$VcpkgRoot = "C:\vcpkg", [string]$Config = "Release")
$ErrorActionPreference = "Stop"
$toolchain = Join-Path $VcpkgRoot "scripts\buildsystems\vcpkg.cmake"
$src = $PSScriptRoot; $build = Join-Path $src "build"
# Test hooks only when plugin/build-options.local (gitignored) says SPY_TEST_HOOKS=ON; always passed explicitly,
# so an earlier ON in the CMake cache cannot leak into a normal build.
$hooks = "OFF"
$opts = Join-Path $src "build-options.local"
if ((Test-Path $opts) -and ((Get-Content $opts -Raw) -match 'SPY_TEST_HOOKS=ON')) { $hooks = "ON" }
cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_TOOLCHAIN_FILE=$toolchain" -DVCPKG_TARGET_TRIPLET=x64-windows-static-md "-DSPY_TEST_HOOKS=$hooks"
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build $build --config $Config --parallel
if ($LASTEXITCODE -ne 0) { throw "build failed" }
Write-Output "SPY_TEST_HOOKS=$hooks"
```
```bash
echo "plugin/build-options.local" >> .gitignore
```

`plugin/config/SkyrimPython.ini` (shipped; section headers are ignored by `parse_config`):
```ini
; SkyrimPython settings. Lines are "key = value"; ';' starts a comment.
[Python]
python_enabled = true
scripts_enabled = true
[Bridge]
; Development only: lets a process outside the game use the same API over loopback TCP.
bridge_enabled = false
port = 45977
; Empty: a new random token each game start, written to the session file.
fixed_token =
; Empty: Documents/My Games/<game>/SKSE/SkyrimPython/session.json
session_file =
max_inflight_per_connection = 256
[Calls]
default_timeout_ms = 5000
max_inflight_total = 1024
enable_console = false
```
`plugin/config/SkyrimPython.dev.ini` is the same file with `bridge_enabled = true`. It keeps `fixed_token` empty: no token is ever committed (global rule); clients read the per-run token from the session file. `plugin/config/variants/python-off.ini` is the dev ini with `python_enabled = false`.

- [ ] **Step 4: The loader**

`plugin/loader/Win.h`:
```cpp
#pragma once
#include <string>

namespace spy::loader {
using LogFn = void (*)(const std::string&);
// Loads python314.dll and SkyrimPythonCore.dll by full path from <this DLL's folder>\SkyrimPython\ and calls
// SpyCore_Load(skse). Returns "" on success, else the reason Python stays disabled. Never throws.
std::string StartCore(const void* skseLoadInterface, LogFn log) noexcept;
// Test hook (SPY_TEST_HOOKS builds only; always false otherwise): SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1 makes the
// loader treat the running game as an unverified runtime (Task 25 Step 10). Logs one TEST HOOK line. Never throws.
bool TestUnverifiedRuntime(LogFn log) noexcept;
}
```

`plugin/loader/Win.cpp` (the only loader file that includes `<windows.h>`; no CommonLib header here):
```cpp
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
        const DWORD n = GetEnvironmentVariableW(L"SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME", v, 16);
        const bool on = n > 0 && n < 16 && std::wstring(v, n) == L"1";
        log(on ? "TEST HOOK: SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1: treating this runtime as unverified"
               : "TEST HOOK: SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME not set");
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
        const fs::path own = plugins / L"SkyrimPython";
        const fs::path pyDll = own / L"python" / L"python314.dll";
        const fs::path core = own / L"SkyrimPythonCore.dll";

#ifdef SPY_TEST_HOOKS
        // Test-only (Task 26, collision variant): stand in for another mod that loaded its own CPython first.
        wchar_t pre[4096];
        const DWORD n = GetEnvironmentVariableW(L"SKYRIM_PYTHON_TEST_PRELOAD", pre, 4096);
        if (n > 0 && n < 4096) {
            HMODULE h = LoadLibraryExW(pre, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
            log("TEST HOOK: preloaded " + U8(pre) + (h ? "" : " (failed)"));
        } else {
            log("TEST HOOK: SKYRIM_PYTHON_TEST_PRELOAD not set");
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
        if (!fn) return "SkyrimPythonCore.dll has no SpyCore_Load export";
        if (!fn(skse)) return "SpyCore_Load returned false (see SkyrimPython.log)";
        log("SpyCore_Load returned true");
        return "";
    } catch (const std::exception& e) {
        return std::string("loader exception: ") + e.what();
    } catch (...) {
        return "loader exception";
    }
}
}
```

`plugin/loader/main.cpp`:
```cpp
#include "Win.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <filesystem>
#include <format>
#include <fstream>

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "SkyrimPython"sv,
    .Author = "skyrim-python"sv,
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
    p /= "SkyrimPythonLoader.log"sv;
    g_log = new std::ofstream(p, std::ios::trunc);
}

// Spec 6: on a runtime that has not been verified, nothing starts. Add a version only after a live run on it.
bool RuntimeSupported()
{
    if (spy::loader::TestUnverifiedRuntime(&Log)) return false;  // SPY_TEST_HOOKS builds only (Step 10)
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
        Log(std::format("SkyrimPython loader {} on Skyrim {} ({})", SPY_VERSION, ver, REL::Module::IsAE() ? "AE" : "SE"));
        if (!RuntimeSupported()) {
            Log(std::format("runtime check: Skyrim {} is not a verified runtime; Python and SkyrimPython stay off", ver));
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
```
Loader log lines (tests and later tasks wait for these exact phrases): `SkyrimPython loader 0.1.0 on Skyrim 1.6.1170.0 (AE)`, `runtime check: ... is not a verified runtime; Python and SkyrimPython stay off`, `loaded python314.dll from <path>`, `core loaded from <path>; python314.dll in use: <path>`, `SpyCore_Load returned true`, `Python disabled: <why>`; in a `SPY_TEST_HOOKS` build only, `TEST HOOK: ...` (one line per hook, `... not set` when its variable is absent).

- [ ] **Step 5: Core logging and settings (no CommonLib headers)**

`plugin/src/Log.h`:
```cpp
#pragma once
#include <memory>
#include <string>

namespace spdlog { class logger; }

// Callable from translation units that must not include CommonLib (PythonHost.cpp, pymodule.cpp).
// Safe before Set(): lines are then discarded.
namespace spy::log {
void Set(std::shared_ptr<spdlog::logger> logger);  // kept in a leaked shared_ptr: threads may log during process exit
void Info(const std::string& line);
void Warn(const std::string& line);
void Error(const std::string& line);
void Debug(const std::string& line);
}
```

`plugin/src/Log.cpp`:
```cpp
#include "Log.h"
#include <spdlog/spdlog.h>

namespace spy::log {
namespace {
std::shared_ptr<spdlog::logger>* g_logger = nullptr;  // leaked on purpose
}
void Set(std::shared_ptr<spdlog::logger> logger) { g_logger = new std::shared_ptr<spdlog::logger>(std::move(logger)); }
void Info(const std::string& line) { if (g_logger) (*g_logger)->info("{}", line); }
void Warn(const std::string& line) { if (g_logger) (*g_logger)->warn("{}", line); }
void Error(const std::string& line) { if (g_logger) (*g_logger)->error("{}", line); }
void Debug(const std::string& line) { if (g_logger) (*g_logger)->debug("{}", line); }
}
```

`plugin/src/Settings.h`:
```cpp
#pragma once
#include "spy/config.h"
#include <filesystem>
#include <optional>
#include <string>

namespace spy::settings {
// Reads Data/SKSE/Plugins/SkyrimPython.ini (the loader's folder, the parent of the core's). Missing file: defaults.
// Invalid value: defaults (bridge off) and an error in the log.
void Load();
const Config& Get();
std::filesystem::path OwnDir();      // Data/SKSE/Plugins/SkyrimPython (folder of SkyrimPythonCore.dll)
std::filesystem::path PluginsDir();  // Data/SKSE/Plugins
// The ini's session_file if set, else <skseDir>/SkyrimPython/session.json; nullopt when neither exists.
std::optional<std::filesystem::path> SessionFile(const std::optional<std::filesystem::path>& skseDir);

// Facts only CommonLib can read, stored by main.cpp so that CommonLib-free files (PythonHost.cpp) can use them.
struct Runtime {
    std::string plugin_version, game_version, runtime;  // "0.1.0", "1.6.1170.0", "AE"
    std::optional<std::filesystem::path> skse_dir;      // Documents/My Games/<game>/SKSE
};
void SetRuntime(Runtime);
const Runtime& GetRuntime();
}
```

`plugin/src/Settings.cpp`:
```cpp
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
    return std::filesystem::path("Data/SKSE/Plugins/SkyrimPython/SkyrimPythonCore.dll");  // last resort
}
}

std::filesystem::path OwnDir() { return CoreDllPath().parent_path(); }
std::filesystem::path PluginsDir() { return OwnDir().parent_path(); }

void Load()
{
    const auto path = PluginsDir() / "SkyrimPython.ini";
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
    return *skseDir / "SkyrimPython" / "session.json";
}

void SetRuntime(Runtime r) { Rt() = std::move(r); }
const Runtime& GetRuntime() { return Rt(); }
}
```

- [ ] **Step 6: Lifecycle (plugin side, a thin layer over core's `LifecycleState`)**

`plugin/src/Lifecycle.h`:
```cpp
#pragma once
#include "spy/errors.h"  // spy::json
#include <SKSE/SKSE.h>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace spy::life {
void OnMessage(SKSE::MessagingInterface::Message* m);  // from the core's messaging listener
bool DataReady();
bool GameLoaded();
bool Loading();
bool Interactive();
bool Paused();
std::uint32_t Epoch();
std::uint32_t LoadsStarted();
// nullopt when interactive; else "starting", "loading", "no_game", then "paused" only when require_unpaused.
std::optional<std::string> NotReadyReason(bool require_unpaused = false);
std::vector<std::string> OpenMenus();
json BaseState();  // LifecycleState::base_state(): data_ready, game_loaded, loading, interactive, paused, menus, epoch
}
```

`plugin/src/Lifecycle.cpp`:
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include "spy/lifecycle_state.h"
#include <RE/Skyrim.h>
#include <atomic>

namespace spy::life {
namespace {
// Immortal: read from Python threads (through the Dispatcher) that can outlive static destruction.
// Task 17's default menu names are "Loading Menu" and "Main Menu" (RE::LoadingMenu/MainMenu::MENU_NAME).
LifecycleState& State()
{
    static LifecycleState& s = *new LifecycleState();
    return s;
}

std::atomic_bool g_lastInteractive{ false };

void NoteInteractive()
{
    const bool now = State().interactive();
    if (g_lastInteractive.exchange(now) != now)
        log::Info(std::string("lifecycle: interactive = ") + (now ? "true" : "false") + ", epoch " + std::to_string(State().epoch()));
}

struct MenuSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        try {
            if (!e) return RE::BSEventNotifyControl::kContinue;
            const std::string name = e->menuName.c_str();
            State().on_menu(name, e->opening);
            // Task 31's menu test checks this is not stale (GameIsPaused may update after the event returns).
            if (auto* ui = RE::UI::GetSingleton()) State().set_paused(ui->GameIsPaused());
            if (name == RE::LoadingMenu::MENU_NAME)
                log::Info(std::string("lifecycle: Loading Menu ") + (e->opening ? "opened" : "closed"));
            else if (name == RE::MainMenu::MENU_NAME && e->opening)
                log::Info("lifecycle: Main Menu opened; no game loaded");
            NoteInteractive();
        } catch (...) {
            log::Error("lifecycle: menu sink threw");
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kDataLoaded: {
        static MenuSink& sink = *new MenuSink();
        auto* ui = RE::UI::GetSingleton();
        ui->AddEventSink<RE::MenuOpenCloseEvent>(&sink);
        State().on_data_loaded();
        log::Info("SkyrimPython core: data loaded");
        // The sink sees only events from now on. If the main menu opened before kDataLoaded, record it here
        // (on_menu is idempotent, so a sink event for the same opening does no harm).
        if (ui->IsMenuOpen(RE::MainMenu::MENU_NAME)) {
            State().on_menu(std::string(RE::MainMenu::MENU_NAME), true);
            State().set_paused(ui->GameIsPaused());
            log::Info("lifecycle: Main Menu opened; no game loaded (already open at kDataLoaded)");
        }
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame:
        State().on_pre_load_game();
        log::Info("lifecycle: loading a save");
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        const bool ok = reinterpret_cast<std::uintptr_t>(m->data) != 0;  // a packed bool, not a pointer
        State().on_post_load_game(ok);
        log::Info(std::string("lifecycle: save load ") + (ok ? "succeeded" : "FAILED") + ", epoch " + std::to_string(State().epoch()));
        break;
    }
    case SKSE::MessagingInterface::kNewGame:
        State().on_new_game();
        log::Info("lifecycle: new game, epoch " + std::to_string(State().epoch()));
        break;
    default:
        return;
    }
    NoteInteractive();
}

bool DataReady() { return State().data_ready(); }
bool GameLoaded() { return State().game_loaded(); }
bool Loading() { return State().loading(); }
bool Interactive() { return State().interactive(); }
bool Paused() { return State().paused(); }
std::uint32_t Epoch() { return State().epoch(); }
std::uint32_t LoadsStarted() { return State().loads_started(); }
std::optional<std::string> NotReadyReason(bool require_unpaused) { return State().not_ready_reason(require_unpaused); }
std::vector<std::string> OpenMenus() { return State().open_menus(); }
json BaseState() { return State().base_state(); }
}
```
Events arrive on the main thread (messaging) and the UI thread (menu sink); every reader goes through `LifecycleState`'s mutex. Whether the main menu opens before or after `kDataLoaded` is not known in advance; the seed above makes both orders give the same state, and the log line says which one happened (Step 9 records it). These log lines are what later tasks wait for; the index fixes four of them exactly: `SkyrimPython core: data loaded`, `lifecycle: Main Menu open` (a prefix of the line above), `lifecycle: interactive = true`, and Task 26's `python host: running`.

- [ ] **Step 7: Core entry point and the CPython link check**

`plugin/src/PythonHost.h`:
```cpp
#pragma once
#include <string>

namespace spy::host {
// Py_GetVersion() of the linked python314.dll (safe before Py_Initialize). Proves the core's import resolved.
std::string LinkedPythonVersion();
}
```
`plugin/src/PythonHost.cpp` (Python headers only; never CommonLib):
```cpp
#include <Python.h>
#include "PythonHost.h"

namespace spy::host {
std::string LinkedPythonVersion()
{
    const std::string v = Py_GetVersion();      // "3.14.8 (tags/v3.14.8:..., ...) [MSC v.19xx 64 bit (AMD64)]"
    return v.substr(0, v.find(' '));
}
}
```

`plugin/src/main.cpp`:
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include "PythonHost.h"
#include "Settings.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/base_sink.h>
#include <filesystem>
#include <format>
#include <fstream>
#include <mutex>

using namespace std::literals;

// The core is never loaded by SKSE itself (it sits in Plugins/SkyrimPython/), but CommonLib looks up this
// declaration in the calling module (Step 2), so it is declared here as well.
SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "SkyrimPythonCore"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace {
// spdlog's file sinks take a narrow file name, which Windows reads in the ANSI code page; a Documents folder whose
// name that code page cannot spell would get no log. This sink opens the std::filesystem::path itself (wide).
class PathFileSink final : public spdlog::sinks::base_sink<std::mutex>
{
public:
    explicit PathFileSink(const std::filesystem::path& p) : out_(p, std::ios::binary | std::ios::trunc)
    {
        if (!out_) throw std::runtime_error("cannot open the log file");
    }

protected:
    void sink_it_(const spdlog::details::log_msg& msg) override
    {
        spdlog::memory_buf_t buf;
        formatter_->format(msg, buf);
        out_.write(buf.data(), static_cast<std::streamsize>(buf.size()));
    }
    void flush_() override { out_.flush(); }

private:
    std::ofstream out_;
};

void SetupLog(const std::optional<std::filesystem::path>& skseDir)
{
    try {
        std::filesystem::path path = skseDir ? *skseDir : std::filesystem::path{};
        path /= "SkyrimPython.log"sv;
        auto sink = std::make_shared<PathFileSink>(path);
        auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
        logger->set_level(spdlog::level::info);
        logger->flush_on(spdlog::level::info);
        spy::log::Set(std::move(logger));
    } catch (const std::exception&) {
        spy::log::Set(std::make_shared<spdlog::logger>("none"));  // no sinks: logging becomes a no-op
    }
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    try {
        spy::life::OnMessage(m);
    } catch (const std::exception& e) {
        spy::log::Error(std::string("lifecycle handler threw: ") + e.what());
    } catch (...) {
        spy::log::Error("lifecycle handler threw");
    }
}
}

extern "C" __declspec(dllexport) bool SpyCore_Load(const SKSE::LoadInterface* skse)
{
    try {
        SKSE::Init(skse, { .log = false });
        const auto skseDir = SKSE::log::log_directory();
        SetupLog(skseDir);
        if (!skseDir) spy::log::Error("SKSE could not resolve the Documents folder; logging to the game folder");
        spy::settings::SetRuntime({ SPY_VERSION, REL::Module::get().version().string("."),
                                    REL::Module::IsAE() ? "AE" : "SE", skseDir });
        spy::log::Info(std::format("SkyrimPythonCore {} on Skyrim {} ({}); linked against CPython {}", SPY_VERSION,
            spy::settings::GetRuntime().game_version, spy::settings::GetRuntime().runtime,
            spy::host::LinkedPythonVersion()));
        spy::settings::Load();
        SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
        return true;
    } catch (...) {
        return false;  // the loader logs "SpyCore_Load returned false"
    }
}
```
(The loader has already refused unsupported runtimes, so the core does not check again.)

- [ ] **Step 8: Deploy script: none (ruling R42)**

The game runs on the Windows box, and `tools/box/box_deploy.sh` (Task 1b) already deploys the two-DLL layout: `out/SkyrimPython.dll` to `Plugins\SkyrimPython.dll`, `out/SkyrimPythonCore.dll` (required, not older than the loader) to `Plugins\SkyrimPython\SkyrimPythonCore.dll`, the ini to `Plugins\SkyrimPython.ini`; `--python` deploys the runtime. Check the targets offline:
```bash
tools/box/test_box_tools.sh | grep -E 'two-DLL|older than the loader|without SkyrimPythonCore|OFFLINE'
```
Expected: `ok   two-DLL targets: loader, core in SkyrimPython\, ini`, the two refusals, `BOX TOOLS OFFLINE PASS`. `tools/deploy.sh` (the Linux game) is not changed.

- [ ] **Step 9: Build, deploy, launch, check both logs (STOP gate)**

Every wait is bounded. The game runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); the save is loaded through DevBench.
```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
objdump -p out/SkyrimPython.dll | grep -i "DLL Name" | grep -ci python    # expect 0: the loader imports no Python
objdump -p out/SkyrimPythonCore.dll | grep -i "DLL Name: python314.dll"   # expect one line
source tools/box/box_env.sh
tools/box/box_deploy.sh --move SkyrimPythonSpike.dll SkyrimPython/spike-off/SkyrimPythonSpike.dll   # SKSE loads only Plugins\*.dll
tools/box/box_deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/box/box_deploy.sh --python
tools/box/box_launch.sh
tools/box/box_wait_log.sh SkyrimPythonLoader.log "SpyCore_Load returned true|Python disabled" 600
tools/box/box_session.sh env && source out/box-live.env
tools/box/box_files.sh pull SkyrimPythonLoader.log >/dev/null && cat "$SKSE_LOG_DIR/SkyrimPythonLoader.log"
# GATE: SKSE::Init from the core and the core's messaging listener must work. No line here within 10 minutes: STOP.
tools/box/box_wait_log.sh SkyrimPython.log "SkyrimPython core: data loaded" 600 \
  || echo "STOP: the core never saw kDataLoaded; ask the owner"
tools/box/box_wait_log.sh SkyrimPython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load spikebase"     # stands in for Continue; then Task 1b block B6 (wait for gameplay)
tools/box/box_wait_log.sh SkyrimPython.log "lifecycle: interactive = true" 300
tools/box/box_files.sh pull SkyrimPython.log >/dev/null && cat "$SKSE_LOG_DIR/SkyrimPython.log"
tools/box/box_quit.sh     # NOT_CLEAN with NO_NEW_CRASH_LOG: tools/box/box_stop.sh (Task 1b Known issue)
```
(The spike DLL is moved, not deleted (recorded in `out/box-moves.txt`); move it back for spike re-runs: `tools/box/box_deploy.sh --move SkyrimPython/spike-off/SkyrimPythonSpike.dll SkyrimPythonSpike.dll`.)

**STOP gate.** This is the first launch where `SKSE::Init` runs in a second DLL (the core) and the core registers the messaging listener with the loader's plugin handle. If the loader log ends with `SpyCore_Load returned true` but the core log has no `SkyrimPython core: data loaded` line (or no core log exists), STOP and ask the owner with both logs attached; do not move the listener into the loader or call `SKSE::Init` differently on your own.

Expected loader log: `SkyrimPython loader 0.1.0 on Skyrim 1.6.1170.0 (AE)`, `loaded python314.dll from ...\SkyrimPython\python\python314.dll`, `core loaded from ...\SkyrimPythonCore.dll; python314.dll in use: <the same python path>`, `SpyCore_Load returned true`. Expected core log, in order: `SkyrimPythonCore 0.1.0 on Skyrim 1.6.1170.0 (AE); linked against CPython 3.14.8`, `settings: ...\Data\SKSE\Plugins\SkyrimPython.ini python_enabled=true scripts_enabled=true bridge_enabled=true port=45977 ...`, `SkyrimPython core: data loaded`, `lifecycle: Main Menu opened; no game loaded` (with ` (already open at kDataLoaded)` if the menu opened first: record which order this runtime uses in `spikes/RESULTS.md`), `lifecycle: loading a save`, `lifecycle: Loading Menu opened`, `lifecycle: save load succeeded, epoch 1`, `lifecycle: Loading Menu closed`, `lifecycle: interactive = true, epoch 1` (`save load succeeded` and `Loading Menu closed` may swap; `interactive = true` comes after both). Any version other than 1.6.1170.0: STOP and ask the owner. Python is linked but not initialised yet (Task 26).

- [ ] **Step 10: Check the unsupported-runtime guard (test-hook loader; no source edit)**

`tools/remote_build.sh` re-syncs the local tree over the box's checkout on every call, so the check uses the `SPY_TEST_HOOKS` override instead of editing `main.cpp`:
```bash
echo "SPY_TEST_HOOKS=ON" > plugin/build-options.local
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll   # build log ends with SPY_TEST_HOOKS=ON
tools/box/box_deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/box/box_launch.sh --env SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1   # launch.log shows "env SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1"
tools/box/box_wait_log.sh SkyrimPythonLoader.log "runtime check:|TEST HOOK: SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME not set" 600
tools/box/box_files.sh pull SkyrimPythonLoader.log >/dev/null && cat out/box-skse/SkyrimPythonLoader.log
tools/box/box_quit.sh     # or box_stop.sh on NOT_CLEAN
```
Expected: `TEST HOOK: SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1: treating this runtime as unverified`, then `runtime check: Skyrim 1.6.1170.0 is not a verified runtime; Python and SkyrimPython stay off`, no `loaded python314.dll` line, and the game reaches the main menu normally. If the log shows `TEST HOOK: SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME not set`, the variable did not reach the game through `box_launch.sh --env` (check `launch.log`, Task 1b block B10, for the `env` line; `box_setup.sh` must have installed the R42 `launch.ps1`): STOP and ask the owner. Afterwards quit the game, turn the hooks off and rebuild:
```bash
: > plugin/build-options.local    # empty: hooks off
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll   # build log ends with SPY_TEST_HOOKS=OFF
tools/box/box_deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
git status --short plugin    # no source file changed
```

- [ ] **Step 11: Commit**

```bash
git add plugin .gitignore docs/PINS.md && git commit -m "feat(plugin): loader and core DLLs with logging, settings and lifecycle"
```
