#!/usr/bin/env bash
# Run a game console command on the box through DevBench (in-game REST server on the box's 127.0.0.1:8920).
#   tools/box/box_console.sh "<console command>"     POST /api/tool/console {"command": "<console command>"}
#   tools/box/box_console.sh --tool NAME ['<json>']   POST /api/tool/NAME with that body (default {})
#   tools/box/box_console.sh --list                   GET /api/tools (DevBench's tool list and schemas)
# Prints DevBench's reply; exit 1 on an HTTP or connection error. DevBench answers only while the game runs.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
case "${1:-}" in
  --list) bps 60 console.ps1 -List | tr -d '\r' ;;
  --tool) tool="${2:?tool name}"; body="${3:-{\}}"
          bps 60 console.ps1 -Tool "$tool" -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
  "") box_die "usage: box_console.sh \"<console command>\" | --tool NAME [json] | --list" ;;
  *) body="$(python3 -c 'import json, sys; print(json.dumps({"command": sys.argv[1]}))' "$1")"
     bps 60 console.ps1 -Tool console -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
esac
