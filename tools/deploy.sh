#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini]  -- copies into SimpleSkyrim's Data/SKSE/Plugins.
# Every copied path is appended to out/deployed.txt so tools/teardown_list.sh can list it later.
# Deploying Skython.dll also copies SkythonCore.dll from the same folder into Data/SKSE/Plugins/Skython/.
# A target that already exists and is not listed in out/deployed.txt is never overwritten (it is not ours).
# A *Spike* DLL opens an unauthenticated port: it needs CONFIRM_THROWAWAY_SAVE=1 (you are on a throwaway save).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
LEDGER="$ROOT/out/deployed.txt"
mkdir -p "$ROOT/out"; touch "$LEDGER"
# ours_or_new <target>: refuse a target that exists but this repo did not deploy.
ours_or_new() {
  if [ -e "$1" ] && ! grep -qxF "$1" "$LEDGER"; then
    echo "deploy: $1 exists but was not deployed by this repo; not overwriting it. Ask the owner." >&2; exit 1
  fi
}
record() { grep -qxF "$1" "$LEDGER" || echo "$1" >> "$LEDGER"; }
# --python [out/<Core>.dll]: copy the pinned embeddable CPython to Data/SKSE/Plugins/Skython/python/
# (with its LICENSE.txt) and, if given, a core DLL to Data/SKSE/Plugins/Skython/. (SkyrimPythonSpike.dll reads
# Plugins/SkyrimPython/ instead; a spike re-run needs OWN pointed back there.)
if [ "${1:-}" = "--python" ]; then
  shift
  "$ROOT/tools/fetch_python.sh" --check || { echo "deploy: third_party/cpython does not match docs/PINS.md" >&2; exit 1; }
  DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"; OWN="$DEST/Skython"; PYDIR="$OWN/python"
  [ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
  ours_or_new "$PYDIR"
  mkdir -p "$PYDIR"
  cp -a "$ROOT/third_party/cpython/embed/." "$PYDIR/"
  record "$PYDIR"
  [ -f "$PYDIR/python314.dll" ] && [ -f "$PYDIR/LICENSE.txt" ] || { echo "deploy: copy incomplete" >&2; exit 1; }
  echo "deployed CPython runtime to $PYDIR"
  if [ $# -ge 1 ]; then
    case "$(basename "$1")" in
      *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || { echo "deploy: $1 is spike code; re-run with CONFIRM_THROWAWAY_SAVE=1 on a throwaway save" >&2; exit 1; } ;;
    esac
    [ -f "$1" ] || { echo "deploy: no such file: $1" >&2; exit 1; }
    ours_or_new "$OWN/$(basename "$1")"
    cp -v "$1" "$OWN/"
    record "$OWN/$(basename "$1")"
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
TARGETS=("$DEST/$(basename "$1")")
CORE=""
if [ "$(basename "$1")" = Skython.dll ]; then   # the loader is useless without its core next to it
  CORE="$(dirname "$1")/SkythonCore.dll"
  [ -f "$CORE" ] || { echo "deploy: $CORE not found; build it first (the loader loads it from Plugins/Skython/)" >&2; exit 1; }
  TARGETS+=("$DEST/Skython/SkythonCore.dll")
fi
INI=""
if [ $# -ge 2 ]; then
  [ -f "$2" ] || { echo "deploy: no such file: $2" >&2; exit 1; }
  INI="$DEST/$(basename "${1%.dll}").ini"
  TARGETS+=("$INI")
fi
for t in "${TARGETS[@]}"; do ours_or_new "$t"; done   # check them all before copying any
cp -v "$1" "$DEST/"
record "$DEST/$(basename "$1")"
if [ -n "$CORE" ]; then
  mkdir -p "$DEST/Skython"
  cp -v "$CORE" "$DEST/Skython/"
  record "$DEST/Skython/SkythonCore.dll"
fi
if [ -n "$INI" ]; then
  cp -v "$2" "$INI"
  record "$INI"
fi
