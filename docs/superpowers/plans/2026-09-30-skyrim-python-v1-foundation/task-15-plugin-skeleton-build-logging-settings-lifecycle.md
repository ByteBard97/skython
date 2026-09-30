# Task 15: Plugin skeleton: build, logging, settings, lifecycle

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `plugin/CMakeLists.txt`, `plugin/vcpkg.json`, `plugin/build.ps1`, `plugin/config/SkyrimPython.ini`, `plugin/config/SkyrimPython.dev.ini`
- Create: `plugin/src/main.cpp`, `plugin/src/Log.h`, `plugin/src/Log.cpp`, `plugin/src/Settings.h`, `plugin/src/Settings.cpp`, `plugin/src/Lifecycle.h`, `plugin/src/Lifecycle.cpp`

**Interfaces:**
- Consumes: `spy::Config`, `spy::parse_config` (Task 12).
- Produces:
  - `spy::log::Info/Warn/Error(const std::string&)` (no RE headers needed to call them)
  - `spy::settings::Load()`, `const spy::Config& spy::settings::Get()`, `std::filesystem::path spy::settings::SessionFile(const std::filesystem::path& skseLogDir)`
  - `spy::life::OnMessage(SKSE::MessagingInterface::Message*)`, `bool spy::life::DataReady()`, `bool spy::life::Interactive()`, `bool spy::life::GameLoaded()`, `bool spy::life::Loading()`, `std::uint32_t spy::life::Epoch()`, `std::optional<std::string> spy::life::NotReadyReason()` (`"starting"`, `"no_game"`, `"loading"`), `std::vector<std::string> spy::life::OpenMenus()`
  - Build: `tools/remote_build.sh plugin SkyrimPython.dll` → `out/SkyrimPython.dll`

Lifecycle rules (spec 4.1): `data_ready` after `kDataLoaded`. `game_loaded` becomes true on `kPostLoadGame` (success only) or `kNewGame`, each of which also increments `epoch`; it becomes false on `kPreLoadGame` and when the Main Menu opens. `loading` mirrors the Loading Menu. `interactive = data_ready && game_loaded && !loading`. This works whichever order S6 recorded for `kPostLoadGame` vs Loading Menu close. `kPostLoadGame`'s `data` is a packed bool, not a pointer.

- [ ] **Step 1: Build files**

`plugin/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.21)
project(SkyrimPython VERSION 0.1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
# Use the option names recorded in Task 0 Step 3; VR off.
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/extern/CommonLibSSE-NG commonlibsse-ng)

set(SPY_CORE_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../core spy_core)

file(GLOB SPY_PLUGIN_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
add_library(SkyrimPython SHARED ${SPY_PLUGIN_SOURCES})
target_compile_definitions(SkyrimPython PRIVATE SPY_VERSION="${PROJECT_VERSION}" WIN32_LEAN_AND_MEAN NOMINMAX)
target_link_libraries(SkyrimPython PRIVATE CommonLibSSE::CommonLibSSE spy_core ws2_32 bcrypt)
```

`plugin/vcpkg.json`: generate it the same way as the spike's (Task 1 Step 3), with name `skyrim-python`:
```bash
python3 - <<'EOF'
import json, pathlib
src = json.loads(pathlib.Path("plugin/extern/CommonLibSSE-NG/vcpkg.json").read_text())
deps = src.get("dependencies", [])
names = [d if isinstance(d, str) else d["name"] for d in deps]
if "spdlog" not in names: deps.append("spdlog")
pathlib.Path("plugin/vcpkg.json").write_text(json.dumps(
    {"name": "skyrim-python", "version-string": "0.1.0", "dependencies": deps}, indent=2) + "\n")
print(deps)
EOF
```

`plugin/build.ps1`:
```powershell
param([string]$VcpkgRoot = "C:\vcpkg", [string]$Config = "Release")
$ErrorActionPreference = "Stop"
$toolchain = Join-Path $VcpkgRoot "scripts\buildsystems\vcpkg.cmake"
$src = $PSScriptRoot; $build = Join-Path $src "build"
cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_TOOLCHAIN_FILE=$toolchain" -DVCPKG_TARGET_TRIPLET=x64-windows-static-md
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build $build --config $Config --parallel
if ($LASTEXITCODE -ne 0) { throw "build failed" }
```

`plugin/config/SkyrimPython.ini` (shipped defaults):
```ini
; SkyrimPython settings. Lines are "key = value"; ';' starts a comment.
[Server]
; Let processes outside the game (such as skyrim-dm) connect over loopback TCP.
allow_external_clients = false
port = 45977
; Leave empty to generate a new random token each game start (written to the session file).
fixed_token =
; Empty: Documents/My Games/<game>/SKSE/SkyrimPython/session.json
session_file =
default_timeout_ms = 5000
max_inflight_per_connection = 256
max_inflight_total = 1024
enable_console = false
```

`plugin/config/SkyrimPython.dev.ini` (deployed by the dev tools as `SkyrimPython.ini`; never ships):
```ini
[Server]
allow_external_clients = true
port = 45977
fixed_token =
session_file =
default_timeout_ms = 5000
max_inflight_per_connection = 256
max_inflight_total = 1024
enable_console = false
```
No token is ever committed: clients read the per-run token from the session file (`source tools/env.sh` sets `SKYRIM_PYTHON_SESSION`).

- [ ] **Step 2: Logging and settings (no RE headers)**

`plugin/src/Log.h`:
```cpp
#pragma once
#include <string>

// Callable from translation units that must not include CommonLib (Server.cpp).
namespace spy::log {
void Info(const std::string& line);
void Warn(const std::string& line);
void Error(const std::string& line);
}
```

`plugin/src/Log.cpp`:
```cpp
#include "Log.h"
#include <spdlog/spdlog.h>

namespace spy::log {
void Info(const std::string& line) { spdlog::info("{}", line); }
void Warn(const std::string& line) { spdlog::warn("{}", line); }
void Error(const std::string& line) { spdlog::error("{}", line); }
}
```

`plugin/src/Settings.h`:
```cpp
#pragma once
#include "spy/config.h"
#include <filesystem>

namespace spy::settings {
// Reads Data/SKSE/Plugins/SkyrimPython.ini (the game's working directory is the game folder).
// Missing file: defaults. Invalid value: defaults (external clients off) and an error in the log.
void Load();
const Config& Get();
std::filesystem::path SessionFile(const std::filesystem::path& skseLogDir);
}
```

`plugin/src/Settings.cpp`:
```cpp
#include "Settings.h"
#include "Log.h"
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace spy::settings {
namespace {
Config g_config;
constexpr const char* kIniPath = "Data/SKSE/Plugins/SkyrimPython.ini";
}

void Load()
{
    std::ifstream f(kIniPath);
    if (!f) {
        log::Info(std::string("settings: ") + kIniPath + " not found; using defaults");
        g_config = Config{};
        return;
    }
    std::stringstream ss;
    ss << f.rdbuf();
    try {
        g_config = parse_config(ss.str());
    } catch (const std::invalid_argument& e) {
        log::Error(std::string("settings: ") + kIniPath + ": " + e.what() + "; using defaults");
        g_config = Config{};
    }
    log::Info("settings: port=" + std::to_string(g_config.port) +
              " allow_external_clients=" + (g_config.allow_external_clients ? "true" : "false") +
              " default_timeout_ms=" + std::to_string(g_config.default_timeout_ms) +
              " fixed_token=" + (g_config.fixed_token.empty() ? "no" : "yes"));
}

const Config& Get() { return g_config; }

std::filesystem::path SessionFile(const std::filesystem::path& skseLogDir)
{
    if (!g_config.session_file.empty()) return std::filesystem::path(g_config.session_file);
    return skseLogDir / "SkyrimPython" / "session.json";
}
}
```

- [ ] **Step 3: Lifecycle**

`plugin/src/Lifecycle.h`:
```cpp
#pragma once
#include <SKSE/SKSE.h>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace spy::life {
void OnMessage(SKSE::MessagingInterface::Message* m);  // call from the plugin's messaging listener
bool DataReady();
bool GameLoaded();
bool Loading();
bool Interactive();
std::uint32_t Epoch();
// nullopt when interactive; otherwise "starting", "no_game" or "loading".
std::optional<std::string> NotReadyReason();
std::vector<std::string> OpenMenus();
}
```

`plugin/src/Lifecycle.cpp`:
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include <RE/Skyrim.h>
#include <atomic>
#include <mutex>
#include <set>

namespace spy::life {
namespace {
std::atomic_bool g_dataReady{false}, g_gameLoaded{false}, g_loading{false};
std::atomic_uint32_t g_epoch{0};
std::mutex g_menuMx;
std::set<std::string> g_menus;

struct MenuSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (!e) return RE::BSEventNotifyControl::kContinue;
        const std::string name = e->menuName.c_str();
        {
            std::lock_guard l(g_menuMx);
            if (e->opening) g_menus.insert(name);
            else g_menus.erase(name);
        }
        if (name == RE::LoadingMenu::MENU_NAME) {
            g_loading = e->opening;
            log::Info(std::string("lifecycle: Loading Menu ") + (e->opening ? "opened" : "closed"));
        } else if (name == RE::MainMenu::MENU_NAME && e->opening) {
            g_gameLoaded = false;
            log::Info("lifecycle: Main Menu opened; no game loaded");
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kDataLoaded: {
        static MenuSink sink;
        RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(&sink);
        g_dataReady = true;
        log::Info("lifecycle: data loaded");
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame:
        g_gameLoaded = false;
        log::Info("lifecycle: loading a save");
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        const bool ok = reinterpret_cast<std::uintptr_t>(m->data) != 0;
        if (ok) {
            ++g_epoch;
            g_gameLoaded = true;
        }
        log::Info(std::string("lifecycle: save load ") + (ok ? "succeeded" : "FAILED") + ", epoch " + std::to_string(g_epoch.load()));
        break;
    }
    case SKSE::MessagingInterface::kNewGame:
        ++g_epoch;
        g_gameLoaded = true;
        log::Info("lifecycle: new game, epoch " + std::to_string(g_epoch.load()));
        break;
    default:
        break;
    }
}

bool DataReady() { return g_dataReady; }
bool GameLoaded() { return g_gameLoaded; }
bool Loading() { return g_loading; }
bool Interactive() { return g_dataReady && g_gameLoaded && !g_loading; }
std::uint32_t Epoch() { return g_epoch; }

std::optional<std::string> NotReadyReason()
{
    if (!g_dataReady) return "starting";
    if (!g_gameLoaded) return "no_game";
    if (g_loading) return "loading";
    return std::nullopt;
}

std::vector<std::string> OpenMenus()
{
    std::lock_guard l(g_menuMx);
    return {g_menus.begin(), g_menus.end()};
}
}
```

- [ ] **Step 4: Entry point**

`plugin/src/main.cpp` (Task 16 and Task 17 replace this file with their versions):
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include "Settings.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "SkyrimPython"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace {
void SetupLog()
{
    auto path = SKSE::log::log_directory();
    *path /= "SkyrimPython.log"sv;
    auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path->string(), true);
    auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
    logger->set_level(spdlog::level::info);
    logger->flush_on(spdlog::level::info);
    spdlog::set_default_logger(std::move(logger));
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    spy::life::OnMessage(m);
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse);
    SetupLog();
    spy::log::Info(std::format("SkyrimPython {} loading on Skyrim {} ({})", SPY_VERSION,
        REL::Module::get().version().string(), REL::Module::IsAE() ? "AE" : "SE"));
    spy::settings::Load();
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
```

- [ ] **Step 5: Build, deploy, launch, check the lifecycle log**

```bash
tools/remote_build.sh plugin SkyrimPython.dll
# Move the spike DLL out of the load path (a move, not a delete; it can be moved back for re-runs):
source tools/env.sh
mkdir -p "$SIMPLESKYRIM_DATA/SKSE/Plugins-disabled"
mv "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPythonSpike.dll" "$SIMPLESKYRIM_DATA/SKSE/Plugins-disabled/"
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh
source tools/env.sh
until grep -q "lifecycle: data loaded" "$SKSE_LOG_DIR/SkyrimPython.log" 2>/dev/null; do sleep 2; done
# In game: Continue (load the latest save). Then:
until grep -q "lifecycle: Loading Menu closed" "$SKSE_LOG_DIR/SkyrimPython.log" 2>/dev/null; do sleep 2; done
cat "$SKSE_LOG_DIR/SkyrimPython.log"
```
Expected, in order: `SkyrimPython 0.1.0 loading on Skyrim 1.6.1170.0 (AE)`, `settings: port=45977 allow_external_clients=true ...`, `lifecycle: data loaded`, `lifecycle: Main Menu opened; no game loaded`, `lifecycle: loading a save`, `lifecycle: Loading Menu opened`, `lifecycle: save load succeeded, epoch 1`, `lifecycle: Loading Menu closed` (the last two may be swapped; S6 recorded which).

- [ ] **Step 6: Commit**

```bash
git add plugin && git commit -m "feat(plugin): SKSE skeleton with logging, settings and lifecycle tracking"
```
