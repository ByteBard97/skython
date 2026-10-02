#!/usr/bin/env bash
# Usage: tools/box/box_saves.sh count NAME
#   Prints how many saves in the box's Saves folder match NAME*.ess (read-only). Used before a new throwaway save
#   (must print 0: never save over an existing name) and after it (the .ess was written). NAME is [A-Za-z0-9_]
#   only, checked before any ssh.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
[ "${1:-}" = count ] || box_die "usage: box_saves.sh count NAME"
name="${2:?usage: box_saves.sh count NAME}"
[[ "$name" =~ ^[A-Za-z0-9_]+$ ]] || box_die "save name must be [A-Za-z0-9_]+ (got '$name')"
bssh "@(Get-ChildItem '$BOX_SAVES_DIR' -Filter '$name*.ess' -ErrorAction SilentlyContinue).Count" | tr -d '\r'
