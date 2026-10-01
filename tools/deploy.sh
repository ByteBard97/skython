#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini]  -- copies into SimpleSkyrim's Data/SKSE/Plugins.
# Every copied path is appended to out/deployed.txt so tools/teardown_list.sh can list it later.
# A *Spike* DLL opens an unauthenticated port: it needs CONFIRM_THROWAWAY_SAVE=1 (you are on a throwaway save).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
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
