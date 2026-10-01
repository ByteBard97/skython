# Task 10: Spike S10 — skyrim-dm's dependencies as wheels in the embedded interpreter

New in this plan (no counterpart on branch `plan-out-of-process`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 3, 4.10 and the S10 row of section 9). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; S2, S9 and S4 gate Part C (Tasks 25-32); S7 gates Tasks 28 and 29; **S5, S6 and S10 gate only the M2 plan and skyrim-dm's packaging**: an S10 FAIL is recorded, the spec change is noted, and M1 continues. Run on a throwaway save.

**Question (spec S10):** do skyrim-dm's in-game dependencies install as CPython 3.14 / win_amd64 wheels and import in the embedded interpreter, and can that interpreter make an HTTP request to a server on the Linux host (skyrim-dm talks to local model servers this way)?

**Which dependencies.** skyrim-dm declares `httpx`, `jsonschema`, `mcp` and `mwparserfromhell` (`pyproject.toml`). In its code (checked 2026-10-01): `httpx` is used by the runtime pipeline (`dm/embellisher.py`, `dm/narrator.py`, ...), `jsonschema` by `dm/contract_schema.py` and `events/`, `mwparserfromhell` only by the build-time tool `tools/build_lore_index.py`, and `mcp` only by `mcp_server.py`. So the game needs `httpx` and `jsonschema`; `mcp` is left out; `mwparserfromhell` is included anyway because it is the awkward case (see below), and skyrim-dm decides whether it ever loads it in the game.

**What was already found on Linux while this task was written (2026-10-01):**
- `httpx` 0.28.1 and its dependencies (`anyio`, `certifi`, `h11`, `httpcore`, `idna`, `typing_extensions`) and `jsonschema` 4.26.0 with `attrs`, `jsonschema-specifications`, `referencing` are pure-Python wheels. `rpds-py` (pulled in by `referencing`) is the one compiled dependency and has a `cp314-cp314-win_amd64` wheel (2026.6.3).
- `mwparserfromhell` 0.7.2 publishes **no cp314 wheel and no pure-Python wheel** (only cp39-cp313 and the sdist). Building the hash-pinned sdist (with a hash-pinned build backend, `--no-build-isolation`) with `WITHOUT_EXTENSION=1` gives a `py3-none-any` wheel that uses its pure-Python tokenizer (`mwparserfromhell.parser.use_c` is `False`); it parses correctly on Linux 3.14.
- `pip download --only-binary=:all: --platform win_amd64 --python-version 3.14 --implementation cp --abi cp314` also accepts `py3-none-any` wheels, so one command fetches everything except `mwparserfromhell`.
- uv offers CPython 3.14.7 for Linux, not 3.14.8 (the index's `uv python install 3.14.8` fails with "No download found"); `uv python install 3.14` is what the script below expects. The game's interpreter is 3.14.8 from the embeddable zip; a patch-level difference does not change wheel tags.

**Files:**
- Create: `spikes/deps/requirements-game.txt`, `spikes/deps/mwparserfromhell-sdist.txt`, `spikes/deps/build-requirements.txt`, `spikes/deps/fetch_wheels.sh`, `spikes/deps/s10_check.py`
- Modify: `tools/deploy.sh` (add the `--site-packages DIR` mode)
- Modify: `spikes/RESULTS.md` (the S10 row; then review every row)

**Interfaces:**
- Consumes (Task 3): the spike commands `py_load`, `py_init` and `py_exec <code>` sent with `spikes/spike_client.py` (Task 2), and the Python runtime deployed by `tools/deploy.sh --python` into `Data/SKSE/Plugins/SkyrimPython/python/`. Consumes (Task 1): `tools/env.sh` (`SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`, `SKYRIM_PYTHON_STATE`), `tools/launch_game.sh`, `tools/wait_for_log.sh`, `tools/teardown_list.sh`, `out/deployed.txt`.
- Produces: `tools/deploy.sh --site-packages DIR` (copies `DIR` into `Data/SKSE/Plugins/SkyrimPython/site-packages/`, records every copied entry in `out/deployed.txt`, refuses when the game's folder holds entries `DIR` lacks; Task 26 reuses it); `spikes/deps/fetch_wheels.sh` (writes `out/wheels/`, `out/wheels/SHA256SUMS`, `out/site-packages/`); the S10 row in `spikes/RESULTS.md` with the wheel list and SHA-256s, for the M2 plan and skyrim-dm's packaging.

- [ ] **Step 0: Check the gate**

```bash
grep -E '^\| S8 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S8 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
uv python find 3.14 || uv python install 3.14
```
Expected: the S8 row prints, and a CPython 3.14 path prints. If the S8 row does not print, **STOP and ask the owner** (without an embedded interpreter there is nothing to test).

- [ ] **Step 1: Pin the dependencies**

`spikes/deps/requirements-game.txt` (every version and hash below was downloaded and checked on 2026-10-01; `--require-hashes` makes pip refuse anything else):
```text
# skyrim-dm's in-game dependencies (spike S10), as CPython 3.14 / win_amd64 wheels.
# Fetched by spikes/deps/fetch_wheels.sh with --only-binary=:all: --no-deps --require-hashes.
# Versions and hashes found on 2026-10-01. rpds-py is the only compiled wheel (jsonschema -> referencing -> rpds).
# mwparserfromhell is NOT here: 0.7.2 publishes no cp314 wheel and no pure-Python wheel, so fetch_wheels.sh
# builds a pure-Python wheel from its hash-pinned sdist (mwparserfromhell-sdist.txt, build-requirements.txt).
anyio==4.15.1 --hash=sha256:6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101
attrs==26.1.0 --hash=sha256:c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309
certifi==2026.7.22 --hash=sha256:62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775
h11==0.16.0 --hash=sha256:63cf8bbe7522de3bf65932fda1d9c2772064ffb3dae62d55932da54b31cb6c86
httpcore==1.0.9 --hash=sha256:2d400746a40668fc9dec9810239072b40b4484b640a8c38fd654a024c7a1bf55
httpx==0.28.1 --hash=sha256:d909fcccc110f8c7faf814ca82a9a4d816bc5a6dbfea25d6591d6985b8ba59ad
idna==3.20 --hash=sha256:ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c
jsonschema==4.26.0 --hash=sha256:d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce
jsonschema-specifications==2025.9.1 --hash=sha256:98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe
referencing==0.37.0 --hash=sha256:381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231
rpds-py==2026.6.3 --hash=sha256:0be972be84cfcaf46c8c6edf690ca0f154ac17babf1f6a955a51579b34ad2dc5
typing-extensions==4.16.0 --hash=sha256:481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8
```
The `mwparserfromhell` sdist and its build backend are pinned the same way. `spikes/deps/mwparserfromhell-sdist.txt`:
```text
# mwparserfromhell 0.7.2 sdist (spike S10). No cp314 wheel and no pure-Python wheel exists upstream, so
# fetch_wheels.sh downloads this sdist (--no-binary :all: --no-deps --require-hashes) and builds it locally.
mwparserfromhell==0.7.2 --hash=sha256:f4193072e9ea93b9e88f772f60a02125c0602d32890d0bbdcb275ed58c8b3763
```
`spikes/deps/build-requirements.txt`:
```text
# Build backend for the mwparserfromhell sdist (its pyproject: setuptools >= 77.0.3, setuptools-scm >= 8), pinned.
# fetch_wheels.sh installs exactly these (--require-hashes) into a throwaway venv, then builds with --no-build-isolation.
# Found 2026-10-01.
packaging==26.3 --hash=sha256:d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c
setuptools==84.0.0 --hash=sha256:51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670
setuptools-scm==10.3.4 --hash=sha256:82f34c3e3084fc2b57d397200637cc13f2338004052d2fee47baa5f0e902464e
vcs-versioning==2.5.0 --hash=sha256:dbf44f6303dc817e5792cb7d0e9cc90e3046ee1e32eaa357b20ad368811313d0
```
If a pin can no longer be downloaded, **STOP and ask the owner** (do not loosen a pin or drop a hash on your own).

- [ ] **Step 2: Write the fetch script and run it**

`spikes/deps/fetch_wheels.sh`:
```bash
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
```
Run:
```bash
chmod +x spikes/deps/fetch_wheels.sh
bash -n spikes/deps/fetch_wheels.sh
spikes/deps/fetch_wheels.sh
```
Expected (the last lines; the `mwparserfromhell` hash may differ, because the wheel is built locally and a build is not byte-reproducible):
```text
linux import check: httpx 0.28.1 mwparserfromhell 0.7.2 (pure Python)
fetch_wheels: 13 wheels in out/wheels, unpacked into out/site-packages
6152fdbbf9a77fdec97731721bebf7c4c44f7c29b424b0065826173efc7ed101  anyio-4.15.1-py3-none-any.whl
...
0be972be84cfcaf46c8c6edf690ca0f154ac17babf1f6a955a51579b34ad2dc5  rpds_py-2026.6.3-cp314-cp314-win_amd64.whl
481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8  typing_extensions-4.16.0-py3-none-any.whl
```
A second run stops with "out/site-packages is not empty": that is the script refusing to mix versions, not a failure of the spike. Everything it writes is under `out/` (git-ignored).

- [ ] **Step 3: Write the in-game check script and dry-run it on Linux**

`spikes/deps/s10_check.py`:
```python
"""Spike S10 check. Runs INSIDE the game's embedded CPython through Task 3's `py_exec`, as
    exec(open(CHECK).read(), {"__name__": "s10", "SITE": ..., "OUT": ..., "URL": ...})
It never raises: every step's result or error goes into the JSON file OUT, which the Linux side reads.
SITE: the deployed site-packages folder. URL: a plain-HTTP URL served from Linux.
The imports run first WITHOUT touching sys.path: they must work through the site-packages entry that
PyLoad.cpp configured (module_search_paths), which is what Task 26 relies on. Only if that pass fails is SITE
inserted at the front and the steps run again, to tell a path problem from a wheel problem."""
import importlib
import importlib.metadata
import json
import os
import ssl
import sys
import time
import traceback

result = {"python": sys.version, "executable": sys.executable, "platform": sys.platform,
          "openssl": ssl.OPENSSL_VERSION, "site": SITE, "steps": {}}


def step(name, fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        result["steps"][name] = {"ok": True, "value": value, "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except BaseException as e:  # noqa: BLE001 - a spike records everything, including SystemExit
        result["steps"][name] = {"ok": False, "error": f"{type(e).__name__}: {e}",
                                 "trace": traceback.format_exc(limit=6)}


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def check_configured_path():
    # Before any insert: is the configured site-packages entry (another spelling of SITE) on sys.path?
    hits = [p for p in sys.path if _same(p, SITE)]
    result["configured_site_in_path"] = bool(hits)
    return {"configured_site_in_path": bool(hits), "entries": hits, "path_head": sys.path[:4]}


def check_httpx_import():
    import httpx
    return httpx.__version__


def check_jsonschema():
    import jsonschema
    import rpds  # the compiled cp314 win_amd64 extension (.pyd)
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    jsonschema.validate({"n": 1}, schema)
    try:
        jsonschema.validate({"n": "x"}, schema)
        raise AssertionError("an invalid document was accepted")
    except jsonschema.ValidationError:
        pass
    return {"jsonschema": importlib.metadata.version("jsonschema"), "rpds": rpds.__file__}


def check_mwparserfromhell():
    import mwparserfromhell
    found = [str(t) for t in mwparserfromhell.parse("x {{a|b}} y").filter_templates()]
    assert found == ["{{a|b}}"], found
    return {"version": mwparserfromhell.__version__, "use_c": mwparserfromhell.parser.use_c}


def check_httpx_get():
    import httpx
    # trust_env=False: no proxy or certificate settings from the player's environment.
    with httpx.Client(timeout=10.0, trust_env=False) as client:  # builds a default SSL context (certifi) too
        r = client.get(URL)
        return {"status": r.status_code, "body": r.text.strip()}


CHECKS = [("import_httpx", check_httpx_import), ("jsonschema_validate", check_jsonschema),
          ("mwparserfromhell_parse", check_mwparserfromhell), ("httpx_get", check_httpx_get)]


def all_ok(suffix=""):
    s = result["steps"]
    return all(s[n + suffix]["ok"] for n, _ in CHECKS) and s["httpx_get" + suffix].get("value", {}).get("body") == "s10-ok"


step("sys_path", check_configured_path)
for name, fn in CHECKS:  # pass 1: the configured sys.path only
    step(name, fn)
result["configured_path_pass"] = all_ok()
if not result["configured_path_pass"]:
    # Diagnosis only: insert SITE and retry in a clean import state (failed imports may have left partial modules).
    for mod in [m for m in sys.modules if m.split(".")[0] in ("httpx", "httpcore", "anyio", "h11", "certifi", "idna", "jsonschema",
                                                               "jsonschema_specifications", "referencing", "rpds", "attrs", "attr",
                                                               "mwparserfromhell", "typing_extensions", "sniffio")]:
        del sys.modules[mod]
    importlib.invalidate_caches()
    sys.path.insert(0, SITE)
    for name, fn in CHECKS:
        step(name + "_with_insert", fn)
    result["with_insert_pass"] = all_ok("_with_insert")
result["pass"] = result["configured_site_in_path"] and result["configured_path_pass"]
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2)
```
Dry run on Linux against a local HTTP server (port 45981: the bridge uses 45977, the spike harness 45978, and Task 3's in-game `echo_server.py` 45979; Wine's loopback is the host's, so a game session with the echo server still running would otherwise take the port). Two runs: the first like a bare interpreter (the staged folder is not on `sys.path`), the second with the folder already on `sys.path` under another spelling (`<dir>/./`), the way `PyLoad.cpp`'s configured entry differs from the `Z:/...` string passed as `SITE` in the game:
```bash
mkdir -p out/s10-www && echo s10-ok > out/s10-www/s10.txt
( timeout 60 python3 -m http.server 45981 --bind 127.0.0.1 --directory out/s10-www >/dev/null 2>&1 & )
for i in $(seq 1 20); do curl -fsS http://127.0.0.1:45981/s10.txt 2>/dev/null && break; sleep 0.5; done   # must print s10-ok
show() { python3 -c "import json, sys; r = json.load(open(sys.argv[1])); print('pass', r['pass'], 'configured_site_in_path', r['configured_site_in_path'], 'configured_path_pass', r['configured_path_pass'], 'with_insert_pass', r.get('with_insert_pass')); [print(k, v['ok'], v.get('value', v.get('error'))) for k, v in r['steps'].items()]" "$1"; }
"$(uv python find 3.14)" -I -c "exec(open('spikes/deps/s10_check.py').read(), {'__name__': 's10', 'SITE': '$PWD/out/site-packages', 'OUT': '$PWD/out/s10-linux.json', 'URL': 'http://127.0.0.1:45981/s10.txt'})"
show out/s10-linux.json
"$(uv python find 3.14)" -I -c "import sys; sys.path.append('$PWD/out/site-packages/./'); exec(open('spikes/deps/s10_check.py').read(), {'__name__': 's10', 'SITE': '$PWD/out/site-packages', 'OUT': '$PWD/out/s10-linux2.json', 'URL': 'http://127.0.0.1:45981/s10.txt'})"
show out/s10-linux2.json
```
Expected on Linux (checked while writing this task): `s10-ok` from `curl` (if it does not print, the server did not start: check that nothing else holds 45981, then retry). First run: `pass False configured_site_in_path False configured_path_pass False with_insert_pass False`; `sys_path True {'configured_site_in_path': False, ...}`; the four pass-1 steps `False ModuleNotFoundError` (nothing configured); then `import_httpx_with_insert True 0.28.1`, `jsonschema_validate_with_insert False ModuleNotFoundError: No module named 'rpds.rpds'`, `mwparserfromhell_parse_with_insert True {'version': '0.7.2', 'use_c': False}`, `httpx_get_with_insert True {'status': 200, 'body': 's10-ok'}`. Second run: `configured_site_in_path True` (the `<dir>/./` entry is found by `os.path.samefile`), and pass 1 itself gives `import_httpx True`, `mwparserfromhell_parse True`, `httpx_get True` and the same `rpds.rpds` failure (so the `_with_insert` steps follow again, with the same results). That one failure is correct on Linux: `rpds` is a Windows `.pyd`. In the game, `configured_site_in_path` must be true and all five pass-1 steps must pass (no `_with_insert` steps then appear).

- [ ] **Step 4: Add `--site-packages` to `tools/deploy.sh`**

Insert this block into `tools/deploy.sh` directly after the line `source "$ROOT/tools/env.sh"`, before any other argument handling (Task 3's `--python` and the DLL path). It handles its own arguments and exits, so it does not interact with the other modes:
```bash
# --site-packages DIR (Task 10): copy a staged site-packages into Data/SKSE/Plugins/SkyrimPython/site-packages/
# and exit. Handled before any other argument, so it works whatever other options deploy.sh has.
if [ "${1:-}" = "--site-packages" ]; then
  SP_SRC="${2:?usage: tools/deploy.sh --site-packages DIR}"
  [ -d "$SP_SRC" ] || { echo "deploy: no such directory: $SP_SRC" >&2; exit 1; }
  SP_DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/site-packages"
  mkdir -p "$SP_DEST" "$ROOT/out"
  # Never mix versions: an entry in the game's site-packages that the new one lacks stops the copy (nothing is deleted).
  stale="$(comm -23 <(ls -A "$SP_DEST" | sort) <(ls -A "$SP_SRC" | sort))"
  if [ -n "$stale" ]; then
    echo "deploy: $SP_DEST holds entries the new site-packages does not have:" >&2
    echo "$stale" >&2
    echo "deploy: ask the owner to approve moving exactly these to the trash (gio trash), then re-run" >&2
    exit 1
  fi
  cp -r "$SP_SRC"/. "$SP_DEST"/
  ls -A "$SP_SRC" | while IFS= read -r e; do echo "$SP_DEST/$e" >> "$ROOT/out/deployed.txt"; done
  echo "deploy: $(ls -A "$SP_SRC" | wc -l) entries copied to $SP_DEST"
  exit 0
fi
```
Check:
```bash
bash -n tools/deploy.sh
```
Expected: no output. (The block was tested against a scratch copy of the game folder: a first and a second run copy 27 entries; an extra folder in the game's `site-packages` stops the copy with exit code 1 and names it.)

- [ ] **Step 5: Run the check inside the game** (throwaway save loaded or main menu; Python works at the main menu)

```bash
source tools/env.sh
tools/deploy.sh --site-packages out/site-packages
CHECK_DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/s10_check.py"
cp spikes/deps/s10_check.py "$CHECK_DEST" && echo "$CHECK_DEST" >> out/deployed.txt
RESULT="$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/s10-result.json"
echo "$RESULT" >> out/deployed.txt      # the game writes it; listed so teardown shows it
# If the game is not running yet (or still runs a spike build from before Task 3), quit it (qqq) and:
tools/launch_game.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "listening on 127.0.0.1:45978" 600
# Bring the interpreter up exactly as Task 3 does (an "already loaded/initialised" answer is fine):
python3 spikes/spike_client.py py_load
python3 spikes/spike_client.py py_init
# A Linux HTTP server for the httpx step, bounded to 10 minutes, on 45981 (45979 is Task 3's in-game echo server):
mkdir -p out/s10-www && echo s10-ok > out/s10-www/s10.txt
( timeout 600 python3 -m http.server 45981 --bind 127.0.0.1 --directory out/s10-www >/dev/null 2>&1 & )
for i in $(seq 1 20); do curl -fsS http://127.0.0.1:45981/s10.txt 2>/dev/null && break; sleep 0.5; done   # must print s10-ok before going on
# Windows paths for the game: Wine maps Z: to /, and Python accepts forward slashes.
W="Z:$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython"
python3 spikes/spike_client.py py_exec "exec(open('$W/s10_check.py').read(), {'__name__': 's10', 'SITE': '$W/site-packages', 'OUT': '$W/s10-result.json', 'URL': 'http://127.0.0.1:45981/s10.txt'})"
python3 -c "import json, sys; r = json.load(open(sys.argv[1])); print('S10', 'PASS' if r['pass'] else 'FAIL', r['python'].split()[0], r['openssl'], 'configured_site_in_path', r['configured_site_in_path']); [print(' ', k, v['ok'], v.get('value', v.get('error'))) for k, v in r['steps'].items()]" "$RESULT"
```
If `curl` does not print `s10-ok`, do not run the `py_exec`: the server did not bind (something else holds 45981); find what (`ss -ltnp | grep 45981`) and retry. Expected PASS: `S10 PASS 3.14.8 OpenSSL ... configured_site_in_path True`, then `sys_path True` (`configured_site_in_path: True`, `entries` naming the configured `...\SkyrimPython\site-packages`; the imports below ran without any `sys.path` change), `import_httpx True 0.28.1`, `jsonschema_validate True {'jsonschema': '4.26.0', 'rpds': '<path ending in rpds __init__.py>'}`, `mwparserfromhell_parse True {'version': '0.7.2', 'use_c': False}`, `httpx_get True {'status': 200, 'body': 's10-ok'}`, and no `_with_insert` steps. The game keeps running. If `configured_site_in_path` is false or pass 1 fails while the `_with_insert` steps pass, the wheels are fine but the configured `site-packages` entry is not working: that is an S10 FAIL with a Task 26 consequence (its `module_search_paths` entry); record both passes.

If `s10-result.json` does not appear, the `py_exec` answer and `python.log`/the spike log say why (a path typo shows as `FileNotFoundError` in the `py_exec` reply). A step with `"ok": false` carries its traceback in the JSON: copy it into the RESULTS.md row. If `jsonschema_validate` fails with a DLL load error for `rpds`, record the exact message: it is the finding that matters for skyrim-dm (spec 4.10's fallback is a pure-Python replacement or running that dependency outside the game).

- [ ] **Step 6: Record S10, review every M0 row, list the teardown, commit**

Fill the S10 row in `spikes/RESULTS.md` (add the row if Task 2's table does not have it yet: `| S10 skyrim-dm dependencies as wheels (Task 10) | <date> | PASS/FAIL | <evidence> | 4.10 (FAIL: pure-Python replacements, or the dependency runs outside the game) |`). Spec S10's criterion asks for `pip download --only-binary=:all: ...` to succeed for every dependency; it cannot for `mwparserfromhell` (no cp314 or pure wheel exists upstream), and this task builds that one wheel locally from the sdist. So when Step 5 passes, the result column says **`PASS with deviation: mwparserfromhell built from sdist (owner to accept)`**, not a plain `PASS`, and the owner accepts or rejects the deviation. Evidence: the output of Step 5, `out/wheels/SHA256SUMS` pasted in full, the `mwparserfromhell` finding (no cp314 wheel upstream; a locally built pure-Python wheel works), and that `mcp` was left out because skyrim-dm only uses it for its MCP server.

Then review all rows (M0 is complete only when every row is filled):
- S1, S8: must be PASS (they gated Tasks 3 and 18-32).
- S2, S9 or S4 FAIL: STOP and ask the owner (they gate Part C, Tasks 25-32).
- S7: each FAIL sub-test names its Task 27-30 change in the row; a FAIL that is not one of the listed changes: STOP and ask the owner.
- S10 `PASS with deviation`: ask the owner to accept the `mwparserfromhell` deviation; record their answer in the row.
- S5, S6 or S10 FAIL: write the change from spec section 9's "If it fails, change" column into the row's last cell, tell the owner, and do not edit the spec until they agree. M1 continues.

Then list what M0 deployed, **show the owner the list, and remove nothing without their sign-off on that exact list** (`gio trash` only; never `rm`). Stop the HTTP server if it is still running (it also stops by itself after 10 minutes).
```bash
tools/teardown_list.sh
git add spikes/deps spikes/RESULTS.md tools/deploy.sh
git commit -m "test(spike): S10 skyrim-dm dependencies as cp314 win_amd64 wheels in the embedded interpreter"
```
