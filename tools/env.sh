# source tools/env.sh
export SIMPLESKYRIM_DATA="$HOME/Games/SimpleSkyrim/Stock Game/Data"
PFX="$HOME/.local/share/Steam/steamapps/compatdata/4190904831/pfx"
export SKSE_LOG_DIR="$PFX/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE"
export SKYTHON_SESSION="$SKSE_LOG_DIR/Skython/session.json"
export SKYTHON_LOG="$SKSE_LOG_DIR/Skython/python.log"
export SKYTHON_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/skython"
