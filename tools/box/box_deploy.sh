#!/usr/bin/env bash
# Deploy into the box's game (<game folder>\Data\SKSE\Plugins), the box twin of tools/deploy.sh. The install is
# BOX_GAME (ae1170 or ae17104; default from tools/box/box.local.env), e.g. BOX_GAME=ae17104 tools/box/box_deploy.sh ...
# Usage:
#   tools/box/box_deploy.sh --where                          -> prints the selected install and its folders (no ssh)
#   tools/box/box_deploy.sh out/<Dll>.dll [path/to/ini]      -> Plugins\<Dll>.dll [+ Plugins\<Dll>.ini]
#                                                               (Skython.dll also deploys out/SkythonCore.dll
#                                                                to Plugins\Skython\, which must not be older)
#   tools/box/box_deploy.sh --python [out/<Core>.dll]        -> Plugins\Skython\python\ (third_party/cpython/embed)
#                                                               [+ the core DLL into Plugins\Skython\]
#   tools/box/box_deploy.sh --site-packages DIR              -> Plugins\Skython\site-packages\<entries>
#   tools/box/box_deploy.sh --package                        -> Plugins\Skython\lib\skython\ (python/src/skython)
#   tools/box/box_deploy.sh --scripts DIR                    -> Plugins\Skython\scripts\<entries>
#   tools/box/box_deploy.sh --file LOCAL REL                 -> Plugins\Skython\REL (one file: s10_check.py, markers)
#   tools/box/box_deploy.sh --pex path/to/SkyrimPython.pex   -> exactly Data\Scripts\SkyrimPython.pex (Task 7: the spike's
#                                                               own script, which keeps its name; the only
#                                                               target outside Plugins; placed only if absent or ours)
#   tools/box/box_deploy.sh --esp path/to/SkythonTest.esp    -> exactly Data\SkythonTest.esp (owner decision 7, Gate 0;
#                                                               Task 45). Placed only if absent or ours.
#   tools/box/box_deploy.sh --test-pex FILE.pex...           -> Data\Scripts\<name> for names on the Gate 0 list only:
#                                                               Skython.pex, SkythonTestQuestBase.pex, SkythonTestQuest.pex,
#                                                               SkythonTestAlias.pex, SkythonTestLocationAlias.pex,
#                                                               QF_SkythonTestQuest_01000800.pex
#   tools/box/box_deploy.sh --move REL NEWREL                -> moves Plugins\REL to Plugins\NEWREL (Task 26's startup
#                                                               variants; Task 25 moves the spike DLL aside). REL must be
#                                                               a recorded target or inside one; NEWREL must be absent.
#                                                               Move-Item, never a delete. Each move is appended to
#                                                               out/box-moves.txt and NEWREL is recorded.
#   BOX_DEPLOY_PRINT_TARGET=1 with --pex, --esp, --test-pex, a DLL or --move prints the resolved target(s) and exits
#   before any ssh
#   (offline test).
# Every target is recorded in out/box-deployed.txt (absolute paths, so each install's targets are its own). A target that exists on the box and is not recorded there
# was not deployed by this tool: the deploy refuses (it never overwrites what it did not put there).
# A *Spike* DLL needs CONFIRM_THROWAWAY_SAVE=1. Refuses while the game runs (DLLs are locked).
# Files are staged under C:\dev\skyrim-python-box\stage (no spaces for scp), then copied into place on the box.
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
mkdir -p "$BOX_ROOT/out/box-stage"
[ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ] || touch "$BOX_DEPLOYED"   # print mode never creates the record

recorded() { grep -qxF -- "$1" "$BOX_DEPLOYED"; }
rec() { recorded "$1" || echo "$1" >> "$BOX_DEPLOYED"; }
check_target() {  # refuse a target that exists on the box and was not deployed by this tool
  recorded "$1" && return 0
  local r; r="$(bps 60 place.ps1 -Mode exists -DestB64 "$(b64 "$1")" -Game "$BOX_GAME" | tr -d '\r')"
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
  r="$(bps 120 place.ps1 -Mode place -Kind file -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" -Game "$BOX_GAME" | tr -d '\r')"
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
  r="$(bps 600 place.ps1 -Mode place -Kind tar -SrcB64 "$(b64 "$BOX_DIR\\stage\\$id")" -DestB64 "$(b64 "$dest")" -Game "$BOX_GAME" | tr -d '\r')"
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
if [ "$1" = --where ]; then
  printf 'BOX_GAME=%s (SkyrimSE.exe %s)\nBOX_GAME_DIR=%s\nBOX_DATA=%s\nBOX_PLUGINS=%s\nBOX_OWN=%s\n' \
    "$BOX_GAME" "$BOX_GAME_VERSION" "$BOX_GAME_DIR" "$BOX_DATA" "$BOX_PLUGINS" "$BOX_OWN"
  exit 0
fi
owned() {  # owned <windows path>: recorded, or inside a recorded folder
  local r
  [ -f "$BOX_DEPLOYED" ] || box_die "no deploy record $BOX_DEPLOYED: this tool deployed nothing, so there is nothing it may move"
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    [ "$1" = "$r" ] && return 0
    case "$1" in "$r\\"*) return 0 ;; esac
  done < "$BOX_DEPLOYED"
  return 1
}
# Owner decision 7 (Gate 0): the only Papyrus scripts --test-pex places. These six names are the whole list.
TEST_PEX_NAMES=(Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex)
test_pex_ok() { local n; for n in "${TEST_PEX_NAMES[@]}"; do [ "$1" = "$n" ] && return 0; done; return 1; }
plugins_rel() {  # plugins_rel <REL>: the Windows path under Plugins, or die
  local r="${1//\//\\}"
  case "$r" in ""|*..*|*:*|\\*) box_die "bad path '$1': give it relative to Plugins, without '..' or a drive" ;; esac
  echo "$BOX_PLUGINS\\$r"
}
if [ "$1" = --pex ]; then
  PEX="${2:?usage: box_deploy.sh --pex path/to/SkyrimPython.pex}"
  [ -f "$PEX" ] || box_die "no such file: $PEX"
  [ "$(basename "$PEX")" = SkyrimPython.pex ] || box_die "--pex places only SkyrimPython.pex (got $(basename "$PEX"))"
  PEX_DEST="$BOX_DATA\\Scripts\\SkyrimPython.pex"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$PEX_DEST"; exit 0; fi
elif [ "$1" = --esp ]; then
  [ $# -eq 2 ] || box_die "usage: box_deploy.sh --esp path/to/SkythonTest.esp"
  ESP="$2"; [ -f "$ESP" ] || box_die "no such file: $ESP"
  [ "$(basename "$ESP")" = SkythonTest.esp ] || box_die "--esp places only SkythonTest.esp (got $(basename "$ESP"))"
  ESP_DEST="$BOX_DATA\\SkythonTest.esp"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$ESP_DEST"; exit 0; fi
  need_gate0
elif [ "$1" = --test-pex ]; then
  [ $# -ge 2 ] || box_die "usage: box_deploy.sh --test-pex FILE.pex..."
  TPEX=(); TPEX_DEST=()
  for f in "${@:2}"; do
    [ -f "$f" ] || box_die "no such file: $f"
    b="$(basename "$f")"
    test_pex_ok "$b" || box_die "--test-pex places only ${TEST_PEX_NAMES[*]} (got $b)"
    for d in "${TPEX_DEST[@]}"; do [ "$d" != "$BOX_DATA\\Scripts\\$b" ] || box_die "--test-pex: $b given twice"; done
    TPEX+=("$f"); TPEX_DEST+=("$BOX_DATA\\Scripts\\$b")
  done
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then printf '%s\n' "${TPEX_DEST[@]}"; exit 0; fi
  need_gate0
elif [ "$1" = --move ]; then
  [ $# -eq 3 ] || box_die "usage: box_deploy.sh --move REL NEWREL (both relative to Plugins)"
  MV_SRC="$(plugins_rel "$2")"; MV_DEST="$(plugins_rel "$3")"
  [ "$MV_SRC" != "$MV_DEST" ] || box_die "--move: source and destination are the same"
  owned "$MV_SRC" || box_die "--move: $MV_SRC was not deployed by this tool (not in out/box-deployed.txt); not moving it"
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "MOVE $MV_SRC -> $MV_DEST"; exit 0; fi
elif [ "${1#-}" = "$1" ]; then
  DLL="$1"; [ -f "$DLL" ] || box_die "no such file: $DLL"; spike_ok "$DLL"
  base="$(basename "$DLL")"
  if [ "$base" = Skython.dll ]; then
    CORE="$(dirname "$DLL")/SkythonCore.dll"
    [ -f "$CORE" ] || box_die "$CORE missing: run tools/remote_build.sh plugin SkythonCore.dll"
    [ "$DLL" -nt "$CORE" ] && box_die "$CORE is older than $DLL: rebuild it"
  fi
  [ $# -ge 2 ] && { [ -f "$2" ] || box_die "no such file: $2"; }
  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then
    echo "$BOX_PLUGINS\\$base"
    [ "$base" = Skython.dll ] && echo "$BOX_OWN\\SkythonCore.dll"
    [ $# -ge 2 ] && echo "$BOX_PLUGINS\\${base%.dll}.ini"
    exit 0
  fi
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
    have="$(bps 60 place.ps1 -Mode list -DestB64 "$(b64 "$BOX_OWN\\site-packages")" -Game "$BOX_GAME" | tr -d '\r' | sort)"
    stale="$(comm -23 <(echo "$have" | sed '/^$/d') <(ls -A "$SP" | sort))"
    [ -z "$stale" ] || { echo "$stale" >&2; box_die "the box's site-packages holds the entries above that $SP lacks; ask the owner (nothing is deleted)"; }
    put_entries "$SP" "$BOX_OWN\\site-packages"
    ;;
  --package)
    [ -f "$BOX_ROOT/python/src/skython/__init__.py" ] || box_die "python/src/skython is missing"
    put_tree "$BOX_ROOT/python/src/skython" "$BOX_OWN\\lib\\skython"
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
  --esp)
    put_file "$ESP" "$ESP_DEST"
    ;;
  --test-pex)
    for i in "${!TPEX[@]}"; do put_file "${TPEX[$i]}" "${TPEX_DEST[$i]}"; done
    ;;
  --move)
    r="$(bps 60 place.ps1 -Mode move -SrcB64 "$(b64 "$MV_SRC")" -DestB64 "$(b64 "$MV_DEST")" -Game "$BOX_GAME" | tr -d '\r')"
    [ "$r" = MOVED ] || box_die "moving $MV_SRC: '$r'"
    echo "$(date -Is) $MV_SRC -> $MV_DEST" >> "$BOX_ROOT/out/box-moves.txt"
    rec "$MV_DEST"; echo "moved $MV_SRC -> $MV_DEST (recorded in out/box-moves.txt)"
    ;;
  -*) box_die "unknown option $1" ;;
  *)
    # DLL, base and CORE were checked before the ssh (above)
    put_file "$DLL" "$BOX_PLUGINS\\$base"
    [ "$base" = Skython.dll ] && put_file "$CORE" "$BOX_OWN\\SkythonCore.dll"
    [ $# -ge 2 ] && put_file "$2" "$BOX_PLUGINS\\${base%.dll}.ini"
    ;;
esac
exit 0
