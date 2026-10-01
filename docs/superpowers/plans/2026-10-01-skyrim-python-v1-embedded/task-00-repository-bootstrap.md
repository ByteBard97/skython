# Task 0: Repository bootstrap, vendored headers, pinned CPython

Adapted from: branch `plan-out-of-process`, task 0 (`task-00-repository-bootstrap.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4). Steps use checkbox (`- [ ]`) syntax for tracking.

**Files:**
- Create: `LICENSE`, `.gitmodules` (via git), `third_party/nlohmann/json.hpp`, `third_party/doctest/doctest.h`, `third_party/pybind11/LICENSE`, `third_party/pybind11/include/pybind11/**` (54 headers), `plugin/extern/CommonLibSSE-NG` (submodule), `tools/fetch_python.sh`, `docs/PINS.md`
- Modify: `.gitignore` (append only; never overwrite it)
- Not committed (gitignored, made by `tools/fetch_python.sh`): `third_party/cpython/{downloads,embed,nuget}/`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - A git repo at `/home/geoff/projects/games/skyrim/skyrim-python` with the vendored headers and the CommonLibSSE-NG submodule pinned at `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a`, every later task builds against.
  - `third_party/pybind11/include/` (pybind11 3.1.0, BSD-3, its `LICENSE` beside it): the include directory for `pyglue/` (Task 19) and the plugin core (Task 25).
  - `tools/fetch_python.sh [--check]` (Contract): downloads `python-3.14.8-embed-amd64.zip` and `python.3.14.8.nupkg`, verifies their SHA-256 against the `sha256sum`-format lines in `docs/PINS.md`, unpacks them to `third_party/cpython/embed/` and `third_party/cpython/nuget/`. `--check` verifies only and exits non-zero on any mismatch or missing file. It never deletes; a stale unpacked folder is reported, not replaced. Test hooks: `CPYTHON_DIR`, `PINS_FILE`, `FETCH_FROM=<dir>`.
  - After it runs: `third_party/cpython/embed/{python314.dll,python314.zip,python314._pth,LICENSE.txt,vcruntime140.dll,...}` (the runtime Task 3 and Task 26 deploy) and `third_party/cpython/nuget/tools/{include/Python.h,libs/python314.lib}` (the build inputs Task 1's `remote_build.sh` syncs to the box).
  - `docs/PINS.md`: the CommonLibSSE-NG commit and the API names checked in it, the CPython/NuGet/pybind11/nlohmann/doctest versions and SHA-256s, and the uv Pythons installed for the Linux tests.
  - uv-managed CPython 3.14 (newest 3.14.x uv offers; uv 0.12.7 offers 3.14.7, not yet 3.14.8; the ABI is the same within 3.14) and 3.12 on the Linux host.

Pinned SHA-256 values used below (computed on 2026-10-01 from the official downloads):

| Artifact | Source | SHA-256 |
|---|---|---|
| `python-3.14.8-embed-amd64.zip` | `https://www.python.org/ftp/python/3.14.8/` | `a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310` |
| `python.3.14.8.nupkg` | `https://api.nuget.org/v3-flatcontainer/python/3.14.8/` | `ce85f674d9a63029f709cbff7a3da1c6bc5bfcfaefdd9999f98fa0290470c454` |
| `pybind11-3.1.0.tar.gz` (PyPI sdist; PyPI files are immutable, GitHub's generated archives are not) | `https://files.pythonhosted.org/packages/76/f3/95b0f40b31df41dbfe6bb0857419c9442c15839cbac4796f1c26ae0b6081/pybind11-3.1.0.tar.gz` | `a1cc06b524ab3edca51f8ad3895f9c4fa20b8b19283173dff4ae781449dc9639` |
| `json.hpp` 3.11.3 | nlohmann/json release asset | `9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6` |
| `doctest.h` 2.4.11 | doctest tag `v2.4.11` | `44faa038e9c3f9728efbda143748d01124ea0a27f4bf78f35a15d8fab2e039fb` |

**If any downloaded file's SHA-256 differs from this table, STOP and ask the owner.** Do not update the hash to match.

- [ ] **Step 1: Add ignore rules and the licence**

The repository already exists: it was initialised on 2026-09-30 with the README, `.gitignore` (which already ignores `HANDOFF-*.md`, `build/`, `*/build/`, `out/`, `__pycache__/`, `.venv/`, `.pytest_cache/`) and the docs commits, authored as the ByteBard97 noreply identity set in the repo config. Do not re-run `git init`. **Append** with `>>` (never `>`; the existing rules must survive):

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
git config user.email   # must print 11368675+ByteBard97@users.noreply.github.com; if not, STOP and ask the owner
cat >> .gitignore <<'EOF'

# plugin and Papyrus build artifacts
**/build/
*.dll
*.pdb
*.pex
*.egg-info/

# CPython runtime and build inputs, fetched by tools/fetch_python.sh (never committed)
third_party/cpython/
EOF
cat > LICENSE <<'EOF'
MIT License

Copyright (c) 2026 Geoffrey Wall

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
grep -c 'third_party/cpython/' .gitignore
```
Expected: `1`. (`out/` is already ignored by the original `.gitignore`.)

- [ ] **Step 2: Vendor the single-header libraries and pybind11**

```bash
mkdir -p third_party/nlohmann third_party/doctest third_party/pybind11
curl -fsSL -o third_party/nlohmann/json.hpp https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp
curl -fsSL -o third_party/doctest/doctest.h https://raw.githubusercontent.com/doctest/doctest/v2.4.11/doctest/doctest.h
sha256sum -c - <<'EOF'
9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6  third_party/nlohmann/json.hpp
44faa038e9c3f9728efbda143748d01124ea0a27f4bf78f35a15d8fab2e039fb  third_party/doctest/doctest.h
EOF

# pybind11 3.1.0: headers and LICENSE only, from the PyPI sdist. The tarball goes to a temp dir that is
# never removed by this plan (the OS clears /tmp).
T="$(mktemp -d)"
curl -fsSL -o "$T/pybind11-3.1.0.tar.gz" \
  https://files.pythonhosted.org/packages/76/f3/95b0f40b31df41dbfe6bb0857419c9442c15839cbac4796f1c26ae0b6081/pybind11-3.1.0.tar.gz
echo "a1cc06b524ab3edca51f8ad3895f9c4fa20b8b19283173dff4ae781449dc9639  $T/pybind11-3.1.0.tar.gz" | sha256sum -c -
tar -xzf "$T/pybind11-3.1.0.tar.gz" -C third_party/pybind11 --strip-components=1 pybind11-3.1.0/include pybind11-3.1.0/LICENSE
grep -E 'define PYBIND11_VERSION_(MAJOR|MINOR|PATCH)' third_party/pybind11/include/pybind11/detail/common.h
find third_party/pybind11 -name '*.h' | wc -l
ls third_party/pybind11
```
Expected: three `: OK` lines; `PYBIND11_VERSION_MAJOR 3`, `MINOR 1`, `PATCH 0`; `54`; `include  LICENSE`. Any `FAILED` from `sha256sum -c`: **STOP and ask the owner.**

- [ ] **Step 3: Add CommonLibSSE-NG as a submodule pinned at the verified commit**

`git submodule add -b ng` checks out the branch head, which may be newer than the commit the plan's API notes (`API-VERIFICATION.md`) were checked against, so check out the pin explicitly.

```bash
git submodule add -b ng https://github.com/alandtse/CommonLibSSE-NG plugin/extern/CommonLibSSE-NG
git -C plugin/extern/CommonLibSSE-NG checkout --quiet 39f9d07a6ffabea8fb559eee87ab7d27cd463e8a
# CommonLib has its own submodule (extern/openvr, headers needed even with VR off); add -b does not fetch it:
git submodule update --init --recursive    # about 40 s; openvr's samples/ are large and are excluded from the box sync
cd plugin/extern/CommonLibSSE-NG
git log -1 --format='%H %cs %s'
grep -n -i -E '1\.7\.99|1\.7\.104|address library.*(5|v5)' CHANGELOG.md | head
grep -n -E 'option\(ENABLE_SKYRIM_(SE|AE|VR)|option\(BUILD_TESTS' CMakeLists.txt
ls extern/openvr/headers | head -3
cd ../../..
git status --short .gitmodules plugin/extern/CommonLibSSE-NG
```
Expected: `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a 2026-09-30 chore(release): 10.1.0 [skip ci]`; the CHANGELOG grep shows the 1.7.99 / Address Library v5 lines (it does **not** mention 1.7.104; that is known and recorded in `API-VERIFICATION.md`); the option grep shows `ENABLE_SKYRIM_SE`, `ENABLE_SKYRIM_AE`, `ENABLE_SKYRIM_VR`, `BUILD_TESTS`; openvr headers exist; `git status` shows `.gitmodules` and the submodule as added. If `git log` prints another commit, **STOP and ask the owner.**

- [ ] **Step 4: Write `tools/fetch_python.sh` and `docs/PINS.md`**

```bash
mkdir -p tools docs
cat > tools/fetch_python.sh <<'EOF'
#!/usr/bin/env bash
# Fetch the pinned CPython runtime and build inputs into third_party/cpython/ (gitignored).
#   embed/  <- python-3.14.8-embed-amd64.zip (python.org): the runtime deployed into the game
#   nuget/  <- python.3.14.8.nupkg (nuget.org): tools/include/ and tools/libs/python314.lib for the build
# Usage: tools/fetch_python.sh          download (if needed), verify SHA-256, unpack
#        tools/fetch_python.sh --check  verify only; downloads and unpacks nothing; non-zero on any mismatch
# The expected SHA-256s are the sha256sum-format lines in docs/PINS.md. A mismatch is never "fixed" here:
# STOP and ask the owner. This script removes nothing; a stale unpacked folder is reported, not replaced.
# Env (for tests): CPYTHON_DIR (default third_party/cpython), PINS_FILE (default docs/PINS.md),
#                  FETCH_FROM=<dir> copies the archives from a local folder instead of downloading.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CPY="${CPYTHON_DIR:-$ROOT/third_party/cpython}"
PINS="${PINS_FILE:-$ROOT/docs/PINS.md}"
EMBED=python-3.14.8-embed-amd64.zip
NUPKG=python.3.14.8.nupkg
declare -A URL=(
  [$EMBED]="https://www.python.org/ftp/python/3.14.8/$EMBED"
  [$NUPKG]="https://api.nuget.org/v3-flatcontainer/python/3.14.8/$NUPKG"
)
declare -A DEST=([$EMBED]=embed [$NUPKG]=nuget)
die() { echo "fetch_python: $*" >&2; exit 1; }

CHECK=0
case "${1:-}" in
  --check) CHECK=1 ;;
  "") ;;
  *) die "usage: fetch_python.sh [--check]" ;;
esac
[ -f "$PINS" ] || die "no pins file at $PINS (Task 0 Step 4 writes it)"

expected() {  # sha256 of $1 from the pins file, or empty
  grep -E "^[0-9a-f]{64}  $1\$" "$PINS" | head -n1 | cut -d' ' -f1
}

status=0
mkdir -p "$CPY/downloads"
for f in "$EMBED" "$NUPKG"; do
  want="$(expected "$f")"
  [ -n "$want" ] || die "docs/PINS.md has no sha256 line for $f"
  arch="$CPY/downloads/$f"
  if [ ! -f "$arch" ]; then
    if [ "$CHECK" = 1 ]; then echo "MISSING $arch" >&2; status=1; continue; fi
    if [ -n "${FETCH_FROM:-}" ]; then cp "$FETCH_FROM/$f" "$arch.part"
    else curl -fsSL --retry 3 --max-time 600 -o "$arch.part" "${URL[$f]}" || die "download of ${URL[$f]} failed"; fi
    mv "$arch.part" "$arch"
  fi
  got="$(sha256sum "$arch" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    echo "fetch_python: SHA-256 mismatch for $arch" >&2
    echo "  expected $want (docs/PINS.md)" >&2
    echo "  got      $got" >&2
    echo "  STOP and ask the owner. Nothing was unpacked." >&2
    exit 1
  fi
  out="$CPY/${DEST[$f]}"
  stamp="$out/.sha256"
  if [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$want" ]; then
    echo "ok $f ($want) -> $out"
    continue
  fi
  if [ "$CHECK" = 1 ]; then echo "NOT UNPACKED (or stale) $out" >&2; status=1; continue; fi
  if [ -e "$out" ]; then
    die "$out exists but was not unpacked from this archive; ask the owner to approve moving it away (gio trash), then re-run"
  fi
  mkdir -p "$out"
  unzip -q "$arch" -d "$out" || die "unzip of $arch failed"
  printf '%s' "$want" > "$stamp"
  echo "unpacked $f -> $out"
done

# The files later tasks rely on must be there.
if [ "$status" = 0 ]; then
  for need in embed/python314.dll embed/python314.zip embed/LICENSE.txt \
              nuget/tools/include/Python.h nuget/tools/libs/python314.lib; do
    [ -f "$CPY/$need" ] || { echo "MISSING $CPY/$need" >&2; status=1; }
  done
fi
[ "$status" = 0 ] && echo "fetch_python: CPython 3.14.8 runtime and build inputs verified in $CPY"
exit "$status"
EOF
chmod +x tools/fetch_python.sh
bash -n tools/fetch_python.sh
command -v shellcheck >/dev/null && shellcheck tools/fetch_python.sh || echo "(shellcheck not installed; bash -n passed)"
```

Then `docs/PINS.md`. The sha256 block **must** keep exactly this format (64 hex, two spaces, file name), because `fetch_python.sh` reads it. The `<...>` fields are filled in from what Step 3 and Step 6 actually print; they are observations, not guesses:

````bash
cat > docs/PINS.md <<'EOF'
# Pins

Recorded 2026-10-01 (Task 0). Later tasks cite this file instead of re-deriving.

## CommonLibSSE-NG

- Repository `https://github.com/alandtse/CommonLibSSE-NG`, branch `ng`, submodule `plugin/extern/CommonLibSSE-NG`.
- Commit `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (2026-09-30, `chore(release): 10.1.0 [skip ci]`), project version 10.1.0.
- CHANGELOG mentions AE 1.7.99 and Address Library format 5; it does not mention 1.7.104 (support for 1.7.104 is unverified).
- CMake options: `ENABLE_SKYRIM_SE`, `ENABLE_SKYRIM_AE`, `ENABLE_SKYRIM_VR`, `BUILD_TESTS` (as printed by Task 0 Step 3).
- vcpkg packages the build needs (box tree 2026-08-24): `spdlog`, `rapidcsv`, `directxtk`.
- Full signature notes: `docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md`.

### API names checked in the pinned checkout (Task 0 Step 5)

<one line per grep in Step 5: the exact signature found, or "absent">

## CPython

- Version 3.14.8, standard (GIL) build, x64.
- Runtime: `python-3.14.8-embed-amd64.zip` from python.org, unmodified; ships `python314.dll`, `python314.zip`, `python314._pth`, `vcruntime140.dll`, `vcruntime140_1.dll`, `LICENSE.txt`. The `._pth` file is ignored once `PyConfig.module_search_paths_set = 1`.
- Build inputs: NuGet package `python` 3.14.8, `tools/include/` and `tools/libs/python314.lib`.
- Linux test interpreters (uv): `<output of: uv python find 3.14>` (`<its --version>`), `<output of: uv python find 3.12>` (`<its --version>`). uv offered no 3.14.8 build on 2026-10-01; any 3.14.x has the same ABI.

## pybind11

- 3.1.0, headers and LICENSE from the PyPI sdist `pybind11-3.1.0.tar.gz`, vendored at `third_party/pybind11/`.

## Downloaded artifacts (sha256sum format; tools/fetch_python.sh reads the python lines)

```
a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310  python-3.14.8-embed-amd64.zip
ce85f674d9a63029f709cbff7a3da1c6bc5bfcfaefdd9999f98fa0290470c454  python.3.14.8.nupkg
a1cc06b524ab3edca51f8ad3895f9c4fa20b8b19283173dff4ae781449dc9639  pybind11-3.1.0.tar.gz
9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6  json.hpp
44faa038e9c3f9728efbda143748d01124ea0a27f4bf78f35a15d8fab2e039fb  doctest.h
```
EOF
````

- [ ] **Step 5: Check the API names later tasks depend on, and record them in `docs/PINS.md`**

`API-VERIFICATION.md` already holds the full table for this commit. Re-run the greps to confirm the checkout is the one that table describes, and replace the `<one line per grep ...>` placeholder in `docs/PINS.md` with one line per grep (the exact signature found, or "absent"):

```bash
cd plugin/extern/CommonLibSSE-NG
grep -rn "FindBoundObject" include/RE/I/IVirtualMachine.h        # 3 parameters: (VMHandle, const char*, BSTSmartPointer<Object>&) const; Tasks 9 and 28
grep -rn "DispatchMethodCall\|DispatchStaticCall" include/RE/I/IVirtualMachine.h include/RE/V/VirtualMachine.h
grep -rn "void PackHandle" include/RE/P/PackUnpack.h
grep -rn "Load(" include/RE/B/BGSSaveLoadManager.h              # two overloads; Task 8 (S6)
grep -rn "GetVMTypeID\|VMTYPEID" include/RE/B/BGSBaseAlias.h include/RE/B/BGSRefAlias.h include/RE/B/BGSLocAlias.h
grep -rn "GetReference" include/RE/B/BGSRefAlias.h
grep -n "Init(" include/SKSE/API.h                                 # Init(const LoadInterface*, InitInfo = {}); Init(skse, bool) is [[deprecated]]
grep -rn "GetObjectHandlePolicy" include/RE/I/IVirtualMachine.h
grep -rn "RegisterFunction" include/RE/I/IVirtualMachine.h
grep -rn "MakeFunctionArguments" include/RE/F/FunctionArguments.h
grep -rn "GameIsPaused" include/RE/U/UI.h
grep -rn "AddTask\|AddUITask" include/SKSE/Interfaces.h
cd ../../..
```
Expected: every line matches the corresponding row of `API-VERIFICATION.md`. A difference means the checkout is not the pinned commit: **STOP and ask the owner.** `docs/PINS.md` is a plain note; nothing in it is a secret.

- [ ] **Step 6: Fetch CPython, prove the checksum gate, install the Linux test interpreters**

```bash
tools/fetch_python.sh
tools/fetch_python.sh --check; echo "check rc=$?"
# Negative test: a pins file with one wrong digit must fail and unpack nothing. The tampered copy lives in a
# temp dir (never committed, never removed by this plan).
T="$(mktemp -d)"
sed 's/^a93abe4/b93abe4/' docs/PINS.md > "$T/PINS.tampered.md"
PINS_FILE="$T/PINS.tampered.md" tools/fetch_python.sh --check; echo "tampered rc=$?"
ls third_party/cpython/embed/python314.dll third_party/cpython/nuget/tools/include/Python.h third_party/cpython/nuget/tools/libs/python314.lib
git status --short third_party/cpython | wc -l

uv python install 3.14 3.12
"$(uv python find 3.14)" --version
"$(uv python find 3.12)" --version
"$(uv python find 3.14)" -c "import sysconfig; print(sysconfig.get_paths()['include'])"
```
Expected:
- First run: `unpacked python-3.14.8-embed-amd64.zip -> .../embed`, `unpacked python.3.14.8.nupkg -> .../nuget`, then `fetch_python: CPython 3.14.8 runtime and build inputs verified in ...`.
- `--check`: two `ok ...` lines, the verified line, `check rc=0`.
- Tampered: `fetch_python: SHA-256 mismatch for .../python-3.14.8-embed-amd64.zip`, `expected b93abe...`, `got a93abe...`, `STOP and ask the owner. Nothing was unpacked.`, `tampered rc=1`.
- The three files exist; `git status` prints `0` (the folder is ignored).
- `Python 3.14.x` and `Python 3.12.x`; the include path ends in `include/python3.14` and contains `Python.h`.

Fill the uv lines in `docs/PINS.md` from this output. A real mismatch on the first run (not the tampered test): **STOP and ask the owner.**

- [ ] **Step 7: Commit**

```bash
git add .gitignore LICENSE .gitmodules third_party/nlohmann third_party/doctest third_party/pybind11 \
        plugin/extern/CommonLibSSE-NG tools/fetch_python.sh docs/PINS.md
git status --short | grep cpython && echo "STOP: third_party/cpython must not be staged" || true
git commit -m "chore: bootstrap repo with vendored headers, pybind11, pinned CommonLibSSE-NG and CPython fetch script"
```
Expected: the `grep cpython` prints nothing; the commit succeeds. Do not push.
</content>
</invoke>
