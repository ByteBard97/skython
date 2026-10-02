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
    box_quit.sh box_setup.sh box_teardown_list.sh box_session.sh box_files.sh box_saves.sh box_shot.sh)
PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1 files.ps1 shot.ps1)

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

# 6a. two-DLL layout: loader -> Plugins, core -> Plugins\Skython, ini -> Plugins\Skython.ini (no ssh)
P='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'
d2="$(mktemp -d)"; : > "$d2/Skython.dll"; : > "$d2/dev.ini"; sleep 1; : > "$d2/SkythonCore.dll"
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d2/Skython.dll" "$d2/dev.ini" 2>&1)"
want="$(printf '%s\n' "$P\Skython.dll" "$P\Skython\SkythonCore.dll" "$P\Skython.ini")"
[ "$t" = "$want" ] && ok "two-DLL targets: loader, core in Skython\\, ini" || { bad "two-DLL targets:"; echo "$t"; }
touch -d '2000-01-01' "$d2/SkythonCore.dll"
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d2/Skython.dll" >/dev/null 2>&1; then bad "accepted a core older than the loader"; else ok "refuses a core DLL older than the loader"; fi
d3="$(mktemp -d)"; : > "$d3/Skython.dll"
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" "$d3/Skython.dll" >/dev/null 2>&1; then bad "accepted a loader without its core"; else ok "refuses the loader without SkythonCore.dll"; fi

# 6b. --move: recorded moves inside Plugins, only of what box_deploy put there (no ssh in print mode)
rec="$(mktemp)"; printf '%s\n' "$P\Skython\python" "$P\SkyrimPythonSpike.dll" > "$rec"
mv_t() { BOX_DEPLOYED="$rec" BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --move "$@" 2>&1; }
t="$(mv_t Skython/python Skython/python.off)"
[ "$t" = "MOVE $P\Skython\python -> $P\Skython\python.off" ] && ok "--move resolves under Plugins" || bad "--move target: '$t'"
t="$(mv_t 'Skython\python\python314.zip' 'Skython\python\python314.zip.off')"
[ "$t" = "MOVE $P\Skython\python\python314.zip -> $P\Skython\python\python314.zip.off" ] && ok "--move of a file inside a recorded folder" || bad "--move inside a recorded folder: '$t'"
for args in "devbench.dll devbench.dll.off" "Skython/../../x Skython/y" "Skython/python C:/elsewhere" "Skython/python Skython/python" "Skython/python"; do
  # shellcheck disable=SC2086
  if mv_t $args >/dev/null; then bad "--move accepted: $args"; else ok "--move refuses: $args"; fi
done
t="$(BOX_DEPLOYED=/nonexistent/box-deployed.txt BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --move Skython/python Skython/python.off 2>&1)"
case "$t" in *"no deploy record"*) ok "--move refuses clearly when the deploy record is missing" ;; *) bad "--move with no deploy record: '$t'" ;; esac
[ -e /nonexistent/box-deployed.txt ] && bad "print mode created the deploy record" || true
t="$(BOX_DEPLOYED=/tmp/elsewhere.txt bash -c 'source "$1/box_env.sh"; echo "$BOX_DEPLOYED"' _ "$B")"
[ "$t" = "$ROOT/out/box-deployed.txt" ] && ok "BOX_DEPLOYED override ignored outside print mode" || bad "BOX_DEPLOYED override honoured outside print mode: '$t'"
grep -qE '^\s*Move-Item -LiteralPath \$src -Destination \$dest$' "$B/win/place.ps1" 2>/dev/null && ok "place.ps1 move: Move-Item without -Force" || bad "place.ps1 has no plain Move-Item for -Mode move"
if grep -hE 'Move-Item' "$B"/win/*.ps1 | grep -vE '^\s*#' | grep -q -- '-Force'; then bad "Move-Item -Force somewhere"; else ok "no Move-Item -Force"; fi
grep -qF '"ERR destination exists' "$B/win/place.ps1" 2>/dev/null && ok "place.ps1 move refuses an existing destination" || bad "place.ps1 move does not refuse an existing destination"
grep -qF 'box-moves.txt' "$B/box_deploy.sh" 2>/dev/null && ok "moves are recorded in out/box-moves.txt" || bad "moves are not recorded"

# 6c. box_launch.sh --env: only SKYTHON_TEST_* names, written for every launch (no ssh in print mode)
t="$(BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" --env SKYTHON_TEST_UNVERIFIED_RUNTIME=1 --env 'SKYTHON_TEST_PRELOAD=C:\a b\python314.dll' 2>&1)"
[ "$t" = "$(printf '%s\n' SKYTHON_TEST_UNVERIFIED_RUNTIME=1 'SKYTHON_TEST_PRELOAD=C:\a b\python314.dll')" ] && ok "--env writes the allow-listed names" || bad "--env content: '$t'"
t="$(BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" 2>&1)"; [ -z "$t" ] && ok "no --env: launch.env is empty (nothing leaks into the next launch)" || bad "launch.env without --env: '$t'"
if BOX_LAUNCH_PRINT_ENV=1 "$B/box_launch.sh" --env PATH=/x >/dev/null 2>&1; then bad "--env accepted PATH"; else ok "--env refuses a name outside SKYTHON_TEST_*"; fi
grep -qF "'^(SKYTHON_TEST_[A-Z0-9_]+)=(.*)\$'" "$B/win/launch.ps1" 2>/dev/null && ok "launch.ps1 applies only SKYTHON_TEST_* lines" || bad "launch.ps1 does not filter launch.env"
# 6c2. launch.ps1 Steam logoff (ruling A7): detects LoggedOff / "Logged In Elsewhere", retries the loader at most
#      once, re-ensures Steam only by starting it -silent, and names the reason in the RESULT line
L="$B/win/launch.ps1"; lc="$(grep -vE '^\s*#' "$L" 2>/dev/null)"
grep -qF "\$logoffPat = 'LoggedOff|Logged In Elsewhere'" "$L" 2>/dev/null && ok "launch.ps1 matches LoggedOff / Logged In Elsewhere" || bad "launch.ps1 has no logoff pattern"
[ "$(echo "$lc" | grep -cE '^\s*foreach \(\$attempt in 1, 2\)')" = 1 ] && [ "$(echo "$lc" | grep -cE 'skse64_loader\.exe.*-WorkingDirectory')" = 1 ] \
  && ! echo "$lc" | grep -qE 'attempt in 1, 2, |while \(\$true\)|\$attempt(\+\+|--| *[-+]?= )' \
  && ok "launch.ps1 starts the loader in one 1, 2 loop (one retry at most)" || bad "launch.ps1 retry is not bounded to one"
grep -qF "steam logged off during launch: Logged In Elsewhere" "$L" 2>/dev/null && grep -qF 'retrying once' "$L" \
  && echo "$lc" | grep -qE 'Result "fail [^"]*\(\$reason\)"' && ok "launch.ps1 warns, retries once and puts the logoff reason in RESULT" || bad "launch.ps1 logoff warning/RESULT reason"
[ "$(echo "$lc" | grep -cE '^\s*\$why = EnsureSteam (120|90)$')" = 2 ] && ok "launch.ps1 re-ensures Steam before the retry (bounded 90 s)" || bad "launch.ps1 EnsureSteam calls"
st="$(echo "$lc" | grep -iE 'steam' | grep -iE 'Stop-Process|Kill|Terminate|taskkill|-shutdown|Set-Content|Out-File|Move-Item')"
[ -z "$st" ] && [ "$(echo "$lc" | grep -cF "Start-Process 'C:\Steam\steam.exe' -ArgumentList '-silent'")" = 2 ] \
  && ok "launch.ps1 only starts Steam -silent, never stops or modifies it" || { bad "launch.ps1 touches Steam beyond -silent start:"; echo "$st"; }

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
ign=1; for f in out/box-session.json out/box-live.env out/box-skse/Skython.log; do git -C "$ROOT" check-ignore -q "$f" || ign=0; done
grep -qE '^out/$' "$ROOT/.gitignore" && [ "$ign" = 1 ] && ok "out/ (session file, env file, log mirror) is gitignored" || bad "out/box-session.json is not gitignored"

# 6e. box_files.sh: pull never takes session.json; put writes only livetest_control.json (no ssh in print mode)
for n in Skython/session.json ../x 'C:/x' /abs; do
  if BOX_FILES_PRINT=1 "$B/box_files.sh" pull "$n" >/dev/null 2>&1; then bad "pull accepted '$n'"; else ok "pull refuses '$n'"; fi
done
t="$(BOX_FILES_PRINT=1 "$B/box_files.sh" pull Skython.log Skython/python.log 2>&1)"
[ "$t" = "$(printf '%s\n' "$ROOT/out/box-skse/Skython.log" "$ROOT/out/box-skse/Skython/python.log")" ] && ok "pull mirrors into out/box-skse" || bad "pull targets: '$t'"
grep -qF "\$PutAllowed = 'Skython\livetest_control.json'" "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 put allow-list: livetest_control.json only" || bad "files.ps1 put allow-list"
grep -qF 'Get-NewText' "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 pull uses the launch-stamp snapshot" || bad "files.ps1 does not use Get-NewText"

# 6f. box_saves.sh: a save name is checked before any ssh
if "$B/box_saves.sh" count 'bad name*' >/dev/null 2>&1; then bad "box_saves.sh accepted 'bad name*'"; else ok "box_saves.sh refuses a name that is not [A-Za-z0-9_]"; fi

# 6g. the teardown list shows Task 31's save
grep -qF 'spikebase|spytest|s4test|skypy_r38_savecall' "$B/win/list.ps1" 2>/dev/null && ok "list.ps1 lists skypy_r38_savecall" || bad "list.ps1 does not list skypy_r38_savecall"

# 6h. the SKSE modal dialog (R50): wait_log.ps1 exits 2 with DIALOG; box_shot.sh and its task
wl="$B/win/wait_log.ps1"
grep -qF 'Start-ScheduledTask -TaskName SkyrimPythonShot' "$wl" 2>/dev/null && grep -qF "'^\s*SkyrimSE\s.*SKSE Plugin Loader'" "$wl" \
  && grep -qF '"DIALOG $dlg"' "$wl" && grep -qE '^\s*exit 2$' "$wl" && grep -qF 'LastWriteTimeUtc -gt $t0' "$wl" \
  && ok "wait_log.ps1: SkyrimPythonShot probe, new windows.txt with SKSE Plugin Loader -> DIALOG, exit 2" || bad "wait_log.ps1 has no DIALOG probe"
grep -vE '^\s*#' "$wl" | grep -q 'MainWindowTitle' && bad "wait_log.ps1 still reads MainWindowTitle over ssh (always empty there)" || ok "wait_log.ps1 does not rely on MainWindowTitle over ssh"
grep -qF '$nextProbe = (Get-Date).AddSeconds(15)' "$wl" && grep -qF '} catch { }' "$wl" && ok "dialog probe every 15 s, never fails the wait" || bad "dialog probe cadence/guard"
grep -qF "Register-Ours 'SkyrimPythonShot' 'shot.ps1'" "$B/win/setup_task.ps1" 2>/dev/null && grep -qF "Register-Ours 'SkyrimPythonGame' 'launch.ps1'" "$B/win/setup_task.ps1" \
  && ok "setup_task.ps1 registers SkyrimPythonGame and SkyrimPythonShot" || bad "setup_task.ps1 task list"
grep -qF "'SkyrimPythonGame','SkyrimPythonShot'" "$B/win/list.ps1" 2>/dev/null && ok "list.ps1 lists both tasks" || bad "list.ps1 does not list SkyrimPythonShot"
w="$(grep -E '\.Save\(|Set-Content' "$B/win/shot.ps1" 2>/dev/null | grep -vE '^\s*#')"
[ "$(echo "$w" | grep -c .)" = 2 ] && ! echo "$w" | grep -qvE "C:\\\\dev\\\\skyrim-python-box\\\\(shot\.png|windows\.txt)" \
  && ok "shot.ps1 writes only shot.png and windows.txt in C:\\dev\\skyrim-python-box" || { bad "shot.ps1 write targets:"; echo "$w"; }
sst="$(mktemp -d)"
t="$(SKYTHON_STATE="$sst" BOX_SHOT_PRINT=1 "$B/box_shot.sh" 2>&1)"
[ "$t" = "$sst/box-shot.png" ] && ok "box_shot.sh default target \$SKYTHON_STATE/box-shot.png" || bad "box_shot.sh default target: '$t'"
t="$(SKYTHON_STATE="$sst" BOX_SHOT_PRINT=1 "$B/box_shot.sh" "$sst/x/y.png" 2>&1)"
[ "$t" = "$sst/x/y.png" ] && ok "box_shot.sh takes a target as \$1" || bad "box_shot.sh \$1 target: '$t'"
t="$(SKYTHON_STATE="$sst" "$B/box_shot.sh" 2>&1)"; rc=$?
[ "$rc" != 0 ] && case "$t" in *"BOX_OFFLINE: refusing"*) true ;; *) false ;; esac && [ -z "$(ls -A "$sst")" ] \
  && ok "box_shot.sh refuses under BOX_OFFLINE (rc=$rc), writes nothing" || bad "box_shot.sh offline: rc=$rc '$t'"

# 7. nothing reached ssh or scp during the whole run
if [ -e "$TRAPLOG" ]; then bad "ssh/scp was called during the offline test:"; cat "$TRAPLOG"; else ok "no ssh/scp call reached PATH"; fi

[ "$fail" = 0 ] && echo "BOX TOOLS OFFLINE PASS" || echo "BOX TOOLS OFFLINE FAIL"
exit "$fail"
