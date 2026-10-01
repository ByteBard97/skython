#!/usr/bin/env bash
# Usage: [SINCE=<file>] tools/wait_for_log.sh <logfile> <egrep-regex> [timeout-seconds=600]
# Bounded replacement for 'until grep ...; do sleep; done'. With SINCE set, the log must also be newer
# than that file (so a log left by an earlier run cannot satisfy the wait).
set -uo pipefail
f="${1:?logfile}"; pat="${2:?regex}"; limit="${3:-600}"
end=$(( $(date +%s) + limit ))
while :; do
  if [ -f "$f" ] && { [ -z "${SINCE:-}" ] || [ "$f" -nt "$SINCE" ]; } && grep -q -E -- "$pat" "$f"; then exit 0; fi
  if [ "$(date +%s)" -ge "$end" ]; then
    echo "wait_for_log: no match for /$pat/ in $f after ${limit}s" >&2
    if [ -f "$f" ]; then echo "--- last 30 lines of $f" >&2; tail -n 30 "$f" >&2; else echo "(the file does not exist)" >&2; fi
    exit 1
  fi
  sleep 2
done
