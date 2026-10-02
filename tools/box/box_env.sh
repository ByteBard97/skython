# source tools/box/box_env.sh  -- the Windows box game harness: paths and the only ssh/scp wrappers.
# The box's OpenSSH default shell is Windows PowerShell 5.1, so every remote command is PowerShell.
# Every ssh/scp call goes through bssh/bssh_t/bscp/bps (timeout + ConnectTimeout + BatchMode), or through
# btunnel_* for the port-forwarding tunnel. Nothing here deletes anything.
# BOX_OFFLINE=1 (set by tools/box/test_box_tools.sh): every wrapper refuses, prints "BOX_OFFLINE: refusing <cmd>"
# and exits non-zero, so an offline check can never reach the box, even through a missing dry-run mode.
# BOX_SSH_STUB=<program> (offline tests only, honoured only together with BOX_OFFLINE=1): bssh/bssh_t/bps run
# that program with the PowerShell command instead of ssh, so a test can drive a script's checks (a save count, a
# deploy target that exists) with canned answers. bscp and the tunnels still refuse.
box_die() { echo "box: $*" >&2; exit 1; }
BOX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# This machine's box settings live in tools/box/box.local.env (gitignored; template box.local.env.example).
# BOX_LOCAL_ENV names another file. BOX and BOX_GAME set in the environment win over the file.
BOX_LOCAL_ENV="${BOX_LOCAL_ENV:-$BOX_ROOT/tools/box/box.local.env}"
BOX_EXAMPLE_ENV="$BOX_ROOT/tools/box/box.local.env.example"
[ -f "$BOX_LOCAL_ENV" ] || box_die "no box settings file $BOX_LOCAL_ENV: copy $BOX_EXAMPLE_ENV to it and fill in your box"
_box_env_box="${BOX:-}"; _box_env_game="${BOX_GAME:-}"
# shellcheck source=/dev/null
source "$BOX_LOCAL_ENV" || box_die "could not read $BOX_LOCAL_ENV"
[ -n "$_box_env_box" ] && BOX="$_box_env_box"
[ -n "$_box_env_game" ] && BOX_GAME="$_box_env_game"
unset _box_env_box _box_env_game
for _v in BOX BOX_WIN_USER BOX_MYGAMES BOX_GAME_DIR_AE1170 BOX_GAME_DIR_AE17104 BOX_GAME; do
  [ -n "${!_v:-}" ] || box_die "$_v is not set in $BOX_LOCAL_ENV (see $BOX_EXAMPLE_ENV)"
done
# Windows paths: a drive, backslashes, no trailing backslash, no '..', and no quote, tab or newline (they are
# embedded in single-quoted PowerShell strings and in the tab-separated box.config).
for _v in BOX_MYGAMES BOX_GAME_DIR_AE1170 BOX_GAME_DIR_AE17104; do
  case "${!_v}" in
    [A-Za-z]:\\*[!\\]) ;;
    *) box_die "$_v='${!_v}' in $BOX_LOCAL_ENV is not a Windows path like C:\\Games\\Skyrim (no trailing backslash)" ;;
  esac
  case "${!_v}" in *..*|*\'*|*\"*|*$'\t'*|*$'\n'*|*$'\r'*) box_die "$_v='${!_v}' in $BOX_LOCAL_ENV holds '..', a quote or a control character" ;; esac
done
[[ "$BOX_WIN_USER" =~ ^[A-Za-z0-9._-]+$ ]] || box_die "BOX_WIN_USER='$BOX_WIN_USER' in $BOX_LOCAL_ENV is not a plain account name"
unset _v
# The installs the tools know, each with the SkyrimSE.exe version launch.ps1 accepts for it (the same table is in
# win/config_lib.ps1). BOX_GAME picks one; everything under the game folder derives from it.
BOX_GAMES=(ae1170 ae17104)
box_game_version() { case "$1" in ae1170) echo 1.6.1170.0 ;; ae17104) echo 1.7.104.0 ;; *) return 1 ;; esac; }
box_game_dir() { case "$1" in ae1170) echo "$BOX_GAME_DIR_AE1170" ;; ae17104) echo "$BOX_GAME_DIR_AE17104" ;; *) return 1 ;; esac; }
BOX_GAME_VERSION="$(box_game_version "$BOX_GAME")" || box_die "BOX_GAME='$BOX_GAME' is not one of: ${BOX_GAMES[*]}"
BOX_GAME_DIR="$(box_game_dir "$BOX_GAME")"
BOX_SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
BOX_DATA="$BOX_GAME_DIR\\Data"
BOX_PLUGINS="$BOX_DATA\\SKSE\\Plugins"
BOX_OWN="$BOX_PLUGINS\\Skython"
# SkyrimPythonSpike.dll still reads Plugins\SkyrimPython\ (spike-autostart, spike-disable, python\): a spike
# re-run needs BOX_OWN pointed back at ...\Plugins\SkyrimPython for its --file/--python deploys.
BOX_SKSE_LOG_DIR="$BOX_MYGAMES\\SKSE"
BOX_SAVES_DIR="$BOX_MYGAMES\\Saves"
BOX_DIR='C:\dev\skyrim-python-box'
BOX_DIR_FWD='C:/dev/skyrim-python-box'          # scp wants forward slashes
BOX_TASK=SkyrimPythonGame
BOX_DEVBENCH_URL='http://127.0.0.1:8920'
# box_stubbed: true only in an offline test that supplies BOX_SSH_STUB (see above).
box_stubbed() { [ "${BOX_OFFLINE:-0}" = 1 ] && [ -n "${BOX_SSH_STUB:-}" ]; }
# The deploy record. An override (BOX_DEPLOYED=...) is honoured only with BOX_DEPLOY_PRINT_TARGET=1 or under
# box_stubbed (offline tests): a real deploy always records in out/box-deployed.txt.
if { [ "${BOX_DEPLOY_PRINT_TARGET:-0}" != 1 ] && ! box_stubbed; } || [ -z "${BOX_DEPLOYED:-}" ]; then BOX_DEPLOYED="$BOX_ROOT/out/box-deployed.txt"; fi
# Gate 0: out/gate0-accepted records, with a date, that the test ESP and its scripts may go into the game. --esp,
# --test-pex and box_plugins.sh backup/add refuse without it. An override (BOX_GATE0_FILE=...) is honoured only
# under box_stubbed (offline tests).
if ! box_stubbed || [ -z "${BOX_GATE0_FILE:-}" ]; then BOX_GATE0_FILE="$BOX_ROOT/out/gate0-accepted"; fi
need_gate0() {
  [ -s "$BOX_GATE0_FILE" ] || box_die "Gate 0 is not recorded ($BOX_GATE0_FILE is missing): the test ESP may go into the game only once it is accepted"
}
SKYTHON_STATE="${SKYTHON_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/skython}"
BOX_TUNNEL_SOCK="$SKYTHON_STATE/box-tunnel.sock"
BOX_SESSION_SOCK="$SKYTHON_STATE/box-session.sock"   # the dev-bridge tunnel (box_session.sh)
export BOX BOX_GAME BOX_GAME_VERSION BOX_GAME_DIR BOX_DATA BOX_PLUGINS BOX_OWN BOX_MYGAMES BOX_SKSE_LOG_DIR BOX_SAVES_DIR BOX_DIR BOX_TASK BOX_WIN_USER SKYTHON_STATE

# box_offline <what>: refuse (exit 97) when BOX_OFFLINE=1. Every wrapper below calls it first.
box_offline() { if [ "${BOX_OFFLINE:-0}" = 1 ]; then echo "BOX_OFFLINE: refusing $*" >&2; exit 97; fi; }
# bssh_t <seconds> <powershell command>: one bounded remote command.
bssh_t() {
  if box_stubbed; then shift; "$BOX_SSH_STUB" "$@"; return; fi
  box_offline "ssh $*"; local t="$1"; shift; timeout "$t" ssh "${BOX_SSH_OPTS[@]}" "$BOX" "$@"
}
# bssh <powershell command>: bounded at 60 s.
bssh() { bssh_t 60 "$@"; }
# bscp <scp args...>: bounded at 600 s.
bscp() { box_offline "scp $*"; timeout 600 scp "${BOX_SSH_OPTS[@]}" -q "$@"; }
# bps <seconds> <script.ps1> [args...]: run one of our scripts in C:\dev\skyrim-python-box (args must not contain
# quotes or spaces: pass text through b64).
bps() { box_stubbed || box_offline "bps $*"; local t="$1" s="$2"; shift 2; bssh_t "$t" "powershell -NoProfile -ExecutionPolicy Bypass -File $BOX_DIR\\$s $*"; }
# b64 <text>: base64 of the UTF-8 text, one line (decoded on the box by our scripts).
b64() { printf '%s' "$1" | base64 -w0; }

# The tunnels: one background ssh each, with a control socket (TUNNEL_SOCK, default BOX_TUNNEL_SOCK; box_session.sh
# passes BOX_SESSION_SOCK). The connect itself is bounded (timeout); with -f the daemonised child outlives the
# timeout's watched process by design.
btunnel_start() { box_offline "tunnel $*"; timeout 30 ssh "${BOX_SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -f -N -M -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" "$@" "$BOX"; }
btunnel_ctl() { box_offline "tunnel control $*"; timeout 15 ssh -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" -O "$1" "$BOX"; }
