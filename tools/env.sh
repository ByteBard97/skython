# source tools/env.sh: the paths the Linux dev scripts use (deploy.sh, launch_game.sh, teardown_list.sh).
# Set these in your shell, or put them in tools/local.env (gitignored), which is read first:
#   SKYRIM_GAME_DIR  the game folder (SkyrimSE.exe, skse64_loader.exe, Data/)
#   SKYRIM_PREFIX    the pfx folder of the Proton prefix the game runs in
#                    (~/.local/share/Steam/steamapps/compatdata/<appid>/pfx)
_skython_tools="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$_skython_tools/local.env" ] && source "$_skython_tools/local.env"
unset _skython_tools
: "${SKYRIM_GAME_DIR:?set SKYRIM_GAME_DIR to the game folder (see tools/env.sh)}"
: "${SKYRIM_PREFIX:?set SKYRIM_PREFIX to the Proton prefix pfx folder (see tools/env.sh)}"
export SKYRIM_GAME_DIR SKYRIM_PREFIX
export SKYRIM_DATA="$SKYRIM_GAME_DIR/Data"
export SKSE_LOG_DIR="$SKYRIM_PREFIX/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE"
export SKYTHON_SESSION="$SKSE_LOG_DIR/Skython/session.json"
export SKYTHON_LOG="$SKSE_LOG_DIR/Skython/python.log"
export SKYTHON_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/skython"
