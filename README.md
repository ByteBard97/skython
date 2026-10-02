# Skython

Python inside Skyrim (SE/AE). Skython is an SKSE plugin that embeds CPython 3.14 and lets Python code call any Papyrus function in the running game. During development the same API also works from a separate process. It is a general-purpose bridge: mods are built on top of it.

Status: M1 (embedded dispatcher). Verified on Skyrim AE 1.6.1170, Windows-native. Proton (the game on Linux) has not been tested yet. The plugin does not start Python on any other runtime. Events and save/load arrive in M2, typed bindings in M3, packaging for players in M4.

## How it fits together

- `Data/SKSE/Plugins/Skython.dll` is a small loader. SKSE loads it. It checks the runtime, loads `Skython/python/python314.dll` by full path (it refuses to share a `python314.dll` that another mod loaded from somewhere else), then loads `Skython/SkythonCore.dll`. Any failure is logged and the game runs on without Python.
- The core starts a host thread once the game's data is loaded. That thread initialises CPython (isolated: no environment variables, no signal handlers), then runs `skython._host`, which loads your scripts and, in dev builds, starts the dev bridge. The game's own threads never run Python.
- Python calls reach the game through the built-in `_skython` module. Each call becomes one SKSE task (the game runs these once per frame), plus at most one more to convert an object result, and is dispatched through the Papyrus VM. A blocking call releases the GIL while it waits.

## Install

There is no packaged release yet (that is M4), and no PyPI package: the `skython` Python package ships inside the mod, in `lib/skython/`. You need SKSE64 (tested with 2.2.6) and Address Library for SKSE Plugins. Build the two DLLs (see Build and test), then lay the files out like this:
```
Data/SKSE/Plugins/Skython.dll          loader
Data/SKSE/Plugins/Skython.ini          settings (bridge off in the shipped ini)
Data/SKSE/Plugins/Skython/
  SkythonCore.dll                      core (links CPython)
  python/                              the official python-3.14.8-embed-amd64.zip, unmodified
  site-packages/                       vendored win_amd64 / cp314 wheels
  lib/skython/                         the skython package
  scripts/                             your scripts
```
Logs: `Documents/My Games/Skyrim Special Edition/SKSE/Skython.log` (C++) and `.../SKSE/Skython/python.log` (Python).

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
Scripts load when the game's data is loaded, before any save, so game work belongs on a thread that calls `game.wait_ready()` first.

Pass every Papyrus argument, positionally, including the ones Papyrus lets you leave out (no default values until M3). Errors are typed: `skython.errors.NotReady`, `TypeMismatch`, `UnknownFunction`, `StaleReference`, `Timeout`, and so on. An exception in a script is logged with its traceback to `python.log`; other scripts and the game carry on.

Third-party packages go in `site-packages/` as vendored win_amd64 / cp314 wheels. Tested in the embedded interpreter: httpx (including an HTTP request), jsonschema (with its compiled `rpds` extension) and mwparserfromhell. mwparserfromhell has no cp314 wheel upstream, so it was built from its source distribution as a pure-Python wheel.

## Examples

See [`examples/`](examples/): Twitch chat spawning named mudcrabs, talking to NPCs through a language model, a live stats overlay for OBS, and real-world weather sync, each in one Python file.

## Develop from outside the game (the dev bridge)

With the dev ini (`bridge_enabled = true`), the same API works from any process on the game's machine, so you can iterate on Python without restarting the game:
```python
import skython

game = skython.connect()                  # set SKYTHON_SESSION to the bridge's session.json first
print(game.call("Utility", "RandomInt", 1, 6))
```
The bridge writes `Documents/My Games/Skyrim Special Edition/SKSE/Skython/session.json` (port and a token that is new each game start). Point `SKYTHON_SESSION` at that file, or pass `port=` and `token=` to `connect()`. The bridge listens on `127.0.0.1` only, checks the token on every connection, and is off in the shipped ini. To use it from another machine, forward the port over ssh (this project's own tests reach the Windows test machine from Linux that way).

Code that works through the bridge works unchanged as a script in `scripts/`. Run it in-process now and then anyway, because only there does it meet Windows CPython, its vendored wheels and the game's threads.

## Limits to know about

- **Call cost.** A call takes about one SKSE task drain: ~46 ms in-process on the test machine (p50 46.1 ms, p99 47.8 ms), ~92 ms over the dev bridge (p50 92.4 ms, p99 100.3 ms; the bridge costs a second drain, cause not yet measured). Calls from several threads overlap, so throughput grows with concurrency. A CPU-bound Python thread adds about one drain per call: in-process p50 went from 46.1 to 92.2 ms with one spinning; over the bridge p99 went from 99.2 to 117.5 ms. Pace heavy Python work (sleep between batches). Full figures: `livetest/results/throughput.json`.
- **Batches are ordered, not atomic, and not fast.** `game.batch()` keeps the order of its calls, but each step is its own SKSE task: 46.6 ms per step in the M1 measurement. Another script's call can run between two steps. For changes that must land together, call one Papyrus function on your own script.
- `game.batch().run()` without a timeout gives the batch 5 s per step in total (capped at one hour); pass `timeout=` for a tighter bound.
- **Arguments are positional and every Papyrus parameter is required.** The plugin fills in no default values. Errors name a native function's parameters `param1..N` (the engine reports no `.psc` names for natives), so read the position in the message; script-defined functions report their real parameter names.
- Papyrus runtime errors are invisible: a script error inside the function you called gives you `None` or a `Timeout`, never a `VmError` (that only means the VM refused to dispatch). Have the function return a value you can check.
- A paused game (inventory, journal, console open) does not run Papyrus: calls wait and may time out. Use `require_unpaused=True` or a generous timeout.
- **Properties.** Auto properties only; full properties (with Papyrus get/set code) are `Unsupported`: call a Papyrus function instead. Reads go through the engine's `GetVariableValue` (whether it takes a lock is unverified). Writes work for Int, Float and Bool properties only. They take no lock (nor does the fallback read used for those three types if the engine read fails), so the worst case is a torn or stale value if Papyrus writes the same property at the same moment. Writes to String, Object (form or alias) and Array properties are refused (`Unsupported`): call a Papyrus setter with `call_method`. No write fires an OnPropertyChanged-style event.
- Member functions are looked up in a script's empty state only; a function that exists only inside a named state is `UnknownFunction`.
- **Aliases.** Only scripted aliases (those carrying a `ReferenceAlias` script) work as call targets and results; a plain alias is `TypeMismatch`. Alias arguments have no live test yet (no vanilla function takes one). An alias whose id is 0xFFFF would decode as its quest (form handles carry 0xFFFF in the high 32 bits; one quest sampled).
- Arrays of objects can be returned but not sent. An array argument holds at most 128 elements (`TypeMismatch`); results are passed through whatever their length.
- Strings returned by the game come back in the casing the engine's string pool already holds (for example `CreateStringArray(2, "x")` gave `['X', 'X']`). Compare game strings case-insensitively.
- A call whose VM callback never fires leaks about 100 bytes.
- A call in flight when the player saves is not written into the save: after loading that save the call ends `NotReady` or `Timeout` and never completes.
- A crash in native code inside the process (the plugin, or a compiled extension in a wheel) takes the game down; pure-Python exceptions do not.
- Script reload (`skython._host.reload(name)`) is best effort: state held in C extensions survives it, and threads a script did not stop in `teardown()` keep running, but their `Game` is closed (calls raise `ConnectionLost`).
- Not in M1: events, and loading or saving the game from Python (`game.load` / `game.save`). Both come in M2.

## Build and test

- Core (Linux): `cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build`.
- The `_skython` module over a fake backend (Linux): `cmake -S pyglue/linux -B pyglue/build -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$(uv python find 3.14)" && cmake --build pyglue/build -j`, then `uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests`.
- The `skython` package (Python 3.12 and 3.14): `cd python && uv run pytest`.
- CPython runtime and headers: `tools/fetch_python.sh` (checks SHA-256s against `docs/PINS.md`).
- Plugin (built on a Windows machine over ssh), one DLL per call: `tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll`.
- On the project's Windows test machine: deploy with `tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini`, then `tools/box/box_deploy.sh --package` (and `--python`, `--scripts livetest/scripts` when the runtime or the scripts changed); launch with `tools/box/box_launch.sh`; stop with `tools/box/box_stop.sh` (the test machine's game hangs on `qqq` exit with or without Skython, so the tools end it instead).
- Live tests (game running on the test machine, save loaded): `tools/box/box_session.sh up && source out/box-live.env && SKYTHON_LIVE=1 uv run --project python pytest livetest -q -rs` (an ssh tunnel, loopback on both ends; the session file with the token stays in `out/`). Without `SKYTHON_LIVE=1` every live test is skipped.
- Protocol: `protocol/PROTOCOL.md`, fixtures in `protocol/v1/fixtures/`. Design: `docs/specs/2026-09-30-skyrim-python-design.md`. Spike results: `spikes/RESULTS.md`.

## Licence

MIT (`LICENSE`). The embedded CPython runtime is under the PSF licence, bundled as `Skython/python/LICENSE.txt`.
