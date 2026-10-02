#!/usr/bin/env bash
# Clean exit on the box: console qqq through DevBench, then a bounded wait (QUIT_WAIT=120 s) for SkyrimSE to end
# and a check that no crash-*.log appeared in the box's SKSE log folder since the launch stamp.
# Exit 0 only for CLEAN_EXIT. DevBench may drop the HTTP reply to qqq because the game is exiting: not an error.
# Gate 0 (Task 45): once the game has EXITED, box_plugins.sh restore-if-added puts plugins.txt's backup back if this
# session added *SkythonTest.esp; a restore that cannot be done safely is a STOP (exit 3).
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
"$(dirname "$0")/box_console.sh" qqq || echo "(no reply to qqq; expected while the game exits)"
out="$(bps $(( ${QUIT_WAIT:-120} + 60 )) quit_check.ps1 -Seconds "${QUIT_WAIT:-120}")"; rc=$?
out="$(printf '%s\n' "$out" | tr -d '\r')"; echo "$out"
if printf '%s\n' "$out" | grep -qx EXITED; then
  "$(dirname "$0")/box_plugins.sh" restore-if-added || rc=3
else
  echo "plugins.txt: not restored yet (the game has not exited); tools/box/box_stop.sh restores after it ends"
fi
exit "$rc"
