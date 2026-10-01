#!/usr/bin/env bash
# Usage: tools/box/box_wait_log.sh <log name> <regex> [seconds=600]
#   <log name> is relative to the box's SKSE log folder (C:\Users\geoff\Documents\My Games\Skyrim Special
#   Edition\SKSE), e.g. SkyrimPythonSpike.log or SkyrimPython/python.log. The regex is .NET (-match); ERE
#   patterns used in this plan mean the same. Only text written after the last box_launch.sh stamp counts.
# Exit 0 and "MATCH <line>" on a match; exit 1 with the tail of this launch's text on timeout.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
name="${1:?log name}"; pat="${2:?regex}"; secs="${3:-600}"
out="$(bps $((secs + 60)) wait_log.ps1 -NameB64 "$(b64 "$name")" -PatB64 "$(b64 "$pat")" -Seconds "$secs" | tr -d '\r')" && rc=0 || rc=$?
echo "$out"
exit "$rc"
