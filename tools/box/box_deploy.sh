#!/usr/bin/env bash
# Deploy into the box's game (C:\ChronicleDev\Stock Game\Data\SKSE\Plugins), the box twin of tools/deploy.sh.
# Usage:
#   tools/box/box_deploy.sh out/<Dll>.dll [path/to/ini]      -> Plugins\<Dll>.dll [+ Plugins\<Dll>.ini]
#                                                               (SkyrimPython.dll also deploys out/SkyrimPythonCore.dll
#                                                                to Plugins\SkyrimPython\, which must not be older)
#   tools/box/box_deploy.sh --python [out/<Core>.dll]        -> Plugins\SkyrimPython\python\ (third_party/cpython/embed)
#                                                               [+ the core DLL into Plugins\SkyrimPython\]
#   tools/box/box_deploy.sh --site-packages DIR              -> Plugins\SkyrimPython\site-packages\<entries>
#   tools/box/box_deploy.sh --package                        -> Plugins\SkyrimPython\lib\skyrim\ (python/src/skyrim)
#   tools/box/box_deploy.sh --scripts DIR                    -> Plugins\SkyrimPython\scripts\<entries>
#   tools/box/box_deploy.sh --file LOCAL REL                 -> Plugins\SkyrimPython\REL (one file: s10_check.py, markers)
#   tools/box/box_deploy.sh --pex path/to/SkyrimPython.pex   -> exactly Data\Scripts\SkyrimPython.pex (Task 7; the only
#                                                               target outside Plugins; placed only if absent or ours)
#   BOX_DEPLOY_PRINT_TARGET=1 with --pex prints the resolved target and exits before any ssh (offline test).
# Every target is recorded in out/box-deployed.txt. A target that exists on the box and is not recorded there
# was not deployed by this tool: the deploy refuses (it never overwrites what it did not put there).
# A *Spike* DLL needs CONFIRM_THROWAWAY_SAVE=1. Refuses while the game runs (DLLs are locked).
# Files are staged under C:\dev\skyrim-python-box\stage (no spaces for scp), then copied into place on the box.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$BOX_ROOT/out/box-stage"; touch "$BOX_DEPLOYED"

recorded() { grep -qxF -- "$1" "$BOX_DEPLOYED"; }
rec() { recorded "$1" || echo "$1" >> "$BOX_DEPLOYED"; }
check_target() {  # refuse a target that exists on the box and was not deployed by this tool
  recorded "$1" && return 0
  local r; r="$(bps 60 place.ps1 -Mode exists -DestB64 "$(b64 "$1")" | tr -d '\r')"
  case "$r" in
    ABSENT) return 0 ;;
    EXISTS) box_die "$1 exists on the box but is not in out/box-deployed.txt; not overwriting it. Ask the owner." ;;
    *) box_die "could not check $1: $r" ;;
  esac
}
stage_id() { echo "$(date +%Y%m%d-%H%M%S)-$$-$RANDOM"; }
put_file() {  # put_file <local file> <windows dest>
  local src="$1" dest="$2" id r sum
  [ -f "$src" ] || box_die "no such file: $src"
  check_target "$dest"
  id="$(stage_id)-$(basename "$src")"
  bscp "$src" "$BOX:$BOX_DIR_FWD/stage/$id" || box_die "scp $src failed"
  r="$(bps 120 place.ps1 -Mode place -Kind file -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" | tr -d '\r')"
  sum="$(sha256sum "$src" | cut -d' ' -f1)"
  [ "$r" = "PLACED $sum" ] || box_die "placing $dest: '$r' (expected PLACED $sum)"
  rec "$dest"; echo "deployed $src -> $dest"
}
put_tree() {  # put_tree <local dir> <windows dest dir>: merge the tree (no __pycache__) into dest
  local src="$1" dest="$2" id tarf r
  [ -d "$src" ] || box_die "no such directory: $src"
  check_target "$dest"
  id="$(stage_id)-$(basename "$src").tar"; tarf="$BOX_ROOT/out/box-stage/$id"
  tar -C "$src" --exclude=__pycache__ -cf "$tarf" .
  bscp "$tarf" "$BOX:$BOX_DIR_FWD/stage/$id" || box_die "scp $tarf failed"
  r="$(bps 600 place.ps1 -Mode place -Kind tar -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" | tr -d '\r')"
  case "$r" in PLACED*) ;; *) box_die "placing $dest: '$r'" ;; esac
  rec "$dest"; echo "deployed $src/ -> $dest ($r)"
}
put_entries() {  # put_entries <local dir> <windows dest dir>: each top-level entry is its own recorded target
  local src="$1" dest="$2" e
  [ -d "$src" ] || box_die "no such directory: $src"
  for e in "$src"/* "$src"/.[!.]*; do
    [ -e "$e" ] || continue
    [ "$(basename "$e")" = __pycache__ ] && continue
    if [ -d "$e" ]; then put_tree "$e" "$dest\\$(basename "$e")"; else put_file "$e" "$dest\\$(basename "$e")"; fi
  done
}
spike_ok() { case "$(basename "$1")" in *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || box_die "$1 is spike code (unauthenticated port); use a throwaway save and re-run with CONFIRM_THROWAWAY_SAVE=1" ;; esac; }

[ $# -ge 1 ] || box_die "usage: see the header of $0"
if [ "$1" = --pex ]; then
  PEX="${2:?usage: box_deploy.sh --pex path/to/SkyrimPython.pex}"
  [ -f "$PEX" ] || box_die "no such file: $PEX"
  [ "$(basename "$PEX")" = SkyrimPython.pex ] || box_die "--pex places only SkyrimPython.pex (got $(basename "$PEX"))"
  PEX_DEST="$BOX_DATA\\Scripts\\SkyrimPython.pex"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$PEX_DEST"; exit 0; fi
fi
st="$(bssh 'if (Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue) { "RUNNING" } else { "IDLE" }' | tr -d '\r')" \
  || box_die "cannot ssh to $BOX (STOP and ask the owner)"
[ "$st" = IDLE ] || box_die "the game is running on the box; quit it first (tools/box/box_quit.sh)"

case "$1" in
  --python)
    "$BOX_ROOT/tools/fetch_python.sh" --check >/dev/null || box_die "third_party/cpython does not match docs/PINS.md"
    [ -f "$BOX_ROOT/third_party/cpython/embed/python314.dll" ] && [ -f "$BOX_ROOT/third_party/cpython/embed/LICENSE.txt" ] || box_die "embed runtime incomplete"
    if [ $# -ge 2 ]; then spike_ok "$2"; [ -f "$2" ] || box_die "no such file: $2"; fi
    put_tree "$BOX_ROOT/third_party/cpython/embed" "$BOX_OWN\\python"
    [ $# -ge 2 ] && put_file "$2" "$BOX_OWN\\$(basename "$2")"
    ;;
  --site-packages)
    SP="${2:?usage: box_deploy.sh --site-packages DIR}"; [ -d "$SP" ] || box_die "no such directory: $SP"
    have="$(bps 60 place.ps1 -Mode list -DestB64 "$(b64 "$BOX_OWN\\site-packages")" | tr -d '\r' | sort)"
    stale="$(comm -23 <(echo "$have" | sed '/^$/d') <(ls -A "$SP" | sort))"
    [ -z "$stale" ] || { echo "$stale" >&2; box_die "the box's site-packages holds the entries above that $SP lacks; ask the owner (nothing is deleted)"; }
    put_entries "$SP" "$BOX_OWN\\site-packages"
    ;;
  --package)
    [ -f "$BOX_ROOT/python/src/skyrim/__init__.py" ] || box_die "python/src/skyrim is missing"
    put_tree "$BOX_ROOT/python/src/skyrim" "$BOX_OWN\\lib\\skyrim"
    ;;
  --scripts)
    put_entries "${2:?usage: box_deploy.sh --scripts DIR}" "$BOX_OWN\\scripts"
    ;;
  --file)
    REL="${3:?usage: box_deploy.sh --file LOCAL REL}"
    case "$REL" in *..*) box_die "REL must not contain .." ;; esac
    put_file "$2" "$BOX_OWN\\${REL//\//\\}"
    ;;
  --pex)
    put_file "$PEX" "$PEX_DEST"
    ;;
  -*) box_die "unknown option $1" ;;
  *)
    DLL="$1"; [ -f "$DLL" ] || box_die "no such file: $DLL"; spike_ok "$DLL"
    base="$(basename "$DLL")"
    if [ "$base" = SkyrimPython.dll ]; then
      CORE="$(dirname "$DLL")/SkyrimPythonCore.dll"
      [ -f "$CORE" ] || box_die "$CORE missing: run tools/remote_build.sh plugin SkyrimPythonCore.dll"
      [ "$DLL" -nt "$CORE" ] && box_die "$CORE is older than $DLL: rebuild it"
    fi
    put_file "$DLL" "$BOX_PLUGINS\\$base"
    [ "$base" = SkyrimPython.dll ] && put_file "$CORE" "$BOX_OWN\\SkyrimPythonCore.dll"
    [ $# -ge 2 ] && put_file "$2" "$BOX_PLUGINS\\${base%.dll}.ini"
    ;;
esac
exit 0
