#!/usr/bin/env bash
# LISTS what the box tools put on the box (deployed paths from out/box-deployed.txt, C:\dev\skyrim-python-box,
# the scheduled tasks SkyrimPythonGame and SkyrimPythonShot, spike/plugin logs, throwaway saves). It removes nothing.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
paths=""; [ -f "$BOX_DEPLOYED" ] && paths="$(cat "$BOX_DEPLOYED")"
if [ -n "$paths" ]; then bps 120 list.ps1 -PathsB64 "$(b64 "$paths")"; else bps 120 list.ps1; fi | tr -d "\r"
