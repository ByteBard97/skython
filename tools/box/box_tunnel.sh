#!/usr/bin/env bash
# Usage: tools/box/box_tunnel.sh up [--reverse PORT]... | down | status
#   up: one background ssh (control socket $SKYRIM_PYTHON_STATE/box-tunnel.sock) forwarding Linux
#       127.0.0.1:45978 and :45979 to the box's 127.0.0.1:45978/:45979 (spike harness, S8 echo server), and
#       for each --reverse PORT the box's 127.0.0.1:PORT back to Linux 127.0.0.1:PORT (S10's HTTP server).
#       Fails loudly (ExitOnForwardFailure) if a local port is taken (e.g. the Linux game is running).
#   Note: through the tunnel a missing listener on the box shows as "server closed the connection"
#   (spike_client.py exit 1), not as ConnectionRefusedError.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$SKYRIM_PYTHON_STATE"
case "${1:-}" in
  up)
    shift
    if btunnel_ctl check 2>/dev/null; then echo "tunnel already up"; exit 0; fi
    fw=(-L 127.0.0.1:45978:127.0.0.1:45978 -L 127.0.0.1:45979:127.0.0.1:45979)
    while [ $# -gt 0 ]; do
      case "$1" in
        --reverse) p="${2:?--reverse PORT}"; fw+=(-R "127.0.0.1:$p:127.0.0.1:$p"); shift 2 ;;
        *) box_die "unknown option $1" ;;
      esac
    done
    btunnel_start "${fw[@]}" || box_die "tunnel did not come up (local port in use, or the box is unreachable)"
    btunnel_ctl check && echo "tunnel up: ${fw[*]}" ;;
  down)
    if btunnel_ctl check 2>/dev/null; then btunnel_ctl exit; echo "tunnel down"; else echo "tunnel not running"; fi ;;
  status)
    btunnel_ctl check 2>&1 || true ;;
  *) box_die "usage: box_tunnel.sh up [--reverse PORT]... | down | status" ;;
esac
