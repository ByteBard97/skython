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
