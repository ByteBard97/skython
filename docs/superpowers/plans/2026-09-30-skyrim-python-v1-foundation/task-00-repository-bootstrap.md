# Task 0: Repository bootstrap

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.


**Files:**
- Create: `LICENSE`, `.gitignore`, `.gitmodules` (via git), `third_party/nlohmann/json.hpp`, `third_party/doctest/doctest.h`, `plugin/extern/CommonLibSSE-NG` (submodule)

**Interfaces:**
- Produces: a git repo at `/home/geoff/projects/games/skyrim/skyrim-python` with the vendored headers and the pinned submodule every later task builds against.

- [ ] **Step 1: Add build rules and the licence**

The repository already exists: it was initialised on 2026-09-30 with the README, `.gitignore` (which already ignores `HANDOFF-*.md`, `build/`, `out/`, `__pycache__/`, `.venv/`, `.pytest_cache/`) and the docs commits, authored as the ByteBard97 noreply identity set in the repo config. Do not re-run `git init`. Append the build-artifact rules:

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
cat >> .gitignore <<'EOF'

# plugin and Papyrus build artifacts
**/build/
*.dll
*.pdb
*.pex
*.egg-info/
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
```

- [ ] **Step 2: Vendor the two single-header libraries**

```bash
mkdir -p third_party/nlohmann third_party/doctest
curl -fsSL -o third_party/nlohmann/json.hpp https://github.com/nlohmann/json/releases/download/v3.11.3/json.hpp
curl -fsSL -o third_party/doctest/doctest.h https://raw.githubusercontent.com/doctest/doctest/v2.4.11/doctest/doctest.h
grep -m1 'version 3.11.3' third_party/nlohmann/json.hpp && grep -m1 'DOCTEST_VERSION_STR "2.4.11"' third_party/doctest/doctest.h
```
Expected: both grep lines print.

- [ ] **Step 3: Add CommonLibSSE-NG as a pinned submodule**

```bash
git submodule add -b ng https://github.com/alandtse/CommonLibSSE-NG plugin/extern/CommonLibSSE-NG
cd plugin/extern/CommonLibSSE-NG
git log -1 --format='%H %cs %s'
grep -n -i -E '1\.7\.99|1\.7\.104|address library.*(5|v5)' CHANGELOG.md | head
grep -n -E 'option\(ENABLE_SKYRIM_(SE|AE|VR)|option\(BUILD_TESTS' CMakeLists.txt
cd ../../..
```
Expected: the CHANGELOG grep shows 1.7.99/1.7.104 support. If it does not, `git -C plugin/extern/CommonLibSSE-NG log --oneline -- CHANGELOG.md | head` and check out the newest commit that does. Record the commit hash and the exact option names printed by the last grep (they are used in Tasks 1 and 15).

- [ ] **Step 4: Commit**

```bash
git add .gitignore LICENSE .gitmodules third_party plugin/extern/CommonLibSSE-NG
git commit -m "chore: bootstrap repo with vendored headers and pinned CommonLibSSE-NG"
```
