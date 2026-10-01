# Task 32: README and milestone tag

Adapted from: branch `plan-out-of-process`, task 20 (`task-20-readme-and-milestone-tag.md`). The README now describes the embedded design: two DLLs, the deployed layout, player scripts, and the dev bridge workflow.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints** and **Fixed paths** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4). Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 31 is done and `spikes/RESULTS.md` has a recorded result (PASS or FAIL with evidence) for every spike S1, S2, S4-S10. M1 is not complete while S5, S6 or S10 is unrecorded (index, Gates).
>
> **Runs on:** Linux (the repository). Step 2 needs the game running with a save loaded, so it is a hand-off to the owner.
>
> **STOP and ask the owner (do not improvise) if:** the full run in Step 2 fails anywhere; the alias or property tests were skipped because the `SPY_S4_*` or `SPY_S7C_*` values are missing (a skip is not a pass); a live test was skipped for lack of `SKYRIM_LIVE=1`; a spike row is missing from `spikes/RESULTS.md`.

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 0-31; the known limits recorded in Tasks 27 and 30; `livetest/results/throughput.json` (Task 31); `spikes/RESULTS.md`.
- Produces: the M1 README and the local annotated tag `m1`.

- [ ] **Step 1: Write the README**

Replace `README.md` with the text below. Fill the two bracketed figures from `livetest/results/throughput.json` (`p50_ms`, and `spin.remote.spin.p99_ms` against `spin.remote.no_spin.p99_ms`) and the S10 line from `spikes/RESULTS.md`; leave no bracket in the committed file.
````markdown
# skyrim-python

Python inside Skyrim (SE/AE): an SKSE plugin that embeds CPython 3.14 and lets Python code call any Papyrus function in the running game, with the same API from a separate process during development. Built as the foundation for [skyrim-dm].

Status: M1 (embedded dispatcher). Verified on Skyrim AE 1.6.1170 under GE-Proton; the plugin does not start Python on any other runtime. Events and save/load arrive in M2, typed bindings in M3, packaging for players in M4.

## How it fits together

- `Data/SKSE/Plugins/SkyrimPython.dll` is a small loader. SKSE loads it; it checks the runtime, loads `SkyrimPython/python/python314.dll` by full path (refusing to share one another mod loaded from elsewhere), then loads `SkyrimPython/SkyrimPythonCore.dll`. Any failure is logged and the game runs on without Python.
- The core starts a host thread once the game's data is loaded. That thread initialises CPython (isolated: no environment variables, no signal handlers), then runs `skyrim._host`, which loads your scripts and, in dev builds, starts the dev bridge. The game's own threads never run Python.
- Python calls reach the game through the built-in `_skyrim` module: each call becomes one task on the game's main thread, dispatched through the Papyrus VM. A blocking call releases the GIL while it waits.

Deployed layout:
```
Data/SKSE/Plugins/SkyrimPython.dll          loader
Data/SKSE/Plugins/SkyrimPython.ini          settings (bridge off in the shipped ini)
Data/SKSE/Plugins/SkyrimPython/
  SkyrimPythonCore.dll                      core (links CPython)
  python/                                   official python-3.14.8-embed-amd64.zip, unmodified
  site-packages/                            vendored win_amd64 / cp314 wheels
  lib/skyrim/                               the skyrim package
  scripts/                                  your scripts
```
Logs: `Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPython.log` (C++) and `.../SKSE/SkyrimPython/python.log` (Python).

## Write a script

A script is a module or package in `scripts/` with `setup(game)` and, optionally, `teardown()`:
```python
import threading

def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()   # setup must return quickly

def run(game):
    game.wait_ready()                        # a save is loaded and no loading screen is up
    player = game.call("Game", "GetPlayer")
    hp = game.call_method(player, "Actor", "GetActorValue", "Health")
    sword = game.form("Skyrim.esm|0x012EB7")
    game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
```
Every Papyrus argument must be passed (no default values until M3). Errors are typed: `skyrim.errors.NotReady`, `TypeMismatch`, `UnknownFunction`, `StaleReference`, `Timeout`, ... An exception in a script is logged with its traceback to `python.log`; other scripts and the game carry on. Third-party packages: vendored win_amd64 / cp314 wheels in `site-packages/` (S10 result: [what spikes/RESULTS.md recorded for httpx, jsonschema, mwparserfromhell]).

## Develop from Linux (the dev bridge)

With the dev ini (`bridge_enabled = true`), the same API works from any process on the machine, so you iterate on Python without restarting the game:
```python
import skyrim

game = skyrim.connect()                  # finds the bridge through SKYRIM_PYTHON_SESSION (source tools/env.sh)
print(game.call("Utility", "RandomInt", 1, 6))
```
The bridge listens on `127.0.0.1` only, checks a token on every connection, and is off in the shipped ini. Code that works through the bridge works unchanged as a script in `scripts/`; run it in-process now and then, because only there does it meet Windows CPython, its vendored wheels and the game's threads.

## Limits to know about
- Papyrus runtime errors are invisible: a script error inside the function you called gives you `None` or a `Timeout`, never a `VmError` (that means only that the VM refused to dispatch). Have the function return a value you can check.
- A paused game (inventory, journal, console open) does not run Papyrus: calls wait and may time out. Use `require_unpaused=True` or a generous timeout.
- `game.batch()` keeps the order of its calls but is neither a transaction nor fast: each step costs 2-3 frames (roughly 15-30 steps per second). For changes that must land together, call one Papyrus function on your own script.
- Property access serves auto properties only: a full property (one with Papyrus get/set code) is refused as `Unsupported`. Reads and writes are not synchronised with Papyrus threads running the same script, and `OnPropertyChanged` does not fire. Call a Papyrus function for anything that must be atomic or must notify.
- Member functions are looked up in a script's empty state only; a function that exists only inside a named state is `UnknownFunction`.
- `game.batch().run()` without a timeout gives the batch 5 s per step in total (capped at one hour); pass `timeout=` for a tighter bound.
- Arrays of objects can be returned but not sent. An array argument holds at most 128 elements (`TypeMismatch`); results are passed through whatever their length.
- Alias references work as call targets and results; alias *arguments* have no live test until M1.5 (no vanilla function takes one).
- Latent calls in flight when the player saves may be dropped by the VM (surfaces as `Timeout`).
- A crash in native code inside the process (the plugin, or a compiled extension in a wheel) takes the game down; pure-Python exceptions do not.
- Script reload (`skyrim._host.reload(name)`) is best effort: state held in C extensions or in threads a script did not stop in `teardown()` survives it.
- Throughput: a call takes about [p50_ms] ms (2-3 frames); a CPU-bound Python thread raises a call's p99 latency from [no-spin p99] to [spin p99] ms. Full figures: `livetest/results/throughput.json`.

## Build and test
- Core and `_skyrim` (Linux): `cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build`; the `_skyrim` module and its tests per `pyglue/` (Task 19); the package: `cd python && uv run pytest`.
- CPython runtime and headers: `tools/fetch_python.sh` (checks SHA-256s against `docs/PINS.md`).
- Plugin (Windows box over ssh), two calls because the script fetches one DLL per call: `tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll`; deploy with `tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --python --package --scripts livetest/scripts` (`--python` and `--scripts` only when the runtime or the scripts changed); launch with `tools/launch_game.sh`.
- Live tests (game running, save loaded): `source tools/env.sh && SKYRIM_LIVE=1 uv run --project python pytest livetest -q -rs`. Without `SKYRIM_LIVE=1` every live test is skipped.
- Protocol: `protocol/PROTOCOL.md`, fixtures in `protocol/v1/fixtures/`. Design: `docs/specs/2026-09-30-skyrim-python-design.md`. Spike results: `spikes/RESULTS.md`.

## Licence
MIT. The embedded CPython runtime is under the PSF licence (`SkyrimPython/python/LICENSE.txt`).

[skyrim-dm]: ../skyrim-dm
````
Check that no placeholder bracket is left:
```bash
grep -n '\[what spikes\|\[p50_ms\]\|\[no-spin\|\[spin p99\]' README.md || echo "README filled in"
```
Expected: `README filled in`.

- [ ] **Step 2: Run everything once more**

```bash
ctest --test-dir core/build --output-on-failure
(cd python && uv run pytest -q)
source tools/env.sh && export SPY_S4_QUEST=0x... SPY_S4_ALIAS=... SPY_S4_REF=0x...   # from spikes/RESULTS.md
export SPY_S7C_QUEST=0x... SPY_S7C_SCRIPT=... SPY_S7C_PROPERTY=... SPY_S7C_KIND=...   # from spikes/RESULTS.md (Task 30)
SKYRIM_LIVE=1 uv run --project python pytest livetest -q -rs --deselect livetest/test_m1_mainmenu.py --deselect livetest/test_m1_throughput.py
git status --short    # clean: nothing in the run rewrote a tracked file
```
(`test_m1_throughput.py` is deselected: Task 31 recorded the baseline that Step 1 copied into the README, and running it again would rewrite `livetest/results/throughput.json` with numbers that disagree with the README. The save loaded must be the one S7 ran on, for the property and alias tests.) Also run the `_skyrim` Linux suite the way Task 19 set it up. Expected: all pass. The skip report (`-rs`) lists only tests that need another setup: the startup-variant tests (Task 26), the low-cap test (run in Task 30 with its own ini) and the two `SPY_MANUAL` tests (run in Task 31). The main-menu tests ran in Tasks 27-29 before a save was loaded. Any skip whose reason is `live test: set SKYRIM_LIVE=1` is a failure. Any skip about missing `SPY_S4_*` or `SPY_S7C_*` values means the alias or property tests did not run: set them and rerun.

- [ ] **Step 3: Commit and tag (local only; never push)**

```bash
git add README.md && git commit -m "docs: README for the M1 embedded dispatcher"
git tag -a m1 -m "M1: embedded CPython, dispatcher, dev bridge, protocol v1"
```
