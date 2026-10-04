# Skython

Python inside Skyrim SE 1.6.1170. Skython is an SKSE plugin that embeds CPython 3.14 and lets Python code call any Papyrus function in the running game. During development the same API also works from a separate process. It's a library for mod authors; it does nothing in game on its own.

Status: early. Calling Papyrus from Python works, events work, and saving and loading the game from Python works. I've only verified Skyrim SE 1.6.1170, Windows-native. The loader also accepts 1.7.104, but I haven't run that version against a live game yet. On any other version the plugin loads but leaves Python off. I haven't tried Proton.

## How it fits together

- `Data/SKSE/Plugins/Skython.dll` is a small loader. SKSE loads it. It checks the runtime, loads `Skython/python/python314.dll` by full path (it refuses to share a `python314.dll` that another mod loaded from somewhere else), then loads `Skython/SkythonCore.dll`. Any failure is logged and the game runs on without Python.
- The core starts a host thread once the game's data is loaded. That thread starts CPython in isolated mode, so `PYTHON*` env vars and the user site-packages are ignored (`os.environ` still works). Then it runs `skython._host`, which loads your scripts and, if `bridge_enabled` is set in `Skython.ini`, the dev bridge. The game's own threads never run Python.
- Python calls reach the game through the built-in `_skython` module. Each call is one SKSE task (two if it returns an object), dispatched through the Papyrus VM. A blocking call releases the GIL while it waits.

## Install

Build from source on Windows with [docs/BUILDING.md](docs/BUILDING.md), then install into a game folder with `tools\install.ps1 <game folder>`. You need SKSE64 (tested with 2.2.6) and Address Library for SKSE Plugins, both installed by you from Nexus; Skython never bundles them. `install.ps1` checks that both are present for your game version before it copies anything, and it never overwrites a file it didn't install.

There is no packaged release and no PyPI package yet. The `skython` Python package ships inside the mod, in `lib/skython/`. The installed layout:

```
Data/SKSE/Plugins/Skython.dll          loader                  out/Skython.dll from the build
Data/SKSE/Plugins/Skython.ini          settings                plugin/config/Skython.ini (bridge off)
Data/SKSE/Plugins/Skython/
  SkythonCore.dll                      core (links CPython)    out/SkythonCore.dll from the build
  python/                              CPython runtime         third_party/cpython/embed/ after tools/fetch_python.sh
                                                               (the official python-3.14.8-embed-amd64.zip, unmodified)
  site-packages/                       third-party packages    vendored win_amd64 / cp314 wheels, unpacked
  lib/skython/                         the skython package     python/src/skython/
  scripts/                             your scripts
Data/Scripts/Skython.pex               Papyrus side of the API (Skython.Send)
Data/Source/Scripts/Skython.psc        its source, to compile your own scripts against
```
For the dev bridge use `plugin/config/Skython.dev.ini` as `Skython.ini` instead.
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

Pass every Papyrus argument, positionally, including the ones Papyrus lets you leave out; the plugin fills in no default values. Errors are typed: `skython.errors.NotReady`, `TypeMismatch`, `UnknownFunction`, `StaleReference`, `Timeout`, and so on. An exception in a script is logged with its traceback to `python.log`, and with `console_echo_errors = true` (the default) one short line also shows in the in-game console. Other scripts and the game carry on.

A script can have a settings file, `scripts/<name>.toml` or `scripts/<name>/settings.toml`, which is read when the script loads and exposed as `game.settings`. It's read-only: tables are mappings and arrays are tuples.

Third-party packages go in `site-packages/` as vendored win_amd64 / cp314 wheels. Tested in the embedded interpreter: httpx (including an HTTP request), jsonschema (with its compiled `rpds` extension) and mwparserfromhell. mwparserfromhell has no cp314 wheel upstream, so I built it from its source distribution as a pure-Python wheel.

## Events

`game.on(event, filter, fn)` subscribes to a game event and runs `fn(event)` on the script's event thread. There are helpers for the common ones: `game.on_death`, `game.on_quest_stage`, `game.on_channel` and a few more. The events are death, dying, hit, equip, unequip, activate, container_changed, cell_attach, cell_detach, quest_start, quest_stage, quest_stop, player_location, menu_open, menu_close, mod_event, channel, game_loaded, game_saved and new_game; the fields of each are in [protocol/PROTOCOL.generated.md](protocol/PROTOCOL.generated.md).

Each subscription has a bounded queue. If your callback falls behind, the oldest events are dropped and the next delivered event says how many. Your own Papyrus can raise an event too: `Skython.Send("my_mod", payload)` arrives as a `channel` event (see `game.on_channel`).

## Save and load from Python

`game.save(name)` saves the game (letters, digits and underscores, at most 64 characters). It never overwrites; an existing name raises `BadRequest`. `game.load(name)` loads a save, works at the main menu, and by default waits until the game is interactive again. A load is a world reset: every call still in flight ends `NotReady`, and event subscriptions survive.

## Examples

See [`examples/`](examples/): Twitch chat spawning named mudcrabs, talking to NPCs through a language model, a live stats overlay for OBS, real-world weather sync, a kill tracker on the death event, Papyrus reporting to Python, and a quest stage notifier, each in one Python file.

## Develop from outside the game (the dev bridge)

With the dev ini (`bridge_enabled = true`), the same API works from any process on the game's machine, so you can iterate on Python without restarting the game. On the development machine, install the package from this repository with `pip install ./python` (or `uv pip install ./python`); it is not on PyPI.
```python
import skython

game = skython.connect()                  # set SKYTHON_SESSION to the bridge's session.json first
print(game.call("Utility", "RandomInt", 1, 6))
```
The bridge writes `Documents/My Games/Skyrim Special Edition/SKSE/Skython/session.json` (port and a token that is new each game start). Point `SKYTHON_SESSION` at that file, or pass `port=` and `token=` to `connect()`. The bridge listens on `127.0.0.1` only, checks the token on every connection, and is off in the shipped ini. To use it from another machine, forward the port over ssh.

Code that works through the bridge works unchanged as a script in `scripts/`. Still test it in game before you ship; that's the only place it runs on Windows CPython with the real wheels and the game's threads.

## Limits to know about

### Call cost

A call takes about one SKSE task drain: ~46 ms in-process on the test machine (p50 46.1 ms, p99 47.8 ms), ~92 ms over the dev bridge (p50 92.4 ms, p99 100.3 ms). The bridge costs a second drain; I haven't measured why. Calls from several threads overlap, so throughput grows with concurrency. A CPU-bound Python thread adds about one drain per call (in-process p50 went from 46.1 to 92.2 ms with one spinning; over the bridge p99 went from 99.2 to 117.5 ms). Pace heavy Python work: sleep between batches. Full figures: `livetest/results/throughput.json`.

### Calls

- Arguments are positional and every Papyrus parameter is required; the plugin fills in no default values. Errors name a native function's parameters `param1..N` (the engine reports no `.psc` names for natives), so read the position in the message. Script-defined functions report their real parameter names.
- Papyrus runtime errors are invisible. A script error inside the function you called gives you `None` or a `Timeout`, never a `VmError` (that only means the VM refused to dispatch). Have the function return a value you can check.
- A paused game (inventory, journal, console open) does not run Papyrus, so calls wait and may time out. Use `require_unpaused=True` or a generous timeout.
- Member functions are looked up in a script's empty state only; a function that exists only inside a named state is `UnknownFunction`.
- A call whose VM callback never fires leaks about 100 bytes.
- A crash in native code inside the process (the plugin, or a compiled extension in a wheel) takes the game down. Pure-Python exceptions do not.

### Batches

- `game.batch()` keeps the order of its calls, but each step is its own SKSE task (46.6 ms per step measured), and another script's call can run between two steps. It isn't a transaction. For changes that must land together, call one Papyrus function on your own script.
- `run()` without a timeout gives the batch 5 s per step in total, capped at one hour. Pass `timeout=` for a tighter bound.

### Properties

- Only auto properties work. Full properties (with Papyrus get/set code) are `Unsupported`; call a Papyrus function instead.
- Reads use the engine's `GetVariableValue`; I don't know if it takes a lock. Writes work for Int, Float and Bool only and take no lock, so a write racing a Papyrus write can leave a stale value.
- Writes to String, Object and Array properties are refused (`Unsupported`). Call a Papyrus setter with `call_method`. No write fires a property-changed event.

### Aliases

- Only scripted aliases (those carrying a `ReferenceAlias` or `LocationAlias` script) work as call targets and results. A plain quest alias, one with no script attached, can't be driven; it's `TypeMismatch`.
- Known edge: an alias whose id is 0xFFFF decodes as its quest.

### Arrays and strings

- Arrays of objects can be returned but not sent. An array argument holds at most 128 elements (`TypeMismatch`); results pass through whatever their length.
- Strings returned by the game come back in the casing the engine's string pool already holds (`CreateStringArray(2, "x")` gave `['X', 'X']`). Compare game strings case-insensitively.

### Saves

- Calls bind forms into the save file. Binding a form so the VM can dispatch on it adds an attachedScripts entry, and that binding persists in saves. It's engine behavior, not something the plugin can turn off.
- A call in flight when the player saves is not written into the save. After loading that save the call ends `NotReady` or `Timeout` and never completes.

### Reloading

- Script reload (`skython._host.reload(name)`) is best effort. The script's modules are imported again and its settings file is re-read, but C extension state survives it, and any thread you didn't stop in `teardown()` keeps running with a closed `Game` (its calls raise `ConnectionLost`).

### The bundled test ESP

- `testesp/SkythonTest.esp` holds the test cell the live tests use. Entering that cell from an exterior cell crashes the game (a null light while the departing exterior is drawn). Entering it from the main menu or another interior works fine.

## Build and test

Building on Windows: [docs/BUILDING.md](docs/BUILDING.md).

- Core (Linux): `cmake -S core -B core/build && cmake --build core/build -j && ctest --test-dir core/build`.
- The `_skython` module over a fake backend (Linux): `cmake -S pyglue/linux -B pyglue/build -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$(uv python find 3.14)" && cmake --build pyglue/build -j`, then `uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests`.
- The `skython` package (Python 3.12 and 3.14): `cd python && uv run pytest`.
- CPython runtime and headers: `tools/fetch_python.sh` (checks SHA-256s against `docs/PINS.md`).
- Plugin (Windows): `plugin\build.ps1` builds both DLLs, fetches CPython and stages an install folder. I build over ssh with `tools/remote_build.sh`, which syncs the repository to a Windows machine and runs `build.ps1` there.
- Live tests: the tests in `livetest/` need a running game with the dev bridge on and the test ESP installed, and they are skipped unless `SKYTHON_LIVE=1` is set.
- Protocol: `protocol/PROTOCOL.md`, fixtures in `protocol/v1/fixtures/`.

## Licence

The source in this repository is MIT (`LICENSE`).

### Third-party software

Vendored in this repository:

| Component | Where | Used in | Licence |
|---|---|---|---|
| CommonLibSSE-NG 10.1.0 (commit `39f9d07`) | `plugin/extern/CommonLibSSE-NG` (git submodule) | statically linked into both DLLs | GPL-3.0-or-later with the Modding Exception and GPL-3.0 Linking Exception in its `EXCEPTIONS.md` (its `COPYING.txt`, `README.md`, `vcpkg.json`; earlier versions were MIT) |
| pybind11 3.1.0 | `third_party/pybind11` (headers) | `SkythonCore.dll`, the Linux test module | BSD-3-Clause (`third_party/pybind11/LICENSE`) |
| nlohmann/json 3.11.3 | `third_party/nlohmann/json.hpp` | `SkythonCore.dll`, core tests | MIT (`third_party/nlohmann/LICENSE.MIT`) |
| doctest 2.4.11 | `third_party/doctest/doctest.h` | core tests only, never shipped | MIT (`third_party/doctest/LICENSE.txt`) |

Fetched at build time:

| Component | Where | Used in | Licence |
|---|---|---|---|
| CPython 3.14.8 embeddable runtime and NuGet headers | `tools/fetch_python.sh` into `third_party/cpython/` (gitignored, SHA-256s in `docs/PINS.md`) | bundled with the mod as `Skython/python/` | PSF License Agreement, shipped as `Skython/python/LICENSE.txt` (that file also covers components the Windows build bundles, such as libffi and Zstandard) |
| spdlog | vcpkg (`plugin/vcpkg.json`, CommonLibSSE-NG) | both DLLs | MIT |
| fmt | vcpkg (CommonLibSSE-NG, spdlog) | both DLLs | MIT |
| rapidcsv | vcpkg (`plugin/vcpkg.json`, CommonLibSSE-NG) | CommonLibSSE-NG | BSD-3-Clause |
| DirectXTK, DirectXMath | vcpkg (`plugin/vcpkg.json`, CommonLibSSE-NG) | CommonLibSSE-NG | MIT |
| SimpleIni | vcpkg (CommonLibSSE-NG) | CommonLibSSE-NG, where it uses it | MIT |
| toml11 | vcpkg (CommonLibSSE-NG) | CommonLibSSE-NG, where it uses it | MIT |
| xbyak | vcpkg (CommonLibSSE-NG) | CommonLibSSE-NG, where it uses it | BSD-3-Clause |
| nlohmann-json (vcpkg copy) | vcpkg (CommonLibSSE-NG) | CommonLibSSE-NG, where it uses it | MIT |

The vcpkg licences are the upstream projects' licences; vcpkg installs each package's licence file under `share/<package>/copyright`.

Both DLLs statically link CommonLibSSE-NG, which is GPL-3.0-or-later with a modding exception. I haven't worked out yet what that means for distributing the built DLLs, so I'm not calling the compiled plugin MIT. The source is MIT.
