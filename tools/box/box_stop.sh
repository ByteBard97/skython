#!/usr/bin/env bash
# Force-stop the game on the box: Stop-Process -Name SkyrimSE,skse64_loader (by name only), bounded 20 s check.
# Use box_quit.sh (console qqq) for a clean exit; this is for a hung game.
# Gate 0 (Task 45): once nothing runs, box_plugins.sh restore-if-added puts plugins.txt's backup back if this session
# added *SkythonTest.esp; a restore that cannot be done safely is a STOP (exit 3).
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
out="$(bps 60 stop.ps1)"; rc=$?
out="$(printf '%s\n' "$out" | tr -d '\r')"; echo "$out"
if [ "$rc" = 0 ]; then
  "$(dirname "$0")/box_plugins.sh" restore-if-added || rc=3
else
  echo "plugins.txt: not restored (the game is still running; STOP and ask the owner)"
fi
exit "$rc"
