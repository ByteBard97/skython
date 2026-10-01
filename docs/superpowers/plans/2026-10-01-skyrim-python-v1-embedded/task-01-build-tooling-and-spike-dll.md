# Task 1: Build tooling and a spike DLL that loads

Adapted from: branch `plan-out-of-process`, task 1 (`task-01-build-tooling-and-a-spike-dll-that.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** (Fixed paths) and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before Task 3 starts** (Task 3's first step checks for the S1 PASS row). **S8 (Task 3) must pass before Tasks 18-32**; its fallback is the out-of-process plan on branch `plan-out-of-process`. S2, S9 and S4 (Tasks 4-6) gate the M1 plugin tasks (25-32); S7 (Task 9) gates Tasks 28-29; S5, S6 and S10 gate only the M2 plan and skyrim-dm's packaging. **On any FAIL of S1, S8, S2, S9 or S4, STOP and ask the owner.** Also STOP and ask the owner if: the game does not reach the plugin's log line within 10 minutes of launch, the Windows build box does not answer, or the game runtime is anything other than AE 1.6.1170.

> **Deploy safety:** the spike DLL opens an **unauthenticated** loopback port (45978) that can load saves and mutate quest aliases, and it is copied into the live Stock Game that also loads other mods. Run every spike on a throwaway save (make one with the console, `save spikebase`, and never save over a real playthrough while the spike DLL is deployed). If the owner has a cloned profile or a second Stock Game copy, deploy there instead. Nothing is removed from the game folder without the owner's sign-off on an itemized list (Step 5). Never `rm`.

**Files:**
- Create: `tools/remote_build.sh`, `tools/deploy.sh`, `tools/launch_game.sh`, `tools/env.sh`, `tools/wait_for_log.sh`, `tools/teardown_list.sh` (`tools/fetch_python.sh` already exists from Task 0)
- Create: `spikes/spike-dll/CMakeLists.txt`, `spikes/spike-dll/vcpkg.json`, `spikes/spike-dll/build.ps1`, `spikes/spike-dll/src/main.cpp`, `spikes/spike-dll/src/Net.cpp`, `spikes/spike-dll/src/Net.h`

**Interfaces:**
- Consumes (Task 0): `plugin/extern/CommonLibSSE-NG` (pinned, with the nested openvr headers), `third_party/pybind11/`, and, once `tools/fetch_python.sh` has run, `third_party/cpython/nuget/tools/{include,libs}`.
- Produces: `tools/remote_build.sh <cmake-source-dir-relative-to-repo> <dll-name>` → copies the built DLL to `out/<dll-name>`; it also syncs `third_party/pybind11` and `third_party/cpython/nuget/tools/{include,libs}` (the CPython headers and `python314.lib`, needed from Task 3 on) to `C:\dev\skyrim-python\third_party\...` on the box, and prints a note and syncs without them when they are absent; `ARCHIVE_ONLY=1` builds and lists the sync archive without touching the box; `tools/deploy.sh out/<dll-name> [ini-file]` (records every copied path in `out/deployed.txt`; a DLL whose name contains `Spike` needs `CONFIRM_THROWAWAY_SAVE=1`); `tools/launch_game.sh` (refuses a second launch; writes `~/.local/state/skyrim-python/launch.log` and touches `launch.stamp`); `tools/wait_for_log.sh <file> <regex> [seconds]` (bounded wait; exits 1 with the log tail on timeout; honours `SINCE=<file>` so a log from an earlier run cannot match); `tools/teardown_list.sh` (LISTS what was deployed; removes nothing); `source tools/env.sh` exports `SKYRIM_PYTHON_SESSION`, `SKYRIM_PYTHON_LOG` (the embedded interpreter's `python.log`), `SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`, `SKYRIM_PYTHON_STATE`. Task 3 extends `deploy.sh` with `--python`; Task 26 with `--package` and `--scripts DIR`.
- Produces for Tasks 2-9, in `spikes/spike-dll/src/main.cpp`: the include block (already contains `<algorithm> <atomic> <chrono> <cstdint> <format> <functional> <future> <memory> <mutex> <sstream> <string> <thread> <vector>`, so later tasks add **no** includes inside `namespace spike`), `spike::Log`, `spike::ParseInt(str, fallback)` (use it, never a bare `std::stoi`), `spike::Handle(line)`.

- [ ] **Step 1: Write the remote build script**

Assumptions, checked by the script's preflight instead of trusted (all verified against the real box on 2026-09-30): the Windows box's OpenSSH default shell is **Windows PowerShell 5.1**, not `cmd.exe`, so every remote command is PowerShell syntax (`New-Item -Force`, `Test-Path`, `Get-FileHash`; no `if not exist`, `cd /d`, `%COMSPEC%`, `&` chaining or `(exit 0)`); `tar` there is bsdtar (`C:\Windows\System32\tar.exe`). The archive is sent as a file with `scp` and checked by SHA-256 before `tar -xzf file`: piping it into `tar -xzf -` also round-trips byte-exact (tested, 5 MB random data), but an empty stdin makes tar exit 0 having extracted nothing, so the file-plus-checksum shape is safer. `scp` wants `C:/dev/...` paths (both `C:/...` and `/C:/...` work). A failing remote command's exit code does propagate through ssh (tested). Every ssh/scp call has a connect timeout, runs with `BatchMode` (no password prompt to hang on), and the build itself is bounded. The nested `openvr` submodule (CommonLibSSE-NG's `extern/openvr`) must be checked out (Task 0 Step 3) because CommonLib's CMake adds its headers; its `samples/` and `bin/` (~330 MB) are excluded from the sync. The CPython build inputs are new in this plan: of `third_party/cpython/` only `nuget/tools/include` and `nuget/tools/libs` are sent (appended to the archive in a second `tar -rf` call, so the folder-wide exclude does not apply to them); the runtime (`embed/`), the downloaded archives and the NuGet package's `Lib/` and executables stay on Linux, because the box builds and never runs Python.

```bash
mkdir -p tools
cat > tools/remote_build.sh <<'EOF'
#!/usr/bin/env bash
# Sync this repo to the Windows build box, build one CMake project there, copy the DLL back.
# Usage: tools/remote_build.sh <source-dir> <DllName.dll>
#   e.g. tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
# Env: FORCE_EXTERN=1 resends the CommonLibSSE-NG submodule; BUILD_TIMEOUT=seconds (default 3600);
#      REMOTE_ROOT='C:\dev\skyrim-python' (override only to test this script somewhere else);
#      ARCHIVE_ONLY=1 builds the sync archive, lists it and stops (no ssh; for checking what gets sent).
# The box's OpenSSH default shell is Windows PowerShell 5.1, so every remote command below is PowerShell.
# What is synced besides the sources: third_party/pybind11 (committed) and, when tools/fetch_python.sh
# has run, only third_party/cpython/nuget/tools/{include,libs} (the CPython headers and import library;
# Tasks 3 and 25 onward need them). The rest of third_party/cpython (archives, embed runtime, the
# NuGet package's Lib/ and exes) is never sent: the box builds, it does not run Python.
set -euo pipefail
SRC_DIR="${1:?usage: remote_build.sh <source-dir> <DllName.dll>}"; DLL="${2:?usage: remote_build.sh <source-dir> <DllName.dll>}"
WIN=geoff@192.168.0.211
SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
BUILD_TIMEOUT="${BUILD_TIMEOUT:-3600}"
REMOTE_ROOT="${REMOTE_ROOT:-C:\\dev\\skyrim-python}"      # backslash form, for PowerShell
REMOTE_FWD="${REMOTE_ROOT//\\//}"                         # forward-slash form, for scp (C:/dev/...)
wssh() { ssh "${SSH_OPTS[@]}" "$WIN" "$@"; }
die() { echo "remote_build: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ARCHIVE_ONLY="${ARCHIVE_ONLY:-0}"

if [ "$ARCHIVE_ONLY" != 1 ]; then
  # --- preflight: fail fast with a clear message instead of a hang or a confusing shell error
  psv="$(wssh 'Write-Output "PSV=$($PSVersionTable.PSVersion.Major)"' 2>&1 | tr -d '\r')" \
    || die "cannot ssh to $WIN within 10 s (is the Windows build box up? STOP and ask the owner)"
  case "$psv" in
    *PSV=[5-9]*) ;;
    *) die "the box's ssh shell is not Windows PowerShell (it answered: '$psv'). Every remote command here is PowerShell syntax. STOP and ask the owner." ;;
  esac
  wssh 'tar --version' 2>&1 | tr -d '\r' | head -1 | grep -qi bsdtar \
    || die "tar on the Windows box is not bsdtar (need C:\\Windows\\System32\\tar.exe)"
  wssh 'Get-Command powershell, cmake, git | Out-Null' >/dev/null 2>&1 || die "powershell, cmake or git is missing from the Windows box's PATH"
  wssh "New-Item -ItemType Directory -Force '$REMOTE_ROOT' | Out-Null" || die "could not create $REMOTE_ROOT"
fi

# --- sync: one tar.gz, copied with scp, checksum-verified, then extracted from the file.
# (Piping the archive into 'tar -xzf -' also round-trips byte-exact over this ssh, but an empty stdin
#  "succeeds" with rc 0 and extracts nothing, so a file plus a checksum is the safer shape.)
# The CommonLibSSE-NG submodule is large and rarely changes. It is sent when the box has no marker for the
# local submodule commit. The marker is written only AFTER a complete, error-free extraction.
EXTERN=plugin/extern/CommonLibSSE-NG
PIN="$(git -C "$EXTERN" rev-parse HEAD 2>/dev/null)" || die "$EXTERN is not checked out (Task 0 Step 3)"
[ -d "$EXTERN/extern/openvr/headers" ] || die "the nested openvr submodule is not checked out (git submodule update --init --recursive)"
MARKER="$REMOTE_ROOT\\plugin\\extern\\.synced-ok"
SEND_EXTERN=1
if [ "$ARCHIVE_ONLY" != 1 ] && [ "${FORCE_EXTERN:-0}" != 1 ]; then
  remote_pin="$(wssh "if (Test-Path '$MARKER') { (Get-Content '$MARKER' -Raw).Trim() }" 2>/dev/null | tr -d '\r\n ' || true)"
  [ "$remote_pin" = "$PIN" ] && SEND_EXTERN=0
fi
[ -f third_party/pybind11/include/pybind11/pybind11.h ] \
  || echo "remote_build: note: third_party/pybind11 is missing (Task 0 Step 2); targets that use pybind11 will not configure" >&2
NUGET=third_party/cpython/nuget/tools
EXTRA=()
if [ -f "$NUGET/include/Python.h" ] && [ -f "$NUGET/libs/python314.lib" ]; then
  EXTRA=("./$NUGET/include" "./$NUGET/libs")
else
  echo "remote_build: note: $NUGET/{include,libs} not present (run tools/fetch_python.sh); syncing without the CPython build inputs (needed from Task 3 on)" >&2
fi
# openvr's samples/ and bin/ are ~330 MB and unused by the build; CommonLib's tests/ are not needed either.
TAR_EXCL=(--exclude='./.git' --exclude='*/build' --exclude='./out' --exclude='./python/.venv' --exclude='./.sync.tgz'
          --exclude='./third_party/cpython'
          --exclude='*/extern/openvr/samples' --exclude='*/extern/openvr/bin' --exclude="./$EXTERN/tests")
[ "$SEND_EXTERN" = 0 ] && TAR_EXCL+=(--exclude='./plugin/extern')
TARF="$(mktemp --suffix=.tar)"   # never rm'd by this script; the OS clears /tmp
tar "${TAR_EXCL[@]}" -cf "$TARF" . || die "could not build the sync archive"
# Appended in a second call so the cpython exclude above does not apply to these two folders.
if [ "${#EXTRA[@]}" -gt 0 ]; then tar -rf "$TARF" "${EXTRA[@]}" || die "could not add the CPython build inputs"; fi
gzip -n -c "$TARF" > "$TARF.gz" || die "could not compress the sync archive"
ARCH="$TARF.gz"
if [ "$ARCHIVE_ONLY" = 1 ]; then
  echo "archive: $ARCH ($(du -h "$ARCH" | cut -f1)), send CommonLib: $SEND_EXTERN"
  tar -tzf "$ARCH" | sed 's#^\./##' | cut -d/ -f1-4 | sort -u
  exit 0
fi
LOCAL_SUM="$(sha256sum "$ARCH" | cut -d' ' -f1)"
scp "${SSH_OPTS[@]}" -q "$ARCH" "$WIN:$REMOTE_FWD/.sync.tgz" || die "scp of the sync archive failed"
REMOTE_SUM="$(wssh "(Get-FileHash '$REMOTE_ROOT\\.sync.tgz' -Algorithm SHA256).Hash" | tr -d '\r\n ' | tr 'A-F' 'a-f')"
[ "$REMOTE_SUM" = "$LOCAL_SUM" ] || die "archive checksum mismatch after scp (local $LOCAL_SUM, remote $REMOTE_SUM)"
wssh "tar -xzf '$REMOTE_ROOT\\.sync.tgz' -C '$REMOTE_ROOT'; exit \$LASTEXITCODE" || die "extraction on the Windows box failed"
if [ "$SEND_EXTERN" = 1 ]; then
  wssh "Set-Content -NoNewline -Path '$MARKER' -Value '$PIN'" || die "could not write the sync marker"
fi

# --- build (bounded)
WIN_SRC="$REMOTE_ROOT\\${SRC_DIR//\//\\}"
timeout "$BUILD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$WIN" "powershell -NoProfile -ExecutionPolicy Bypass -File '${WIN_SRC}\\build.ps1'" \
  || die "build failed or took longer than ${BUILD_TIMEOUT}s"

# --- copy the DLL back (scp wants forward slashes: C:/dev/...)
mkdir -p out
got=0
for rel in "$SRC_DIR/build/Release/$DLL" "$SRC_DIR/build/$DLL"; do
  if scp "${SSH_OPTS[@]}" -q "$WIN:$REMOTE_FWD/$rel" "out/${DLL}.part" 2>/dev/null; then got=1; break; fi
done
[ "$got" = 1 ] || die "could not scp $DLL from $REMOTE_FWD/$SRC_DIR/build/Release/"
[ "$(head -c2 "out/${DLL}.part")" = "MZ" ] || die "out/${DLL}.part is not a Windows binary"
mv "out/${DLL}.part" "out/${DLL}"
echo "built out/${DLL}"
EOF
chmod +x tools/remote_build.sh
bash -n tools/remote_build.sh
# What would be sent (no ssh). Before Task 0 Step 6 has run there is a note about the CPython inputs; after it,
# the list shows third_party/cpython/nuget/tools/include and .../libs and nothing else from third_party/cpython.
ARCHIVE_ONLY=1 tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll | grep -E '^archive|cpython|pybind11/' 
```
Expected: `bash -n` prints nothing; the listing shows `archive: /tmp/tmp.XXXX.tar.gz (<size>), send CommonLib: 1`, `third_party/cpython/nuget/tools/include`, `third_party/cpython/nuget/tools/libs` and `third_party/pybind11/...`, and no `embed`, `downloads` or `Lib` entries.

- [ ] **Step 2: Write deploy, launch, env, wait and teardown-list scripts**

```bash
cat > tools/env.sh <<'EOF'
# source tools/env.sh
export SIMPLESKYRIM_DATA="$HOME/Games/SimpleSkyrim/Stock Game/Data"
PFX="$HOME/.local/share/Steam/steamapps/compatdata/4190904831/pfx"
export SKSE_LOG_DIR="$PFX/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE"
export SKYRIM_PYTHON_SESSION="$SKSE_LOG_DIR/SkyrimPython/session.json"
export SKYRIM_PYTHON_LOG="$SKSE_LOG_DIR/SkyrimPython/python.log"
export SKYRIM_PYTHON_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/skyrim-python"
EOF

cat > tools/deploy.sh <<'EOF'
#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini]  -- copies into SimpleSkyrim's Data/SKSE/Plugins.
# Every copied path is appended to out/deployed.txt so tools/teardown_list.sh can list it later.
# A *Spike* DLL opens an unauthenticated port: it needs CONFIRM_THROWAWAY_SAVE=1 (you are on a throwaway save).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
[ -f "${1:-}" ] || { echo "deploy: no such file: ${1:-<none>}" >&2; exit 1; }
case "$(basename "$1")" in
  *Spike*)
    [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || {
      echo "deploy: $1 opens an unauthenticated port that can load saves and change quest aliases." >&2
      echo "deploy: use only with a throwaway save, then re-run with CONFIRM_THROWAWAY_SAVE=1." >&2
      exit 1; } ;;
esac
DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"
[ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
mkdir -p "$ROOT/out"
cp -v "$1" "$DEST/"
echo "$DEST/$(basename "$1")" >> "$ROOT/out/deployed.txt"
if [ $# -ge 2 ]; then
  cp -v "$2" "$DEST/$(basename "${1%.dll}").ini"
  echo "$DEST/$(basename "${1%.dll}").ini" >> "$ROOT/out/deployed.txt"
fi
EOF

cat > tools/launch_game.sh <<'EOF'
#!/usr/bin/env bash
# Launch SimpleSkyrim through GE-Proton with skse64_loader.exe (not MO2), in the background.
# Refuses a second launch. Steam should already be running (a warning is printed if it is not).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
mkdir -p "$SKYRIM_PYTHON_STATE"
if pgrep -x SkyrimSE.exe >/dev/null || pgrep -f skse64_loader.exe >/dev/null; then
  echo "launch_game: the game (or its loader) is already running; quit it first (console: qqq)" >&2
  exit 1
fi
pgrep -x steam >/dev/null || echo "launch_game: warning: Steam is not running; the launch may fail" >&2
STEAM_ROOT="$HOME/.local/share/Steam"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$STEAM_ROOT/steamapps/compatdata/4190904831"
export STEAM_COMPAT_MOUNTS="/mnt/games/SteamLibrary"
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
PROTON="$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/proton"
cd "$HOME/Games/SimpleSkyrim/Stock Game"
: > "$SKYRIM_PYTHON_STATE/launch.stamp"   # logs older than this stamp belong to an earlier run
nohup "$PROTON" waitforexitandrun "$(pwd)/skse64_loader.exe" > "$SKYRIM_PYTHON_STATE/launch.log" 2>&1 &
echo "launched pid $! (log: $SKYRIM_PYTHON_STATE/launch.log)"
EOF

cat > tools/wait_for_log.sh <<'EOF'
#!/usr/bin/env bash
# Usage: [SINCE=<file>] tools/wait_for_log.sh <logfile> <egrep-regex> [timeout-seconds=600]
# Bounded replacement for 'until grep ...; do sleep; done'. With SINCE set, the log must also be newer
# than that file (so a log left by an earlier run cannot satisfy the wait).
set -uo pipefail
f="${1:?logfile}"; pat="${2:?regex}"; limit="${3:-600}"
end=$(( $(date +%s) + limit ))
while :; do
  if [ -f "$f" ] && { [ -z "${SINCE:-}" ] || [ "$f" -nt "$SINCE" ]; } && grep -q -E -- "$pat" "$f"; then exit 0; fi
  if [ "$(date +%s)" -ge "$end" ]; then
    echo "wait_for_log: no match for /$pat/ in $f after ${limit}s" >&2
    if [ -f "$f" ]; then echo "--- last 30 lines of $f" >&2; tail -n 30 "$f" >&2; else echo "(the file does not exist)" >&2; fi
    exit 1
  fi
  sleep 2
done
EOF

cat > tools/teardown_list.sh <<'EOF'
#!/usr/bin/env bash
# LISTS what the spikes and the plugin put into the game install and the prefix. It removes nothing.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
echo "== copied into the game by tools/deploy.sh (out/deployed.txt) =="
if [ -f "$ROOT/out/deployed.txt" ]; then sort -u "$ROOT/out/deployed.txt" | while read -r p; do [ -e "$p" ] && ls -ld "$p"; done; else echo "(none recorded)"; fi
echo "== logs and session files written by the spikes and the plugin =="
ls -ld "$SKSE_LOG_DIR"/SkyrimPythonSpike*.log "$SKSE_LOG_DIR"/SkyrimPython.log "$SKSE_LOG_DIR"/SkyrimPython 2>/dev/null || echo "(none)"
echo "== throwaway saves (names used by the spikes) =="
SAVES="$(dirname "$SKSE_LOG_DIR")/Saves"
ls -l "$SAVES"/spikebase* "$SAVES"/spytest* "$SAVES"/s4test* 2>/dev/null || echo "(none found under $SAVES; look where your Stock Game keeps saves)"
echo
echo "Nothing was removed. Show this list to the owner. Only after the owner signs off on exactly these"
echo "paths, move each one with: gio trash <path>.  Never rm. Do not remove anything not on the approved list."
EOF
chmod +x tools/deploy.sh tools/launch_game.sh tools/wait_for_log.sh tools/teardown_list.sh
for f in tools/*.sh; do bash -n "$f"; done
```
Expected: `bash -n` prints nothing for all seven files (`fetch_python.sh` included).

- [ ] **Step 3: Write the spike DLL skeleton**

`spikes/spike-dll/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.21)
project(SkyrimPythonSpike VERSION 0.0.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
# Use the option names recorded in docs/PINS.md (Task 0); VR off.
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../plugin/extern/CommonLibSSE-NG commonlibsse-ng)
add_library(SkyrimPythonSpike SHARED src/main.cpp src/Net.cpp)
# NOMINMAX: windows.h's min/max macros break std::min/std::max. WIN32_LEAN_AND_MEAN keeps winsock2.h before windows.h.
target_compile_definitions(SkyrimPythonSpike PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(SkyrimPythonSpike PRIVATE CommonLibSSE::CommonLibSSE ws2_32)
```

`spikes/spike-dll/vcpkg.json`: exactly the three packages CommonLibSSE-NG's CMake looks up (`find_package(spdlog)`, `find_package(directxtk)`, `find_path(rapidcsv.h)`). Do **not** copy CommonLibSSE-NG's own `vcpkg.json`: its `version>=` constraints are rejected without a `builtin-baseline` ("was rejected because it uses version>= and does not have a builtin-baseline", reproduced on the box), and the older runtime-harness precedent (`rapidcsv` + `spdlog`) is too small for CommonLib 10.1.0, which also needs `directxtk`. With no baseline, vcpkg takes the versions in the box's `C:\vcpkg` tree (2026-08-24: spdlog 1.17.0, directxtk 2026-05-07, rapidcsv 8.99):
```json
{
  "name": "skyrim-python-spike",
  "version-string": "0.0.1",
  "dependencies": [ "spdlog", "rapidcsv", "directxtk" ]
}
```
Manifest mode needs network on the first build (vcpkg downloads sources; CommonLib's configure also fetches minhook from GitHub because `SKSE_SUPPORT_PATCH_SAFETY` defaults ON). vcpkg's build trees and downloads live under `C:\vcpkg` and the binary cache under `%LOCALAPPDATA%\vcpkg\archives`, not in the project directory; only `vcpkg_installed` is local.

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
// A handler that throws produces an "ERR exception: ..." reply; it never reaches std::terminate.
void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler);
}
```

`spikes/spike-dll/src/Net.cpp` (no CommonLib headers in this file):
```cpp
#include "Net.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <chrono>
#include <exception>
#include <thread>

namespace spike {
namespace {
void SendAll(SOCKET c, const std::string& s)
{
    std::size_t off = 0;
    while (off < s.size()) {
        const int n = send(c, s.data() + off, static_cast<int>(s.size() - off), 0);
        if (n <= 0) return;
        off += static_cast<std::size_t>(n);
    }
}
}

void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler)
{
    std::thread([port, handler] {
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
            Log("spike: WSAStartup failed");
            return;
        }
        SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (ls == INVALID_SOCKET) {
            Log("spike: socket() failed, WSA error " + std::to_string(WSAGetLastError()));
            return;
        }
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
        if (bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, 4) != 0) {
            Log("spike: bind/listen failed, WSA error " + std::to_string(WSAGetLastError()));
            closesocket(ls);
            return;
        }
        Log("spike: listening on 127.0.0.1:" + std::to_string(port));
        int acceptFailures = 0;
        for (;;) {
            SOCKET c = accept(ls, nullptr, nullptr);
            if (c == INVALID_SOCKET) {
                // Do not spin: back off, and give up if accept keeps failing.
                if (++acceptFailures >= 50) { Log("spike: accept keeps failing, listener stopping"); break; }
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                continue;
            }
            acceptFailures = 0;
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
                    std::string reply;
                    try {
                        reply = handler(line);
                    } catch (const std::exception& e) {
                        reply = std::string("ERR exception: ") + e.what();
                    } catch (...) {
                        reply = "ERR unknown exception";
                    }
                    reply += "\n";
                    SendAll(c, reply);
                }
            }
            closesocket(c);
            Log("spike: client disconnected");
        }
        closesocket(ls);
    }).detach();
}
}
```

`spikes/spike-dll/src/main.cpp` (skeleton; Tasks 3-9 add handlers to `Handle`). **All includes go at the top of the file, before `namespace spike`**; later tasks add their code inside `namespace spike { ... }` blocks only, never includes:
```cpp
#include "Net.h"

#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <format>
#include <functional>
#include <future>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

// windows.h (pulled in by RE/Skyrim.h) defines GetObject as a macro for GetObjectA, which turns every
// RE::BSScript::Variable::GetObject() call into "GetObjectA is not a member" (compile error C2039). Undo it
// in every translation unit that includes RE/Skyrim.h and calls GetObject().
#undef GetObject

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 0, 1, 0 },
    .Name = "SkyrimPythonSpike"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace spike {
void Log(const std::string& line) { spdlog::info("{}", line); }

// Never call std::stoi on client input: it throws, and an exception out of the listener thread kills the game.
int ParseInt(const std::string& s, int fallback)
{
    try {
        std::size_t used = 0;
        const int v = std::stoi(s, &used);
        return used == s.size() ? v : fallback;
    } catch (...) {
        return fallback;
    }
}

std::string Handle(const std::string& line)
{
    if (line.rfind("echo ", 0) == 0) return line.substr(5);
    // Tasks 3-9 add their commands here, before the fallback.
    return "ERR unknown command";
}
}

namespace {
void SetupLog()
{
    try {
        auto dir = SKSE::log::log_directory();
        if (!dir) return;  // Documents not resolvable: keep spdlog's default logger
        auto path = *dir;
        path /= "SkyrimPythonSpike.log"sv;
        auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path.string(), true);
        auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
        logger->set_level(spdlog::level::info);
        logger->flush_on(spdlog::level::info);
        spdlog::set_default_logger(std::move(logger));
    } catch (...) {
        // no log file; the spike still runs
    }
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
    SKSE::Init(skse, { .log = false });  // InitInfo aggregate (SKSE/API.h); the old Init(skse, bool) is [[deprecated]]
    SetupLog();
    spike::Log(std::format("SkyrimPythonSpike loading on Skyrim {}", REL::Module::get().version().string(".")));  // string() alone joins with '-' (1-6-1170-0)
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
```
(The spike uses port 45978 so it can never collide with the real plugin's 45977.) `std::format("{}", 42.0f)` prints `42`, not `42.000000`: any later task that needs a fixed-point float must write `{:.6f}`.

- [ ] **Step 4: Build, deploy, launch, check the log**

Make sure a throwaway save exists first (Step 0 of Task 6 makes `s4test`; for now you only need the game to reach the main menu). Run `tools/fetch_python.sh --check` first too: it must pass, so the first sync already carries the CPython build inputs Task 3 needs.

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
tools/launch_game.sh
source tools/env.sh
# Bounded wait (10 minutes). The script prints the log tail and exits 1 on timeout.
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "listening on 127.0.0.1:45978" 600
cat "$SKSE_LOG_DIR/SkyrimPythonSpike.log"
```
Expected: `SkyrimPythonSpike loading on Skyrim 1.6.1170.0`, `spike: listening on 127.0.0.1:45978`. **If the wait times out, STOP and ask the owner** (do not relaunch in a loop). **If the logged version is not 1.6.1170, STOP and ask the owner.** If the build fails in CommonLibSSE-NG's configure step, the missing dependency is named in the CMake error: add it to `spikes/spike-dll/vcpkg.json` and rebuild.

- [ ] **Step 5: Teardown procedure (reference; run at the end of M0, after Task 10)**

The spike DLL stays deployed for the whole of M0. When M0 is finished (or abandoned), run `tools/teardown_list.sh`. It removes nothing: it prints the deployed DLLs and ini, the embedded runtime folder Task 3 deployed, any wheels Task 10 deployed, the spike and Python logs, the compiled `SkyrimPython.pex` if Task 7 deployed one (append its path to `out/deployed.txt` when you copy it), and any throwaway saves. **Show that exact list to the owner and wait for sign-off on it.** Only then move each approved path with `gio trash <path>`, one at a time. Never `rm`. Do not remove anything that was not on the approved list.

- [ ] **Step 6: Commit**

```bash
git add tools spikes/spike-dll
git commit -m "chore(spike): remote build tooling and spike DLL skeleton"
```
