# skyrim-python v1 foundation (M0 spikes + M1 dispatcher) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle the unverified premises with spikes (M0), then build the loopback RPC plugin, generic Papyrus dispatcher and synchronous Python client (M1), so a Linux process can call any Papyrus function in the running game.

**Architecture:** A pure C++20 core library (`core/`: framing, JSON messages, value codec, form keys, config) is unit-tested on Linux and linked into the SKSE plugin (`plugin/`, CommonLibSSE-NG, built on the Windows box). The plugin runs one Winsock IO thread; every request becomes one `AddTask` on the main thread, which dispatches through the Papyrus VM; the VM callback hands the result back. A stdlib-only Python package (`python/src/skyrim`) speaks the same protocol, pinned by shared golden fixtures in `protocol/v1/`.

**Tech Stack:** C++20 (core, g++ 11 on Linux / MSVC v143), C++23 (plugin), CommonLibSSE-NG (alandtse `ng`, git submodule), nlohmann/json 3.11.3 and doctest 2.4.11 (vendored single headers), spdlog (vcpkg), Python ≥3.11 stdlib + pytest (dev only), uv.

**Spec:** `docs/specs/2026-09-30-skyrim-python-design.md` (draft 3). Read sections 4.1–4.5, 4.7, 5, 9 before starting.

**Scope of this plan:** M0 and M1 only. M1.5 (test ESP + engine probes), M2 (events, lifecycle, save/load, harness) and the skyrim-dm acceptance test get their own plan after M0's results, because spikes S2, S4, S5 and S6 decide parts of their design (see "After this plan" at the end).

## Global Constraints

- Transport: TCP bound to `127.0.0.1` only; default port `45977`; non-loopback peers refused.
- Framing: 4-byte little-endian length + UTF-8 JSON; max frame 16 MiB (`16 * 1024 * 1024` bytes).
- Protocol version `[1, 0]`; a major mismatch is refused.
- Token: 128-bit random, lowercase hex (32 chars), checked on every connection.
- `allow_external_clients` default `false` in the shipped ini; the dev ini sets `true`.
- Papyrus Int is 32-bit signed: out-of-range is `type_mismatch`, never truncation.
- No raw engine pointers kept across tasks; every task re-resolves by FormID.
- One `AddTask` per request or batch step, plus at most one more to convert a result when the VM callback runs off the main thread; a task never re-queues itself.
- `game.load` / `game.save` use `AddUITask` (M2; not in this plan).
- No work in `DllMain`; server thread starts at `kPostLoad`; game requests allowed only after `kDataLoaded` and the interactive signal.
- CommonLibSSE-NG: `alandtse/CommonLibSSE-NG`, branch `ng`, pinned submodule at a commit that supports 1.7.99/1.7.104 and Address Library format 5. Never use skyrim-re-toolkit's vendored copy.
- Verified runtime: AE 1.6.1170 under GE-Proton (`~/Games/SimpleSkyrim`). Release notes list only verified runtimes.
- Python client: ≥3.11, stdlib only at runtime; sync API only in v1.
- Licence: MIT. Copy no code from Chronicle (AGPL), SkyrimPlatform or DevBench (GPL). IntelEngine (Apache-2.0) and SkyLink (no LICENSE file) are read-only references.
- Commits: conventional style, no AI attribution lines (global rule; overrides harness trailers). Never `git push` without the owner saying so.
- Never delete files with `rm`; use `gio trash` (global rule).

## Review Focus

1. **Client disconnects with calls in flight** → the plugin must drop the late results quietly and keep running (no crash, no leak of the connection object). Pinned in Task 12 (outbox and limiter tests) and Task 19 (live test).
2. **A request while the game is at the main menu or loading** → `not_ready` with a `reason`, immediately, never a hang or crash. Pinned in Task 17 (`livetest/test_m1_mainmenu.py`).
3. **Wrong arguments** (Int out of range, string where Int expected, wrong argument count, unknown script or function) → `type_mismatch` / `unknown_function` before dispatch, with the parameter named in the message. Pinned in Tasks 10, 17.
4. **Garbage or oversized frames** from a client → that connection is closed, others and the game unaffected. Pinned in Task 9 (decoder test) and Task 16 (`livetest/test_m1_transport.py`).
5. **Stale session file** (game restarted, old token/port) → the client raises `Unauthorized` or a clear connection error within its connect timeout, never hangs. Pinned in Task 14.

---

## File Structure

```
skyrim-python/
  LICENSE                               MIT
  README.md                             (update in Task 20)
  .gitignore  .gitmodules
  third_party/nlohmann/json.hpp         vendored, MIT
  third_party/doctest/doctest.h         vendored, MIT
  protocol/
    PROTOCOL.md                         human-readable protocol v1
    v1/fixtures/*.json                  golden fixtures shared by C++ and Python tests
  core/                                 pure C++20; no Windows or CommonLib headers
    CMakeLists.txt
    include/spy/{framing,errors,values,refs,messages,config,outbox}.h   (outbox.h also holds InflightLimiter)
    src/{framing,errors,values,refs,messages,config,outbox}.cpp
    tests/{main,test_framing,test_values,test_refs,test_messages,test_config,test_outbox,test_fixtures}.cpp
  plugin/                               SKSE plugin (Windows build only)
    CMakeLists.txt  vcpkg.json  build.ps1
    extern/CommonLibSSE-NG              git submodule
    config/SkyrimPython.ini             shipped defaults (external clients off)
    config/SkyrimPython.dev.ini         dev defaults (external clients on, fixed token)
    src/main.cpp                        SKSE entry, logging, messaging listener
    src/Log.{h,cpp}                     spdlog setup; Log helpers usable from Winsock TU
    src/Settings.{h,cpp}                load ini via core::parse_config, session file
    src/Lifecycle.{h,cpp}               data_ready / loading / interactive / epoch, menu sink
    src/Wire.h                          json -> frame (invalid UTF-8 replaced, oversize -> error)
    src/Server.{h,cpp}                  Winsock IO thread, connections, handshake, session file (no RE headers)
    src/Dispatch.{h,cpp}                request routing, AddTask, timeouts, caps
    src/PapyrusValues.{h,cpp}           spy::Value <-> RE::BSScript::Variable, refs <-> forms
    src/Signatures.{h,cpp}              find a script function and check arguments against it
    src/Ops.{h,cpp}                     op handlers (call_static, call_method, properties, batch, resolve, state)
                                        (per-connection reply routing and caps are core's Outbox/InflightLimiter)
  spikes/
    spike-dll/{CMakeLists.txt,vcpkg.json,build.ps1,src/main.cpp,src/Net.cpp,src/Net.h}
    spike-dll/papyrus/SkyrimPython.psc  native Send declaration for S5
    spike_client.py
    RESULTS.md
  python/
    pyproject.toml
    src/skyrim/{__init__,_framing,errors,values,refs,discovery,client}.py
    tests/{support,conftest,test_framing,test_values_refs,test_fixtures,test_discovery,test_client}.py
  tools/
    remote_build.sh                     sync to Windows box, build a target, copy DLL back
    deploy.sh                           copy DLL + ini into SimpleSkyrim Stock Game
    launch_game.sh                      GE-Proton launch via skse64_loader.exe
    env.sh                              exports SKYRIM_PYTHON_SESSION for the SimpleSkyrim prefix
  livetest/
    conftest.py  support.py  test_m1_transport.py  test_m1_mainmenu.py  test_m1_calls.py  test_m1_batch_props.py
    test_m1_robustness.py  test_m1_throughput.py  results/throughput.json
  docs/specs/  docs/superpowers/plans/  docs/research/
```

Fixed paths used throughout:
- Windows box: `geoff@192.168.0.211`, checkout root `C:\dev\skyrim-python`, vcpkg `C:\vcpkg`, VS2022 Build Tools.
- Game: `~/Games/SimpleSkyrim/Stock Game` (plugins in `Data/SKSE/Plugins/`), Proton prefix `~/.local/share/Steam/steamapps/compatdata/4190904831`.
- Session file (inside the prefix): `.../compatdata/4190904831/pfx/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPython/session.json`.

---

# Part A: M0 spikes

The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**

### Task 0: Repository bootstrap

**Files:**
- Create: `LICENSE`, `.gitignore`, `.gitmodules` (via git), `third_party/nlohmann/json.hpp`, `third_party/doctest/doctest.h`, `plugin/extern/CommonLibSSE-NG` (submodule)

**Interfaces:**
- Produces: a git repo at `/home/geoff/projects/games/skyrim/skyrim-python` with the vendored headers and the pinned submodule every later task builds against.

- [ ] **Step 1: Initialise the repository**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
git init -b main
cat > .gitignore <<'EOF'
build/
**/build/
out/
*.dll
*.pdb
*.pex
__pycache__/
.venv/
.pytest_cache/
*.egg-info/
spikes/**/build/
EOF
cat > LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Geoffrey Wall

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
```

- [ ] **Step 2: Vendor the two single-header libraries**

```bash
mkdir -p third_party/nlohmann third_party/doctest
curl -fsSL -o third_party/nlohmann/json.hpp https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp
curl -fsSL -o third_party/doctest/doctest.h https://raw.githubusercontent.com/doctest/doctest/v2.4.11/doctest/doctest.h
grep -m1 'version 3.11.3' third_party/nlohmann/json.hpp && grep -m1 'DOCTEST_VERSION_STR "2.4.11"' third_party/doctest/doctest.h
```
Expected: both grep lines print.

- [ ] **Step 3: Add CommonLibSSE-NG as a pinned submodule**

```bash
git submodule add -b ng https://github.com/alandtse/CommonLibSSE-NG plugin/extern/CommonLibSSE-NG
cd plugin/extern/CommonLibSSE-NG
git log -1 --format='%H %cs %s'
grep -n -i -E '1\.7\.99|1\.7\.104|address library.*(5|v5)' CHANGELOG.md | head
grep -n -E 'option\(ENABLE_SKYRIM_(SE|AE|VR)|option\(BUILD_TESTS' CMakeLists.txt
cd ../../..
```
Expected: the CHANGELOG grep shows 1.7.99/1.7.104 support. If it does not, `git -C plugin/extern/CommonLibSSE-NG log --oneline -- CHANGELOG.md | head` and check out the newest commit that does. Record the commit hash and the exact option names printed by the last grep (they are used in Tasks 1 and 15).

- [ ] **Step 4: Commit**

```bash
git add .gitignore LICENSE .gitmodules third_party plugin/extern/CommonLibSSE-NG docs README.md
git commit -m "chore: bootstrap repo with vendored headers and pinned CommonLibSSE-NG"
```

### Task 1: Build tooling and a spike DLL that loads

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

### Task 2: Spike S1 — Linux connects to a socket bound inside Proton

**Files:**
- Create: `spikes/spike_client.py`, `spikes/RESULTS.md`

- [ ] **Step 1: Write the spike client**

```python
#!/usr/bin/env python3
"""Spike client: sends line commands to the spike DLL on 127.0.0.1:45978."""
import socket
import sys
import time


def send(sock_file, sock, line: str) -> str:
    sock.sendall((line + "\n").encode())
    return sock_file.readline().rstrip("\n")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "s1"
    with socket.create_connection(("127.0.0.1", 45978), timeout=30) as s:
        f = s.makefile("r", encoding="utf-8")
        if cmd == "s1":
            t0 = time.perf_counter()
            for i in range(1000):
                reply = send(f, s, f"echo ping-{i}")
                if reply != f"ping-{i}":
                    print(f"S1 FAIL at {i}: {reply!r}")
                    return 1
            dt = time.perf_counter() - t0
            print(f"S1 PASS: 1000 round trips in {dt:.3f}s ({1000 / dt:.0f}/s)")
            return 0
        print(send(f, s, " ".join(sys.argv[1:])))
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the running game (from Task 1)**

Run: `python3 spikes/spike_client.py s1`
Expected: `S1 PASS: 1000 round trips in ...`. A `ConnectionRefusedError` means S1 FAILS.

- [ ] **Step 3: Record the result**

Create `spikes/RESULTS.md`:
```markdown
# M0 spike results

| Spike | Date | Result | Evidence |
|---|---|---|---|
| S1 Linux → Wine-bound socket | <date> | PASS/FAIL | `<exact client output>`; runtime `<version from log>`; GE-Proton10-14 |
```
**If S1 fails: stop this plan.** Update spec section 4.2 to the fallback (the plugin connects outbound to a Linux-side broker), then re-plan Tasks 16 and 14.

- [ ] **Step 4: Commit**

```bash
git add spikes/spike_client.py spikes/RESULTS.md
git commit -m "test(spike): S1 loopback transport from Linux into Proton"
```

### Task 3: Spike S2 — which thread runs the VM callback; dispatch from inside it; argument ownership

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Add the S2 handler**

Add above `Handle` in `main.cpp`:
```cpp
#include <atomic>
#include <future>
#include <sstream>
#include <thread>

namespace spike {
std::string Tid()
{
    std::ostringstream s;
    s << std::this_thread::get_id();
    return s.str();
}

class Functor : public RE::BSScript::IStackCallbackFunctor
{
public:
    explicit Functor(std::function<void(RE::BSScript::Variable)> f) : f_(std::move(f)) {}
    void operator()(RE::BSScript::Variable a_result) override { f_(a_result); }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}

private:
    std::function<void(RE::BSScript::Variable)> f_;
};

// Runs fn on the main thread via AddTask and waits up to timeout for it to call done().
std::string OnMainThread(std::function<void(std::function<void(std::string)>)> fn, std::chrono::seconds timeout)
{
    auto p = std::make_shared<std::promise<std::string>>();
    auto fut = p->get_future();
    auto once = std::make_shared<std::atomic_bool>(false);
    SKSE::GetTaskInterface()->AddTask([fn, p, once] {
        fn([p, once](std::string r) {
            if (!once->exchange(true)) p->set_value(std::move(r));
        });
    });
    if (fut.wait_for(timeout) != std::future_status::ready) return "TIMEOUT";
    return fut.get();
}

std::string S2(int n)
{
    auto done = std::make_shared<std::atomic_int>(0);
    auto nested = std::make_shared<std::atomic_int>(0);
    auto cbTid = std::make_shared<std::string>();
    std::string mainTid;
    auto t0 = std::chrono::steady_clock::now();
    auto r = OnMainThread([&, n, done, nested, cbTid](std::function<void(std::string)> finish) {
        mainTid = Tid();
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (int i = 0; i < n; ++i) {
            RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor(
                [vm, n, done, nested, cbTid, finish](RE::BSScript::Variable) {
                    if (cbTid->empty()) *cbTid = Tid();
                    // Nested dispatch from inside the callback (the S2 question).
                    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb2{ new Functor(
                        [n, done, nested, finish](RE::BSScript::Variable v) {
                            if (v.IsInt()) nested->fetch_add(1);
                            if (done->fetch_add(1) + 1 == n) finish("ok");
                        }) };
                    auto* args2 = RE::MakeFunctionArguments(static_cast<std::int32_t>(1), static_cast<std::int32_t>(6));
                    vm->DispatchStaticCall("Utility"sv, "RandomInt"sv, args2, cb2);
                }) };
            auto* args = RE::MakeFunctionArguments();
            vm->DispatchStaticCall("Utility"sv, "GetCurrentRealTime"sv, args, cb);
        }
    }, std::chrono::seconds(60));
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    return std::format("S2 {} n={} completed={} nested_ints={} main_tid={} callback_tid={} elapsed_ms={}",
        r, n, done->load(), nested->load(), mainTid, *cbTid, ms);
}
}
```
And in `Handle`, before the fallback:
```cpp
    if (line.rfind("s2", 0) == 0) return S2(line.size() > 3 ? std::stoi(line.substr(3)) : 1000);
```

- [ ] **Step 2: Build, deploy, relaunch, run**

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game first (console: qqq), then:
tools/launch_game.sh
# load a save / coc into a cell so the game is running, then:
python3 spikes/spike_client.py s2 1000
python3 spikes/spike_client.py s2 10000
```
Expected PASS: `S2 ok n=1000 completed=1000 nested_ints=1000`, `callback_tid` recorded (compare to `main_tid`), no hang, and the game still responsive after the 10000 run. While the 10000 run executes, watch the game's memory in `top -p $(pgrep -f SkyrimSE.exe)`: RES must not grow by more than ~50 MB after the run completes (tests that the VM frees `MakeFunctionArguments` objects; a steady per-call growth means we must free them ourselves).

- [ ] **Step 3: Record and commit**

Add rows to `spikes/RESULTS.md` for S2 (callback thread, nested dispatch works?, per-call latency = elapsed/n, memory before/after). If nested dispatch hangs or crashes: record FAIL; Task 18's `batch` then uses a fresh `AddTask` per step (already the plan's default, so no re-plan needed).
```bash
git add spikes && git commit -m "test(spike): S2 VM callback thread, nested dispatch, argument ownership"
```

### Task 4: Spike S4 — bind a quest alias, `ForceRefTo` it, read it back

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

Spec S4 criterion: `ForceRefTo` on an alias bound by quest + alias ID, then `GetReference` returns the same reference. There is no test ESP until M1.5, so the spike borrows a running vanilla quest's filled `ReferenceAlias` in a throwaway save, forces it to the player, reads it back, and restores the original. Each step is its own main-thread task (no dispatch from inside a callback), so S4 does not depend on S2's nested-dispatch answer.

- [ ] **Step 1: Add the S4 handler**

```cpp
namespace spike {
struct AliasPick
{
    RE::VMHandle handle = 0;
    RE::FormID quest = 0, original = 0;
    std::uint32_t alias = 0;
};

// Dispatches ReferenceAlias.<fn>(arg) on the alias handle; returns the result's form id as hex ("00000000" for None).
std::string AliasCall(RE::VMHandle handle, std::string fn, RE::TESObjectREFR* arg)
{
    return OnMainThread([handle, fn, arg](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish](RE::BSScript::Variable v) {
            std::uint32_t id = 0;
            if (v.IsObject() && v.GetObject()) id = static_cast<std::uint32_t>(v.GetObject()->GetHandle() & 0xFFFFFFFF);
            finish(std::format("{:08X}", id));
        }) };
        RE::BSScript::IFunctionArguments* args = arg ? RE::MakeFunctionArguments(std::move(arg)) : RE::MakeFunctionArguments();
        if (!vm->DispatchMethodCall(handle, "ReferenceAlias"sv, RE::BSFixedString(fn), args, cb)) finish("DISPATCH_FAILED");
    }, std::chrono::seconds(10));
}

std::string S4()
{
    auto pick = std::make_shared<AliasPick>();
    auto found = OnMainThread([pick](std::function<void(std::string)> finish) {
        auto* dh = RE::TESDataHandler::GetSingleton();
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (auto* quest : dh->GetFormArray<RE::TESQuest>()) {
            if (!quest || !quest->IsRunning()) continue;
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* base : quest->aliases) {
                auto* ref = base ? skyrim_cast<RE::BGSRefAlias*>(base) : nullptr;
                auto* target = ref ? ref->GetReference() : nullptr;
                if (!target) continue;
                pick->handle = vm->GetObjectHandlePolicy()->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ref);
                pick->quest = quest->GetFormID();
                pick->alias = ref->aliasID;
                pick->original = target->GetFormID();
                return finish("found");
            }
        }
        finish("none");
    }, std::chrono::seconds(10));
    if (found != "found") return "S4 FAIL no running quest with a filled reference alias";

    auto* player = RE::PlayerCharacter::GetSingleton();
    const auto before = AliasCall(pick->handle, "GetReference", nullptr);
    const auto forced = AliasCall(pick->handle, "ForceRefTo", player);
    const auto after = AliasCall(pick->handle, "GetReference", nullptr);
    const auto restored = AliasCall(pick->handle, "ForceRefTo", RE::TESForm::LookupByID<RE::TESObjectREFR>(pick->original));
    const auto last = AliasCall(pick->handle, "GetReference", nullptr);
    const bool pass = before == std::format("{:08X}", pick->original) && after == "00000014" && last == before;
    return std::format("S4 {} quest={:08X} alias={} handle={:016X} before={} forced={} after={} restored={} last={}",
        pass ? "PASS" : "FAIL", pick->quest, pick->alias, pick->handle, before, forced, after, restored, last);
}
}
```
And in `Handle`: `if (line == "s4") return S4();`

- [ ] **Step 2: Build, deploy, relaunch, run** (with a throwaway save loaded: make one with console `save s4test` first)

Run: `python3 spikes/spike_client.py s4`
Expected PASS: `S4 PASS quest=Q alias=A handle=H before=R ... after=00000014 ... last=R`. Record `quest`, `alias` and `before` (the original reference): Task 17's live test uses them as `SPY_S4_QUEST`, `SPY_S4_ALIAS`, `SPY_S4_REF`. Also record the `handle` layout (high 32 bits) in `RESULTS.md`. If it FAILS, record the output; spec 4.4 then routes alias calls through a pre-authored Papyrus helper (M1.5), and Task 17's alias support becomes `unsupported`.

- [ ] **Step 3: Record and commit**

```bash
git add spikes && git commit -m "test(spike): S4 bind ReferenceAlias by handle, ForceRefTo and read back"
```

### Task 5: Spike S5 — mod events and a native Papyrus function reach C++

**Files:**
- Create: `spikes/spike-dll/papyrus/SkyrimPython.psc`
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Declare the native script**

`spikes/spike-dll/papyrus/SkyrimPython.psc`:
```papyrus
ScriptName SkyrimPython Hidden

; Delivers {channel, payload} to Python subscribers of `channel`.
Function Send(String channel, String payload) global native
```

- [ ] **Step 2: Compile it to `.pex`**

Follow the working Papyrus-compiler-under-Proton recipe in `/home/geoff/skse-http-probe/STATUS-2026-08-23.md` (lines 12–25), with `spikes/spike-dll/papyrus` as the input folder and `Scripts.zip`-free flags (the script imports nothing). Output: `SkyrimPython.pex`. Copy it to `"$SIMPLESKYRIM_DATA/Scripts/SkyrimPython.pex"`.

- [ ] **Step 3: Register the native function and the mod-event sink**

Add to `main.cpp`:
```cpp
namespace spike {
std::atomic_int g_sends{0}, g_modEvents{0};
std::string g_lastSend, g_lastMod;
std::mutex g_mx;

void NativeSend(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    std::lock_guard l(g_mx);
    g_lastSend = std::format("{}|{}", channel.c_str(), payload.c_str());
    g_sends.fetch_add(1);
}

bool RegisterPapyrus(RE::BSScript::IVirtualMachine* vm)
{
    vm->RegisterFunction("Send"sv, "SkyrimPython"sv, NativeSend);
    return true;
}

struct ModSink : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        if (e && e->eventName == "spyTest") {
            std::lock_guard l(g_mx);
            g_lastMod = std::format("{}|{}|{}", e->eventName.c_str(), e->strArg.c_str(), e->numArg);
            g_modEvents.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S5()
{
    auto r = OnMainThread([](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto handle = policy->GetHandleForObject(static_cast<RE::VMTypeID>(RE::FormType::ActorCharacter), player);
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> none;
        vm->DispatchMethodCall(handle, "Form"sv, "SendModEvent"sv,
            RE::MakeFunctionArguments(RE::BSFixedString("spyTest"), RE::BSFixedString("hello"), 42.0f), none);
        vm->DispatchStaticCall("SkyrimPython"sv, "Send"sv,
            RE::MakeFunctionArguments(RE::BSFixedString("chan"), RE::BSFixedString("{\"x\":1}")), none);
        finish("dispatched");
    }, std::chrono::seconds(10));
    std::this_thread::sleep_for(std::chrono::seconds(2));  // spike only: let both land
    std::lock_guard l(g_mx);
    return std::format("S5 {} dispatch={} mod_events={} last_mod={} sends={} last_send={}",
        (g_modEvents > 0 && g_sends > 0) ? "PASS" : "FAIL", r, g_modEvents.load(), g_lastMod, g_sends.load(), g_lastSend);
}
}
```
In `SKSEPluginLoad`, after `SKSE::Init`: `SKSE::GetPapyrusInterface()->Register(spike::RegisterPapyrus);`
In `OnMessage` `kDataLoaded`: `static spike::ModSink sink; SKSE::GetModCallbackEventSource()->AddEventSink(&sink);`
In `Handle`: `if (line == "s5") return S5();`

- [ ] **Step 4: Build, deploy, relaunch, run** (save loaded)

Run: `python3 spikes/spike_client.py s5`
Expected PASS: `mod_events=1 last_mod=spyTest|hello|42.000000 sends=1 last_send=chan|{"x":1}`.

- [ ] **Step 5: Record and commit**

```bash
git add spikes && git commit -m "test(spike): S5 ModCallbackEvent sink and native SkyrimPython.Send"
```

### Task 6: Spike S6 — load a save through `AddUITask` under Proton

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Add the S6 handler and lifecycle logging**

```cpp
namespace spike {
std::atomic_int g_postLoad{0}, g_loadingClosed{0};

struct MenuSink : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (e && e->menuName == RE::LoadingMenu::MENU_NAME) {
            Log(std::format("spike: Loading Menu {}", e->opening ? "opened" : "closed"));
            if (!e->opening) g_loadingClosed.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S6(const std::string& save)
{
    const int pl0 = g_postLoad, lc0 = g_loadingClosed;
    SKSE::GetTaskInterface()->AddUITask([save] {
        Log("spike: S6 calling BGSSaveLoadManager::Load(" + save + ")");
        RE::BGSSaveLoadManager::GetSingleton()->Load(save.c_str());
    });
    for (int i = 0; i < 120 && (g_postLoad == pl0 || g_loadingClosed == lc0); ++i)
        std::this_thread::sleep_for(std::chrono::seconds(1));
    return std::format("S6 {} postLoadGame_delta={} loadingClosed_delta={}",
        (g_postLoad > pl0 && g_loadingClosed > lc0) ? "PASS" : "FAIL", g_postLoad - pl0, g_loadingClosed - lc0);
}
}
```
In `OnMessage`: add `case SKSE::MessagingInterface::kPostLoadGame: spike::Log("kPostLoadGame"); spike::g_postLoad.fetch_add(1); break;` and `kPreLoadGame`/`kNewGame` log lines. In `kDataLoaded`: `static spike::MenuSink menuSink; RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(&menuSink);`
In `Handle`: `if (line.rfind("s6 ", 0) == 0) return S6(line.substr(3));`

- [ ] **Step 2: Build, deploy, relaunch; make a named save; run**

In game: open the console and type `save spytest`. Then:
Run: `python3 spikes/spike_client.py s6 spytest`
Expected PASS: `S6 PASS postLoadGame_delta=1 loadingClosed_delta=1`. The spike log shows the order of `kPostLoadGame` and `Loading Menu closed` — record which comes first (Task 15's interactive signal depends on it). Also try from the main menu (quit to main menu, run again).

- [ ] **Step 3: Record, gate, commit**

Fill the S6 row in `spikes/RESULTS.md`. Then review every row: for any FAIL, apply the "If it fails, change" column of spec section 9 to the spec before continuing.
```bash
git add spikes docs/specs && git commit -m "test(spike): S6 AddUITask load under Proton; record M0 results"
```

---

# Part B: M1 dispatcher

Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.

### Task 7: Protocol v1 document and golden fixtures

**Files:**
- Create: `protocol/PROTOCOL.md`
- Create: `protocol/v1/fixtures/hello.json`, `requests.json`, `values.json`, `form_keys.json`, `responses.json`

**Interfaces:**
- Produces: fixture files with the exact JSON shapes below. C++ tests (Task 12) and Python tests (Task 13) load them from `protocol/v1/fixtures/`.

- [ ] **Step 1: Write `protocol/PROTOCOL.md`**

````markdown
# skyrim-python protocol v1

Transport: TCP on 127.0.0.1. Each message is a frame: 4-byte little-endian unsigned length, then that many bytes of UTF-8 JSON (one object). Max 16 MiB. A frame header over the limit, or a payload that is not a JSON object, closes the connection.

Every message has `"type"`: `hello`, `welcome`, `handshake_error`, `request`, `response`, `event`.

## Handshake
Client first: `{"type":"hello","protocol":[1,0],"token":"<32 lowercase hex>","client":"<name>"}`
Server: `{"type":"welcome","protocol":[1,0],"plugin_version":"0.1.0","game_version":"1.6.1170.0","runtime":"AE","epoch":3,"capabilities":["papyrus.call_static", ...]}`
or `{"type":"handshake_error","code":"unauthorized"|"protocol_mismatch"|"bad_request","message":"..."}` and the server closes.

## Requests and responses
`{"type":"request","id":<uint64>,"op":"<op>","args":{...}}`
`{"type":"response","id":<same>,"ok":true,"result":<value or object>}`
`{"type":"response","id":<same>,"ok":false,"error":{"code":"<code>","message":"...","detail":{...}}}`
Responses may arrive in any order. Error codes: not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference, not_found, timeout, vm_error, unsupported, internal. `not_ready` detail: `{"reason":"starting"|"no_game"|"loading"|"paused"}`.

## Events (M2)
`{"type":"event","event":"<name>","sub":<id>,"seq":<n>,"epoch":<n>,"data":{...}}`

## Values
null, true/false, integers (Papyrus Int: -2147483648..2147483647), floats, strings,
form reference `{"kind":"form","form_id":20,"form_key":"Skyrim.esm|0x000014","type":"Actor","epoch":1}` (at least one of form_id/form_key; runtime-created references carry `"fingerprint":{"base":"<form key or decimal form id>","type":"<script type>"}` and no form_key),
alias reference `{"kind":"alias","quest":<form reference>,"alias_id":0,"alias_type":"ReferenceAlias"|"LocationAlias","epoch":1}`,
array `{"array":"Bool"|"Int"|"Float"|"String"|"Form"|"Alias","items":[...]}` (Float arrays accept integer items).
Bare JSON arrays are not values.

## Ops (M1)
- `game.state` `{}` → `{"data_ready":bool,"game_loaded":bool,"loading":bool,"interactive":bool,"paused":bool,"menus":[...],"epoch":n,"player":<form ref or null>,"game_days_passed":float or null}` (player and game_days_passed are null unless a game is loaded)
- `form.resolve` `{"form_key":...}` | `{"form_id":n}` | `{"editor_id":"..."}` → form reference
- `papyrus.call_static` `{"script","function","args":[values],"timeout_ms"?,"require_unpaused"?}` → value
- `papyrus.call_method` `{"object":<form or alias ref>,"script","function","args":[...],"timeout_ms"?,"require_unpaused"?}` → value
- `papyrus.get_property` `{"object","script","property"}` → value
- `papyrus.set_property` `{"object","script","property","value"}` → null
- `papyrus.batch` `{"calls":[{"op":"papyrus.call_static"|"papyrus.call_method"|"papyrus.get_property"|"papyrus.set_property","args":{...}}],"stop_on_error":bool,"timeout_ms"?}` → `{"results":[{"ok":true,"result":v}|{"ok":false,"error":{...}}]}`
````

- [ ] **Step 2: Write the fixtures**

`protocol/v1/fixtures/hello.json`:
```json
{
  "valid": [
    {"type": "hello", "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": "test"},
    {"type": "hello", "protocol": [1, 3], "token": "ffffffffffffffffffffffffffffffff", "client": "skyrim-dm"}
  ],
  "invalid": [
    {"message": {"type": "hello", "protocol": [1, 0], "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": "1.0", "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1, 0], "token": "XYZ", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "request", "id": 1, "op": "game.state", "args": {}}, "error": "bad_request"}
  ]
}
```

`protocol/v1/fixtures/requests.json`:
```json
{
  "valid": [
    {"type": "request", "id": 1, "op": "game.state", "args": {}},
    {"type": "request", "id": 2, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}},
    {"type": "request", "id": 3, "op": "papyrus.call_method", "args": {"object": {"kind": "form", "form_id": 20}, "script": "Actor", "function": "GetActorValue", "args": ["Health"], "timeout_ms": 2000, "require_unpaused": true}},
    {"type": "request", "id": 4, "op": "form.resolve", "args": {"form_key": "Skyrim.esm|0x012EB7"}},
    {"type": "request", "id": 5, "op": "papyrus.batch", "args": {"calls": [{"op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}}], "stop_on_error": true}}
  ],
  "invalid": [
    {"message": {"type": "request", "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": -1, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 6, "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 7, "op": "game.state", "args": []}, "error": "bad_request"},
    {"message": {"type": "request", "id": 8, "op": "papyrus.call_static", "args": {"script": "Utility", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 9, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 2147483648]}}, "error": "type_mismatch"},
    {"message": {"type": "request", "id": 10, "op": "papyrus.call_method", "args": {"script": "Actor", "function": "Kill", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 11, "op": "form.resolve", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 12, "op": "no.such.op", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 13, "op": "papyrus.batch", "args": {"calls": [{"op": "game.state", "args": {}}], "stop_on_error": true}}, "error": "bad_request"}
  ]
}
```

`protocol/v1/fixtures/values.json`:
```json
{
  "valid": [
    {"wire": null, "kind": "null"},
    {"wire": true, "kind": "bool"},
    {"wire": 0, "kind": "int"},
    {"wire": 2147483647, "kind": "int"},
    {"wire": -2147483648, "kind": "int"},
    {"wire": 1.5, "kind": "float"},
    {"wire": "Iron Sword", "kind": "string"},
    {"wire": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}, "kind": "form"},
    {"wire": {"kind": "form", "form_id": 4278190082, "type": "ObjectReference", "epoch": 2, "fingerprint": {"base": "Skyrim.esm|0x012EB7", "type": "ObjectReference"}}, "kind": "form"},
    {"wire": {"kind": "form", "form_key": "Skyrim.esm|0x01A69F"}, "kind": "form"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 123456, "form_key": "SkyrimPythonTest.esp|0x000800", "type": "Quest", "epoch": 1}, "alias_id": 0, "alias_type": "ReferenceAlias", "epoch": 1}, "kind": "alias"},
    {"wire": {"array": "Int", "items": [1, 2, 3]}, "kind": "array"},
    {"wire": {"array": "Float", "items": [1, 2.5]}, "kind": "array"},
    {"wire": {"array": "Form", "items": [{"kind": "form", "form_id": 20}]}, "kind": "array"}
  ],
  "invalid": [
    {"wire": 2147483648, "error": "type_mismatch"},
    {"wire": -2147483649, "error": "type_mismatch"},
    {"wire": {"kind": "form"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_key": "nonsense"}, "error": "bad_request"},
    {"wire": {"kind": "widget"}, "error": "bad_request"},
    {"wire": {"array": "Int", "items": [1, "x"]}, "error": "type_mismatch"},
    {"wire": {"array": "Bogus", "items": []}, "error": "bad_request"},
    {"wire": [1, 2], "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 0, "alias_type": "MagicAlias"}, "error": "bad_request"}
  ]
}
```

`protocol/v1/fixtures/form_keys.json`:
```json
{
  "valid": [
    {"text": "Skyrim.esm|0x01A69F", "plugin": "Skyrim.esm", "local_id": 108191, "canonical": "Skyrim.esm|0x01A69F"},
    {"text": "Skyrim.esm|0x14", "plugin": "Skyrim.esm", "local_id": 20, "canonical": "Skyrim.esm|0x000014"},
    {"text": "SkyrimPythonTest.esp|0x000800", "plugin": "SkyrimPythonTest.esp", "local_id": 2048, "canonical": "SkyrimPythonTest.esp|0x000800"},
    {"text": "Light Mod.esl|0xabc", "plugin": "Light Mod.esl", "local_id": 2748, "canonical": "Light Mod.esl|0x000ABC"}
  ],
  "invalid": ["Skyrim.esm", "Skyrim.esm|12", "notes.txt|0x1", "Skyrim.esm|0x1000000", "|0x1", "Skyrim.esm|0x", "Skyrim.esm|0xZZ"]
}
```

`protocol/v1/fixtures/responses.json`:
```json
{
  "valid": [
    {"type": "response", "id": 1, "ok": true, "result": 6},
    {"type": "response", "id": 2, "ok": false, "error": {"code": "not_ready", "message": "a game is loading", "detail": {"reason": "loading"}}},
    {"type": "response", "id": 3, "ok": true, "result": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}},
    {"type": "welcome", "protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]},
    {"type": "handshake_error", "code": "unauthorized", "message": "bad token"}
  ]
}
```

- [ ] **Step 3: Commit**

```bash
git add protocol && git commit -m "docs(protocol): protocol v1 description and golden fixtures"
```

### Task 8: Core library scaffold (Linux build and test runner)

**Files:**
- Create: `core/CMakeLists.txt`, `core/tests/main.cpp`

**Interfaces:**
- Produces: CMake target `spy_core` (static library, include dir `core/include` + `third_party`), test executable `spy_core_tests`, compile definition `SPY_FIXTURES_DIR` pointing at `protocol/v1/fixtures`.

- [ ] **Step 1: Write the CMake file and test main**

`core/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.21)
project(spy_core LANGUAGES CXX)

file(GLOB SPY_CORE_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
add_library(spy_core STATIC ${SPY_CORE_SOURCES})
target_include_directories(spy_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/../third_party)
target_compile_features(spy_core PUBLIC cxx_std_20)

option(SPY_CORE_TESTS "Build core unit tests" ON)
if(SPY_CORE_TESTS)
  enable_testing()
  file(GLOB SPY_CORE_TEST_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cpp)
  add_executable(spy_core_tests ${SPY_CORE_TEST_SOURCES})
  target_link_libraries(spy_core_tests PRIVATE spy_core)
  target_compile_definitions(spy_core_tests PRIVATE
    SPY_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../protocol/v1/fixtures")
  add_test(NAME spy_core_tests COMMAND spy_core_tests)
endif()
```

`core/tests/main.cpp`:
```cpp
#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include "doctest/doctest.h"
```

Create an empty placeholder source so the library has one translation unit until Task 9: `core/src/version.cpp` with
```cpp
namespace spy { const char* core_version() { return "0.1.0"; } }
```

- [ ] **Step 2: Build and run the (empty) test suite**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug && cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: build succeeds; `100% tests passed, 0 tests failed out of 1`.

- [ ] **Step 3: Commit**

```bash
git add core && git commit -m "build(core): pure C++20 core library scaffold with doctest"
```

### Task 9: Framing

**Files:**
- Create: `core/include/spy/framing.h`, `core/src/framing.cpp`, `core/tests/test_framing.cpp`

**Interfaces:**
- Produces: `spy::kMaxFrameBytes`, `std::string spy::encode_frame(std::string_view)`, `class spy::FrameDecoder { void feed(std::string_view); std::optional<std::string> next(); bool failed() const; }`

- [ ] **Step 1: Write the failing tests**

`core/tests/test_framing.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/framing.h"
#include <stdexcept>

TEST_CASE("encode_frame prefixes a little-endian length")
{
    auto f = spy::encode_frame("abc");
    REQUIRE(f.size() == 7);
    CHECK(f.substr(0, 4) == std::string("\x03\x00\x00\x00", 4));
    CHECK(f.substr(4) == "abc");
}

TEST_CASE("empty payload round trips")
{
    spy::FrameDecoder d;
    d.feed(spy::encode_frame(""));
    auto p = d.next();
    REQUIRE(p.has_value());
    CHECK(p->empty());
}

TEST_CASE("decoder reassembles split frames and yields several in order")
{
    spy::FrameDecoder d;
    auto all = spy::encode_frame("one") + spy::encode_frame("two");
    d.feed(all.substr(0, 5));
    CHECK_FALSE(d.next().has_value());
    d.feed(all.substr(5));
    CHECK(d.next().value() == "one");
    CHECK(d.next().value() == "two");
    CHECK_FALSE(d.next().has_value());
    CHECK_FALSE(d.failed());
}

TEST_CASE("decoder fails permanently on an oversized header")
{
    spy::FrameDecoder d;
    d.feed(std::string("\xFF\xFF\xFF\x7F", 4));
    CHECK_FALSE(d.next().has_value());
    CHECK(d.failed());
    d.feed(spy::encode_frame("later"));
    CHECK_FALSE(d.next().has_value());
}

TEST_CASE("a frame exactly at the limit is accepted, one byte over is rejected")
{
    std::string header(4, '\0');
    auto put = [&](std::uint32_t n) {
        for (int i = 0; i < 4; ++i) header[i] = static_cast<char>((n >> (8 * i)) & 0xFF);
    };
    spy::FrameDecoder ok;
    put(spy::kMaxFrameBytes);
    ok.feed(header);
    ok.next();
    CHECK_FALSE(ok.failed());
    spy::FrameDecoder bad;
    put(spy::kMaxFrameBytes + 1);
    bad.feed(header);
    bad.next();
    CHECK(bad.failed());
}

TEST_CASE("encode_frame refuses payloads over the limit")
{
    std::string big(spy::kMaxFrameBytes + 1, 'x');
    CHECK_THROWS_AS(spy::encode_frame(big), std::length_error);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL to compile, `spy/framing.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/framing.h`:
```cpp
#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace spy {
inline constexpr std::uint32_t kMaxFrameBytes = 16u * 1024u * 1024u;

// 4-byte little-endian length prefix, then the payload. Throws std::length_error over kMaxFrameBytes.
std::string encode_frame(std::string_view payload);

class FrameDecoder
{
public:
    void feed(std::string_view bytes);
    // The next complete payload, or nullopt if more bytes are needed or the decoder has failed.
    std::optional<std::string> next();
    // True once a header announced more than kMaxFrameBytes. The connection must be closed.
    bool failed() const { return failed_; }

private:
    std::string buf_;
    bool failed_ = false;
};
}
```

`core/src/framing.cpp`:
```cpp
#include "spy/framing.h"
#include <stdexcept>

namespace spy {
std::string encode_frame(std::string_view payload)
{
    if (payload.size() > kMaxFrameBytes) throw std::length_error("frame exceeds 16 MiB");
    const auto n = static_cast<std::uint32_t>(payload.size());
    std::string out;
    out.reserve(4 + payload.size());
    for (int i = 0; i < 4; ++i) out.push_back(static_cast<char>((n >> (8 * i)) & 0xFF));
    out.append(payload);
    return out;
}

void FrameDecoder::feed(std::string_view bytes)
{
    if (!failed_) buf_.append(bytes);
}

std::optional<std::string> FrameDecoder::next()
{
    if (failed_ || buf_.size() < 4) return std::nullopt;
    std::uint32_t n = 0;
    for (int i = 0; i < 4; ++i)
        n |= static_cast<std::uint32_t>(static_cast<unsigned char>(buf_[i])) << (8 * i);
    if (n > kMaxFrameBytes) {
        failed_ = true;
        buf_.clear();
        return std::nullopt;
    }
    if (buf_.size() < 4 + static_cast<std::size_t>(n)) return std::nullopt;
    std::string payload = buf_.substr(4, n);
    buf_.erase(0, 4 + static_cast<std::size_t>(n));
    return payload;
}
}
```
Remove the placeholder: `gio trash core/src/version.cpp`.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A core && git commit -m "feat(core): length-prefixed framing with 16 MiB limit"
```

### Task 10: Errors and the value codec

**Files:**
- Create: `core/include/spy/errors.h`, `core/src/errors.cpp`, `core/include/spy/values.h`, `core/src/values.cpp`, `core/tests/test_values.cpp`

**Interfaces:**
- Produces:
  - `enum class spy::ErrorCode { not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference, not_found, timeout, vm_error, unsupported, internal, unauthorized, protocol_mismatch }`
  - `std::string_view spy::to_string(ErrorCode)`; `std::optional<ErrorCode> spy::error_code_from_string(std::string_view)`
  - `struct spy::ProtocolError : std::runtime_error { ErrorCode code; ProtocolError(ErrorCode, const std::string&); }`
  - `using spy::json = nlohmann::json;`
  - `struct spy::Fingerprint { std::string base; std::string type; }`
  - `struct spy::FormRef { std::optional<std::uint32_t> form_id; std::optional<std::string> form_key; std::string type; std::optional<std::uint32_t> epoch; std::optional<Fingerprint> fingerprint; }`
  - `struct spy::AliasRef { FormRef quest; std::int32_t alias_id; std::string alias_type; std::optional<std::uint32_t> epoch; }`
  - `struct spy::ArrayValue { std::string elem_type; std::vector<Value> items; }`
  - `struct spy::Value { std::variant<std::monostate, bool, std::int32_t, float, std::string, FormRef, AliasRef, ArrayValue> v; }`
  - `Value spy::value_from_json(const json&)`, `json spy::value_to_json(const Value&)`, `FormRef spy::form_ref_from_json(const json&)`, `json spy::form_ref_to_json(const FormRef&)`, `AliasRef spy::alias_ref_from_json(const json&)`, `json spy::alias_ref_to_json(const AliasRef&)`, `std::string_view spy::value_kind(const Value&)` (returns `"null","bool","int","float","string","form","alias","array"`)
- Consumes: `spy::parse_form_key` from Task 11 is needed to validate `form_key`; to keep this task self-contained, `values.cpp` declares and uses `bool spy::is_valid_form_key(std::string_view)` which Task 11 implements in `refs.cpp`. Until Task 11 lands, add a temporary definition in `values.cpp` marked `// replaced in Task 11` that returns true, and delete it in Task 11 Step 3.

- [ ] **Step 1: Write the failing tests**

`core/tests/test_values.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/values.h"

using spy::json;

static spy::ErrorCode code_of(const json& j)
{
    try {
        spy::value_from_json(j);
    } catch (const spy::ProtocolError& e) {
        return e.code;
    }
    FAIL("expected ProtocolError");
    return spy::ErrorCode::internal;
}

TEST_CASE("scalars decode to the right kinds")
{
    CHECK(spy::value_kind(spy::value_from_json(nullptr)) == "null");
    CHECK(spy::value_kind(spy::value_from_json(true)) == "bool");
    CHECK(spy::value_kind(spy::value_from_json(7)) == "int");
    CHECK(spy::value_kind(spy::value_from_json(1.5)) == "float");
    CHECK(spy::value_kind(spy::value_from_json("x")) == "string");
}

TEST_CASE("Int range is enforced, never truncated")
{
    CHECK(std::get<std::int32_t>(spy::value_from_json(2147483647).v) == 2147483647);
    CHECK(std::get<std::int32_t>(spy::value_from_json(-2147483648LL).v) == -2147483647 - 1);
    CHECK(code_of(json(2147483648LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(-2147483649LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(4294967296ULL)) == spy::ErrorCode::type_mismatch);
}

TEST_CASE("form references need an id or a key")
{
    auto v = spy::value_from_json(json{{"kind", "form"}, {"form_id", 20}});
    CHECK(std::get<spy::FormRef>(v.v).form_id.value() == 20u);
    CHECK(code_of(json{{"kind", "form"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", "form"}, {"form_id", -1}}) == spy::ErrorCode::bad_request);
    CHECK(std::get<spy::FormRef>(spy::value_from_json(json::parse(R"({"kind":"form","form_id":20})")).v).form_id.value() == 20u);
}

TEST_CASE("arrays are typed and bare JSON arrays are rejected")
{
    auto v = spy::value_from_json(json{{"array", "Float"}, {"items", {1, 2.5}}});
    auto& a = std::get<spy::ArrayValue>(v.v);
    CHECK(a.elem_type == "Float");
    CHECK(std::get<float>(a.items[0].v) == doctest::Approx(1.0));
    CHECK(code_of(json::array({1, 2})) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"array", "Int"}, {"items", {1, "x"}}}) == spy::ErrorCode::type_mismatch);
}

TEST_CASE("alias types are restricted")
{
    json bad = {{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", 0}, {"alias_type", "MagicAlias"}};
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
}

TEST_CASE("error codes round trip through strings")
{
    for (auto c : {spy::ErrorCode::not_ready, spy::ErrorCode::stale_reference, spy::ErrorCode::protocol_mismatch})
        CHECK(spy::error_code_from_string(spy::to_string(c)).value() == c);
    CHECK_FALSE(spy::error_code_from_string("nope").has_value());
}
```
(The full fixture sweep over `values.json` is in Task 12.)

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/values.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/errors.h`:
```cpp
#pragma once
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace spy {
enum class ErrorCode {
    not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference,
    not_found, timeout, vm_error, unsupported, internal, unauthorized, protocol_mismatch
};

std::string_view to_string(ErrorCode code);
std::optional<ErrorCode> error_code_from_string(std::string_view s);

struct ProtocolError : std::runtime_error
{
    ErrorCode code;
    ProtocolError(ErrorCode c, const std::string& message) : std::runtime_error(message), code(c) {}
};
}
```

`core/src/errors.cpp`:
```cpp
#include "spy/errors.h"
#include <array>
#include <utility>

namespace spy {
namespace {
constexpr std::array<std::pair<ErrorCode, std::string_view>, 13> kNames{{
    {ErrorCode::not_ready, "not_ready"}, {ErrorCode::busy, "busy"},
    {ErrorCode::bad_request, "bad_request"}, {ErrorCode::unknown_function, "unknown_function"},
    {ErrorCode::type_mismatch, "type_mismatch"}, {ErrorCode::stale_reference, "stale_reference"},
    {ErrorCode::not_found, "not_found"}, {ErrorCode::timeout, "timeout"},
    {ErrorCode::vm_error, "vm_error"}, {ErrorCode::unsupported, "unsupported"},
    {ErrorCode::internal, "internal"}, {ErrorCode::unauthorized, "unauthorized"},
    {ErrorCode::protocol_mismatch, "protocol_mismatch"},
}};
}

std::string_view to_string(ErrorCode code)
{
    for (auto& [c, n] : kNames)
        if (c == code) return n;
    return "internal";
}

std::optional<ErrorCode> error_code_from_string(std::string_view s)
{
    for (auto& [c, n] : kNames)
        if (n == s) return c;
    return std::nullopt;
}
}
```

`core/include/spy/values.h`:
```cpp
#pragma once
#include "spy/errors.h"
#include <nlohmann/json.hpp>
#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace spy {
using json = nlohmann::json;

struct Fingerprint
{
    std::string base;  // base form's form key, or its decimal form id if it has no key
    std::string type;  // Papyrus script type of the reference
    bool operator==(const Fingerprint&) const = default;
};

struct FormRef
{
    std::optional<std::uint32_t> form_id;
    std::optional<std::string> form_key;
    std::string type;  // empty when unknown
    std::optional<std::uint32_t> epoch;
    std::optional<Fingerprint> fingerprint;
};

struct AliasRef
{
    FormRef quest;
    std::int32_t alias_id = 0;
    std::string alias_type;  // "ReferenceAlias" or "LocationAlias"
    std::optional<std::uint32_t> epoch;
};

struct Value;
struct ArrayValue
{
    std::string elem_type;  // Bool Int Float String Form Alias
    std::vector<Value> items;
};

struct Value
{
    std::variant<std::monostate, bool, std::int32_t, float, std::string, FormRef, AliasRef, ArrayValue> v;
};

bool is_valid_form_key(std::string_view s);  // implemented in refs.cpp (Task 11)
// True for a JSON integer in [0, max]. JSON built in C++ stores small non-negative ints as signed, so is_number_unsigned() is not enough.
bool is_uint(const json& j, std::uint64_t max);

Value value_from_json(const json& j);
json value_to_json(const Value& v);
FormRef form_ref_from_json(const json& j);
json form_ref_to_json(const FormRef& r);
AliasRef alias_ref_from_json(const json& j);
json alias_ref_to_json(const AliasRef& r);
std::string_view value_kind(const Value& v);
}
```

`core/src/values.cpp`:
```cpp
#include "spy/values.h"
#include <cmath>
#include <limits>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }
[[noreturn]] void mismatch(const std::string& m) { throw ProtocolError(ErrorCode::type_mismatch, m); }

std::int32_t to_int32(const json& j)
{
    if (j.is_number_unsigned()) {
        auto u = j.get<std::uint64_t>();
        if (u > static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max()))
            mismatch("integer " + j.dump() + " is out of Papyrus Int range");
        return static_cast<std::int32_t>(u);
    }
    auto s = j.get<std::int64_t>();
    if (s < std::numeric_limits<std::int32_t>::min() || s > std::numeric_limits<std::int32_t>::max())
        mismatch("integer " + j.dump() + " is out of Papyrus Int range");
    return static_cast<std::int32_t>(s);
}

bool is_elem_type(const std::string& t)
{
    return t == "Bool" || t == "Int" || t == "Float" || t == "String" || t == "Form" || t == "Alias";
}

Value elem_from_json(const std::string& elem, const json& j)
{
    if (j.is_object() || j.is_array()) {
        if (elem == "Form" && j.is_object() && j.value("kind", "") == "form") return Value{form_ref_from_json(j)};
        if (elem == "Alias" && j.is_object() && j.value("kind", "") == "alias") return Value{alias_ref_from_json(j)};
        mismatch("array of " + elem + " cannot hold " + j.dump());
    }
    if (elem == "Bool" && j.is_boolean()) return Value{j.get<bool>()};
    if (elem == "Int" && j.is_number_integer()) return Value{to_int32(j)};
    if (elem == "Float" && j.is_number()) return Value{j.get<float>()};
    if (elem == "String" && j.is_string()) return Value{j.get<std::string>()};
    if ((elem == "Form" || elem == "Alias") && j.is_null()) return Value{};
    mismatch("array of " + elem + " cannot hold " + j.dump());
}
}

bool is_uint(const json& j, std::uint64_t max)
{
    if (j.is_number_unsigned()) return j.get<std::uint64_t>() <= max;
    if (j.is_number_integer()) return j.get<std::int64_t>() >= 0 && static_cast<std::uint64_t>(j.get<std::int64_t>()) <= max;
    return false;
}

FormRef form_ref_from_json(const json& j)
{
    if (!j.is_object() || j.value("kind", "") != "form") bad("expected a form reference");
    FormRef r;
    if (j.contains("form_id")) {
        if (!is_uint(j["form_id"], 0xFFFFFFFFull)) bad("form_id must be an unsigned 32-bit integer");
        r.form_id = j["form_id"].get<std::uint32_t>();
    }
    if (j.contains("form_key") && !j["form_key"].is_null()) {
        if (!j["form_key"].is_string() || !is_valid_form_key(j["form_key"].get<std::string>()))
            bad("form_key must look like Plugin.esp|0x0012AB");
        r.form_key = j["form_key"].get<std::string>();
    }
    if (!r.form_id && !r.form_key) bad("a form reference needs form_id or form_key");
    if (j.contains("type") && j["type"].is_string()) r.type = j["type"].get<std::string>();
    if (j.contains("epoch") && is_uint(j["epoch"], 0xFFFFFFFFull)) r.epoch = j["epoch"].get<std::uint32_t>();
    if (j.contains("fingerprint") && j["fingerprint"].is_object()) {
        const auto& f = j["fingerprint"];
        if (!f.contains("base") || !f["base"].is_string() || !f.contains("type") || !f["type"].is_string())
            bad("fingerprint needs string base and type");
        r.fingerprint = Fingerprint{f["base"].get<std::string>(), f["type"].get<std::string>()};
    }
    return r;
}

json form_ref_to_json(const FormRef& r)
{
    json j = {{"kind", "form"}};
    if (r.form_id) j["form_id"] = *r.form_id;
    if (r.form_key) j["form_key"] = *r.form_key;
    if (!r.type.empty()) j["type"] = r.type;
    if (r.epoch) j["epoch"] = *r.epoch;
    if (r.fingerprint) j["fingerprint"] = {{"base", r.fingerprint->base}, {"type", r.fingerprint->type}};
    return j;
}

AliasRef alias_ref_from_json(const json& j)
{
    if (!j.is_object() || j.value("kind", "") != "alias") bad("expected an alias reference");
    AliasRef a;
    if (!j.contains("quest")) bad("alias reference needs quest");
    a.quest = form_ref_from_json(j["quest"]);
    if (!j.contains("alias_id") || !j["alias_id"].is_number_integer()) bad("alias_id must be an integer");
    a.alias_id = j["alias_id"].get<std::int32_t>();
    a.alias_type = j.value("alias_type", "ReferenceAlias");
    if (a.alias_type != "ReferenceAlias" && a.alias_type != "LocationAlias")
        bad("alias_type must be ReferenceAlias or LocationAlias");
    if (j.contains("epoch") && is_uint(j["epoch"], 0xFFFFFFFFull)) a.epoch = j["epoch"].get<std::uint32_t>();
    return a;
}

json alias_ref_to_json(const AliasRef& a)
{
    json j = {{"kind", "alias"}, {"quest", form_ref_to_json(a.quest)}, {"alias_id", a.alias_id}, {"alias_type", a.alias_type}};
    if (a.epoch) j["epoch"] = *a.epoch;
    return j;
}

Value value_from_json(const json& j)
{
    if (j.is_null()) return Value{};
    if (j.is_boolean()) return Value{j.get<bool>()};
    if (j.is_number_integer()) return Value{to_int32(j)};
    if (j.is_number_float()) {
        double d = j.get<double>();
        if (!std::isfinite(d)) mismatch("float must be finite");
        return Value{static_cast<float>(d)};
    }
    if (j.is_string()) return Value{j.get<std::string>()};
    if (j.is_array()) bad("bare JSON arrays are not values; use {\"array\": type, \"items\": [...]}");
    if (j.contains("array")) {
        if (!j["array"].is_string() || !is_elem_type(j["array"].get<std::string>())) bad("unknown array element type");
        if (!j.contains("items") || !j["items"].is_array()) bad("array needs items");
        ArrayValue a{j["array"].get<std::string>(), {}};
        for (const auto& item : j["items"]) a.items.push_back(elem_from_json(a.elem_type, item));
        return Value{std::move(a)};
    }
    const auto kind = j.value("kind", "");
    if (kind == "form") return Value{form_ref_from_json(j)};
    if (kind == "alias") return Value{alias_ref_from_json(j)};
    bad("unknown value object: " + j.dump());
}

json value_to_json(const Value& v)
{
    struct Visitor
    {
        json operator()(std::monostate) const { return nullptr; }
        json operator()(bool b) const { return b; }
        json operator()(std::int32_t i) const { return i; }
        json operator()(float f) const { return static_cast<double>(f); }
        json operator()(const std::string& s) const { return s; }
        json operator()(const FormRef& r) const { return form_ref_to_json(r); }
        json operator()(const AliasRef& a) const { return alias_ref_to_json(a); }
        json operator()(const ArrayValue& a) const
        {
            json items = json::array();
            for (const auto& item : a.items) items.push_back(value_to_json(item));
            return json{{"array", a.elem_type}, {"items", items}};
        }
    };
    return std::visit(Visitor{}, v.v);
}

std::string_view value_kind(const Value& v)
{
    static constexpr std::string_view names[] = {"null", "bool", "int", "float", "string", "form", "alias", "array"};
    return names[v.v.index()];
}

bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 11
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): error codes and Papyrus value codec with Int range checks"
```

### Task 11: Form keys and the fingerprint rule

**Files:**
- Create: `core/include/spy/refs.h`, `core/src/refs.cpp`, `core/tests/test_refs.cpp`
- Modify: `core/src/values.cpp` (delete the temporary `is_valid_form_key`)

**Interfaces:**
- Produces:
  - `struct spy::FormKey { std::string plugin; std::uint32_t local_id; }`
  - `std::optional<FormKey> spy::parse_form_key(std::string_view)` (plugin must end `.esm/.esp/.esl`, case-insensitive; id `0x` + 1–8 hex digits; local id ≤ `0xFFFFFF`)
  - `std::string spy::format_form_key(const FormKey&)` → `Plugin|0x` + 6 uppercase hex digits
  - `bool spy::is_valid_form_key(std::string_view)`
  - `bool spy::is_runtime_form_id(std::uint32_t)` (top byte `0xFF`), `bool spy::is_light_form_id(std::uint32_t)` (top byte `0xFE`)
  - `enum class spy::RefStatus { ok, stale }`; `RefStatus spy::check_fingerprint(const FormRef& claimed, const Fingerprint& live)`: `ok` unless the claimed `form_id` is runtime, the claim carries a fingerprint, and it differs from `live`.

- [ ] **Step 1: Write the failing tests**

`core/tests/test_refs.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/refs.h"

TEST_CASE("form keys parse and format canonically")
{
    auto k = spy::parse_form_key("Skyrim.esm|0x14");
    REQUIRE(k);
    CHECK(k->plugin == "Skyrim.esm");
    CHECK(k->local_id == 0x14u);
    CHECK(spy::format_form_key(*k) == "Skyrim.esm|0x000014");
    CHECK(spy::parse_form_key("Light Mod.esl|0xabc")->local_id == 0xABCu);
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1000000"));
    CHECK_FALSE(spy::parse_form_key("notes.txt|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm"));
    CHECK_FALSE(spy::parse_form_key("|0x1"));
}

TEST_CASE("form id ranges")
{
    CHECK(spy::is_runtime_form_id(0xFF000002u));
    CHECK_FALSE(spy::is_runtime_form_id(0x00000014u));
    CHECK(spy::is_light_form_id(0xFE001800u));
}

TEST_CASE("fingerprint rule only applies to runtime references that carry one")
{
    spy::Fingerprint live{"Skyrim.esm|0x012EB7", "ObjectReference"};
    spy::FormRef plugin{0x00012EB7u, "Skyrim.esm|0x012EB7", "Weapon", 1, std::nullopt};
    CHECK(spy::check_fingerprint(plugin, live) == spy::RefStatus::ok);

    spy::FormRef runtime{0xFF000002u, std::nullopt, "ObjectReference", 1, live};
    CHECK(spy::check_fingerprint(runtime, live) == spy::RefStatus::ok);

    spy::FormRef other = runtime;
    other.fingerprint = spy::Fingerprint{"Skyrim.esm|0x000F", "ObjectReference"};
    CHECK(spy::check_fingerprint(other, live) == spy::RefStatus::stale);

    spy::FormRef unchecked{0xFF000002u, std::nullopt, "", std::nullopt, std::nullopt};
    CHECK(spy::check_fingerprint(unchecked, live) == spy::RefStatus::ok);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/refs.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/refs.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace spy {
struct FormKey
{
    std::string plugin;
    std::uint32_t local_id = 0;
};

std::optional<FormKey> parse_form_key(std::string_view s);
std::string format_form_key(const FormKey& k);
bool is_runtime_form_id(std::uint32_t id);
bool is_light_form_id(std::uint32_t id);

enum class RefStatus { ok, stale };
RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live);
}
```

`core/src/refs.cpp`:
```cpp
#include "spy/refs.h"
#include <algorithm>
#include <cctype>
#include <cstdio>

namespace spy {
namespace {
bool ends_with_ci(std::string_view s, std::string_view suffix)
{
    if (s.size() < suffix.size()) return false;
    return std::equal(suffix.begin(), suffix.end(), s.end() - suffix.size(),
        [](char a, char b) { return std::tolower(static_cast<unsigned char>(a)) == std::tolower(static_cast<unsigned char>(b)); });
}
}

std::optional<FormKey> parse_form_key(std::string_view s)
{
    const auto bar = s.find('|');
    if (bar == std::string_view::npos || bar == 0) return std::nullopt;
    const auto plugin = s.substr(0, bar);
    if (!ends_with_ci(plugin, ".esm") && !ends_with_ci(plugin, ".esp") && !ends_with_ci(plugin, ".esl")) return std::nullopt;
    auto id = s.substr(bar + 1);
    if (id.size() < 3 || id.size() > 10 || id[0] != '0' || (id[1] != 'x' && id[1] != 'X')) return std::nullopt;
    id.remove_prefix(2);
    std::uint64_t v = 0;
    for (char c : id) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) return std::nullopt;
        v = v * 16 + static_cast<std::uint64_t>(std::isdigit(static_cast<unsigned char>(c)) ? c - '0' : std::tolower(static_cast<unsigned char>(c)) - 'a' + 10);
    }
    if (v > 0xFFFFFFull) return std::nullopt;
    return FormKey{std::string(plugin), static_cast<std::uint32_t>(v)};
}

std::string format_form_key(const FormKey& k)
{
    char buf[16];
    std::snprintf(buf, sizeof buf, "0x%06X", k.local_id);
    return k.plugin + "|" + buf;
}

bool is_valid_form_key(std::string_view s) { return parse_form_key(s).has_value(); }
bool is_runtime_form_id(std::uint32_t id) { return (id >> 24) == 0xFFu; }
bool is_light_form_id(std::uint32_t id) { return (id >> 24) == 0xFEu; }

RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live)
{
    if (!claimed.form_id || !is_runtime_form_id(*claimed.form_id) || !claimed.fingerprint) return RefStatus::ok;
    return *claimed.fingerprint == live ? RefStatus::ok : RefStatus::stale;
}
}
```
In `core/src/values.cpp`, delete the line `bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 11`.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): form keys and runtime-reference fingerprint rule"
```

### Task 12: Messages, op-argument validation, config, outbox, in-flight limiter, fixture sweep

**Files:**
- Create: `core/include/spy/messages.h`, `core/src/messages.cpp`, `core/include/spy/config.h`, `core/src/config.cpp`, `core/include/spy/outbox.h`, `core/src/outbox.cpp`
- Create: `core/tests/test_messages.cpp`, `core/tests/test_config.cpp`, `core/tests/test_outbox.cpp`, `core/tests/test_fixtures.cpp`

**Interfaces:**
- Produces:
  - `inline constexpr int spy::kProtocolMajor = 1, spy::kProtocolMinor = 0;`
  - `struct spy::Hello { int major; int minor; std::string token; std::string client; }`; `Hello spy::parse_hello(const json&)` (throws `bad_request`)
  - `std::optional<ErrorCode> spy::check_hello(const Hello&, std::string_view expected_token)` → `unauthorized` or `protocol_mismatch` or nullopt
  - `struct spy::Request { std::uint64_t id; std::string op; json args; }`; `Request spy::parse_request(const json&)` (envelope + known op + op args; throws `bad_request`/`type_mismatch`)
  - `struct spy::CallArgs { std::optional<Value> object; std::string script; std::string function; std::vector<Value> args; std::optional<std::uint32_t> timeout_ms; bool require_unpaused; }`; `CallArgs spy::parse_call_args(const json& args, bool needs_object)`
  - `struct spy::PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; }`; `PropertyArgs spy::parse_property_args(const json& args, bool needs_value)`
  - `struct spy::BatchStep { std::string op; json args; }`; `struct spy::BatchArgs { std::vector<BatchStep> calls; bool stop_on_error; std::optional<std::uint32_t> timeout_ms; }`; `BatchArgs spy::parse_batch_args(const json&)`
  - `struct spy::WelcomeInfo { std::string plugin_version, game_version, runtime; std::uint32_t epoch; std::vector<std::string> capabilities; }`
  - `json spy::make_welcome(const WelcomeInfo&)`, `json spy::make_handshake_error(ErrorCode, std::string_view)`, `json spy::make_ok(std::uint64_t id, json result)`, `json spy::make_error(std::uint64_t id, ErrorCode, std::string_view message, json detail = json::object())`, `json spy::make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version)`
  - `const std::vector<std::string>& spy::known_ops()`
  - `struct spy::Config { std::uint16_t port = 45977; std::string fixed_token; bool allow_external_clients = false; std::string session_file; std::uint32_t default_timeout_ms = 5000; std::uint32_t max_inflight_per_connection = 256; std::uint32_t max_inflight_total = 1024; bool enable_console = false; }`; `Config spy::parse_config(std::string_view ini_text)` (throws `std::invalid_argument` on a bad value)
  - `using spy::ConnId = std::uint64_t;` `class spy::Outbox { void open(ConnId); void close(ConnId); bool post(ConnId, std::string frame); std::vector<std::string> drain(ConnId); std::size_t open_count() const; }`
  - `class spy::InflightLimiter { InflightLimiter(std::uint32_t per_conn, std::uint32_t total); bool try_acquire(ConnId); void release(ConnId); void forget(ConnId); std::uint32_t total() const; }`

- [ ] **Step 1: Write the failing tests**

`core/tests/test_fixtures.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/messages.h"
#include "spy/refs.h"
#include "spy/values.h"
#include <fstream>

using spy::json;

static json load(const char* name)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/" + name);
    REQUIRE(f.good());
    return json::parse(f);
}

static std::string error_of(auto&& fn)
{
    try {
        fn();
    } catch (const spy::ProtocolError& e) {
        return std::string(spy::to_string(e.code));
    }
    return "none";
}

TEST_CASE("values fixtures")
{
    auto fx = load("values.json");
    for (const auto& c : fx["valid"]) {
        CAPTURE(c.dump());
        auto v = spy::value_from_json(c["wire"]);
        CHECK(spy::value_kind(v) == c["kind"].get<std::string>());
        CHECK(spy::value_to_json(v) == c["wire"]);
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::value_from_json(c["wire"]); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("form key fixtures")
{
    auto fx = load("form_keys.json");
    for (const auto& c : fx["valid"]) {
        auto k = spy::parse_form_key(c["text"].get<std::string>());
        REQUIRE(k);
        CHECK(k->plugin == c["plugin"].get<std::string>());
        CHECK(k->local_id == c["local_id"].get<std::uint32_t>());
        CHECK(spy::format_form_key(*k) == c["canonical"].get<std::string>());
    }
    for (const auto& t : fx["invalid"]) CHECK_FALSE(spy::parse_form_key(t.get<std::string>()));
}

TEST_CASE("hello fixtures")
{
    auto fx = load("hello.json");
    for (const auto& m : fx["valid"]) CHECK_NOTHROW(spy::parse_hello(m));
    for (const auto& c : fx["invalid"])
        CHECK(error_of([&] { spy::parse_hello(c["message"]); }) == c["error"].get<std::string>());
}

TEST_CASE("request fixtures")
{
    auto fx = load("requests.json");
    for (const auto& m : fx["valid"]) {
        CAPTURE(m.dump());
        CHECK_NOTHROW(spy::parse_request(m));
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::parse_request(c["message"]); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("responses built by the server match the fixture shapes")
{
    auto fx = load("responses.json");
    CHECK(spy::make_ok(1, 6) == fx["valid"][0]);
    CHECK(spy::make_error(2, spy::ErrorCode::not_ready, "a game is loading", {{"reason", "loading"}}) == fx["valid"][1]);
    CHECK(spy::make_welcome({"0.1.0", "1.6.1170.0", "AE", 1, {"game.state"}}) == fx["valid"][3]);
    CHECK(spy::make_handshake_error(spy::ErrorCode::unauthorized, "bad token") == fx["valid"][4]);
}
```

`core/tests/test_messages.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/messages.h"

using spy::json;

TEST_CASE("check_hello enforces token and protocol major")
{
    spy::Hello h{1, 0, "00112233445566778899aabbccddeeff", "t"};
    CHECK_FALSE(spy::check_hello(h, "00112233445566778899aabbccddeeff"));
    CHECK(spy::check_hello(h, "ffffffffffffffffffffffffffffffff") == spy::ErrorCode::unauthorized);
    h.major = 2;
    CHECK(spy::check_hello(h, "00112233445566778899aabbccddeeff") == spy::ErrorCode::protocol_mismatch);
}

TEST_CASE("call args parse values and optional fields")
{
    json a = {{"object", {{"kind", "form"}, {"form_id", 20}}}, {"script", "Actor"}, {"function", "GetActorValue"},
              {"args", {"Health"}}, {"timeout_ms", 2000}, {"require_unpaused", true}};
    auto c = spy::parse_call_args(a, true);
    CHECK(c.object.has_value());
    CHECK(c.args.size() == 1);
    CHECK(c.timeout_ms.value() == 2000u);
    CHECK(c.require_unpaused);
}

TEST_CASE("session file has port, token, pid, versions")
{
    auto s = spy::make_session_file(45977, "00112233445566778899aabbccddeeff", 1234, "0.1.0");
    CHECK(s["port"] == 45977);
    CHECK(s["protocol"] == json::array({1, 0}));
}
```

`core/tests/test_config.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/config.h"
#include <stdexcept>

TEST_CASE("defaults when empty")
{
    auto c = spy::parse_config("");
    CHECK(c.port == 45977);
    CHECK_FALSE(c.allow_external_clients);
    CHECK(c.default_timeout_ms == 5000u);
}

TEST_CASE("parses keys, ignores sections, comments and unknown keys")
{
    auto c = spy::parse_config(
        "; comment\n[Server]\nport = 45990\nallow_external_clients=true\nfixed_token = 00112233445566778899aabbccddeeff\n"
        "session_file = C:/tmp/s.json\nunknown_key = 5\n# another comment\nmax_inflight_total=10\n");
    CHECK(c.port == 45990);
    CHECK(c.allow_external_clients);
    CHECK(c.fixed_token == "00112233445566778899aabbccddeeff");
    CHECK(c.session_file == "C:/tmp/s.json");
    CHECK(c.max_inflight_total == 10u);
}

TEST_CASE("bad values throw")
{
    CHECK_THROWS_AS(spy::parse_config("port = hello\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("port = 70000\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("allow_external_clients = maybe\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("fixed_token = short\n"), std::invalid_argument);
}
```

`core/tests/test_outbox.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/outbox.h"

TEST_CASE("posts to a closed connection are dropped, not queued")
{
    spy::Outbox o;
    o.open(1);
    CHECK(o.post(1, "a"));
    o.close(1);
    CHECK_FALSE(o.post(1, "late result"));
    CHECK(o.drain(1).empty());
    CHECK(o.open_count() == 0);
}

TEST_CASE("drain returns messages in order and empties the queue")
{
    spy::Outbox o;
    o.open(7);
    o.post(7, "a");
    o.post(7, "b");
    auto d = o.drain(7);
    REQUIRE(d.size() == 2);
    CHECK(d[0] == "a");
    CHECK(o.drain(7).empty());
}

TEST_CASE("in-flight limiter enforces per-connection and total caps")
{
    spy::InflightLimiter l(2, 3);
    CHECK(l.try_acquire(1));
    CHECK(l.try_acquire(1));
    CHECK_FALSE(l.try_acquire(1));
    CHECK(l.try_acquire(2));
    CHECK_FALSE(l.try_acquire(3));
    l.release(1);
    CHECK(l.try_acquire(3));
    l.forget(2);
    CHECK(l.total() == 2u);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/messages.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/messages.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace spy {
inline constexpr int kProtocolMajor = 1;
inline constexpr int kProtocolMinor = 0;

struct Hello { int major = 0; int minor = 0; std::string token; std::string client; };
Hello parse_hello(const json& j);
std::optional<ErrorCode> check_hello(const Hello& h, std::string_view expected_token);

struct Request { std::uint64_t id = 0; std::string op; json args; };
Request parse_request(const json& j);
const std::vector<std::string>& known_ops();

struct CallArgs
{
    std::optional<Value> object;
    std::string script;
    std::string function;
    std::vector<Value> args;
    std::optional<std::uint32_t> timeout_ms;
    bool require_unpaused = false;
};
CallArgs parse_call_args(const json& args, bool needs_object);

struct PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; };
PropertyArgs parse_property_args(const json& args, bool needs_value);

struct BatchStep { std::string op; json args; };
struct BatchArgs { std::vector<BatchStep> calls; bool stop_on_error = true; std::optional<std::uint32_t> timeout_ms; };
BatchArgs parse_batch_args(const json& args);

struct WelcomeInfo
{
    std::string plugin_version, game_version, runtime;
    std::uint32_t epoch = 0;
    std::vector<std::string> capabilities;
};
json make_welcome(const WelcomeInfo& w);
json make_handshake_error(ErrorCode code, std::string_view message);
json make_ok(std::uint64_t id, json result);
json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object());
json make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version);
}
```

`core/src/messages.cpp`:
```cpp
#include "spy/messages.h"
#include "spy/refs.h"
#include <algorithm>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

std::string req_string(const json& o, const char* key)
{
    if (!o.contains(key) || !o[key].is_string() || o[key].get<std::string>().empty())
        bad(std::string("missing or empty string field '") + key + "'");
    return o[key].get<std::string>();
}

std::optional<std::uint32_t> opt_u32(const json& o, const char* key)
{
    if (!o.contains(key)) return std::nullopt;
    if (!is_uint(o[key], 0xFFFFFFFFull)) bad(std::string("field '") + key + "' must be an unsigned 32-bit integer");
    return o[key].get<std::uint32_t>();
}

Value parse_object_ref(const json& o)
{
    if (!o.contains("object")) bad("missing field 'object'");
    Value v = value_from_json(o["object"]);
    if (!std::holds_alternative<FormRef>(v.v) && !std::holds_alternative<AliasRef>(v.v))
        bad("'object' must be a form or alias reference");
    return v;
}

bool is_hex32(const std::string& t)
{
    return t.size() == 32 && std::all_of(t.begin(), t.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}

void validate_op_args(const std::string& op, const json& args)
{
    if (op == "papyrus.call_static") parse_call_args(args, false);
    else if (op == "papyrus.call_method") parse_call_args(args, true);
    else if (op == "papyrus.get_property") parse_property_args(args, false);
    else if (op == "papyrus.set_property") parse_property_args(args, true);
    else if (op == "papyrus.batch") parse_batch_args(args);
    else if (op == "form.resolve") {
        const int n = args.contains("form_key") + args.contains("form_id") + args.contains("editor_id");
        if (n != 1) bad("form.resolve needs exactly one of form_key, form_id, editor_id");
        if (args.contains("form_key") && (!args["form_key"].is_string() || !is_valid_form_key(args["form_key"].get<std::string>())))
            bad("form_key must look like Plugin.esp|0x0012AB");
        if (args.contains("form_id")) opt_u32(args, "form_id");
        if (args.contains("editor_id")) req_string(args, "editor_id");
    }
}
}

const std::vector<std::string>& known_ops()
{
    static const std::vector<std::string> ops = {
        "game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
        "papyrus.get_property", "papyrus.set_property", "papyrus.batch"};
    return ops;
}

Hello parse_hello(const json& j)
{
    if (!j.is_object() || j.value("type", "") != "hello") bad("first message must be a hello");
    if (!j.contains("protocol") || !j["protocol"].is_array() || j["protocol"].size() != 2 ||
        !j["protocol"][0].is_number_integer() || !j["protocol"][1].is_number_integer())
        bad("protocol must be [major, minor]");
    auto token = j.contains("token") && j["token"].is_string() ? j["token"].get<std::string>() : std::string();
    if (!is_hex32(token)) bad("token must be 32 lowercase hex characters");
    return Hello{j["protocol"][0].get<int>(), j["protocol"][1].get<int>(), token, j.value("client", "")};
}

std::optional<ErrorCode> check_hello(const Hello& h, std::string_view expected_token)
{
    if (h.major != kProtocolMajor) return ErrorCode::protocol_mismatch;
    if (h.token != expected_token) return ErrorCode::unauthorized;
    return std::nullopt;
}

Request parse_request(const json& j)
{
    if (!j.is_object() || j.value("type", "") != "request") bad("expected a request");
    if (!j.contains("id") || !is_uint(j["id"], UINT64_MAX)) bad("request id must be a non-negative integer");
    Request r{j["id"].get<std::uint64_t>(), req_string(j, "op"), j.value("args", json::object())};
    if (!r.args.is_object()) bad("args must be an object");
    const auto& ops = known_ops();
    if (std::find(ops.begin(), ops.end(), r.op) == ops.end()) bad("unknown op '" + r.op + "'");
    validate_op_args(r.op, r.args);
    return r;
}

CallArgs parse_call_args(const json& a, bool needs_object)
{
    CallArgs c;
    if (needs_object) c.object = parse_object_ref(a);
    c.script = req_string(a, "script");
    c.function = req_string(a, "function");
    if (a.contains("args")) {
        if (!a["args"].is_array()) bad("'args' must be a JSON array of values");
        for (const auto& v : a["args"]) c.args.push_back(value_from_json(v));
    }
    c.timeout_ms = opt_u32(a, "timeout_ms");
    if (a.contains("require_unpaused")) {
        if (!a["require_unpaused"].is_boolean()) bad("require_unpaused must be a boolean");
        c.require_unpaused = a["require_unpaused"].get<bool>();
    }
    return c;
}

PropertyArgs parse_property_args(const json& a, bool needs_value)
{
    PropertyArgs p{parse_object_ref(a), req_string(a, "script"), req_string(a, "property"), std::nullopt};
    if (needs_value) {
        if (!a.contains("value")) bad("missing field 'value'");
        p.value = value_from_json(a["value"]);
    }
    return p;
}

BatchArgs parse_batch_args(const json& a)
{
    if (!a.contains("calls") || !a["calls"].is_array() || a["calls"].empty()) bad("batch needs a non-empty 'calls' array");
    BatchArgs b;
    for (const auto& step : a["calls"]) {
        if (!step.is_object()) bad("each batch call must be an object");
        auto op = req_string(step, "op");
        if (op != "papyrus.call_static" && op != "papyrus.call_method" && op != "papyrus.get_property" && op != "papyrus.set_property")
            bad("batch calls may only be papyrus.* ops, got '" + op + "'");
        auto args = step.value("args", json::object());
        validate_op_args(op, args);
        b.calls.push_back({op, args});
    }
    if (a.contains("stop_on_error")) {
        if (!a["stop_on_error"].is_boolean()) bad("stop_on_error must be a boolean");
        b.stop_on_error = a["stop_on_error"].get<bool>();
    }
    b.timeout_ms = opt_u32(a, "timeout_ms");
    return b;
}

json make_welcome(const WelcomeInfo& w)
{
    return {{"type", "welcome"}, {"protocol", {kProtocolMajor, kProtocolMinor}}, {"plugin_version", w.plugin_version},
            {"game_version", w.game_version}, {"runtime", w.runtime}, {"epoch", w.epoch}, {"capabilities", w.capabilities}};
}

json make_handshake_error(ErrorCode code, std::string_view message)
{
    return {{"type", "handshake_error"}, {"code", to_string(code)}, {"message", message}};
}

json make_ok(std::uint64_t id, json result)
{
    return {{"type", "response"}, {"id", id}, {"ok", true}, {"result", std::move(result)}};
}

json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail)
{
    return {{"type", "response"}, {"id", id}, {"ok", false},
            {"error", {{"code", to_string(code)}, {"message", message}, {"detail", std::move(detail)}}}};
}

json make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version)
{
    return {{"port", port}, {"token", token}, {"pid", pid}, {"plugin_version", plugin_version},
            {"protocol", {kProtocolMajor, kProtocolMinor}}};
}
}
```

`core/include/spy/config.h`:
```cpp
#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace spy {
struct Config
{
    std::uint16_t port = 45977;
    std::string fixed_token;  // empty: random per start
    bool allow_external_clients = false;
    std::string session_file;  // empty: <SKSE log dir>/SkyrimPython/session.json
    std::uint32_t default_timeout_ms = 5000;
    std::uint32_t max_inflight_per_connection = 256;
    std::uint32_t max_inflight_total = 1024;
    bool enable_console = false;
};

// INI subset: "key = value" lines; [sections], ';' and '#' comments and unknown keys are ignored.
Config parse_config(std::string_view text);
}
```

`core/src/config.cpp`:
```cpp
#include "spy/config.h"
#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace spy {
namespace {
std::string trim(std::string_view s)
{
    auto b = s.find_first_not_of(" \t\r");
    if (b == std::string_view::npos) return {};
    auto e = s.find_last_not_of(" \t\r");
    return std::string(s.substr(b, e - b + 1));
}

std::uint64_t to_uint(const std::string& key, const std::string& v, std::uint64_t max)
{
    if (v.empty() || !std::all_of(v.begin(), v.end(), [](char c) { return std::isdigit(static_cast<unsigned char>(c)); }))
        throw std::invalid_argument(key + " must be a non-negative integer");
    auto n = std::stoull(v);
    if (n > max) throw std::invalid_argument(key + " is out of range");
    return n;
}

bool to_bool(const std::string& key, const std::string& v)
{
    if (v == "true" || v == "1") return true;
    if (v == "false" || v == "0") return false;
    throw std::invalid_argument(key + " must be true or false");
}
}

Config parse_config(std::string_view text)
{
    Config c;
    std::size_t start = 0;
    while (start <= text.size()) {
        auto end = text.find('\n', start);
        if (end == std::string_view::npos) end = text.size();
        auto line = trim(text.substr(start, end - start));
        start = end + 1;
        if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
        auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        auto key = trim(line.substr(0, eq));
        auto val = trim(line.substr(eq + 1));
        if (key == "port") {
            auto p = to_uint(key, val, 65535);
            c.port = static_cast<std::uint16_t>(p);
        } else if (key == "fixed_token") {
            if (!val.empty() && (val.size() != 32 || !std::all_of(val.begin(), val.end(), [](char ch) { return std::isxdigit(static_cast<unsigned char>(ch)) && !std::isupper(static_cast<unsigned char>(ch)); })))
                throw std::invalid_argument("fixed_token must be 32 lowercase hex characters");
            c.fixed_token = val;
        } else if (key == "allow_external_clients") c.allow_external_clients = to_bool(key, val);
        else if (key == "session_file") c.session_file = val;
        else if (key == "default_timeout_ms") c.default_timeout_ms = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "max_inflight_per_connection") c.max_inflight_per_connection = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "max_inflight_total") c.max_inflight_total = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "enable_console") c.enable_console = to_bool(key, val);
    }
    return c;
}
}
```
Note: `port = 70000` must throw — `to_uint(key, val, 65535)` does that.

`core/include/spy/outbox.h`:
```cpp
#pragma once
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace spy {
using ConnId = std::uint64_t;

// Thread-safe routing of outbound frames. Any thread may post; the IO thread drains.
// Posting to a connection that is not open drops the frame (a late reply after disconnect).
class Outbox
{
public:
    void open(ConnId id);
    void close(ConnId id);
    bool post(ConnId id, std::string frame);
    std::vector<std::string> drain(ConnId id);
    std::size_t open_count() const;

private:
    mutable std::mutex m_;
    std::unordered_map<ConnId, std::vector<std::string>> q_;
};

class InflightLimiter
{
public:
    InflightLimiter(std::uint32_t per_conn, std::uint32_t total) : per_conn_(per_conn), total_cap_(total) {}
    bool try_acquire(ConnId id);
    void release(ConnId id);
    void forget(ConnId id);  // connection closed: drop its count
    std::uint32_t total() const;

private:
    mutable std::mutex m_;
    std::uint32_t per_conn_, total_cap_, total_ = 0;
    std::unordered_map<ConnId, std::uint32_t> counts_;
};
}
```

`core/src/outbox.cpp`:
```cpp
#include "spy/outbox.h"

namespace spy {
void Outbox::open(ConnId id) { std::lock_guard l(m_); q_[id]; }
void Outbox::close(ConnId id) { std::lock_guard l(m_); q_.erase(id); }

bool Outbox::post(ConnId id, std::string frame)
{
    std::lock_guard l(m_);
    auto it = q_.find(id);
    if (it == q_.end()) return false;
    it->second.push_back(std::move(frame));
    return true;
}

std::vector<std::string> Outbox::drain(ConnId id)
{
    std::lock_guard l(m_);
    auto it = q_.find(id);
    if (it == q_.end()) return {};
    std::vector<std::string> out;
    out.swap(it->second);
    return out;
}

std::size_t Outbox::open_count() const { std::lock_guard l(m_); return q_.size(); }

bool InflightLimiter::try_acquire(ConnId id)
{
    std::lock_guard l(m_);
    auto& n = counts_[id];
    if (n >= per_conn_ || total_ >= total_cap_) return false;
    ++n;
    ++total_;
    return true;
}

void InflightLimiter::release(ConnId id)
{
    std::lock_guard l(m_);
    auto it = counts_.find(id);
    if (it == counts_.end() || it->second == 0) return;
    --it->second;
    --total_;
}

void InflightLimiter::forget(ConnId id)
{
    std::lock_guard l(m_);
    auto it = counts_.find(id);
    if (it == counts_.end()) return;
    total_ -= it->second;
    counts_.erase(it);
}

std::uint32_t InflightLimiter::total() const { std::lock_guard l(m_); return total_; }
}
```
Note: `InflightLimiter::release` after `forget` for the same connection is a no-op (the count entry is gone), so a late completion for a closed connection never double-decrements the total.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS (all fixture cases included). If a fixture case fails, fix the code, never the fixture, unless the fixture contradicts `protocol/PROTOCOL.md`.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): messages, op validation, config, outbox, in-flight limiter; fixture sweep"
```

### Task 13: Python package basics: framing, errors, values, form keys

**Files:**
- Create: `python/pyproject.toml`, `python/src/skyrim/__init__.py`, `python/src/skyrim/_framing.py`, `python/src/skyrim/errors.py`, `python/src/skyrim/values.py`, `python/src/skyrim/refs.py`
- Create: `python/tests/support.py`, `python/tests/test_framing.py`, `python/tests/test_values_refs.py`, `python/tests/test_fixtures.py`

**Interfaces:**
- Produces:
  - `skyrim._framing`: `MAX_FRAME: int`, `encode(obj: dict) -> bytes`, `read_frame(sock) -> dict` (raises `ConnectionLost` on EOF, `ProtocolViolation` on an oversized header or non-object JSON)
  - `skyrim.errors`: `SkyrimPythonError`; `ConnectionLost`, `NotRunning`, `CallTimeout`, `ProtocolViolation`; `HandshakeError(code, message)` with `Unauthorized`, `ProtocolMismatch`; `RemoteError(code, message, detail)` with one subclass per code: `NotReady`, `Busy`, `BadRequest`, `UnknownFunction`, `TypeMismatch`, `StaleReference`, `NotFound`, `Timeout`, `VmError`, `Unsupported`, `InternalError`; `remote_error_from_wire(err: dict) -> RemoteError`; `handshake_error_from_wire(msg: dict) -> HandshakeError`
  - `skyrim.values`: frozen dataclasses `Fingerprint(base, type)`, `Form(form_id=None, form_key=None, type=None, epoch=None, fingerprint=None)`, `Alias(quest: Form, alias_id: int, alias_type="ReferenceAlias", epoch=None)`, `PArray(elem: str, items: tuple)`; `INT_MIN`, `INT_MAX`; `to_wire(v) -> Any`; `from_wire(j) -> Any` (arrays decode to plain `list`)
  - `skyrim.refs`: `parse_form_key(text) -> tuple[str, int]` (raises `ValueError`), `format_form_key(plugin, local_id) -> str`, `is_runtime_form_id(int) -> bool`, `is_light_form_id(int) -> bool`
  - `python/tests/support.py` (plain module; pytest's default import mode puts `tests/` on `sys.path`): `FIXTURES: pathlib.Path`, `load_fixture(name) -> dict`

- [ ] **Step 1: Project file and test helpers**

`python/pyproject.toml`:
```toml
[project]
name = "skyrim-python"
version = "0.1.0"
description = "Call Papyrus in a running Skyrim from Python, over the SkyrimPython SKSE plugin"
requires-python = ">=3.11"
license = "MIT"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/skyrim"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`python/tests/support.py` (Task 14 appends the fake server to this file):

`python/tests/support.py`:
```python
import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())
```

- [ ] **Step 2: Write the failing tests**

`python/tests/test_framing.py`:
```python
import socket
import struct

import pytest

from skyrim import _framing
from skyrim.errors import ConnectionLost, ProtocolViolation


def pair():
    a, b = socket.socketpair()
    return a, b


def test_round_trip_split_writes():
    a, b = pair()
    data = _framing.encode({"type": "hello", "x": 1})
    assert data[:4] == struct.pack("<I", len(data) - 4)
    a.sendall(data[:3])
    a.sendall(data[3:])
    assert _framing.read_frame(b) == {"type": "hello", "x": 1}


def test_eof_is_connection_lost():
    a, b = pair()
    a.close()
    with pytest.raises(ConnectionLost):
        _framing.read_frame(b)


def test_oversized_header_is_protocol_violation():
    a, b = pair()
    a.sendall(struct.pack("<I", _framing.MAX_FRAME + 1))
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)


def test_non_object_payload_is_protocol_violation():
    a, b = pair()
    body = b"[1,2]"
    a.sendall(struct.pack("<I", len(body)) + body)
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)
```

`python/tests/test_values_refs.py`:
```python
import math

import pytest

from skyrim import errors
from skyrim.refs import format_form_key, is_light_form_id, is_runtime_form_id, parse_form_key
from skyrim.values import INT_MAX, INT_MIN, Alias, Form, PArray, from_wire, to_wire


def test_int_range_checked_not_truncated():
    assert to_wire(INT_MAX) == INT_MAX
    assert to_wire(INT_MIN) == INT_MIN
    with pytest.raises(errors.TypeMismatch):
        to_wire(INT_MAX + 1)
    with pytest.raises(errors.TypeMismatch):
        to_wire(INT_MIN - 1)


def test_bool_is_not_int():
    assert to_wire(True) is True
    assert to_wire([True, False]) == {"array": "Bool", "items": [True, False]}


def test_list_inference():
    assert to_wire([1, 2]) == {"array": "Int", "items": [1, 2]}
    assert to_wire([1, 2.5]) == {"array": "Float", "items": [1, 2.5]}
    assert to_wire(["a"]) == {"array": "String", "items": ["a"]}
    assert to_wire([Form(form_id=20), None]) == {"array": "Form", "items": [{"kind": "form", "form_id": 20}, None]}
    with pytest.raises(TypeError):
        to_wire([])
    with pytest.raises(TypeError):
        to_wire([1, "a"])
    assert to_wire(PArray("Int", ())) == {"array": "Int", "items": []}


def test_unrepresentable_values():
    with pytest.raises(TypeError):
        to_wire({"a": 1})
    with pytest.raises(errors.TypeMismatch):
        to_wire(math.inf)


def test_form_needs_id_or_key():
    with pytest.raises(ValueError):
        Form()
    with pytest.raises(ValueError):
        Form(form_key="nonsense")


def test_alias_round_trip():
    a = Alias(Form(form_id=0x800, type="Quest", epoch=1), 3, epoch=1)
    assert from_wire(to_wire(a)) == a


def test_form_keys():
    assert parse_form_key("Skyrim.esm|0x14") == ("Skyrim.esm", 0x14)
    assert format_form_key("Skyrim.esm", 0x14) == "Skyrim.esm|0x000014"
    with pytest.raises(ValueError):
        parse_form_key("Skyrim.esm|0x1000000")
    assert is_runtime_form_id(0xFF000002)
    assert not is_runtime_form_id(0x14)
    assert is_light_form_id(0xFE001800)
```

`python/tests/test_fixtures.py`:
```python
import pytest

from support import load_fixture
from skyrim import errors
from skyrim.refs import format_form_key, parse_form_key
from skyrim.values import from_wire, to_wire

VALUES = load_fixture("values.json")
KEYS = load_fixture("form_keys.json")
RESPONSES = load_fixture("responses.json")


@pytest.mark.parametrize("case", VALUES["valid"], ids=lambda c: str(c["wire"])[:40])
def test_valid_values_round_trip(case):
    assert to_wire(from_wire(case["wire"])) == case["wire"]


@pytest.mark.parametrize("case", [c for c in VALUES["invalid"] if isinstance(c["wire"], int)])
def test_int_overflow_rejected_client_side(case):
    with pytest.raises(errors.TypeMismatch):
        to_wire(case["wire"])


@pytest.mark.parametrize("case", KEYS["valid"], ids=lambda c: c["text"])
def test_valid_form_keys(case):
    plugin, local_id = parse_form_key(case["text"])
    assert (plugin, local_id) == (case["plugin"], case["local_id"])
    assert format_form_key(plugin, local_id) == case["canonical"]


@pytest.mark.parametrize("text", KEYS["invalid"])
def test_invalid_form_keys(text):
    with pytest.raises(ValueError):
        parse_form_key(text)


def test_error_responses_map_to_typed_exceptions():
    err = RESPONSES["valid"][1]["error"]
    exc = errors.remote_error_from_wire(err)
    assert isinstance(exc, errors.NotReady)
    assert exc.detail == {"reason": "loading"}
    hs = errors.handshake_error_from_wire(RESPONSES["valid"][4])
    assert isinstance(hs, errors.Unauthorized)
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run pytest -q`
Expected: FAIL — uv cannot build `skyrim-python` yet (`src/skyrim` does not exist) or pytest reports `ModuleNotFoundError: No module named 'skyrim'`.

- [ ] **Step 4: Implement**

`python/src/skyrim/errors.py`:
```python
"""Exceptions. Server error codes map one-to-one onto RemoteError subclasses."""


class SkyrimPythonError(Exception):
    """Base class for everything this package raises on purpose."""


class ConnectionLost(SkyrimPythonError):
    """The connection to the game closed (game quit, crashed, or the client closed it)."""


class NotRunning(SkyrimPythonError):
    """No reachable SkyrimPython server: no session file, connection refused, or no welcome in time."""


class CallTimeout(SkyrimPythonError):
    """The client stopped waiting for a response. The call may still run in the game."""


class ProtocolViolation(SkyrimPythonError):
    """The peer sent something that is not protocol v1."""


class HandshakeError(SkyrimPythonError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Unauthorized(HandshakeError):
    """Wrong token. Usually a stale session file from an earlier game run."""


class ProtocolMismatch(HandshakeError):
    """Client and plugin speak different protocol major versions."""


class RemoteError(SkyrimPythonError):
    code = "internal"

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}


class NotReady(RemoteError): code = "not_ready"
class Busy(RemoteError): code = "busy"
class BadRequest(RemoteError): code = "bad_request"
class UnknownFunction(RemoteError): code = "unknown_function"
class TypeMismatch(RemoteError): code = "type_mismatch"
class StaleReference(RemoteError): code = "stale_reference"
class NotFound(RemoteError): code = "not_found"
class Timeout(RemoteError): code = "timeout"
class VmError(RemoteError): code = "vm_error"
class Unsupported(RemoteError): code = "unsupported"
class InternalError(RemoteError): code = "internal"


_REMOTE = {c.code: c for c in (NotReady, Busy, BadRequest, UnknownFunction, TypeMismatch, StaleReference,
                               NotFound, Timeout, VmError, Unsupported, InternalError)}
_HANDSHAKE = {"unauthorized": Unauthorized, "protocol_mismatch": ProtocolMismatch}


def remote_error_from_wire(err: dict) -> RemoteError:
    code = err.get("code", "internal")
    return _REMOTE.get(code, RemoteError)(code, err.get("message", ""), err.get("detail"))


def handshake_error_from_wire(msg: dict) -> HandshakeError:
    code = msg.get("code", "bad_request")
    return _HANDSHAKE.get(code, HandshakeError)(code, msg.get("message", ""))


__all__ = ["SkyrimPythonError", "ConnectionLost", "NotRunning", "CallTimeout", "ProtocolViolation",
           "HandshakeError", "Unauthorized", "ProtocolMismatch", "RemoteError", *[c.__name__ for c in _REMOTE.values()]]
```

`python/src/skyrim/_framing.py`:
```python
"""4-byte little-endian length prefix + UTF-8 JSON object. Same rules as protocol/PROTOCOL.md."""
import json
import socket
import struct

from .errors import ConnectionLost, ProtocolViolation

MAX_FRAME = 16 * 1024 * 1024


def encode(obj: dict) -> bytes:
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError(f"message is {len(body)} bytes; the limit is {MAX_FRAME}")
    return struct.pack("<I", len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionLost("connection closed by peer")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket) -> dict:
    (n,) = struct.unpack("<I", _recv_exact(sock, 4))
    if n > MAX_FRAME:
        raise ProtocolViolation(f"frame of {n} bytes exceeds {MAX_FRAME}")
    try:
        obj = json.loads(_recv_exact(sock, n).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolViolation(f"frame is not UTF-8 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolViolation("frame is not a JSON object")
    return obj
```

`python/src/skyrim/refs.py`:
```python
"""Form keys ("Plugin.esp|0x0012AB") and form-id ranges."""
import re

_KEY = re.compile(r"^(?P<plugin>[^|]*\.(?:esm|esp|esl))\|0[xX](?P<id>[0-9A-Fa-f]{1,8})$", re.IGNORECASE)


def parse_form_key(text: str) -> tuple[str, int]:
    m = _KEY.match(text)
    if not m:
        raise ValueError(f"not a form key (expected Plugin.esp|0x0012AB): {text!r}")
    local_id = int(m.group("id"), 16)
    if local_id > 0xFFFFFF:
        raise ValueError(f"local form id {local_id:#x} is over 24 bits: {text!r}")
    return m.group("plugin"), local_id


def format_form_key(plugin: str, local_id: int) -> str:
    return f"{plugin}|0x{local_id:06X}"


def is_runtime_form_id(form_id: int) -> bool:
    return (form_id >> 24) == 0xFF


def is_light_form_id(form_id: int) -> bool:
    return (form_id >> 24) == 0xFE
```

`python/src/skyrim/values.py`:
```python
"""Papyrus values on the wire. See protocol/PROTOCOL.md "Values"."""
import math
from dataclasses import dataclass, field
from typing import Any

from . import errors
from .refs import parse_form_key

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1
ELEM_TYPES = ("Bool", "Int", "Float", "String", "Form", "Alias")


@dataclass(frozen=True)
class Fingerprint:
    base: str
    type: str


@dataclass(frozen=True)
class Form:
    """A form as the game names it. form_id is load-order dependent; form_key is not."""
    form_id: int | None = None
    form_key: str | None = None
    type: str | None = field(default=None, compare=False)
    epoch: int | None = field(default=None, compare=False)
    fingerprint: Fingerprint | None = None

    def __post_init__(self):
        if self.form_id is None and self.form_key is None:
            raise ValueError("a Form needs form_id or form_key")
        if self.form_key is not None:
            parse_form_key(self.form_key)


@dataclass(frozen=True)
class Alias:
    quest: Form
    alias_id: int
    alias_type: str = "ReferenceAlias"
    epoch: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class PArray:
    """An explicitly typed Papyrus array, for when a Python list is empty or ambiguous."""
    elem: str
    items: tuple = ()


def _int(v: int) -> int:
    if not INT_MIN <= v <= INT_MAX:
        raise errors.TypeMismatch("type_mismatch", f"{v} is out of Papyrus Int range")
    return v


def _form(f: Form) -> dict:
    j: dict[str, Any] = {"kind": "form"}
    if f.form_id is not None:
        j["form_id"] = f.form_id
    if f.form_key is not None:
        j["form_key"] = f.form_key
    if f.type:
        j["type"] = f.type
    if f.epoch is not None:
        j["epoch"] = f.epoch
    if f.fingerprint is not None:
        j["fingerprint"] = {"base": f.fingerprint.base, "type": f.fingerprint.type}
    return j


def _infer_elem(items: list) -> str:
    present = [i for i in items if i is not None]
    if not present:
        raise TypeError("cannot infer the element type of an empty or all-None list; use PArray")
    if all(isinstance(i, bool) for i in items):
        return "Bool"
    if all(isinstance(i, int) and not isinstance(i, bool) for i in items):
        return "Int"
    if all(isinstance(i, (int, float)) and not isinstance(i, bool) for i in items):
        return "Float"
    if all(isinstance(i, str) for i in items):
        return "String"
    if all(isinstance(i, Form) or i is None for i in items):
        return "Form"
    if all(isinstance(i, Alias) or i is None for i in items):
        return "Alias"
    raise TypeError(f"list mixes element types: {items!r}")


def to_wire(v: Any) -> Any:
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return _int(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise errors.TypeMismatch("type_mismatch", "Papyrus Float must be finite")
        return v
    if isinstance(v, Form):
        return _form(v)
    if isinstance(v, Alias):
        j = {"kind": "alias", "quest": _form(v.quest), "alias_id": v.alias_id, "alias_type": v.alias_type}
        if v.epoch is not None:
            j["epoch"] = v.epoch
        return j
    if isinstance(v, PArray):
        if v.elem not in ELEM_TYPES:
            raise TypeError(f"unknown array element type {v.elem!r}")
        return {"array": v.elem, "items": [to_wire(i) for i in v.items]}
    if isinstance(v, (list, tuple)):
        return {"array": _infer_elem(list(v)), "items": [to_wire(i) for i in v]}
    raise TypeError(f"cannot send {type(v).__name__} to Papyrus: {v!r}")


def _form_from(j: dict) -> Form:
    fp = j.get("fingerprint")
    return Form(form_id=j.get("form_id"), form_key=j.get("form_key"), type=j.get("type"), epoch=j.get("epoch"),
                fingerprint=Fingerprint(fp["base"], fp["type"]) if fp else None)


def from_wire(j: Any) -> Any:
    if not isinstance(j, dict):
        return j
    if "array" in j:
        items = [from_wire(i) for i in j.get("items", [])]
        if j["array"] == "Float":
            items = [float(i) for i in items]
        return items
    kind = j.get("kind")
    if kind == "form":
        return _form_from(j)
    if kind == "alias":
        return Alias(_form_from(j["quest"]), j["alias_id"], j.get("alias_type", "ReferenceAlias"), j.get("epoch"))
    return j
```

`python/src/skyrim/__init__.py` (Task 14 adds `connect`, `Game` and `find_session`):
```python
"""Call Papyrus in a running Skyrim from Python."""
from .errors import *  # noqa: F401,F403
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd python && uv run pytest -q`
Expected: all pass.

Note on the fixture round trip: `{"array":"Float","items":[1,2.5]}` decodes to `[1.0, 2.5]` and re-encodes as Float (all items are numbers, one is a float); `1.0 == 1` in the comparison. An all-integer Float array such as `[1.0, 2.0]` decodes to floats and so re-encodes as Float too.

- [ ] **Step 6: Commit**

```bash
git add python && git commit -m "feat(python): framing, typed errors, value codec, form keys"
```

### Task 14: Python discovery and the synchronous client

**Files:**
- Create: `python/src/skyrim/discovery.py`, `python/src/skyrim/client.py`
- Modify: `python/src/skyrim/__init__.py`, `python/tests/support.py`
- Create: `python/tests/conftest.py`, `python/tests/test_discovery.py`, `python/tests/test_client.py`

**Interfaces:**
- Consumes: everything from Task 13.
- Produces:
  - `skyrim.discovery`: `Session(host: str, port: int, token: str, pid: int | None = None, plugin_version: str | None = None)`; `find_session(session: str | os.PathLike | None = None, *, host="127.0.0.1", port=None, token=None, env=os.environ) -> Session`. Precedence: explicit `port`+`token` → explicit `session` path → `SKYRIM_PYTHON_SESSION` → `SKYRIM_PYTHON_PORT`+`SKYRIM_PYTHON_TOKEN`. Raises `NotRunning` naming what it tried.
  - `skyrim.client`: `connect(session=None, *, host="127.0.0.1", port=None, token=None, client_name="skyrim-python", connect_timeout=5.0, call_timeout=30.0, retry_not_ready: bool | float = False) -> Game`
  - `Game` methods: `call(script, function, *args, timeout=None, require_unpaused=False)`, `call_method(obj, script, function, *args, timeout=None, require_unpaused=False)`, `get_property(obj, script, prop)`, `set_property(obj, script, prop, value)`, `batch() -> Batch`, `resolve(*, form_key=None, form_id=None, editor_id=None) -> Form`, `form(form_key: str) -> Form`, `alias(quest: Form, alias_id: int, alias_type="ReferenceAlias") -> Alias`, `state() -> dict`, `wait_ready(timeout=120.0) -> dict`, `close()`; attributes `welcome: dict`, `epoch: int`, `closed: bool`; context manager.
  - `Batch` methods: `call(...)`, `call_method(...)`, `get_property(...)`, `set_property(...)` (each returns the batch), `run(stop_on_error=True, timeout=None) -> list` (each entry is a decoded value or a `RemoteError` instance; shorter than the step count if it stopped early).
  - `python/tests/support.py`: `TOKEN`, `ok(id, result)`, `err(id, code, message, detail)`, `FakeServer(handler=None, token=TOKEN, mode="normal")` with `.port`, `.requests`, `.close()`; handler signature `handler(request: dict, reply: Callable[[dict], None])`; modes `"normal"`, `"hang"` (accept, never answer the hello), `"mismatch"`. `python/tests/conftest.py`: fixture `server`.

- [ ] **Step 1: Add the fake server to `python/tests/support.py` and a fixture in `conftest.py`**

Append to `python/tests/support.py`:
```python
import socket
import threading

from skyrim import _framing

TOKEN = "00112233445566778899aabbccddeeff"


def ok(req_id, result):
    return {"type": "response", "id": req_id, "ok": True, "result": result}


def err(req_id, code, message="", detail=None):
    return {"type": "response", "id": req_id, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def echo_handler(req, reply):
    reply(ok(req["id"], {"op": req["op"], "args": req["args"]}))


class FakeServer:
    """A protocol-v1 server on a random loopback port. One thread per connection."""

    def __init__(self, handler=None, token=TOKEN, mode="normal"):
        self.handler = handler or echo_handler
        self.token = token
        self.mode = mode
        self.requests = []
        self.conns = []
        self._lsock = socket.create_server(("127.0.0.1", 0))
        self.port = self._lsock.getsockname()[1]
        self._stop = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                return
            self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        lock = threading.Lock()

        def reply(obj):
            with lock:
                try:
                    conn.sendall(_framing.encode(obj))
                except OSError:
                    pass

        try:
            hello = _framing.read_frame(conn)
            if self.mode == "hang":
                self._stop.wait()
                return
            if self.mode == "mismatch":
                reply({"type": "handshake_error", "code": "protocol_mismatch", "message": "server speaks 2.0"})
                return
            if hello.get("token") != self.token:
                reply({"type": "handshake_error", "code": "unauthorized", "message": "bad token"})
                return
            reply({"type": "welcome", "protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0",
                   "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]})
            while True:
                req = _framing.read_frame(conn)
                self.requests.append(req)
                self.handler(req, reply)
        except Exception:
            pass
        finally:
            conn.close()

    def drop_all(self):
        for c in self.conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        self._lsock.close()
        self.drop_all()
```

`python/tests/conftest.py`:
```python
import pytest

from support import FakeServer


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()
```

- [ ] **Step 2: Write the failing tests**

`python/tests/test_discovery.py`:
```python
import json

import pytest

from skyrim.discovery import find_session
from skyrim.errors import NotRunning

TOK = "00112233445566778899aabbccddeeff"


def write_session(path, port=45977, token=TOK):
    path.write_text(json.dumps({"port": port, "token": token, "pid": 42, "plugin_version": "0.1.0", "protocol": [1, 0]}))
    return path


def test_explicit_port_and_token_win(tmp_path):
    s = find_session(write_session(tmp_path / "s.json", port=1), port=2, token=TOK, env={})
    assert s.port == 2


def test_explicit_session_path(tmp_path):
    s = find_session(write_session(tmp_path / "s.json"), env={})
    assert (s.host, s.port, s.token, s.pid) == ("127.0.0.1", 45977, TOK, 42)


def test_env_session_then_env_port(tmp_path):
    p = write_session(tmp_path / "s.json", port=3)
    assert find_session(env={"SKYRIM_PYTHON_SESSION": str(p)}).port == 3
    assert find_session(env={"SKYRIM_PYTHON_PORT": "4", "SKYRIM_PYTHON_TOKEN": TOK}).port == 4


def test_missing_session_file_is_not_running(tmp_path):
    with pytest.raises(NotRunning, match="no session file"):
        find_session(tmp_path / "absent.json", env={})


def test_nothing_configured_is_not_running():
    with pytest.raises(NotRunning, match="SKYRIM_PYTHON_SESSION"):
        find_session(env={})


def test_corrupt_session_file_is_not_running(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json")
    with pytest.raises(NotRunning, match="unreadable"):
        find_session(p, env={})
```

`python/tests/test_client.py`:
```python
import socket
import threading
import time

import pytest

from support import TOKEN, FakeServer, err, ok
import skyrim
from skyrim import errors
from skyrim.values import Form


def connect(server, **kw):
    return skyrim.connect(port=server.port, token=TOKEN, **kw)


def test_handshake_and_call(server):
    with connect(server) as game:
        assert game.epoch == 1
        r = game.call("Utility", "RandomInt", 1, 6)
    assert r == {"op": "papyrus.call_static", "args": {
        "script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": False}}
    assert server.requests[0]["type"] == "request"


def test_call_method_sends_reference_and_timeout(server):
    with connect(server) as game:
        game.call_method(Form(form_id=20), "Actor", "GetActorValue", "Health", timeout=2.0)
    args = server.requests[0]["args"]
    assert args["object"] == {"kind": "form", "form_id": 20}
    assert args["timeout_ms"] == 2000


def test_int_overflow_raises_before_sending(server):
    with connect(server) as game:
        with pytest.raises(errors.TypeMismatch):
            game.call("Utility", "RandomInt", 1, 2**31)
    assert server.requests == []


def test_wrong_token_is_unauthorized(server):
    with pytest.raises(errors.Unauthorized):
        skyrim.connect(port=server.port, token="ffffffffffffffffffffffffffffffff")


def test_protocol_mismatch():
    s = FakeServer(mode="mismatch")
    try:
        with pytest.raises(errors.ProtocolMismatch):
            skyrim.connect(port=s.port, token=TOKEN)
    finally:
        s.close()


def test_hung_server_fails_within_connect_timeout():
    s = FakeServer(mode="hang")
    try:
        t0 = time.monotonic()
        with pytest.raises(errors.NotRunning, match="no welcome"):
            skyrim.connect(port=s.port, token=TOKEN, connect_timeout=0.5)
        assert time.monotonic() - t0 < 3
    finally:
        s.close()


def test_nothing_listening_is_not_running():
    with socket.create_server(("127.0.0.1", 0)) as tmp:
        port = tmp.getsockname()[1]
    with pytest.raises(errors.NotRunning):
        skyrim.connect(port=port, token=TOKEN, connect_timeout=0.5)


def test_remote_errors_are_typed():
    s = FakeServer(handler=lambda req, reply: reply(err(req["id"], "not_ready", "loading", {"reason": "loading"})))
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            with pytest.raises(errors.NotReady) as ei:
                game.call("Game", "GetPlayer")
            assert ei.value.detail["reason"] == "loading"
    finally:
        s.close()


def test_retry_not_ready_until_ok():
    count = {"n": 0}

    def handler(req, reply):
        count["n"] += 1
        reply(err(req["id"], "not_ready", "loading") if count["n"] < 3 else ok(req["id"], 7))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN, retry_not_ready=5.0) as game:
            assert game.call("Utility", "RandomInt", 1, 6) == 7
        assert count["n"] == 3
    finally:
        s.close()


def test_out_of_order_responses_reach_the_right_callers():
    held = []

    def handler(req, reply):
        held.append((req, reply))
        if len(held) == 2:
            for r, rep in reversed(held):
                rep(ok(r["id"], r["args"]["args"][0]))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            results = {}
            threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, game.call("X", "Y", i)))
                       for i in (1, 2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
            assert results == {1: 1, 2: 2}
    finally:
        s.close()


def test_connection_drop_fails_inflight_and_later_calls():
    s = FakeServer(handler=lambda req, reply: None)  # never answers
    try:
        game = skyrim.connect(port=s.port, token=TOKEN)
        caught = []

        def worker():
            try:
                game.call("Utility", "Wait", 10.0)
            except errors.ConnectionLost as e:
                caught.append(e)

        t = threading.Thread(target=worker)
        t.start()
        while not s.requests:
            time.sleep(0.01)
        s.drop_all()
        t.join(5)
        assert caught, "in-flight call did not fail with ConnectionLost"
        with pytest.raises(errors.ConnectionLost):
            game.call("Utility", "RandomInt", 1, 2)
        assert game.closed
    finally:
        s.close()


def test_client_timeout_then_late_reply_is_ignored():
    first = {"reply": None}

    def handler(req, reply):
        if first["reply"] is None:
            first["reply"] = (req, reply)  # hold the first request
        else:
            held_req, held_reply = first["reply"]
            held_reply(ok(held_req["id"], "late"))
            reply(ok(req["id"], "second"))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN, call_timeout=0.3) as game:
            with pytest.raises(errors.CallTimeout):
                game.call("Utility", "Wait", 10.0)
            assert game.call("Utility", "RandomInt", 1, 2) == "second"
    finally:
        s.close()


def test_batch_results_mix_values_and_errors():
    def handler(req, reply):
        reply(ok(req["id"], {"results": [{"ok": True, "result": 3},
                                         {"ok": False, "error": {"code": "unknown_function", "message": "no"}}]}))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            b = game.batch().call("Utility", "RandomInt", 1, 6).call("Nope", "Nope")
            results = b.run(stop_on_error=False)
        assert results[0] == 3
        assert isinstance(results[1], errors.UnknownFunction)
        sent = s.requests[0]["args"]
        assert sent["stop_on_error"] is False
        assert [c["op"] for c in sent["calls"]] == ["papyrus.call_static", "papyrus.call_static"]
    finally:
        s.close()


def test_form_decodes_result():
    s = FakeServer(handler=lambda req, reply: reply(ok(req["id"], {
        "kind": "form", "form_id": 0x12EB7, "form_key": "Skyrim.esm|0x012EB7", "type": "Weapon", "epoch": 1})))
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            f = game.form("Skyrim.esm|0x012EB7")
        assert f.form_id == 0x12EB7 and f.type == "Weapon"
        assert s.requests[0]["args"] == {"form_key": "Skyrim.esm|0x012EB7"}
    finally:
        s.close()


def test_wait_ready_polls_state():
    states = iter([False, False, True])

    def handler(req, reply):
        reply(ok(req["id"], {"interactive": next(states), "player": None, "epoch": 1}))

    s = FakeServer(handler=handler)
    try:
        with skyrim.connect(port=s.port, token=TOKEN) as game:
            st = game.wait_ready(timeout=5)
        assert st["interactive"] is True
    finally:
        s.close()
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run pytest -q tests/test_discovery.py tests/test_client.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'skyrim.discovery'` / `AttributeError: module 'skyrim' has no attribute 'connect'`.

- [ ] **Step 4: Implement**

`python/src/skyrim/discovery.py`:
```python
"""Find the running plugin: explicit port+token, a session file, or environment variables."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import NotRunning


@dataclass(frozen=True)
class Session:
    host: str
    port: int
    token: str
    pid: int | None = None
    plugin_version: str | None = None


def _read(path: Path, host: str) -> Session:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRunning(f"no session file at {path}; is the game running with SkyrimPython.dll?") from None
    except (OSError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: {e}") from None
    try:
        return Session(host, int(data["port"]), str(data["token"]), data.get("pid"), data.get("plugin_version"))
    except (KeyError, TypeError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: missing {e}") from None


def find_session(session=None, *, host: str = "127.0.0.1", port: int | None = None, token: str | None = None,
                 env=os.environ) -> Session:
    if port is not None and token is not None:
        return Session(host, int(port), token)
    if session is not None:
        return _read(Path(session), host)
    if env.get("SKYRIM_PYTHON_SESSION"):
        return _read(Path(env["SKYRIM_PYTHON_SESSION"]), host)
    if env.get("SKYRIM_PYTHON_PORT") and env.get("SKYRIM_PYTHON_TOKEN"):
        return Session(host, int(env["SKYRIM_PYTHON_PORT"]), env["SKYRIM_PYTHON_TOKEN"])
    raise NotRunning("no session given: pass session=/port=+token=, or set SKYRIM_PYTHON_SESSION "
                     "(or SKYRIM_PYTHON_PORT and SKYRIM_PYTHON_TOKEN)")
```

`python/src/skyrim/client.py`:
```python
"""Synchronous client. One reader thread per connection; any thread may call."""
import itertools
import socket
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from . import _framing, errors
from .discovery import find_session
from .values import Alias, Form, from_wire, to_wire

PROTOCOL = [1, 0]
_GRACE = 2.0  # seconds the client waits beyond a server-side timeout


class Game:
    def __init__(self, sock: socket.socket, welcome: dict, *, call_timeout: float, retry_not_ready: float):
        self.welcome = welcome
        self.epoch: int = welcome.get("epoch", 0)
        self.closed = False
        self._sock = sock
        self._call_timeout = call_timeout
        self._retry_not_ready = retry_not_ready
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_loop, name="skyrim-python-reader", daemon=True)
        self._reader.start()

    # -- plumbing -------------------------------------------------------------------------------
    def _read_loop(self):
        try:
            while True:
                msg = _framing.read_frame(self._sock)
                if msg.get("type") == "response":
                    with self._lock:
                        fut = self._pending.pop(msg.get("id"), None)
                    if fut is not None:
                        fut.set_result(msg)
                # "event" messages arrive in M2; M1 has no subscriptions, so none are sent.
        except Exception as e:  # noqa: BLE001 - any read failure ends the connection
            self._fail_all(errors.ConnectionLost(str(e) or type(e).__name__))

    def _fail_all(self, exc: Exception):
        with self._lock:
            self.closed = True
            pending, self._pending = self._pending, {}
        for fut in pending.values():
            fut.set_exception(exc)

    def _send_once(self, op: str, args: dict, wait: float) -> Any:
        fut: Future = Future()
        with self._lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            req_id = next(self._ids)
            self._pending[req_id] = fut
        try:
            with self._send_lock:
                self._sock.sendall(_framing.encode({"type": "request", "id": req_id, "op": op, "args": args}))
        except OSError as e:
            self._fail_all(errors.ConnectionLost(str(e)))
            raise errors.ConnectionLost(str(e)) from e
        try:
            resp = fut.result(timeout=wait)
        except FutureTimeout:
            with self._lock:
                self._pending.pop(req_id, None)
            raise errors.CallTimeout(f"{op}: no response after {wait:.1f}s (the call may still run in the game)") from None
        if resp.get("ok"):
            return resp.get("result")
        raise errors.remote_error_from_wire(resp.get("error", {}))

    def _request(self, op: str, args: dict, timeout: float | None = None) -> Any:
        wait = self._call_timeout if timeout is None else timeout + _GRACE
        deadline = time.monotonic() + self._retry_not_ready
        while True:
            try:
                return self._send_once(op, args, wait)
            except errors.NotReady:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)

    @staticmethod
    def _call_args(script, function, args, timeout, require_unpaused, obj=None) -> dict:
        a: dict[str, Any] = {}
        if obj is not None:
            a["object"] = to_wire(obj)
        a.update({"script": script, "function": function, "args": [to_wire(x) for x in args],
                  "require_unpaused": require_unpaused})
        if timeout is not None:
            a["timeout_ms"] = int(timeout * 1000)
        return a

    # -- public API -----------------------------------------------------------------------------
    def call(self, script: str, function: str, *args, timeout: float | None = None, require_unpaused: bool = False):
        """Call a global Papyrus function, e.g. game.call("Utility", "RandomInt", 1, 6)."""
        a = self._call_args(script, function, args, timeout, require_unpaused)
        return from_wire(self._request("papyrus.call_static", a, timeout))

    def call_method(self, obj: Form | Alias, script: str, function: str, *args, timeout: float | None = None,
                    require_unpaused: bool = False):
        """Call a member function on a form or alias, e.g. game.call_method(player, "Actor", "GetActorValue", "Health")."""
        a = self._call_args(script, function, args, timeout, require_unpaused, obj=obj)
        return from_wire(self._request("papyrus.call_method", a, timeout))

    def get_property(self, obj: Form | Alias, script: str, prop: str):
        return from_wire(self._request("papyrus.get_property", {"object": to_wire(obj), "script": script, "property": prop}))

    def set_property(self, obj: Form | Alias, script: str, prop: str, value) -> None:
        self._request("papyrus.set_property",
                      {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)})

    def batch(self) -> "Batch":
        return Batch(self)

    def resolve(self, *, form_key: str | None = None, form_id: int | None = None, editor_id: str | None = None) -> Form:
        given = {k: v for k, v in (("form_key", form_key), ("form_id", form_id), ("editor_id", editor_id)) if v is not None}
        if len(given) != 1:
            raise TypeError("resolve() takes exactly one of form_key, form_id, editor_id")
        return from_wire(self._request("form.resolve", given))

    def form(self, form_key: str) -> Form:
        """game.form("Skyrim.esm|0x012EB7") -> Form with the live form_id and type."""
        return self.resolve(form_key=form_key)

    def alias(self, quest: Form, alias_id: int, alias_type: str = "ReferenceAlias") -> Alias:
        return Alias(quest, alias_id, alias_type, quest.epoch)

    def state(self) -> dict:
        st = self._request("game.state", {})
        if st.get("player") is not None:
            st["player"] = from_wire(st["player"])
        return st

    def wait_ready(self, timeout: float = 120.0) -> dict:
        """Block until a save is loaded and no loading screen is up."""
        deadline = time.monotonic() + timeout
        while True:
            st = self.state()
            if st.get("interactive"):
                return st
            if time.monotonic() >= deadline:
                raise errors.CallTimeout(f"game not interactive after {timeout:.0f}s (last state: {st})")
            time.sleep(0.25)

    def close(self) -> None:
        with self._lock:
            self.closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._reader.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Batch:
    """Several papyrus.* steps in one round trip, run in order. Not a transaction (spec 4.3)."""

    def __init__(self, game: Game):
        self._game = game
        self._calls: list[dict] = []

    def call(self, script, function, *args, require_unpaused=False):
        self._calls.append({"op": "papyrus.call_static",
                            "args": Game._call_args(script, function, args, None, require_unpaused)})
        return self

    def call_method(self, obj, script, function, *args, require_unpaused=False):
        self._calls.append({"op": "papyrus.call_method",
                            "args": Game._call_args(script, function, args, None, require_unpaused, obj=obj)})
        return self

    def get_property(self, obj, script, prop):
        self._calls.append({"op": "papyrus.get_property",
                            "args": {"object": to_wire(obj), "script": script, "property": prop}})
        return self

    def set_property(self, obj, script, prop, value):
        self._calls.append({"op": "papyrus.set_property",
                            "args": {"object": to_wire(obj), "script": script, "property": prop, "value": to_wire(value)}})
        return self

    def run(self, stop_on_error: bool = True, timeout: float | None = None) -> list:
        if not self._calls:
            return []
        args: dict[str, Any] = {"calls": self._calls, "stop_on_error": stop_on_error}
        if timeout is not None:
            args["timeout_ms"] = int(timeout * 1000)
        out = self._game._request("papyrus.batch", args, timeout)
        return [from_wire(r.get("result")) if r.get("ok") else errors.remote_error_from_wire(r.get("error", {}))
                for r in out["results"]]


def connect(session=None, *, host: str = "127.0.0.1", port: int | None = None, token: str | None = None,
            client_name: str = "skyrim-python", connect_timeout: float = 5.0, call_timeout: float = 30.0,
            retry_not_ready: bool | float = False) -> Game:
    """Connect and handshake. Raises NotRunning, Unauthorized or ProtocolMismatch."""
    s = find_session(session, host=host, port=port, token=token)
    try:
        sock = socket.create_connection((s.host, s.port), timeout=connect_timeout)
    except OSError as e:
        raise errors.NotRunning(f"cannot connect to {s.host}:{s.port}: {e}") from None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(_framing.encode({"type": "hello", "protocol": PROTOCOL, "token": s.token, "client": client_name}))
        msg = _framing.read_frame(sock)
    except (socket.timeout, TimeoutError):
        sock.close()
        raise errors.NotRunning(f"{s.host}:{s.port} accepted but sent no welcome within {connect_timeout}s") from None
    except (OSError, errors.ConnectionLost) as e:
        sock.close()
        raise errors.NotRunning(f"{s.host}:{s.port} closed during the handshake: {e}") from None
    if msg.get("type") == "handshake_error":
        sock.close()
        raise errors.handshake_error_from_wire(msg)
    if msg.get("type") != "welcome":
        sock.close()
        raise errors.ProtocolViolation(f"expected welcome, got {msg.get('type')!r}")
    sock.settimeout(None)
    retry = 30.0 if retry_not_ready is True else float(retry_not_ready or 0)
    return Game(sock, msg, call_timeout=call_timeout, retry_not_ready=retry)
```

Replace `python/src/skyrim/__init__.py` with:
```python
"""Call Papyrus in a running Skyrim from Python."""
from .client import Batch, Game, connect  # noqa: F401
from .discovery import Session, find_session  # noqa: F401
from .errors import *  # noqa: F401,F403
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd python && uv run pytest -q`
Expected: all pass, in under 10 s.

- [ ] **Step 6: Commit**

```bash
git add python && git commit -m "feat(python): session discovery and synchronous client"
```

### Task 15: Plugin skeleton: build, logging, settings, lifecycle

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

### Task 16: Loopback server: IO thread, handshake, session file

**Files:**
- Create: `plugin/src/Wire.h`, `plugin/src/Server.h`, `plugin/src/Server.cpp`
- Modify: `plugin/src/main.cpp` (full replacement below)
- Create: `livetest/conftest.py`, `livetest/support.py`, `livetest/test_m1_transport.py`

**Interfaces:**
- Consumes: `spy::FrameDecoder`, `spy::encode_frame`, `spy::parse_hello`, `spy::check_hello`, `spy::parse_request`, `spy::make_welcome`, `spy::make_handshake_error`, `spy::make_error`, `spy::make_session_file`, `spy::Outbox`, `spy::ConnId`, `spy::known_ops` (Tasks 9–12); `spy::settings`, `spy::life`, `spy::log` (Task 15).
- Produces:
  - `plugin/src/Wire.h`: `std::string spy::Frame(const json&)` (invalid UTF-8 from game strings is replaced, never thrown; a reply over 16 MiB becomes an `internal` error frame for the same id)
  - `plugin/src/Server.h`: `struct spy::net::Callbacks { std::function<void(ConnId, Request)> onRequest; std::function<void(ConnId)> onDisconnect; std::function<void()> onTick; std::function<WelcomeInfo()> welcomeInfo; }`; `bool spy::net::Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox)`; `std::string spy::net::RandomToken()`; `bool spy::net::WriteSessionFile(const std::filesystem::path&, const json&)`; `std::uint32_t spy::net::ProcessId()`
  - Server rules: binds `127.0.0.1` only; refuses non-loopback peers; requests that arrive together with the client's close are still dispatched (their replies are dropped); the first frame must be a valid hello within 5 s; bad token / protocol → `handshake_error` then close; a frame that is not a JSON object or has an oversized header closes that connection; a request that fails validation gets an error response (the connection stays open); a connection whose unsent output exceeds 64 MiB is closed (a client that stopped reading).
  - `livetest/conftest.py`: fixtures `session` (a `skyrim.Session` from `SKYRIM_PYTHON_SESSION`), `game` (a connected `skyrim.Game`). `livetest/support.py`: `raw_connect(session) -> socket.socket` (handshaken raw socket), `send_raw(sock, bytes)`, `closed_by_server(sock, timeout=3.0) -> bool`

- [ ] **Step 1: Write the server**

`plugin/src/Wire.h`:
```cpp
#pragma once
#include "spy/framing.h"
#include "spy/messages.h"
#include <stdexcept>

namespace spy {
// Game strings are not always valid UTF-8; replace bad bytes instead of throwing.
inline std::string Frame(const json& j)
{
    const auto text = j.dump(-1, ' ', false, json::error_handler_t::replace);
    try {
        return encode_frame(text);
    } catch (const std::length_error&) {
        const auto id = j.contains("id") && j["id"].is_number_integer() ? j["id"].get<std::uint64_t>() : 0;
        return encode_frame(make_error(id, ErrorCode::internal, "response exceeds 16 MiB").dump());
    }
}
}
```

`plugin/src/Server.h`:
```cpp
#pragma once
#include "spy/messages.h"
#include "spy/outbox.h"
#include <filesystem>
#include <functional>

namespace spy::net {
struct Callbacks
{
    std::function<void(ConnId, Request)> onRequest;  // IO thread; must not block
    std::function<void(ConnId)> onDisconnect;        // IO thread; only for connections that finished the handshake
    std::function<void()> onTick;                    // IO thread; about every 5 ms
    std::function<WelcomeInfo()> welcomeInfo;        // IO thread
};

// Binds 127.0.0.1:port and starts the IO thread. Returns false (and logs why) if it cannot bind.
bool Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox);
std::string RandomToken();  // 128 bits from BCryptGenRandom, 32 lowercase hex chars
// Writes path.tmp then renames over path, so a client never reads a half-written file.
bool WriteSessionFile(const std::filesystem::path& path, const json& content);
std::uint32_t ProcessId();
}
```

`plugin/src/Server.cpp` (Winsock only; must not include CommonLib headers):
```cpp
#include "Server.h"
#include "Log.h"
#include "Wire.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <chrono>
#include <fstream>
#include <thread>
#include <unordered_map>
#include <vector>

namespace spy::net {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto kHelloTimeout = std::chrono::seconds(5);
constexpr std::size_t kMaxUnsent = 64u * 1024u * 1024u;

struct Conn
{
    SOCKET sock = INVALID_SOCKET;
    ConnId id = 0;
    FrameDecoder decoder;
    bool authed = false;
    bool closeAfterFlush = false;
    bool dead = false;
    std::string out;
    Clock::time_point accepted;
};

void SetNonBlocking(SOCKET s)
{
    u_long on = 1;
    ioctlsocket(s, FIONBIO, &on);
}

std::uint64_t IdOf(const json& j)
{
    if (j.is_object() && j.contains("id") && j["id"].is_number_integer() && j["id"].get<std::int64_t>() >= 0)
        return j["id"].get<std::uint64_t>();
    return 0;
}

class Server
{
public:
    Server(SOCKET listen, std::string token, Callbacks cb, Outbox& outbox)
        : listen_(listen), token_(std::move(token)), cb_(std::move(cb)), outbox_(outbox) {}

    void Run()
    {
        for (;;) {
            std::vector<WSAPOLLFD> fds;
            fds.push_back({listen_, POLLRDNORM, 0});
            for (auto& [id, c] : conns_)
                fds.push_back({c.sock, static_cast<SHORT>(POLLRDNORM | (c.out.empty() ? 0 : POLLWRNORM)), 0});
            WSAPoll(fds.data(), static_cast<ULONG>(fds.size()), 5);

            if (fds[0].revents & POLLRDNORM) Accept();
            for (std::size_t i = 1; i < fds.size(); ++i) {
                auto it = FindBySocket(fds[i].fd);
                if (it == conns_.end()) continue;
                if (fds[i].revents & (POLLRDNORM | POLLHUP | POLLERR)) Read(it->second);
            }
            if (cb_.onTick) cb_.onTick();
            const auto now = Clock::now();
            for (auto& [id, c] : conns_) {
                if (c.dead) continue;
                if (!c.authed && !c.closeAfterFlush && now - c.accepted > kHelloTimeout) {
                    log::Warn("server: connection " + std::to_string(id) + " sent no hello within 5 s; closing");
                    c.dead = true;
                    continue;
                }
                if (c.authed)
                    for (auto& f : outbox_.drain(id)) c.out += f;
                Write(c);
            }
            Reap();
        }
    }

private:
    std::unordered_map<ConnId, Conn>::iterator FindBySocket(SOCKET s)
    {
        for (auto it = conns_.begin(); it != conns_.end(); ++it)
            if (it->second.sock == s) return it;
        return conns_.end();
    }

    void Accept()
    {
        for (;;) {
            sockaddr_in peer{};
            int len = sizeof peer;
            SOCKET s = accept(listen_, reinterpret_cast<sockaddr*>(&peer), &len);
            if (s == INVALID_SOCKET) return;  // WSAEWOULDBLOCK: no more pending
            if (peer.sin_family != AF_INET || ntohl(peer.sin_addr.s_addr) != INADDR_LOOPBACK) {
                log::Warn("server: refused a non-loopback peer");
                closesocket(s);
                continue;
            }
            SetNonBlocking(s);
            BOOL nodelay = TRUE;
            setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof nodelay);
            Conn c;
            c.sock = s;
            c.id = nextId_++;
            c.accepted = Clock::now();
            conns_.emplace(c.id, std::move(c));
        }
    }

    void Read(Conn& c)
    {
        char buf[64 * 1024];
        bool eof = false;
        for (;;) {
            const int n = recv(c.sock, buf, sizeof buf, 0);
            if (n > 0) {
                c.decoder.feed(std::string_view(buf, static_cast<std::size_t>(n)));
                continue;
            }
            if (n == SOCKET_ERROR && WSAGetLastError() == WSAEWOULDBLOCK) break;
            eof = true;  // 0 = orderly close; other errors = reset
            break;
        }
        // Handle every complete frame first: a client may send requests and close in the same instant.
        while (!c.dead && !c.closeAfterFlush) {
            auto payload = c.decoder.next();
            if (!payload) break;
            auto j = json::parse(*payload, nullptr, false);
            if (j.is_discarded() || !j.is_object()) {
                log::Warn("server: connection " + std::to_string(c.id) + " sent a frame that is not a JSON object; closing");
                c.dead = true;
                break;
            }
            if (!c.authed) Handshake(c, j);
            else HandleRequest(c, j);
        }
        if (c.decoder.failed()) {
            log::Warn("server: connection " + std::to_string(c.id) + " announced a frame over 16 MiB; closing");
            c.dead = true;
        }
        if (eof) c.dead = true;
    }

    void Handshake(Conn& c, const json& j)
    {
        try {
            const auto hello = parse_hello(j);
            if (auto bad = check_hello(hello, token_)) {
                c.out += Frame(make_handshake_error(*bad, *bad == ErrorCode::unauthorized ? "bad token (stale session file?)"
                                                                                         : "protocol major version mismatch"));
                c.closeAfterFlush = true;
                log::Warn("server: handshake refused (" + std::string(to_string(*bad)) + ") for client '" + hello.client + "'");
                return;
            }
            c.authed = true;
            outbox_.open(c.id);
            c.out += Frame(make_welcome(cb_.welcomeInfo()));
            log::Info("server: client '" + hello.client + "' connected as " + std::to_string(c.id));
        } catch (const ProtocolError& e) {
            c.out += Frame(make_handshake_error(e.code, e.what()));
            c.closeAfterFlush = true;
        }
    }

    void HandleRequest(Conn& c, const json& j)
    {
        try {
            cb_.onRequest(c.id, parse_request(j));
        } catch (const ProtocolError& e) {
            c.out += Frame(make_error(IdOf(j), e.code, e.what()));
        }
    }

    void Write(Conn& c)
    {
        while (!c.dead && !c.out.empty()) {
            const int n = send(c.sock, c.out.data(), static_cast<int>(std::min<std::size_t>(c.out.size(), 1 << 20)), 0);
            if (n > 0) {
                c.out.erase(0, static_cast<std::size_t>(n));
                continue;
            }
            if (WSAGetLastError() != WSAEWOULDBLOCK) c.dead = true;
            break;
        }
        if (c.out.size() > kMaxUnsent) {
            log::Warn("server: connection " + std::to_string(c.id) + " is not reading its replies; closing");
            c.dead = true;
        }
        if (c.closeAfterFlush && c.out.empty()) c.dead = true;
    }

    void Reap()
    {
        for (auto it = conns_.begin(); it != conns_.end();) {
            if (!it->second.dead) {
                ++it;
                continue;
            }
            auto& c = it->second;
            closesocket(c.sock);
            if (c.authed) {
                outbox_.close(c.id);
                if (cb_.onDisconnect) cb_.onDisconnect(c.id);
                log::Info("server: connection " + std::to_string(c.id) + " closed");
            }
            it = conns_.erase(it);
        }
    }

    SOCKET listen_;
    std::string token_;
    Callbacks cb_;
    Outbox& outbox_;
    std::unordered_map<ConnId, Conn> conns_;
    ConnId nextId_ = 1;  // never reused, so a late reply can never reach a newer connection
};
}

bool Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox)
{
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        log::Error("server: WSAStartup failed");
        return false;
    }
    SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    BOOL excl = TRUE;
    setsockopt(ls, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&excl), sizeof excl);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, SOMAXCONN) != 0) {
        log::Error("server: cannot listen on 127.0.0.1:" + std::to_string(port) + " (WSA error " +
                   std::to_string(WSAGetLastError()) + "); is another game instance running?");
        closesocket(ls);
        return false;
    }
    SetNonBlocking(ls);
    auto* server = new Server(ls, std::move(token), std::move(cb), outbox);  // lives until process exit
    std::thread([server] { server->Run(); }).detach();
    log::Info("server: listening on 127.0.0.1:" + std::to_string(port));
    return true;
}

std::uint32_t ProcessId() { return GetCurrentProcessId(); }

std::string RandomToken()
{
    unsigned char bytes[16];
    BCryptGenRandom(nullptr, bytes, sizeof bytes, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    static constexpr char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char b : bytes) {
        out.push_back(hex[b >> 4]);
        out.push_back(hex[b & 0xF]);
    }
    return out;
}

bool WriteSessionFile(const std::filesystem::path& path, const json& content)
{
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    auto tmp = path;
    tmp += ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) {
            log::Error("server: cannot write " + tmp.string());
            return false;
        }
        f << content.dump(2);
    }
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        log::Error("server: cannot move session file into place: error " + std::to_string(GetLastError()));
        return false;
    }
    log::Info("server: session file " + path.string());
    return true;
}
}
```

- [ ] **Step 2: Start the server from `main.cpp`**

Replace `plugin/src/main.cpp` with (the stub request handler answers `unsupported` until Task 17):
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include "Server.h"
#include "Settings.h"
#include "Wire.h"
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
spy::Outbox g_outbox;

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

spy::WelcomeInfo Welcome()
{
    return {SPY_VERSION, REL::Module::get().version().string(), REL::Module::IsAE() ? "AE" : "SE",
            spy::life::Epoch(), spy::known_ops()};
}

void StartServer()
{
    const auto& cfg = spy::settings::Get();
    if (!cfg.allow_external_clients) {
        spy::log::Info("server: allow_external_clients=false; not listening");
        return;
    }
    const auto token = cfg.fixed_token.empty() ? spy::net::RandomToken() : cfg.fixed_token;
    spy::net::Callbacks cb;
    cb.onRequest = [](spy::ConnId conn, spy::Request req) {
        g_outbox.post(conn, spy::Frame(spy::make_error(req.id, spy::ErrorCode::unsupported, "dispatcher not built yet")));
    };
    cb.welcomeInfo = Welcome;
    if (!spy::net::Start(cfg.port, token, std::move(cb), g_outbox)) return;
    spy::net::WriteSessionFile(spy::settings::SessionFile(*SKSE::log::log_directory()),
        spy::make_session_file(cfg.port, token, spy::net::ProcessId(), SPY_VERSION));
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    spy::life::OnMessage(m);
    if (m->type == SKSE::MessagingInterface::kPostLoad) StartServer();
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
(`ProcessId()` lives in `Server.cpp` so that `main.cpp` never includes `<windows.h>` alongside CommonLib.)

- [ ] **Step 3: Live-test harness and transport tests**

`livetest/conftest.py`:
```python
"""Live tests: need the game running with SkyrimPython.dll and the dev ini.
Run from the repo root:  source tools/env.sh && uv run --project python pytest livetest -q
"""
import pytest

import skyrim


@pytest.fixture(scope="session")
def session():
    return skyrim.find_session()


@pytest.fixture
def game(session):
    g = skyrim.connect(port=session.port, token=session.token, client_name="livetest")
    yield g
    g.close()
```

`livetest/support.py` (helpers; test modules import it directly, as `python/tests` does):
```python
import socket
import struct
import time

from skyrim import _framing


def raw_connect(session) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(_framing.encode({"type": "hello", "protocol": [1, 0], "token": session.token, "client": "livetest-raw"}))
    assert _framing.read_frame(s)["type"] == "welcome"
    return s


def send_raw(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def closed_by_server(sock: socket.socket, timeout: float = 3.0) -> bool:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if sock.recv(65536) == b"":
                return True
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except socket.timeout:
        return False
    return False


def frame_header(n: int) -> bytes:
    return struct.pack("<I", n)
```

`livetest/test_m1_transport.py`:
```python
import socket

import pytest

import skyrim
from support import closed_by_server, frame_header, raw_connect, send_raw
from skyrim import _framing


def test_connect_and_welcome(session):
    with skyrim.connect(port=session.port, token=session.token) as g:
        assert g.welcome["protocol"][0] == 1
        assert "papyrus.call_static" in g.welcome["capabilities"]


def test_wrong_token_is_unauthorized(session):
    with pytest.raises(skyrim.Unauthorized):
        skyrim.connect(port=session.port, token="0" * 32)


def test_garbage_frame_closes_only_that_connection(session):
    bad = raw_connect(session)
    body = b"this is not json"
    send_raw(bad, frame_header(len(body)) + body)
    assert closed_by_server(bad)
    with skyrim.connect(port=session.port, token=session.token):
        pass


def test_oversized_header_closes_connection(session):
    bad = raw_connect(session)
    send_raw(bad, frame_header(_framing.MAX_FRAME + 1))
    assert closed_by_server(bad)


def test_invalid_request_gets_error_and_connection_stays_open(session):
    s = raw_connect(session)
    s.sendall(_framing.encode({"type": "request", "id": 5, "op": "no.such.op", "args": {}}))
    reply = _framing.read_frame(s)
    assert reply["id"] == 5 and reply["ok"] is False and reply["error"]["code"] == "bad_request"
    s.sendall(_framing.encode({"type": "request", "id": 6, "op": "no.such.op", "args": {}}))
    assert _framing.read_frame(s)["id"] == 6


def test_silent_client_is_dropped_after_hello_timeout(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=10)
    assert closed_by_server(s, timeout=8.0)
```

- [ ] **Step 4: Build, deploy, run**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh
source tools/env.sh
until [ -f "$SKYRIM_PYTHON_SESSION" ] && grep -q "listening on 127.0.0.1:45977" "$SKSE_LOG_DIR/SkyrimPython.log"; do sleep 2; done
uv run --project python pytest livetest/test_m1_transport.py -q
```
Expected: 6 passed (at the main menu is fine; no game needs to be loaded). The log shows `handshake refused (unauthorized)`, `not a JSON object; closing`, `over 16 MiB; closing`, `sent no hello within 5 s; closing`.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): loopback server with token handshake and session file"
```

### Task 17: Dispatcher, Papyrus value conversion, signatures, and the first ops

**Files:**
- Create: `plugin/src/Dispatch.h`, `plugin/src/Dispatch.cpp`, `plugin/src/PapyrusValues.h`, `plugin/src/PapyrusValues.cpp`, `plugin/src/Signatures.h`, `plugin/src/Signatures.cpp`, `plugin/src/Ops.h`, `plugin/src/Ops.cpp`
- Modify: `plugin/src/main.cpp` (three edits, shown below)
- Create: `livetest/test_m1_calls.py`, `livetest/test_m1_mainmenu.py`

**Interfaces:**
- Consumes: Tasks 10–12 (`Value`, `FormRef`, `AliasRef`, `ArrayValue`, `parse_call_args`, `make_ok`, `make_error`, `InflightLimiter`, `Outbox`, `form_ref_to_json`, `alias_ref_to_json`, `parse_form_key`, `format_form_key`, `is_runtime_form_id`, `check_fingerprint`), Task 15 (`spy::life`), Task 16 (`spy::Frame`, `spy::net::Callbacks`).
- Consumes from spikes: S2 (VM owns the `IFunctionArguments` passed to a dispatch; which thread runs the callback), S4 (alias handles work with `DispatchMethodCall`). If S2 showed the VM does *not* free the arguments, `DispatchStatic`/`DispatchMethod` in `Ops.cpp` must `delete` the `DynArgs` after the dispatch call returns.
- Produces:
  - `spy::dispatch::Init(Outbox&, const Config&)`, `OnRequest(ConnId, Request)`, `OnDisconnect(ConnId)`, `OnTick()`
  - `spy::ops::OpResult { bool ok; json result; ErrorCode code; std::string message; json detail; static Ok(json); static Err(ErrorCode, std::string, json = object) }`, `using Done = std::function<void(OpResult)>`, `using Cancelled = std::function<bool()>`, `void spy::ops::Run(const std::string& op, const json& args, Done, Cancelled)` (main thread), `json spy::ops::StartingState()`
  - `spy::pv::ResolveForm(const FormRef&) -> RE::TESForm*`, `ResolveAlias(const AliasRef&) -> RE::BGSBaseAlias*`, `DescribeForm(const RE::TESForm*) -> FormRef`, `DescribeAlias(const RE::BGSBaseAlias*) -> AliasRef`, `ScriptClassOf(const RE::TESForm*) -> std::string`, `struct Target { RE::BSTSmartPointer<RE::BSScript::Object> object; std::string desc; }`, `ResolveTarget(const Value& object, const std::string& script) -> Target`, `ToVariable(const Value&, const RE::BSScript::TypeInfo&, const std::string& param, RE::BSScript::Variable&)`, `FromVariable(const RE::BSScript::Variable&) -> json`, `TypeName(const RE::BSScript::TypeInfo&) -> std::string`
  - `spy::sig::IsA(const std::string& script, const std::string& ancestor) -> bool`, `FindFunction(const std::string& script, const std::string& function, bool member) -> RE::BSTSmartPointer<RE::BSScript::IFunction>`, `BindArgs(const RE::BSScript::IFunction&, const std::vector<Value>&, const std::string& qualified) -> std::vector<RE::BSScript::Variable>`, `FindProperty(const std::string& script, const std::string& property) -> RE::BSScript::TypeInfo`, `IEquals(std::string_view, std::string_view) -> bool`

Rules this task implements (spec 4.3–4.5):
- IO thread: cap check (`busy`), readiness check (`not_ready` with `reason`, answered immediately, never queued), then exactly one `AddTask` per request. `game.state` is answered on the IO thread before `kDataLoaded`, and on the main thread afterwards.
- Main thread: re-check readiness, resolve references, find the function, check every argument against the declared parameter types (the server fills no default values: argument count must match), dispatch.
- VM callback (thread recorded by S2): copy the raw `Variable`; convert it to JSON on the main thread (inline if the callback is already on the main thread, otherwise one more `AddTask`).
- IO thread, every tick: any request past its deadline gets `timeout`; its late result is dropped. A disconnect cancels the connection's queued work and forgets its in-flight count.
- Member functions are looked up in the script's empty state only (state-specific overrides are not considered).

- [ ] **Step 1: Write the live tests first** (they fail until the plugin is rebuilt)

`livetest/test_m1_calls.py`:
```python
"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars:
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
"""
import os
import time

import pytest

from skyrim import errors
from skyrim.values import Form

PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_state_reports_player(game):
    st = game.state()
    assert st["interactive"] is True and st["loading"] is False
    assert st["player"].form_id == PLAYER_ID
    assert st["player"].form_key == "Skyrim.esm|0x000014"
    assert st["player"].type == "Actor"
    assert st["game_days_passed"] > 0


def test_static_call(game):
    for _ in range(20):
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_static_call_returns_form(game):
    p = game.call("Game", "GetPlayer")
    assert p.form_id == PLAYER_ID and p.type == "Actor"


def test_method_call_float(game):
    hp = game.call_method(Form(form_id=PLAYER_ID), "Actor", "GetActorValue", "Health")
    assert isinstance(hp, float) and hp > 0


def test_method_call_on_ancestor_script(game):
    name = game.call_method(Form(form_id=PLAYER_ID), "Form", "GetName")
    assert isinstance(name, str)


def test_method_call_on_cold_base_form(game):
    # A base form no script has touched has no bound script object until the server binds one.
    sword = game.form(IRON_SWORD)
    name = game.call_method(sword, "Form", "GetName")
    assert isinstance(name, str) and name
    assert game.call_method(sword, "Weapon", "GetBaseDamage") > 0


def test_latent_function_completes(game):
    t0 = time.monotonic()
    assert game.call("Utility", "Wait", 0.5) is None
    assert time.monotonic() - t0 >= 0.4


def test_resolve_by_key_and_by_id(game):
    sword = game.form(IRON_SWORD)
    assert sword.type == "Weapon" and sword.form_key == IRON_SWORD
    assert game.resolve(form_id=sword.form_id).form_key == IRON_SWORD


def test_array_result_and_argument(game):
    assert game.call("Utility", "CreateIntArray", 3, 7) == [7, 7, 7]
    assert game.call("Utility", "ResizeIntArray", [1, 2, 3], 5, 0) == [1, 2, 3, 0, 0]


def test_unknown_script_and_function(game):
    with pytest.raises(errors.UnknownFunction):
        game.call("NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.UnknownFunction):
        game.call("Utility", "NoSuchFunction")


def test_wrong_argument_type_names_the_parameter(game):
    with pytest.raises(errors.TypeMismatch, match="aiMin"):
        game.call("Utility", "RandomInt", "one", 6)


def test_wrong_argument_count(game):
    with pytest.raises(errors.TypeMismatch, match="takes 2"):
        game.call("Utility", "RandomInt", 1)


def test_global_called_as_member_is_bad_request(game):
    with pytest.raises(errors.BadRequest, match="global"):
        game.call_method(Form(form_id=PLAYER_ID), "Utility", "RandomInt", 1, 6)


def test_method_on_wrong_kind_of_form(game):
    sword = game.form(IRON_SWORD)
    with pytest.raises(errors.TypeMismatch, match="Weapon"):
        game.call_method(sword, "Actor", "GetActorValue", "Health")


def test_missing_form_is_not_found(game):
    with pytest.raises(errors.NotFound):
        game.form("Skyrim.esm|0xFFFFF0")
    with pytest.raises(errors.NotFound):
        game.form("NotAPlugin.esp|0x000800")


def test_alias_method_calls(game):
    quest_id = int(os.environ["SPY_S4_QUEST"], 16)
    alias_id = int(os.environ["SPY_S4_ALIAS"])
    quest = game.resolve(form_id=quest_id)
    alias = game.alias(quest, alias_id)
    assert game.call_method(alias, "Alias", "GetOwningQuest").form_id == quest_id
    ref = game.call_method(alias, "ReferenceAlias", "GetReference")
    assert ref.form_id == int(os.environ["SPY_S4_REF"], 16)
```

`livetest/test_m1_mainmenu.py`:
```python
"""Run with the game at the main menu (launched, no save loaded)."""
import pytest

from skyrim import errors


def test_state_at_main_menu(game):
    st = game.state()
    assert st["data_ready"] is True
    assert st["game_loaded"] is False and st["interactive"] is False
    assert st["player"] is None


def test_calls_are_not_ready_with_reason(game):
    with pytest.raises(errors.NotReady) as ei:
        game.call("Utility", "RandomInt", 1, 6)
    assert ei.value.detail["reason"] == "no_game"
    with pytest.raises(errors.NotReady):
        game.form("Skyrim.esm|0x012EB7")
```

- [ ] **Step 2: Signatures**

`plugin/src/Signatures.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>
#include <string_view>
#include <vector>

namespace spy::sig {
bool IEquals(std::string_view a, std::string_view b);  // Papyrus names are case-insensitive
// True if `script` is `ancestor` or extends it. False if `script` is not a loaded script.
bool IsA(const std::string& script, const std::string& ancestor);
// Throws unknown_function (no such script or function) or bad_request (global vs member mix-up).
RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member);
// Converts arguments to the declared parameter types. Throws type_mismatch naming the parameter.
std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified);
// Declared type of a property on `script` or its parents. Throws not_found.
RE::BSScript::TypeInfo FindProperty(const std::string& script, const std::string& property);
}
```

`plugin/src/Signatures.cpp`:
```cpp
#include "Signatures.h"
#include "PapyrusValues.h"
#include <algorithm>
#include <cctype>
#include <format>

namespace spy::sig {
namespace {
RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> TypeOf(const std::string& script)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) return {};
    return info;
}
}

bool IEquals(std::string_view a, std::string_view b)
{
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
        return std::tolower(static_cast<unsigned char>(x)) == std::tolower(static_cast<unsigned char>(y));
    });
}

bool IsA(const std::string& script, const std::string& ancestor)
{
    auto info = TypeOf(script);
    for (auto* t = info.get(); t; t = t->GetParent())
        if (IEquals(t->GetName(), ancestor)) return true;
    return false;
}

RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::unknown_function, "no script named " + script + " is loaded");
    bool otherKind = false;
    // Global functions belong to the declaring script only; member functions are inherited.
    for (auto* t = info.get(); t; t = member ? t->GetParent() : nullptr) {
        for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
            const auto& f = t->GetMemberFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (member) return f;
                otherKind = true;
            }
        }
        for (std::uint32_t i = 0; i < t->GetNumGlobalFuncs(); ++i) {
            const auto& f = t->GetGlobalFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (!member) return f;
                otherKind = true;
            }
        }
    }
    if (otherKind)
        throw ProtocolError(ErrorCode::bad_request, script + "." + function +
            (member ? " is a global function; use papyrus.call_static" : " is a member function; use papyrus.call_method on an object"));
    throw ProtocolError(ErrorCode::unknown_function, "script " + script + " has no function " + function);
}

std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified)
{
    const auto n = fn.GetParamCount();
    if (args.size() != n)
        throw ProtocolError(ErrorCode::type_mismatch, std::format(
            "{} takes {} argument(s), got {} (the server does not fill in Papyrus default values)", qualified, n, args.size()));
    std::vector<RE::BSScript::Variable> out(n);
    for (std::uint32_t i = 0; i < n; ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        fn.GetParam(i, name, type);
        pv::ToVariable(args[i], type, std::format("{} (argument {} of {})", name.c_str(), i, qualified), out[i]);
    }
    return out;
}

RE::BSScript::TypeInfo FindProperty(const std::string& script, const std::string& property)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::not_found, "no script named " + script + " is loaded");
    for (auto* t = info.get(); t; t = t->GetParent())
        for (std::uint32_t i = 0; i < t->GetNumProperties(); ++i)
            if (IEquals(t->GetPropertyIter()[i].name.c_str(), property)) return t->GetPropertyIter()[i].info.type;
    throw ProtocolError(ErrorCode::not_found, "script " + script + " has no property " + property);
}
}
```

- [ ] **Step 3: Papyrus values**

`plugin/src/PapyrusValues.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>

// Main thread only: every function here touches forms or the VM.
namespace spy::pv {
RE::TESForm* ResolveForm(const FormRef& ref);            // throws not_found / stale_reference
RE::BGSBaseAlias* ResolveAlias(const AliasRef& ref);     // throws not_found / type_mismatch
FormRef DescribeForm(const RE::TESForm* form);
AliasRef DescribeAlias(const RE::BGSBaseAlias* alias);
std::string ScriptClassOf(const RE::TESForm* form);       // "Actor", "Weapon", ...; "Form" if unknown

struct Target
{
    RE::BSTSmartPointer<RE::BSScript::Object> object;  // bound script object to dispatch on
    std::string desc;
};
// The object a method or property is called on. `script` must be a script attached to it, or its
// native class or an ancestor of that; otherwise type_mismatch. A form no script has touched yet has
// no bound object: one is created and bound for its native class (as CommonLib does for arguments).
Target ResolveTarget(const Value& object, const std::string& script);

void ToVariable(const Value& v, const RE::BSScript::TypeInfo& declared, const std::string& param, RE::BSScript::Variable& out);
json FromVariable(const RE::BSScript::Variable& v);
std::string TypeName(const RE::BSScript::TypeInfo& t);
}
```

`plugin/src/PapyrusValues.cpp`:
```cpp
#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <format>

namespace spy::pv {
namespace {
using RE::BSScript::TypeInfo;
using RE::BSScript::Variable;
using Raw = TypeInfo::RawType;

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

std::string KeyOrId(const RE::TESForm* form)
{
    const auto id = form->GetFormID();
    if (!is_runtime_form_id(id))
        if (const auto* file = form->GetFile(0))
            return format_form_key({std::string(file->GetFilename()), form->GetLocalFormID()});
    return std::to_string(id);
}

Fingerprint LiveFingerprint(const RE::TESForm* form)
{
    const RE::TESForm* base = form;
    if (const auto* ref = form->As<RE::TESObjectREFR>())
        if (const auto* b = ref->GetBaseObject()) base = b;
    return {KeyOrId(base), ScriptClassOf(form)};
}

std::string Describe(const FormRef& r)
{
    return r.form_key ? *r.form_key : std::format("0x{:08X}", r.form_id.value_or(0));
}

TypeInfo ElementType(const TypeInfo& arrayType)
{
    const auto raw = static_cast<std::uintptr_t>(arrayType.GetRawType());
    if (arrayType.IsObjectArray()) return TypeInfo(static_cast<Raw>(raw & ~std::uintptr_t{1}));
    return TypeInfo(static_cast<Raw>(raw - (static_cast<std::uintptr_t>(Raw::kNoneArray) - static_cast<std::uintptr_t>(Raw::kNone))));
}

void BindObject(const void* ptr, RE::VMTypeID type, const std::string& nativeClass, const std::string& desc,
    const TypeInfo& declared, const std::string& param, Variable& out)
{
    const auto* cls = declared.GetTypeInfo();
    const std::string want = cls ? cls->GetName() : "";
    if (want.empty() || sig::IsA(nativeClass, want)) {
        RE::BSScript::PackHandle(&out, ptr, type);
        return;
    }
    // The parameter's type is a script (for example a quest script): it must be attached to this object.
    auto* policy = VM()->GetObjectHandlePolicy();
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (VM()->FindBoundObject(policy->GetHandleForObject(type, ptr), want.c_str(), bound) && bound) {
        out.SetObject(bound, declared.GetRawType());
        return;
    }
    throw ProtocolError(ErrorCode::type_mismatch, "parameter " + param + " expects " + want + ", got " + desc + " (a " + nativeClass + ")");
}

json ObjectToJson(const RE::BSScript::Object& obj)
{
    auto* policy = VM()->GetObjectHandlePolicy();
    const auto handle = obj.GetHandle();
    if (auto* form = RE::TESForm::LookupByID(static_cast<RE::FormID>(handle & 0xFFFFFFFF));
        form && policy->HandleIsType(static_cast<RE::VMTypeID>(form->GetFormType()), handle))
        return form_ref_to_json(DescribeForm(form));
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSRefAlias*>(policy->GetObjectForHandle(RE::BGSRefAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
    if (policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSLocAlias*>(policy->GetObjectForHandle(RE::BGSLocAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
    const auto* cls = obj.GetTypeInfo();
    throw ProtocolError(ErrorCode::unsupported, std::string("the result is a ") + (cls ? cls->GetName() : "script") +
        " object, which protocol v1 cannot return (for example ActiveMagicEffect)");
}
}

std::string ScriptClassOf(const RE::TESForm* form)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (VM()->GetScriptObjectType(static_cast<RE::VMTypeID>(form->GetFormType()), info) && info) return info->GetName();
    return "Form";
}

RE::TESForm* ResolveForm(const FormRef& r)
{
    RE::TESForm* form = nullptr;
    const bool runtime = !r.form_key && r.form_id && is_runtime_form_id(*r.form_id);
    if (r.form_key) {
        const auto k = parse_form_key(*r.form_key).value();  // validated when the request was parsed
        form = RE::TESDataHandler::GetSingleton()->LookupForm(k.local_id, k.plugin);
        if (!form) throw ProtocolError(ErrorCode::not_found, "no form " + *r.form_key + " (is " + k.plugin + " loaded?)");
    } else {
        form = RE::TESForm::LookupByID(*r.form_id);
        if (!form)
            throw ProtocolError(runtime ? ErrorCode::stale_reference : ErrorCode::not_found,
                "no form " + Describe(r) + (runtime ? " (the runtime reference no longer exists)" : ""));
    }
    if (form->IsDeleted())
        throw ProtocolError(runtime ? ErrorCode::stale_reference : ErrorCode::not_found, "form " + Describe(r) + " is deleted");
    if (runtime && r.fingerprint && check_fingerprint(r, LiveFingerprint(form)) == RefStatus::stale)
        throw ProtocolError(ErrorCode::stale_reference, "form " + Describe(r) + " is now a different object (expected base " +
            r.fingerprint->base + ", " + r.fingerprint->type + ")");
    return form;
}

RE::BGSBaseAlias* ResolveAlias(const AliasRef& a)
{
    auto* quest = ResolveForm(a.quest)->As<RE::TESQuest>();
    if (!quest) throw ProtocolError(ErrorCode::type_mismatch, "alias owner " + Describe(a.quest) + " is not a Quest");
    const auto want = a.alias_type == "ReferenceAlias" ? RE::BGSRefAlias::VMTYPEID : RE::BGSLocAlias::VMTYPEID;
    RE::BSReadLockGuard lock(quest->aliasAccessLock);
    for (auto* alias : quest->aliases) {
        if (!alias || alias->aliasID != static_cast<std::uint32_t>(a.alias_id)) continue;
        if (alias->GetVMTypeID() != want)
            throw ProtocolError(ErrorCode::type_mismatch, std::format("alias {} of {} is not a {}", a.alias_id, Describe(a.quest), a.alias_type));
        return alias;
    }
    throw ProtocolError(ErrorCode::not_found, std::format("quest {} has no alias {}", Describe(a.quest), a.alias_id));
}

FormRef DescribeForm(const RE::TESForm* form)
{
    FormRef r;
    r.form_id = form->GetFormID();
    r.type = ScriptClassOf(form);
    r.epoch = life::Epoch();
    if (is_runtime_form_id(*r.form_id)) r.fingerprint = LiveFingerprint(form);
    else if (form->GetFile(0)) r.form_key = KeyOrId(form);
    return r;
}

AliasRef DescribeAlias(const RE::BGSBaseAlias* alias)
{
    AliasRef a;
    a.quest = DescribeForm(alias->owningQuest);
    a.alias_id = static_cast<std::int32_t>(alias->aliasID);
    a.alias_type = alias->GetVMTypeID() == RE::BGSRefAlias::VMTYPEID ? "ReferenceAlias" : "LocationAlias";
    a.epoch = life::Epoch();
    return a;
}

Target ResolveTarget(const Value& object, const std::string& script)
{
    auto* vm = VM();
    auto* policy = vm->GetObjectHandlePolicy();
    const void* ptr = nullptr;
    RE::VMTypeID type = 0;
    std::string nativeClass, desc;
    if (const auto* f = std::get_if<FormRef>(&object.v)) {
        auto* form = ResolveForm(*f);
        ptr = form;
        type = static_cast<RE::VMTypeID>(form->GetFormType());
        nativeClass = ScriptClassOf(form);
        desc = Describe(*f);
    } else if (const auto* a = std::get_if<AliasRef>(&object.v)) {
        auto* alias = ResolveAlias(*a);
        ptr = alias;
        type = alias->GetVMTypeID();
        nativeClass = a->alias_type;
        desc = std::format("alias {} of {}", a->alias_id, Describe(a->quest));
    } else {
        throw ProtocolError(ErrorCode::bad_request, "object must be a form or alias reference");
    }
    const auto handle = policy->GetHandleForObject(type, ptr);
    if (handle == policy->EmptyHandle()) throw ProtocolError(ErrorCode::vm_error, "the VM has no handle for " + desc);
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (vm->FindBoundObject(handle, script.c_str(), bound) && bound) return {bound, desc};
    if (sig::IsA(nativeClass, script)) {
        // Finds the native object bound to this handle, or creates and binds one (cold forms).
        Variable v;
        RE::BSScript::PackHandle(&v, ptr, type);
        if (auto obj = v.GetObject()) return {obj, desc};
        throw ProtocolError(ErrorCode::vm_error, "could not bind a " + nativeClass + " script object to " + desc);
    }
    throw ProtocolError(ErrorCode::type_mismatch, desc + " is a " + nativeClass + " and has no script " + script + " attached");
}

std::string TypeName(const TypeInfo& t)
{
    if (t.IsObjectArray()) return std::string(ElementType(t).GetTypeInfo()->GetName()) + "[]";
    if (t.IsObject() && t.GetTypeInfo()) return t.GetTypeInfo()->GetName();
    switch (t.GetRawType()) {
    case Raw::kInt: return "Int";
    case Raw::kFloat: return "Float";
    case Raw::kBool: return "Bool";
    case Raw::kString: return "String";
    case Raw::kIntArray: return "Int[]";
    case Raw::kFloatArray: return "Float[]";
    case Raw::kBoolArray: return "Bool[]";
    case Raw::kStringArray: return "String[]";
    default: return "None";
    }
}

void ToVariable(const Value& v, const TypeInfo& t, const std::string& param, Variable& out)
{
    auto mismatch = [&]() -> void {
        throw ProtocolError(ErrorCode::type_mismatch,
            "parameter " + param + " expects " + TypeName(t) + ", got " + std::string(value_kind(v)));
    };
    const bool isNull = std::holds_alternative<std::monostate>(v.v);
    if (t.IsArray()) {
        if (isNull) {
            out.SetNone();
            return;
        }
        const auto* a = std::get_if<ArrayValue>(&v.v);
        if (!a) mismatch();
        const auto elem = ElementType(t);
        RE::BSTSmartPointer<RE::BSScript::Array> arr;
        if (!VM()->CreateArray(elem, static_cast<std::uint32_t>(a->items.size()), arr) || !arr)
            throw ProtocolError(ErrorCode::vm_error, "could not create an array for parameter " + param);
        for (std::size_t i = 0; i < a->items.size(); ++i)
            ToVariable(a->items[i], elem, std::format("{}[{}]", param, i), (*arr)[static_cast<std::uint32_t>(i)]);
        out.SetArray(arr);
        return;
    }
    if (t.IsObject()) {
        if (isNull) {
            out.SetObject(nullptr, t.GetRawType());
            return;
        }
        if (const auto* f = std::get_if<FormRef>(&v.v)) {
            auto* form = ResolveForm(*f);
            BindObject(form, static_cast<RE::VMTypeID>(form->GetFormType()), ScriptClassOf(form), Describe(*f), t, param, out);
            return;
        }
        if (const auto* a = std::get_if<AliasRef>(&v.v)) {
            auto* alias = ResolveAlias(*a);
            BindObject(alias, alias->GetVMTypeID(), a->alias_type, std::format("alias {}", a->alias_id), t, param, out);
            return;
        }
        mismatch();
    }
    switch (t.GetRawType()) {
    case Raw::kInt:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetSInt(*i);
        break;
    case Raw::kFloat:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetFloat(static_cast<float>(*i));
        if (const auto* f = std::get_if<float>(&v.v)) return out.SetFloat(*f);
        break;
    case Raw::kBool:
        if (const auto* b = std::get_if<bool>(&v.v)) return out.SetBool(*b);
        break;
    case Raw::kString:
        if (const auto* s = std::get_if<std::string>(&v.v)) return out.SetString(*s);
        break;
    default:
        throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " has a type protocol v1 cannot send: " + TypeName(t));
    }
    mismatch();
}

json FromVariable(const Variable& v)
{
    const auto t = v.GetType();
    if (t.GetRawType() == Raw::kNone || v.IsNoneObject()) return nullptr;
    if (v.IsBool()) return v.GetBool();
    if (v.IsInt()) return v.GetSInt();
    if (v.IsFloat()) return static_cast<double>(v.GetFloat());
    if (v.IsString()) return std::string(v.GetString());
    if (t.IsArray()) {
        auto arr = v.GetArray();
        if (!arr) return nullptr;
        json items = json::array();
        for (std::uint32_t i = 0; i < arr->size(); ++i) items.push_back(FromVariable((*arr)[i]));
        std::string elem;
        const auto& et = arr->type_info();
        if (et.IsBool()) elem = "Bool";
        else if (et.IsInt()) elem = "Int";
        else if (et.IsFloat()) elem = "Float";
        else if (et.IsString()) elem = "String";
        else {
            elem = "Form";
            for (const auto& item : items)
                if (item.is_object()) {
                    elem = item.value("kind", "") == "alias" ? "Alias" : "Form";
                    break;
                }
        }
        return json{{"array", elem}, {"items", items}};
    }
    if (t.IsObject()) {
        auto obj = v.GetObject();
        if (!obj) return nullptr;
        return ObjectToJson(*obj);
    }
    throw ProtocolError(ErrorCode::unsupported, "the result has a type protocol v1 cannot return");
}
}
```

- [ ] **Step 4: Ops**

`plugin/src/Ops.h`:
```cpp
#pragma once
#include "spy/messages.h"
#include <functional>

namespace spy::ops {
struct OpResult
{
    bool ok = true;
    json result;
    ErrorCode code = ErrorCode::internal;
    std::string message;
    json detail = json::object();

    static OpResult Ok(json r)
    {
        OpResult o;
        o.result = std::move(r);
        return o;
    }
    static OpResult Err(ErrorCode c, std::string m, json d = json::object())
    {
        OpResult o;
        o.ok = false;
        o.code = c;
        o.message = std::move(m);
        o.detail = std::move(d);
        return o;
    }
};

using Done = std::function<void(OpResult)>;  // called once per op; from the main thread
using Cancelled = std::function<bool()>;     // true once the request was answered (timeout or disconnect)

// Main thread only. Validation failures become Err results; nothing throws out of Run.
void Run(const std::string& op, const json& args, Done done, Cancelled cancelled);
// game.state before kDataLoaded (answered on the IO thread; touches no game objects).
json StartingState();
}
```

`plugin/src/Ops.cpp`:
```cpp
#include "Ops.h"
#include "Lifecycle.h"
#include "PapyrusValues.h"
#include "Signatures.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <thread>

namespace spy::ops {
namespace {
using RE::BSScript::Variable;

std::thread::id g_mainThread;  // set by Run, which only ever runs on the main thread

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

// Arguments for one dispatch. The VM takes ownership of this object (spike S2).
class DynArgs final : public RE::BSScript::IFunctionArguments
{
public:
    explicit DynArgs(std::vector<Variable> v) : v_(std::move(v)) {}
    bool operator()(RE::BSScrapArray<Variable>& dst) const override
    {
        dst.resize(static_cast<std::uint32_t>(v_.size()));
        for (std::size_t i = 0; i < v_.size(); ++i) dst[static_cast<std::uint32_t>(i)] = v_[i];
        return true;
    }

private:
    std::vector<Variable> v_;
};

void Convert(const Done& done, const Variable& result)
{
    try {
        done(OpResult::Ok(pv::FromVariable(result)));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
}

class Callback final : public RE::BSScript::IStackCallbackFunctor
{
public:
    explicit Callback(Done d) : done_(std::move(d)) {}
    void operator()(Variable result) override
    {
        if (std::this_thread::get_id() == g_mainThread) return Convert(done_, result);
        auto done = done_;
        SKSE::GetTaskInterface()->AddTask([done, result] { Convert(done, result); });
    }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}

private:
    Done done_;
};

bool Paused()
{
    auto* ui = RE::UI::GetSingleton();
    return ui && ui->GameIsPaused();
}

OpResult PausedError() { return OpResult::Err(ErrorCode::not_ready, "the game is paused", {{"reason", "paused"}}); }

json GameState()
{
    json s = {{"data_ready", life::DataReady()}, {"game_loaded", life::GameLoaded()}, {"loading", life::Loading()},
              {"interactive", life::Interactive()}, {"epoch", life::Epoch()}, {"menus", life::OpenMenus()},
              {"paused", Paused()}, {"player", nullptr}, {"game_days_passed", nullptr}};
    if (life::GameLoaded() && !life::Loading()) {
        if (auto* player = RE::PlayerCharacter::GetSingleton()) s["player"] = form_ref_to_json(pv::DescribeForm(player));
        if (auto* cal = RE::Calendar::GetSingleton()) s["game_days_passed"] = cal->GetDaysPassed();
    }
    return s;
}

json Resolve(const json& args)
{
    RE::TESForm* form = nullptr;
    if (args.contains("editor_id")) {
        const auto eid = args["editor_id"].get<std::string>();
        form = RE::TESForm::LookupByEditorID(eid);
        if (!form)
            throw ProtocolError(ErrorCode::not_found, "no form with editor id " + eid +
                " (the game keeps editor ids for only some form types unless powerofthree's Tweaks is installed)");
    } else {
        json ref = args;
        ref["kind"] = "form";
        form = pv::ResolveForm(form_ref_from_json(ref));
    }
    return form_ref_to_json(pv::DescribeForm(form));
}

void CallStatic(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && Paused()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto fn = sig::FindFunction(c.script, c.function, false);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done)};
    if (!VM()->DispatchStaticCall(RE::BSFixedString(c.script), RE::BSFixedString(c.function), new DynArgs(std::move(vars)), cb))
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified));
}

void CallMethod(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && Paused()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto target = pv::ResolveTarget(*c.object, c.script);
    auto fn = sig::FindFunction(c.script, c.function, true);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done)};
    if (!VM()->DispatchMethodCall(target.object, RE::BSFixedString(c.function), new DynArgs(std::move(vars)), cb))
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified + " on " + target.desc));
}
}

json StartingState()
{
    return {{"data_ready", false}, {"game_loaded", false}, {"loading", false}, {"interactive", false},
            {"epoch", 0}, {"menus", json::array()}, {"paused", false}, {"player", nullptr}, {"game_days_passed", nullptr}};
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled)
{
    g_mainThread = std::this_thread::get_id();
    try {
        if (op == "game.state") return done(OpResult::Ok(GameState()));
        if (auto why = life::NotReadyReason())
            return done(OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", {{"reason", *why}}));
        if (op == "form.resolve") return done(OpResult::Ok(Resolve(args)));
        if (op == "papyrus.call_static") return CallStatic(parse_call_args(args, false), done);
        if (op == "papyrus.call_method") return CallMethod(parse_call_args(args, true), done);
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
    (void)cancelled;  // used by papyrus.batch (Task 18)
}
}
```

- [ ] **Step 5: Dispatch**

`plugin/src/Dispatch.h`:
```cpp
#pragma once
#include "spy/config.h"
#include "spy/messages.h"
#include "spy/outbox.h"

namespace spy::dispatch {
void Init(Outbox& outbox, const Config& cfg);
void OnRequest(ConnId conn, Request req);  // IO thread
void OnDisconnect(ConnId conn);            // IO thread
void OnTick();                             // IO thread
}
```

`plugin/src/Dispatch.cpp`:
```cpp
#include "Dispatch.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Wire.h"
#include <SKSE/SKSE.h>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <unordered_map>

namespace spy::dispatch {
namespace {
using Clock = std::chrono::steady_clock;

Outbox* g_outbox = nullptr;
const Config* g_cfg = nullptr;
std::unique_ptr<InflightLimiter> g_limiter;

struct PendingCall
{
    ConnId conn = 0;
    std::uint64_t id = 0;
    std::uint32_t timeoutMs = 0;
    Clock::time_point deadline;
    std::atomic_bool done{false};

    // Once only: the first of result / timeout / cancel wins; later ones are dropped.
    bool Finish(const ops::OpResult& r)
    {
        if (done.exchange(true)) return false;
        g_outbox->post(conn, Frame(r.ok ? make_ok(id, r.result) : make_error(id, r.code, r.message, r.detail)));
        g_limiter->release(conn);
        return true;
    }
};

std::mutex g_mx;
std::unordered_map<PendingCall*, std::shared_ptr<PendingCall>> g_pending;

void Forget(PendingCall* p)
{
    std::lock_guard l(g_mx);
    g_pending.erase(p);
}

void Complete(const std::shared_ptr<PendingCall>& call, const ops::OpResult& r)
{
    if (call->Finish(r)) Forget(call.get());
}
}

void Init(Outbox& outbox, const Config& cfg)
{
    g_outbox = &outbox;
    g_cfg = &cfg;
    g_limiter = std::make_unique<InflightLimiter>(cfg.max_inflight_per_connection, cfg.max_inflight_total);
}

void OnRequest(ConnId conn, Request req)
{
    if (!g_limiter->try_acquire(conn)) {
        g_outbox->post(conn, Frame(make_error(req.id, ErrorCode::busy, "too many calls in flight",
            {{"max_per_connection", g_cfg->max_inflight_per_connection}, {"max_total", g_cfg->max_inflight_total}})));
        return;
    }
    auto call = std::make_shared<PendingCall>();
    call->conn = conn;
    call->id = req.id;
    call->timeoutMs = req.args.value("timeout_ms", g_cfg->default_timeout_ms);
    call->deadline = Clock::now() + std::chrono::milliseconds(call->timeoutMs);
    {
        std::lock_guard l(g_mx);
        g_pending.emplace(call.get(), call);
    }
    if (req.op == "game.state" && !life::DataReady()) return Complete(call, ops::OpResult::Ok(ops::StartingState()));
    if (req.op != "game.state")
        if (auto why = life::NotReadyReason())
            return Complete(call, ops::OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", {{"reason", *why}}));

    SKSE::GetTaskInterface()->AddTask([call, op = std::move(req.op), args = std::move(req.args)] {
        if (call->done) return;  // timed out or disconnected while queued
        ops::Run(op, args, [call](ops::OpResult r) { Complete(call, r); }, [call] { return call->done.load(); });
    });
}

void OnDisconnect(ConnId conn)
{
    g_limiter->forget(conn);
    std::lock_guard l(g_mx);
    for (auto it = g_pending.begin(); it != g_pending.end();) {
        if (it->second->conn == conn) {
            it->second->done = true;  // cancels queued work; its result, if any, is dropped
            it = g_pending.erase(it);
        } else {
            ++it;
        }
    }
}

void OnTick()
{
    const auto now = Clock::now();
    std::vector<std::shared_ptr<PendingCall>> expired;
    {
        std::lock_guard l(g_mx);
        for (auto& [p, call] : g_pending)
            if (now >= call->deadline) expired.push_back(call);
    }
    for (auto& call : expired)
        Complete(call, ops::OpResult::Err(ErrorCode::timeout,
            "no result within " + std::to_string(call->timeoutMs) + " ms (the call may still finish in the game; "
            "Papyrus does not run while the game is paused in a menu)"));
}
}
```

- [ ] **Step 6: Wire the dispatcher into `main.cpp`**

In `plugin/src/main.cpp`:
1. Add `#include "Dispatch.h"` after `#include "Lifecycle.h"`.
2. Replace the stub `cb.onRequest = [](spy::ConnId conn, spy::Request req) { ... };` block with:
```cpp
    spy::dispatch::Init(g_outbox, cfg);
    cb.onRequest = spy::dispatch::OnRequest;
    cb.onDisconnect = spy::dispatch::OnDisconnect;
    cb.onTick = spy::dispatch::OnTick;
```
3. Nothing else changes (`cfg` is the `const Config&` already in `StartServer`; `settings::Get()` returns a reference to a static, so the pointer `Init` keeps stays valid).

- [ ] **Step 7: Build, deploy, run the live tests**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh
source tools/env.sh
until grep -q "listening on 127.0.0.1:45977" "$SKSE_LOG_DIR/SkyrimPython.log" 2>/dev/null; do sleep 2; done
until grep -q "lifecycle: Main Menu opened" "$SKSE_LOG_DIR/SkyrimPython.log"; do sleep 2; done
uv run --project python pytest livetest/test_m1_mainmenu.py livetest/test_m1_transport.py -q
# Load a save from the main menu (Continue), then, with the values recorded by spike S4:
export SPY_S4_QUEST=0x... SPY_S4_ALIAS=... SPY_S4_REF=0x...
uv run --project python pytest livetest/test_m1_calls.py -q
```
Expected: main-menu and transport tests pass before the save is loaded; all of `test_m1_calls.py` passes after. If `test_array_result_and_argument` fails with `UnknownFunction`, the runtime's `Utility` lacks those functions: check `Scripts/Source/Utility.psc` and replace them with another array function listed there (record which). If S4 failed, `test_alias_method_calls` is expected to fail with `vm_error`; mark it `xfail(reason="S4 failed; aliases via M1.5 helper")` and note it in `spikes/RESULTS.md`.

- [ ] **Step 8: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): generic Papyrus dispatcher with typed argument checks"
```

### Task 18: Properties, batch, and the in-flight caps

**Files:**
- Modify: `plugin/src/Ops.cpp`
- Create: `livetest/test_m1_batch_props.py`

**Interfaces:**
- Consumes: Task 17 (`ops::Run`, `pv::ResolveTarget`, `pv::ToVariable`, `pv::FromVariable`, `sig::FindProperty`), Task 12 (`parse_property_args`, `parse_batch_args`, `BatchArgs`).
- Produces: `papyrus.get_property`, `papyrus.set_property`, `papyrus.batch` served by `ops::Run`. Batch semantics (spec 4.3): steps run in order, one `AddTask` per step, not a transaction; the batch-level `timeout_ms` covers all steps (per-step `timeout_ms` is ignored); the response is `{"results": [...]}` with one entry per step run, shorter than the step list if `stop_on_error` stopped it; a timed-out or disconnected batch runs no further steps.

- [ ] **Step 1: Write the live tests**

`livetest/test_m1_batch_props.py`:
```python
import pytest

from support import raw_connect
from skyrim import _framing, errors
from skyrim.values import Form

PLAYER = Form(form_id=0x14)


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_batch_runs_in_order_and_reports_each_step(game):
    b = (game.batch()
         .call("Utility", "RandomInt", 1, 6)
         .call("Game", "GetPlayer")
         .call("NoSuchScriptAnywhere", "Foo")
         .call_method(PLAYER, "Actor", "GetActorValue", "Health"))
    results = b.run(stop_on_error=False)
    assert len(results) == 4
    assert 1 <= results[0] <= 6
    assert results[1].form_id == 0x14
    assert isinstance(results[2], errors.UnknownFunction)
    assert results[3] > 0


def test_batch_stops_on_first_error(game):
    results = (game.batch()
               .call("Utility", "RandomInt", 1, 6)
               .call("NoSuchScriptAnywhere", "Foo")
               .call("Utility", "RandomInt", 1, 6)).run(stop_on_error=True)
    assert len(results) == 2 and isinstance(results[1], errors.UnknownFunction)


def test_batch_of_100_steps(game):
    b = game.batch()
    for _ in range(100):
        b.call("Utility", "RandomInt", 1, 6)
    results = b.run(timeout=30)
    assert len(results) == 100 and all(1 <= r <= 6 for r in results)


def test_property_errors(game):
    with pytest.raises(errors.NotFound, match="no script named"):
        game.get_property(PLAYER, "NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.NotFound, match="no property"):
        game.get_property(PLAYER, "Actor", "NoSuchProperty")


def test_per_connection_cap_returns_busy(session):
    s = raw_connect(session)
    n = 300
    for i in range(1, n + 1):
        s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                   "args": {"script": "Utility", "function": "Wait", "args": [1.0]}}))
    codes = []
    s.settimeout(30)
    for _ in range(n):
        r = _framing.read_frame(s)
        codes.append("ok" if r["ok"] else r["error"]["code"])
    s.close()
    assert codes.count("busy") == n - 256
    assert codes.count("ok") == 256
```
v1 property ops target auto properties. Property reads and writes on a real script property are tested in M1.5 (including what the engine does with a full property that has getter/setter functions; `vm_error` is an acceptable, documented outcome), against the test ESP's quest script (no vanilla script offers a property that is both always bound and safe to write). Here only the error paths run live; the happy path is exercised by M1.5's plan before v1 ships.

- [ ] **Step 2: Implement properties and batch in `Ops.cpp`**

Add inside the anonymous namespace of `plugin/src/Ops.cpp`, after `CallMethod`:
```cpp
// v1 targets auto properties (a stored value). A full property's getter/setter is Papyrus code the
// engine cannot run synchronously here; M1.5's test ESP probes what the engine does with those.
json GetProperty(const PropertyArgs& p)
{
    sig::FindProperty(p.script, p.property);  // clear not_found before touching the object
    auto obj = pv::ResolveTarget(p.object, p.script).object;
    Variable out;
    if (!VM()->GetPropertyValue(obj, p.property.c_str(), out))
        throw ProtocolError(ErrorCode::vm_error, "could not read " + p.script + "." + p.property);
    return pv::FromVariable(out);
}

void SetProperty(const PropertyArgs& p)
{
    const auto type = sig::FindProperty(p.script, p.property);
    auto obj = pv::ResolveTarget(p.object, p.script).object;
    Variable in;
    pv::ToVariable(*p.value, type, p.property + " (property of " + p.script + ")", in);
    if (!VM()->SetPropertyValue(obj, p.property.c_str(), in))
        throw ProtocolError(ErrorCode::vm_error, "could not write " + p.script + "." + p.property);
}

struct BatchState
{
    BatchArgs args;
    std::size_t next = 0;
    json results = json::array();
    Done done;
    Cancelled cancelled;
};

void BatchStep(const std::shared_ptr<BatchState>& st);

void QueueBatchStep(const std::shared_ptr<BatchState>& st)
{
    SKSE::GetTaskInterface()->AddTask([st] { BatchStep(st); });  // one task per step (spec 4.5)
}

void BatchStep(const std::shared_ptr<BatchState>& st)
{
    if (st->cancelled()) return;
    if (st->next == st->args.calls.size()) return st->done(OpResult::Ok({{"results", st->results}}));
    const auto step = st->args.calls[st->next++];
    Run(step.op, step.args, [st](OpResult r) {
        st->results.push_back(r.ok ? json{{"ok", true}, {"result", r.result}}
                                   : json{{"ok", false}, {"error", {{"code", to_string(r.code)}, {"message", r.message}, {"detail", r.detail}}}});
        if (!r.ok && st->args.stop_on_error) return st->done(OpResult::Ok({{"results", st->results}}));
        QueueBatchStep(st);
    }, st->cancelled);
}
```
Then in `Run`, replace the line
```cpp
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
    (void)cancelled;  // used by papyrus.batch (Task 18)
```
with
```cpp
        if (op == "papyrus.get_property") return done(OpResult::Ok(GetProperty(parse_property_args(args, false))));
        if (op == "papyrus.set_property") {
            SetProperty(parse_property_args(args, true));
            return done(OpResult::Ok(nullptr));
        }
        if (op == "papyrus.batch") {
            auto st = std::make_shared<BatchState>();
            st->args = parse_batch_args(args);
            st->done = std::move(done);
            st->cancelled = std::move(cancelled);
            return BatchStep(st);
        }
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
```
Note: a batch step's `done` runs on the main thread (Task 17's `Callback` converts results there), so `BatchState` is only touched from the main thread. The `catch` in `Run` cannot fire after `done` was moved into the batch: `parse_batch_args` is the only throwing call before the move, and `BatchStep` routes each step's errors through that step's own `Run`.

- [ ] **Step 3: Build, deploy, run**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh   # then load a save
source tools/env.sh
uv run --project python pytest livetest/test_m1_batch_props.py livetest/test_m1_calls.py -q
```
Expected: all pass. The cap test takes about 1–2 s (256 one-second waits run concurrently in Papyrus).

- [ ] **Step 4: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): property access, ordered batches, in-flight caps"
```

### Task 19: Robustness and throughput baseline

**Files:**
- Create: `livetest/test_m1_robustness.py`, `livetest/test_m1_throughput.py`, `livetest/results/.gitkeep`

**Interfaces:**
- Consumes: the live plugin from Task 18 and the Python client.
- Produces: `livetest/results/throughput.json` with keys `date`, `game_version`, `sequential_calls`, `sequential_seconds`, `sequential_per_s`, `p50_ms`, `p99_ms`, `pipelined_calls`, `pipelined_threads`, `pipelined_seconds`, `pipelined_per_s`.

- [ ] **Step 1: Write the robustness tests**

`livetest/test_m1_robustness.py`:
```python
import time

import pytest

import skyrim
from support import raw_connect
from skyrim import _framing, errors
from skyrim.values import Fingerprint, Form

PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_int_overflow_is_rejected_by_the_server(session):
    s = raw_connect(session)
    s.sendall(_framing.encode({"type": "request", "id": 1, "op": "papyrus.call_static",
                               "args": {"script": "Utility", "function": "RandomInt", "args": [1, 2147483648]}}))
    r = _framing.read_frame(s)
    assert r["ok"] is False and r["error"]["code"] == "type_mismatch"


def test_runtime_reference_fingerprint(game):
    sword = game.form(IRON_SWORD)
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", sword, 1, False, True)
    try:
        assert ref.form_id >> 24 == 0xFF and ref.form_key is None
        assert ref.fingerprint == Fingerprint(IRON_SWORD, "ObjectReference")
        assert game.call_method(ref, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
        forged = Form(form_id=ref.form_id, fingerprint=Fingerprint("Skyrim.esm|0x000F", "ObjectReference"))
        with pytest.raises(errors.StaleReference):
            game.call_method(forged, "ObjectReference", "GetBaseObject")
    finally:
        game.call_method(ref, "ObjectReference", "Delete")


def test_disconnect_with_calls_in_flight(session, game):
    s = raw_connect(session)
    for i in range(1, 51):
        s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                   "args": {"script": "Utility", "function": "Wait", "args": [2.0]}}))
    time.sleep(0.2)  # let the server dispatch them, so they really are in flight when we close
    s.close()
    time.sleep(3)  # let the 50 waits finish in the game and their results be dropped
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6
    # The closed connection's slots were released: a new connection gets the full per-connection cap.
    s2 = raw_connect(session)
    for i in range(1, 257):
        s2.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                    "args": {"script": "Utility", "function": "Wait", "args": [0.2]}}))
    s2.settimeout(30)
    codes = [_framing.read_frame(s2)["ok"] for _ in range(256)]
    s2.close()
    assert all(codes)


def test_timeout_is_reported_and_late_result_dropped(game):
    with pytest.raises(errors.Timeout):
        game.call("Utility", "Wait", 3.0, timeout=0.5)
    time.sleep(3)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_many_short_connections(session):
    for _ in range(50):
        with skyrim.connect(port=session.port, token=session.token) as g:
            assert g.state()["interactive"] is True
```

- [ ] **Step 2: Write the throughput baseline**

`livetest/test_m1_throughput.py`:
```python
import datetime
import json
import pathlib
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

RESULTS = pathlib.Path(__file__).parent / "results" / "throughput.json"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_throughput_baseline(game):
    lat = []
    t0 = time.perf_counter()
    for _ in range(2000):
        c0 = time.perf_counter()
        game.call("Utility", "RandomInt", 1, 6)
        lat.append((time.perf_counter() - c0) * 1000)
    seq_s = time.perf_counter() - t0

    threads = 32
    t1 = time.perf_counter()
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda _: game.call("Utility", "RandomInt", 1, 6), range(10000)))
    pipe_s = time.perf_counter() - t1
    assert all(1 <= r <= 6 for r in results)

    lat.sort()
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": game.welcome["game_version"],
        "sequential_calls": 2000,
        "sequential_seconds": round(seq_s, 3),
        "sequential_per_s": round(2000 / seq_s, 1),
        "p50_ms": round(statistics.median(lat), 2),
        "p99_ms": round(lat[int(len(lat) * 0.99)], 2),
        "pipelined_calls": 10000,
        "pipelined_threads": threads,
        "pipelined_seconds": round(pipe_s, 3),
        "pipelined_per_s": round(10000 / pipe_s, 1),
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
```
There is no pass threshold in v1: this records the baseline that M2 and skyrim-dm budget against. Compare `p50_ms` with the frame time (about 16.7 ms at 60 fps): a p50 of 2–3 frames is expected (one frame for the `AddTask`, one for the VM, one more if the callback is off the main thread).

- [ ] **Step 3: Run**

```bash
source tools/env.sh
uv run --project python pytest livetest/test_m1_robustness.py livetest/test_m1_throughput.py -q -s
grep -ciE "error|exception" "$SKSE_LOG_DIR/SkyrimPython.log"
```
Expected: all pass; the grep shows only the errors the tests provoked on purpose (none at `error` level from the server: refused handshakes and bad frames are `warning`). The game is still responsive afterwards.

- [ ] **Step 4: Commit**

```bash
git add livetest && git commit -m "test(live): robustness and throughput baseline for the M1 dispatcher"
```

### Task 20: README and milestone tag

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the README**

Replace `README.md` with:
````markdown
# skyrim-python

Call any Papyrus function in a running Skyrim (SE/AE) from Python.

`SkyrimPython.dll` (an SKSE plugin) serves a small JSON protocol on `127.0.0.1`; the `skyrim` Python package (stdlib only) is the client. Built as the foundation for [skyrim-dm].

Status: M1 (dispatcher). Verified on Skyrim AE 1.6.1170 under GE-Proton. Events and save/load arrive in M2.

## Use

```python
import skyrim

game = skyrim.connect()                  # finds the session file via SKYRIM_PYTHON_SESSION
game.wait_ready()                        # a save is loaded and no loading screen is up
player = game.call("Game", "GetPlayer")
hp = game.call_method(player, "Actor", "GetActorValue", "Health")
sword = game.form("Skyrim.esm|0x012EB7")
game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
```
Every Papyrus argument must be passed (the server does not fill in default values yet). Errors are typed: `skyrim.NotReady`, `skyrim.TypeMismatch`, `skyrim.UnknownFunction`, `skyrim.StaleReference`, ...

## Install (player)
Copy `SkyrimPython.dll` and `SkyrimPython.ini` to `Data/SKSE/Plugins/`. Set `allow_external_clients = true` in the ini to let a program outside the game connect. The plugin writes `Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPython/session.json` (port and a per-run token) each start.

## Develop
- Core and client tests (Linux): `cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build` and `cd python && uv run pytest`.
- Plugin build (Windows box over ssh): `tools/remote_build.sh plugin SkyrimPython.dll`; deploy with `tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini`; launch with `tools/launch_game.sh`.
- Live tests (game running): `source tools/env.sh && uv run --project python pytest livetest -q`.
- Protocol: `protocol/PROTOCOL.md`, fixtures in `protocol/v1/fixtures/`. Design: `docs/specs/2026-09-30-skyrim-python-design.md`.

## Licence
MIT.

[skyrim-dm]: ../skyrim-dm
````

- [ ] **Step 2: Run everything once more**

```bash
ctest --test-dir core/build --output-on-failure
(cd python && uv run pytest -q)
source tools/env.sh && uv run --project python pytest livetest -q --deselect livetest/test_m1_mainmenu.py
```
Expected: all pass (main-menu tests were run in Task 17 before loading a save).

- [ ] **Step 3: Commit and tag (local only; do not push)**

```bash
git add README.md && git commit -m "docs: README for the M1 dispatcher"
git tag -a m1 -m "M1: dispatcher, protocol v1, sync client"
```

---

## After this plan

Write the next plan when this one is done and `spikes/RESULTS.md` is final. It covers, per spec section 9:

1. **M1.5: test ESP and engine probes.** Choose the authoring tool (Mutagen from C#, the Creation Kit on the Windows box, or xEdit scripts); author `SkyrimPythonTest.esp` with the shell quest (≥3 reference aliases, 1 location alias, stages 10/20/100/200, one objective) and a quest script with an auto property, a full property, `SetupContract(...)` and stage fragments calling `SkyrimPython.Send`. Add the property happy-path live tests deferred from Task 18. Run the three probes (three alias tags in one objective line; PO3 `SetObjectiveText` with alias tags; objective text and alias fills after save and reload) and write `docs/probes/`.
2. **M2: events and lifecycle.** Event bus with per-connection bounded queues and `dropped` counts; `events.subscribe/unsubscribe` with filters; the v1 event list in spec 4.6; mod events and `SkyrimPython.Send` (design per S5); `game.load`/`game.save` via `AddUITask` (per S6); the client's event dispatch thread, `game.events.wait`, `on_*` helpers; a live-test harness that loads a fixed save before each module; `require_unpaused` live test by opening a menu from the harness. Also place the spec 4.3 ops that no milestone names yet: `native.actors_near` and `native.console` (off by default; `enable_console` already exists in the config) — build them in M2 only if skyrim-dm's acceptance test needs them, otherwise move them to M3 in the spec.
   Also: move result encoding (`json::dump` + framing) off the main thread — post `json` to the outbox and encode on the IO thread — so a large array result does not cost the game a frame (M1 encodes on whichever thread finishes the call).
3. **skyrim-dm acceptance test** (spec section 8), using the generic call with every argument passed, with skyrim-dm depending on the `skyrim` package.

M3 (`pscgen`, typed bindings, client-side defaults) and M4 (script host, packaging) follow v1 with their own plans.
