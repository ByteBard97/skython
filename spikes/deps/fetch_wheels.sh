#!/usr/bin/env bash
# Spike S10: fetch skyrim-dm's in-game dependencies as CPython 3.14 / win_amd64 wheels and unpack them into a
# staging site-packages. Writes only under out/ (git-ignored): out/wheels/, out/wheels/SHA256SUMS,
# out/site-packages/. Refuses to write into a non-empty out/site-packages (it never deletes anything).
# Usage: spikes/deps/fetch_wheels.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
MWPARSERFROMHELL_VERSION=0.7.2
REQ=spikes/deps/requirements-game.txt
WHEELS=out/wheels
STAGE=out/site-packages
VENV=out/s10-venv

PY="$(uv python find 3.14 2>/dev/null)" || { echo "fetch_wheels: no CPython 3.14 (run: uv python install 3.14)" >&2; exit 1; }
if [ -d "$STAGE" ] && [ -n "$(ls -A "$STAGE")" ]; then
  echo "fetch_wheels: $STAGE is not empty; ask the owner to approve moving it to the trash (gio trash $STAGE), then re-run" >&2
  exit 1
fi
mkdir -p "$WHEELS" "$STAGE"
# A private venv only for pip (the download flags need a recent pip); nothing is installed into it.
[ -x "$VENV/bin/pip" ] || { "$PY" -m venv "$VENV" && "$VENV/bin/python" -m pip install -q --upgrade pip; }

# 1) Every pinned wheel, hash-checked, for the game's interpreter (cp314, win_amd64).
"$VENV/bin/pip" download -q --no-deps --require-hashes -r "$REQ" -d "$WHEELS" \
  --only-binary=:all: --platform win_amd64 --python-version 3.14 --implementation cp --abi cp314

# 2) mwparserfromhell: no cp314 wheel exists, so build a pure-Python wheel from the sdist. WITHOUT_EXTENSION=1
#    skips its C tokenizer; the package then uses its Python tokenizer (mwparserfromhell.parser.use_c is False).
WITHOUT_EXTENSION=1 "$VENV/bin/pip" wheel -q --no-deps --no-binary :all: \
  "mwparserfromhell==$MWPARSERFROMHELL_VERSION" -w "$WHEELS"

# 3) Record what we have, then unpack (a wheel is a zip) into the staging site-packages.
( cd "$WHEELS" && sha256sum ./*.whl | sed 's# \./# #' > SHA256SUMS )
for w in "$WHEELS"/*.whl; do
  "$PY" -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' "$w" "$STAGE"
done

# 4) Every wheel must be pure Python or built for cp314 win_amd64.
bad=0
for w in "$WHEELS"/*.whl; do
  case "$(basename "$w")" in
    *-py3-none-any.whl|*-cp314-cp314-win_amd64.whl) ;;
    *) echo "fetch_wheels: unexpected wheel tag: $(basename "$w")" >&2; bad=1 ;;
  esac
done
[ "$bad" = 0 ] || exit 1

# 5) Import what can be imported on Linux (rpds, and therefore jsonschema, are Windows-only binaries here).
"$PY" -I -c '
import sys; sys.path.insert(0, sys.argv[1])
import httpx, mwparserfromhell
assert not mwparserfromhell.parser.use_c, "expected the pure-Python tokenizer"
assert [str(t) for t in mwparserfromhell.parse("{{a|b}}").filter_templates()] == ["{{a|b}}"]
print("linux import check: httpx", httpx.__version__, "mwparserfromhell", mwparserfromhell.__version__, "(pure Python)")
' "$STAGE"
ls "$STAGE"/rpds/*.pyd >/dev/null || { echo "fetch_wheels: rpds has no .pyd: the win_amd64 wheel was not used" >&2; exit 1; }
echo "fetch_wheels: $(ls "$WHEELS"/*.whl | wc -l) wheels in $WHEELS, unpacked into $STAGE"
cat "$WHEELS/SHA256SUMS"
