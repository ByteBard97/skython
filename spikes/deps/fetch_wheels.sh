#!/usr/bin/env bash
# Spike S10: fetch skyrim-dm's in-game dependencies as CPython 3.14 / win_amd64 wheels and unpack them into a
# staging site-packages. Writes only under out/ (git-ignored): out/wheels/, out/wheels/SHA256SUMS,
# out/site-packages/. Refuses to write into a non-empty out/site-packages (it never deletes anything).
# Usage: [S10_OUT=out] spikes/deps/fetch_wheels.sh   (S10_OUT: another output root, e.g. out/s10-r2)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
OUTDIR="${S10_OUT:-out}"
REQ=spikes/deps/requirements-game.txt
SDIST_REQ=spikes/deps/mwparserfromhell-sdist.txt
BUILD_REQ=spikes/deps/build-requirements.txt
WHEELS=$OUTDIR/wheels
STAGE=$OUTDIR/site-packages
SDIST=$OUTDIR/sdist
VENV=$OUTDIR/s10-venv
BUILD_VENV=$OUTDIR/s10-build-venv

PY="$(uv python find 3.14 2>/dev/null)" || { echo "fetch_wheels: no CPython 3.14 (run: uv python install 3.14)" >&2; exit 1; }
if [ -d "$STAGE" ] && [ -n "$(ls -A "$STAGE")" ]; then
  echo "fetch_wheels: $STAGE is not empty; ask the owner to approve moving it to the trash (gio trash $STAGE), then re-run" >&2
  exit 1
fi
mkdir -p "$WHEELS" "$STAGE" "$SDIST"
# A private venv only for pip (the download flags need a recent pip); nothing is installed into it.
[ -x "$VENV/bin/pip" ] || { "$PY" -m venv "$VENV" && "$VENV/bin/python" -m pip install -q --upgrade pip; }

# 1) Every pinned wheel, hash-checked, for the game's interpreter (cp314, win_amd64).
"$VENV/bin/pip" download -q --no-deps --require-hashes -r "$REQ" -d "$WHEELS" \
  --only-binary=:all: --platform win_amd64 --python-version 3.14 --implementation cp --abi cp314

# 2) mwparserfromhell: no cp314 wheel exists, so build a pure-Python wheel from the hash-pinned sdist, with the
#    build backend pinned too (build-requirements.txt, --require-hashes, throwaway venv, --no-build-isolation).
#    WITHOUT_EXTENSION=1 skips its C tokenizer; the package then uses its Python tokenizer (use_c is False).
#    The built wheel is not byte-reproducible (two builds give identical content but different zip bytes), so its hash
#    is recorded in SHA256SUMS but not pinned; what is verified is the sdist hash and the pinned build backend.
"$VENV/bin/pip" download -q --no-deps --no-binary :all: --require-hashes -r "$SDIST_REQ" -d "$SDIST"
[ -x "$BUILD_VENV/bin/pip" ] || "$PY" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/pip" install -q --require-hashes -r "$BUILD_REQ"
WITHOUT_EXTENSION=1 "$BUILD_VENV/bin/pip" wheel -q --no-build-isolation --no-deps --no-index \
  "$SDIST"/mwparserfromhell-*.tar.gz -w "$WHEELS"

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
