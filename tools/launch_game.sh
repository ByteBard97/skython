#!/usr/bin/env bash
# Launch the game in $SKYRIM_GAME_DIR through GE-Proton with skse64_loader.exe (not MO2), in the background.
# Paths come from tools/env.sh. Env: STEAM_ROOT (default ~/.local/share/Steam), PROTON (default GE-Proton10-14's
# proton script), STEAM_COMPAT_MOUNTS (extra library folders Proton must see, e.g. where Skyrim.esm lives).
# Refuses when: the game/loader is already running, a launch is already pending, or Steam is not running
# (without Steam the game hands off to steam://run/489830 and exits). A Wine session left over from a failed
# launch makes 'proton waitforexitandrun' hang, so it is stopped with the prefix's own 'wineserver -k'
# (and verified gone) before launching.
# LAUNCH_DRY_RUN=1 runs every check, prints the branch it would take and why, and acts on nothing.
# Processes are recognised by argv, never by a substring of the command line: under Proton the game's comm is
# "Main" and its argv0 is a Windows path (Z:\...\SkyrimSE.exe).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/tools/env.sh"
STEAM_ROOT="${STEAM_ROOT:-$HOME/.local/share/Steam}"
PFX_DIR="$SKYRIM_PREFIX"
COMPAT_DIR="$(dirname "$SKYRIM_PREFIX")"
PROC_ROOT="${PROC_ROOT:-/proc}"   # overridable so the matcher can be tested against a fake /proc

# classify_cmdline: reads one NUL-separated command line on stdin, prints game | loader | wrapper | other.
classify_cmdline() {
  local -a a=()
  mapfile -d '' -t a || true
  local old; old="$(shopt -p nocasematch || true)"
  shopt -s nocasematch
  local kind=other
  if [ "${#a[@]}" -ge 1 ] && [[ "${a[0]}" =~ ^[A-Za-z]:\\.*\\SkyrimSE\.exe$ ]]; then kind=game
  elif [ "${#a[@]}" -ge 1 ] && [[ "${a[0]}" =~ ^[A-Za-z]:\\.*\\skse64_loader\.exe$ ]]; then kind=loader
  elif [ "${#a[@]}" -ge 3 ] && [[ "${a[0]##*/}" == python* ]] && [[ "${a[1]}" == */proton ]] && [[ "${a[2]}" == waitforexit* || "${a[2]}" == run ]]; then kind=wrapper
  fi
  eval "$old"
  echo "$kind"
}

# scan_procs <kind>: succeeds when some process under $PROC_ROOT classifies as <kind>.
# A wrapper only counts when it belongs to this prefix (STEAM_COMPAT_DATA_PATH in its environment).
scan_procs() {
  local want="$1" d k
  for d in "$PROC_ROOT"/[0-9]*; do
    [ -r "$d/cmdline" ] || continue
    k="$(classify_cmdline < "$d/cmdline" 2>/dev/null)" || continue
    [ "$k" = "$want" ] || continue
    if [ "$want" = wrapper ]; then
      { tr '\0' '\n' < "$d/environ" | grep -qxF -e "STEAM_COMPAT_DATA_PATH=$COMPAT_DIR"; } 2>/dev/null || continue
    fi
    return 0
  done
  return 1
}

# True when a wineserver for this prefix is alive (compared as plain strings, not as a regex).
prefix_wineserver_alive() {
  local p line
  for p in $(pgrep -x wineserver); do
    while IFS= read -r line; do
      case "$line" in "WINEPREFIX=$PFX_DIR"|"WINEPREFIX=$PFX_DIR/") return 0 ;; esac
    done < <(tr '\0' '\n' < "/proc/$p/environ" 2>/dev/null)
  done
  return 1
}

[ "${LAUNCH_SOURCE_ONLY:-0}" = 1 ] && return 0 2>/dev/null

if scan_procs game || scan_procs loader; then running=1; else running=0; fi
if scan_procs wrapper; then wrapper=1; else wrapper=0; fi
if pgrep -x steam >/dev/null; then steam=1; else steam=0; fi
if prefix_wineserver_alive; then stale=1; else stale=0; fi

# Decide the branch.
if [ "$running" = 1 ]; then action=refuse; why="the game (or its loader) is already running; quit it first (console: qqq)"
elif [ "$steam" = 0 ]; then action=refuse; why="Steam is not running; start Steam first (without it the game hands off to Steam and exits)"
elif [ "$wrapper" = 1 ] && [ "$stale" = 0 ]; then action=refuse; why="a launch is already pending (a proton waitforexitandrun wrapper for this prefix is alive); wait for it"
elif [ "$stale" = 1 ] && [ "$wrapper" = 1 ]; then action=kill_only; why="a leftover Wine session is blocking a pending launch; stop it so that launch proceeds (no second launch is started)"
elif [ "$stale" = 1 ]; then action=kill_launch; why="a leftover Wine session for this prefix would hang the launch; stop it, then launch"
else action=launch; why="nothing in the way"
fi

if [ "${LAUNCH_DRY_RUN:-0}" = 1 ]; then
  echo "dry-run: steam=$steam game_or_loader_running=$running pending_wrapper=$wrapper stale_wineserver=$stale"
  echo "dry-run: would $action: $why"
  exit 0
fi
if [ "$action" = refuse ]; then echo "launch_game: $why" >&2; exit 1; fi

mkdir -p "$SKYTHON_STATE"
if [ "$action" = kill_only ] || [ "$action" = kill_launch ]; then
  WINEPREFIX="$PFX_DIR" "$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/files/bin/wineserver" -k || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do prefix_wineserver_alive || break; sleep 1; done
  if prefix_wineserver_alive; then
    echo "launch_game: the leftover wineserver for this prefix is still alive after 'wineserver -k' and 10 s; not launching" >&2
    exit 1
  fi
  echo "launch_game: stopped a leftover Wine session for this prefix" >&2
  if [ "$action" = kill_only ]; then
    : > "$SKYTHON_STATE/launch.stamp"
    echo "launch_game: the pending launch will now proceed; not starting a second one"
    exit 0
  fi
fi
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$STEAM_ROOT"
export STEAM_COMPAT_DATA_PATH="$COMPAT_DIR"
export STEAM_COMPAT_MOUNTS="${STEAM_COMPAT_MOUNTS:-}"
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/$(id -u)/gdm/Xauthority}"
PROTON="${PROTON:-$STEAM_ROOT/compatibilitytools.d/GE-Proton10-14/proton}"
cd "$SKYRIM_GAME_DIR"
: > "$SKYTHON_STATE/launch.stamp"   # logs older than this stamp belong to an earlier run
nohup "$PROTON" waitforexitandrun "$(pwd)/skse64_loader.exe" > "$SKYTHON_STATE/launch.log" 2>&1 &
echo "launched pid $! (log: $SKYTHON_STATE/launch.log)"
