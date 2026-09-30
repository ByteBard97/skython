# skyrim-python: design spec

Date: 2026-09-30. Status: draft 3, after an advisor review and a second reviewer's feedback.

## Changes from draft 2

1. **v1 is M1 + M2** (dispatcher, events, lifecycle, save/load, test harness), followed directly by the skyrim-dm acceptance test. Typed bindings (`pscgen`) move to M3 and the script host to M4; the async client is dropped from v1. The acceptance test uses the generic `call` with every argument passed.
2. **Engine probes (M1.5)** right after M1: three `<Alias=X>` tags in one objective line, PO3 `SetObjectiveText` resolving alias tags, and objective text surviving save/reload. These are skyrim-dm's real unknowns and cost little once generic calls work.
3. **The shell-quest test ESP is scheduled work** (M1.5), with its authoring tool chosen there.
4. **Runtime-created references** (`PlaceAtMe` evidence items, spawned NPCs) now have a defined identity and lifetime: FormID plus a base-form fingerprint, valid across save/load while the object exists; `stale_reference` only when the FormID now names a different object. This replaces draft 2's "every `0xFF` reference is stale after a load", which would have broken skyrim-dm's evidence items.
5. **External clients are off by default in the player build** (`allow_external_clients = false`); the development config and skyrim-dm's setup turn them on.
6. **Throughput is measured in M1**, not at the end.
7. G6 says "built for" SE/AE/1.7 and "verified on" only what the test matrix has actually run.
8. M4 risks added: MO2's USVFS hooks child processes (one documented crash in the research); MLVScan flags process execution in mod DLLs; Nexus pre-clearance email. The script host moves into an optional second DLL so the core plugin never calls `CreateProcess`.
9. Paused and menu-open behaviour defined (4.1, 4.5).
10. Open question on rebuilding ChronicleBridge answered: no, borrow its patterns.
11. Verified from the CommonLibSSE-NG headers: `IFunction` exposes parameter names and types but no default values, so client-side defaults (draft 2 change 2) is confirmed; spike S3 is dropped.

## Changes from draft 1

1. References gain an **alias** kind (`Quest` + alias ID), because aliases are script objects, not forms, and `ForceRefTo` lives on them. `ActiveMagicEffect` is out of scope for v1.
2. **Papyrus default arguments are filled in by the client** from the generated signature table, not by the plugin. The Papyrus compiler most likely inserts defaults at each call site, so the VM may not know them (spike S3 settles this). Until M3, hand-written calls pass every argument.
3. The **staleness rule is narrowed**: every call re-resolves by FormID; only runtime-created references (the `0xFF` FormID range) from an older session epoch are rejected as stale. Form keys are defined for `0xFF` references (none) and light plugins (`0xFE`).
4. A **Papyrus-to-Python channel** is added: subscriptions to SKSE mod events (`ModCallbackEvent`) and a native Papyrus function `SkyrimPython.Send(channel, payload)`. skyrim-dm's pre-authored quest and dialogue fragments use it to report `npc_told`, `confronted`, "location entered" exactly.
5. **Atomic multi-step writes are done by one pre-authored Papyrus function** called once (it runs in order on one script thread). `papyrus.batch` stays as a convenience with no ordering claim beyond what spike S2 proves. `papyrus.get_property`/`set_property` are added.
6. Threading is spelled out: which thread runs what, no fictitious per-frame cap, a concrete "game is interactive" signal.
7. The client runs event callbacks on their own dispatch thread, so a callback can make blocking calls without deadlocking.
8. Default port changed from 27015 (Steam's) to 45977; discovery uses an explicit session-file path or `SKYRIM_PYTHON_SESSION`, not a guessed Proton prefix.
9. Consumer modes stated: external process (skyrim-dm) and child-host script. The child host in v1 is stdlib-only; skyrim-dm stays external.
10. The protocol is a first-class artifact: one versioned schema plus golden fixtures that both the C++ server and the Python client are tested against.
11. CommonLibSSE-NG source and pin are named.
12. A spike milestone (M0) is added for every unverified premise, each with a pass/fail criterion and the section it would change.

## 1. What we are building

A Skyrim SE/AE library that lets Python code read and change the running game through an API that feels native, in the way KiCad exposes scripting. It has three parts:

1. **`SkyrimPython.dll`**, an SKSE plugin (C++, CommonLibSSE-NG). It runs a small RPC server on loopback, calls any Papyrus function by name through the Papyrus VM, runs a short list of native operations, pushes game events to subscribers, and does game work on the game thread.
2. **`skyrim`**, a pure-Python client package with no runtime dependencies. It talks to the plugin over a socket and exposes typed game classes (`Actor`, `Quest`, `ReferenceAlias`, ...) generated from the Papyrus `.psc` headers, plus events and futures.
3. **`pscgen`**, a build-time generator that reads `.psc` headers (vanilla, SKSE, PO3) and writes the typed bindings, `.pyi` stubs and a JSON signature table.

**Consumer modes (v1):**
- **External process** on the same machine: skyrim-dm, tests, tools. On Linux this is a native Linux process talking to the game under Proton. This is the primary mode and the one skyrim-dm uses.
- **Child-host script**: a player script run by the Python process the plugin launches (M4). Stdlib only in v1.

**skyrim-dm is the first consumer.** It will depend on the `skyrim` package to deliver contracts (start a shell quest, fill aliases, set objective text, place items, write consequences) and to learn when a contract completes or fails.

## 2. Goals and non-goals

**Goals (v1)**
- G1. Call any Papyrus function (global or member, vanilla, SKSE or any installed mod) from Python by name, with typed arguments and return values, including latent functions such as `Utility.Wait`, on forms and on quest aliases.
- G2. Typed Python bindings for vanilla, SKSE and PO3, generated rather than hand-written, with IDE autocomplete and client-side argument checking and defaults.
- G3. Subscribe to game events (list in 4.6) with filters applied in C++, plus a Papyrus-to-Python channel for events a mod's own scripts raise.
- G4. Never crash the game because of a bad request, a stale reference, a request during a load screen, or a client that disconnects.
- G5. Work under GE-Proton on Linux with external clients running natively on the Linux host, and on Windows.
- G6. One DLL **built for** SE 1.5.97, AE 1.6.1170 and AE 1.7.x (CommonLibSSE-NG multi-target). It is **verified on** 1.6.1170 only until the others have passed the live tests; release notes list only verified runtimes.
- G7. A player-facing script host, shipped as an optional second DLL: it launches the official embeddable CPython as a hidden child process that runs player scripts, with crash isolation and hot reload (M4, after v1).
- G8. A live-test harness that drives the real game through this library, able to replace DevBench for skyrim-dm and Chronicle testing, including save and load.

**Non-goals**
- Embedding CPython inside the DLL (option A). No shipped precedent, a documented same-process DLL collision risk, no Proton data. The protocol keeps this open for later.
- Wrapping CommonLibSSE-NG classes wholesale, or calling engine functions by raw address.
- Creating quests, dialogue topics, INFO records, books or journal log text at runtime (the engine cannot). skyrim-dm pre-authors these in an ESP.
- `ActiveMagicEffect` and other non-form script objects besides aliases.
- VR, Fallout 4, Starfield. An MCP server (a thin Python adapter later). Any network access beyond loopback, any auto-update.
- Third-party Python packages in the child host (v1).

## 3. Constraints and facts this design rests on

- Target runtime: AE 1.6.1170, SKSE 2.2.6, Address Library. 1.7.99 (2026-08-20) and 1.7.104 (2026-08-27) changed class layouts and moved Address Library to a new file format; old CommonLibSSE-NG builds all broke and needed a recompile against a CommonLibSSE-NG that knows the new format.
- CommonLibSSE-NG: **alandtse/CommonLibSSE-NG, branch `ng`**, pinned to a commit that models the 1.7.99/1.7.104 layouts and Address Library format 5 (the changelog shows the 1.7.99 layout changes on 2026-08-22). Consumed as a git submodule and `add_subdirectory`, as skyrim-re-toolkit's RuntimeHarness does, not through the Color-Glass vcpkg registry (reported broken). The copy vendored in skyrim-re-toolkit (b93280e8, 2024-09) is too old and must not be used. Other C++ dependencies through classic vcpkg (manifest mode, pinned baseline).
- Development: Linux host. C++ is built only on the Windows box (192.168.0.211, VS2022 v143) over SSH, as ChronicleBridge and RuntimeHarness do. The game runs under GE-Proton (`~/Games/SimpleSkyrim`, launched with `skse64_loader.exe` directly, not through MO2).
- Transport: Wine's loopback is the host's loopback. Verified in the outbound direction (ChronicleBridge's plugin connects to a Linux listener). The inbound direction this design needs (a Linux process connecting to a socket bound inside Wine) is expected to work but is **unverified** (spike S1). Windows named pipes do not cross the boundary.
- skyrim-dm is synchronous Python ≥3.12 (blocking httpx, sqlite; no asyncio). It names game objects by form key `Plugin.esp|0xLocalID`. Its engine needs (from its docs): quest start/stage/objective display, alias `ForceRefTo`, PO3 `SetObjectiveText`, `Game.GetFormFromFile`, alive/dead checks, inventory transfer, `SetName`, relationship rank, gold, game time; events for death, quest stage, location change, items added/removed, and dialogue-driven completion.
- Precedents (ideas; code only where the licence allows): ChronicleBridge (AGPL-3.0, ours) for `AddTask` + promise hand-off, sink-to-queue-to-sender-thread, plugin+local-ID references, `gameActive` checks, the `kPostLoadGame` packed-value crash; IntelEngine-NativePlugin `AsyncDispatch.cpp` (Apache-2.0) for `MakeFunctionArguments` + `DispatchMethodCall` via `AddTask`; SkyLink AI (MIT-intended, no LICENSE file) for PapyrusBridge/TaskQueue/EventSystem and a safety checker; SkyrimPlatform (GPL-3.0) for promises and hot reload; KiCad 9 for the external-interpreter + socket + token model.
- Header sources: SKSE (62 files) and PO3 under `~/Games/*/mods/...`; a vanilla replacement set (UOSC, 503 files); full vanilla sources from the CK `Scripts.zip` / `Skyrim - Misc.bsa`.

## 4. Architecture

```
 ┌──────────────── Skyrim process (Windows or Proton) ─────────────────┐
 │  SkyrimPython.dll                                                  │
 │   ┌───────────┐ requests ┌────────────┐ AddTask  ┌───────────────┐ │
 │   │ RPC server├─────────►│ Dispatcher ├─────────►│ main thread:  │ │
 │   │ (loopback │          │            │          │ resolve refs, │ │
 │   │  TCP, IO  │          └────────────┘          │ check types,  │ │
 │   │  thread)  │◄──────── responses ──────────────┤ Dispatch*Call │ │
 │   │           │                                  └──────┬────────┘ │
 │   │           │◄──── IStackCallbackFunctor (VM thread) ─┘          │
 │   │           │◄──── events ── event bus ◄── sinks / ModCallback / │
 │   └─────▲─────┘                              SkyrimPython.Send     │
 │         │ launches (optional, M4)                                  │
 └─────────┼──────────────────────────────────────────────────────────┘
           │ TCP 127.0.0.1:<port>, token
   ┌───────┴────────────┐        ┌──────────────────────────────┐
   │ script host (child)│        │ external client (Linux host) │
   │ embeddable CPython │        │ skyrim-dm, tests, tools      │
   │ + `skyrim` package │        │ + `skyrim` package           │
   └────────────────────┘        └──────────────────────────────┘
```

### 4.1 Plugin lifecycle
- `SKSEPlugin_Load`: register the messaging listener, read the config, register the native Papyrus function `SkyrimPython.Send` through the SKSE Papyrus interface. No work in `DllMain`.
- `kPostLoad`: start the RPC server thread.
- `kDataLoaded`: forms and the VM are ready. Register event sinks. Mark the plugin `data_ready`. Launch the script host if enabled.
- `kPreLoadGame`: mark `loading`. `kPostLoadGame` / `kNewGame`: bump the **session epoch**. (`kPostLoadGame`'s data is a packed value, not a pointer.)
- **Interactive signal:** after `kPostLoadGame` or `kNewGame`, the game is "interactive" when the `Loading Menu` closes (`MenuOpenCloseEvent`, opening = false). Only then do game requests run and does the `game.loaded` event fire. Before `kDataLoaded`, and between `kPreLoadGame` and that signal, game requests return `not_ready` with `reason: "loading"`. At the main menu (no game loaded), requests that need a game return `not_ready` with `reason: "no_game"`; `game.state` and `game.load` still work.
- **Paused and menus:** a loaded game that is paused or has a menu open (inventory, dialogue, console, journal) is not "not ready": most calls work, and refusing them would break menu-driven mods. But Papyrus scripts, latent functions such as `Utility.Wait`, and AI do not advance while the game is paused, so such calls simply wait and may hit their timeout. The plugin therefore: reports `paused` and the open-menu stack in `game.state` and in every `not_ready`/`timeout` error; accepts a per-call flag `require_unpaused` that returns `not_ready` with `reason: "paused"` immediately instead of waiting; and the client documents that latent calls should use it or a generous timeout.

### 4.2 Transport and protocol
- TCP bound to `127.0.0.1` only. Port from the config (default **45977**; `0` = pick a free port). Non-loopback peers are refused.
- Framing: 4-byte little-endian length, then a UTF-8 JSON object. Maximum frame 16 MiB.
- Handshake: the first client message is `hello {protocol, token, client}`; the server answers `welcome {protocol, plugin_version, game_version, runtime, epoch, capabilities}` or `handshake_error {code, message}` and closes. Exact shapes: `protocol/PROTOCOL.md`. A protocol-major mismatch is refused with a clear error.
- **Token and discovery:** the plugin generates a random 128-bit token at start and writes `{port, token, pid, plugin_version, protocol}` to a session file. Its path comes from the config (`session_file`); the default is `Documents/My Games/<game>/SKSE/SkyrimPython/session.json` inside whatever prefix the game runs in. Clients find it through, in order: an explicit argument, the `SKYRIM_PYTHON_SESSION` environment variable, then a fixed `port`+`token` pair from the client's own config (for development, matching a fixed pair in the plugin ini). There is no guessing of Proton prefix paths.
- A config switch `allow_external_clients` controls whether any client other than the script host (which gets its own token through its environment) may connect. **Default `false` in the player build**, so a released mod does not open a local scripting port that any local process can use. The development config sets it `true`, and skyrim-dm's own setup step turns it on (and says so) when a player installs skyrim-dm. When it is `false` and no script host is running, the plugin does not open the port at all.
- Messages: requests `{id, op, args}`, responses `{id, ok, result | error}`, pushed events `{event, sub, seq, epoch, data}`. Requests are pipelined; responses may arrive out of order.
- Errors are typed: `not_ready`, `busy`, `bad_request`, `unknown_function`, `type_mismatch`, `stale_reference`, `not_found`, `timeout`, `vm_error`, `unsupported`, `internal`. A bad request never takes down the connection; a broken connection never affects the game.
- **The protocol is an artifact:** `protocol/` holds a versioned JSON Schema for every message and a set of golden request/response/event fixtures. The C++ server's unit tests and the Python client's unit tests both run against the same fixtures.

### 4.3 Operations (protocol v1)
- `papyrus.call_static {script, function, args, timeout_ms}`.
- `papyrus.call_method {object, script, function, args, timeout_ms}`: `object` is a reference (4.4), `script` is the script type to bind (for example `Actor`, `ReferenceAlias`, or a mod's own script attached to the form, such as `SkyrimDMShellQuestScript`).
- `papyrus.get_property` / `papyrus.set_property {object, script, property, value}`: script properties on forms and aliases (skyrim-dm's shell quest script needs these).
- `papyrus.batch {calls[], stop_on_error}`: runs the calls in the listed order, one after another, returning all results. Ordering is guaranteed; same-frame execution is not. How the next call is issued (from the previous call's callback, or a fresh `AddTask`) depends on spike S2. **Not a transaction.** For anything that must happen together, the recommended pattern is a single pre-authored Papyrus function on the mod's own script, called once (see 4.9).
- `form.resolve {form_key | editor_id | form_id}` → reference with type and form key (which names the plugin); the display name is one `Form.GetName` call away. `editor_id` works only where the game keeps editor IDs (it does not for most forms without a helper like PO3's `GetFormEditorID` or powerofthree's Tweaks); the result says so.
- `game.state` → data_ready, loading, interactive, paused, menu stack, player reference, game time, epoch.
- `game.load {save}` / `game.save {name}`: dispatched with `AddUITask`, because DevBench's `AddTask`-based load silently does nothing under Proton (spike S6 confirms the fix).
- `events.subscribe {event, filter}` → subscription id; `events.unsubscribe {sub}`.
- `debug.dump_natives`: lists the native functions registered in the live VM, to check the generated signature table against the running game. Development only.
- `native.*`, a small fixed set that Papyrus cannot do well:
  - `native.actors_near {center, radius}`: references, positions, alive, in-combat, for actors in loaded cells.
  - `native.console {command}`: off unless the config enables it.
  - The native set grows only when a real consumer needs it and Papyrus cannot do it. Every native op uses only classes on skyrim-re-toolkit's verified list, through CommonLibSSE-NG accessors, never raw offsets.

### 4.4 References and values
- **Form reference** on the wire: `{"kind": "form", "form_id": 0x0001A69F, "form_key": "Skyrim.esm|0x01A69F", "type": "Actor", "epoch": n}`.
  - `form_key` is canonical for forms from plugins. For light plugins (`0xFE` FormIDs) it is `Plugin.esl|0x<12-bit local ID>` and the plugin resolves it through the light-plugin index.
  - Runtime-created forms and references (`0xFF` FormIDs, e.g. items placed with `PlaceAtMe`, spawned NPCs) have `form_key: null`. They carry `form_id` plus a **fingerprint** `{"base": <base form's form key or form_id>, "type": ...}` taken when the plugin first hands the reference out.
- **Alias reference**: `{"kind": "alias", "quest": <form reference>, "alias_id": n, "alias_type": "ReferenceAlias" | "LocationAlias", "epoch": n}`. The plugin finds the alias in the quest's alias list and binds it through the VM's handle policy for the alias type. `ForceRefTo`, `GetRef`, `Clear` and the rest are ordinary `call_method` calls on it.
- **Re-resolution:** the plugin never keeps raw pointers across tasks. Every task resolves every reference on the main thread (`TESForm::LookupByID`, or the quest's alias list for aliases). If the form is gone, the call fails with `not_found`.
- **Lifetime of runtime-created references:** a created reference is saved in the savegame with its FormID, so it stays usable across save and load for as long as the game keeps the object (until it is deleted, or cleaned up because it was never persistent and its cell reset). The risk is that after loading a *different* save, or after the object is deleted, the same `0xFF` FormID can name another object. So for `0xFF` references the plugin compares the live object's base form and type with the fingerprint: equal means valid, different means `stale_reference`. Plugin-defined forms, including non-persistent placed NPCs and items, stay valid across loads because their FormIDs are fixed.
- **Consumer guidance:** skyrim-dm stores the whole reference (FormID + fingerprint) in its ledger for evidence items. To make an item survive cell resets it should be placed persistent (`PlaceAtMe(..., abForcePersist = true)`) or held in a quest alias, which is also what keeps quest items from being cleaned up in vanilla.
- **Values:**

| Papyrus | Python | Wire |
|---|---|---|
| Bool | `bool` | `true/false` |
| Int | `int`, range-checked to 32-bit signed; out of range is `type_mismatch`, never truncation | number |
| Float | `float` | number |
| String | `str` | string |
| Form and subclasses | `skyrim.Form` subclass instance | form reference |
| Alias | `skyrim.ReferenceAlias` / `LocationAlias` | alias reference |
| arrays of the above | `list` | `{"array": elem_type, "items": [...]}` |
| None (object) | `None` | `null` |

- **Type checking** is done twice: in the client against the generated signature table (good error messages, no round trip), and in the plugin against the VM's own function signature (the source of truth), before dispatch.
- **Defaults:** the Papyrus compiler inserts default values at each call site; the VM's function objects (`IFunction::GetParam`) expose only parameter names and types. So the plugin requires every parameter, and the client fills missing trailing arguments from the generated signature table (M3). Until then, callers pass every argument.

### 4.5 Threading
- **IO thread** (one per plugin): accepts connections, parses and serialises frames, owns each connection's outbound queue. Never touches game objects.
- **Main thread**, reached with `SKSE::GetTaskInterface()->AddTask`, one task per request (or per batch step): resolves references, checks argument types against the VM signature, builds `FunctionArguments`, calls `DispatchStaticCall`/`DispatchMethodCall`. Native ops run entirely here. Tasks never re-queue themselves (a self-requeuing task runs again in the same drain and freezes the frame).
- **UI thread**, reached with `AddUITask`: `game.load`, `game.save`, and anything touching menus.
- **VM threads**: the `IStackCallbackFunctor` fires on a Papyrus VM thread when the function returns (later for latent functions). It converts the result (Papyrus values are safe to read here; any Form in the result is converted to a reference by FormID only, no further engine access) and pushes the response onto the IO thread's outbound queue.
- **Timeouts:** per request (default 5 s, overridable). On timeout the client gets `timeout`; a late result is dropped and logged.
- **Back-pressure:** a per-connection cap on in-flight requests (default 256) and a global cap (default 1024). Beyond those the server answers `busy`. There is no per-frame task cap; `AddTask` offers no drain control.

### 4.6 Events
- Sinks are registered at `kDataLoaded`. A sink does nothing unless at least one subscription exists for its event type, and it filters in C++ before serialising. Filters are simple equality sets on the event's references (e.g. `victim in [...]`, `quest in [...]`).
- Each connection has a bounded event queue (default 1024). On overflow the oldest events are dropped, and the next delivered event carries a `dropped` count.
- Events carry references (4.4), the epoch, and a per-connection monotonic `seq`.
- **v1 events:** dying and death (from `TESDeathEvent`'s `dead` flag, exposed separately), hit, equip/unequip, container changed, cell attach/detach, quest stage, quest start/stop, player location change, activate, menu open/close, game loaded (the interactive signal), game saved, new game, and **mod events**.
- **Mod events (Papyrus → Python):**
  - Subscribing to `mod_event` with a name filter delivers SKSE `ModCallbackEvent`s (`SendModEvent(name, strArg, numArg)`) raised by any mod.
  - A native Papyrus function `SkyrimPython.Send(string channel, string payload)` (script `SkyrimPython`, shipped as `.psc` + `.pex`) delivers `{channel, payload}` to subscribers of `channel`. Payload is an arbitrary string, typically JSON.
  - This is how skyrim-dm's pre-authored dialogue fragments, quest stage fragments and trigger boxes report `npc_told`, `npc_confronted` and "location entered" precisely, instead of inferring them from engine dialogue events.
- Known quirks handled in C++ and documented: death fires for dying and for dead; cell attach can repeat.

### 4.7 Python package `skyrim`
- Python ≥3.11, stdlib only.
- **Sync API only in v1** (fits skyrim-dm): `game = skyrim.connect()`; calls block until the result. A background **reader thread** handles the socket; **event callbacks run on a separate dispatch thread**, in order, so a callback may make blocking calls. `game.events.wait(...)` for polling-style code. Internally each request is a future, so an async front end (`await skyrim.aconnect()`) can be added later without protocol changes; it is not in v1.
- **v1 call surface (M1-M2):** the generic `game.call(script, function, *args)`, `game.call_method(obj, script, function, *args)`, properties, and the hand-written layer below. Generated per-class methods (`game.player.get_actor_value("Health")`) arrive with `pscgen` in M3.
- `skyrim.connect(session=..., host=..., port=..., token=...)` resolves discovery as in 4.2, handshakes, and checks protocol compatibility.
- Generated classes (`skyrim.papyrus.Actor`, `Quest`, `ReferenceAlias`, `ObjectReference`, `Game`, `Utility`, `Debug`, ...), with SKSE and PO3 functions merged onto the same classes. Method names are snake_case with the Papyrus name kept as an alias. Globals are static methods.
- A hand-written layer: `game.form("Skyrim.esm|0x1A69F")`, `quest.alias(n)`, `game.state()`, `game.wait_ready()`, `game.batch()`, `game.wait_for(predicate, timeout)`, event helpers (`game.on_death(actor, fn)`, `game.on_mod_event(name, fn)`, `game.on_channel(channel, fn)`), and `call_script(obj, script, function, *args)` for a mod's own script functions that have no generated binding.
- Typed exceptions mirror the protocol errors (`skyrim.NotReady`, `skyrim.StaleReference`, ...). The sync client can retry `not_ready` for a bounded time (`retry_not_ready=True`).
- Versioned independently of the DLL; the handshake checks protocol compatibility.

### 4.8 `pscgen`
- A Python tool (dev time only): a small parser for what headers declare (`ScriptName X extends Y [Hidden] [Conditional]`, `Function`/`Event` signatures with `native`, `global`, types, array types, default values, `Property` declarations, `Import`), ignoring function bodies. The OpenPapyrus ANTLR grammar and Papyrus-2-Typescript (MIT) are references, not dependencies.
- Inputs: vanilla, SKSE, PO3 header sets, each pinned by source and version in a manifest.
- Outputs: Python modules, `.pyi` stubs, and a JSON signature table (script, function, parameter names/types/defaults, return type, global/member, native). The client uses the table for local checking and defaults.
- Also usable on a mod's own headers, so skyrim-dm can generate bindings for its shell-quest script.
- Licensing: we commit and ship **generated signatures**, never `.psc` source text (open question 2).

### 4.9 Writing to the game atomically (guidance for consumers)
- The engine has no transactions. A consumer that needs several changes to land together (skyrim-dm's contract setup: start quest, fill three aliases, set objective text, display objective) should put them in one Papyrus function on its own quest script and call that once. One Papyrus call runs its statements in order on one script thread.
- Consumers generate bindings for that script with `pscgen` or use `call_script`.
- The library documents this pattern with a worked example in the skyrim-dm acceptance test.

### 4.10 Script host (M4, after v1)
- The host launcher is a **separate optional DLL**, `SkyrimPythonHost.dll`. The core `SkyrimPython.dll` never starts processes, so a player who only uses external clients (skyrim-dm) installs a plugin with no process-launch code for community scanners (MLVScan flags "process execution" in mod DLLs) to flag.
- The launcher starts `python.exe` from the unmodified official Windows embeddable CPython 3.12 x64 zip (`Data/SKSE/Plugins/SkyrimPython/python/`), with `CreateProcess`, no console window, passing the port and host token through the environment.
- **Risks to settle in M4 before building further:** MO2's USVFS hooks child processes of the game, and the research records one documented crash from that; the child must be tested under MO2 on Windows and under Proton. Nexus's rules do not forbid a helper process, but the research recommends emailing Nexus support with the design before release.
- It runs player scripts from `Data/SKSE/Plugins/SkyrimPython/scripts/` (shipped empty so MO2 keeps new files in the mod folder, not Overwrite). A script is a module with `setup(game)` and optional `teardown()`. Stdlib and the `skyrim` package only in v1.
- Hot reload means restarting the child process: a clean process every time.
- If the child crashes, the plugin logs it, drops its subscriptions and restarts it with back-off (1 s, 5 s, 30 s, then stops and shows one in-game notification).
- Logs go to `Documents/My Games/<game>/SKSE/SkyrimPython/`.
- Off unless there are scripts to run.
- **skyrim-dm is not a child-host script.** It needs third-party packages (httpx, jsonschema, mcp) that the embeddable zip cannot install. It stays an external client with its own packaging, which is skyrim-dm's problem, not this library's.

## 5. Save and load semantics
- The plugin stores nothing in the save in v1 (no SKSE co-save). State that must survive belongs to the client (skyrim-dm's ledger) or to the game (quest stages, aliases, globals, script properties).
- On load or new game: the epoch increments (clients can see a load happened); `0xFF` references stay valid while their fingerprint still matches the live object (4.4); plugin-defined references stay valid; subscriptions stay registered; `game.loaded` fires at the interactive signal.
- Requests between `kPreLoadGame` and the interactive signal return `not_ready`.

## 6. Safety and security
- Loopback only; token on every connection; `allow_external_clients` switch.
- The console op and any future write-anywhere native op are off by default.
- The plugin is open source (licence: open question 1). The mod page says plainly that it runs Python scripts the player installs.
- No outbound network, no auto-update, no downloads.
- Every task body is wrapped so a C++ exception becomes an `internal` error, never a crash. Structured exceptions are not caught; the design avoids them by never holding pointers across tasks.
- On an unsupported runtime the plugin logs the version and does not start its server.

## 7. Build and versions
- CMake, C++23, MSVC v143 on the Windows box; CommonLibSSE-NG as a pinned submodule (section 3) with SE, AE and 1.7 targets on; vcpkg manifest for other dependencies (a JSON library, e.g. nlohmann-json or glaze, and spdlog).
- Build is driven from Linux: sync sources to the Windows box, run the build over SSH, copy the DLL back, deploy into the SimpleSkyrim test profile. One script does all of it.
- Test matrix: 1.6.1170 under GE-Proton (every change); 1.7.104 before any release; Windows native when the box can run the game (open question 3).

## 8. Testing
- **Protocol fixtures** (`protocol/fixtures/`): golden messages; both sides' unit tests consume them.
- **C++ unit tests** (no game): framing, JSON, value conversion and range checks, reference/epoch logic, event queue, filters. Built and run on the Windows box.
- **Python unit tests**: the client against a fake server that replays fixtures; `pscgen` against a fixture header set plus the full SKSE and PO3 sets.
- **Live tests** (pytest, opt-in with `SKYRIM_LIVE=1`), driving the real game through `skyrim`, adapted from Chronicle's harness (launch under GE-Proton with `skse64_loader.exe`, `coc` to a known cell, dismiss modals, check frames advance):
  - M1: global call, method call, alias call, latent call, `require_unpaused`, bad calls, and **throughput**: sequential calls per second and pipelined calls per second (is it frame-bound?), recorded as a baseline.
  - M2: each event type, mod events and `SkyrimPython.Send`, save then load, a `PlaceAtMe` item still valid after save/load, `stale_reference` after loading an unrelated save, plugin-defined NPC still valid after a load, 30-minute soak.
- **skyrim-dm acceptance test**, against a small test ESP with a shell quest and a quest script: one `call_method` sets up the contract (start, fill three aliases, `SetObjectiveText`, display objective); an item is placed; killing the target raises a death event and the quest script's stage fragment calls `SkyrimPython.Send("skyrim_dm", ...)`; the test reads the new stage and objective state back.

## 9. Milestones

**v1 = M0, M1, M1.5, M2, and the skyrim-dm acceptance test.**

- **M0: spikes.** A throwaway spike DLL and Python script settle each unverified premise (S1, S2, S4, S5, S6 below). S1 (the transport) runs first; nothing else starts until it passes or the transport section is rewritten.
- **M1: dispatcher.** Protocol schema and fixtures; plugin with RPC server, handshake, token, `call_static`, `call_method` (forms and aliases), `get_property`/`set_property`, `batch`, `form.resolve`, `game.state`, paused/menu reporting; references, fingerprints and epochs; value mapping. Sync client with the generic call surface. Build/deploy script. Live tests for calls and the throughput baseline.
- **M1.5: test ESP and engine probes.** A small test plugin `SkyrimPythonTest.esp` with a shell quest (an alias pool of at least 3 reference aliases and 1 location alias, stages 10/20/100/200, one objective) and a quest script with a `SetupContract(...)` function and stage fragments that call `SkyrimPython.Send`. Authoring tool decided in this milestone (candidates: Mutagen scripted from C#, the Creation Kit on the Windows box, or xEdit scripts); the ESP and its `.psc` sources live in this repo. Then three probes, through the generic call: (1) three distinct `<Alias=X>` tags in one objective line; (2) PO3 `SetObjectiveText` with alias tags still resolving; (3) objective text and alias fills surviving save and reload (manual load until M2's `game.load`, then automated). Results are written to `docs/probes/` for skyrim-dm.
- **M2: events and lifecycle.** Event bus, subscriptions, filters, lifecycle and interactive signal, mod events and `SkyrimPython.Send`, `game.load`/`game.save`, the live-test harness with save/load.
- **skyrim-dm acceptance test** (section 8), using the generic call with every argument passed.
- **M3: typed bindings (after v1).** `pscgen`, generated `skyrim.papyrus`, client-side checking and defaults, `debug.dump_natives` cross-check, optional async front end.
- **M4: script host and packaging (after v1).** `SkyrimPythonHost.dll`, child-process host, hot reload, crash restart, USVFS and Proton checks, archive layout, VirusTotal and MLVScan on the exact archive, Nexus pre-clearance email.

### Spikes (M0)

| # | Question | Pass criterion | If it fails, change |
|---|---|---|---|
| S1 | Can a native Linux process connect to a TCP socket bound to 127.0.0.1 inside the Proton game process? | A Linux Python client completes a round trip with a spike DLL's listener, 1000 times, under GE-Proton. | 4.2: fall back to the plugin connecting outbound to a Linux-side broker, or bind 0.0.0.0 with a peer check. |
| S2 | Which thread runs the `IStackCallbackFunctor`, and can a new `Dispatch*Call` be issued from inside it? | Callback thread ID logged; a second dispatch from the callback returns correctly 1000 times with no hang. | 4.3/4.5: `batch` issues each step with a fresh `AddTask`. |
| S4 | Can the plugin bind a `ReferenceAlias` by quest + alias ID and call `ForceRefTo` by name? | `ForceRefTo` on a test quest's alias, then `GetRef` returns the same reference. | 4.4: aliases via a pre-authored Papyrus helper function instead. |
| S5 | Does a `ModCallbackEvent` sink receive `SendModEvent` from Papyrus, and does a native `SkyrimPython.Send` reach C++? | Both arrive with their arguments intact. | 4.6: `Send` only, or `ModCallbackEvent` only. |
| S6 | Does `game.load` via `AddUITask` load a save under GE-Proton? | Load completes, `kPostLoadGame` fires, `Loading Menu` closes. | 4.3/G8: test harness uses a new game + `coc` instead of loads; save/load tests become manual. |

## 10. Open questions
1. Licence for the whole project: MIT proposed. Copying any ChronicleBridge code means the owner relicenses those parts from AGPL (they are ours).
2. Can generated signatures derived from Bethesda/SKSE `.psc` headers be shipped? SkyrimPlatform ships the equivalent; no authoritative statement found.
3. Where do live tests run: Linux/Proton (works today) or Windows (owner ruling of 2026-08-28, but native Windows crashes in `InitTESThread` even without our plugin)?
4. ~~Should ChronicleBridge be rebuilt on this library?~~ Answered: no, not now; borrow its patterns.
5. Third-party packages in the child host (a post-v1 feature): vendored wheels plus a `._pth` path, or never.
6. ESP authoring tool for `SkyrimPythonTest.esp` (M1.5): Mutagen, the Creation Kit, or xEdit.
