#!/usr/bin/env bash
# Builds the public Skython tree from this repository's HEAD commit into OUT_DIR, as a new git repository with one
# commit (no remote; nothing is pushed). Development notes, spikes and local machine files are left out, links into
# them are rewritten, and the result is checked: tools/check_no_em_dash.sh, tools/check_no_plan_refs.sh, links left
# pointing into excluded paths, and a scan for personal and private-project names (text files and Papyrus/ESP binaries).
#
# Usage: tools/export_public.sh OUT_DIR
#   OUT_DIR must not exist or must be an empty directory; anything else is refused. Nothing is ever deleted, so a
#   failed or repeated run needs a new OUT_DIR. Only committed content (HEAD) is exported; uncommitted changes are not.
# Exit: 0 = exported and clean; 1 = refused or failed before export; 2 = exported, but a check or the scan found
#   something to fix (OUT_DIR is kept for review).
set -euo pipefail

die() { echo "export_public: $*" >&2; exit 1; }
[ $# -eq 1 ] || die "usage: tools/export_public.sh OUT_DIR"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$1"
if [ -e "$OUT" ]; then
  [ -d "$OUT" ] || die "$OUT exists and is not a directory"
  [ -z "$(ls -A "$OUT")" ] || die "$OUT is not empty; pick a new folder (this script never deletes anything)"
fi
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
case "$OUT/" in "$REPO/") die "OUT_DIR is the repository itself" ;; esac

SUBMODULE=plugin/extern/CommonLibSSE-NG
# Paths left out of the public tree (anchored at the repo root).
#   docs/superpowers, docs/research: planning and research notes from development
#   docs/specs: the design spec names private projects and machines
#   spikes: throwaway feasibility code and its notes; no product code, test or build file uses anything from it
#   tools/export_public.sh, tools/test_export_public.sh: this tool and its test only make sense in the private repo
#   the rest: local notes and machine settings, normally untracked and gitignored (listed in case one gets tracked)
EXCLUDE_RE='^(docs/superpowers/|docs/research/|docs/specs/|spikes/|\.superpowers/|out/|\.claude/|\.playwright-mcp/)'
EXCLUDE_RE+='|(^|/)(HANDOFF-|OWNER-DECISIONS-|QUESTIONS-|KIMI-)[^/]*$'
EXCLUDE_RE+='|^tools/(local\.env|box/box\.local\.env|export_public\.sh|test_export_public\.sh)$'

cd "$REPO"
HEAD_SHA="$(git rev-parse HEAD)"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "note: the working tree has uncommitted changes; only HEAD ($HEAD_SHA) is exported" >&2
fi
mapfile -t ALL < <(git ls-tree -r --name-only HEAD)
KEEP=(); EXCLUDED=()
for f in "${ALL[@]}"; do
  [ "$f" = "$SUBMODULE" ] && continue          # a gitlink: recorded below, not archived
  if [[ "$f" =~ $EXCLUDE_RE ]]; then EXCLUDED+=("$f"); else KEEP+=("$f"); fi
done
SUB_SHA="$(git ls-tree HEAD "$SUBMODULE" | awk '$1 == "160000" {print $3}')"
[ -n "$SUB_SHA" ] || die "$SUBMODULE is not a submodule in HEAD"
git config -f .gitmodules --get "submodule.$SUBMODULE.url" >/dev/null || die ".gitmodules has no url for $SUBMODULE"
AUTHOR_NAME="$(git log -1 --format=%an HEAD)"
AUTHOR_EMAIL="$(git log -1 --format=%ae HEAD)"

git --literal-pathspecs archive --format=tar HEAD -- "${KEEP[@]}" | tar -x -C "$OUT"
mkdir -p "$OUT/$SUBMODULE"
echo "exported ${#KEEP[@]} files from HEAD $HEAD_SHA; left out ${#EXCLUDED[@]}:"
printf '%s\n' "${EXCLUDED[@]}" | awk -F/ '{ d = (NF > 2) ? $1 "/" $2 "/" : (NF > 1 ? $1 "/" : $0); n[d]++ }
  END { for (d in n) printf "  %-28s %d file(s)\n", d, n[d] }' | sort

# ---- rewrites: links and mentions that point into excluded paths -------------------------------------------------
# Each is a perl substitution on one file. A rewrite that no longer matches is reported, not fatal: the check after
# the rewrites is what decides whether anything still points into an excluded path.
cd "$OUT"
rewrite() {   # rewrite <file> <what> <perl -0 substitution>
  local file="$1" what="$2" expr="$3"
  [ -f "$file" ] || { echo "  $file: missing, skipped ($what)"; return; }
  local before; before="$(cat "$file")"
  perl -0pi -e "$expr" "$file"
  if [ "$before" = "$(cat "$file")" ]; then echo "  $file: no match ($what)"; else echo "  $file: $what"; fi
}
echo "rewrites:"
rewrite README.md "dropped the design spec link (docs/specs is not exported)" \
  's/ Design: `docs\/specs\/[^`]*`\.//g'
rewrite README.md "dropped the spike results link (spikes/ is not exported)" \
  's/ Spike results: `spikes\/RESULTS\.md`\.//g'
rewrite docs/PINS.md "dropped the pointer to spikes/RESULTS.md" \
  's/; details in the S7 row of `spikes\/RESULTS\.md`//g'
rewrite tools/papyrus_compile.sh "dropped the pointer to spikes/RESULTS.md" \
  's/wine64\n# \(spike S5.s recipe, spikes\/RESULTS\.md\)\. /wine64.\n# /'
rewrite tools/remote_build.sh "usage example builds plugin/ instead of the spike DLL" \
  's/tools\/remote_build\.sh spikes\/spike-dll SkyrimPythonSpike\.dll/tools\/remote_build.sh plugin Skython.dll/'
rewrite tools/check_no_em_dash.sh "no longer skips the development notes folders" \
  's/ is\n# skipped; so are the development notes under docs\/research\/ and docs\/superpowers\/, which are reworked separately\./ is\n# skipped./; s/\^\(third_party\/\|docs\/research\/\|docs\/superpowers\/\)/^(third_party\/)/'
rewrite tools/check_no_plan_refs.sh "no longer skips spikes/ and .superpowers/" \
  's/docs\/, spikes\/ and vendored/docs\/ and vendored/; s/\^\(docs\/\|spikes\/\|third_party\/\|plugin\/extern\/\|\\\.superpowers\/\)/^(docs\/|third_party\/|plugin\/extern\/)/'
rewrite .gitignore "dropped the entries for local development notes" \
  's/^# handoff notes[^\n]*\nHANDOFF-\*\.md\n\n//m; s/^# local working notes\n\.superpowers\/\n\n//m; s/^(\.playwright-mcp\/|OWNER-DECISIONS-\*\.md|QUESTIONS-\*\.md|KIMI-\*\.md|\.claude\/)\n//mg'

# ---- the new repository ------------------------------------------------------------------------------------------
git init -q -b main
git add -A -f   # -f: a file stays committed even if a future ignore rule comes to match it; the count check below catches anything git still drops
git update-index --add --cacheinfo "160000,$SUB_SHA,$SUBMODULE"
git -c user.name="$AUTHOR_NAME" -c user.email="$AUTHOR_EMAIL" -c commit.gpgsign=false \
  commit -q -m "Initial public release of Skython" \
  -m "Skython embeds CPython 3.14 in an SKSE plugin so Skyrim mods can be written in Python."
echo "committed $(git rev-parse --short HEAD) as $AUTHOR_NAME <$AUTHOR_EMAIL> (edit the message with: git commit --amend)"

# ---- checks ------------------------------------------------------------------------------------------------------
status=0
committed="$(git ls-files | wc -l)"
if [ "$committed" != $(( ${#KEEP[@]} + 1 )) ]; then
  echo "committed $committed entries, expected ${#KEEP[@]} files plus the gitlink"; status=2
fi
echo "== tools/check_no_em_dash.sh"; tools/check_no_em_dash.sh || status=2
echo "== tools/check_no_plan_refs.sh"; tools/check_no_plan_refs.sh || status=2

echo "== links into excluded paths"
X='super''powers'
dangling="$(git ls-files -z | grep -zv -E '^(third_party/|plugin/extern/)' \
  | xargs -0 grep -n -I -E "docs/(specs|research|$X)|spikes/|\.$X" 2>/dev/null || true)"
if [ -z "$dangling" ]; then echo "none"
else
  # livetest/ comments cite spike results; they are left as they are (other work owns those files) and reported
  echo "$dangling" | sed 's/^/  /'
  if echo "$dangling" | grep -qv '^livetest/'; then status=2; echo "links outside livetest/ need a rewrite"
  else echo "only livetest/ comments (left as they are; listed for review)"; fi
fi

echo "== personal and private-name scan"
# Built from parts so this file does not match its own patterns.
scan_re="geo""ff|/ho""me/|192[.]168[.]|C:\\\\Us""ers\\\\|Chron""icle|Counten""ance|Runtime""Harness"
scan_re+="|skyrim-""dm|Cla""ude|Anthro""pic|$X|sub""agent"
# The hit that is expected and left for the owner: the placeholder Windows path in the box settings example.
known_re="^tools/box/box\.local\.env\.example:[0-9]+:BOX_MYGAMES='C:.Users.user."
text_hits="$(git ls-files -z | grep -zv '^plugin/extern/' | xargs -0 grep -n -I -i -E "$scan_re" 2>/dev/null || true)"
bin_hits=""
while IFS= read -r -d '' f; do
  h="$(strings -a "$f" | grep -i -E "$scan_re" || true)"
  [ -n "$h" ] && bin_hits+="$f: $h"$'\n'
done < <(git ls-files -z '*.pex' '*.esp' '*.esm' '*.esl' '*.bsa' '*.dll' '*.exe')
known="$(echo "$text_hits" | grep -E "$known_re" || true)"
unknown="$(echo "$text_hits" | grep -v -E "$known_re" | sed '/^$/d' || true)"
[ -n "$bin_hits" ] && unknown+=$'\n'"$bin_hits"
unknown="$(echo "$unknown" | sed '/^$/d')"
if [ -n "$known" ]; then echo "expected (for review):"; echo "$known" | sed 's/^/  /'; fi
if [ -n "$unknown" ]; then echo "UNEXPECTED:"; echo "$unknown" | sed 's/^/  /'; status=2; else echo "no unexpected hits"; fi

echo "== summary"
echo "folder:  $OUT"
echo "commit:  $(git log -1 --format='%h %an <%ae> %s')"
echo "files:   $(git ls-files | wc -l) tracked (incl. the $SUBMODULE gitlink at ${SUB_SHA:0:7})"
echo "size:    $(du -sh --exclude=.git . | cut -f1) without .git, $(du -sh .git | cut -f1) .git"
[ "$status" = 0 ] && echo "result:  clean" || echo "result:  needs attention (see above)"
exit "$status"
