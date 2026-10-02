#!/usr/bin/env bash
# Run a game console command on the box through DevBench (in-game REST server on the box's 127.0.0.1:8920).
#   tools/box/box_console.sh "<console command>"     POST /api/tool/console {"command": "<console command>"}
#   tools/box/box_console.sh --tool NAME ['<json>']   POST /api/tool/NAME with that body (default {})
#   tools/box/box_console.sh --list                   GET /api/tools (DevBench's tool list and schemas)
# Prints DevBench's reply; exit 1 on an HTTP or connection error. DevBench answers only while the game runs.
# A `save NAME` console command (any case, also through --tool console) is sent only when
# tools/box/box_saves.sh count NAME prints 0: it never saves over an existing save. If the count cannot be read
# (box unreachable, a NAME that is not [A-Za-z0-9_]), the command is refused.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
save_guard() {  # save_guard <console command>: die unless it is not a save, or a save to a new name
  local name n
  name="$(python3 -c 'import re, sys; m = re.match(r"\s*save\s+(\S+)", sys.argv[1], re.I); print(m.group(1) if m else "")' "$1")"
  [ -n "$name" ] || return 0
  n="$("$(dirname "$0")/box_saves.sh" count "$name" 2>/dev/null)" || n="unknown"
  [ "$n" = 0 ] || box_die "refusing '$1': box_saves.sh count $name says '$n' (must be 0: never save over an existing save)"
}
case "${1:-}" in
  --list) bps 60 console.ps1 -List | tr -d '\r' ;;
  --tool) tool="${2:?tool name}"; body="${3:-{\}}"
          if [ "$tool" = console ]; then
            cmd="$(python3 -c 'import json, sys; c = json.loads(sys.argv[1]).get("command"); print(c if isinstance(c, str) else "")' "$body")" \
              || box_die "--tool console: the body is not a JSON object: $body"
            save_guard "$cmd"
          fi
          bps 60 console.ps1 -Tool "$tool" -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
  "") box_die "usage: box_console.sh \"<console command>\" | --tool NAME [json] | --list" ;;
  *) save_guard "$1"
     body="$(python3 -c 'import json, sys; print(json.dumps({"command": sys.argv[1]}))' "$1")"
     bps 60 console.ps1 -Tool console -JsonB64 "$(b64 "$body")" | tr -d '\r' ;;
esac
