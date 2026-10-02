#!/usr/bin/env bash
# Sync this repo to the Windows build box, build one CMake project there, copy the DLL back.
# Usage: tools/remote_build.sh <source-dir> <DllName.dll>
#   e.g. tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
# Env: FORCE_EXTERN=1 resends the CommonLibSSE-NG submodule; BUILD_TIMEOUT=seconds (default 3600);
#      REMOTE_ROOT='C:\dev\skyrim-python' (override only to test this script somewhere else);
#      ARCHIVE_ONLY=1 builds the sync archive, lists it and stops (no ssh; for checking what gets sent).
# The box's OpenSSH default shell is Windows PowerShell 5.1, so every remote command below is PowerShell.
# The archive is extracted into REMOTE_ROOT (C:\dev\skyrim-python): the build checkout, the existing build area,
# never the game folder. Every ssh call is bounded: 60 s for a command, 600 s for an scp and for hashing and
# extracting the archive (CommonLib makes it large); the build step keeps its own BUILD_TIMEOUT.
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
SSH_CMD_TIMEOUT=60; SCP_TIMEOUT=600
wssh() { timeout "$SSH_CMD_TIMEOUT" ssh "${SSH_OPTS[@]}" "$WIN" "$@"; }
wssh_long() { timeout "$SCP_TIMEOUT" ssh "${SSH_OPTS[@]}" "$WIN" "$@"; }   # hash and extract a large archive
wscp() { timeout "$SCP_TIMEOUT" scp "${SSH_OPTS[@]}" -q "$@"; }
die() { echo "remote_build: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ARCHIVE_ONLY="${ARCHIVE_ONLY:-0}"

if [ "$ARCHIVE_ONLY" != 1 ]; then
  # --- preflight: fail fast with a clear message instead of a hang or a confusing shell error
  psv="$(wssh 'Write-Output "PSV=$($PSVersionTable.PSVersion.Major)"' 2>&1 | tr -d '\r')" \
    || die "cannot ssh to $WIN within ${SSH_CMD_TIMEOUT} s (is the Windows build box up? STOP and ask the owner)"
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
wscp "$ARCH" "$WIN:$REMOTE_FWD/.sync.tgz" || die "scp of the sync archive failed or took longer than ${SCP_TIMEOUT}s"
REMOTE_SUM="$(wssh_long "(Get-FileHash '$REMOTE_ROOT\\.sync.tgz' -Algorithm SHA256).Hash" | tr -d '\r\n ' | tr 'A-F' 'a-f')"
[ "$REMOTE_SUM" = "$LOCAL_SUM" ] || die "archive checksum mismatch after scp (local $LOCAL_SUM, remote $REMOTE_SUM)"
wssh_long "tar -xzf '$REMOTE_ROOT\\.sync.tgz' -C '$REMOTE_ROOT'; exit \$LASTEXITCODE" || die "extraction on the Windows box failed or took longer than ${SCP_TIMEOUT}s"
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
  if wscp "$WIN:$REMOTE_FWD/$rel" "out/${DLL}.part" 2>/dev/null; then got=1; break; fi
done
[ "$got" = 1 ] || die "could not scp $DLL from $REMOTE_FWD/$SRC_DIR/build/Release/"
[ "$(head -c2 "out/${DLL}.part")" = "MZ" ] || die "out/${DLL}.part is not a Windows binary"
mv "out/${DLL}.part" "out/${DLL}"
echo "built out/${DLL}"
