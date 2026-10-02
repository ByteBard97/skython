#!/usr/bin/env bash
# Fails when a tracked file a reader of this repo sees contains an em dash (U+2014). Vendored code (third_party/) is
# skipped; so are the development notes under docs/research/ and docs/superpowers/, which are reworked separately.
# Usage: tools/check_no_em_dash.sh        (exit 0 = clean; otherwise prints file:line for each hit)
set -uo pipefail
cd "$(dirname "$0")/.."
hits="$(git ls-files -z | grep -zv -E '^(third_party/|docs/research/|docs/superpowers/)' \
  | xargs -0 grep -n -I $'—' 2>/dev/null)"
if [ -n "$hits" ]; then
  echo "$hits"
  echo "em dashes found: $(echo "$hits" | wc -l) line(s)"
  exit 1
fi
echo "no em dashes"
