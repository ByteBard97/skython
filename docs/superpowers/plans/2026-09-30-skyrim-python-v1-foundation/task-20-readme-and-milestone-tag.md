# Task 20: README and milestone tag

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Modify: `README.md`

- [ ] **Step 1: Write the README**

Replace `README.md` with:
````markdown
# skyrim-python

Call any Papyrus function in a running Skyrim (SE/AE) from Python.

`SkyrimPython.dll` (an SKSE plugin) serves a small JSON protocol on `127.0.0.1`; the `skyrim` Python package (stdlib only) is the client. Built as the foundation for [skyrim-dm].

Status: M1 (dispatcher). Verified on Skyrim AE 1.6.1170 under GE-Proton. Events and save/load arrive in M2.

## Use

```python
import skyrim

game = skyrim.connect()                  # finds the session file via SKYRIM_PYTHON_SESSION
game.wait_ready()                        # a save is loaded and no loading screen is up
player = game.call("Game", "GetPlayer")
hp = game.call_method(player, "Actor", "GetActorValue", "Health")
sword = game.form("Skyrim.esm|0x012EB7")
game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
```
Every Papyrus argument must be passed (the server does not fill in default values yet). Errors are typed: `skyrim.NotReady`, `skyrim.TypeMismatch`, `skyrim.UnknownFunction`, `skyrim.StaleReference`, ...

## Install (player)
Copy `SkyrimPython.dll` and `SkyrimPython.ini` to `Data/SKSE/Plugins/`. Set `allow_external_clients = true` in the ini to let a program outside the game connect. The plugin writes `Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPython/session.json` (port and a per-run token) each start.

## Develop
- Core and client tests (Linux): `cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build` and `cd python && uv run pytest`.
- Plugin build (Windows box over ssh): `tools/remote_build.sh plugin SkyrimPython.dll`; deploy with `tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini`; launch with `tools/launch_game.sh`.
- Live tests (game running): `source tools/env.sh && uv run --project python pytest livetest -q`.
- Protocol: `protocol/PROTOCOL.md`, fixtures in `protocol/v1/fixtures/`. Design: `docs/specs/2026-09-30-skyrim-python-design.md`.

## Licence
MIT.

[skyrim-dm]: ../skyrim-dm
````

- [ ] **Step 2: Run everything once more**

```bash
ctest --test-dir core/build --output-on-failure
(cd python && uv run pytest -q)
source tools/env.sh && uv run --project python pytest livetest -q --deselect livetest/test_m1_mainmenu.py
```
Expected: all pass (main-menu tests were run in Task 17 before loading a save).

- [ ] **Step 3: Commit and tag (local only; do not push)**

```bash
git add README.md && git commit -m "docs: README for the M1 dispatcher"
git tag -a m1 -m "M1: dispatcher, protocol v1, sync client"
```
