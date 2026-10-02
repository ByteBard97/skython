#!/usr/bin/env bash
# Compile Papyrus sources on Linux, headless, with the SimpleSkyrim Papyrus Compiler under GE-Proton10-14's wine64
# (spike S5's recipe, spikes/RESULTS.md). Compiles only: it never starts the game or Steam.
# Usage:
#   tools/papyrus_compile.sh <source.psc>... -o <out_dir> [-i <extra import dir>]...
#   tools/papyrus_compile.sh --stable-hash <file.pex>...    sha256 of each .pex with its two time fields zeroed
#   tools/papyrus_compile.sh --vanilla-dir                  prints the vanilla import dir (extracting it first)
# Imports, in this order: each source's own folder, every -i dir, plugin/papyrus (Skython.psc), the vanilla script
# sources. The vanilla sources and TESV_Papyrus_Flags.flg come from the game's Data/Scripts.zip, extracted once to
# out/papyrus-import/vanilla (out/ is gitignored: vanilla sources are never committed).
# The compiler exits 0 even when a script fails, so success is judged from its summary line
# ("<n> succeeded, 0 failed") and from each .pex existing and being newer than this run's start.
# A .pex holds the compile time (header bytes 8..15) and, in its debug info, the source file's modification time, so a
# rebuild never matches a recorded raw SHA-256. --stable-hash zeroes both; it is the value docs/PINS.md compares across
# rebuilds. The header also holds the source file name, user name and machine name, so the stable hash holds only for
# the same file name, machine and account that built the recorded file.
# Env (defaults are the S5 values): PAPYRUS_COMPILER, PAPYRUS_WINEPREFIX, PAPYRUS_WINE, SKYRIM_SCRIPTS_ZIP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAPYRUS_COMPILER="${PAPYRUS_COMPILER:-/home/geoff/Games/SimpleSkyrim/Stock Game/Papyrus Compiler/PapyrusCompiler.exe}"
PAPYRUS_WINEPREFIX="${PAPYRUS_WINEPREFIX:-/home/geoff/.local/share/Steam/steamapps/compatdata/4190904831/pfx}"
PAPYRUS_WINE="${PAPYRUS_WINE:-$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-14/files/bin/wine64}"
SKYRIM_SCRIPTS_ZIP="${SKYRIM_SCRIPTS_ZIP:-/mnt/games/SteamLibrary/steamapps/common/Skyrim Special Edition/Data/Scripts.zip}"
VANILLA="$ROOT/out/papyrus-import/vanilla/Source/Scripts"
die() { echo "papyrus_compile: $*" >&2; exit 1; }

stable_hash() {  # stable_hash <file.pex>: sha256 with the compile time and the source modification time zeroed
  python3 - "$1" <<'PY'
import hashlib, struct, sys
def stable(path):
    b = bytearray(open(path, "rb").read())
    if b[:4] != bytes.fromhex("fa57c0de"):
        sys.exit(f"not a Skyrim .pex: {path}")
    b[8:16] = bytes(8)                      # header: compilation time
    pos = 16
    def skip_str(p):
        (n,) = struct.unpack_from(">H", b, p)
        return p + 2 + n
    for _ in range(3):                      # source file name, user name, machine name
        pos = skip_str(pos)
    (count,) = struct.unpack_from(">H", b, pos); pos += 2
    for _ in range(count):                  # string table
        pos = skip_str(pos)
    if b[pos] == 1:                         # debug info present: its first field is the source's modification time
        b[pos + 1:pos + 9] = bytes(8)
    return hashlib.sha256(b).hexdigest()
print(stable(sys.argv[1]))
PY
}
vanilla() {  # extract the vanilla sources and the flags file once
  if [ ! -f "$VANILLA/TESV_Papyrus_Flags.flg" ] || [ ! -f "$VANILLA/Quest.psc" ]; then
    [ -f "$SKYRIM_SCRIPTS_ZIP" ] || die "no $SKYRIM_SCRIPTS_ZIP (set SKYRIM_SCRIPTS_ZIP to the game's Data/Scripts.zip)"
    mkdir -p "$ROOT/out/papyrus-import/vanilla"
    unzip -q -o "$SKYRIM_SCRIPTS_ZIP" 'Source/Scripts/*' -d "$ROOT/out/papyrus-import/vanilla" || die "could not extract $SKYRIM_SCRIPTS_ZIP"
  fi
  [ -f "$VANILLA/TESV_Papyrus_Flags.flg" ] || die "TESV_Papyrus_Flags.flg is not in $SKYRIM_SCRIPTS_ZIP"
}
winpath() { echo "Z:$(cd "$1" && pwd)"; }   # Wine maps / to Z:

case "${1:-}" in
  --stable-hash) shift; [ $# -ge 1 ] || die "usage: --stable-hash <file.pex>..."
                 for f in "$@"; do h="$(stable_hash "$f")" || die "--stable-hash: $f is not a Skyrim .pex"; echo "$h  $f"; done
                 exit 0 ;;
  --vanilla-dir) vanilla; echo "$VANILLA"; exit 0 ;;
  ""|-h|--help)  sed -n '2,8p' "$0"; exit 2 ;;
esac

sources=(); imports=(); out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="${2:?-o needs a directory}"; shift 2 ;;
    -i) [ -d "${2:?-i needs a directory}" ] || die "no such import dir: $2"; imports+=("$(cd "$2" && pwd)"); shift 2 ;;
    -*) die "unknown option $1" ;;
    *.psc) [ -f "$1" ] || die "no such source: $1"; sources+=("$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"); shift ;;
    *) die "not a .psc: $1" ;;
  esac
done
[ ${#sources[@]} -ge 1 ] || die "no sources"
[ -n "$out" ] || die "no -o <out_dir>"
[ -f "$PAPYRUS_COMPILER" ] || die "no compiler at $PAPYRUS_COMPILER"
[ -x "$PAPYRUS_WINE" ] || die "no wine64 at $PAPYRUS_WINE"
vanilla
mkdir -p "$out"; out="$(cd "$out" && pwd)"
start="$(mktemp)"; log="$(mktemp)"
for src in "${sources[@]}"; do
  # The compiler takes ONE -i (a second -i replaces the first): join the import dirs with ';'.
  dirs=("$(dirname "$src")" "${imports[@]}" "$ROOT/plugin/papyrus" "$VANILLA")
  imp=""; for d in "${dirs[@]}"; do imp+="${imp:+;}$(winpath "$d")"; done
  name="$(basename "$src")"
  # env -u DISPLAY -u WAYLAND_DISPLAY: no window can open on the owner's desktop (S5). WINEDEBUG=-all: quiet.
  ( cd "$(dirname "$src")" && timeout 300 env -u DISPLAY -u WAYLAND_DISPLAY WINEPREFIX="$PAPYRUS_WINEPREFIX" WINEDEBUG=-all \
      "$PAPYRUS_WINE" "$PAPYRUS_COMPILER" "$name" -i="$imp" -o="$(winpath "$out")" -f=TESV_Papyrus_Flags.flg ) > "$log" 2>&1 || true
  tr -d '\r' < "$log" | grep -v '^wineserver:' || true
  pex="$out/${name%.psc}.pex"
  if ! tr -d '\r' < "$log" | grep -qE '^Batch compile of 1 files finished\. 1 succeeded, 0 failed\.$' || [ ! "$pex" -nt "$start" ]; then
    die "FAILED: $src (see the compiler output above)"
  fi
  echo "compiled $src -> $pex ($(stat -c %s "$pex") bytes)"
done
