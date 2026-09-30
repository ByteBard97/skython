# Task 1: Build tooling and a spike DLL that loads

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Create: `tools/remote_build.sh`, `tools/deploy.sh`, `tools/launch_game.sh`, `tools/env.sh`
- Create: `spikes/spike-dll/CMakeLists.txt`, `spikes/spike-dll/vcpkg.json`, `spikes/spike-dll/build.ps1`, `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Produces: `tools/remote_build.sh <cmake-source-dir-relative-to-repo> <dll-name>` → copies the built DLL to `out/<dll-name>`; `tools/deploy.sh out/<dll-name> [ini-file]`; `tools/launch_game.sh`; `source tools/env.sh` exports `SKYRIM_PYTHON_SESSION`, `SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`.

- [ ] **Step 1: Write the remote build script**

```bash
mkdir -p tools
cat > tools/remote_build.sh <<'EOF'
#!/usr/bin/env bash
# Sync this repo to the Windows build box, build one CMake project there, copy the DLL back.
# Usage: tools/remote_build.sh <source-dir> <DllName.dll>
#   e.g. tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
set -euo pipefail
SRC_DIR="$1"; DLL="$2"
WIN=geoff@192.168.0.211
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ssh "$WIN" 'if not exist C:\dev\skyrim-python mkdir C:\dev\skyrim-python'
# The CommonLibSSE-NG submodule is large and rarely changes: send it only on the first sync or with FORCE_EXTERN=1.
EXTERN_EXCL=()
if [ "${FORCE_EXTERN:-0}" != 1 ] && ssh "$WIN" 'if exist C:\dev\skyrim-python\plugin\extern\CommonLibSSE-NG\CMakeLists.txt (exit 0) else (exit 1)'; then
  EXTERN_EXCL=(--exclude='./plugin/extern')
fi
tar --exclude='./.git' --exclude='*/build' --exclude='./out' --exclude='./python/.venv' "${EXTERN_EXCL[@]}" -czf - . \
  | ssh "$WIN" 'cd /d C:\dev\skyrim-python && tar -xzf -'
WIN_SRC="C:\\dev\\skyrim-python\\${SRC_DIR//\//\\}"
ssh "$WIN" "powershell -NoProfile -ExecutionPolicy Bypass -File ${WIN_SRC}\\build.ps1"
mkdir -p out
scp "$WIN:C:/dev/skyrim-python/${SRC_DIR}/build/Release/${DLL}" "out/${DLL}"
echo "built out/${DLL}"
EOF
chmod +x tools/remote_build.sh
```

- [ ] **Step 2: Write deploy, launch and env scripts**

```bash
cat > tools/env.sh <<'EOF'
# source tools/env.sh
export SIMPLESKYRIM_DATA="$HOME/Games/SimpleSkyrim/Stock Game/Data"
PFX="$HOME/.local/share/Steam/steamapps/compatdata/4190904831/pfx"
export SKSE_LOG_DIR="$PFX/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE"
export SKYRIM_PYTHON_SESSION="$SKSE_LOG_DIR/SkyrimPython/session.json"
EOF
cat > tools/deploy.sh <<'EOF'
#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini]  -- copies into SimpleSkyrim's Data/SKSE/Plugins
set -euo pipefail
source "$(dirname "$0")/env.sh"
DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"
cp -v "$1" "$DEST/"
if [ $# -ge 2 ]; then cp -v "$2" "$DEST/$(basename "${1%.dll}").ini"; fi
EOF
cat > tools/launch_game.sh <<'EOF'
#!/usr/bin/env bash
# Launch SimpleSkyrim through GE-Proton with skse64_loader.exe (not MO2), in the background.
set -euo pipefail
STEAM_ROOT="$HOME/.local/share/Steam"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$STEAM_ROOT/steamapps/compatdata/4190904831"
export STEAM_COMPAT_MOUNTS="/mnt/games/SteamLibrary"
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
PROTON="$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/proton"
cd "$HOME/Games/SimpleSkyrim/Stock Game"
nohup "$PROTON" waitforexitandrun "$(pwd)/skse64_loader.exe" > /tmp/skyrim-python-launch.log 2>&1 &
echo "launched pid $!"
EOF
chmod +x tools/deploy.sh tools/launch_game.sh
```

- [ ] **Step 3: Write the spike DLL skeleton**

`spikes/spike-dll/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.21)
project(SkyrimPythonSpike VERSION 0.0.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
# Use the option names recorded in Task 0 Step 3; VR off.
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../plugin/extern/CommonLibSSE-NG commonlibsse-ng)
add_library(SkyrimPythonSpike SHARED src/main.cpp src/Net.cpp)
target_link_libraries(SkyrimPythonSpike PRIVATE CommonLibSSE::CommonLibSSE ws2_32)
```

`spikes/spike-dll/vcpkg.json` — copy the `dependencies` list from `plugin/extern/CommonLibSSE-NG/vcpkg.json` verbatim, then add `"spdlog"` if it is not already there:
```bash
python3 - <<'EOF'
import json, pathlib
src = json.loads(pathlib.Path("plugin/extern/CommonLibSSE-NG/vcpkg.json").read_text())
deps = src.get("dependencies", [])
names = [d if isinstance(d, str) else d["name"] for d in deps]
if "spdlog" not in names: deps.append("spdlog")
pathlib.Path("spikes/spike-dll/vcpkg.json").write_text(json.dumps(
    {"name": "skyrim-python-spike", "version-string": "0.0.1", "dependencies": deps}, indent=2) + "\n")
print(deps)
EOF
```
(This is a one-off generator step run by hand, not a committed script.)

`spikes/spike-dll/build.ps1`:
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

`spikes/spike-dll/src/Net.h`:
```cpp
#pragma once
#include <cstdint>
#include <functional>
#include <string>

namespace spike {
void Log(const std::string& line);  // defined in main.cpp
// Line-based TCP listener on 127.0.0.1:port. Each received line is passed to handler
// on the listener thread; handler's return value (plus '\n') is sent back.
void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler);
}
```

`spikes/spike-dll/src/Net.cpp` (no CommonLib headers in this file):
```cpp
#include "Net.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <thread>

namespace spike {
void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler)
{
    std::thread([port, handler] {
        WSADATA wsa;
        WSAStartup(MAKEWORD(2, 2), &wsa);
        SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
        if (bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, 4) != 0) {
            Log("spike: bind/listen failed, WSA error " + std::to_string(WSAGetLastError()));
            return;
        }
        Log("spike: listening on 127.0.0.1:" + std::to_string(port));
        for (;;) {
            SOCKET c = accept(ls, nullptr, nullptr);
            if (c == INVALID_SOCKET) continue;
            Log("spike: client connected");
            std::string buf;
            char tmp[4096];
            for (;;) {
                int n = recv(c, tmp, sizeof tmp, 0);
                if (n <= 0) break;
                buf.append(tmp, n);
                for (auto pos = buf.find('\n'); pos != std::string::npos; pos = buf.find('\n')) {
                    std::string line = buf.substr(0, pos);
                    buf.erase(0, pos + 1);
                    std::string reply = handler(line) + "\n";
                    send(c, reply.data(), static_cast<int>(reply.size()), 0);
                }
            }
            closesocket(c);
            Log("spike: client disconnected");
        }
    }).detach();
}
}
```

`spikes/spike-dll/src/main.cpp` (skeleton; Tasks 2–6 add handlers to `Handle`):
```cpp
#include "Net.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 0, 1, 0 },
    .Name = "SkyrimPythonSpike"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace spike {
void Log(const std::string& line) { spdlog::info("{}", line); }

std::string Handle(const std::string& line)
{
    if (line.rfind("echo ", 0) == 0) return line.substr(5);
    return "ERR unknown command";
}
}

namespace {
void SetupLog()
{
    auto path = SKSE::log::log_directory();
    *path /= "SkyrimPythonSpike.log"sv;
    auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path->string(), true);
    auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
    logger->set_level(spdlog::level::info);
    logger->flush_on(spdlog::level::info);
    spdlog::set_default_logger(std::move(logger));
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kPostLoad:
        spike::StartListener(45978, spike::Handle);
        break;
    case SKSE::MessagingInterface::kDataLoaded:
        spike::Log("kDataLoaded");
        break;
    default:
        break;
    }
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse);
    SetupLog();
    spike::Log(std::format("SkyrimPythonSpike loading on Skyrim {}", REL::Module::get().version().string()));
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
```
(The spike uses port 45978 so it can never collide with the real plugin's 45977.)

- [ ] **Step 4: Build, deploy, launch, check the log**

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
tools/deploy.sh out/SkyrimPythonSpike.dll
tools/launch_game.sh
source tools/env.sh
# Monitor-style wait: until the log shows the listener line (no foreground sleep chains)
until grep -q "listening on 127.0.0.1:45978" "$SKSE_LOG_DIR/SkyrimPythonSpike.log" 2>/dev/null; do sleep 2; done
cat "$SKSE_LOG_DIR/SkyrimPythonSpike.log"
```
Expected: `SkyrimPythonSpike loading on Skyrim 1.6.1170.0`, `spike: listening on 127.0.0.1:45978`. If the build fails in CommonLibSSE-NG's configure step, the missing dependency is named in the CMake error: add it to `spikes/spike-dll/vcpkg.json` and rebuild.

- [ ] **Step 5: Commit**

```bash
git add tools spikes/spike-dll
git commit -m "chore(spike): remote build tooling and spike DLL skeleton"
```
