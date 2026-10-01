#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini]  -- copies into SimpleSkyrim's Data/SKSE/Plugins.
# Every copied path is appended to out/deployed.txt so tools/teardown_list.sh can list it later.
# A *Spike* DLL opens an unauthenticated port: it needs CONFIRM_THROWAWAY_SAVE=1 (you are on a throwaway save).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
# --python [out/<Core>.dll]: copy the pinned embeddable CPython to Data/SKSE/Plugins/SkyrimPython/python/
# (with its LICENSE.txt) and, if given, a core DLL to Data/SKSE/Plugins/SkyrimPython/.
if [ "${1:-}" = "--python" ]; then
  shift
  "$ROOT/tools/fetch_python.sh" --check || { echo "deploy: third_party/cpython does not match docs/PINS.md" >&2; exit 1; }
  DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"; OWN="$DEST/SkyrimPython"; PYDIR="$OWN/python"
  [ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
  mkdir -p "$ROOT/out"; touch "$ROOT/out/deployed.txt"
  if [ -e "$PYDIR" ] && ! grep -qxF "$PYDIR" "$ROOT/out/deployed.txt"; then
    echo "deploy: $PYDIR exists but was not deployed by this repo; not overwriting it. Ask the owner." >&2; exit 1
  fi
  mkdir -p "$PYDIR"
  cp -a "$ROOT/third_party/cpython/embed/." "$PYDIR/"
  grep -qxF "$PYDIR" "$ROOT/out/deployed.txt" || echo "$PYDIR" >> "$ROOT/out/deployed.txt"
  [ -f "$PYDIR/python314.dll" ] && [ -f "$PYDIR/LICENSE.txt" ] || { echo "deploy: copy incomplete" >&2; exit 1; }
  echo "deployed CPython runtime to $PYDIR"
  if [ $# -ge 1 ]; then
    case "$(basename "$1")" in
      *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || { echo "deploy: $1 is spike code; re-run with CONFIRM_THROWAWAY_SAVE=1 on a throwaway save" >&2; exit 1; } ;;
    esac
    [ -f "$1" ] || { echo "deploy: no such file: $1" >&2; exit 1; }
    cp -v "$1" "$OWN/"
    echo "$OWN/$(basename "$1")" >> "$ROOT/out/deployed.txt"
  fi
  exit 0
fi
[ -f "${1:-}" ] || { echo "deploy: no such file: ${1:-<none>}" >&2; exit 1; }
case "$(basename "$1")" in
  *Spike*)
    [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || {
      echo "deploy: $1 opens an unauthenticated port that can load saves and change quest aliases." >&2
      echo "deploy: use only with a throwaway save, then re-run with CONFIRM_THROWAWAY_SAVE=1." >&2
      exit 1; } ;;
esac
DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"
[ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
mkdir -p "$ROOT/out"
cp -v "$1" "$DEST/"
echo "$DEST/$(basename "$1")" >> "$ROOT/out/deployed.txt"
if [ $# -ge 2 ]; then
  cp -v "$2" "$DEST/$(basename "${1%.dll}").ini"
  echo "$DEST/$(basename "${1%.dll}").ini" >> "$ROOT/out/deployed.txt"
fi
