#!/usr/bin/env bash
# Offline test of tools/export_public.sh: exports HEAD into a new folder under out/ and checks what was left out,
# the submodule pointer, the single commit, the refusals and the checks the export runs.
# Each run makes new folders (out/export-test.XXXXXX); this test, like the export, never deletes anything, so old
# ones stay in out/ (gitignored) until removed by hand.
# Usage: tools/test_export_public.sh        (exit 0 = all checks passed)
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"
mkdir -p out
pass=0; fail=0
ok()  { echo "ok   $*"; pass=$((pass + 1)); }
bad() { echo "FAIL $*"; fail=$((fail + 1)); }
check() { local what="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$what"; else bad "$what"; fi; }

# refusals: a non-empty folder and a plain file
busy="$(mktemp -d out/export-test.XXXXXX)"
echo keep > "$busy/marker"
tools/export_public.sh "$busy" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$(ls -A "$busy")" = marker ] && ok "refuses a non-empty folder and writes nothing" \
  || bad "non-empty folder: exit $rc, contents: $(ls -A "$busy" | tr '\n' ' ')"
tools/export_public.sh "$busy/marker" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$(cat "$busy/marker")" = keep ] && ok "refuses a path that is a file" || bad "file path: exit $rc"
tools/export_public.sh >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && ok "refuses a missing argument" || bad "no argument: exit $rc"

# the export itself
OUT="$(mktemp -d out/export-test.XXXXXX)"
log="$OUT.log"
tools/export_public.sh "$OUT" > "$log" 2>&1; rc=$?
[ "$rc" = 0 ] && ok "export exits 0 (clean)" || { bad "export exit $rc (log: $log)"; tail -20 "$log"; }
tools/export_public.sh "$OUT" >/dev/null 2>&1; rc=$?
[ "$rc" = 1 ] && ok "a second run into the same folder is refused" || bad "second run: exit $rc"

files="$(git -C "$OUT" ls-files)"
X='super''powers'
for p in "docs/$X/" docs/research/ docs/specs/ spikes/ ".$X/" out/ .claude/ .playwright-mcp/ \
         tools/local.env tools/box/box.local.env tools/export_public.sh tools/test_export_public.sh; do
  case "$p" in */) hit="$(echo "$files" | grep -F -- "$p" | grep -c "^${p//./\\.}")" ;;   # a folder: any file under it
               *) hit="$(echo "$files" | grep -c -F -x -- "$p")" ;; esac                # a file: that path only
  if [ "$hit" != 0 ]; then bad "left out: $p"
  else ok "left out: $p"; fi
done
echo "$files" | grep -q -E '(^|/)(HANDOFF|OWNER-DECISIONS|QUESTIONS|KIMI)-' && bad "local notes exported" \
  || ok "no local notes (handoff, decisions, questions, work lists)"
for p in README.md LICENSE .gitmodules .gitignore docs/BUILDING.md docs/PINS.md protocol/protocol.json \
         tools/check_no_em_dash.sh tools/check_no_plan_refs.sh tools/box/box.local.env.example \
         tools/box/test_box_tools.sh core/CMakeLists.txt python/pyproject.toml; do
  echo "$files" | grep -q -F -x "$p" && ok "kept: $p" || bad "kept: $p"
done
want="$(git ls-tree HEAD plugin/extern/CommonLibSSE-NG)"
got="$(git -C "$OUT" ls-tree HEAD plugin/extern/CommonLibSSE-NG)"
[ -n "$want" ] && [ "$want" = "$got" ] && ok "submodule pointer kept ($(echo "$got" | cut -c1-19)...)" \
  || bad "submodule pointer: want '$want' got '$got'"
[ "$(git -C "$OUT" config -f .gitmodules submodule.plugin/extern/CommonLibSSE-NG.url)" \
  = "$(git config -f .gitmodules submodule.plugin/extern/CommonLibSSE-NG.url)" ] && ok ".gitmodules url kept" \
  || bad ".gitmodules url"
[ "$(git -C "$OUT" rev-list --count HEAD)" = 1 ] && ok "one commit" || bad "commit count"
[ "$(git -C "$OUT" log -1 --format='%an <%ae>|%cn <%ce>')" = "$(git log -1 --format='%an <%ae>|%an <%ae>' HEAD)" ] \
  && ok "author and committer match this repo's identity" || bad "identity: $(git -C "$OUT" log -1 --format='%an <%ae>')"
git -C "$OUT" log -1 --format=%B | grep -q -i -E 'co-authored|generated with|cla''ude|anthro''pic' \
  && bad "commit message has an attribution line" || ok "commit message has no attribution line"
[ -z "$(git -C "$OUT" remote)" ] && ok "no remote" || bad "remote set"
[ -z "$(git -C "$OUT" status --porcelain)" ] && ok "export folder is committed in full" || bad "uncommitted files in export"

# the export's own checks, read from its log
check "em dash check passes in the export" grep -q '^no em dashes$' "$log"
check "plan reference check passes in the export" grep -q '^no plan references$' "$log"
check "scan: no unexpected hits" grep -q '^no unexpected hits$' "$log"
leftover="$(git -C "$OUT" ls-files -z | grep -zv -E '^(third_party/|plugin/extern/|livetest/)' \
  | (cd "$OUT" && xargs -0 grep -l -I -E "docs/(specs|research|$X)|spikes/" 2>/dev/null))"
[ -z "$leftover" ] && ok "no links into excluded paths outside livetest/" || bad "links left in: $leftover"

echo "export folder: $OUT (log: $log)"
echo "$pass passed, $fail failed"
[ "$fail" = 0 ]
