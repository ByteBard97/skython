#!/usr/bin/env bash
# Force-stop the game on the box: Stop-Process -Name SkyrimSE,skse64_loader (by name only), bounded 20 s check.
# Use box_quit.sh (console qqq) for a clean exit; this is for a hung game.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
bps 60 stop.ps1 | tr -d '\r'
