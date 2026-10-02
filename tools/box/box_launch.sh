#!/usr/bin/env bash
# Launch the game on the box through OUR scheduled task SkyrimPythonGame (launch.ps1). Refuses when the game or
# loader already runs. Writes the launch stamp on the box (box clock; stamp.ps1 also snapshots the SKSE log
# folder so box_wait_log.sh and box_quit.sh see only this launch), copies it to
# $SKYTHON_STATE/box-launch.stamp, then waits (bounded, LAUNCH_WAIT=300 s) for launch.ps1's result
# (launch.ps1's worst case: Steam logon waits of 120 + 90 s and two 20 s loader checks).
# Usage: [BOX_GAME=ae1170|ae17104] tools/box/box_launch.sh [--env SKYTHON_TEST_<NAME>=VALUE]...
#   The install: C:\dev\skyrim-python-box\launch.game is rewritten on every launch with BOX_GAME's key; launch.ps1
#   starts that install and refuses unless its SkyrimSE.exe is the version required for it (1.6.1170.0 for
#   ae1170, 1.7.104.0 for ae17104). BOX_LAUNCH_PRINT_GAME=1 prints "<key> <version> <folder>" and exits before any
#   ssh (offline test).
#   --env: launch.ps1 sets these in its own environment before it starts skse64_loader.exe, so the game inherits
#   them (for example SKYTHON_TEST_UNVERIFIED_RUNTIME, SKYTHON_TEST_PRELOAD). Only
#   SKYTHON_TEST_* names. C:\dev\skyrim-python-box\launch.env is rewritten on every launch (empty without
#   --env), so a variable never leaks into a later launch; launch.ps1 also empties it after reading it.
#   BOX_LAUNCH_PRINT_ENV=1 prints launch.env's content and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
envtext=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env)
      kv="${2:?--env NAME=VALUE}"
      [[ "$kv" =~ ^SKYTHON_TEST_[A-Z0-9_]+=.*$ ]] || box_die "--env takes only SKYTHON_TEST_<NAME>=VALUE (got '${kv%%=*}')"
      case "$kv" in *$'\n'*|*$'\r'*) box_die "--env value must be one line" ;; esac
      envtext+="$kv"$'\n'; shift 2 ;;
    *) box_die "unknown option $1 (usage: box_launch.sh [--env SKYTHON_TEST_<NAME>=VALUE]...)" ;;
  esac
done
if [ "${BOX_LAUNCH_PRINT_ENV:-0}" = 1 ]; then printf '%s' "$envtext"; exit 0; fi
if [ "${BOX_LAUNCH_PRINT_GAME:-0}" = 1 ]; then echo "$BOX_GAME $BOX_GAME_VERSION $BOX_GAME_DIR"; exit 0; fi
mkdir -p "$SKYTHON_STATE"
out="$(bps 60 stamp.ps1 | tr -d '\r')" || box_die "refused: $out"
stamp="$(echo "$out" | sed -n 's/^STAMP \([^ ]*\).*/\1/p')"; [ -n "$stamp" ] || box_die "no stamp: $out"
echo "$stamp" > "$SKYTHON_STATE/box-launch.stamp"
echo "box launch stamp $stamp"
bssh "[IO.File]::WriteAllText('$BOX_DIR\\launch.env', [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$(b64 "$envtext")')))" \
  || box_die "could not write launch.env"
[ -n "$envtext" ] && printf 'launch env: %s' "$envtext"
bssh "[IO.File]::WriteAllText('$BOX_DIR\\launch.game', '$BOX_GAME')" || box_die "could not write launch.game"
echo "launch install: $BOX_GAME ($BOX_GAME_DIR, SkyrimSE.exe $BOX_GAME_VERSION)"
bssh "Start-ScheduledTask -TaskName $BOX_TASK" || box_die "could not start the scheduled task $BOX_TASK (run tools/box/box_setup.sh once)"
end=$(( $(date +%s) + ${LAUNCH_WAIT:-300} ))
while :; do
  res="$(bssh "Get-Content '$BOX_DIR\\launch.result' -Raw -ErrorAction SilentlyContinue" | tr -d '\r' || true)"
  case "$res" in "$stamp "*) break ;; esac
  if [ "$(date +%s)" -ge "$end" ]; then
    echo "box_launch: no result from launch.ps1 after ${LAUNCH_WAIT:-300}s; its log:" >&2
    bssh "Get-Content '$BOX_DIR\\launch.log' -Tail 20" >&2 || true; exit 1
  fi
  sleep 3
done
bssh "Get-Content '$BOX_DIR\\launch.log' | Select-String -SimpleMatch '[$stamp]' | % Line" | tr -d '\r'
case "$res" in "$stamp ok"*) echo "launched: ${res#"$stamp "}" ;; *) box_die "launch failed: ${res#"$stamp "}" ;; esac
