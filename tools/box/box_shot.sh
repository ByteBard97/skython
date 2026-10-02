#!/usr/bin/env bash
# Usage: tools/box/box_shot.sh [LOCAL_PNG]
#   Screenshot of the box's desktop plus the window titles, for a game that hangs behind a dialog (box_wait_log.sh
#   exit 2, DIALOG). Starts OUR scheduled task SkyrimPythonShot (shot.ps1, runs in the interactive session of
#   BOX_WIN_USER), waits (bounded, SHOT_WAIT=60 s, box clock) until shot.png and windows.txt in
#   C:\dev\skyrim-python-box are both newer than the start, copies shot.png to LOCAL_PNG (default
#   $SKYTHON_STATE/box-shot.png) and prints windows.txt. Writes nothing on the box outside those two files.
#   BOX_SHOT_PRINT=1 prints the local target and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
dest="${1:-$SKYTHON_STATE/box-shot.png}"
if [ "${BOX_SHOT_PRINT:-0}" = 1 ]; then echo "$dest"; exit 0; fi
t0="$(bssh '$t = (Get-Date).ToUniversalTime().Ticks; Start-ScheduledTask -TaskName SkyrimPythonShot; $t' | tr -d '\r')" \
  || box_die "could not start the scheduled task SkyrimPythonShot (run tools/box/box_setup.sh once)"
[[ "$t0" =~ ^[0-9]+$ ]] || box_die "no box start time: '$t0'"
end=$(( $(date +%s) + ${SHOT_WAIT:-60} ))
while :; do
  r="$(bssh "\$p = Get-Item '$BOX_DIR\\shot.png' -ErrorAction SilentlyContinue; \$w = Get-Item '$BOX_DIR\\windows.txt' -ErrorAction SilentlyContinue; if (\$p -and \$w -and \$p.LastWriteTimeUtc.Ticks -gt $t0 -and \$w.LastWriteTimeUtc.Ticks -gt $t0) { 'READY' }" | tr -d '\r' || true)"
  [ "$r" = READY ] && break
  [ "$(date +%s)" -ge "$end" ] && box_die "no new shot.png and windows.txt after ${SHOT_WAIT:-60}s (is $BOX_WIN_USER logged on at the box?)"
  sleep 2
done
mkdir -p "$(dirname "$dest")"
bscp "$BOX:$BOX_DIR_FWD/shot.png" "$dest" || box_die "scp of shot.png failed"
echo "box screenshot: $dest"
bssh "Get-Content '$BOX_DIR\\windows.txt'" | tr -d '\r' || box_die "could not read windows.txt"
