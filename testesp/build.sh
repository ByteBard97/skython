#!/usr/bin/env bash
# Rebuild the test plugin and its Papyrus scripts, then compare them with the pins in docs/PINS.md.
# Usage: testesp/build.sh               build into testesp/ (SkythonTest.esp, compiled/*.pex) and compare
#        testesp/build.sh --check       build into out/testesp-check/ and compare; the committed files are untouched
#        testesp/build.sh --print-pins  build into out/testesp-check/ and print the pin lines for docs/PINS.md
# Pins are lines "- pin <path> sha256 <hex>" (the ESP: Mutagen's output is byte-identical across builds) and
# "- pin <path> stable <hex>" (a .pex: tools/papyrus_compile.sh --stable-hash). Exit 0 only when every pin matches and
# Mutagen's read-back equals testesp/expected-dump.txt. If a pin differs and no source changed, something in the
# toolchain changed; find out what before re-pinning.
# Offline only: dotnet restores from ~/.nuget/packages and never from the network. The full build copies a cell record
# and a location record from the installed Skyrim.esm (path: SKYTHON_SKYRIM_ESM, else TestEsp.DefaultSkyrimEsm).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOTNET="${DOTNET:-$HOME/.dotnet/dotnet}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
SCRIPTS=(SkythonTestQuestBase SkythonTestQuest SkythonTestAlias SkythonTestLocationAlias QF_SkythonTestQuest_01000800)
mode="${1:-build}"
case "$mode" in
  build) esp_dir="$ROOT/testesp"; pex_dir="$ROOT/testesp/compiled" ;;
  --check|--print-pins) esp_dir="$ROOT/out/testesp-check"; pex_dir="$ROOT/out/testesp-check/compiled" ;;
  *) echo "usage: testesp/build.sh [--check|--print-pins]" >&2; exit 2 ;;
esac
cd "$ROOT"
timeout 300 "$DOTNET" restore testesp/builder --source "$HOME/.nuget/packages" >/dev/null
timeout 300 "$DOTNET" build testesp/builder -c Release --no-restore -v q -nologo | grep -E 'error|warn' && exit 1 || true
B="testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll"
timeout 120 "$DOTNET" "$B" full "$esp_dir"
srcs=(); for s in "${SCRIPTS[@]}"; do srcs+=("testesp/papyrus/$s.psc"); done
tools/papyrus_compile.sh "${srcs[@]}" -o "$pex_dir" | grep -E '^compiled'
keys=(testesp/SkythonTest.esp)
declare -A got=(["testesp/SkythonTest.esp"]="sha256 $(sha256sum "$esp_dir/SkythonTest.esp" | cut -d' ' -f1)")
for s in "${SCRIPTS[@]}"; do
  keys+=("testesp/compiled/$s.pex")
  got["testesp/compiled/$s.pex"]="stable $(tools/papyrus_compile.sh --stable-hash "$pex_dir/$s.pex" | cut -d' ' -f1)"
done
if [ "$mode" = --print-pins ]; then
  for k in "${keys[@]}"; do echo "- pin $k ${got[$k]}"; done
  exit 0
fi
fail=0
timeout 120 "$DOTNET" "$B" dump "$esp_dir/SkythonTest.esp" | diff - testesp/expected-dump.txt > /dev/null \
  && echo "ok   dump equals testesp/expected-dump.txt" || { echo "FAIL dump differs from testesp/expected-dump.txt"; fail=1; }
for k in "${keys[@]}"; do
  want="$(awk -v k="$k" '$1 == "-" && $2 == "pin" && $3 == k { print $4 " " $5 }' docs/PINS.md)"
  if [ -z "$want" ]; then echo "FAIL no pin for $k in docs/PINS.md (got ${got[$k]})"; fail=1
  elif [ "$want" = "${got[$k]}" ]; then echo "ok   $k ${got[$k]}"
  else echo "FAIL $k: built ${got[$k]}, pinned $want"; fail=1; fi
done
[ "$fail" = 0 ] && echo "TEST ESP BUILD MATCHES PINS" || echo "TEST ESP BUILD DIFFERS"
exit "$fail"
