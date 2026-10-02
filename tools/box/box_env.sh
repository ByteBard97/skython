# source tools/box/box_env.sh  -- the Windows box game harness: paths and the only ssh/scp wrappers.
# The box's OpenSSH default shell is Windows PowerShell 5.1, so every remote command is PowerShell.
# Every ssh/scp call goes through bssh/bssh_t/bscp/bps (timeout + ConnectTimeout + BatchMode), or through
# btunnel_* for the port-forwarding tunnel. Nothing here deletes anything.
# BOX_OFFLINE=1 (set by tools/box/test_box_tools.sh): every wrapper refuses, prints "BOX_OFFLINE: refusing <cmd>"
# and exits non-zero, so an offline check can never reach the box, even through a missing dry-run mode.
BOX="${BOX:-geoff@192.168.0.211}"
BOX_SSH_OPTS=(-o ConnectTimeout=10 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
BOX_GAME_DIR='C:\ChronicleDev\Stock Game'
BOX_DATA='C:\ChronicleDev\Stock Game\Data'
BOX_PLUGINS='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins'
BOX_OWN='C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\SkyrimPython'
BOX_MYGAMES='C:\Users\geoff\Documents\My Games\Skyrim Special Edition'
BOX_SKSE_LOG_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE'
BOX_SAVES_DIR='C:\Users\geoff\Documents\My Games\Skyrim Special Edition\Saves'
BOX_DIR='C:\dev\skyrim-python-box'
BOX_DIR_FWD='C:/dev/skyrim-python-box'          # scp wants forward slashes
BOX_TASK=SkyrimPythonGame
BOX_DEVBENCH_URL='http://127.0.0.1:8920'
BOX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# The deploy record. An override (BOX_DEPLOYED=...) is honoured only with BOX_DEPLOY_PRINT_TARGET=1 (offline tests):
# a real deploy always records in out/box-deployed.txt.
if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" != 1 ] || [ -z "${BOX_DEPLOYED:-}" ]; then BOX_DEPLOYED="$BOX_ROOT/out/box-deployed.txt"; fi
SKYRIM_PYTHON_STATE="${SKYRIM_PYTHON_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/skyrim-python}"
BOX_TUNNEL_SOCK="$SKYRIM_PYTHON_STATE/box-tunnel.sock"
BOX_SESSION_SOCK="$SKYRIM_PYTHON_STATE/box-session.sock"   # the dev-bridge tunnel (box_session.sh)
export BOX BOX_GAME_DIR BOX_DATA BOX_PLUGINS BOX_OWN BOX_MYGAMES BOX_SKSE_LOG_DIR BOX_SAVES_DIR BOX_DIR BOX_TASK SKYRIM_PYTHON_STATE

# box_offline <what>: refuse (exit 97) when BOX_OFFLINE=1. Every wrapper below calls it first.
box_offline() { if [ "${BOX_OFFLINE:-0}" = 1 ]; then echo "BOX_OFFLINE: refusing $*" >&2; exit 97; fi; }
# bssh_t <seconds> <powershell command>: one bounded remote command.
bssh_t() { box_offline "ssh $*"; local t="$1"; shift; timeout "$t" ssh "${BOX_SSH_OPTS[@]}" "$BOX" "$@"; }
# bssh <powershell command>: bounded at 60 s.
bssh() { bssh_t 60 "$@"; }
# bscp <scp args...>: bounded at 600 s.
bscp() { box_offline "scp $*"; timeout 600 scp "${BOX_SSH_OPTS[@]}" -q "$@"; }
# bps <seconds> <script.ps1> [args...]: run one of our scripts in C:\dev\skyrim-python-box (args must not contain
# quotes or spaces: pass text through b64).
bps() { box_offline "bps $*"; local t="$1" s="$2"; shift 2; bssh_t "$t" "powershell -NoProfile -ExecutionPolicy Bypass -File $BOX_DIR\\$s $*"; }
# b64 <text>: base64 of the UTF-8 text, one line (decoded on the box by our scripts).
b64() { printf '%s' "$1" | base64 -w0; }
box_die() { echo "box: $*" >&2; exit 1; }

# The tunnels: one background ssh each, with a control socket (TUNNEL_SOCK, default BOX_TUNNEL_SOCK; box_session.sh
# passes BOX_SESSION_SOCK). The connect itself is bounded (timeout); with -f the daemonised child outlives the
# timeout's watched process by design.
btunnel_start() { box_offline "tunnel $*"; timeout 30 ssh "${BOX_SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -f -N -M -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" "$@" "$BOX"; }
btunnel_ctl() { box_offline "tunnel control $*"; timeout 15 ssh -S "${TUNNEL_SOCK:-$BOX_TUNNEL_SOCK}" -O "$1" "$BOX"; }
