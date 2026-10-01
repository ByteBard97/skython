#!/usr/bin/env bash
# Launch SimpleSkyrim through GE-Proton with skse64_loader.exe (not MO2), in the background.
# Refuses a second launch. Steam should already be running (a warning is printed if it is not).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
mkdir -p "$SKYRIM_PYTHON_STATE"
STEAM_ROOT="$HOME/.local/share/Steam"
PFX_DIR="$STEAM_ROOT/steamapps/compatdata/4190904831/pfx"
game_running() { pgrep -x SkyrimSE.exe >/dev/null || pgrep -f skse64_loader.exe >/dev/null; }
# True when a wineserver for this prefix is alive (a leftover Wine session makes 'proton waitforexitandrun' hang).
prefix_wineserver_alive() {
  local p
  for p in $(pgrep -x wineserver); do
    tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null | grep -q "^WINEPREFIX=${PFX_DIR%/}/\?$" && return 0
  done
  return 1
}
if [ "${LAUNCH_DRY_RUN:-0}" = 1 ]; then   # test hook: report what would happen, launch nothing, stop nothing
  game_running && echo "dry-run: game running" || echo "dry-run: game not running"
  prefix_wineserver_alive && echo "dry-run: prefix wineserver alive" || echo "dry-run: no prefix wineserver"
  exit 0
fi
if game_running; then
  echo "launch_game: the game (or its loader) is already running; quit it first (console: qqq)" >&2
  exit 1
fi
if ! pgrep -x steam >/dev/null; then
  echo "launch_game: Steam is not running; start Steam first (without it the game hands off to Steam and exits)" >&2
  exit 1
fi
if prefix_wineserver_alive; then
  WINEPREFIX="$PFX_DIR" "$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/files/bin/wineserver" -k || true
  echo "launch_game: stopped a leftover Wine session for this prefix" >&2
  sleep 2
fi
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$STEAM_ROOT/steamapps/compatdata/4190904831"
export STEAM_COMPAT_MOUNTS="/mnt/games/SteamLibrary"
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
PROTON="$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/proton"
cd "$HOME/Games/SimpleSkyrim/Stock Game"
: > "$SKYRIM_PYTHON_STATE/launch.stamp"   # logs older than this stamp belong to an earlier run
nohup "$PROTON" waitforexitandrun "$(pwd)/skse64_loader.exe" > "$SKYRIM_PYTHON_STATE/launch.log" 2>&1 &
echo "launched pid $! (log: $SKYRIM_PYTHON_STATE/launch.log)"
