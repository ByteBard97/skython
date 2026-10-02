#!/usr/bin/env bash
# Usage: tools/box/box_files.sh pull NAME... | put-control LOCAL_FILE
#   pull: for each NAME relative to the box's SKSE log folder (Skython.log, SkythonLoader.log,
#         Skython/python.log, Skython/livetest_smoke.json, ...), writes the text written there since the last
#         box_launch.sh stamp to out/box-skse/NAME and prints that local path. The file is empty when nothing was
#         written in this launch (or it is absent), so a log or result from an earlier launch never counts. Never
#         session.json (the token stays only in box_session.sh's file). The live tests' box mode
#         (SKYTHON_BOX=1, livetest/support.py) calls this before every log read.
#   put-control: copies LOCAL_FILE to the box's SKSE\Skython\livetest_control.json, the only file Linux writes
#         there (livetest/smoke.py's command channel, Task 31).
#   BOX_FILES_PRINT=1 validates and prints the local targets (pull) and exits before any ssh (offline test).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
MIRROR="$BOX_ROOT/out/box-skse"
case "${1:-}" in
  pull)
    shift; [ $# -ge 1 ] || box_die "usage: box_files.sh pull NAME..."
    for n in "$@"; do
      case "$n" in ""|*..*|*:*|/*|\\*|*session.json) box_die "pull: refused name '$n'" ;; esac
    done
    if [ "${BOX_FILES_PRINT:-0}" = 1 ]; then for n in "$@"; do echo "$MIRROR/${n//\\//}"; done; exit 0; fi
    out="$(bps 120 files.ps1 -Mode new -NamesB64 "$(b64 "$(printf '%s\n' "$@")")" | tr -d '\r')" || box_die "pull failed: $out"
    while IFS=$'\t' read -r n state data; do
      [ -n "$n" ] || continue
      local_path="$MIRROR/${n//\\//}"; mkdir -p "$(dirname "$local_path")"
      case "$state" in
        NEW) printf '%s' "$data" | base64 -d > "$local_path" ;;
        UNCHANGED|ABSENT) : > "$local_path" ;;
        *) box_die "pull $n: $state" ;;
      esac
      echo "$local_path"
    done <<< "$out"
    ;;
  put-control)
    f="${2:?usage: box_files.sh put-control LOCAL_FILE}"; [ -f "$f" ] || box_die "no such file: $f"
    r="$(bps 60 files.ps1 -Mode put -NameB64 "$(b64 'Skython/livetest_control.json')" -DataB64 "$(base64 -w0 < "$f")" | tr -d '\r')"
    [ "$r" = 'PUT Skython\livetest_control.json' ] || box_die "put-control: '$r'"
    ;;
  *) box_die "usage: box_files.sh pull NAME... | put-control LOCAL_FILE" ;;
esac
