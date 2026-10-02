#!/usr/bin/env bash
# Offline checks for the Windows-box game harness (no ssh, no game). Exit 0 = all pass.
# 1. every tools/box/*.sh exists and parses (bash -n);
# 2. no ssh/scp call outside box_env.sh's wrappers (so every call has a timeout and BatchMode);
#    in tools/*.sh every ssh/scp is written `timeout "$VAR" ssh|scp "${SSH_OPTS[@]}"` with BatchMode in SSH_OPTS;
# 3. no destructive verb in any script that acts on the box (Remove-Item, Unregister-ScheduledTask, rm, del, ...);
# 4. Stop-Process only by the two game process names;
# 5. the box paths in box_env.sh are the ones the investigation verified;
# 6. Part C (Tasks 25-32): the two-DLL deploy targets, recorded moves, launch env, the bridge session tunnel
#    (loopback only, token only in out/), the box file mirror, save-name checks, the teardown list's saves, and the
#    snapshot's rule for a log rewritten with the same bytes after the stamp;
#    Gate 0 (Task 45, owner decision 7): --esp, --test-pex, the single-file allow-list, plugins.txt add/restore.
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
    box_quit.sh box_setup.sh box_teardown_list.sh box_session.sh box_files.sh box_saves.sh box_shot.sh box_plugins.sh)
PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1 files.ps1 shot.ps1
    plugins.ps1)

for f in "${SH[@]}"; do
  if [ ! -f "$B/$f" ]; then bad "missing tools/box/$f"; continue; fi
  if bash -n "$B/$f" 2>/dev/null; then ok "bash -n $f"; else bad "bash -n $f"; fi
done
for f in "${PS[@]}"; do [ -f "$B/win/$f" ] && ok "present win/$f" || bad "missing tools/box/win/$f"; done

# 2. raw ssh/scp only inside box_env.sh (the wrappers bssh/bssh_t/bscp/bps carry timeout + BatchMode)
raw="$(grep -HnE '(^\s*|[;&|({]\s*|\$\(\s*|timeout +[^ ]+ +|then +|do +)(ssh|scp) ' "$B"/*.sh 2>/dev/null | grep -v '^[^:]*/box_env.sh:' | grep -v '^[^:]*/test_box_tools.sh:' | grep -vE ':[0-9]+:\s*#' )"
if [ -z "$raw" ]; then ok "no raw ssh/scp outside box_env.sh"; else bad "raw ssh/scp outside box_env.sh:"; echo "$raw"; fi
# 2b. tools/*.sh (remote_build.sh and friends): every ssh/scp is bounded by timeout and carries SSH_OPTS (BatchMode)
raw="$(grep -HnE '(^\s*|[;&|({]\s*|\$\(\s*|timeout +[^ ]+ +|then +|do +|if +|! +)(ssh|scp) ' "$ROOT"/tools/*.sh 2>/dev/null | grep -vE ':[0-9]+:\s*#' \
       | grep -vE 'timeout +"\$[A-Z_]+" +(ssh|scp) +"\$\{SSH_OPTS\[@\]\}"')"
if [ -z "$raw" ]; then ok "tools/*.sh: every ssh/scp has a timeout and SSH_OPTS"; else bad "tools/*.sh: unbounded ssh/scp:"; echo "$raw"; fi
for f in "$ROOT"/tools/*.sh; do
  if grep -qE 'timeout +[^ ]+ +(ssh|scp) ' "$f" && ! grep -qE 'SSH_OPTS=\(.*BatchMode=yes' "$f"; then bad "$(basename "$f"): SSH_OPTS lacks BatchMode=yes"; fi
done
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
# place.ps1 allows outside Data\SKSE\Plugins only the single files in $Singles: that .pex plus the Gate 0 list (6j)
grep -qF "\$PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'" "$B/win/place.ps1" 2>/dev/null \
  && grep -qF -- '-and -not ($Singles -ccontains $dest)' "$B/win/place.ps1" && ok "place.ps1 allow-list: Plugins tree + the single files" || bad "place.ps1 allow-list"
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
grep -qF '[IO.File]::Replace($tmp, $dest, [NullString]::Value)' "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 put replaces with no backup path ([NullString]::Value, not \$null)" || bad "files.ps1 put passes \$null as File.Replace's backup path (PowerShell 5.1 makes it \"\")"
grep -qF 'Get-NewText' "$B/win/files.ps1" 2>/dev/null && ok "files.ps1 pull uses the launch-stamp snapshot" || bad "files.ps1 does not use Get-NewText"

# 6f. box_saves.sh: a save name is checked before any ssh
if "$B/box_saves.sh" count 'bad name*' >/dev/null 2>&1; then bad "box_saves.sh accepted 'bad name*'"; else ok "box_saves.sh refuses a name that is not [A-Za-z0-9_]"; fi

# 6f2. box_console.sh never saves over an existing save: `save NAME` is sent only when box_saves.sh count NAME is 0.
#      BOX_SSH_STUB (honoured only with BOX_OFFLINE=1) answers in place of the box and logs every command.
stubd="$(mktemp -d)"; STUB="$stubd/stub.sh"; STUBLOG="$stubd/calls.log"
cat > "$STUB" <<'STUBEOF'
#!/usr/bin/env bash
echo "$*" >> "$STUBLOG"
case "$*" in
  *"Get-ChildItem"*".ess"*)   echo "${STUB_SAVES:-0}" ;;
  *"Get-Process -Name SkyrimSE,skse64_loader"*) echo IDLE ;;
  *"place.ps1 -Mode exists"*) echo "${STUB_EXISTS:-ABSENT}" ;;
  *"console.ps1"*)            echo '{"ok": true}' ;;
  *) echo "stub: unexpected command" >&2; exit 99 ;;
esac
STUBEOF
chmod +x "$STUB"; export STUBLOG
con() { : > "$STUBLOG"; STUB_SAVES="$1" BOX_SSH_STUB="$STUB" "$B/box_console.sh" "${@:2}" 2>&1; }
t="$(con 1 'save t31_probe')"; rc=$?
[ "$rc" != 0 ] && case "$t" in *"refusing 'save t31_probe'"*) true ;; *) false ;; esac && ! grep -q console.ps1 "$STUBLOG" \
  && ok "box_console.sh refuses 'save NAME' when box_saves count is 1 (console.ps1 never called)" || bad "box_console save with count 1: rc=$rc '$t'"
t="$(con 2 '  SAVE   t31_probe')"; rc=$?
[ "$rc" != 0 ] && ! grep -q console.ps1 "$STUBLOG" && ok "box_console.sh save guard is case-insensitive and allows leading spaces" || bad "SAVE (upper case) not guarded: rc=$rc '$t'"
t="$(con 1 --tool console '{"command": "save t31_probe"}')"; rc=$?
[ "$rc" != 0 ] && ! grep -q console.ps1 "$STUBLOG" && ok "box_console.sh --tool console is guarded too" || bad "--tool console save not guarded: rc=$rc '$t'"
t="$(con 0 'save t31_probe')"; rc=$?
[ "$rc" = 0 ] && grep -q console.ps1 "$STUBLOG" && ok "box_console.sh sends 'save NAME' when box_saves count is 0" || bad "box_console save with count 0: rc=$rc '$t'"
t="$(con 0 'save "two words"')"; rc=$?
[ "$rc" != 0 ] && ! grep -q console.ps1 "$STUBLOG" && ok "box_console.sh refuses a save name box_saves cannot check" || bad "quoted save name sent: rc=$rc '$t'"
t="$(con 1 'player.additem f 1')"; rc=$?
[ "$rc" = 0 ] && grep -q console.ps1 "$STUBLOG" && ! grep -q '\.ess' "$STUBLOG" && ok "box_console.sh passes other commands through" || bad "non-save command: rc=$rc '$t'"
t="$("$B/box_console.sh" 'save t31_probe' 2>&1)"; rc=$?
[ "$rc" != 0 ] && case "$t" in *refusing*) true ;; *) false ;; esac && ok "box_console.sh offline without a stub: a save is refused before any ssh" || bad "offline save: rc=$rc '$t'"

# 6f3. box_deploy.sh check_target: a target that exists on the box and is not in the deploy record is never overwritten
dd="$(mktemp -d)"; : > "$dd/T12Probe.dll"; drec="$dd/box-deployed.txt"; : > "$drec"
dep() { : > "$STUBLOG"; STUB_EXISTS="$1" BOX_DEPLOYED="$drec" BOX_SSH_STUB="$STUB" "$B/box_deploy.sh" "$dd/T12Probe.dll" 2>&1; }
t="$(dep EXISTS)"; rc=$?
[ "$rc" != 0 ] && case "$t" in *"exists on the box but is not in out/box-deployed.txt; not overwriting it"*) true ;; *) false ;; esac \
  && grep -q 'place.ps1 -Mode exists' "$STUBLOG" && ! grep -q 'place.ps1 -Mode place' "$STUBLOG" && [ ! -s "$drec" ] \
  && ok "box_deploy.sh refuses to overwrite an unrecorded target that exists (nothing placed, nothing recorded)" || bad "check_target refusal: rc=$rc '$t'"
t="$(dep ABSENT)"; rc=$?
[ "$rc" != 0 ] && case "$t" in *"BOX_OFFLINE: refusing scp"*) true ;; *) false ;; esac \
  && ok "box_deploy.sh goes on to the copy for an absent target (stopped at scp by BOX_OFFLINE)" || bad "check_target absent: rc=$rc '$t'"
[ "$(BOX_DEPLOYED=/tmp/elsewhere.txt bash -c 'source "$1/box_env.sh"; echo "$BOX_DEPLOYED"' _ "$B")" = "$ROOT/out/box-deployed.txt" ] \
  && ok "BOX_DEPLOYED override still ignored without BOX_SSH_STUB" || bad "BOX_DEPLOYED override honoured without a stub"
t="$(BOX_OFFLINE=0 BOX_SSH_STUB="$STUB" BOX=nobody@127.0.0.1 bash -c 'source "$1/box_env.sh"; box_stubbed && echo STUBBED || echo REAL' _ "$B")"
[ "$t" = REAL ] && ok "BOX_SSH_STUB is ignored unless BOX_OFFLINE=1" || bad "BOX_SSH_STUB honoured without BOX_OFFLINE: '$t'"

# 6f4. place.ps1: every mode normalises Dest (GetFullPath) and rejects '..', not only -Mode move
pl="$B/win/place.ps1"
pre="$(sed -n '/^switch (\$Mode)/q;p' "$pl" 2>/dev/null)"
echo "$pre" | grep -qF '$fullDest = [IO.Path]::GetFullPath($dest)' \
  && echo "$pre" | grep -qF "if (\$dest -match '\.\.' -or -not (\$fullDest.StartsWith(\$root) -or (\$Singles -ccontains \$fullDest))) { \"REFUSED" \
  && ok "place.ps1 checks GetFullPath(Dest) and '..' before the mode switch (all modes)" || bad "place.ps1 does not normalise Dest for every mode"

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

# 6i. a log rewritten after the stamp with the same bytes (SkythonLoader.log every launch) counts as new: stamp.ps1
#     records each file's LastWriteTimeUtc, snapshot_lib.ps1 drops the offset only for same length + hash + later mtime
sl="$B/win/snapshot_lib.ps1"
grep -qF 'mtime = [IO.File]::GetLastWriteTimeUtc($_.FullName).Ticks' "$B/win/stamp.ps1" 2>/dev/null \
  && ok "stamp.ps1 records each file's LastWriteTimeUtc (mtime ticks)" || bad "stamp.ps1 does not record mtime"
grep -qF 'if ($h -eq $entry.Value.sha256) {' "$sl" 2>/dev/null && grep -qE '^\s*\$offset = \$L$' "$sl" \
  && ok "snapshot_lib.ps1 append case: matching prefix -> only bytes after L" || bad "snapshot_lib.ps1 append case"
grep -qF "\$m = \$entry.Value.PSObject.Properties['mtime']" "$sl" 2>/dev/null \
  && grep -qF 'if ($bytes.Length -eq $L -and $m -and [IO.File]::GetLastWriteTimeUtc($path).Ticks -gt [int64]$m.Value) { $offset = 0 }' "$sl" \
  && ok "snapshot_lib.ps1: same length + hash + later mtime -> the whole file is new" || bad "snapshot_lib.ps1 has no rewritten-identical rule"
grep -vE '^\s*#' "$sl" | grep -qE 'LastWriteTime(Utc)? *-(lt|le)|launch\.stamp' && bad "snapshot_lib.ps1 uses a file time to drop text" \
  || ok "snapshot_lib.ps1 never drops text by file time or the stamp time"
grep -qF 'reads as UNCHANGED: snapshot_lib cannot tell' "$B/win/files.ps1" "$B/win/wait_log.ps1" "$ROOT/docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded/task-01b-box-game-harness.md" 2>/dev/null \
  && bad "the old 'reads as UNCHANGED' caveat is still documented" || ok "no stale 'reads as UNCHANGED' caveat"

# 6j. Gate 0 (Task 45, owner decision 7): exactly Data\SkythonTest.esp, exactly six Data\Scripts\*.pex, and
#     plugins.txt changed only by adding *SkythonTest.esp and putting the backup back (no ssh in print/local modes).
D='C:\ChronicleDev\Stock Game\Data'
gd="$(mktemp -d)"; mkdir -p "$gd/sub"
for n in SkythonTest.esp Other.esp Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex \
         SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex Other.pex skython.pex SkyrimPython.pex Skython.psc; do : > "$gd/$n"; done
: > "$gd/sub/Skython.pex"
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --esp "$gd/SkythonTest.esp" 2>&1)"
[ "$t" = "$D\SkythonTest.esp" ] && ok "--esp target is Data\\SkythonTest.esp" || bad "--esp target: '$t'"
for a in "$gd/Other.esp" "$gd/missing.esp" "$gd/SkythonTest.esp $gd/SkythonTest.esp"; do
  # shellcheck disable=SC2086
  if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --esp $a >/dev/null 2>&1; then bad "--esp accepted: $a"; else ok "--esp refuses: ${a//$gd\//}"; fi
done
six=(Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex)
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "${six[@]/#/$gd/}" 2>&1)"
want="$(for n in "${six[@]}"; do printf '%s\n' "$D\Scripts\\$n"; done)"
[ "$t" = "$want" ] && ok "--test-pex: the six names resolve to Data\\Scripts\\<name>" || { bad "--test-pex targets:"; echo "$t"; }
t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "$gd/sub/../sub/Skython.pex" 2>&1)"
[ "$t" = "$D\Scripts\Skython.pex" ] && ok "--test-pex: the local path never reaches the destination (basename only)" || bad "--test-pex local path: '$t'"
for a in Other.pex skython.pex SkyrimPython.pex Skython.psc missing.pex "Skython.pex Skython.pex" "Skython.pex Other.pex"; do
  args=(); for n in $a; do args+=("$gd/$n"); done
  if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "${args[@]}" >/dev/null 2>&1; then bad "--test-pex accepted: $a"; else ok "--test-pex refuses: $a"; fi
done
if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex >/dev/null 2>&1; then bad "--test-pex accepted no file"; else ok "--test-pex refuses no file"; fi
grep -qE 'TEST_PEX_NAMES=\(Skython\.pex SkythonTestQuestBase\.pex SkythonTestQuest\.pex SkythonTestAlias\.pex SkythonTestLocationAlias\.pex QF_SkythonTestQuest_01000800\.pex\)$' "$B/box_deploy.sh" \
  && ok "box_deploy.sh TEST_PEX_NAMES is exactly the six names" || bad "box_deploy.sh TEST_PEX_NAMES"
grep -qE 'put_file "\$ESP" "\$ESP_DEST"' "$B/box_deploy.sh" && grep -qE 'put_file "\$\{TPEX\[\$i\]\}" "\$\{TPEX_DEST\[\$i\]\}"' "$B/box_deploy.sh" \
  && ok "--esp and --test-pex go through put_file (absent or ours; recorded)" || bad "--esp/--test-pex do not use put_file"
# Gate 0 file and the deploy record, against 6f3's stub (never print mode): without out/gate0-accepted nothing is
# placed; with it, an existing Data\SkythonTest.esp or Data\Scripts\*.pex that is not ours is never overwritten.
drec2="$gd/box-deployed.txt"; : > "$drec2"; g0="$gd/gate0-accepted"
gdep() { : > "$STUBLOG"; STUB_EXISTS="$1" BOX_DEPLOYED="$drec2" BOX_GATE0_FILE="$g0" BOX_SSH_STUB="$STUB" "$B/box_deploy.sh" "${@:2}" 2>&1; }
for m in "--esp $gd/SkythonTest.esp" "--test-pex $gd/Skython.pex"; do
  # shellcheck disable=SC2086
  t="$(gdep ABSENT $m)"; rc=$?
  [ "$rc" != 0 ] && case "$t" in *"Gate 0 is not recorded"*) true ;; *) false ;; esac && ! grep -q 'place.ps1' "$STUBLOG" && [ ! -s "$drec2" ] \
    && ok "${m%% *} refuses without out/gate0-accepted (no place.ps1 call, nothing recorded)" || bad "${m%% *} without Gate 0: rc=$rc '$t'"
done
echo 'owner decision 7 accepted on 2026-10-02: "offline test"' > "$g0"
for m in "--esp $gd/SkythonTest.esp" "--test-pex $gd/Skython.pex"; do
  # shellcheck disable=SC2086
  t="$(gdep EXISTS $m)"; rc=$?
  [ "$rc" != 0 ] && case "$t" in *"not in out/box-deployed.txt"*) true ;; *) false ;; esac \
    && grep -q 'place.ps1 -Mode exists' "$STUBLOG" && ! grep -q 'place.ps1 -Mode place' "$STUBLOG" && [ ! -s "$drec2" ] \
    && ok "${m%% *} never overwrites an existing target that is not ours (nothing placed, nothing recorded)" || bad "${m%% *} over an unrecorded target: rc=$rc '$t'"
done
[ "$(BOX_GATE0_FILE=/tmp/elsewhere bash -c 'source "$1/box_env.sh"; echo "$BOX_GATE0_FILE"' _ "$B")" = "$ROOT/out/gate0-accepted" ] \
  && ok "BOX_GATE0_FILE override ignored without BOX_SSH_STUB" || bad "BOX_GATE0_FILE override honoured without a stub"
pl="$B/win/place.ps1"
grep -qF "\$EspDest = 'C:\ChronicleDev\Stock Game\Data\SkythonTest.esp'" "$pl" && ok "place.ps1 lists Data\\SkythonTest.esp" || bad "place.ps1 EspDest"
all6=1; for n in "${six[@]}"; do grep -qF "'C:\ChronicleDev\Stock Game\Data\Scripts\\$n'" "$pl" || all6=0; done
[ "$(grep -cE "^  'C:\\\\ChronicleDev\\\\Stock Game\\\\Data\\\\Scripts\\\\[A-Za-z0-9_]+\.pex'[,)]?$" "$pl")" = 6 ] && [ "$all6" = 1 ] \
  && ok "place.ps1 TestPexDests: exactly the six Data\\Scripts names" || bad "place.ps1 TestPexDests is not exactly the six names"
grep -qF '$Singles = @($PexDest, $EspDest) + $TestPexDests' "$pl" && grep -qF "if ((\$Singles -ccontains \$dest) -and \$Kind -ne 'file')" "$pl" \
  && ok "place.ps1: single files are compared exactly (-ccontains) and take one file each" || bad "place.ps1 single-file rule"

# plugins.txt: compose / classify (local) and the box script's guards
P_="$B/box_plugins.sh"; pd="$(mktemp -d)"; export SKYTHON_STATE="$pd/state"
printf 'a.esp\r\n*B.esp\r\n' > "$pd/bak1"; printf 'a.esp\r\n*B.esp\r\n*SkythonTest.esp\r\n' > "$pd/want1"
printf 'a.esp\r\n*B.esp' > "$pd/bak2"; printf 'a.esp\r\n*B.esp\r\n*SkythonTest.esp\r\n' > "$pd/want2"
: > "$pd/bak3"; printf '*SkythonTest.esp\n' > "$pd/want3"
# R64: an LF-only backup (the box's real plugins.txt; the game rewrites it with LF at launch) gets an LF line
printf 'a.esp\nB.esp\n' > "$pd/bak4"; printf 'a.esp\nB.esp\n*SkythonTest.esp\n' > "$pd/want4"
printf 'a.esp\nB.esp' > "$pd/bak5"; printf 'a.esp\nB.esp\n*SkythonTest.esp\n' > "$pd/want5"
for i in 1 2 3 4 5; do
  "$P_" compose "$pd/bak$i" > "$pd/got$i" 2>&1 && cmp -s "$pd/got$i" "$pd/want$i" && ok "compose case $i: backup + [EOL] + *SkythonTest.esp EOL (the backup's line ending)" || bad "compose case $i: $(od -c "$pd/got$i" | head -3)"
done
t="$("$P_" classify "$pd/want4" "$pd/bak4" 2>&1)"; [ "$t" = ADDED ] && ok "classify: LF backup + LF line -> ADDED" || bad "classify LF: '$t'"
# R71: the game appends an inactive "SkythonTest.esp" line (no "*") when the ESP is in Data but not enabled
printf 'a.esp\nB.esp\nSkythonTest.esp\n' > "$pd/inactive4"
t="$("$P_" classify "$pd/inactive4" "$pd/bak4" 2>&1)"; [ "$t" = INACTIVE ] && ok "classify: backup + the game's inactive line -> INACTIVE" || bad "classify inactive: '$t'"
grep -qF "(Sha \$c) -ne (Sha (Compose \$b ''))" "$B/win/plugins.ps1" && ok "plugins.ps1 restore accepts the inactive line" || bad "plugins.ps1 restore inactive"
grep -qF 'if ($bak -contains 13) { $eol = [byte[]](13, 10) }' "$B/win/plugins.ps1" && ok "plugins.ps1 Compose takes the backup's line ending" || bad "plugins.ps1 Compose line ending"
printf 'a.esp\r\n*skythontest.esp\r\n' > "$pd/listed"; printf 'SkythonTest.esp\r\n' > "$pd/listed2"
for f in listed listed2; do if "$P_" compose "$pd/$f" >/dev/null 2>&1; then bad "compose accepted a backup that already lists it ($f)"; else ok "compose refuses a backup that already lists SkythonTest.esp ($f)"; fi; done
printf '*B.esp\r\na.esp\r\n*SkythonTest.esp\r\n' > "$pd/reordered"; cp "$pd/want1" "$pd/extra"; printf 'c.esp\r\n' >> "$pd/extra"
for c in "bak1 BACKUP" "want1 ADDED" "reordered OTHER" "extra OTHER" "bak2 OTHER"; do
  set -- $c; t="$("$P_" classify "$pd/$1" "$pd/bak1" 2>&1)"
  [ "$t" = "$2" ] && ok "classify $1 -> $2" || bad "classify $1: '$t' (expected $2)"
done
pp="$B/win/plugins.ps1"
grep -qF "\$PluginsTxt = 'C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt'" "$pp" && grep -qF "\$Backup = 'C:\dev\skyrim-python-box\plugins.txt.bak'" "$pp" \
  && ok "plugins.ps1: the verified plugins.txt path and the backup in C:\\dev\\skyrim-python-box" || bad "plugins.ps1 paths"
w="$(grep -vE '^\s*#' "$pp" | grep -E 'WriteAllBytes|Copy-Item|Set-Content|Out-File|Move-Item|Add-Content|New-Item')"
[ "$(echo "$w" | grep -c .)" = 3 ] && echo "$w" | grep -qF '[IO.File]::WriteAllBytes($PluginsTxt, $n)' \
  && echo "$w" | grep -qF 'Copy-Item -LiteralPath $PluginsTxt -Destination $Backup' && echo "$w" | grep -qF 'Copy-Item -LiteralPath $Backup -Destination $PluginsTxt -Force' \
  && ok "plugins.ps1 writes only: the backup (once), plugins.txt (add), plugins.txt from the backup (restore)" || { bad "plugins.ps1 write operations:"; echo "$w"; }
[ "$(grep -cF "if (GameRunning) { Refuse 'the game is running' }" "$pp")" = 2 ] && ok "plugins.ps1 add and restore refuse while the game runs" || bad "plugins.ps1 game-running guard"
grep -qF 'function GameRunning { return [bool](Get-Process -Name SkyrimSE,skse64_loader,SkyrimSELauncher,ModOrganizer -ErrorAction SilentlyContinue) }' "$pp" \
  && ok "plugins.ps1 GameRunning covers the game, the loader, the launcher and MO2" || bad "plugins.ps1 GameRunning process list"
grep -qF 'if ((Sha $c) -ne (Sha (Compose $b)) -and (Sha $c) -ne (Sha (Compose $b '"''"'))) { Refuse' "$pp" && grep -qF 'if ((Sha $c) -ne (Sha $b)) { Refuse' "$pp" && grep -qF 'if (Listed $b) { Refuse' "$pp" \
  && ok "plugins.ps1 re-checks on the box: add only from the backup, restore only from backup + our line or the game's inactive line" || bad "plugins.ps1 box-side checks"

# box_quit.sh / box_stop.sh / box_plugins.sh against a stubbed box (BOX_SSH_STUB, offline only)
pst="$pd/pstub.sh"; PSTUBLOG="$pd/pcalls.log"; export PSTUBLOG
cat > "$pst" <<'PSTUBEOF'
#!/usr/bin/env bash
echo "$*" >> "$PSTUBLOG"
case "$*" in
  *"console.ps1"*)                 echo '{"ok": true}' ;;
  *"quit_check.ps1"*)              printf 'EXITED\nNO_NEW_CRASH_LOG\nCLEAN_EXIT\n' ;;
  *"stop.ps1"*)                    echo NOTHING_RUNNING ;;
  *"plugins.ps1 -Mode read"*)      cat "$PSTUB_READ" ;;
  *"plugins.ps1 -Mode restore"*)   echo "RESTORED $PSTUB_BAKSHA" ;;
  *"plugins.ps1 -Mode add"*)       echo "ADDED $PSTUB_NEWSHA" ;;
  *) echo "pstub: unexpected $*" >&2; exit 99 ;;
esac
PSTUBEOF
chmod +x "$pst"
readf() { printf 'CUR %s %s\nBAK %s %s\nGAME %s\n' "$(sha256sum "$1" | cut -d' ' -f1)" "$(base64 -w0 < "$1")" \
          "$(sha256sum "$2" | cut -d' ' -f1)" "$(base64 -w0 < "$2")" "$3" > "$pd/read.txt"; }
echo 'owner decision 7 accepted on 2026-10-02: "offline test"' > "$pd/gate0"
sbox() { : > "$PSTUBLOG"; PSTUB_READ="$pd/read.txt" PSTUB_BAKSHA="$(sha256sum "$pd/bak1" | cut -d' ' -f1)" \
         PSTUB_NEWSHA="$(sha256sum "$pd/want1" | cut -d' ' -f1)" BOX_GATE0_FILE="$pd/gate0" BOX_SSH_STUB="$pst" "$@" 2>&1; }
mkdir -p "$SKYTHON_STATE"; echo "restored 2026-10-02T00:00:00" > "$SKYTHON_STATE/box-plugins.state"
t="$(sbox "$B/box_quit.sh")"; rc=$?
[ "$rc" = 0 ] && case "$t" in *"nothing to restore"*) true ;; *) false ;; esac && ! grep -q plugins.ps1 "$PSTUBLOG" \
  && ok "box_quit.sh with no add this session: no plugins.ps1 call" || bad "box_quit.sh without add: rc=$rc '$t'"
echo "added 2026-10-02T00:00:00" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/want1" "$pd/bak1" IDLE
t="$(sbox "$B/box_quit.sh")"; rc=$?
[ "$rc" = 0 ] && grep -qE "plugins.ps1 -Mode restore -ExpectCur $(sha256sum "$pd/want1" | cut -d' ' -f1)" "$PSTUBLOG" \
  && [ "$(cut -d' ' -f1 "$SKYTHON_STATE/box-plugins.state")" = restored ] && ok "box_quit.sh after EXITED restores the backup (state -> restored)" || bad "box_quit.sh restore: rc=$rc '$t'"
echo "added x" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/reordered" "$pd/bak1" IDLE
t="$(sbox "$B/box_quit.sh")"; rc=$?
[ "$rc" = 3 ] && ! grep -q 'plugins.ps1 -Mode restore' "$PSTUBLOG" && case "$t" in *STOP*) true ;; *) false ;; esac \
  && ok "box_quit.sh: plugins.txt neither backup nor backup + line -> STOP (exit 3), nothing written" || bad "box_quit.sh other: rc=$rc '$t'"
echo "added x" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/want1" "$pd/bak1" IDLE
t="$(sbox "$B/box_stop.sh")"; rc=$?
[ "$rc" = 0 ] && grep -q 'plugins.ps1 -Mode restore' "$PSTUBLOG" && ok "box_stop.sh restores after the game is gone" || bad "box_stop.sh restore: rc=$rc '$t'"
readf "$pd/bak1" "$pd/bak1" IDLE
t="$(sbox "$P_" add)"; rc=$?
[ "$rc" = 0 ] && grep -qE "plugins.ps1 -Mode add -ExpectCur $(sha256sum "$pd/bak1" | cut -d' ' -f1) -ExpectNew $(sha256sum "$pd/want1" | cut -d' ' -f1)" "$PSTUBLOG" \
  && [ "$(cut -d' ' -f1 "$SKYTHON_STATE/box-plugins.state")" = added ] && ok "add sends the backup's and the composed file's SHA-256 (state -> added)" || bad "add: rc=$rc '$t'"
readf "$pd/bak1" "$pd/bak1" RUNNING
t="$(sbox "$P_" add)"; rc=$?
[ "$rc" != 0 ] && ! grep -q 'plugins.ps1 -Mode add' "$PSTUBLOG" && ok "add refuses while the game runs" || bad "add while running: rc=$rc '$t'"
readf "$pd/extra" "$pd/bak1" IDLE
t="$(sbox "$P_" add)"; rc=$?
[ "$rc" = 3 ] && ! grep -q 'plugins.ps1 -Mode add' "$PSTUBLOG" && ok "add: plugins.txt differs from the backup -> STOP, nothing written" || bad "add other: rc=$rc '$t'"
readf "$pd/bak1" "$pd/bak1" IDLE
for m in backup add; do
  : > "$PSTUBLOG"; t="$(PSTUB_READ="$pd/read.txt" BOX_GATE0_FILE="$pd/no-gate0" BOX_SSH_STUB="$pst" "$P_" "$m" 2>&1)"; rc=$?
  [ "$rc" != 0 ] && case "$t" in *"Gate 0 is not recorded"*) true ;; *) false ;; esac && ! grep -q plugins.ps1 "$PSTUBLOG" \
    && ok "box_plugins.sh $m refuses without out/gate0-accepted (no plugins.ps1 call)" || bad "box_plugins.sh $m without Gate 0: rc=$rc '$t'"
done
unset SKYTHON_STATE

# 7. nothing reached ssh or scp during the whole run
if [ -e "$TRAPLOG" ]; then bad "ssh/scp was called during the offline test:"; cat "$TRAPLOG"; else ok "no ssh/scp call reached PATH"; fi

[ "$fail" = 0 ] && echo "BOX TOOLS OFFLINE PASS" || echo "BOX TOOLS OFFLINE FAIL"
exit "$fail"
