#!/usr/bin/env bash
# Fails when a tracked source, test or tool file contains a planning-note reference (task numbers, ruling tags),
# a machine-specific path (a Linux home folder) or address, or a private project name. docs/, spikes/ and vendored
# code (third_party/, plugin/extern/) are not checked.
# Usage: tools/check_no_plan_refs.sh        (exit 0 = clean; otherwise prints file:line for each hit)
set -uo pipefail
cd "$(dirname "$0")/.."
# Each pattern is built from parts so this file does not match itself.
T='Task'; O='owner'; H='home'; D='dm'
pats=(
  "\\b${T} [0-9]+\\b"
  '\b[R]([0-9]{2,3})\b'
  "ask the ${O}"
  "STOP"" and ask"
  "contr""oller"
  "sub""agent"
  "skyrim-${D}"
  "/${H}/[A-Za-z0-9_.-]+/"
  '192[.]168[.]'
)
re="$(IFS='|'; echo "${pats[*]}")"
hits="$(git ls-files -z \
  | grep -zv -E '^(docs/|spikes/|third_party/|plugin/extern/|\.superpowers/)' \
  | xargs -0 grep -H -n -I -P "$re" 2>/dev/null)"
if [ -n "$hits" ]; then
  echo "$hits"
  echo "plan references found: $(echo "$hits" | wc -l) line(s)"
  exit 1
fi
echo "no plan references"
