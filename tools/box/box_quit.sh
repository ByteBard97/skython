#!/usr/bin/env bash
# Clean exit on the box: console qqq through DevBench, then a bounded wait (QUIT_WAIT=120 s) for SkyrimSE to end
# and a check that no crash-*.log appeared in the box's SKSE log folder since the launch stamp.
# Exit 0 only for CLEAN_EXIT. DevBench may drop the HTTP reply to qqq because the game is exiting: not an error.
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
"$(dirname "$0")/box_console.sh" qqq || echo "(no reply to qqq; expected while the game exits)"
bps $(( ${QUIT_WAIT:-120} + 60 )) quit_check.ps1 -Seconds "${QUIT_WAIT:-120}" | tr -d '\r'
exit "${PIPESTATUS[0]}"
