#!/usr/bin/env bash
# Offline checks for tools/papyrus_compile.sh (Linux, headless Wine; never the game or Steam). Exit 0 = all pass.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PC="$ROOT/tools/papyrus_compile.sh"
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
T="$(mktemp -d)"

# 1. never a window on the owner's desktop: both display variables unset on the compiler's command line
grep -qF 'env -u DISPLAY -u WAYLAND_DISPLAY' "$PC" && ok "the compiler runs with DISPLAY and WAYLAND_DISPLAY unset" \
  || bad "papyrus_compile.sh does not unset DISPLAY and WAYLAND_DISPLAY"
# 2. Skython.psc compiles to a non-empty Skyrim .pex, also when the caller's environment has a display
DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 "$PC" "$ROOT/plugin/papyrus/Skython.psc" -o "$T/out" > "$T/log" 2>&1; rc=$?
magic="$(head -c4 "$T/out/Skython.pex" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
[ "$rc" = 0 ] && [ -s "$T/out/Skython.pex" ] && [ "$magic" = fa57c0de ] && ok "Skython.psc -> non-empty Skython.pex" \
  || { bad "compiling Skython.psc: rc=$rc magic=$magic"; tail -5 "$T/log"; }
# 3. a fresh build has the committed file's stable hash, and docs/PINS.md records it
a="$("$PC" --stable-hash "$T/out/Skython.pex" 2>/dev/null | cut -d' ' -f1)"
b="$("$PC" --stable-hash "$ROOT/plugin/papyrus/compiled/Skython.pex" 2>/dev/null | cut -d' ' -f1)"
[ -n "$a" ] && [ "$a" = "$b" ] && ok "a fresh build matches the committed Skython.pex (stable hash $a)" || bad "stable hash: fresh '$a', committed '$b'"
[ -n "$b" ] && grep -qF "$b" "$ROOT/docs/PINS.md" && ok "docs/PINS.md records that stable hash" || bad "docs/PINS.md lacks the stable hash '$b'"
# 4. a compile error fails loudly (the compiler itself exits 0 on errors)
printf 'ScriptName Broken\nFunction X(\n' > "$T/Broken.psc"
"$PC" "$T/Broken.psc" -o "$T/bout" > "$T/blog" 2>&1; rc=$?
[ "$rc" != 0 ] && grep -q 'FAILED' "$T/blog" && [ ! -e "$T/bout/Broken.pex" ] && ok "a compile error exits non-zero, no .pex" || bad "broken script: rc=$rc"
# 5. argument checks
if "$PC" "$ROOT/plugin/papyrus/Skython.psc" >/dev/null 2>&1; then bad "accepted no -o"; else ok "refuses without -o"; fi
if "$PC" "$T/nope.psc" -o "$T/x" >/dev/null 2>&1; then bad "accepted a missing source"; else ok "refuses a missing source"; fi
if "$PC" --stable-hash "$T/Broken.psc" >/dev/null 2>&1; then bad "--stable-hash accepted a non-.pex"; else ok "--stable-hash refuses a non-.pex"; fi
# 6. the vanilla script sources live only under out/ (gitignored, never committed)
d="$("$PC" --vanilla-dir 2>/dev/null)"
case "$d" in "$ROOT/out/"*) ok "vanilla imports under out/" ;; *) bad "vanilla import dir '$d'" ;; esac
git -C "$ROOT" check-ignore -q "$d/Quest.psc" && ok "vanilla sources are gitignored" || bad "vanilla sources are not gitignored"

[ "$fail" = 0 ] && echo "PAPYRUS COMPILE PASS" || echo "PAPYRUS COMPILE FAIL"
exit "$fail"
