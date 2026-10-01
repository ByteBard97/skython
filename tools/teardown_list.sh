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
