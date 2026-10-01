#!/usr/bin/env bash
# Fetch the pinned CPython runtime and build inputs into third_party/cpython/ (gitignored).
#   embed/  <- python-3.14.8-embed-amd64.zip (python.org): the runtime deployed into the game
#   nuget/  <- python.3.14.8.nupkg (nuget.org): tools/include/ and tools/libs/python314.lib for the build
# Usage: tools/fetch_python.sh          download (if needed), verify SHA-256, unpack
#        tools/fetch_python.sh --check  verify only; downloads and unpacks nothing; non-zero on any mismatch
# The expected SHA-256s are the sha256sum-format lines in docs/PINS.md. A mismatch is never "fixed" here:
# STOP and ask the owner. This script removes nothing; a stale unpacked folder is reported, not replaced.
# Env (for tests): CPYTHON_DIR (default third_party/cpython), PINS_FILE (default docs/PINS.md),
#                  FETCH_FROM=<dir> copies the archives from a local folder instead of downloading.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CPY="${CPYTHON_DIR:-$ROOT/third_party/cpython}"
PINS="${PINS_FILE:-$ROOT/docs/PINS.md}"
EMBED=python-3.14.8-embed-amd64.zip
NUPKG=python.3.14.8.nupkg
declare -A URL=(
  [$EMBED]="https://www.python.org/ftp/python/3.14.8/$EMBED"
  [$NUPKG]="https://api.nuget.org/v3-flatcontainer/python/3.14.8/$NUPKG"
)
declare -A DEST=([$EMBED]=embed [$NUPKG]=nuget)
die() { echo "fetch_python: $*" >&2; exit 1; }

CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) die "usage: fetch_python.sh [--check]" ;;
esac
[ -f "$PINS" ] || die "no pins file at $PINS (Task 0 Step 4 writes it)"

expected() {  # sha256 of $1 from the pins file, or empty
  grep -E "^[0-9a-f]{64}  $1\$" "$PINS" | head -n1 | cut -d' ' -f1
}

status=0
mkdir -p "$CPY/downloads"
for f in "$EMBED" "$NUPKG"; do
  want="$(expected "$f")"
  [ -n "$want" ] || die "docs/PINS.md has no sha256 line for $f"
  arch="$CPY/downloads/$f"
  if [ ! -f "$arch" ]; then
    if [ "$CHECK" = 1 ]; then echo "MISSING $arch" >&2; status=1; continue; fi
    if [ -n "${FETCH_FROM:-}" ]; then cp "$FETCH_FROM/$f" "$arch.part"
    else curl -fsSL --retry 3 --max-time 600 -o "$arch.part" "${URL[$f]}" || die "download of ${URL[$f]} failed"; fi
    mv "$arch.part" "$arch"
  fi
  got="$(sha256sum "$arch" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    echo "fetch_python: SHA-256 mismatch for $arch" >&2
    echo "  expected $want (docs/PINS.md)" >&2
    echo "  got      $got" >&2
    echo "  STOP and ask the owner. Nothing was unpacked." >&2
    exit 1
  fi
  out="$CPY/${DEST[$f]}"
  stamp="$out/.sha256"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ]; then
    echo "ok $f ($want) -> $out"
    continue
  fi
  if [ "$CHECK" = 1 ]; then echo "NOT UNPACKED (or stale) $out" >&2; status=1; continue; fi
  if [ -e "$out" ]; then
    die "$out exists but was not unpacked from this archive; ask the owner to approve moving it away (gio trash), then re-run"
  fi
  mkdir -p "$out"
  unzip -q "$arch" -d "$out" || die "unzip of $arch failed"
  printf '%s' "$want" > "$stamp"
  echo "unpacked $f -> $out"
done

# The files later tasks rely on must be there.
if [ "$status" = 0 ]; then
  for need in embed/python314.dll embed/python314.zip embed/LICENSE.txt \
              nuget/tools/include/Python.h nuget/tools/libs/python314.lib; do
    [ -f "$CPY/$need" ] || { echo "MISSING $CPY/$need" >&2; status=1; }
  done
fi
[ "$status" = 0 ] && echo "fetch_python: CPython 3.14.8 runtime and build inputs verified in $CPY"
exit "$status"
