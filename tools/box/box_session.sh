#!/usr/bin/env bash
# The dev bridge from Linux while the game runs on the box.
# Usage: tools/box/box_session.sh up | env | down | status | probe [PORT] | rewrite IN|- OUT LOCAL_PORT|same
#   up:      after each box launch, once the core log shows "python host: running". Reads the box's
#            SKSE\Skython\session.json written since the last launch stamp (a file left by an earlier launch is
#            refused), writes out/box-session.json (mode 600; the bridge token lives only there, never printed) with
#            "port" set to the tunnel's local end (BOX_SESSION_LOCAL_PORT, default the box's port, 45977 with the dev
#            ini) and "host", if present, to 127.0.0.1; then brings up, or keeps, one ssh tunnel
#            Linux 127.0.0.1:LOCAL -> box 127.0.0.1:PORT (control socket $SKYTHON_STATE/box-session.sock).
#            Loopback on both ends. An inactive session file ("active": false) is written as is (the live tests'
#            load_session reports its reason) and no tunnel is needed. Prints the session file's path last.
#   env:     writes out/box-live.env (also done by up). `source out/box-live.env` sets SKYTHON_SESSION to
#            out/box-session.json, SKSE_LOG_DIR and SKYTHON_LOG to the log mirror out/box-skse (box_files.sh
#            pull) and SKYTHON_BOX=1 (livetest/support.py's box mode). No token in it.
#   down / status: the session tunnel.
#   probe [PORT]: on the box, "LISTEN pid <n>" (exit 0) or "NO_LISTENER" (exit 1) for 127.0.0.1:PORT (default
#            45977). Through the tunnel a missing listener does not look refused, so the live tests ask the box.
#   rewrite: the offline part of up (tested by test_box_tools.sh): IN (or - for stdin) -> OUT; prints
#            "BOXPORT <port>" or "INACTIVE <reason>", never the token.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
SESSION_OUT="$BOX_ROOT/out/box-session.json"
ENV_OUT="$BOX_ROOT/out/box-live.env"
FWD_STATE="$SKYTHON_STATE/box-session.fwd"

# The rewrite program (python3 -c, so stdin stays free for the session JSON).
REWRITE_PY="$(cat <<'PY'
import json, os, sys
src, out, local = sys.argv[1:4]
data = json.load(sys.stdin if src == "-" else open(src, encoding="utf-8"))
if not isinstance(data, dict):
    sys.exit("box_session: session.json is not an object")
def port(v, what):
    if isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 65536:
        sys.exit(f"box_session: bad {what}: {v!r}")
    return v
if data.get("active") is False:
    msg = f"INACTIVE {data.get('reason')}"
else:
    box_port = port(data.get("port"), "port in session.json")
    if not isinstance(data.get("token"), str):
        sys.exit("box_session: session.json has no token")
    data["port"] = box_port if local == "same" else port(int(local) if local.isdigit() else local, "local port")
    if "host" in data:
        data["host"] = "127.0.0.1"
    msg = f"BOXPORT {box_port}"
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(data, f)
print(msg)
PY
)"
rewrite() { python3 -c "$REWRITE_PY" "$1" "$2" "$3"; }  # rewrite <in|-> <out> <local port|same>

write_env() {
  mkdir -p "$BOX_ROOT/out"
  cat > "$ENV_OUT" <<ENV
# Written by tools/box/box_session.sh: the live tests against the game on the box. No token here.
export SKYTHON_SESSION="$SESSION_OUT"
export SKSE_LOG_DIR="$BOX_ROOT/out/box-skse"
export SKYTHON_LOG="$BOX_ROOT/out/box-skse/Skython/python.log"
export SKYTHON_BOX=1
export SPY_GAME_VERSION="$BOX_GAME_VERSION"
ENV
  echo "env: source $ENV_OUT"
}

case "${1:-}" in
  rewrite) rewrite "${2:?IN}" "${3:?OUT}" "${4:?LOCAL_PORT}" ;;
  env) write_env ;;
  up)
    write_env
    mkdir -p "$SKYTHON_STATE"
    st="$(bps 60 files.ps1 -Mode session | tr -d '\r')" || box_die "could not read the box's session.json: ${st%% *}"
    case "$st" in
      "NEW "*) ;;
      UNCHANGED) box_die "session.json was not written in this launch (wait for 'python host: running' in Skython.log)" ;;
      ABSENT) box_die "no session.json on the box (python host not running yet, or disabled)" ;;
      *) box_die "session.json: ${st%% *}" ;;
    esac
    res="$(printf '%s' "${st#NEW }" | base64 -d | rewrite - "$SESSION_OUT" "${BOX_SESSION_LOCAL_PORT:-same}")" || box_die "rewrite failed"
    echo "$res"
    case "$res" in
      "INACTIVE "*) echo "the bridge is not serving; no tunnel" ;;
      "BOXPORT "*)
        PORT="${res#BOXPORT }"; LOCAL="${BOX_SESSION_LOCAL_PORT:-$PORT}"
        export TUNNEL_SOCK="$BOX_SESSION_SOCK"
        if btunnel_ctl check 2>/dev/null; then
          if [ "$(cat "$FWD_STATE" 2>/dev/null)" = "$LOCAL $PORT" ]; then echo "session tunnel already up: 127.0.0.1:$LOCAL -> box 127.0.0.1:$PORT"
          else btunnel_ctl exit; fi
        fi
        if ! btunnel_ctl check 2>/dev/null; then
          btunnel_start -L "127.0.0.1:$LOCAL:127.0.0.1:$PORT" || box_die "session tunnel did not come up (local port $LOCAL in use, or the box is unreachable)"
          echo "$LOCAL $PORT" > "$FWD_STATE"
          echo "session tunnel up: 127.0.0.1:$LOCAL -> box 127.0.0.1:$PORT"
        fi ;;
    esac
    echo "$SESSION_OUT"
    ;;
  down)
    export TUNNEL_SOCK="$BOX_SESSION_SOCK"
    if btunnel_ctl check 2>/dev/null; then btunnel_ctl exit; echo "session tunnel down"; else echo "session tunnel not running"; fi ;;
  status)
    export TUNNEL_SOCK="$BOX_SESSION_SOCK"; btunnel_ctl check 2>&1 || true ;;
  probe)
    p="${2:-45977}"; [[ "$p" =~ ^[0-9]+$ ]] || box_die "probe: bad port '$p'"
    r="$(bssh "\$c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; if (\$c) { 'LISTEN pid ' + \$c.OwningProcess } else { 'NO_LISTENER' }" | tr -d '\r')"
    echo "$r"; case "$r" in "LISTEN "*) exit 0 ;; *) exit 1 ;; esac ;;
  *) box_die "usage: box_session.sh up | env | down | status | probe [PORT] | rewrite IN|- OUT LOCAL_PORT|same" ;;
esac
