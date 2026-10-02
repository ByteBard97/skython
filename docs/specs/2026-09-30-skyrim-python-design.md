# Skython: design spec

Date: 2026-10-01. Status: draft 4. Supersedes draft 3 (2026-09-30), whose out-of-process design is kept on the git branch `plan-out-of-process` as the fallback if spike S8 fails.

## Changes from draft 3

1. **CPython is embedded in the plugin.** The owner's goal is a mod that ships Python code and runs it inside Skyrim, not beside it. Draft 3 listed embedding as a non-goal and ran every consumer as a separate process; that contradicted the goal and the Q3 research recommendation (option A). Draft 4 reverses it.
2. The C++ RPC server, framing, handshake and session file are gone from the plugin. The plugin exposes a built-in extension module, `_skython`, to the embedded interpreter. Python code calls the game through it directly.
3. **Development keeps a separate, restartable process.** A dev bridge, written in Python and running inside the embedded interpreter, serves the same `skython` API over loopback TCP. skyrim-dm and the live tests run on Linux against it during development and move in-process when they ship. The bridge is off in player builds. It is a protocol (framing, handshake, typed errors, references), so it is real work, but it is Python, testable on Linux end to end.
4. **Threading contract:** the game's main thread and the VM's threads never run Python. Python runs on threads the plugin owns. This is what keeps long Python work (skyrim-dm's 66-second contract generation) from ever stalling a frame.
5. Third-party packages are allowed in the embedded interpreter (vendored Windows wheels), because skyrim-dm needs them. Draft 3's "stdlib only" rule for the child host is gone with the child host.
6. New spikes: S8 (CPython initialises inside the game under GE-Proton and a Linux process reaches a Python socket inside it; absorbs S1), S9 (a Python thread drives VM calls through `AddTask` and a CPU-bound Python thread leaves frame time alone), S10 (skyrim-dm's dependencies install and import in the embedded interpreter).
7. Pinned: CPython **3.14.8** (standard GIL build), runtime from the official Windows embeddable zip, headers and import library from the `python` NuGet package of the same version; **pybind11 3.1.0** for the extension module.
8. **Two DLLs** (4.1): a loader with no Python dependency, and a core that links CPython, because `python314.dll` cannot be delay-loaded (it exports data symbols) and is not on the game's DLL search path.
9. **One request format in both modes:** request and response JSON text crosses the `_skython` boundary, so the C++ request parser and response builders serve the in-process path and the bridge alike (4.2).

## 1. What we are building

A Skyrim SE/AE library that lets Python code read and change the running game through an API that feels native, in the way KiCad exposes scripting. It has three parts:

1. **`Skython.dll`**, an SKSE plugin (C++, CommonLibSSE-NG), in two files: a small loader that SKSE loads (`Data/SKSE/Plugins/Skython.dll`) and the core (`Data/SKSE/Plugins/Skython/SkythonCore.dll`), which links CPython. Together they embed CPython, exposes the built-in module `_skython` to it, calls any Papyrus function by name through the Papyrus VM, runs a short list of native operations, pushes game events to Python, and does game work on the game thread.
2. **`skython`**, a pure-Python package on top of `_skython`: typed game classes, references, errors, events. The same API is available from a separate process through the dev bridge (`skython.bridge` in the game, `skython.remote` on the other side).
3. **`pscgen`**, a build-time generator that reads Papyrus `.psc` headers (vanilla, SKSE, PO3) and writes typed bindings, `.pyi` stubs and a JSON signature table.

**Consumer modes:**
- **In-process script** (the shipping mode): a Python package in `Data/SKSE/Plugins/Skython/scripts/`, loaded by the embedded interpreter at startup. skyrim-dm ships this way.
- **External process through the dev bridge** (the development mode): skyrim-dm, live tests and tools on the Linux host (or Windows) connect over loopback and use the identical API. Restarting them never restarts the game.

**skyrim-dm is the first consumer.** It will depend on the `skython` package to deliver contracts (start a shell quest, fill aliases, set objective text, place items, write consequences) and to learn when a contract completes or fails. Its delivery interface (`executor/game_client.py`, `GameClient`) gets a `skython`-backed implementation.

## 2. Goals and non-goals

**Goals (v1)**
- G1. Call any Papyrus function (global or member, vanilla, SKSE or any installed mod) from Python by name, with typed arguments and return values, including latent functions such as `Utility.Wait`, on forms and on quest aliases.
- G2. Typed Python bindings for vanilla, SKSE and PO3, generated rather than hand-written, with IDE autocomplete and client-side argument checking and defaults.
- G3. Subscribe to game events (list in 4.6) with filters applied in C++, plus a Papyrus-to-Python channel for events a mod's own scripts raise.
- G4. Never crash the game because of a bad call, a stale reference, a call during a load screen, an exception in Python code, or a dev-bridge client that disconnects. A crash in native code inside the process (our C++ glue, or a compiled third-party extension) can still take the game down; that is the accepted cost of embedding, so the glue is kept small and goes through pybind11.
- G5. Work under GE-Proton on Linux and on Windows. In development, external clients run natively on the Linux host through the dev bridge.
- G6. One DLL **built for** SE 1.5.97, AE 1.6.1170 and AE 1.7.x (CommonLibSSE-NG multi-target). It is **verified on** 1.6.1170 only until the others have passed the live tests; release notes list only verified runtimes.
- G7. Player scripts run inside the game: the plugin loads script packages from a folder, each with `setup(game)` and an optional `teardown()`. Third-party packages are allowed as vendored wheels for win_amd64 / cp314.
- G8. A live-test harness that drives the real game through the dev bridge, able to replace DevBench for skyrim-dm and Chronicle testing, including save and load.

**Non-goals**
- Running Python on the game's main thread or the VM's threads, or per-frame Python hooks.
- Restarting or finalising the interpreter while the game runs. There is no `Py_Finalize`; the process exit ends it.
- Wrapping CommonLibSSE-NG classes wholesale, or calling engine functions by raw address.
- Creating quests, dialogue topics, INFO records, books or journal log text at runtime (the engine cannot). skyrim-dm pre-authors these in an ESP.
- `ActiveMagicEffect` and other non-form script objects besides aliases.
- VR, Fallout 4, Starfield. An MCP server (a thin Python adapter later). Any network access beyond loopback, any auto-update.
- A free-threaded (no-GIL) CPython build.

## 3. Constraints and facts this design rests on

- Target runtime: AE 1.6.1170, SKSE 2.2.6, Address Library. 1.7.99 (2026-08-20) and 1.7.104 (2026-08-27) changed class layouts and moved Address Library to a new file format; old CommonLibSSE-NG builds all broke and needed a recompile against a CommonLibSSE-NG that knows the new format.
- CommonLibSSE-NG: **alandtse/CommonLibSSE-NG, branch `ng`**, pinned submodule, consumed with `add_subdirectory`. Verified on the build box 2026-09-30 at commit `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (v10.1.0); real signatures are recorded in the plan's `API-VERIFICATION.md`. The copy vendored in skyrim-re-toolkit is too old and must not be used. Other C++ dependencies through classic vcpkg (manifest mode).
- **CPython 3.14.8**, standard (GIL) build. Runtime: the unmodified official `python-3.14.8-embed-amd64.zip` (python.org), unpacked to `Data/SKSE/Plugins/Skython/python/`. Build inputs: `include/` and `libs/python314.lib` from the `python` 3.14.8 NuGet package (the embeddable zip has neither). Linux-side builds and tests use uv's CPython 3.14.
- **pybind11 3.1.0** (header-only) for `_skython`; one module source builds as a Linux extension module (tests) and as a built-in module inside the plugin (`PyImport_AppendInittab`).
- Development: Linux host. C++ for the plugin is built only on the Windows box (192.168.0.211, VS2022 v143) over SSH; its default SSH shell is Windows PowerShell 5.1. The game runs under GE-Proton (`~/Games/SimpleSkyrim`, launched with `skse64_loader.exe` directly, not through MO2).
- Transport (dev bridge only): Wine's loopback is the host's loopback. Verified outbound (ChronicleBridge connects to a Linux listener); inbound (a Linux process connecting to a socket bound inside Wine) is expected to work and is checked by S8.
- skyrim-dm is synchronous Python ≥3.12 (blocking httpx, sqlite; no asyncio), with third-party dependencies (`httpx`, `jsonschema`, `mwparserfromhell`; `mcp` only for its MCP server, not needed in game). It names game objects by form key `Plugin.esp|0xLocalID`. Its engine needs (from its docs): quest start/stage/objective display, alias `ForceRefTo`, PO3 `SetObjectiveText`, `Game.GetFormFromFile`, alive/dead checks, inventory transfer, `SetName`, relationship rank, gold, game time; events for death, quest stage, location change, items added/removed, and dialogue-driven completion.
- Precedents: JContainers (Lua embedded in an SKSE plugin, shipped for years) and SkyrimPlatform (ChakraCore embedded, hot reload) prove in-process scripting hosts ship to players; no CPython-in-process SKSE mod has shipped (SkyrimPy was abandoned, Heart of Magic runs Python as a child process). ChronicleBridge (AGPL-3.0, ours) for `AddTask` + promise hand-off and the `kPostLoadGame` packed-value crash; IntelEngine-NativePlugin `AsyncDispatch.cpp` (Apache-2.0) for `MakeFunctionArguments` + `DispatchMethodCall`; KiCad for the scripting-API shape.
- Header sources: SKSE (62 files) and PO3 under `~/Games/*/mods/...`; a vanilla replacement set (UOSC, 503 files); full vanilla sources from the CK `Scripts.zip` / `Skyrim - Misc.bsa`.

## 4. Architecture

```
 ┌──────────────────────── Skyrim process (Windows or Proton) ─────────────────────────┐
 │ Skython.dll                                                                    │
 │                                                                                     │
 │  Python threads (GIL)               C++ (no Python)                                 │
 │  ┌────────────────────────┐ submit  ┌────────────┐ AddTask ┌──────────────────────┐ │
 │  │ host thread: init,     ├────────►│ Dispatcher ├────────►│ main thread: resolve │ │
 │  │ load scripts, bridge   │  _skython│ (requests, │         │ refs, check types,   │ │
 │  │ player scripts' threads│◄────────┤  pendings) │◄────────┤ Dispatch*Call        │ │
 │  │ event dispatch thread  │ result  └────────────┘ VM cb   └──────────────────────┘ │
 │  │                        │◄──── event queues ◄── sinks / ModCallback / Send        │
 │  └──────────┬─────────────┘                                                         │
 │             │ dev bridge (Python, off in player builds)                             │
 └─────────────┼───────────────────────────────────────────────────────────────────────┘
               │ TCP 127.0.0.1:45977, token
       ┌───────┴──────────────────────┐
       │ external process (Linux host)│
       │ skyrim-dm, live tests, tools │
       │ `skython.remote` (same API)   │
       └──────────────────────────────┘
```

### 4.1 Plugin lifecycle
- **Two DLLs.** `python314.dll` exports data symbols (`_Py_NoneStruct`, `PyExc_*`, type objects), so MSVC cannot delay-load it, and a normally linked plugin would need it on the game's DLL search path. So the SKSE-facing **loader** (`Skython.dll`, no Python headers) does, in `SKSEPlugin_Load`: refuse an unsupported runtime; check `GetModuleHandleW(L"python314.dll")` (another module of that name from a different path disables Python); `LoadLibraryExW` the plugin's own `python/python314.dll` by full path (with `LOAD_WITH_ALTERED_SEARCH_PATH`); `LoadLibraryExW` `Skython/SkythonCore.dll` by full path; and call its exported `SpyCore_Load(const SKSE::LoadInterface*)`. When Windows resolves the core's import of `python314.dll`, the module already loaded under that name is used. Any failure is logged and the loader returns `true` with Python disabled (the game is unaffected).
- `SpyCore_Load` (core): `SKSE::Init`, register the messaging listener, read the ini, register the native Papyrus function `Skython.Send`. No work in `DllMain`; CPython is loaded but not initialised here.
- `kDataLoaded`: forms and the VM are ready. Register event sinks. Mark `data_ready`. Start the **host thread** (4.5), which loads `python314.dll` from the plugin's own folder, initialises CPython and imports `skython._host`.
- `kPreLoadGame`: mark `loading`. `kPostLoadGame` / `kNewGame`: bump the **session epoch**. (`kPostLoadGame`'s data is a packed value, not a pointer.)
- **Interactive signal:** after `kPostLoadGame` or `kNewGame`, the game is "interactive" when the `Loading Menu` closes (`MenuOpenCloseEvent`, opening = false). Only then do game calls run and does the `game.loaded` event fire. Before `kDataLoaded`, and between `kPreLoadGame` and that signal, game calls fail at once with `NotReady(reason="loading")`. At the main menu (no game loaded), calls that need a game fail with `NotReady(reason="no_game")`; `game.state()` and `game.load()` still work.
- **Paused and menus:** a loaded game that is paused or has a menu open is not "not ready": most calls work. Papyrus scripts, latent functions and AI do not advance while paused, so such calls wait and may time out. `game.state()` and every `NotReady`/`Timeout` report `paused` and the menu stack; a per-call `require_unpaused=True` fails at once with `NotReady(reason="paused")` instead of waiting.
- **Python startup failures** (the DLL is missing, another module named `python314.dll` from a different path is already loaded, `Py_InitializeFromConfig` fails, `skython._host` fails to import) are logged and leave the game running with Python disabled. They never crash or block the game.

### 4.2 The `_skython` module and the dev bridge
- `_skython` is the only boundary between Python and the game. It is small: submit a request (JSON text), wait for its response (JSON text) with the GIL released, cancel, read the lifecycle state, read the plugin's settings, write to the plugin log, and (M2) read event batches. No Python object ever crosses into C++ game code, and no engine pointer ever reaches Python.
- `_skython` reports which backend it is running on (`BACKEND == "game"` in the plugin, `"fake"` in the Linux test build). `skython.connect()` returns an in-process game when `_skython.BACKEND == "game"` and a remote one otherwise.
- **Dev bridge** (`skython.bridge`, pure Python, runs on its own thread in the game): TCP bound to `127.0.0.1` only, port from the ini (default **45977**; `0` = pick a free port), non-loopback peers refused. Framing: 4-byte little-endian length, then a UTF-8 JSON object; maximum frame 16 MiB. Handshake: `hello {protocol, token, client}` answered by `welcome {protocol, plugin_version, game_version, runtime, epoch}` or `handshake_error {code, message}`. Requests `{id, op, args}`, responses `{id, ok, result | error}`, pushed events `{event, sub, seq, epoch, data}`; requests are pipelined. Exact shapes and golden fixtures live in `protocol/`; `skython.bridge` and `skython.remote` are both tested against them.
- **Token and discovery:** the bridge generates a random 128-bit token at start (or uses a fixed one from the dev ini) and writes `{port, token, pid, plugin_version, protocol}` to a session file (default `Documents/My Games/<game>/SKSE/Skython/session.json` inside the game's prefix). `skython.remote` finds it through an explicit argument, then `SKYTHON_SESSION`, then a fixed port+token from its own config. No guessing of Proton prefix paths.
- **The bridge is off in player builds** (`bridge_enabled = false` in the shipped ini, `true` in the dev ini). When off, the plugin opens no port.
- Errors are typed and identical in both modes: `NotReady`, `Busy`, `BadRequest`, `UnknownFunction`, `TypeMismatch`, `StaleReference`, `NotFound`, `Timeout`, `VmError`, `Unsupported`, `InternalError`, all subclasses of `skython.RemoteError` (itself a `skython.SkythonError`). A bad call never takes down the interpreter, the bridge or the game.
- **One request format for both modes.** A request is the JSON object `{id, op, args}` and a response is `{id, ok, result | error}`, as text. In-process, `skython` hands the request text to `_skython` and gets the response text back; the bridge forwards the same text over the socket. So the C++ request parser, op-argument checks and response builders are shared by both modes and tested once against the golden fixtures, and `_skython` passes only strings and numbers across the Python/C++ boundary (no Python object conversion in C++). JSON encoding costs microseconds against a call that takes at least one frame.

### 4.3 Operations
The operation set is the same in-process and over the bridge; the Python API names follow each op.
- `papyrus.call_static {script, function, args, timeout}` → `game.call(script, function, *args)`.
- `papyrus.call_method {object, script, function, args, timeout}` → `game.call_method(obj, script, function, *args)`. `object` is a reference (4.4); `script` is the script type to bind (`Actor`, `ReferenceAlias`, or a mod's own script attached to the form).
- `papyrus.get_property` / `papyrus.set_property {object, script, property, value}`.
- `papyrus.batch {calls[], stop_on_error}`: runs the calls in order, one after another, returning all results. Not a transaction (see 4.9).
- `form.resolve {form_key | editor_id | form_id}` → reference with type and form key. `editor_id` works only where the game keeps editor IDs; the result says so.
- `game.state` → data_ready, loading, interactive, paused, menu stack, player reference, game time, epoch.
- `game.load {save}` / `game.save {name}`: dispatched with `AddUITask` (spike S6).
- `events.subscribe {event, filter}` → subscription id; `events.unsubscribe {sub}`.
- `debug.dump_natives`: lists the native functions registered in the live VM. Development only.
- `native.*`, a small fixed set Papyrus cannot do well: `native.actors_near {center, radius}`; `native.console {command}` (off unless the ini enables it). The set grows only when a real consumer needs it; every native op uses CommonLibSSE-NG accessors on skyrim-re-toolkit's verified class list, never raw offsets.

### 4.4 References and values
- **Form reference** (Python `skython.Form`; on the bridge `{"kind": "form", "form_id": 0x0001A69F, "form_key": "Skyrim.esm|0x01A69F", "type": "Actor", "epoch": n}`).
  - `form_key` is canonical for forms from plugins. Light plugins (`0xFE` FormIDs): `Plugin.esl|0x<12-bit local ID>`.
  - Runtime-created forms (`0xFF` FormIDs) have `form_key = None` and carry a **fingerprint** `{base, type}` taken when the plugin first hands the reference out.
- **Alias reference** (`skython.Alias`): quest reference, `alias_id`, `alias_type` (`ReferenceAlias` or `LocationAlias`), epoch. Bound through the VM's handle policy for the alias type; `ForceRefTo`, `GetRef`, `Clear` are ordinary `call_method` calls on it.
- **Re-resolution:** C++ never keeps raw pointers across tasks. Every task resolves every reference on the main thread by FormID (or the quest's alias list). A form that is gone is `NotFound`.
- **Lifetime of runtime-created references:** they stay usable across save and load while the game keeps the object. For `0xFF` references the plugin compares the live object's base form and type with the fingerprint: equal is valid, different is `StaleReference`. Plugin-defined forms stay valid across loads.
- **Consumer guidance:** skyrim-dm stores the whole reference (FormID + fingerprint) for evidence items, places them persistent (`PlaceAtMe(..., abForcePersist = true)`) or holds them in a quest alias.
- **Values** (`spy::Value` in C++, converted to and from Papyrus `Variable`s on the main or VM thread; carried between C++ and Python as the JSON forms below, in both modes; `skython.values` decodes them into Python objects):

| Papyrus | Python | Bridge JSON |
|---|---|---|
| Bool | `bool` | `true/false` |
| Int | `int`, range-checked to 32-bit signed; out of range is `TypeMismatch`, never truncation | number |
| Float | `float` | number |
| String | `str` | string |
| Form and subclasses | `skython.Form` | form reference |
| Alias | `skython.Alias` | alias reference |
| arrays of the above (max 128 elements) | `list` | `{"array": elem_type, "items": [...]}` |
| None (object) | `None` | `null` |

- **Type checking** is done twice: in Python against the generated signature table (M3), and in C++ against the VM's own function signature (the source of truth), before dispatch.
- **Defaults:** the VM's function objects expose parameter names and types but not default values, so C++ requires every parameter and Python fills missing trailing arguments from the generated signature table (M3). Until then, callers pass every argument.

### 4.5 Threading
Rules (each is pinned by a test):
1. **The game's main thread and the VM's threads never execute Python and never take the GIL.** Event sinks and `IStackCallbackFunctor`s convert to `spy::Value` and push into C++ queues.
2. **The host thread** is a `std::thread` the core starts at `kDataLoaded`. It calls `Py_InitializeFromConfig` with an isolated `PyConfig` (`isolated = 1`, `use_environment = 0`, `install_signal_handlers = 0`, `site_import = 1`, explicit `home` and `module_search_paths`: `python314.zip`, the python folder, `site-packages/`, `lib/` (the `skython` package) and `scripts/`), imports `skython._host` and runs `skython._host.main()`, which returns after setup (scripts loaded, bridge started). The host thread then releases the GIL with `PyEval_SaveThread()` and blocks forever on a condition variable (it must not end while holding the GIL, or every Python thread deadlocks). CPython's main thread is therefore ours, not the game's.
3. Python code runs only on the host thread and on threads Python creates. A blocking call (`game.call(...)`) releases the GIL while it waits on its C++ pending result.
4. **Main thread**, reached with `SKSE::GetTaskInterface()->AddTask`, one task per request (or per batch step): resolves references, checks argument types against the VM signature, builds `FunctionArguments`, calls `DispatchStaticCall` / `DispatchMethodCall`. Native ops run entirely here. Tasks never re-queue themselves.
5. **UI thread** via `AddUITask`: `game.load`, `game.save`, anything touching menus.
6. **VM threads:** the `IStackCallbackFunctor` converts the result to `spy::Value` (any form becomes a reference by FormID only) and completes the pending result. No Python.
7. **Timeouts:** per call (default 5 s). On timeout the caller gets `Timeout`; a late result is dropped and logged.
8. **Back-pressure:** a global cap on in-flight requests (default 1024) and, for the bridge, a per-connection cap (default 256). Beyond those, `Busy`.
9. **Exceptions:** every C++ task, callback and sink body is wrapped (`std::exception` and `...`); a failure becomes `Internal`. Every Python entry point the plugin calls (`skython._host.main`, a script's `setup`/`teardown`, an event callback) is wrapped in Python; an exception is logged with its traceback and the script is marked failed, nothing else.

### 4.6 Events
- Sinks are registered at `kDataLoaded`. A sink does nothing unless at least one subscription exists for its event type, and it filters in C++ before converting. Filters are equality sets on the event's references (`victim in [...]`, `quest in [...]`).
- Each subscription has a bounded C++ queue (default 1024). On overflow the oldest events are dropped and the next delivered event carries a `dropped` count.
- A Python **event dispatch thread** blocks on `_skython.next_events(timeout)` with the GIL released and runs callbacks in order; a callback may make blocking game calls. Over the bridge, the same events are pushed as `{event, sub, seq, epoch, data}` frames.
- Events carry references (4.4), the epoch and a per-subscription monotonic `seq`.
- **v1 events:** dying and death (from `TESDeathEvent`'s `dead` flag, exposed separately), hit, equip/unequip, container changed, cell attach/detach, quest stage, quest start/stop, player location change, activate, menu open/close, game loaded (the interactive signal), game saved, new game, and **mod events**.
- **Mod events (Papyrus → Python):** subscribing to `mod_event` with a name filter delivers SKSE `ModCallbackEvent`s (`SendModEvent(name, strArg, numArg)`); a native Papyrus function `Skython.Send(string channel, string payload)` (script `Skython`, shipped as `.psc` + `.pex`) delivers `{channel, payload}` to subscribers of `channel`. This is how skyrim-dm's pre-authored dialogue fragments, quest stage fragments and trigger boxes report `npc_told`, `npc_confronted` and "location entered".
- Known quirks handled in C++ and documented: death fires for dying and for dead; cell attach can repeat.

### 4.7 Python package `skython`
- Pure Python ≥3.12 (CPython 3.14 in the game). No runtime dependencies.
- **Sync API** (fits skyrim-dm): `game = skython.connect()`; calls block until the result. In-process, each call is `_skython.submit` plus a GIL-released wait; remotely, `skython.remote` sends a frame and a reader thread completes a future. Event callbacks run on a separate dispatch thread in both modes.
- `skython.connect(session=..., host=..., port=..., token=...)`: in the game, with no bridge argument, returns the in-process `Game`; given any bridge argument (`session`, `host`, `port`, `token`, ...) or outside the game, it resolves discovery (4.2), handshakes over the bridge and checks protocol compatibility.
- **v1 call surface (M1-M2):** `game.call(script, function, *args, timeout=5.0, require_unpaused=False)`, `game.call_method(obj, script, function, *args, ...)`, `game.get_property` / `set_property`, `game.batch()`, `game.form("Skyrim.esm|0x1A69F")`, `game.resolve(...)`, `game.state()`, `game.wait_ready()`, `quest.alias(n)`, `game.wait_for(predicate, timeout)`, event helpers (`game.on_death(actor, fn)`, `game.on_mod_event(name, fn)`, `game.on_channel(channel, fn)`). Generated per-class methods arrive with `pscgen` in M3.
- Typed exceptions (4.2). The sync API can retry `NotReady` for a bounded time (`retry_not_ready=True`).
- `skython._host` (in-game only): logging to `Documents/My Games/<game>/SKSE/Skython/python.log`, the script loader (4.10), and the bridge when enabled.

### 4.8 `pscgen`
- A Python tool (dev time only): a small parser for what headers declare (`ScriptName X extends Y [Hidden] [Conditional]`, `Function`/`Event` signatures with `native`, `global`, types, array types, default values, `Property` declarations, `Import`), ignoring function bodies. OpenPapyrus's grammar and Papyrus-2-Typescript (MIT) are references, not dependencies.
- Inputs: vanilla, SKSE, PO3 header sets, each pinned by source and version in a manifest. Outputs: Python modules, `.pyi` stubs, and a JSON signature table. Also usable on a mod's own headers.
- Licensing: we commit and ship **generated signatures**, never `.psc` source text (open question 2).

### 4.9 Writing to the game atomically (guidance for consumers)
- The engine has no transactions. A consumer that needs several changes to land together (skyrim-dm's contract setup: start quest, fill three aliases, set objective text, display objective) puts them in one Papyrus function on its own quest script and calls that once. One Papyrus call runs its statements in order on one script thread.

### 4.10 Player scripts (G7)
- Scripts live in `Data/SKSE/Plugins/Skython/scripts/` (shipped empty so MO2 keeps new files in the mod folder). Each script is a package or module with `setup(game)` and an optional `teardown()`. `skython._host` imports each one at startup, in name order, and runs its import and `setup(game)` on a thread of its own, waiting at most 10 s before moving on (the script is then reported as `timeout` and keeps running); a script that needs long-running work starts its own thread. `setup` should return quickly (it is logged if it takes more than 1 s).
- Third-party packages: vendored win_amd64 / cp314 wheels unpacked into `Data/SKSE/Plugins/Skython/site-packages/` (or a script's own `vendor/` folder added to `sys.path` by the script). Pure-Python wheels and packages with official Windows wheels only.
- A failing script (exception in import or `setup`) is logged and skipped; others still load.
- **Reload** (development): `skython._host.reload(name)` calls `teardown()`, drops the script's modules from `sys.modules`, re-imports and calls `setup(game)`. Best effort and documented as such: state held in C extensions or in threads the script did not stop in `teardown()` is not reset. During development the dev bridge is the primary loop, so reload is a convenience, not a guarantee.
- **skyrim-dm ships as a player script.** During development it runs on Linux through the dev bridge.

## 5. Save and load semantics
- The plugin stores nothing in the save in v1 (no SKSE co-save). State that must survive belongs to the consumer (skyrim-dm's ledger) or to the game (quest stages, aliases, globals, script properties).
- On load or new game: the epoch increments; `0xFF` references stay valid while their fingerprint matches the live object (4.4); plugin-defined references stay valid; subscriptions stay registered; `game.loaded` fires at the interactive signal. The interpreter and its scripts keep running across loads.
- Calls between `kPreLoadGame` and the interactive signal fail with `NotReady`.

## 6. Safety and security
- Python scripts run with the game's full privileges. The mod page says plainly that it runs Python code the player installs.
- Dev bridge: off in player builds; loopback only; token on every connection.
- The console op and any future write-anywhere native op are off by default.
- No outbound network from the plugin or `skython`, no auto-update, no downloads. (A player script may make its own network calls, e.g. skyrim-dm to a local model server; that is the script's responsibility and its own mod page's disclosure.)
- Every C++ task body is wrapped so an exception becomes `Internal`, never a crash. Structured exceptions are not caught; the design avoids them by never holding pointers across tasks.
- `python314.dll` is loaded by full path from the plugin's folder, before the core DLL. If a module of that name from another path is already loaded, Python stays disabled and the log says which module was found (two copies of the same CPython version in one process are not safe).
- On an unsupported runtime the plugin logs the version and does not start Python.

## 7. Build and versions
- CMake, C++23, MSVC v143 on the Windows box; CommonLibSSE-NG as a pinned submodule with SE, AE and 1.7 targets on; vcpkg manifest for spdlog and CommonLib's own dependencies; pybind11 3.1.0 and the CPython 3.14.8 NuGet headers/import library fetched by a pinned, checksummed script. Two DLLs (4.1): the loader has no Python dependency; the core links `python314.lib` normally.
- The C++ core (`core/`) and `_skython` module source build on Linux with g++ against uv's CPython 3.14 for unit tests (with a fake backend).
- Build is driven from Linux: sync to the Windows box, build over SSH, copy the DLL back, deploy into the SimpleSkyrim test profile with the embeddable Python, `lib/skython`, and the dev ini. One script does all of it.
- Test matrix: 1.6.1170 under GE-Proton (every change); 1.7.104 before any release; Windows native when the box can run the game (open question 3).

## 8. Testing
- **Protocol fixtures** (`protocol/v1/fixtures/`): golden bridge messages; `skython.bridge` and `skython.remote` tests both consume them.
- **C++ unit tests** (Linux, no game): value range checks, form keys and fingerprints, error codes, ini parsing, pending-result table, event queue, filters.
- **`_skython` tests** (Linux, pytest): the real module built against uv's CPython 3.14 with `FakeBackend`: GIL released while waiting, timeouts, cancellation, not-ready answers, the in-flight cap, a call from 16 Python threads at once.
- **Python unit tests:** `skython` against `FakeBackend`; `skython.remote` against a real `skython.bridge` running on Linux over the fake backend (the whole dev path, end to end, with no game); `pscgen` against fixture headers.
- **Live tests** (pytest on Linux, opt-in with `SKYTHON_LIVE=1`), through the dev bridge into the real game:
  - M1: global call, method call, alias call, latent call, `require_unpaused`, bad calls, a Python exception in a script, and **throughput** (sequential and pipelined calls per second) recorded as a baseline; an in-process smoke script that makes the same calls from inside the game.
  - M2: each event type, mod events and `Skython.Send`, save then load, a `PlaceAtMe` item still valid after save/load, `StaleReference` after loading an unrelated save, plugin-defined NPC still valid after a load, 30-minute soak.
- **skyrim-dm acceptance test**, against the test ESP: one `call_method` sets up the contract (start, fill three aliases, `SetObjectiveText`, display objective); an item is placed; killing the target raises a death event and the quest script's stage fragment calls `Skython.Send("skyrim_dm", ...)`; the test reads the new stage and objective state back. Run once through the bridge and once in-process as a player script.

## 9. Milestones

**v1 = M0, M1, M1.5, M2, and the skyrim-dm acceptance test.**

- **M0: spikes.** A throwaway spike DLL settles each unverified premise (table below). S8 runs first; if it fails, STOP: the fallback is draft 3's out-of-process design on branch `plan-out-of-process`.
- **M1: embedded dispatcher.** C++ core; `_skython` with the game backend (`call_static`, `call_method` on forms and aliases, properties, `batch`, `form.resolve`, `game.state`, paused/menu reporting, references, fingerprints, epochs, value mapping); CPython host thread and script loader; `skython` package; dev bridge and `skython.remote` with protocol fixtures; build/deploy script; live tests and the throughput baseline.
- **M1.5: test ESP and engine probes.** `SkythonTest.esp` with a shell quest (at least 3 reference aliases and 1 location alias, stages 10/20/100/200, one objective) and a quest script with `SetupContract(...)` and stage fragments that call `Skython.Send`. Authoring tool decided here (Mutagen, the Creation Kit on the Windows box, or xEdit scripts). Three probes through the generic call: (1) three `<Alias=X>` tags in one objective line; (2) PO3 `SetObjectiveText` with alias tags still resolving; (3) objective text and alias fills surviving save and reload. Results to `docs/probes/` for skyrim-dm. Which repo owns skyrim-dm's production shell quest is decided with skyrim-dm's integration work before this milestone starts.
- **M2: events and lifecycle.** Event queues, subscriptions, filters, the event dispatch thread, mod events and `Skython.Send`, `game.load`/`game.save`, the live-test harness with save/load.
- **skyrim-dm acceptance test** (section 8).
- **M3: typed bindings (after v1).** `pscgen`, generated `skython.papyrus`, client-side checking and defaults, `debug.dump_natives` cross-check.
- **M4: packaging (after v1).** Archive layout with the embeddable zip and its licence, MO2/USVFS checks on Windows and under Proton, Vortex install, VirusTotal and Windows Defender on the exact archive, MLVScan, Nexus pre-clearance email.

### Spikes (M0)

| # | Question | Pass criterion | If it fails, change |
|---|---|---|---|
| S8 | Does CPython 3.14.8 from the embeddable zip initialise inside the game under GE-Proton from an SKSE plugin's host thread, does the two-DLL load (4.1) resolve the core's `python314.dll` import to the copy loaded by full path, and can a Linux process reach a Python socket inside it? | The spike loads `python314.dll` by full path, then a spike core DLL linked against `python314.lib` loads and its import resolves to that module (logged module paths match); `Py_InitializeFromConfig` succeeds at `kDataLoaded`; `import json, ssl, socket, sqlite3, threading, ctypes` succeed; a Python `socketserver` on 127.0.0.1 answers 1000 round trips from a Linux client; the game keeps running and exits cleanly. | Whole design: fall back to draft 3 (branch `plan-out-of-process`). If only the socket fails: the bridge connects outbound to a Linux-side listener. |
| S2 | Which thread runs the `IStackCallbackFunctor`, and can a new `Dispatch*Call` be issued from inside it? Do arguments leak on a failed dispatch? | Callback thread ID logged; a second dispatch from the callback returns correctly 1000 times with no hang. | 4.3/4.5: `batch` issues each step with a fresh `AddTask`. |
| S9 | Can Python threads drive VM calls through `AddTask` without touching the game thread, and what does CPU-bound Python cost? | 8 Python threads × 1000 `call_static` round trips (`Utility.GetCurrentRealTime`) through a minimal built-in module, all correct, no hang; while one Python thread spins for 60 s, median and 99th-percentile frame time stay within 10% of a baseline run; and the p99 latency of calls from another Python thread during the spin is recorded against the no-spin p99 (GIL hand-off cost, `sys.getswitchinterval()` noted). | 4.5: pace Python work (sleep between batches) or move heavy work to a worker process; if calls hang, revisit the pending-result design. |
| S4 | Can the plugin bind a `ReferenceAlias` by quest + alias ID and call `ForceRefTo` by name? | `ForceRefTo` on a test quest's alias, then `GetRef` returns the same reference. | 4.4: aliases via a pre-authored Papyrus helper. |
| S5 | Does a `ModCallbackEvent` sink receive `SendModEvent` from Papyrus, and does a native `SkyrimPython.Send` reach C++? | Both arrive with their arguments intact. | 4.6: `Send` only, or `ModCallbackEvent` only. |
| S6 | Does `game.load` via `AddUITask` load a save under GE-Proton? | Load completes, `kPostLoadGame` fires, `Loading Menu` closes. | 4.3/G8: harness uses new game + `coc`; save/load tests become manual. |
| S7 | Do function lookup by name, bound-object dispatch on forms and aliases, and object arrays work as the dispatcher assumes? | As recorded in the S7 task. | 4.3/4.4 per criterion. |
| S10 | Do skyrim-dm's in-game dependencies install as win_amd64 / cp314 wheels and import in the embedded interpreter? | `pip download --only-binary=:all: --platform win_amd64 --python-version 3.14` succeeds for `httpx`, `jsonschema`, `mwparserfromhell`; unpacked into `site-packages/`, all three import in the game and an `httpx` request to a Linux HTTP server returns. | 4.10: pure-Python replacements, or the dependency runs outside the game. |

## 10. Open questions
1. Licence for the whole project: MIT proposed. Copying any ChronicleBridge code means the owner relicenses those parts from AGPL (they are ours).
2. Can generated signatures derived from Bethesda/SKSE `.psc` headers be shipped? SkyrimPlatform ships the equivalent; no authoritative statement found.
3. Where do live tests run: Linux/Proton (works today) or Windows (owner ruling of 2026-08-28, but native Windows crashes in `InitTESThread` even without our plugin)?
4. ESP authoring tool for `SkythonTest.esp` (M1.5): Mutagen, the Creation Kit, or xEdit.
5. Which repo owns skyrim-dm's production shell quest and its Papyrus script (Skython's M1.5 test ESP or skyrim-dm's `game/` drafts).
6. Interaction with other mods that embed CPython in-process: none known today; the plugin refuses to share a `python314.dll` from another path (section 6).
