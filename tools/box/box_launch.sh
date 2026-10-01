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
