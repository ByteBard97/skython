# Task 1b: Game harness on the Windows box

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** (Fixed paths) and **Contracts**; they apply to this task. Steps use checkbox (`- [ ]`) syntax for tracking. New in this plan (no counterpart on branch `plan-out-of-process`).

**Why.** Tasks 1-3 ran the game on the owner's Linux desktop under GE-Proton. That steals the desktop's keyboard and mouse focus, and the owner needs that machine. From Task 3's remaining runs on, the M0 spikes (Tasks 3-10) run the game on the Windows build box instead (`geoff@192.168.0.211`, the same box that builds the DLLs). Everything is still driven from Linux: the tools below act on the box over ssh, and an ssh tunnel brings the spike ports to Linux's `127.0.0.1`, so `spikes/spike_client.py`, `spikes/py_send.py` and `spikes/py_echo_client.py` run unchanged.

> **Never on the Linux desktop:** no step of this task (or of Tasks 3-10 after it) launches the game on Linux, uses xdotool, or starts Linux Steam. Linux Steam logged in to the same account makes the box's Steam log off ("Logged In Elsewhere"); the game keeps running when that happens, but leave Linux Steam alone either way: it is the owner's.

> **Deploy safety (unchanged from Task 1):** the spike DLL opens an unauthenticated port that runs arbitrary Python after Task 3. Use only the box's throwaway save `spikebase` (made in Step 6), never save over another save, and remove nothing on the box without the owner's sign-off on `tools/box/box_teardown_list.sh`'s list. New files on the box go only under `C:\dev\skyrim-python-box`, `...\Data\SKSE\Plugins` (our DLLs, ini) and `...\Data\SKSE\Plugins\SkyrimPython`, plus exactly one file outside them, `...\Data\Scripts\SkyrimPython.pex` (Task 7, owner-approved, ruling R30). Never `Remove-Item`, `del`, `Unregister-ScheduledTask`; never modify or remove `ChronicleLiveDirect` or any other existing task, file, ini or save.

**What is on the box (investigated 2026-10-01, `.superpowers/sdd/README/windows-box-investigation.md`):** `C:\ChronicleDev\Stock Game\SkyrimSE.exe` 1.6.1170.0 with SKSE 2.2.6; SteamStub DRM, so Steam (`C:\Steam`) must be logged on before `skse64_loader.exe` starts (a launch that raced the logon gave no game: the loader started 5 s before Steam's `Logged On` line). DevBench 1.15.1 (`devbench.dll`, an SKSE plugin) serves REST on the box's `127.0.0.1:8920` while the game runs: `GET /api/tools`, `POST /api/tool/<name>`. CrashLogger writes `crash-*.log` in the SKSE log folder. 236 saves exist; none is named `spikebase*`. Other SKSE plugins in that Stock Game: ChronicleBridge, CountenanceNeuralFaceSpike, CrashLogger, devbench, mfgfix, PapyrusUtil, po3_PapyrusExtender, RuntimeHarness.

**Files:**
- Create: `tools/box/box_env.sh`, `tools/box/box_setup.sh`, `tools/box/box_deploy.sh`, `tools/box/box_launch.sh`, `tools/box/box_stop.sh`, `tools/box/box_wait_log.sh`, `tools/box/box_tunnel.sh`, `tools/box/box_console.sh`, `tools/box/box_quit.sh`, `tools/box/box_teardown_list.sh`, `tools/box/test_box_tools.sh`; for Part C (ruling R42): `tools/box/box_session.sh`, `tools/box/box_files.sh`, `tools/box/box_saves.sh`
- Create (copied to `C:\dev\skyrim-python-box` by `box_setup.sh`): `tools/box/win/launch.ps1`, `setup_task.ps1`, `stamp.ps1`, `snapshot_lib.ps1`, `wait_log.ps1`, `console.ps1`, `stop.ps1`, `quit_check.ps1`, `place.ps1`, `list.ps1`; for Part C: `files.ps1`
- On the box only: the scheduled task `SkyrimPythonGame`; the save `spikebase`.

**Interfaces:**
- Consumes: Task 1 (`out/SkyrimPythonSpike.dll` from `tools/remote_build.sh`, `$SKYRIM_PYTHON_STATE`), Task 0 (`third_party/cpython/embed`, `tools/fetch_python.sh --check`), Task 2 (`spikes/spike_client.py` on 45978), Task 3 (`out/SpikePyCore.dll`, `spikes/py_send.py`, `spikes/py_echo_client.py` on 45979).
- Produces (Tasks 3-10 use these through the substitution table below):
  - `source tools/box/box_env.sh`: `BOX`, `BOX_GAME_DIR='C:\ChronicleDev\Stock Game'`, `BOX_DATA`, `BOX_PLUGINS='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'`, `BOX_OWN` (`...\Plugins\SkyrimPython`), `BOX_MYGAMES`, `BOX_SKSE_LOG_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE'`, `BOX_SAVES_DIR`, `BOX_DIR='C:\dev\skyrim-python-box'`, `BOX_TASK=SkyrimPythonGame`; functions `bssh CMD` (60 s), `bssh_t SECONDS CMD`, `bscp ARGS` (600 s), `bps SECONDS SCRIPT.ps1 ARGS` (runs one of our box scripts), `b64 TEXT`. All calls carry `timeout`, `ConnectTimeout=10` and `BatchMode=yes`; remote commands are PowerShell 5.1.
  - `tools/box/box_setup.sh` (re-runnable): installs the `.ps1` files after the box's PowerShell parser accepts each one; registers `SkyrimPythonGame` and `SkyrimPythonShot` once (prints `REGISTERED ...` or `EXISTS ...`).
  - `tools/box/box_deploy.sh out/<Dll>.dll [ini] | --python [out/<Core>.dll] | --site-packages DIR | --package | --scripts DIR | --file LOCAL REL | --pex path/to/SkyrimPython.pex` → records Windows paths in `out/box-deployed.txt`; refuses a target that exists and is not recorded there (so `--pex` places `C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex` only if it is absent or ours; `place.ps1` accepts no other path outside `Data\SKSE\Plugins`); refuses while the game runs; spike DLLs need `CONFIRM_THROWAWAY_SAVE=1`.
  - `tools/box/box_launch.sh`: refuses if the game runs; writes the launch stamp on the box (box clock) and a snapshot of the SKSE log folder; copies the stamp to `$SKYRIM_PYTHON_STATE/box-launch.stamp`; runs the task; waits (`LAUNCH_WAIT`, 240 s) for `launch.ps1`'s result; prints `launched: ok pid=<n>` or fails.
  - `tools/box/box_wait_log.sh <log name> <regex> [seconds=600]`: `<log name>` relative to the box's SKSE log folder; only text written since the last launch stamp counts; prints `MATCH <line>` (exit 0) or `TIMEOUT ...` plus the tail (exit 1), or `DIALOG <title>` (exit 2) when SKSE's modal `SKSE Plugin Loader` dialog blocks the game (ruling R50; every 15 s while SkyrimSE runs it starts `SkyrimPythonShot` and reads its `windows.txt`, since `MainWindowTitle` is empty over ssh).
  - `tools/box/box_shot.sh [LOCAL_PNG]` (R50): runs our task `SkyrimPythonShot` (`shot.ps1`, registered by `box_setup.sh`), copies the box's screenshot to `$SKYRIM_PYTHON_STATE/box-shot.png` (or LOCAL_PNG) and prints the window titles.
  - `tools/box/box_tunnel.sh up [--reverse PORT]... | down | status`: Linux `127.0.0.1:45978` and `:45979` → the box's; `--reverse PORT` maps the box's `127.0.0.1:PORT` to Linux's.
  - `tools/box/box_console.sh "<console command>" | --tool NAME [json] | --list`: DevBench. `save <name>` and `load <name>` typed as console commands are rerouted by DevBench to its `game` tool (`{"redirected":"game"}`); running them raw would deadlock the engine.
  - `tools/box/box_quit.sh`: console `qqq`, then `quit_check.ps1`: prints `EXITED`/`STILL_RUNNING`, `NO_NEW_CRASH_LOG`/`NEW_CRASH_LOGS ...`, and `CLEAN_EXIT` (exit 0) or `NOT_CLEAN` (exit 1).
  - `tools/box/box_stop.sh`: `Stop-Process -Name SkyrimSE,skse64_loader -Force` and a 20 s check; if either is still alive, CIM `Win32_Process.Terminate` for exactly `Name='SkyrimSE.exe'` and `Name='skse64_loader.exe'` and another 20 s check. Prints `NOTHING_RUNNING`, `GONE via Stop-Process` or `GONE via CIM Terminate` (exit 0), or `STILL_RUNNING ...` (exit 1: STOP and ask the owner).
  - `tools/box/box_teardown_list.sh`: lists, removes nothing (saves `spikebase*`, `spytest*`, `s4test*`, `skypy_r38_savecall*`).
  - `BOX_OFFLINE=1` (ruling R43): every wrapper in `box_env.sh` (`bssh_t`, `bssh`, `bscp`, `bps`, `btunnel_start`, `btunnel_ctl`) prints `BOX_OFFLINE: refusing <cmd>` and exits 97 before any ssh/scp. `test_box_tools.sh` exports it, puts failing `ssh`/`scp` traps first on `PATH`, proves the refusal first and that no call reached the traps last; every check that runs a real script uses its print-only mode on top of that.
  - Part C additions (ruling R42; Tasks 25-32 use them through [Task 1c](task-01c-part-c-on-the-box.md)):
    - `box_deploy.sh out/SkyrimPython.dll [ini]`: loader to `Plugins\SkyrimPython.dll`, `out/SkyrimPythonCore.dll` (must exist, not older) to `Plugins\SkyrimPython\SkyrimPythonCore.dll`, the ini to `Plugins\SkyrimPython.ini`; `BOX_DEPLOY_PRINT_TARGET=1` prints the three targets before any ssh.
    - `box_deploy.sh --move REL NEWREL` (both relative to `Plugins`): `Move-Item` without `-Force` (`place.ps1 -Mode move`), only of a recorded target or something inside one (refused with `no deploy record ...` when `out/box-deployed.txt` does not exist; a `BOX_DEPLOYED=` override is honoured only in print mode), only to an absent destination inside `Plugins`; appends `<date> <src> -> <dest>` to `out/box-moves.txt` and records the destination. Never a delete.
    - `box_launch.sh [--env SKYRIM_PYTHON_TEST_<NAME>=VALUE]...`: `launch.env` is rewritten on every launch (empty without `--env`); `launch.ps1` sets only `SKYRIM_PYTHON_TEST_*` lines in its environment before starting the loader (the game inherits them), logs them, and empties the file.
    - `box_session.sh up | env | down | status | probe [PORT] | rewrite`: see its header. `up` writes `out/box-session.json` (mode 600, port = the tunnel's local end) and `out/box-live.env`, and keeps one tunnel `127.0.0.1:LOCAL -> box 127.0.0.1:PORT` (socket `box-session.sock`); `probe` asks the box whether a port listens.
    - `box_files.sh pull NAME... | put-control FILE`: mirror of the text written since the launch stamp into `out/box-skse/NAME` (empty when none); the one Linux-written file on the box is `SKSE\SkyrimPython\livetest_control.json`.
    - `box_saves.sh count NAME`: number of `NAME*.ess` in the box's Saves.
    - `out/` is gitignored, so the session file, `box-live.env`, the mirror, `box-deployed.txt` and `box-moves.txt` are never committed.

- [ ] **Step 1: Write the offline test and see it fail**

`tools/box/test_box_tools.sh` checks what can be checked without the box: every script exists and parses, no `ssh`/`scp` call bypasses the wrappers in `box_env.sh`, no destructive verb appears in any script, `Stop-Process` is used only by the two game names, and the box paths are the investigated ones.

```bash
#!/usr/bin/env bash
# Offline checks for the Windows-box game harness (no ssh, no game). Exit 0 = all pass.
# 1. every tools/box/*.sh exists and parses (bash -n);
# 2. no ssh/scp call outside box_env.sh's wrappers (so every call has a timeout and BatchMode);
# 3. no destructive verb in any script that acts on the box (Remove-Item, Unregister-ScheduledTask, rm, del, ...);
# 4. Stop-Process only by the two game process names;
# 5. the box paths in box_env.sh are the ones the investigation verified;
# 6. Part C (Tasks 25-32): the two-DLL deploy targets, recorded moves, launch env, the bridge session tunnel
#    (loopback only, token only in out/), the box file mirror, save-name checks, and the teardown list's saves.
# Every check runs offline: BOX_OFFLINE=1 makes box_env.sh's wrappers refuse (exit 97), and ssh/scp on PATH are
# traps that record any call and fail. A check that exercises a real script uses its print-only mode on top of that.
set -uo pipefail
export BOX_OFFLINE=1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
B="$ROOT/tools/box"
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
TRAP="$(mktemp -d)"; TRAPLOG="$TRAP/calls.log"
for c in ssh scp; do printf '#!/bin/sh\necho "%s $*" >> "%s"\nexit 99\n' "$c" "$TRAPLOG" > "$TRAP/$c"; chmod +x "$TRAP/$c"; done
export PATH="$TRAP:$PATH"

# 0. the offline guard: a wrapper refuses before any ssh/scp, and the traps are what PATH finds
g="$(bash -c 'source "$1/box_env.sh"; bssh "Get-Process"; echo "NOT REFUSED"' _ "$B" 2>&1)"; grc=$?
if [ "$grc" = 97 ] && [ "$g" = 'BOX_OFFLINE: refusing ssh 60 Get-Process' ] && [ ! -e "$TRAPLOG" ]; then ok "BOX_OFFLINE=1: bssh refuses (exit 97) and nothing reached ssh"
else bad "offline guard: rc=$grc '$g'"; echo "TEST ABORTED: the offline guard does not hold; no further check runs"; exit 1; fi
for w in 'bscp a b' 'bps 5 x.ps1' 'btunnel_start -L 1:2' 'btunnel_ctl check'; do
  g="$(bash -c 'source "$1/box_env.sh"; '"$w"'; echo "NOT REFUSED"' _ "$B" 2>&1)"; grc=$?
  [ "$grc" = 97 ] && case "$g" in "BOX_OFFLINE: refusing "*) true ;; *) false ;; esac && ok "BOX_OFFLINE=1: ${w%% *} refuses" \
    || { bad "offline guard for ${w%% *}: rc=$grc '$g'"; echo "TEST ABORTED"; exit 1; }
done
[ "$(command -v ssh)" = "$TRAP/ssh" ] && [ "$(command -v scp)" = "$TRAP/scp" ] && ok "ssh and scp on PATH are the traps" \
  || { bad "ssh/scp traps not first on PATH"; exit 1; }

SH=(box_env.sh box_deploy.sh box_launch.sh box_stop.sh box_wait_log.sh box_tunnel.sh box_console.sh
    box_quit.sh box_setup.sh box_teardown_list.sh box_session.sh box_files.sh box_saves.sh)
PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1 files.ps1)

for f in "${SH[@]}"; do
  if [ ! -f "$B/$f" ]; then bad "missing tools/box/$f"; continue; fi
  if bash -n "$B/$f" 2>/dev/null; then ok "bash -n $f"; else bad "bash -n $f"; fi
done
for f in "${PS[@]}"; do [ -f "$B/win/$f" ] && ok "present win/$f" || bad "missing tools/box/win/$f"; done

# 2. raw ssh/scp only inside box_env.sh (the wrappers bssh/bssh_t/bscp/bps carry timeout + BatchMode)
raw="$(grep -HnE '(^\s*|[;&|({]\s*|\$\(\s*|timeout +[^ ]+ +|then +|do +)(ssh|scp) ' "$B"/*.sh 2>/dev/null | grep -v '^[^:]*/box_env.sh:' | grep -v '^[^:]*/test_box_tools.sh:' | grep -vE ':[0-9]+:\s*#' )"
if [ -z "$raw" ]; then ok "no raw ssh/scp outside box_env.sh"; else bad "raw ssh/scp outside box_env.sh:"; echo "$raw"; fi
if grep -q 'BatchMode=yes' "$B/box_env.sh" 2>/dev/null && grep -q 'ConnectTimeout=' "$B/box_env.sh" 2>/dev/null \
   && grep -qE 'timeout "\$' "$B/box_env.sh" 2>/dev/null; then ok "wrappers use BatchMode, ConnectTimeout and timeout"
else bad "box_env.sh wrappers lack BatchMode/ConnectTimeout/timeout"; fi

# 3. destructive verbs (code lines only; comments may mention them to forbid them)
destr="$(grep -HnEi '(^|[^-a-z])(Remove-Item|Unregister-ScheduledTask|rmdir|rd /s|del |erase |Clear-Content|git clean|rm -|rm ")' \
        "$B"/*.sh "$B"/win/*.ps1 2>/dev/null | grep -v '/test_box_tools.sh:' | grep -vE ':[0-9]+:\s*#')"
if [ -z "$destr" ]; then ok "no destructive verbs"; else bad "destructive verbs:"; echo "$destr"; fi

# 4. Stop-Process (as a command, not in a message) only by name SkyrimSE / skse64_loader
sp="$(grep -nhE '(^\s*|[;|{]\s*)Stop-Process' "$B"/win/*.ps1 2>/dev/null | grep -vE '^\s*[0-9]+:\s*#')"
if [ -n "$sp" ] && ! echo "$sp" | grep -vq -- '-Name SkyrimSE,skse64_loader'; then ok "Stop-Process only by the game names"
else bad "Stop-Process used otherwise:"; echo "$sp"; fi

# 4b. CIM Terminate fallback: exactly two Win32_Process lines, pinned to the exact image names
cim="$(grep -hE 'Win32_Process|Invoke-CimMethod' "$B"/win/*.ps1 2>/dev/null | grep -vE '^\s*#')"
want1="Get-CimInstance Win32_Process -Filter \"Name='SkyrimSE.exe'\" | Invoke-CimMethod -MethodName Terminate"
want2="Get-CimInstance Win32_Process -Filter \"Name='skse64_loader.exe'\" | Invoke-CimMethod -MethodName Terminate"
if [ "$(echo "$cim" | grep -c .)" = 2 ] && echo "$cim" | grep -qF -- "$want1" && echo "$cim" | grep -qF -- "$want2"; then
  ok "CIM Terminate only for Name='SkyrimSE.exe' and Name='skse64_loader.exe'"
else bad "CIM Terminate lines are not exactly the two pinned ones:"; echo "$cim"; fi
for m in 'GONE via Stop-Process' 'GONE via CIM Terminate' 'STILL_RUNNING'; do
  grep -qF "\"$m" "$B/win/stop.ps1" 2>/dev/null && ok "stop.ps1 reports '$m'" || bad "stop.ps1 does not report '$m'"
done

# 4c. .pex mode: box_deploy.sh --pex resolves to exactly Data\Scripts\SkyrimPython.pex (no ssh in this mode)
tmpd="$(mktemp -d)"; : > "$tmpd/SkyrimPython.pex"; : > "$tmpd/Other.pex"; : > "$tmpd/x.txt"
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --pex "$tmpd/SkyrimPython.pex" 2>&1)"
[ "$t" = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex' ] && ok "--pex target is Data\\Scripts\\SkyrimPython.pex" || bad "--pex target: '$t'"
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --pex "$tmpd/Other.pex" >/dev/null 2>&1; then bad "--pex accepted Other.pex"; else ok "--pex refuses a file not named SkyrimPython.pex"; fi
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --pex "$tmpd/missing.pex" >/dev/null 2>&1; then bad "--pex accepted a missing file"; else ok "--pex refuses a missing file"; fi
# place.ps1 allows exactly one destination outside Data\SKSE\Plugins: that .pex
grep -qF "\$PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'" "$B/win/place.ps1" 2>/dev/null \
  && grep -qF -- '-and $dest -ne $PexDest' "$B/win/place.ps1" && ok "place.ps1 allow-list: Plugins tree + the one .pex" || bad "place.ps1 allow-list"
# box_deploy records through put_file, which refuses a target that exists and is not recorded
grep -qE 'put_file "\$PEX" "\$PEX_DEST"' "$B/box_deploy.sh" 2>/dev/null && ok "--pex goes through put_file (absent or ours; recorded)" || bad "--pex does not use put_file"

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

# 6a. two-DLL layout: loader -> Plugins, core -> Plugins\SkyrimPython, ini -> Plugins\SkyrimPython.ini (no ssh)
P='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'
d2="$(mktemp -d)"; : > "$d2/SkyrimPython.dll"; : > "$d2/dev.ini"; sleep 1; : > "$d2/SkyrimPythonCore.dll"
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d2/SkyrimPython.dll" "$d2/dev.ini" 2>&1)"
want="$(printf '%s\n' "$P\SkyrimPython.dll" "$P\SkyrimPython\SkyrimPythonCore.dll" "$P\SkyrimPython.ini")"
[ "$t" = "$want" ] && ok "two-DLL targets: loader, core in SkyrimPython\\, ini" || { bad "two-DLL targets:"; echo "$t"; }
touch -d '2000-01-01' "$d2/SkyrimPythonCore.dll"
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d2/SkyrimPython.dll" >/dev/null 2>&1; then bad "accepted a core older than the loader"; else ok "refuses a core DLL older than the loader"; fi
d3="$(mktemp -d)"; : > "$d3/SkyrimPython.dll"
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d3/SkyrimPython.dll" >/dev/null 2>&1; then bad "accepted a loader without its core"; else ok "refuses the loader without SkyrimPythonCore.dll"; fi

# 6b. --move: recorded moves inside Plugins, only of what box_deploy put there (no ssh in print mode)
rec="$(mktemp)"; printf '%s\n' "$P\SkyrimPython\python" "$P\SkyrimPythonSpike.dll" > "$rec"
mv_t() { BOX_DEPLOYED="$rec" BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --move "$@" 2>&1; }
t="$(mv_t SkyrimPython/python SkyrimPython/python.off)"
[ "$t" = "MOVE $P\SkyrimPython\python -> $P\SkyrimPython\python.off" ] && ok "--move resolves under Plugins" || bad "--move target: '$t'"
t="$(mv_t 'SkyrimPython\python\python314.zip' 'SkyrimPython\python\python314.zip.off')"
[ "$t" = "MOVE $P\SkyrimPython\python\python314.zip -> $P\SkyrimPython\python\python314.zip.off" ] && ok "--move of a file inside a recorded folder" || bad "--move inside a recorded folder: '$t'"
for args in "devbench.dll devbench.dll.off" "SkyrimPython/../../x SkyrimPython/y" "SkyrimPython/python C:/elsewhere" "SkyrimPython/python SkyrimPython/python" "SkyrimPython/python"; do
  # shellcheck disable=SC2086
  if mv_t $args >/dev/null; then bad "--move accepted: $args"; else ok "--move refuses: $args"; fi
done
t="$(BOX_DEPLOYED=/nonexistent/box-deployed.txt BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --move SkyrimPython/python SkyrimPython/python.off 2>&1)"
case "$t" in *"no deploy record"*) ok "--move refuses clearly when the deploy record is missing" ;; *) bad "--move with no deploy record: '$t'" ;; esac
[ -e /nonexistent/box-deployed.txt ] && bad "print mode created the deploy record" || true
t="$(BOX_DEPLOYED=/tmp/elsewhere.txt bash -c 'source "$1/box_env.sh"; echo "$BOX_DEPLOYED"' _ "$B")"
[ "$t" = "$ROOT/out/box-deployed.txt" ] && ok "BOX_DEPLOYED override ignored outside print mode" || bad "BOX_DEPLOYED override honoured outside print mode: '$t'"
grep -qE '^\s*Move-Item -LiteralPath \$src -Destination \$dest$' "$B/win/place.ps1" 2>/dev/null && ok "place.ps1 move: Move-Item without -Force" || bad "place.ps1 has no plain Move-Item for -Mode move"
if grep -hE 'Move-Item' "$B"/win/*.ps1 | grep -vE '^\s*#' | grep -q -- '-Force'; then bad "Move-Item -Force somewhere"; else ok "no Move-Item -Force"; fi
grep -qF '"ERR destination exists' "$B/win/place.ps1" 2>/dev/null && ok "place.ps1 move refuses an existing destination" || bad "place.ps1 move does not refuse an existing destination"
grep -qF 'box-moves.txt' "$B/box_deploy.sh" 2>/dev/null && ok "moves are recorded in out/box-moves.txt" || bad "moves are not recorded"

# 6c. box_launch.sh --env: only SKYRIM_PYTHON_TEST_* names, written for every launch (no ssh in print mode)
t="$(BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" --env SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1 --env 'SKYRIM_PYTHON_TEST_PRELOAD=C:\a b\python314.dll' 2>&1)"
[ "$t" = "$(printf '%s\n' SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME=1 'SKYRIM_PYTHON_TEST_PRELOAD=C:\a b\python314.dll')" ] && ok "--env writes the allow-listed names" || bad "--env content: '$t'"
t="$(BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" 2>&1)"; [ -z "$t" ] && ok "no --env: launch.env is empty (nothing leaks into the next launch)" || bad "launch.env without --env: '$t'"
if BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" --env PATH=/x >/dev/null 2>&1; then bad "--env accepted PATH"; else ok "--env refuses a name outside SKYRIM_PYTHON_TEST_*"; fi
grep -qF "'^(SKYRIM_PYTHON_TEST_[A-Z0-9_]+)=(.*)\$'" "$B/win/launch.ps1" 2>/dev/null && ok "launch.ps1 applies only SKYRIM_PYTHON_TEST_* lines" || bad "launch.ps1 does not filter launch.env"

# 6d. box_session.sh: loopback-only forward; the token only in out/box-session.json (mode 600), never printed
fw="$(grep -oE -- '-L "?[^ "]+' "$B/box_session.sh" 2>/dev/null | tr -d '"')"
if [ -n "$fw" ] && ! echo "$fw" | grep -qvE -- '^-L 127\.0\.0\.1:\$[A-Za-z_]+:127\.0\.0\.1:\$[A-Za-z_]+$'; then ok "session tunnel binds 127.0.0.1 on both ends"
else bad "session tunnel forward is not loopback-only: '$fw'"; fi
grep -qE -- '(^|[^A-Za-z])-R |GatewayPorts|0\.0\.0\.0' "$B/box_session.sh" 2>/dev/null && bad "box_session.sh has -R, GatewayPorts or 0.0.0.0" || ok "no reverse forward, no GatewayPorts, no 0.0.0.0"
tok="0123456789abcdef0123456789abcdef"; sd="$(mktemp -d)"
printf '{"port": 45977, "token": "%s", "pid": 7, "plugin_version": "0.1.0", "protocol": [1, 0]}' "$tok" > "$sd/in.json"
t="$("$B/box_session.sh" rewrite "$sd/in.json" "$sd/out.json" 45990 2>&1)"; rc=$?
out_ok="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["port"]==45990 and d["token"]==sys.argv[2] and d["pid"]==7 and "host" not in d)' "$sd/out.json" "$tok" 2>&1)"
[ "$rc" = 0 ] && [ "$out_ok" = True ] && ok "rewrite: port -> the tunnel's local end, token kept" || bad "rewrite: rc=$rc '$out_ok' '$t'"
case "$t" in *"$tok"*) bad "rewrite printed the token" ;; *) ok "rewrite does not print the token" ;; esac
[ "$(stat -c %a "$sd/out.json" 2>/dev/null)" = 600 ] && ok "session file is mode 600" || bad "session file mode $(stat -c %a "$sd/out.json" 2>/dev/null)"
printf '{"host": "0.0.0.0", "port": 45977, "token": "%s"}' "$tok" | "$B/box_session.sh" rewrite - "$sd/h.json" 45977 >/dev/null 2>&1
[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("host"))' "$sd/h.json" 2>&1)" = 127.0.0.1 ] && ok "rewrite: a host field becomes 127.0.0.1" || bad "rewrite: host not rewritten"
printf '{"port": "x", "token": "%s"}' "$tok" | "$B/box_session.sh" rewrite - "$sd/bad.json" 45977 >/dev/null 2>&1 && bad "rewrite accepted a bad port" || ok "rewrite refuses a bad port"
printf '{"active": false, "reason": "bridge_enabled is false"}' | "$B/box_session.sh" rewrite - "$sd/off.json" 45977 >/dev/null 2>&1
[ "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["active"])' "$sd/off.json" 2>&1)" = False ] && ok "rewrite keeps an inactive session file as it is" || bad "rewrite of an inactive session file"
ign=1; for f in out/box-session.json out/box-live.env out/box-skse/SkyrimPython.log; do git -C "$ROOT" check-ignore -q "$f" || ign=0; done
grep -qE '^out/$' "$ROOT/.gitignore" && [ "$ign" = 1 ] && ok "out/ (session file, env file, log mirror) is gitignored" || bad "out/box-session.json is not gitignored"

# 6e. box_files.sh: pull never takes session.json; put writes only livetest_control.json (no ssh in print mode)
for n in SkyrimPython/session.json ../x 'C:/x' /abs; do
  if BOX_FILES_PRINT=1 "$B/box_files.sh" pull "$n" >/dev/null 2>&1; then bad "pull accepted '$n'"; else ok "pull refuses '$n'"; fi
done
t="$(BOX_FILES_PRINT=1 "$B/box_files.sh" pull SkyrimPython.log SkyrimPython/python.log 2>&1)"
[ "$t" = "$(printf '%s\n' "$ROOT/out/box-skse/SkyrimPython.log" "$ROOT/out/box-skse/SkyrimPython/python.log")" ] && ok "pull mirrors into out/box-skse" || bad "pull targets: '$t'"
grep -qF "\$PutAllowed = 'SkyrimPython\livetest_control.json'" "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 put allow-list: livetest_control.json only" || bad "files.ps1 put allow-list"
grep -qF 'Get-NewText' "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 pull uses the launch-stamp snapshot" || bad "files.ps1 does not use Get-NewText"

# 6f. box_saves.sh: a save name is checked before any ssh
if "$B/box_saves.sh" count 'bad name*' >/dev/null 2>&1; then bad "box_saves.sh accepted 'bad name*'"; else ok "box_saves.sh refuses a name that is not [A-Za-z0-9_]"; fi

# 6g. the teardown list shows Task 31's save
grep -qF 'spikebase|spytest|s4test|skypy_r38_savecall' "$B/win/list.ps1" 2>/dev/null && ok "list.ps1 lists skypy_r38_savecall" || bad "list.ps1 does not list skypy_r38_savecall"

# 7. nothing reached ssh or scp during the whole run
if [ -e "$TRAPLOG" ]; then bad "ssh/scp was called during the offline test:"; cat "$TRAPLOG"; else ok "no ssh/scp call reached PATH"; fi

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
# BOX_OFFLINE=1 (set by tools/box/test_box_tools.sh): every wrapper refuses, prints "BOX_OFFLINE: refusing <cmd>"
# and exits non-zero, so an offline check can never reach the box, even through a missing dry-run mode.
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
# The deploy record. An override (BOX_DEPLOYED=...) is honoured only with BOX_DEPLOY_PRINT_TARGET=1 (offline tests):
# a real deploy always records in out/box-deployed.txt.
if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" != 1 ] || [ -z "${BOX_DEPLOYED:-}" ]; then BOX_DEPLOYED="$BOX_ROOT/out/box-deployed.txt"; fi
SKYRIM_PYTHON_STATE="${SKYRIM_PYTHON_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/skyrim-python}"
BOX_TUNNEL_SOCK="$SKYRIM_PYTHON_STATE/box-tunnel.sock"
BOX_SESSION_SOCK="$SKYRIM_PYTHON_STATE/box-session.sock"   # the dev-bridge tunnel (box_session.sh)
export BOX BOX_GAME_DIR BOX_DATA BOX_PLUGINS BOX_OWN BOX_MYGAMES BOX_SKSE_LOG_DIR BOX_SAVES_DIR BOX_DIR BOX_TASK SKYRIM_PYTHON_STATE

# box_offline <what>: refuse (exit 97) when BOX_OFFLINE=1. Every wrapper below calls it first.
box_offline() { if [ "${BOX_OFFLINE:-0}" = 1 ]; then echo "BOX_OFFLINE: refusing $*" >&2; exit 97; fi; }
# bssh_t <seconds> <powershell command>: one bounded remote command.
bssh_t() { box_offline "ssh $*"; local t="$1"; shift; timeout "$t" ssh "${BOX_SSH_OPTS[@]}" "$BOX" "$@"; }
# bssh <powershell command>: bounded at 60 s.
bssh() { bssh_t 60 "$@"; }
# bscp <scp args...>: bounded at 600 s.
bscp() { box_offline "scp $*"; timeout 600 scp "${BOX_SSH_OPTS[@]}" -q "$@"; }
# bps <seconds> <script.ps1> [args...]: run one of our scripts in C:\dev\skyrim-python-box (args must not contain
# quotes or spaces: pass text through b64).
bps() { box_offline "bps $*"; local t="$1" s="$2"; shift 2; bssh_t "$t" "powershell -NoProfile -ExecutionPolicy Bypass -File $BOX_DIR\\$s $*"; }
# b64 <text>: base64 of the UTF-8 text, one line (decoded on the box by our scripts).
b64() { printf '%s' "$1" | base64 -w0; }
box_die() { echo "box: $*" >&2; exit 1; }

# The tunnels: one background ssh each, with a control socket (TUNNEL_SOCK, default BOX_TUNNEL_SOCK; box_session.sh
# passes BOX_SESSION_SOCK). The connect itself is bounded (timeout); with -f the daemonised child outlives the
# timeout's watched process by design.
btunnel_start() { box_offline "tunnel $*"; timeout 30 ssh "${BOX_SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -f -N -M -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" "$@" "$BOX"; }
btunnel_ctl() { box_offline "tunnel control $*"; timeout 15 ssh -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" -O "$1" "$BOX"; }
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
#   tools/box/box_deploy.sh --pex path/to/SkyrimPython.pex   -> exactly Data\Scripts\SkyrimPython.pex (Task 7; the only
#                                                               target outside Plugins; placed only if absent or ours)
#   tools/box/box_deploy.sh --move REL NEWREL                -> moves Plugins\REL to Plugins\NEWREL (Task 26's startup
#                                                               variants; Task 25 moves the spike DLL aside). REL must be
#                                                               a recorded target or inside one; NEWREL must be absent.
#                                                               Move-Item, never a delete. Each move is appended to
#                                                               out/box-moves.txt and NEWREL is recorded.
#   BOX_DEPLOY_PRINT_TARGET=1 with --pex, a DLL or --move prints the resolved target(s) and exits before any ssh
#   (offline test).
# Every target is recorded in out/box-deployed.txt. A target that exists on the box and is not recorded there
# was not deployed by this tool: the deploy refuses (it never overwrites what it did not put there).
# A *Spike* DLL needs CONFIRM_THROWAWAY_SAVE=1. Refuses while the game runs (DLLs are locked).
# Files are staged under C:\dev\skyrim-python-box\stage (no spaces for scp), then copied into place on the box.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$BOX_ROOT/out/box-stage"
[ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ] || touch "$BOX_DEPLOYED"   # print mode never creates the record

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
owned() {  # owned <windows path>: recorded, or inside a recorded folder
  local r
  [ -f "$BOX_DEPLOYED" ] || box_die "no deploy record $BOX_DEPLOYED: this tool deployed nothing, so there is nothing it may move"
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    [ "$1" = "$r" ] && return 0
    case "$1" in "$r\\"*) return 0 ;; esac
  done < "$BOX_DEPLOYED"
  return 1
}
plugins_rel() {  # plugins_rel <REL>: the Windows path under Plugins, or die
  local r="${1//\//\\}"
  case "$r" in ""|*..*|*:*|\\*) box_die "bad path '$1': give it relative to Plugins, without '..' or a drive" ;; esac
  echo "$BOX_PLUGINS\\$r"
}
if [ "$1" = --pex ]; then
  PEX="${2:?usage: box_deploy.sh --pex path/to/SkyrimPython.pex}"
  [ -f "$PEX" ] || box_die "no such file: $PEX"
  [ "$(basename "$PEX")" = SkyrimPython.pex ] || box_die "--pex places only SkyrimPython.pex (got $(basename "$PEX"))"
  PEX_DEST="$BOX_DATA\\Scripts\\SkyrimPython.pex"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$PEX_DEST"; exit 0; fi
elif [ "$1" = --move ]; then
  [ $# -eq 3 ] || box_die "usage: box_deploy.sh --move REL NEWREL (both relative to Plugins)"
  MV_SRC="$(plugins_rel "$2")"; MV_DEST="$(plugins_rel "$3")"
  [ "$MV_SRC" != "$MV_DEST" ] || box_die "--move: source and destination are the same"
  owned "$MV_SRC" || box_die "--move: $MV_SRC was not deployed by this tool (not in out/box-deployed.txt); not moving it"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "MOVE $MV_SRC -> $MV_DEST"; exit 0; fi
elif [ "${1#-}" = "$1" ]; then
  DLL="$1"; [ -f "$DLL" ] || box_die "no such file: $DLL"; spike_ok "$DLL"
  base="$(basename "$DLL")"
  if [ "$base" = SkyrimPython.dll ]; then
    CORE="$(dirname "$DLL")/SkyrimPythonCore.dll"
    [ -f "$CORE" ] || box_die "$CORE missing: run tools/remote_build.sh plugin SkyrimPythonCore.dll"
    [ "$DLL" -nt "$CORE" ] && box_die "$CORE is older than $DLL: rebuild it"
  fi
  [ $# -ge 2 ] && { [ -f "$2" ] || box_die "no such file: $2"; }
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then
    echo "$BOX_PLUGINS\\$base"
    [ "$base" = SkyrimPython.dll ] && echo "$BOX_OWN\\SkyrimPythonCore.dll"
    [ $# -ge 2 ] && echo "$BOX_PLUGINS\\${base%.dll}.ini"
    exit 0
  fi
fi
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
  --pex)
    put_file "$PEX" "$PEX_DEST"
    ;;
  --move)
    r="$(bps 60 place.ps1 -Mode move -SrcB64 "$(b64 "$MV_SRC")" -DestB64 "$(b64 "$MV_DEST")" | tr -d '\r')"
    [ "$r" = MOVED ] || box_die "moving $MV_SRC: '$r'"
    echo "$(date -Is) $MV_SRC -> $MV_DEST" >> "$BOX_ROOT/out/box-moves.txt"
    rec "$MV_DEST"; echo "moved $MV_SRC -> $MV_DEST (recorded in out/box-moves.txt)"
    ;;
  -*) box_die "unknown option $1" ;;
  *)
    # DLL, base and CORE were checked before the ssh (above)
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
# Usage: tools/box/box_launch.sh [--env SKYRIM_PYTHON_TEST_<NAME>=VALUE]...
#   --env: launch.ps1 sets these in its own environment before it starts skse64_loader.exe, so the game inherits
#   them (Task 25's SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME, Task 26's SKYRIM_PYTHON_TEST_PRELOAD). Only
#   SKYRIM_PYTHON_TEST_* names. C:\dev\skyrim-python-box\launch.env is rewritten on every launch (empty without
#   --env), so a variable never leaks into a later launch; launch.ps1 also empties it after reading it.
#   BOX_LAUNCH_PRINT_ENV=1 prints launch.env's content and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
envtext=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env)
      kv="${2:?--env NAME=VALUE}"
      [[ "$kv" =~ ^SKYRIM_PYTHON_TEST_[A-Z0-9_]+=.*$ ]] || box_die "--env takes only SKYRIM_PYTHON_TEST_<NAME>=VALUE (got '${kv%%=*}')"
      case "$kv" in *$'\n'*|*$'\r'*) box_die "--env value must be one line" ;; esac
      envtext+="$kv"$'\n'; shift 2 ;;
    *) box_die "unknown option $1 (usage: box_launch.sh [--env SKYRIM_PYTHON_TEST_<NAME>=VALUE]...)" ;;
  esac
done
if [ "${BOX_LAUNCH_PRINT_ENV:-0}" = 1 ]; then printf '%s' "$envtext"; exit 0; fi
mkdir -p "$SKYRIM_PYTHON_STATE"
out="$(bps 60 stamp.ps1 | tr -d '\r')" || box_die "refused: $out"
stamp="$(echo "$out" | sed -n 's/^STAMP \([^ ]*\).*/\1/p')"; [ -n "$stamp" ] || box_die "no stamp: $out"
echo "$stamp" > "$SKYRIM_PYTHON_STATE/box-launch.stamp"
echo "box launch stamp $stamp"
bssh "[IO.File]::WriteAllText('$BOX_DIR\\launch.env', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$(b64 "$envtext")')))" \
  || box_die "could not write launch.env"
[ -n "$envtext" ] && printf 'launch env: %s' "$envtext"
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

`tools/box/box_session.sh` (Part C, ruling R42): the dev bridge from Linux. It reads the box's `session.json` only if it was written since the launch stamp, writes the token only into `out/box-session.json` (mode 600, gitignored), and forwards loopback to loopback:
```bash
#!/usr/bin/env bash
# The dev bridge from Linux while the game runs on the box (Tasks 25-32).
# Usage: tools/box/box_session.sh up | env | down | status | probe [PORT] | rewrite IN|- OUT LOCAL_PORT|same
#   up:      after each box launch, once the core log shows "python host: running". Reads the box's
#            SKSE\SkyrimPython\session.json written since the last launch stamp (a file left by an earlier launch is
#            refused), writes out/box-session.json (mode 600; the bridge token lives only there, never printed) with
#            "port" set to the tunnel's local end (BOX_SESSION_LOCAL_PORT, default the box's port, 45977 with the dev
#            ini) and "host", if present, to 127.0.0.1; then brings up, or keeps, one ssh tunnel
#            Linux 127.0.0.1:LOCAL -> box 127.0.0.1:PORT (control socket $SKYRIM_PYTHON_STATE/box-session.sock).
#            Loopback on both ends. An inactive session file ("active": false) is written as is (the live tests'
#            load_session reports its reason) and no tunnel is needed. Prints the session file's path last.
#   env:     writes out/box-live.env (also done by up). `source out/box-live.env` sets SKYRIM_PYTHON_SESSION to
#            out/box-session.json, SKSE_LOG_DIR and SKYRIM_PYTHON_LOG to the log mirror out/box-skse (box_files.sh
#            pull) and SKYRIM_PYTHON_BOX=1 (livetest/support.py's box mode). No token in it.
#   down / status: the session tunnel.
#   probe [PORT]: on the box, "LISTEN pid <n>" (exit 0) or "NO_LISTENER" (exit 1) for 127.0.0.1:PORT (default
#            45977). Through the tunnel a missing listener does not look refused, so the live tests ask the box.
#   rewrite: the offline part of up (tested by test_box_tools.sh): IN (or - for stdin) -> OUT; prints
#            "BOXPORT <port>" or "INACTIVE <reason>", never the token.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
SESSION_OUT="$BOX_ROOT/out/box-session.json"
ENV_OUT="$BOX_ROOT/out/box-live.env"
FWD_STATE="$SKYRIM_PYTHON_STATE/box-session.fwd"

# The rewrite program (python3 -c, so stdin stays free for the session JSON).
REWRITE_PY="$(cat <<'PY'
import json, os, sys
src, out, local = sys.argv[1:4]
data = json.load(sys.stdin if src == "-" else open(src, encoding="utf-8"))
if not isinstance(data, dict):
    sys.exit("box_session: session.json is not an object")
def port(v, what):
    if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 65536:
        sys.exit(f"box_session: bad {what}: {v!r}")
    return v
if data.get("active") is False:
    msg = f"INACTIVE {data.get('reason')}"
else:
    box_port = port(data.get("port"), "port in session.json")
    if not isinstance(data.get("token"), str):
        sys.exit("box_session: session.json has no token")
    data["port"] = box_port if local == "same" else port(int(local) if local.isdigit() else local, "local port")
    if "host" in data:
        data["host"] = "127.0.0.1"
    msg = f"BOXPORT {box_port}"
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f)
print(msg)
PY
)"
rewrite() { python3 -c "$REWRITE_PY" "$1" "$2" "$3"; }  # rewrite <in|-> <out> <local port|same>

write_env() {
  mkdir -p "$BOX_ROOT/out"
  cat > "$ENV_OUT" <<ENV
# Written by tools/box/box_session.sh: the live tests against the game on the box. No token here.
export SKYRIM_PYTHON_SESSION="$SESSION_OUT"
export SKSE_LOG_DIR="$BOX_ROOT/out/box-skse"
export SKYRIM_PYTHON_LOG="$BOX_ROOT/out/box-skse/SkyrimPython/python.log"
export SKYRIM_PYTHON_BOX=1
ENV
  echo "env: source $ENV_OUT"
}

case "${1:-}" in
  rewrite) rewrite "${2:?IN}" "${3:?OUT}" "${4:?LOCAL_PORT}" ;;
  env) write_env ;;
  up)
    write_env
    mkdir -p "$SKYRIM_PYTHON_STATE"
    st="$(bps 60 files.ps1 -Mode session | tr -d '\r')" || box_die "could not read the box's session.json: ${st%% *}"
    case "$st" in
      "NEW "*) ;;
      UNCHANGED) box_die "session.json was not written in this launch (wait for 'python host: running' in SkyrimPython.log)" ;;
      ABSENT) box_die "no session.json on the box (python host not running yet, or disabled)" ;;
      *) box_die "session.json: ${st%% *}" ;;
    esac
    res="$(printf '%s' "${st#NEW }" | base64 -d | rewrite - "$SESSION_OUT" "${BOX_SESSION_LOCAL_PORT:-same}")" || box_die "rewrite failed"
    echo "$res"
    case "$res" in
      "INACTIVE "*) echo "the bridge is not serving; no tunnel" ;;
      "BOXPORT "*)
        PORT="${res#BOXPORT }"; LOCAL="${BOX_SESSION_LOCAL_PORT:-$PORT}"
        export TUNNEL_SOCK="$BOX_SESSION_SOCK"
        if btunnel_ctl check 2>/dev/null; then
          if [ "$(cat "$FWD_STATE" 2>/dev/null)" = "$LOCAL $PORT" ]; then echo "session tunnel already up: 127.0.0.1:$LOCAL -> box 127.0.0.1:$PORT"
          else btunnel_ctl exit; fi
        fi
        if ! btunnel_ctl check 2>/dev/null; then
          btunnel_start -L "127.0.0.1:$LOCAL:127.0.0.1:$PORT" || box_die "session tunnel did not come up (local port $LOCAL in use, or the box is unreachable)"
          echo "$LOCAL $PORT" > "$FWD_STATE"
          echo "session tunnel up: 127.0.0.1:$LOCAL -> box 127.0.0.1:$PORT"
        fi ;;
    esac
    echo "$SESSION_OUT"
    ;;
  down)
    export TUNNEL_SOCK="$BOX_SESSION_SOCK"
    if btunnel_ctl check 2>/dev/null; then btunnel_ctl exit; echo "session tunnel down"; else echo "session tunnel not running"; fi ;;
  status)
    export TUNNEL_SOCK="$BOX_SESSION_SOCK"; btunnel_ctl check 2>&1 || true ;;
  probe)
    p="${2:-45977}"; [[ "$p" =~ ^[0-9]+$ ]] || box_die "probe: bad port '$p'"
    r="$(bssh "\$c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if (\$c) { 'LISTEN pid ' + \$c.OwningProcess } else { 'NO_LISTENER' }" | tr -d '\r')"
    echo "$r"; case "$r" in "LISTEN "*) exit 0 ;; *) exit 1 ;; esac ;;
  *) box_die "usage: box_session.sh up | env | down | status | probe [PORT] | rewrite IN|- OUT LOCAL_PORT|same" ;;
esac
```

`tools/box/box_files.sh` (Part C): the live tests' local mirror of the box's logs and smoke files (text written since the launch stamp only), and the one file Linux writes on the box, `livetest_control.json`:
```bash
#!/usr/bin/env bash
# Usage: tools/box/box_files.sh pull NAME... | put-control LOCAL_FILE
#   pull: for each NAME relative to the box's SKSE log folder (SkyrimPython.log, SkyrimPythonLoader.log,
#         SkyrimPython/python.log, SkyrimPython/livetest_smoke.json, ...), writes the text written there since the last
#         box_launch.sh stamp to out/box-skse/NAME and prints that local path. The file is empty when nothing was
#         written in this launch (or it is absent), so a log or result from an earlier launch never counts. Never
#         session.json (the token stays only in box_session.sh's file). The live tests' box mode
#         (SKYRIM_PYTHON_BOX=1, livetest/support.py) calls this before every log read.
#   put-control: copies LOCAL_FILE to the box's SKSE\SkyrimPython\livetest_control.json, the only file Linux writes
#         there (livetest/smoke.py's command channel, Task 31).
#   BOX_FILES_PRINT=1 validates and prints the local targets (pull) and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
MIRROR="$BOX_ROOT/out/box-skse"
case "${1:-}" in
  pull)
    shift; [ $# -ge 1 ] || box_die "usage: box_files.sh pull NAME..."
    for n in "$@"; do
      case "$n" in ""|*..*|*:*|/*|\\*|*session.json) box_die "pull: refused name '$n'" ;; esac
    done
    if [ "${BOX_FILES_PRINT:-0}" = 1 ]; then for n in "$@"; do echo "$MIRROR/${n//\\//}"; done; exit 0; fi
    out="$(bps 120 files.ps1 -Mode new -NamesB64 "$(b64 "$(printf '%s\n' "$@")")" | tr -d '\r')" || box_die "pull failed: $out"
    while IFS=$'\t' read -r n state data; do
      [ -n "$n" ] || continue
      local_path="$MIRROR/${n//\\//}"; mkdir -p "$(dirname "$local_path")"
      case "$state" in
        NEW) printf '%s' "$data" | base64 -d > "$local_path" ;;
        UNCHANGED|ABSENT) : > "$local_path" ;;
        *) box_die "pull $n: $state" ;;
      esac
      echo "$local_path"
    done <<< "$out"
    ;;
  put-control)
    f="${2:?usage: box_files.sh put-control LOCAL_FILE}"; [ -f "$f" ] || box_die "no such file: $f"
    r="$(bps 60 files.ps1 -Mode put -NameB64 "$(b64 'SkyrimPython/livetest_control.json')" -DataB64 "$(base64 -w0 < "$f")" | tr -d '\r')"
    [ "$r" = 'PUT SkyrimPython\livetest_control.json' ] || box_die "put-control: '$r'"
    ;;
  *) box_die "usage: box_files.sh pull NAME... | put-control LOCAL_FILE" ;;
esac
```

`tools/box/box_saves.sh` (Part C): read-only count of a save name's `.ess` files, before and after a throwaway save:
```bash
#!/usr/bin/env bash
# Usage: tools/box/box_saves.sh count NAME
#   Prints how many saves in the box's Saves folder match NAME*.ess (read-only). Used before a new throwaway save
#   (must print 0: never save over an existing name) and after it (the .ess was written). NAME is [A-Za-z0-9_]
#   only, checked before any ssh.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
[ "${1:-}" = count ] || box_die "usage: box_saves.sh count NAME"
name="${2:?usage: box_saves.sh count NAME}"
[[ "$name" =~ ^[A-Za-z0-9_]+$ ]] || box_die "save name must be [A-Za-z0-9_]+ (got '$name')"
bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter '$name*.ess' -ErrorAction SilentlyContinue).Count" | tr -d '\r'
```

- [ ] **Step 3: Write the box scripts**

`tools/box/win/launch.ps1` (the scheduled task's action). Steam's logon is detected by a **new** `[Logged On, ...] RecvMsgClientLogOnResponse() : processing complete` line in `C:\Steam\logs\connection_log.txt` (compared with the last such line before Steam was started, so a rotated log still works), not by a fixed sleep:
```powershell
# launch.ps1: run by the scheduled task SkyrimPythonGame (interactive, as geoff, on demand only).
# 1. refuse if the game or loader runs, or if the Stock Game exe is not 1.6.1170.0;
# 2. Steam: start it with -silent if it is not running, then wait (bounded, 120 s) for a NEW
#    "[Logged On ...] RecvMsgClientLogOnResponse() : processing complete" line in connection_log.txt
#    (if Steam already runs and its latest state line is "Logged On", no wait);
# 3. apply launch.env (box_launch.sh --env; only SKYRIM_PYTHON_TEST_* lines) to this process's environment, which
#    skse64_loader.exe and the game inherit, then empty the file so it is used by one launch only;
# 4. start skse64_loader.exe from the game folder; if no SkyrimSE process 15 s later, retry once.
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

$envFile = Join-Path $box 'launch.env'
if (Test-Path $envFile) {
  foreach ($line in (Get-Content -Encoding UTF8 $envFile)) {
    if ($line -match '^(SKYRIM_PYTHON_TEST_[A-Z0-9_]+)=(.*)$') { Set-Item -Path ('Env:' + $Matches[1]) -Value $Matches[2]; Log "env $($Matches[1])=$($Matches[2])" }
    elseif ($line.Trim()) { Log "ignored launch.env line: $line" }
  }
  Set-Content -Encoding ASCII -NoNewline -Path $envFile -Value ''
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
# stop.ps1: force-stop the game and its loader, by process name only, in two stages with a bounded check after each:
#   1. Stop-Process -Name SkyrimSE,skse64_loader -Force                     -> "GONE via Stop-Process"
#   2. if either is still alive: CIM Win32_Process.Terminate, matched by the exact image names
#      SkyrimSE.exe and skse64_loader.exe                                   -> "GONE via CIM Terminate"
#   otherwise "STILL_RUNNING" (exit 1): STOP and ask the owner.
# Stage 2 exists because on 2026-10-01 a game hung at exit (one thread in a kernel wait) survived Stop-Process and
# taskkill /F, and Win32_Process.Terminate ended it.
function Alive { Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue }
function WaitGone([int]$Seconds) {
  $end = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $end) { if (-not (Alive)) { return $true }; Start-Sleep -Seconds 1 }
  return (-not (Alive))
}
$p = Alive
if (-not $p) { "NOTHING_RUNNING"; exit 0 }
$p | ForEach-Object { "STOPPING $($_.Name) $($_.Id)" }
Stop-Process -Name SkyrimSE,skse64_loader -Force -ErrorAction SilentlyContinue
if (WaitGone 20) { "GONE via Stop-Process"; exit 0 }
(Alive) | ForEach-Object { "still alive after Stop-Process: $($_.Name) $($_.Id); trying CIM Terminate" }
Get-CimInstance Win32_Process -Filter "Name='SkyrimSE.exe'" | Invoke-CimMethod -MethodName Terminate | ForEach-Object { "CIM Terminate SkyrimSE.exe returned $($_.ReturnValue)" }
Get-CimInstance Win32_Process -Filter "Name='skse64_loader.exe'" | Invoke-CimMethod -MethodName Terminate | ForEach-Object { "CIM Terminate skse64_loader.exe returned $($_.ReturnValue)" }
if (WaitGone 20) { "GONE via CIM Terminate"; exit 0 }
"STILL_RUNNING after Stop-Process and CIM Terminate: " + (((Alive) | ForEach-Object { "$($_.Name) $($_.Id)" }) -join ', ')
"STOP and ask the owner."; exit 1
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
# place.ps1 -Mode exists|list|place|move -DestB64 <path> [-SrcB64 <staged file or .tar | path to move>] [-Kind file|tar]
#   exists: prints EXISTS or ABSENT.   list: prints the names of the entries in a folder (nothing if absent).
#   place:  file -> Copy-Item to Dest (parent created), prints "PLACED <sha256>";
#           tar  -> extracts into a fresh folder next to the .tar, then robocopy /E merges it into Dest (
#                   files with the same name are replaced, nothing else in Dest is touched), prints "PLACED <n> files".
#   move:   Move-Item Src to Dest (no -Force), both inside Data\SKSE\Plugins; refuses when Src is absent or Dest
#           exists; prints "MOVED". Used for Task 26's startup variants (moved, never deleted, moved back after).
# Only box_deploy.sh calls this, for paths under the game's Data\SKSE\Plugins and for Data\Scripts\SkyrimPython.pex.
param([string]$Mode, [string]$DestB64, [string]$SrcB64 = '', [string]$Kind = 'file')
$ErrorActionPreference = 'Stop'
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
$dest = From-B64 $DestB64
# Allowed destinations: the Data\SKSE\Plugins tree, and exactly one file outside it, the spike's compiled Papyrus
# script Data\Scripts\SkyrimPython.pex (Task 7; box_deploy.sh --pex).
$PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'
if (-not $dest.StartsWith('C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\') -and $dest -ne $PexDest) { "REFUSED $dest is outside Data\SKSE\Plugins and is not $PexDest"; exit 2 }
switch ($Mode) {
  'exists' { if (Test-Path -LiteralPath $dest) { 'EXISTS' } else { 'ABSENT' }; exit 0 }
  'list'   { if (Test-Path -LiteralPath $dest) { Get-ChildItem -LiteralPath $dest -Force | ForEach-Object { $_.Name } }; exit 0 }
  'move' {
    $src = From-B64 $SrcB64
    $root = 'C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\'
    foreach ($p in @($src, $dest)) {
      if (-not ([IO.Path]::GetFullPath($p)).StartsWith($root) -or $p -match '\.\.') { "REFUSED $p is not inside Data\SKSE\Plugins"; exit 2 }
    }
    if (-not (Test-Path -LiteralPath $src)) { "ERR source absent $src"; exit 1 }
    if (Test-Path -LiteralPath $dest) { "ERR destination exists $dest"; exit 1 }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Move-Item -LiteralPath $src -Destination $dest
    'MOVED'; exit 0
  }
  'place' {
    if ($dest -eq $PexDest -and $Kind -ne 'file') { "REFUSED $PexDest takes a single file"; exit 2 }
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
"== throwaway saves (spikebase, spytest, s4test, skypy_r38_savecall) =="
Get-ChildItem (Join-Path $my 'Saves') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(spikebase|spytest|s4test|skypy_r38_savecall)' } | ForEach-Object { Show $_.FullName }
''
'Nothing was removed. Show this list to the owner; remove only what the owner signs off on, item by item.'
```

`tools/box/win/files.ps1` (Part C; `box_files.sh` and `box_session.sh` call it):
```powershell
# files.ps1 -Mode new -NamesB64 <base64 of newline-separated names> | -Mode session | -Mode put -NameB64 <n> -DataB64 <c>
#   new: for each name (relative to the SKSE log folder), one line "<name>`t<state>`t<base64>": state NEW (the text
#        written since the last launch stamp, see snapshot_lib.ps1; base64 of its UTF-8), UNCHANGED (nothing written
#        since the stamp), ABSENT, or REFUSED (a path with '..', a drive or a leading slash, or session.json).
#        Exit 3 with NO_STAMP when no launch was stamped. A file rewritten since the stamp with exactly the bytes it
#        had then (same length, same hash) reads as UNCHANGED: snapshot_lib cannot tell it from an untouched file.
#   session: the same for SkyrimPython\session.json alone (box_session.sh; it holds the bridge token, so only that
#        script reads it, and only into out/box-session.json).
#   put: writes exactly one file, SkyrimPython\livetest_control.json (livetest/smoke.py's command channel; Task 31),
#        through a temp file and an atomic replace. Any other name is refused. Nothing is ever deleted.
param([string]$Mode, [string]$NamesB64 = '', [string]$NameB64 = '', [string]$DataB64 = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$PutAllowed = 'SkyrimPython\livetest_control.json'
function Bad-Name([string]$n) { return ($n -match '\.\.' -or $n -match ':' -or $n -match '^[\\/]' -or $n -match 'session\.json$') }
switch ($Mode) {
  'new' {
    $snap = Get-Snapshot
    if (-not $snap) { 'NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)'; exit 3 }
    foreach ($n in ((From-B64 $NamesB64) -split "`n" | Where-Object { $_ })) {
      $rel = $n -replace '/', '\'
      if (Bad-Name $rel) { "$n`tREFUSED`t"; continue }
      $t = $null
      try { $t = Get-NewText $rel $snap } catch { $t = $null }
      if ($null -eq $t) { "$n`tABSENT`t" }
      elseif ($t -eq '') { "$n`tUNCHANGED`t" }
      else { "$n`tNEW`t" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t)) }
    }
    exit 0
  }
  'session' {
    $snap = Get-Snapshot
    if (-not $snap) { 'NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)'; exit 3 }
    $t = $null
    try { $t = Get-NewText 'SkyrimPython\session.json' $snap } catch { $t = $null }
    if ($null -eq $t) { 'ABSENT' } elseif ($t -eq '') { 'UNCHANGED' } else { 'NEW ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t)) }
    exit 0
  }
  'put' {
    $rel = (From-B64 $NameB64) -replace '/', '\'
    if ($rel -ne $PutAllowed) { "REFUSED $rel (only $PutAllowed may be written)"; exit 2 }
    $dest = Join-Path $LogDir $rel
    $tmp = $dest + '.linux-tmp'
    [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($DataB64))
    # The in-game script may be reading the file at that moment: a few short retries.
    for ($i = 1; $i -le 10; $i++) {
      try {
        if (Test-Path -LiteralPath $dest) { [IO.File]::Replace($tmp, $dest, $null) } else { [IO.File]::Move($tmp, $dest) }
        "PUT $rel"; exit 0
      } catch { if ($i -eq 10) { "ERR $($_.Exception.Message)"; exit 1 }; Start-Sleep -Milliseconds 100 }
    }
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
```

- [ ] **Step 4: Run the offline test**

Run: `chmod +x tools/box/*.sh && tools/box/test_box_tools.sh; echo rc=$?`
Expected: every line `ok ...` (the first ones `BOX_OFFLINE=1: ... refuses`, the last one `no ssh/scp call reached PATH`), then `BOX TOOLS OFFLINE PASS`, `rc=0`. If the offline guard check fails, the test stops at once (`TEST ABORTED`) and nothing else runs.

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
Expected: `parsed <name>.ps1` for all ten scripts (eleven since ruling R42 added `files.ps1`), `INSTALLED`, `REGISTERED SkyrimPythonGame -> powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\dev\skyrim-python-box\launch.ps1 as geoff Interactive` (or `EXISTS SkyrimPythonGame -> ...` on a re-run); three `deployed ...` lines (the runtime as `PLACED 38 files`); `out/box-deployed.txt` holds `...\Plugins\SkyrimPythonSpike.dll`, `...\Plugins\SkyrimPython\python`, `...\Plugins\SkyrimPython\SpikePyCore.dll`; then `box: C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\devbench.dll exists on the box but is not in out/box-deployed.txt; not overwriting it. Ask the owner.` and `rc=1`.

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
Expected for a clean exit: DevBench's reply to `qqq` (or `(no reply to qqq; ...)`), `EXITED`, `NO_NEW_CRASH_LOG`, `CLEAN_EXIT`, `rc=0`; `tunnel down`. **2026-10-01 result: `STILL_RUNNING after 120s`, `NO_NEW_CRASH_LOG`, `NOT_CLEAN`** (see "Known issue" below). On `NOT_CLEAN`, run `tools/box/box_stop.sh`: it prints `GONE via Stop-Process` or `GONE via CIM Terminate`. Only if it prints `STILL_RUNNING` after both stages, STOP and ask the owner (do not retry, do not reboot the box yourself).

**Known issue (2026-10-01): the game hangs at exit on the box.** After `qqq` the game saved SKSE's co-save state (`skse64.log` ends with `cleared save path`), DevBench's server stopped, and the process sat with one thread in a kernel wait (no CPU, working set falling to 80 MB), still holding the spike's 45978 listener (`Listen` and `CloseWait`). `Stop-Process -Force` and `taskkill /F` did not end it; `Get-CimInstance Win32_Process -Filter "ProcessId=11312" | Invoke-CimMethod -MethodName Terminate` did (no reboot), which is why `box_stop.sh` now falls back to CIM Terminate by exact image name. Python was not loaded in that run (no `py_load`; neither `python314.dll` nor `SpikePyCore.dll` among the process's modules), so the embedded interpreter is not the cause; the other SKSE plugins in that Stock Game were loaded, so the cause is open. Whether the box's game also hangs at exit without `SkyrimPythonSpike.dll` is not known (testing it means taking our DLL out of `Plugins`, which needs the owner's sign-off). Until the owner decides, a box `NOT_CLEAN` with no new crash log is a **baseline question**, not an S8 verdict: Task 3's clean-exit criterion needs a baseline run without our DLLs first.

- [ ] **Step 7: Commit**

```bash
git add tools/box docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded/task-01b-box-game-harness.md
git commit -m "chore(tools): game harness on the Windows box"
```

## Substitutions for Tasks 3-10

From Task 3's remaining runs on, read every Linux game step in Tasks 3-10 through this table. Run `tools/box/box_tunnel.sh up` once after each launch (it survives a game restart; bring it down at the end of the session). Nothing else in those tasks changes: the spike DLLs, `remote_build.sh`, the clients and the pass criteria stay as written. Commands that need a pipe are in the numbered blocks under the table (B1, B2, ...); the table names the block.

| Linux step in Tasks 3-10 | On the box |
|---|---|
| `source tools/env.sh` (`SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`, `SKYRIM_PYTHON_STATE`) | `source tools/box/box_env.sh` (`BOX_DATA`, `BOX_PLUGINS`, `BOX_OWN`, `BOX_SKSE_LOG_DIR`, `BOX_SAVES_DIR`, `BOX_MYGAMES`, `SKYRIM_PYTHON_STATE`). Box paths are Windows paths: read them with `bssh "Get-Content '...'"`, never with Linux tools. |
| Task 3 Step 5 and Task 10 Step 4: edits to the Linux `tools/deploy.sh` (`--python`, `--site-packages`) | Skip them on the box path: `tools/box/box_deploy.sh` already has `--python` and `--site-packages`. (`tools/deploy.sh` already carries Task 3's `--python` from the Linux run; leave it as it is.) |
| `CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll [ini]` | `CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh out/SkyrimPythonSpike.dll [ini]` (record: `out/box-deployed.txt`) |
| `tools/deploy.sh --python out/SpikePyCore.dll` (Task 3) | `CONFIRM_THROWAWAY_SAVE=1 tools/box/box_deploy.sh --python out/SpikePyCore.dll` |
| `tools/deploy.sh --site-packages out/site-packages` (Task 10) | `tools/box/box_deploy.sh --site-packages out/site-packages` |
| `--package`, `--scripts DIR` (Task 26, later plan) | `tools/box/box_deploy.sh --package`, `tools/box/box_deploy.sh --scripts DIR` |
| `cp ... "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/<f>"` and append to `out/deployed.txt` (Task 10's `s10_check.py`) | `tools/box/box_deploy.sh --file spikes/deps/s10_check.py s10_check.py` |
| `touch "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/spike-autostart"` (Task 3 run B) | `: > out/spike-autostart && tools/box/box_deploy.sh --file out/spike-autostart spike-autostart` (an empty marker file) |
| Task 7 Step 2: compile `SkyrimPython.psc` to `.pex` | Unchanged: compile on Linux exactly as Task 7 says (the Creation Kit's Papyrus compiler under Wine; it opens no window and takes no focus). Only the game runs on the box. |
| Task 7: `cp spikes/spike-dll/papyrus/compiled/SkyrimPython.pex "$SIMPLESKYRIM_DATA/Scripts/SkyrimPython.pex"` and append to `out/deployed.txt` | `tools/box/box_deploy.sh --pex spikes/spike-dll/papyrus/compiled/SkyrimPython.pex`: places exactly `C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex`, only if it is absent or was deployed by this tool, and records it in `out/box-deployed.txt`. |
| Task 8 (S6, load a save through `AddUITask`) | Runs on the box as written through this table, and is recorded as a **Windows-native data point** ("S6 on the Windows box, native"), not as the S6 verdict. The Proton S6 run stays pending until the Linux machine is idle; the S6 row says both. |
| `tools/launch_game.sh` (and `LAUNCH_DRY_RUN=1`) | `tools/box/box_launch.sh` (no dry run; it refuses while the game runs and prints the launch log) |
| "quit the game first (console: `qqq`)" | `tools/box/box_quit.sh`; on `NOT_CLEAN`, `tools/box/box_stop.sh` (Stop-Process, then CIM Terminate by exact name; `STILL_RUNNING` after both: STOP and ask the owner). See Known issue in Step 6. |
| `SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/<log>" "<regex>" [s]` | `tools/box/box_wait_log.sh <log> "<regex>" [s]` with `<log>` relative to the SKSE log folder, e.g. `SkyrimPythonSpike.log`, `SkyrimPython/python.log`. The launch stamp is implicit. |
| `grep -E '<re>' "$SKSE_LOG_DIR/SkyrimPythonSpike.log"`, `cat` or `tail` of a log | Block B1 |
| `cp "$SKSE_LOG_DIR"/SkyrimPythonSpike_s9_*.txt out/s9/` (Task 5) | Block B2 (`bscp -s`: SFTP mode takes the path with spaces as is; plain scp 8.9 splits it, and quoting it fails with "filename does not match request") |
| Task 5 Step 6: `grep ... iPresentInterval "$(dirname "$SKSE_LOG_DIR")"/Skyrim*.ini` | Block B3. The box's key is `iVSyncPresentInterval` in `SkyrimPrefs.ini` (2026-10-01: `iVSyncPresentInterval=1`, vsync on), so the pattern is `PresentInterval`; no line means the game default 1. |
| Task 5 Step 6: `grep ... "$SIMPLESKYRIM_DATA/SKSE/Plugins/SSEDisplayTweaks.ini"` | Block B4: the same keys in `$BOX_PLUGINS\SSEDisplayTweaks.ini` (prints "no Display Tweaks ini" when it is absent, as on 2026-10-01). The sample header's `displaytweaks=` (the DLL check) stays the verdict input. |
| Task 5 Step 6: `grep ... maxFrameRate "$SIMPLESKYRIM_DATA/../dxvk.conf"`, `DXVK_FRAME_RATE` | No DXVK on the box: record "no DXVK on the box (dxvk.conf present but inert)". Checked 2026-10-01: the game folder has a 43-byte `dxvk.conf` (2026-08-28) whose only setting is `dxvk.enableGraphicsPipelineLibrary = False` (no `maxFrameRate`), and no `d3d11.dll` or `dxgi.dll` beside the exe, so the game runs native D3D11 and that file is never read. The header's `dxvk_frame_rate=` reads `unset`. Block B11 re-checks it. |
| The spike ports 45978 (harness) and 45979 (S8 echo server) on Linux's `127.0.0.1` | The same commands after `tools/box/box_tunnel.sh up`. Through the tunnel a listener that is missing on the box shows as `FAIL: ConnectionError: server closed the connection` (exit 1) instead of `connection refused`, and a listener held by a hung process shows as `FAIL: TIMEOUT` (exit 2). Latency includes the LAN and ssh: compare S1/S8 timings only with box runs. |
| Task 10's Linux HTTP server on 45981, reached from the game | Start it on Linux as written, and bring the tunnel up with `tools/box/box_tunnel.sh up --reverse 45981` (`down` first if it is already up without it). The game's `http://127.0.0.1:45981/s10.txt` then reaches Linux. |
| Task 10's `W="Z:$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython"` and reading `$RESULT` on Linux | `W="C:/ChronicleDev/Stock Game/Data/SKSE/Plugins/SkyrimPython"`; the result file is under `BOX_OWN` (the teardown list shows it); fetch it with block B5, then run Task 10's printing one-liner on `out/s10-result.json`. |
| Manual in-game actions: load the throwaway save (Continue/Load), "wait for gameplay" | `tools/box/box_console.sh "load spikebase"` (DevBench reroutes it to its `game` tool `load`, which also skips the mod-mismatch prompt; not yet exercised on the box), then wait with block B6: `"playerLoaded":true` and only `"HUD Menu"` open (unpaused, no menu; a console command sent through DevBench leaves no console open). |
| Typing a console command | `tools/box/box_console.sh "<command>"`. Output of printing commands: `tools/box/box_console.sh --tool console '{"command":"getpos x","capture":true}'` then `tools/box/box_console.sh --tool console '{"action":"read"}'`. |
| `save <name>` in the console (Task 6 `s4test`, Task 8 `spytest`) | Block B7: check the name is unused (must print 0), save through DevBench, confirm the `.ess`. |
| "Load the save in an interior" (Task 5) | `tools/box/box_console.sh "load spikebase"`, wait with block B6, then `tools/box/box_console.sh "coc QASmoke"` (DevBench's configured quiet test cell) and wait until `tools/box/box_console.sh --tool inspect '{"kind":"scene"}'` names it. |
| "Play for about a minute (walk, open and close the inventory)" (Task 3 soak), any xdotool step | No player input on the box: run the soak with the save loaded and the game unpaused; `tools/box/box_console.sh --tool menu '{"action":"open","name":"InventoryMenu"}'` then `'{"action":"close","name":"InventoryMenu"}'` stands in for the inventory. Record "no player movement (box harness)" in the evidence. |
| `pgrep -x SkyrimSE.exe`, `/proc/$PID/status` `VmRSS` (Task 4) | Block B8 (one PID; the working set in kB stands in for VmRSS) |
| `ss -ltnp` filtered for 45978 (Task 2 check 4) | Block B9 |
| Clean-exit check: `timeout 120 bash -c 'while pgrep -x SkyrimSE.exe ...'`, the `launch.log` tail, `find "$SKSE_LOG_DIR" -newer launch.stamp -iname 'crash*'` | `tools/box/box_quit.sh`: console `qqq` through DevBench, a bounded wait (`QUIT_WAIT`, 120 s) for `SkyrimSE` to end, then any `crash-*.log` in `BOX_SKSE_LOG_DIR` that was not there at the launch stamp. `CLEAN_EXIT` = process gone and no new crash log. Launch log: block B10. |
| `objdump -p out/*.dll` | Unchanged (Linux, on the built DLLs). |
| `tools/teardown_list.sh` | `tools/box/box_teardown_list.sh` (also lists `C:\dev\skyrim-python-box`, the task `SkyrimPythonGame` and the `.pex` if deployed). |
| The runtime note "GE-Proton10-14" in `spikes/RESULTS.md` | Box rows say "Windows 11 box, native" next to the runtime version from the spike log. |

Each block assumes `source tools/box/box_env.sh` has run.

B1, grep or tail a box log:
```bash
bssh "Select-String -Path '$BOX_SKSE_LOG_DIR\\SkyrimPythonSpike.log' -Pattern '<re>' | % Line" | tr -d '\r'
bssh "Get-Content '$BOX_SKSE_LOG_DIR\\SkyrimPythonSpike.log' -Tail 50" | tr -d '\r'
```

B2, fetch Task 5's sample files:
```bash
mkdir -p out/s9
for n in base_a spin combo base_b; do
  bscp -s "$BOX:C:/Users/geoff/Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPythonSpike_s9_$n.txt" "out/s9/SkyrimPythonSpike_s9_$n.txt"
done
```

B3, the present interval in the My Games ini files:
```bash
bssh "Select-String -Path '$BOX_MYGAMES\\Skyrim*.ini' -Pattern PresentInterval | % { \$_.Filename + ': ' + \$_.Line }" | tr -d '\r'
```

B4, Display Tweaks settings:
```bash
bssh "if (Test-Path '$BOX_PLUGINS\\SSEDisplayTweaks.ini') { Select-String -Path '$BOX_PLUGINS\\SSEDisplayTweaks.ini' -Pattern 'EnableVSync|FramerateLimit|PresentInterval' | % Line } else { 'no Display Tweaks ini' }" | tr -d '\r'
```

B5, fetch Task 10's result:
```bash
bscp -s "$BOX:C:/ChronicleDev/Stock Game/Data/SKSE/Plugins/SkyrimPython/s10-result.json" out/s10-result.json
```

B6, wait for gameplay (bounded, about 3 minutes):
```bash
for i in $(seq 1 60); do
  st="$(tools/box/box_console.sh --tool inspect '{"kind":"state"}' 2>&1)"; m="$(tools/box/box_console.sh --tool menu '{"action":"list"}' 2>&1)"
  case "$st" in *'"playerLoaded":true'*) case "$m" in *'"openMenus":["HUD Menu"]'*) break ;; esac ;; esac
  sleep 3
done; echo "$st"; echo "$m"
```

B7, a new save name (never over an existing one):
```bash
bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter '<name>*').Count" | tr -d '\r'      # must print 0
tools/box/box_console.sh "save <name>"
for i in $(seq 1 30); do n="$(bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter '<name>*.ess').Count" | tr -d '\r')"; [ "$n" != 0 ] && break; sleep 2; done; echo "$n"
```

B8, the game's PID and working set (Task 4):
```bash
bssh "Get-Process SkyrimSE | % { 'pid ' + \$_.Id + ' ws_kB ' + [int](\$_.WorkingSet64/1KB) }" | tr -d '\r'
```

B9, who holds 45978:
```bash
bssh "Get-NetTCPConnection -LocalPort 45978 -ErrorAction SilentlyContinue | % { \$_.State.ToString() + ' pid ' + \$_.OwningProcess }" | tr -d '\r'
```

B10, the launch log:
```bash
bssh "Get-Content '$BOX_DIR\\launch.log' -Tail 20" | tr -d '\r'
```

B11, DXVK on the box (expect `dxvk.conf` lines without `maxFrameRate`, then `False` `False`):
```bash
bssh "if (Test-Path '$BOX_GAME_DIR\\dxvk.conf') { Get-Content '$BOX_GAME_DIR\\dxvk.conf' } else { 'no dxvk.conf' }; Test-Path '$BOX_GAME_DIR\\d3d11.dll'; Test-Path '$BOX_GAME_DIR\\dxgi.dll'" | tr -d '\r'
```

## For the controller: add to the plan index (`README.md`)

1. Part A table, after Task 1: `| 1b | [Game harness on the Windows box](task-01b-box-game-harness.md) | Windows box + game | 1 |`, and in "Suggested order" `0 → 1 → 1b → 2 → 3 ...` with a note that Tasks 3-10 run the game on the box via the substitution table in Task 1b.
2. File Structure, under `tools/`: `box/{box_env,box_setup,box_deploy,box_launch,box_stop,box_wait_log,box_tunnel,box_console,box_quit,box_teardown_list,test_box_tools}.sh` and `box/win/{launch,setup_task,stamp,snapshot_lib,wait_log,console,stop,quit_check,place,list}.ps1`.
3. Fixed paths: game on the box `C:\ChronicleDev\Stock Game` (1.6.1170.0, SKSE 2.2.6), plugins `...\Data\SKSE\Plugins`, logs `C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE`, saves `...\Saves`, harness folder `C:\dev\skyrim-python-box`, scheduled task `SkyrimPythonGame`, DevBench `127.0.0.1:8920` on the box.
4. Contracts → Tools: the `tools/box/*` interfaces from this task's Interfaces block.
5. Global Constraints: "The game runs on the Windows box (Task 1b); no step launches the game on the Linux desktop, uses xdotool, or starts Linux Steam."
6. Ruling R30's scope in the index's deploy-safety text: one file outside `Data\SKSE\Plugins` on the box, `Data\Scripts\SkyrimPython.pex`; Task 8's box run is a Windows-native data point and the Proton S6 stays pending.
7. The open question on the box exit hang (Step 6, Known issue) before Task 3's clean-exit criterion is judged on the box.
8. File Structure, under `tools/box/` (ruling R42): `box_session.sh`, `box_files.sh`, `box_saves.sh` and `win/files.ps1`; Contracts → Tools: the Part C additions and `BOX_OFFLINE=1` from this task's Interfaces block.
