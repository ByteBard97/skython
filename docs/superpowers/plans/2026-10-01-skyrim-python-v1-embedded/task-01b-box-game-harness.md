# Task 1b: Game harness on the Windows box

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** (Fixed paths) and **Contracts**; they apply to this task. Steps use checkbox (`- [ ]`) syntax for tracking. New in this plan (no counterpart on branch `plan-out-of-process`).

**Why.** Tasks 1-3 ran the game on the owner's Linux desktop under GE-Proton. That steals the desktop's keyboard and mouse focus, and the owner needs that machine. From Task 3's remaining runs on, the M0 spikes (Tasks 3-10) run the game on the Windows build box instead (`geoff@192.168.0.211`, the same box that builds the DLLs). Everything is still driven from Linux: the tools below act on the box over ssh, and an ssh tunnel brings the spike ports to Linux's `127.0.0.1`, so `spikes/spike_client.py`, `spikes/py_send.py` and `spikes/py_echo_client.py` run unchanged.

> **Never on the Linux desktop:** no step of this task (or of Tasks 3-10 after it) launches the game on Linux, uses xdotool, or starts Linux Steam. Linux Steam logged in to the same account makes the box's Steam log off ("Logged In Elsewhere"); the game keeps running when that happens, but leave Linux Steam alone either way: it is the owner's.

> **Deploy safety (unchanged from Task 1):** the spike DLL opens an unauthenticated port that runs arbitrary Python after Task 3. Use only the box's throwaway save `spikebase` (made in Step 6), never save over another save, and remove nothing on the box without the owner's sign-off on `tools/box/box_teardown_list.sh`'s list. New files on the box go only under `C:\dev\skyrim-python-box`, `...\Data\SKSE\Plugins` (our DLLs, ini) and `...\Data\SKSE\Plugins\SkyrimPython`. Never `Remove-Item`, `del`, `Unregister-ScheduledTask`; never modify or remove `ChronicleLiveDirect` or any other existing task, file, ini or save.

**What is on the box (investigated 2026-10-01, `.superpowers/sdd/README/windows-box-investigation.md`):** `C:\ChronicleDev\Stock Game\SkyrimSE.exe` 1.6.1170.0 with SKSE 2.2.6; SteamStub DRM, so Steam (`C:\Steam`) must be logged on before `skse64_loader.exe` starts (a launch that raced the logon gave no game: the loader started 5 s before Steam's `Logged On` line). DevBench 1.15.1 (`devbench.dll`, an SKSE plugin) serves REST on the box's `127.0.0.1:8920` while the game runs: `GET /api/tools`, `POST /api/tool/<name>`. CrashLogger writes `crash-*.log` in the SKSE log folder. 236 saves exist; none is named `spikebase*`. Other SKSE plugins in that Stock Game: ChronicleBridge, CountenanceNeuralFaceSpike, CrashLogger, devbench, mfgfix, PapyrusUtil, po3_PapyrusExtender, RuntimeHarness.

**Files:**
- Create: `tools/box/box_env.sh`, `tools/box/box_setup.sh`, `tools/box/box_deploy.sh`, `tools/box/box_launch.sh`, `tools/box/box_stop.sh`, `tools/box/box_wait_log.sh`, `tools/box/box_tunnel.sh`, `tools/box/box_console.sh`, `tools/box/box_quit.sh`, `tools/box/box_teardown_list.sh`, `tools/box/test_box_tools.sh`
- Create (copied to `C:\dev\skyrim-python-box` by `box_setup.sh`): `tools/box/win/launch.ps1`, `setup_task.ps1`, `stamp.ps1`, `snapshot_lib.ps1`, `wait_log.ps1`, `console.ps1`, `stop.ps1`, `quit_check.ps1`, `place.ps1`, `list.ps1`
- On the box only: the scheduled task `SkyrimPythonGame`; the save `spikebase`.

**Interfaces:**
- Consumes: Task 1 (`out/SkyrimPythonSpike.dll` from `tools/remote_build.sh`, `$SKYRIM_PYTHON_STATE`), Task 0 (`third_party/cpython/embed`, `tools/fetch_python.sh --check`), Task 2 (`spikes/spike_client.py` on 45978), Task 3 (`out/SpikePyCore.dll`, `spikes/py_send.py`, `spikes/py_echo_client.py` on 45979).
- Produces (Tasks 3-10 use these through the substitution table below):
  - `source tools/box/box_env.sh`: `BOX`, `BOX_GAME_DIR='C:\ChronicleDev\Stock Game'`, `BOX_DATA`, `BOX_PLUGINS='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'`, `BOX_OWN` (`...\Plugins\SkyrimPython`), `BOX_MYGAMES`, `BOX_SKSE_LOG_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE'`, `BOX_SAVES_DIR`, `BOX_DIR='C:\dev\skyrim-python-box'`, `BOX_TASK=SkyrimPythonGame`; functions `bssh CMD` (60 s), `bssh_t SECONDS CMD`, `bscp ARGS` (600 s), `bps SECONDS SCRIPT.ps1 ARGS` (runs one of our box scripts), `b64 TEXT`. All calls carry `timeout`, `ConnectTimeout=10` and `BatchMode=yes`; remote commands are PowerShell 5.1.
  - `tools/box/box_setup.sh` (re-runnable): installs the `.ps1` files after the box's PowerShell parser accepts each one; registers `SkyrimPythonGame` once (prints `REGISTERED ...` or `EXISTS ...`).
  - `tools/box/box_deploy.sh out/<Dll>.dll [ini] | --python [out/<Core>.dll] | --site-packages DIR | --package | --scripts DIR | --file LOCAL REL` → records Windows paths in `out/box-deployed.txt`; refuses a target that exists and is not recorded there; refuses while the game runs; spike DLLs need `CONFIRM_THROWAWAY_SAVE=1`.
  - `tools/box/box_launch.sh`: refuses if the game runs; writes the launch stamp on the box (box clock) and a snapshot of the SKSE log folder; copies the stamp to `$SKYRIM_PYTHON_STATE/box-launch.stamp`; runs the task; waits (`LAUNCH_WAIT`, 240 s) for `launch.ps1`'s result; prints `launched: ok pid=<n>` or fails.
  - `tools/box/box_wait_log.sh <log name> <regex> [seconds=600]`: `<log name>` relative to the box's SKSE log folder; only text written since the last launch stamp counts; prints `MATCH <line>` (exit 0) or `TIMEOUT ...` plus the tail (exit 1).
  - `tools/box/box_tunnel.sh up [--reverse PORT]... | down | status`: Linux `127.0.0.1:45978` and `:45979` → the box's; `--reverse PORT` maps the box's `127.0.0.1:PORT` to Linux's.
  - `tools/box/box_console.sh "<console command>" | --tool NAME [json] | --list`: DevBench. `save <name>` and `load <name>` typed as console commands are rerouted by DevBench to its `game` tool (`{"redirected":"game"}`); running them raw would deadlock the engine.
  - `tools/box/box_quit.sh`: console `qqq`, then `quit_check.ps1`: prints `EXITED`/`STILL_RUNNING`, `NO_NEW_CRASH_LOG`/`NEW_CRASH_LOGS ...`, and `CLEAN_EXIT` (exit 0) or `NOT_CLEAN` (exit 1).
  - `tools/box/box_stop.sh`: `Stop-Process -Name SkyrimSE,skse64_loader -Force`, then a 20 s check (`GONE`, `NOTHING_RUNNING`, or `STILL_RUNNING`, exit 1).
  - `tools/box/box_teardown_list.sh`: lists, removes nothing.

- [ ] **Step 1: Write the offline test and see it fail**

`tools/box/test_box_tools.sh` checks what can be checked without the box: every script exists and parses, no `ssh`/`scp` call bypasses the wrappers in `box_env.sh`, no destructive verb appears in any script, `Stop-Process` is used only by the two game names, and the box paths are the investigated ones.

```bash
#!/usr/bin/env bash
# Offline checks for the Windows-box game harness (no ssh, no game). Exit 0 = all pass.
# 1. every tools/box/*.sh exists and parses (bash -n);
# 2. no ssh/scp call outside box_env.sh's wrappers (so every call has a timeout and BatchMode);
# 3. no destructive verb in any script that acts on the box (Remove-Item, Unregister-ScheduledTask, rm, del, ...);
# 4. Stop-Process only by the two game process names;
# 5. the box paths in box_env.sh are the ones the investigation verified.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
B="$ROOT/tools/box"
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }

SH=(box_env.sh box_deploy.sh box_launch.sh box_stop.sh box_wait_log.sh box_tunnel.sh box_console.sh
    box_quit.sh box_setup.sh box_teardown_list.sh)
PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1)

for f in "${SH[@]}"; do
  if [ ! -f "$B/$f" ]; then bad "missing tools/box/$f"; continue; fi
  if bash -n "$B/$f" 2>/dev/null; then ok "bash -n $f"; else bad "bash -n $f"; fi
done
for f in "${PS[@]}"; do [ -f "$B/win/$f" ] && ok "present win/$f" || bad "missing tools/box/win/$f"; done

# 2. raw ssh/scp only inside box_env.sh (the wrappers bssh/bscp/bssh_long carry timeout + BatchMode)
raw="$(grep -HnE '(^\s*|[;&|({]\s*|\$\(\s*|timeout +[^ ]+ +|then +|do +)(ssh|scp) ' "$B"/*.sh 2>/dev/null | grep -v '^[^:]*/box_env.sh:' | grep -v '^[^:]*/test_box_tools.sh:' | grep -vE ':[0-9]+:\s*#' )"
if [ -z "$raw" ]; then ok "no raw ssh/scp outside box_env.sh"; else bad "raw ssh/scp outside box_env.sh:"; echo "$raw"; fi
if grep -q 'BatchMode=yes' "$B/box_env.sh" 2>/dev/null && grep -q 'ConnectTimeout=' "$B/box_env.sh" 2>/dev/null \
   && grep -qE 'timeout "\$' "$B/box_env.sh" 2>/dev/null; then ok "wrappers use BatchMode, ConnectTimeout and timeout"
else bad "box_env.sh wrappers lack BatchMode/ConnectTimeout/timeout"; fi

# 3. destructive verbs (code lines only; comments may mention them to forbid them)
destr="$(grep -HnEi '(^|[^-a-z])(Remove-Item|Unregister-ScheduledTask|rmdir|rd /s|del |erase |Clear-Content|git clean|rm -|rm ")' \
        "$B"/*.sh "$B"/win/*.ps1 2>/dev/null | grep -v '/test_box_tools.sh:' | grep -vE ':[0-9]+:\s*#')"
if [ -z "$destr" ]; then ok "no destructive verbs"; else bad "destructive verbs:"; echo "$destr"; fi

# 4. Stop-Process only by name SkyrimSE / skse64_loader
sp="$(grep -nh 'Stop-Process' "$B"/win/*.ps1 2>/dev/null | grep -vE '^\s*[0-9]+:\s*#')"
if [ -n "$sp" ] && ! echo "$sp" | grep -vq -- '-Name SkyrimSE,skse64_loader'; then ok "Stop-Process only by the game names"
else bad "Stop-Process used otherwise:"; echo "$sp"; fi

# 5. box paths
if [ -f "$B/box_env.sh" ]; then
  # shellcheck source=/dev/null
  source "$B/box_env.sh"
  [ "${BOX_GAME_DIR:-}" = 'C:\ChronicleDev\Stock Game' ] && ok "BOX_GAME_DIR" || bad "BOX_GAME_DIR=${BOX_GAME_DIR:-}"
  [ "${BOX_PLUGINS:-}" = 'C:\ChronicleDev\Stock Game\Data\SKSE\Plugins' ] && ok "BOX_PLUGINS" || bad "BOX_PLUGINS=${BOX_PLUGINS:-}"
  [ "${BOX_DIR:-}" = 'C:\dev\skyrim-python-box' ] && ok "BOX_DIR" || bad "BOX_DIR=${BOX_DIR:-}"
  [ "${BOX_SKSE_LOG_DIR:-}" = 'C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE' ] && ok "BOX_SKSE_LOG_DIR" || bad "BOX_SKSE_LOG_DIR=${BOX_SKSE_LOG_DIR:-}"
  [ "${BOX_TASK:-}" = SkyrimPythonGame ] && ok "BOX_TASK" || bad "BOX_TASK=${BOX_TASK:-}"
  [ "$(b64 'a"b c')" = "YSJiIGM=" ] && ok "b64 helper" || bad "b64 helper"
fi

[ "$fail" = 0 ] && echo "BOX TOOLS OFFLINE PASS" || echo "BOX TOOLS OFFLINE FAIL"
exit "$fail"
```

Run: `chmod +x tools/box/test_box_tools.sh && tools/box/test_box_tools.sh; echo rc=$?`
Expected: `FAIL missing tools/box/...` for the ten shell scripts and nine `.ps1` files (`snapshot_lib.ps1` is a library, not listed), `BOX TOOLS OFFLINE FAIL`, `rc=1`.

- [ ] **Step 2: Write `box_env.sh` and the Linux-side tools**

`tools/box/box_env.sh`:
```bash
# source tools/box/box_env.sh  -- the Windows box game harness: paths and the only ssh/scp wrappers.
# The box's OpenSSH default shell is Windows PowerShell 5.1, so every remote command is PowerShell.
# Every ssh/scp call goes through bssh/bssh_t/bscp/bps (timeout + ConnectTimeout + BatchMode), or through
# btunnel_* for the port-forwarding tunnel. Nothing here deletes anything.
BOX="${BOX:-geoff@192.168.0.211}"
BOX_SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
BOX_GAME_DIR='C:\ChronicleDev\Stock Game'
BOX_DATA='C:\ChronicleDev\Stock Game\Data'
BOX_PLUGINS='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'
BOX_OWN='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\SkyrimPython'
BOX_MYGAMES='C:\Users\geoff\Documents\My Games\Skyrim Special Edition'
BOX_SKSE_LOG_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE'
BOX_SAVES_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\Saves'
BOX_DIR='C:\dev\skyrim-python-box'
BOX_DIR_FWD='C:/dev/skyrim-python-box'          # scp wants forward slashes
BOX_TASK=SkyrimPythonGame
BOX_DEVBENCH_URL='http://127.0.0.1:8920'
BOX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOX_DEPLOYED="$BOX_ROOT/out/box-deployed.txt"
SKYRIM_PYTHON_STATE="${SKYRIM_PYTHON_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/skyrim-python}"
BOX_TUNNEL_SOCK="$SKYRIM_PYTHON_STATE/box-tunnel.sock"
export BOX BOX_GAME_DIR BOX_DATA BOX_PLUGINS BOX_OWN BOX_MYGAMES BOX_SKSE_LOG_DIR BOX_SAVES_DIR BOX_DIR BOX_TASK SKYRIM_PYTHON_STATE

# bssh_t <seconds> <powershell command>: one bounded remote command.
bssh_t() { local t="$1"; shift; timeout "$t" ssh "${BOX_SSH_OPTS[@]}" "$BOX" "$@"; }
# bssh <powershell command>: bounded at 60 s.
bssh() { bssh_t 60 "$@"; }
# bscp <scp args...>: bounded at 600 s.
bscp() { timeout 600 scp "${BOX_SSH_OPTS[@]}" -q "$@"; }
# bps <seconds> <script.ps1> [args...]: run one of our scripts in C:\dev\skyrim-python-box (args must not contain
# quotes or spaces: pass text through b64).
bps() { local t="$1" s="$2"; shift 2; bssh_t "$t" "powershell -NoProfile -ExecutionPolicy Bypass -File $BOX_DIR\\$s $*"; }
# b64 <text>: base64 of the UTF-8 text, one line (decoded on the box by our scripts).
b64() { printf '%s' "$1" | base64 -w0; }
box_die() { echo "box: $*" >&2; exit 1; }

# The tunnel: one background ssh with a control socket. The connect itself is bounded (timeout); with -f the
# daemonised child outlives the timeout's watched process by design.
btunnel_start() { timeout 30 ssh "${BOX_SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -f -N -M -S "$BOX_TUNNEL_SOCK" "$@" "$BOX"; }
btunnel_ctl() { timeout 15 ssh -S "$BOX_TUNNEL_SOCK" -O "$1" "$BOX"; }
```

`tools/box/box_setup.sh`:
```bash
#!/usr/bin/env bash
# One-time (re-runnable) setup: copy tools/box/win/*.ps1 to C:\dev\skyrim-python-box, check each parses as
# PowerShell on the box before it is put in place, then register the scheduled task SkyrimPythonGame
# (setup_task.ps1; an existing task is left as it is). Touches nothing outside C:\dev\skyrim-python-box
# except creating that one new task.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
psv="$(bssh 'Write-Output "PSV=$($PSVersionTable.PSVersion.Major)"' | tr -d '\r')" || box_die "cannot ssh to $BOX (STOP and ask the owner)"
case "$psv" in *PSV=5*) ;; *) box_die "the box's ssh shell is not Windows PowerShell 5 (answered '$psv')" ;; esac
bssh "New-Item -ItemType Directory -Force '$BOX_DIR\\incoming','$BOX_DIR\\stage' | Out-Null" || box_die "could not create $BOX_DIR"
bscp "$BOX_ROOT"/tools/box/win/*.ps1 "$BOX:$BOX_DIR_FWD/incoming/" || box_die "scp of the scripts failed"
# Parse every script with PowerShell's own parser; only then copy it into place.
bssh "\$bad = 0; Get-ChildItem '$BOX_DIR\\incoming' -Filter *.ps1 | ForEach-Object { \$e = \$null; [void][System.Management.Automation.Language.Parser]::ParseFile(\$_.FullName, [ref]\$null, [ref]\$e); if (\$e.Count) { \$bad++; 'PARSE_ERROR ' + \$_.Name + ': ' + (\$e | % { \$_.Message + ' line ' + \$_.Extent.StartLineNumber }) -join '; ' } else { 'parsed ' + \$_.Name } }; if (\$bad) { exit 1 }; Copy-Item '$BOX_DIR\\incoming\\*.ps1' '$BOX_DIR' -Force; 'INSTALLED'" \
  | tr -d '\r' || box_die "a script does not parse; nothing was installed"
bps 60 setup_task.ps1 | tr -d '\r'
```

`tools/box/box_deploy.sh`. Files are staged into `C:\dev\skyrim-python-box\stage` first (scp and paths with spaces do not mix well; the staging path has none) and then copied into the game by `place.ps1`, which checks the SHA-256 of each file it places. A folder goes over as one tar and is merged in with `robocopy /E` (never `/MIR`/`/PURGE`). The stage folder only grows; it is ours and listed by the teardown list.
```bash
#!/usr/bin/env bash
# Deploy into the box's game (C:\ChronicleDev\Stock Game\Data\SKSE\Plugins), the box twin of tools/deploy.sh.
# Usage:
#   tools/box/box_deploy.sh out/<Dll>.dll [path/to/ini]      -> Plugins\<Dll>.dll [+ Plugins\<Dll>.ini]
#                                                               (SkyrimPython.dll also deploys out/SkyrimPythonCore.dll
#                                                                to Plugins\SkyrimPython\, which must not be older)
#   tools/box/box_deploy.sh --python [out/<Core>.dll]        -> Plugins\SkyrimPython\python\ (third_party/cpython/embed)
#                                                               [+ the core DLL into Plugins\SkyrimPython\]
#   tools/box/box_deploy.sh --site-packages DIR              -> Plugins\SkyrimPython\site-packages\<entries>
#   tools/box/box_deploy.sh --package                        -> Plugins\SkyrimPython\lib\skyrim\ (python/src/skyrim)
#   tools/box/box_deploy.sh --scripts DIR                    -> Plugins\SkyrimPython\scripts\<entries>
#   tools/box/box_deploy.sh --file LOCAL REL                 -> Plugins\SkyrimPython\REL (one file: s10_check.py, markers)
# Every target is recorded in out/box-deployed.txt. A target that exists on the box and is not recorded there
# was not deployed by this tool: the deploy refuses (it never overwrites what it did not put there).
# A *Spike* DLL needs CONFIRM_THROWAWAY_SAVE=1. Refuses while the game runs (DLLs are locked).
# Files are staged under C:\dev\skyrim-python-box\stage (no spaces for scp), then copied into place on the box.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$BOX_ROOT/out/box-stage"; touch "$BOX_DEPLOYED"

recorded() { grep -qxF -- "$1" "$BOX_DEPLOYED"; }
rec() { recorded "$1" || echo "$1" >> "$BOX_DEPLOYED"; }
check_target() {  # refuse a target that exists on the box and was not deployed by this tool
  recorded "$1" && return 0
  local r; r="$(bps 60 place.ps1 -Mode exists -DestB64 "$(b64 "$1")" | tr -d '\r')"
  case "$r" in
    ABSENT) return 0 ;;
    EXISTS) box_die "$1 exists on the box but is not in out/box-deployed.txt; not overwriting it. Ask the owner." ;;
    *) box_die "could not check $1: $r" ;;
  esac
}
stage_id() { echo "$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"; }
put_file() {  # put_file <local file> <windows dest>
  local src="$1" dest="$2" id r sum
  [ -f "$src" ] || box_die "no such file: $src"
  check_target "$dest"
  id="$(stage_id)-$(basename "$src")"
  bscp "$src" "$BOX:$BOX_DIR_FWD/stage/$id" || box_die "scp $src failed"
  r="$(bps 120 place.ps1 -Mode place -Kind file -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" | tr -d '\r')"
  sum="$(sha256sum "$src" | cut -d' ' -f1)"
  [ "$r" = "PLACED $sum" ] || box_die "placing $dest: '$r' (expected PLACED $sum)"
  rec "$dest"; echo "deployed $src -> $dest"
}
put_tree() {  # put_tree <local dir> <windows dest dir>: merge the tree (no __pycache__) into dest
  local src="$1" dest="$2" id tarf r
  [ -d "$src" ] || box_die "no such directory: $src"
  check_target "$dest"
  id="$(stage_id)-$(basename "$src").tar"; tarf="$BOX_ROOT/out/box-stage/$id"
  tar -C "$src" --exclude=__pycache__ -cf "$tarf" .
  bscp "$tarf" "$BOX:$BOX_DIR_FWD/stage/$id" || box_die "scp $tarf failed"
  r="$(bps 600 place.ps1 -Mode place -Kind tar -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" | tr -d '\r')"
  case "$r" in PLACED*) ;; *) box_die "placing $dest: '$r'" ;; esac
  rec "$dest"; echo "deployed $src/ -> $dest ($r)"
}
put_entries() {  # put_entries <local dir> <windows dest dir>: each top-level entry is its own recorded target
  local src="$1" dest="$2" e
  [ -d "$src" ] || box_die "no such directory: $src"
  for e in "$src"/* "$src"/.[!.]*; do
    [ -e "$e" ] || continue
    [ "$(basename "$e")" = __pycache__ ] && continue
    if [ -d "$e" ]; then put_tree "$e" "$dest\\$(basename "$e")"; else put_file "$e" "$dest\\$(basename "$e")"; fi
  done
}
spike_ok() { case "$(basename "$1")" in *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || box_die "$1 is spike code (unauthenticated port); use a throwaway save and re-run with CONFIRM_THROWAWAY_SAVE=1" ;; esac; }

[ $# -ge 1 ] || box_die "usage: see the header of $0"
st="$(bssh 'if (Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue) { "RUNNING" } else { "IDLE" }' | tr -d '\r')" \
  || box_die "cannot ssh to $BOX (STOP and ask the owner)"
[ "$st" = IDLE ] || box_die "the game is running on the box; quit it first (tools/box/box_quit.sh)"

case "$1" in
  --python)
    "$BOX_ROOT/tools/fetch_python.sh" --check >/dev/null || box_die "third_party/cpython does not match docs/PINS.md"
    [ -f "$BOX_ROOT/third_party/cpython/embed/python314.dll" ] && [ -f "$BOX_ROOT/third_party/cpython/embed/LICENSE.txt" ] || box_die "embed runtime incomplete"
    if [ $# -ge 2 ]; then spike_ok "$2"; [ -f "$2" ] || box_die "no such file: $2"; fi
    put_tree "$BOX_ROOT/third_party/cpython/embed" "$BOX_OWN\\python"
    [ $# -ge 2 ] && put_file "$2" "$BOX_OWN\\$(basename "$2")"
    ;;
  --site-packages)
    SP="${2:?usage: box_deploy.sh --site-packages DIR}"; [ -d "$SP" ] || box_die "no such directory: $SP"
    have="$(bps 60 place.ps1 -Mode list -DestB64 "$(b64 "$BOX_OWN\\site-packages")" | tr -d '\r' | sort)"
    stale="$(comm -23 <(echo "$have" | sed '/^$/d') <(ls -A "$SP" | sort))"
    [ -z "$stale" ] || { echo "$stale" >&2; box_die "the box's site-packages holds the entries above that $SP lacks; ask the owner (nothing is deleted)"; }
    put_entries "$SP" "$BOX_OWN\\site-packages"
    ;;
  --package)
    [ -f "$BOX_ROOT/python/src/skyrim/__init__.py" ] || box_die "python/src/skyrim is missing"
    put_tree "$BOX_ROOT/python/src/skyrim" "$BOX_OWN\\lib\\skyrim"
    ;;
  --scripts)
    put_entries "${2:?usage: box_deploy.sh --scripts DIR}" "$BOX_OWN\\scripts"
    ;;
  --file)
    REL="${3:?usage: box_deploy.sh --file LOCAL REL}"
    case "$REL" in *..*) box_die "REL must not contain .." ;; esac
    put_file "$2" "$BOX_OWN\\${REL//\//\\}"
    ;;
  -*) box_die "unknown option $1" ;;
  *)
    DLL="$1"; [ -f "$DLL" ] || box_die "no such file: $DLL"; spike_ok "$DLL"
    base="$(basename "$DLL")"
    if [ "$base" = SkyrimPython.dll ]; then
      CORE="$(dirname "$DLL")/SkyrimPythonCore.dll"
      [ -f "$CORE" ] || box_die "$CORE missing: run tools/remote_build.sh plugin SkyrimPythonCore.dll"
      [ "$DLL" -nt "$CORE" ] && box_die "$CORE is older than $DLL: rebuild it"
    fi
    put_file "$DLL" "$BOX_PLUGINS\\$base"
    [ "$base" = SkyrimPython.dll ] && put_file "$CORE" "$BOX_OWN\\SkyrimPythonCore.dll"
    [ $# -ge 2 ] && put_file "$2" "$BOX_PLUGINS\\${base%.dll}.ini"
    ;;
esac
exit 0
```

`tools/box/box_launch.sh`:
```bash
#!/usr/bin/env bash
# Launch the game on the box through OUR scheduled task SkyrimPythonGame (launch.ps1). Refuses when the game or
# loader already runs. Writes the launch stamp on the box (box clock; stamp.ps1 also snapshots the SKSE log
# folder so box_wait_log.sh and box_quit.sh see only this launch), copies it to
# $SKYRIM_PYTHON_STATE/box-launch.stamp, then waits (bounded, LAUNCH_WAIT=240 s) for launch.ps1's result.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$SKYRIM_PYTHON_STATE"
out="$(bps 60 stamp.ps1 | tr -d '\r')" || box_die "refused: $out"
stamp="$(echo "$out" | sed -n 's/^STAMP \([^ ]*\).*/\1/p')"; [ -n "$stamp" ] || box_die "no stamp: $out"
echo "$stamp" > "$SKYRIM_PYTHON_STATE/box-launch.stamp"
echo "box launch stamp $stamp"
bssh "Start-ScheduledTask -TaskName $BOX_TASK" || box_die "could not start the scheduled task $BOX_TASK (run tools/box/box_setup.sh once)"
end=$(( $(date +%s) + ${LAUNCH_WAIT:-240} ))
while :; do
  res="$(bssh "Get-Content '$BOX_DIR\\launch.result' -Raw -ErrorAction SilentlyContinue" | tr -d '\r' || true)"
  case "$res" in "$stamp "*) break ;; esac
  if [ "$(date +%s)" -ge "$end" ]; then
    echo "box_launch: no result from launch.ps1 after ${LAUNCH_WAIT:-240}s; its log:" >&2
    bssh "Get-Content '$BOX_DIR\\launch.log' -Tail 20" >&2 || true; exit 1
  fi
  sleep 3
done
bssh "Get-Content '$BOX_DIR\\launch.log' | Select-String -SimpleMatch '[$stamp]' | % Line" | tr -d '\r'
case "$res" in "$stamp ok"*) echo "launched: ${res#"$stamp "}" ;; *) box_die "launch failed: ${res#"$stamp "}" ;; esac
```

`tools/box/box_stop.sh`:
```bash
#!/usr/bin/env bash
# Force-stop the game on the box: Stop-Process -Name SkyrimSE,skse64_loader (by name only), bounded 20 s check.
# Use box_quit.sh (console qqq) for a clean exit; this is for a hung game.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
bps 60 stop.ps1 | tr -d '\r'
```

`tools/box/box_wait_log.sh`:
```bash
#!/usr/bin/env bash
# Usage: tools/box/box_wait_log.sh <log name> <regex> [seconds=600]
#   <log name> is relative to the box's SKSE log folder (C:\Users\geoff\Documents\My Games\Skyrim Special
#   Edition\SKSE), e.g. SkyrimPythonSpike.log or SkyrimPython/python.log. The regex is .NET (-match); ERE
#   patterns used in this plan mean the same. Only text written after the last box_launch.sh stamp counts.
# Exit 0 and "MATCH <line>" on a match; exit 1 with the tail of this launch's text on timeout.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
name="${1:?log name}"; pat="${2:?regex}"; secs="${3:-600}"
out="$(bps $((secs + 60)) wait_log.ps1 -NameB64 "$(b64 "$name")" -PatB64 "$(b64 "$pat")" -Seconds "$secs" | tr -d '\r')" && rc=0 || rc=$?
echo "$out"
exit "$rc"
```

`tools/box/box_tunnel.sh`:
```bash
#!/usr/bin/env bash
# Usage: tools/box/box_tunnel.sh up [--reverse PORT]... | down | status
#   up: one background ssh (control socket $SKYRIM_PYTHON_STATE/box-tunnel.sock) forwarding Linux
#       127.0.0.1:45978 and :45979 to the box's 127.0.0.1:45978/:45979 (spike harness, S8 echo server), and
#       for each --reverse PORT the box's 127.0.0.1:PORT back to Linux 127.0.0.1:PORT (S10's HTTP server).
#       Fails loudly (ExitOnForwardFailure) if a local port is taken (e.g. the Linux game is running).
#   Note: through the tunnel a missing listener on the box shows as "server closed the connection"
#   (spike_client.py exit 1), not as ConnectionRefusedError.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$SKYRIM_PYTHON_STATE"
case "${1:-}" in
  up)
    shift
    if btunnel_ctl check 2>/dev/null; then echo "tunnel already up"; exit 0; fi
    fw=(-L 127.0.0.1:45978:127.0.0.1:45978 -L 127.0.0.1:45979:127.0.0.1:45979)
    while [ $# -gt 0 ]; do
      case "$1" in
        --reverse) p="${2:?--reverse PORT}"; fw+=(-R "127.0.0.1:$p:127.0.0.1:$p"); shift 2 ;;
        *) box_die "unknown option $1" ;;
      esac
    done
    btunnel_start "${fw[@]}" || box_die "tunnel did not come up (local port in use, or the box is unreachable)"
    btunnel_ctl check && echo "tunnel up: ${fw[*]}" ;;
  down)
    if btunnel_ctl check 2>/dev/null; then btunnel_ctl exit; echo "tunnel down"; else echo "tunnel not running"; fi ;;
  status)
    btunnel_ctl check 2>&1 || true ;;
  *) box_die "usage: box_tunnel.sh up [--reverse PORT]... | down | status" ;;
esac
```

`tools/box/box_console.sh`:
```bash
#!/usr/bin/env bash
# Run a game console command on the box through DevBench (in-game REST server on the box's 127.0.0.1:8920).
#   tools/box/box_console.sh "<console command>"     POST /api/tool/console {"command": "<console command>"}
#   tools/box/box_console.sh --tool NAME ['<json>']   POST /api/tool/NAME with that body (default {})
#   tools/box/box_console.sh --list                   GET /api/tools (DevBench's tool list and schemas)
# Prints DevBench's reply; exit 1 on an HTTP or connection error. DevBench answers only while the game runs.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
case "${1:-}" in
  --list) bps 60 console.ps1 -List | tr -d '\r' ;;
  --tool) tool="${2:?tool name}"; body="${3:-{\}}"
          bps 60 console.ps1 -Tool "$tool" -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
  "") box_die "usage: box_console.sh \"<console command>\" | --tool NAME [json] | --list" ;;
  *) body="$(python3 -c 'import json, sys; print(json.dumps({"command": sys.argv[1]}))' "$1")"
     bps 60 console.ps1 -Tool console -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
esac
```

`tools/box/box_quit.sh`:
```bash
#!/usr/bin/env bash
# Clean exit on the box: console qqq through DevBench, then a bounded wait (QUIT_WAIT=120 s) for SkyrimSE to end
# and a check that no crash-*.log appeared in the box's SKSE log folder since the launch stamp.
# Exit 0 only for CLEAN_EXIT. DevBench may drop the HTTP reply to qqq because the game is exiting: not an error.
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
"$(dirname "$0")/box_console.sh" qqq || echo "(no reply to qqq; expected while the game exits)"
bps $(( ${QUIT_WAIT:-120} + 60 )) quit_check.ps1 -Seconds "${QUIT_WAIT:-120}" | tr -d '\r'
exit "${PIPESTATUS[0]}"
```

`tools/box/box_teardown_list.sh`:
```bash
#!/usr/bin/env bash
# LISTS what the harness put on the box (deployed paths from out/box-deployed.txt, C:\dev\skyrim-python-box,
# the scheduled task SkyrimPythonGame, spike/plugin logs, throwaway saves). It removes nothing.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
paths=""; [ -f "$BOX_DEPLOYED" ] && paths="$(cat "$BOX_DEPLOYED")"
if [ -n "$paths" ]; then bps 120 list.ps1 -PathsB64 "$(b64 "$paths")"; else bps 120 list.ps1; fi | tr -d "\r"
```

- [ ] **Step 3: Write the box scripts**

`tools/box/win/launch.ps1` (the scheduled task's action). Steam's logon is detected by a **new** `[Logged On, ...] RecvMsgClientLogOnResponse() : processing complete` line in `C:\Steam\logs\connection_log.txt` (compared with the last such line before Steam was started, so a rotated log still works), not by a fixed sleep:
```powershell
# launch.ps1: run by the scheduled task SkyrimPythonGame (interactive, as geoff, on demand only).
# 1. refuse if the game or loader runs, or if the Stock Game exe is not 1.6.1170.0;
# 2. Steam: start it with -silent if it is not running, then wait (bounded, 120 s) for a NEW
#    "[Logged On ...] RecvMsgClientLogOnResponse() : processing complete" line in connection_log.txt
#    (if Steam already runs and its latest state line is "Logged On", no wait);
# 3. start skse64_loader.exe from the game folder; if no SkyrimSE process 15 s later, retry once.
# Every step goes to launch.log; the outcome goes to launch.result as "<launch stamp> ok|fail ...".
$ErrorActionPreference = 'Continue'
$box  = 'C:\dev\skyrim-python-box'
$game = 'C:\ChronicleDev\Stock Game'
$conn = 'C:\Steam\logs\connection_log.txt'
$log  = Join-Path $box 'launch.log'
$stampFile = Join-Path $box 'launch.stamp'
$stamp = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { 'no-stamp' }
function Log([string]$m) { "$(Get-Date -Format o) [$stamp] $m" | Out-File -FilePath $log -Append -Encoding UTF8 }
function Result([string]$m) { Log "RESULT $m"; Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $box 'launch.result') -Value "$stamp $m" }
$logonPat = '^\[[^\]]+\] \[Logged On,[^\]]*\].*RecvMsgClientLogOnResponse\(\) : processing complete'
$statePat = '^\[[^\]]+\] \[(Logged On|Logged Off|Connecting|Connected)[,\]]'
function LastMatch([string]$pat) {
  if (-not (Test-Path $conn)) { return '' }
  $m = Select-String -Path $conn -Pattern $pat | Select-Object -Last 1
  if ($m) { return $m.Line } else { return '' }
}

Log 'start'
$running = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if ($running) { Result ('fail already running: ' + (($running | % { "$($_.Name) $($_.Id)" }) -join ', ')); exit 2 }
$ver = (Get-Item (Join-Path $game 'SkyrimSE.exe')).VersionInfo.FileVersion
Log "SkyrimSE.exe version $ver"
if ($ver -ne '1.6.1170.0') { Result "fail SkyrimSE.exe is $ver, not 1.6.1170.0 (STOP and ask the owner)"; exit 3 }

$before = LastMatch $logonPat
$steam = Get-Process -Name steam -ErrorAction SilentlyContinue
$needWait = $true
if ($steam) {
  $state = LastMatch $statePat
  Log "steam already running (pid $($steam[0].Id)); latest state line: $state"
  if ($state -match '\[Logged On,' -and $state -notmatch 'LoggedOff|Logged In Elsewhere') { $needWait = $false }
} else {
  Log 'starting steam -silent'
  Start-Process 'C:\Steam\steam.exe' -ArgumentList '-silent'
}
if ($needWait) {
  $end = (Get-Date).AddSeconds(120); $got = ''
  while ((Get-Date) -lt $end) {
    $now = LastMatch $logonPat
    if ($now -and $now -ne $before) { $got = $now; break }
    Start-Sleep -Seconds 2
  }
  if (-not $got) { Result 'fail no new Steam "Logged On" line within 120 s'; exit 4 }
  Log "steam logged on: $got"
  Start-Sleep -Seconds 3
}

$elsewhereBefore = LastMatch 'Logged In Elsewhere'
$pidOk = $null
foreach ($attempt in 1, 2) {
  Log "starting skse64_loader.exe (attempt $attempt)"
  Start-Process (Join-Path $game 'skse64_loader.exe') -WorkingDirectory $game
  Start-Sleep -Seconds 15
  $g = Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue
  if ($g) { $pidOk = $g[0].Id; Log "SkyrimSE running, pid $pidOk"; break }
  Log "no SkyrimSE process 15 s after attempt $attempt"
}
$elsewhere = LastMatch 'Logged In Elsewhere'
if ($elsewhere -and $elsewhere -ne $elsewhereBefore) { Log "WARNING steam was logged off elsewhere: $elsewhere" }
if ($pidOk) { Result "ok pid=$pidOk"; exit 0 }
Result 'fail no SkyrimSE process after two loader starts'; exit 5
```

`tools/box/win/setup_task.ps1` (the task has no trigger: it runs only when `box_launch.sh` starts it; interactive, so the game shows on the box's desktop in geoff's session 1, where `AutoAdminLogon` keeps him logged on):
```powershell
# setup_task.ps1: one-time. Registers the scheduled task SkyrimPythonGame (interactive, runs as the logged-on
# user geoff, limited rights, on demand only: no trigger) that runs launch.ps1. If the task exists it prints
# its action and changes nothing. It never modifies or removes any other task.
$ErrorActionPreference = 'Stop'
$name = 'SkyrimPythonGame'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) { "EXISTS $name -> " + ($existing.Actions | % { "$($_.Execute) $($_.Arguments)" }); exit 0 }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\dev\skyrim-python-box\launch.ps1'
$principal = New-ScheduledTaskPrincipal -UserId 'geoff' -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings `
  -Description 'skyrim-python: start Steam if needed, wait for logon, start skse64_loader (C:\dev\skyrim-python-box\launch.ps1)' | Out-Null
$t = Get-ScheduledTask -TaskName $name
"REGISTERED $name -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) + " as $($t.Principal.UserId) $($t.Principal.LogonType)"
```

`tools/box/win/stamp.ps1`:
```powershell
# stamp.ps1: before a launch. Refuses (exit 2) when the game or its loader runs. Otherwise writes
# launch.stamp (box clock, ISO 8601) and launch-snapshot.json: length and SHA-256 of every file under the SKSE
# log folder, so wait_log.ps1 and quit_check.ps1 can tell which log lines and crash logs are from this launch.
$ErrorActionPreference = 'Stop'
$box = 'C:\dev\skyrim-python-box'
$logDir = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition\SKSE'
$running = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if ($running) { "REFUSED game running: " + (($running | % { "$($_.Name) $($_.Id)" }) -join ', '); exit 2 }
$snap = @{}
if (Test-Path $logDir) {
  Get-ChildItem $logDir -Recurse -File | Where-Object { $_.Length -lt 50MB } | ForEach-Object {
    $rel = $_.FullName.Substring($logDir.Length + 1)
    $snap[$rel] = @{ length = $_.Length; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
  }
}
$stamp = (Get-Date).ToString('o')
($snap | ConvertTo-Json -Depth 3 -Compress) | Set-Content -Encoding UTF8 (Join-Path $box 'launch-snapshot.json')
Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $box 'launch.stamp') -Value $stamp
"STAMP $stamp files=$($snap.Count)"
```

`tools/box/win/snapshot_lib.ps1`. "Only lines written after the launch stamp" without trusting file times (NTFS updates the time of a file the game holds open lazily): a log not in the snapshot is new; a log whose first L bytes still hash to the snapshot was appended to (only bytes after L count; `python.log` is like this); anything else was rewritten (the spdlog logs and `skse64.log` truncate on each launch), so all of it counts:
```powershell
# snapshot_lib.ps1 (dot-sourced): "what is new since launch.stamp" for files under the SKSE log folder.
# A file not in launch-snapshot.json is new (all of it is from this launch). A file whose first L bytes still
# hash to the snapshot (L = its length then) was appended to: only bytes after L are new. Otherwise it was
# rewritten (spdlog truncate, skse64.log): all of it is new. No file times are used (NTFS updates them lazily
# for files held open by the game).
$Box = 'C:\dev\skyrim-python-box'
$LogDir = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition\SKSE'
function Get-Snapshot {
  $p = Join-Path $Box 'launch-snapshot.json'
  if (-not (Test-Path $p)) { return $null }
  return (Get-Content $p -Raw | ConvertFrom-Json)
}
function Read-Shared([string]$path) {
  $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
  try { $ms = New-Object IO.MemoryStream; $fs.CopyTo($ms); return ,$ms.ToArray() } finally { $fs.Close() }
}
function Get-NewText([string]$rel, $snap) {
  $path = Join-Path $LogDir $rel
  if (-not (Test-Path $path)) { return $null }
  $bytes = Read-Shared $path
  $offset = 0
  $entry = $null
  if ($snap) { $entry = $snap.PSObject.Properties[$rel] }
  if ($entry) {
    $L = [int64]$entry.Value.length
    if ($bytes.Length -ge $L) {
      $sha = [Security.Cryptography.SHA256]::Create()
      $h = ($sha.ComputeHash($bytes, 0, [int]$L) | ForEach-Object { $_.ToString('X2') }) -join ''
      if ($h -eq $entry.Value.sha256) { $offset = $L }
    }
  }
  return [Text.Encoding]::UTF8.GetString($bytes, [int]$offset, [int]($bytes.Length - $offset))
}
function Get-NewCrashLogs($snap) {
  if (-not (Test-Path $LogDir)) { return @() }
  return @(Get-ChildItem $LogDir -File -Filter 'crash-*.log' | Where-Object {
    -not ($snap -and $snap.PSObject.Properties[$_.Name]) } | ForEach-Object { $_.Name })
}
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
```

`tools/box/win/wait_log.ps1`:
```powershell
# wait_log.ps1 -NameB64 <log path relative to the SKSE log folder> -PatB64 <.NET regex> [-Seconds 600]
# Bounded wait for a line written after the last launch stamp (see snapshot_lib.ps1). Exit 0 and
# "MATCH <line>" on a match; exit 1 with the tail of this launch's text on timeout; exit 3 with no stamp.
param([string]$NameB64, [string]$PatB64, [int]$Seconds = 600)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$name = (From-B64 $NameB64) -replace '/', '\'
$pat = From-B64 $PatB64
$snap = Get-Snapshot
if (-not $snap) { "NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)"; exit 3 }
$end = (Get-Date).AddSeconds($Seconds)
$text = $null
while ($true) {
  try { $text = Get-NewText $name $snap } catch { $text = $null }
  if ($text) {
    foreach ($line in ($text -split "`r?`n")) { if ($line -match $pat) { "MATCH $line"; exit 0 } }
  }
  if ((Get-Date) -ge $end) {
    "TIMEOUT no match for /$pat/ in $name after ${Seconds}s"
    if ($text) { "--- last 30 lines written since the launch stamp"; ($text -split "`r?`n") | Select-Object -Last 30 }
    else { "(no text written to $name since the launch stamp)" }
    exit 1
  }
  Start-Sleep -Seconds 2
}
```

`tools/box/win/console.ps1`:
```powershell
# console.ps1 -Tool <devbench tool> -JsonB64 <request body> [-TimeoutSec 15] | -List
# Calls DevBench's REST API on the box's loopback (POST /api/tool/<tool>, GET /api/tools). DevBench runs inside
# the game (SKSE plugin), so it answers only while the game runs.
param([string]$Tool = 'console', [string]$JsonB64 = '', [int]$TimeoutSec = 15, [switch]$List)
$base = 'http://127.0.0.1:8920'
# Reply bytes are written as UTF-8 unchanged (Content would decode JSON without a charset as Latin-1, and the
# console code page would mangle it again).
[Console]::OutputEncoding = New-Object Text.UTF8Encoding $false
function Emit($r) { [Console]::Out.Write([Text.Encoding]::UTF8.GetString($r.RawContentStream.ToArray())); [Console]::Out.WriteLine() }
try {
  if ($List) { Emit (Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec "$base/api/tools"); exit 0 }
  $body = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($JsonB64))
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Method Post -ContentType 'application/json' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) "$base/api/tool/$Tool"
  Emit $r; exit 0
} catch {
  $detail = ''
  if ($_.ErrorDetails) { $detail = $_.ErrorDetails.Message }
  "ERR $($_.Exception.Message) $detail"; exit 1
}
```

`tools/box/win/stop.ps1`:
```powershell
# stop.ps1: force-stop the game and its loader, by process name only; bounded check that they are gone.
$p = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if (-not $p) { "NOTHING_RUNNING"; exit 0 }
$p | ForEach-Object { "STOPPING $($_.Name) $($_.Id)" }
Stop-Process -Name SkyrimSE,skse64_loader -Force -ErrorAction SilentlyContinue
$end = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $end) {
  if (-not (Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue)) { "GONE"; exit 0 }
  Start-Sleep -Seconds 1
}
"STILL_RUNNING after 20 s: the process ignores termination (seen 2026-10-01: hung at exit, one thread in a kernel wait,"
"still holding 45978). STOP and ask the owner; a box reboot is their call."; exit 1
```

`tools/box/win/quit_check.ps1`:
```powershell
# quit_check.ps1 [-Seconds 120]: after console qqq. Waits (bounded) for SkyrimSE to end, then lists crash logs
# written since the launch stamp. Exit 0 only for a clean exit: process gone and no new crash-*.log.
param([int]$Seconds = 120)
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$snap = Get-Snapshot
$end = (Get-Date).AddSeconds($Seconds)
$gone = $false
while ((Get-Date) -lt $end) {
  if (-not (Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue)) { $gone = $true; break }
  Start-Sleep -Seconds 2
}
$crash = Get-NewCrashLogs $snap
if ($gone) { "EXITED" } else { "STILL_RUNNING after ${Seconds}s" }
if ($crash.Count -gt 0) { "NEW_CRASH_LOGS " + ($crash -join ', ') } else { "NO_NEW_CRASH_LOG" }
if ($gone -and $crash.Count -eq 0) { "CLEAN_EXIT"; exit 0 }
"NOT_CLEAN"; exit 1
```

`tools/box/win/place.ps1`:
```powershell
# place.ps1 -Mode exists|list|place -DestB64 <path> [-SrcB64 <staged file or .tar>] [-Kind file|tar]
#   exists: prints EXISTS or ABSENT.   list: prints the names of the entries in a folder (nothing if absent).
#   place:  file -> Copy-Item to Dest (parent created), prints "PLACED <sha256>";
#           tar  -> extracts into a fresh folder next to the .tar, then robocopy /E merges it into Dest (
#                   files with the same name are replaced, nothing else in Dest is touched), prints "PLACED <n> files".
# Only box_deploy.sh calls this, and only for paths under the game's Data\SKSE\Plugins.
param([string]$Mode, [string]$DestB64, [string]$SrcB64 = '', [string]$Kind = 'file')
$ErrorActionPreference = 'Stop'
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
$dest = From-B64 $DestB64
if (-not $dest.StartsWith('C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\')) { "REFUSED $dest is outside Data\SKSE\Plugins"; exit 2 }
switch ($Mode) {
  'exists' { if (Test-Path -LiteralPath $dest) { 'EXISTS' } else { 'ABSENT' }; exit 0 }
  'list'   { if (Test-Path -LiteralPath $dest) { Get-ChildItem -LiteralPath $dest -Force | ForEach-Object { $_.Name } }; exit 0 }
  'place' {
    $src = From-B64 $SrcB64
    if ($Kind -eq 'file') {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
      Copy-Item -LiteralPath $src -Destination $dest -Force
      "PLACED " + (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLower(); exit 0
    }
    $x = $src + '.d'
    New-Item -ItemType Directory -Force -Path $x | Out-Null
    & tar.exe -xf $src -C $x
    if ($LASTEXITCODE -ne 0) { "ERR tar exit $LASTEXITCODE"; exit 1 }
    # robocopy /E merges the tree into Dest (no /PURGE, /MIR or /MOV: it never deletes); codes 0-7 are success.
    & robocopy.exe $x $dest /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { "ERR robocopy exit $LASTEXITCODE"; exit 1 }
    "PLACED " + (Get-ChildItem -LiteralPath $x -Recurse -File).Count + " files"; exit 0
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
```

`tools/box/win/list.ps1`:
```powershell
# list.ps1 -PathsB64 <base64 of newline-separated paths>: LISTS (removes nothing) what the harness put on the box:
# the given deployed paths, our folder, our scheduled task, spike/plugin logs and the spike throwaway saves.
param([string]$PathsB64 = '')
$my = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition'
function Show($p) { if (Test-Path -LiteralPath $p) { $i = Get-Item -LiteralPath $p -Force; "{0,-10} {1:s} {2}" -f $(if ($i.PSIsContainer) { 'dir' } else { $i.Length }), $i.LastWriteTime, $i.FullName } }
"== deployed into the game by box_deploy.sh (out/box-deployed.txt) =="
if ($PathsB64) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PathsB64)) -split "`n" | Where-Object { $_ } | Sort-Object -Unique | ForEach-Object { $s = Show $_; if ($s) { $s } else { "absent     $_" } } }
else { '(none recorded)' }
"== our folder C:\dev\skyrim-python-box =="
Get-ChildItem 'C:\dev\skyrim-python-box' -Force -ErrorAction SilentlyContinue | ForEach-Object { Show $_.FullName }
"== our scheduled task =="
$t = Get-ScheduledTask -TaskName SkyrimPythonGame -ErrorAction SilentlyContinue
if ($t) { "SkyrimPythonGame -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) } else { '(not registered)' }
"== logs written by the spikes and the plugin =="
Get-ChildItem (Join-Path $my 'SKSE') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'SkyrimPython*' } | ForEach-Object { Show $_.FullName }
"== throwaway saves (spikebase, spytest, s4test) =="
Get-ChildItem (Join-Path $my 'Saves') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(spikebase|spytest|s4test)' } | ForEach-Object { Show $_.FullName }
''
'Nothing was removed. Show this list to the owner; remove only what the owner signs off on, item by item.'
```

- [ ] **Step 4: Run the offline test**

Run: `chmod +x tools/box/*.sh && tools/box/test_box_tools.sh; echo rc=$?`
Expected: every line `ok ...`, then `BOX TOOLS OFFLINE PASS`, `rc=0`.

- [ ] **Step 5: Install on the box, register the task, deploy**

```bash
tools/box/box_setup.sh
CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh out/SkyrimPythonSpike.dll
CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh --python out/SpikePyCore.dll
cat out/box-deployed.txt
# The overwrite refusal, against a file this tool did not deploy (the check runs before anything is copied):
mkdir -p out/refuse-test && printf 'MZfake' > out/refuse-test/devbench.dll
tools/box/box_deploy.sh out/refuse-test/devbench.dll; echo "rc=$? (expect 1)"
```
Expected: `parsed <name>.ps1` for all ten scripts, `INSTALLED`, `REGISTERED SkyrimPythonGame -> powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\dev\skyrim-python-box\launch.ps1 as geoff Interactive` (or `EXISTS SkyrimPythonGame -> ...` on a re-run); three `deployed ...` lines (the runtime as `PLACED 38 files`); `out/box-deployed.txt` holds `...\Plugins\SkyrimPythonSpike.dll`, `...\Plugins\SkyrimPython\python`, `...\Plugins\SkyrimPython\SpikePyCore.dll`; then `box: C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\devbench.dll exists on the box but is not in out/box-deployed.txt; not overwriting it. Ask the owner.` and `rc=1`.

- [ ] **Step 6: Launch, S1 from Linux through the tunnel, make `spikebase`, clean exit**

```bash
tools/box/box_launch.sh
tools/box/box_wait_log.sh SkyrimPythonSpike.log "listening on 127.0.0.1:45978" 600
tools/box/box_wait_log.sh SkyrimPythonSpike.log "kDataLoaded" 600
tools/box/box_wait_log.sh SkyrimPythonSpike.log "loading on Skyrim" 30
tools/box/box_tunnel.sh up
python3 spikes/spike_client.py ping
python3 spikes/spike_client.py s1
```
Expected: `launched: ok pid=<n>` after the launch log lines (`steam logged on: ...`, `SkyrimSE running, pid <n>`); three `MATCH` lines, the last `SkyrimPythonSpike loading on Skyrim 1.6.1170.0` (**any other version: STOP and ask the owner**; a wait that times out: STOP and ask the owner, do not relaunch in a loop); `tunnel up: ...`; `pong`; the three `S1 ... PASS` lines and `S1 PASS`. Through the tunnel the round trips include the LAN and ssh (2026-10-01: 352 round trips/s, split median 43.9 ms, 1 MiB in 222 ms); this is the Windows-native S1, recorded as such.

Now the throwaway save. `coc` from the main menu starts a new game in Whiterun; the new game then asks about Survival Mode in a message box, and a save requested while that box is open is silently not written, so answer it first:
```bash
tools/box/box_console.sh --list > out/box-devbench-tools.json        # DevBench's tools and schemas, for the record
tools/box/box_console.sh "coc WhiterunOrigin"
for i in $(seq 1 60); do s="$(tools/box/box_console.sh --tool inspect '{"kind":"scene"}' 2>&1)"; case "$s" in *'"WhiterunOrigin"'*) break;; esac; sleep 3; done; echo "$s"
for i in $(seq 1 20); do m="$(tools/box/box_console.sh --tool menu '{"action":"list"}')"; case "$m" in *'"messageBoxOpen":true'*) break;; esac; sleep 3; done; echo "$m"
tools/box/box_console.sh --tool menu '{"action":"describe"}'          # "Survival Mode ... Enable Survival Mode?" buttons ["Yes","No"]
tools/box/box_console.sh --tool menu '{"action":"accept","index":1}'  # No
tools/box/box_console.sh --tool menu '{"action":"list"}'              # only "HUD Menu": unpaused, no menu open
source tools/box/box_env.sh
bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter 'spikebase*').Count" # must print 0: never save over an existing name
tools/box/box_console.sh "save spikebase"
for i in $(seq 1 30); do n="$(bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter 'spikebase*.ess').Count" | tr -d '\r')"; [ "$n" != 0 ] && break; sleep 2; done
bssh "Get-ChildItem '$BOX_SAVES_DIR' -Filter 'spikebase*' | % { \$_.Name + ' ' + \$_.Length }" | tr -d '\r'
tools/box/box_console.sh --tool game '{"action":"list","filter":"spikebase","detail":true}'
python3 spikes/spike_client.py echo still-here
```
Expected: the scene JSON with `"cell":{"editorId":"WhiterunOrigin",...}` and `"playerLoaded":true`; the Survival Mode box, then `{"messageBoxOpen":false,"openMenus":["HUD Menu"],...}`; `0`; `{"action":"save","name":"spikebase","queued":true,"redirected":"game"}`; `spikebase.ess <about 2.9 MB>` and `spikebase.skse`; the `game` listing with one save `spikebase` (`location` `Whiterun`); `still-here`. If the Survival Mode box never shows (an install without that Creation Club content), skip the two `menu` calls after the wait.

Clean exit:
```bash
tools/box/box_quit.sh; echo "rc=$?"
tools/box/box_tunnel.sh down
```
Expected for a clean exit: DevBench's reply to `qqq` (or `(no reply to qqq; ...)`), `EXITED`, `NO_NEW_CRASH_LOG`, `CLEAN_EXIT`, `rc=0`; `tunnel down`. **2026-10-01 result: `STILL_RUNNING after 120s`, `NO_NEW_CRASH_LOG`, `NOT_CLEAN`** (see "Known issue" below). On `NOT_CLEAN`, run `tools/box/box_stop.sh`; if it prints `STILL_RUNNING`, STOP and ask the owner (do not retry, do not reboot the box yourself).

**Known issue (2026-10-01): the game hangs at exit on the box.** After `qqq` the game saved SKSE's co-save state (`skse64.log` ends with `cleared save path`), DevBench's server stopped, and the process sat with one thread in a kernel wait (no CPU, working set falling to 80 MB), still holding the spike's 45978 listener (`Listen` and `CloseWait`). `Stop-Process -Force` did not end it either. Python was not loaded in that run (no `py_load`; neither `python314.dll` nor `SpikePyCore.dll` among the process's modules), so the embedded interpreter is not the cause; the other SKSE plugins in that Stock Game were. Whether the box's game also hangs at exit without `SkyrimPythonSpike.dll` is not known (testing it means taking our DLL out of `Plugins`, which needs the owner's sign-off). Until the owner decides, a box `NOT_CLEAN` with no new crash log is a **baseline question**, not an S8 verdict: Task 3's clean-exit criterion needs a baseline run without our DLLs first.

- [ ] **Step 7: Commit**

```bash
git add tools/box docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded/task-01b-box-game-harness.md
git commit -m "chore(tools): game harness on the Windows box"
```

## Substitutions for Tasks 3-10

From Task 3's remaining runs on, read every Linux game step in Tasks 3-10 through this table. Run `tools/box/box_tunnel.sh up` once after each launch (it survives a game restart; bring it down at the end of the session). Nothing else in those tasks changes: the spike DLLs, `remote_build.sh`, the clients and the pass criteria stay as written.

| Linux step in Tasks 3-10 | On the box |
|---|---|
| `source tools/env.sh` (`SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`, `SKYRIM_PYTHON_STATE`) | `source tools/box/box_env.sh` (`BOX_DATA`, `BOX_PLUGINS`, `BOX_OWN`, `BOX_SKSE_LOG_DIR`, `BOX_SAVES_DIR`, `BOX_MYGAMES`, `SKYRIM_PYTHON_STATE`). Box paths are Windows paths: read them with `bssh "Get-Content '...'"`, never with Linux tools. |
| `CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll [ini]` | `CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh out/SkyrimPythonSpike.dll [ini]` (record: `out/box-deployed.txt`) |
| `tools/deploy.sh --python out/SpikePyCore.dll` (Task 3) | `CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh --python out/SpikePyCore.dll` |
| `tools/deploy.sh --site-packages out/site-packages` (Task 10) | `tools/box/box_deploy.sh --site-packages out/site-packages` |
| `--package`, `--scripts DIR` (Task 26, later plan) | `tools/box/box_deploy.sh --package`, `tools/box/box_deploy.sh --scripts DIR` |
| `cp ... "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/<f>"` + append to `out/deployed.txt` (Task 10's `s10_check.py`) | `tools/box/box_deploy.sh --file spikes/deps/s10_check.py s10_check.py` |
| `touch "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/spike-autostart"` (Task 3 run B) | `: > out/spike-autostart && tools/box/box_deploy.sh --file out/spike-autostart spike-autostart` (an empty marker file) |
| `tools/launch_game.sh` (and `LAUNCH_DRY_RUN=1`) | `tools/box/box_launch.sh` (no dry run; it refuses while the game runs and prints the launch log) |
| "quit the game first (console: `qqq`)" | `tools/box/box_quit.sh` (on `NOT_CLEAN`: see Known issue in Step 6; `tools/box/box_stop.sh`) |
| `SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/<log>" "<regex>" [s]` | `tools/box/box_wait_log.sh <log> "<regex>" [s]` with `<log>` relative to the SKSE log folder, e.g. `SkyrimPythonSpike.log`, `SkyrimPython/python.log`. The launch stamp is implicit. |
| `grep -E '<re>' "$SKSE_LOG_DIR/SkyrimPythonSpike.log"`, `cat`/`tail` of a log | `bssh "Select-String -Path '$BOX_SKSE_LOG_DIR\\SkyrimPythonSpike.log' -Pattern '<re>' \| % Line"` / `bssh "Get-Content '$BOX_SKSE_LOG_DIR\\SkyrimPythonSpike.log' -Tail 50"` |
| `cp "$SKSE_LOG_DIR"/SkyrimPythonSpike_s9_*.txt out/s9/` (Task 5) | `mkdir -p out/s9 && for n in base_a spin combo base_b; do bscp -s "$BOX:C:/Users/geoff/Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPythonSpike_s9_$n.txt" out/s9/SkyrimPythonSpike_s9_$n.txt; done` (`-s`: SFTP mode, which takes the path with spaces as is; plain scp 8.9 splits it at the spaces, and quoting it fails with "filename does not match request") |
| `grep -i iPresentInterval "$(dirname "$SKSE_LOG_DIR")"/Skyrim*.ini` (Task 5) | `bssh "Select-String -Path '$BOX_MYGAMES\\Skyrim*.ini' -Pattern iPresentInterval \| % Line"` (no line: game default 1, vsync). There is no DXVK on the box: `dxvk_frame_rate` is `unset`. |
| The spike ports 45978 (harness) and 45979 (S8 echo server) on Linux's `127.0.0.1` | The same commands after `tools/box/box_tunnel.sh up`. Through the tunnel a listener that is missing on the box shows as `FAIL: ConnectionError: server closed the connection` (exit 1) instead of `connection refused`, and a listener held by a hung process shows as `FAIL: TIMEOUT` (exit 2). Latency includes the LAN and ssh: compare S1/S8 timings with box runs only. |
| Task 10's Linux HTTP server on 45981 reached from the game | Start it on Linux as written, and bring the tunnel up with `tools/box/box_tunnel.sh up --reverse 45981` (down first if it is already up without it). The game's `http://127.0.0.1:45981/s10.txt` then reaches Linux. |
| Task 10's `W="Z:$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython"` and reading `$RESULT` on Linux | `W="C:/ChronicleDev/Stock Game/Data/SKSE/Plugins/SkyrimPython"`; register the result path with nothing (it is under `BOX_OWN`, listed by the teardown list) and fetch it with `bscp -s "$BOX:C:/ChronicleDev/Stock Game/Data/SKSE/Plugins/SkyrimPython/s10-result.json" out/s10-result.json`, then run Task 10's printing one-liner on `out/s10-result.json`. |
| Manual in-game actions: load the throwaway save (Continue/Load), "wait for gameplay", type in the console | `tools/box/box_console.sh "load spikebase"` (rerouted to DevBench's `game` tool `load`, which also skips the mod-mismatch prompt; not yet exercised on the box), then wait until `tools/box/box_console.sh --tool inspect '{"kind":"state"}'` shows `"playerLoaded":true` and `--tool menu '{"action":"list"}'` shows only `"HUD Menu"` (unpaused, no menu open; a console command sent through DevBench leaves no console open). Any console command: `tools/box/box_console.sh "<command>"`; output of printing commands: `--tool console '{"command":"getpos x","capture":true}'` then `--tool console '{"action":"read"}'`. |
| `save <name>` in the console (Task 6 `s4test`, Task 8 `spytest`) | First `bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter '<name>*').Count"` must print 0; then `tools/box/box_console.sh "save <name>"`; confirm the `.ess` appears. |
| "Load the save in an interior" (Task 5) | `tools/box/box_console.sh "load spikebase"`, wait as above, then `tools/box/box_console.sh "coc QASmoke"` (DevBench's configured quiet test cell) and wait for `--tool inspect '{"kind":"scene"}'` to name it. |
| "Play for about a minute (walk, open and close the inventory)" (Task 3 soak), any xdotool step | Not available as input; run the soak with the save loaded and the game unpaused (`--tool menu '{"action":"open","name":"InventoryMenu"}'` then `close` stands in for the inventory). Record "no player movement (box harness)" in the evidence. |
| `pgrep -x SkyrimSE.exe`, `/proc/$PID/status` `VmRSS` (Task 4) | `bssh "Get-Process SkyrimSE \| % { \$_.Id; [int](\$_.WorkingSet64/1KB) }"` (one PID; working set in kB stands in for VmRSS) |
| `ss -ltnp \| grep 45978` (Task 2 check 4) | `bssh "Get-NetTCPConnection -LocalPort 45978 \| % { \$_.State.ToString() + ' pid ' + \$_.OwningProcess }"` |
| Clean-exit check: `timeout 120 bash -c 'while pgrep -x SkyrimSE.exe ...'`, `tail "$SKYRIM_PYTHON_STATE/launch.log"`, `find "$SKSE_LOG_DIR" -newer launch.stamp -iname 'crash*'` | `tools/box/box_quit.sh`: console `qqq` through DevBench, then a bounded wait (`QUIT_WAIT`, 120 s) for `SkyrimSE` to end, then any `crash-*.log` in `BOX_SKSE_LOG_DIR` that was not there at the launch stamp. `CLEAN_EXIT` = process gone and no new crash log. Launch log: `bssh "Get-Content '$BOX_DIR\\launch.log' -Tail 20"`. |
| `objdump -p out/*.dll` | Unchanged (Linux, on the built DLLs). |
| `tools/teardown_list.sh` | `tools/box/box_teardown_list.sh` (also lists `C:\dev\skyrim-python-box` and the task `SkyrimPythonGame`). |
| The runtime note "GE-Proton10-14" in `spikes/RESULTS.md` | Box rows say "Windows 11 box, native" next to the runtime version from the spike log. |

## For the controller: add to the plan index (`README.md`)

1. Part A table, after Task 1: `| 1b | [Game harness on the Windows box](task-01b-box-game-harness.md) | Windows box + game | 1 |`, and in "Suggested order" `0 → 1 → 1b → 2 → 3 ...` with a note that Tasks 3-10 run the game on the box via the substitution table in Task 1b.
2. File Structure, under `tools/`: `box/{box_env,box_setup,box_deploy,box_launch,box_stop,box_wait_log,box_tunnel,box_console,box_quit,box_teardown_list,test_box_tools}.sh` and `box/win/{launch,setup_task,stamp,snapshot_lib,wait_log,console,stop,quit_check,place,list}.ps1`.
3. Fixed paths: game on the box `C:\ChronicleDev\Stock Game` (1.6.1170.0, SKSE 2.2.6), plugins `...\Data\SKSE\Plugins`, logs `C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE`, saves `...\Saves`, harness folder `C:\dev\skyrim-python-box`, scheduled task `SkyrimPythonGame`, DevBench `127.0.0.1:8920` on the box.
4. Contracts → Tools: the `tools/box/*` interfaces from this task's Interfaces block.
5. Global Constraints: "The game runs on the Windows box (Task 1b); no step launches the game on the Linux desktop, uses xdotool, or starts Linux Steam."
6. The open question on the box exit hang (Step 6, Known issue) before Task 3's clean-exit criterion is judged on the box.
