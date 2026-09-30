# skyrim-python v1 foundation (M0 spikes + M1 dispatcher): plan index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **How to use this plan:** it is split into one file per task. To do a task, read **this file** (overview, Global Constraints, Fixed paths) and **that task's file** only. Each task file lists the exact names and types it consumes from earlier tasks and produces for later ones (its **Interfaces** block), so earlier task files are not required reading. Open another task's file only if an Interfaces line is unclear. The design spec is background, not required reading for a single task.

**Goal:** Settle the unverified premises with spikes (M0), then build the loopback RPC plugin, generic Papyrus dispatcher and synchronous Python client (M1), so a Linux process can call any Papyrus function in the running game.

**Architecture:** A pure C++20 core library (`core/`: framing, JSON messages, value codec, form keys, config) is unit-tested on Linux and linked into the SKSE plugin (`plugin/`, CommonLibSSE-NG, built on the Windows box). The plugin runs one Winsock IO thread; every request becomes one `AddTask` on the main thread, which dispatches through the Papyrus VM; the VM callback hands the result back. A stdlib-only Python package (`python/src/skyrim`) speaks the same protocol, pinned by shared golden fixtures in `protocol/v1/`.

**Tech Stack:** C++20 (core, g++ 11 on Linux / MSVC v143), C++23 (plugin), CommonLibSSE-NG (alandtse `ng`, git submodule), nlohmann/json 3.11.3 and doctest 2.4.11 (vendored single headers), spdlog (vcpkg), Python ≥3.11 stdlib + pytest (dev only), uv.

**Spec:** `docs/specs/2026-09-30-skyrim-python-design.md` (draft 3). Background for whoever coordinates the plan (sections 4.1–4.5, 4.7, 5, 9 matter most); a worker doing one task does not need to read it.

**Scope of this plan:** M0 and M1 only. M1.5 (test ESP + engine probes), M2 (events, lifecycle, save/load, harness) and the skyrim-dm acceptance test get their own plan after M0's results, because spikes S2, S4, S5 and S6 decide parts of their design (see "After this plan" at the end).

## Global Constraints

- Transport: TCP bound to `127.0.0.1` only; default port `45977`; non-loopback peers refused.
- Framing: 4-byte little-endian length + UTF-8 JSON; max frame 16 MiB (`16 * 1024 * 1024` bytes).
- Protocol version `[1, 0]`; a major mismatch is refused.
- Token: 128-bit random, lowercase hex (32 chars), checked on every connection.
- `allow_external_clients` default `false` in the shipped ini; the dev ini sets `true`.
- Papyrus Int is 32-bit signed: out-of-range is `type_mismatch`, never truncation.
- No raw engine pointers kept across tasks; every task re-resolves by FormID.
- One `AddTask` per request or batch step, plus at most one more to convert a result when the VM callback runs off the main thread; a task never re-queues itself.
- `game.load` / `game.save` use `AddUITask` (M2; not in this plan).
- No work in `DllMain`; server thread starts at `kPostLoad`; game requests allowed only after `kDataLoaded` and the interactive signal.
- CommonLibSSE-NG: `alandtse/CommonLibSSE-NG`, branch `ng`, pinned submodule at a commit that supports 1.7.99/1.7.104 and Address Library format 5. Never use skyrim-re-toolkit's vendored copy.
- Verified runtime: AE 1.6.1170 under GE-Proton (`~/Games/SimpleSkyrim`). Release notes list only verified runtimes.
- Python client: ≥3.11, stdlib only at runtime; sync API only in v1.
- Licence: MIT. Copy no code from Chronicle (AGPL), SkyrimPlatform or DevBench (GPL). IntelEngine (Apache-2.0) and SkyLink (no LICENSE file) are read-only references.
- Commits: conventional style, no AI attribution lines (global rule; overrides harness trailers). Never `git push` without the owner saying so.
- Never delete files with `rm`; use `gio trash` (global rule).

## Review Focus

1. **Client disconnects with calls in flight** → the plugin must drop the late results quietly and keep running (no crash, no leak of the connection object). Pinned in Task 12 (outbox and limiter tests) and Task 19 (live test).
2. **A request while the game is at the main menu or loading** → `not_ready` with a `reason`, immediately, never a hang or crash. Pinned in Task 17 (`livetest/test_m1_mainmenu.py`).
3. **Wrong arguments** (Int out of range, string where Int expected, wrong argument count, unknown script or function) → `type_mismatch` / `unknown_function` before dispatch, with the parameter named in the message. Pinned in Tasks 10, 17.
4. **Garbage or oversized frames** from a client → that connection is closed, others and the game unaffected. Pinned in Task 9 (decoder test) and Task 16 (`livetest/test_m1_transport.py`).
5. **Stale session file** (game restarted, old token/port) → the client raises `Unauthorized` or a clear connection error within its connect timeout, never hangs. Pinned in Task 14.

---

## File Structure

```
skyrim-python/
  LICENSE                               MIT
  README.md                             (update in Task 20)
  .gitignore  .gitmodules
  third_party/nlohmann/json.hpp         vendored, MIT
  third_party/doctest/doctest.h         vendored, MIT
  protocol/
    PROTOCOL.md                         human-readable protocol v1
    v1/fixtures/*.json                  golden fixtures shared by C++ and Python tests
  core/                                 pure C++20; no Windows or CommonLib headers
    CMakeLists.txt
    include/spy/{framing,errors,values,refs,messages,config,outbox}.h   (outbox.h also holds InflightLimiter)
    src/{framing,errors,values,refs,messages,config,outbox}.cpp
    tests/{main,test_framing,test_values,test_refs,test_messages,test_config,test_outbox,test_fixtures}.cpp
  plugin/                               SKSE plugin (Windows build only)
    CMakeLists.txt  vcpkg.json  build.ps1
    extern/CommonLibSSE-NG              git submodule
    config/SkyrimPython.ini             shipped defaults (external clients off)
    config/SkyrimPython.dev.ini         dev defaults (external clients on, fixed token)
    src/main.cpp                        SKSE entry, logging, messaging listener
    src/Log.{h,cpp}                     spdlog setup; Log helpers usable from Winsock TU
    src/Settings.{h,cpp}                load ini via core::parse_config, session file
    src/Lifecycle.{h,cpp}               data_ready / loading / interactive / epoch, menu sink
    src/Wire.h                          json -> frame (invalid UTF-8 replaced, oversize -> error)
    src/Server.{h,cpp}                  Winsock IO thread, connections, handshake, session file (no RE headers)
    src/Dispatch.{h,cpp}                request routing, AddTask, timeouts, caps
    src/PapyrusValues.{h,cpp}           spy::Value <-> RE::BSScript::Variable, refs <-> forms
    src/Signatures.{h,cpp}              find a script function and check arguments against it
    src/Ops.{h,cpp}                     op handlers (call_static, call_method, properties, batch, resolve, state)
                                        (per-connection reply routing and caps are core's Outbox/InflightLimiter)
  spikes/
    spike-dll/{CMakeLists.txt,vcpkg.json,build.ps1,src/main.cpp,src/Net.cpp,src/Net.h}
    spike-dll/papyrus/SkyrimPython.psc  native Send declaration for S5
    spike_client.py
    RESULTS.md
  python/
    pyproject.toml
    src/skyrim/{__init__,_framing,errors,values,refs,discovery,client}.py
    tests/{support,conftest,test_framing,test_values_refs,test_fixtures,test_discovery,test_client}.py
  tools/
    remote_build.sh                     sync to Windows box, build a target, copy DLL back
    deploy.sh                           copy DLL + ini into SimpleSkyrim Stock Game
    launch_game.sh                      GE-Proton launch via skse64_loader.exe
    env.sh                              exports SKYRIM_PYTHON_SESSION for the SimpleSkyrim prefix
  livetest/
    conftest.py  support.py  test_m1_transport.py  test_m1_mainmenu.py  test_m1_calls.py  test_m1_batch_props.py
    test_m1_robustness.py  test_m1_throughput.py  results/throughput.json
  docs/specs/  docs/superpowers/plans/  docs/research/
```

Fixed paths used throughout:
- Windows box: `geoff@192.168.0.211`, checkout root `C:\dev\skyrim-python`, vcpkg `C:\vcpkg`, VS2022 Build Tools.
- Game: `~/Games/SimpleSkyrim/Stock Game` (plugins in `Data/SKSE/Plugins/`), Proton prefix `~/.local/share/Steam/steamapps/compatdata/4190904831`.
- Session file (inside the prefix): `.../compatdata/4190904831/pfx/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE/SkyrimPython/session.json`.

---

## Tasks

M0 tasks are sequential (each spike extends the same spike DLL). M1 tasks 8–14 are pure Linux work (C++ core and Python client) and need no game; tasks 15–19 need the Windows build box and a running game. "Lines" is the size of the task file.

| # | Task | Milestone | Runs on | Depends on | Lines |
|---|---|---|---|---|---|
| 0 | [Repository bootstrap](task-00-repository-bootstrap.md) | M0 | Linux | — | 85 |
| 1 | [Build tooling and a spike DLL that loads](task-01-build-tooling-and-a-spike-dll-that.md) | M0 | Windows box + game | 0 | 272 |
| 2 | [Spike S1 — Linux connects to a socket bound inside Proton](task-02-spike-s1-linux-connects-to-a-socket.md) | M0 | Windows box + game | 1 | 71 |
| 3 | [Spike S2 — which thread runs the VM callback; dispatch from inside it; argument ownership](task-03-spike-s2-which-thread-runs-the-vm.md) | M0 | Windows box + game | 2 | 110 |
| 4 | [Spike S4 — bind a quest alias, `ForceRefTo` it, read it back](task-04-spike-s4-bind-a-quest-alias-forcerefto.md) | M0 | Windows box + game | 3 | 87 |
| 5 | [Spike S5 — mod events and a native Papyrus function reach C++](task-05-spike-s5-mod-events-and-a-native.md) | M0 | Windows box + game | 4 | 96 |
| 6 | [Spike S6 — load a save through `AddUITask` under Proton](task-06-spike-s6-load-a-save-through-adduitask.md) | M0 | Windows box + game | 5 | 58 |
| 7 | [Protocol v1 document and golden fixtures](task-07-protocol-v1-document-and-golden-fixtures.md) | M1 | Linux | 2 (S1 PASS) | 162 |
| 8 | [Core library scaffold (Linux build and test runner)](task-08-core-library-scaffold-linux-build-and-test.md) | M1 | Linux | 0 | 61 |
| 9 | [Framing](task-09-framing.md) | M1 | Linux | 8 | 176 |
| 10 | [Errors and the value codec](task-10-errors-and-the-value-codec.md) | M1 | Linux | 9 | 405 |
| 11 | [Form keys and the fingerprint rule](task-11-form-keys-and-the-fingerprint-rule.md) | M1 | Linux | 10 | 165 |
| 12 | [Messages, op-argument validation, config, outbox, in-flight limiter, fixture sweep](task-12-messages-op-argument-validation-config-outbox-in.md) | M1 | Linux | 7, 11 | 672 |
| 13 | [Python package basics: framing, errors, values, form keys](task-13-python-package-basics-framing-errors-values-form.md) | M1 | Linux | 7 | 543 |
| 14 | [Python discovery and the synchronous client](task-14-python-discovery-and-the-synchronous-client.md) | M1 | Linux | 13 | 711 |
| 15 | [Plugin skeleton: build, logging, settings, lifecycle](task-15-plugin-skeleton-build-logging-settings-lifecycle.md) | M1 | Windows box + game | 6 (all spike results recorded), 12 | 381 |
| 16 | [Loopback server: IO thread, handshake, session file](task-16-loopback-server-io-thread-handshake-session-file.md) | M1 | Windows box + game | 14, 15 | 570 |
| 17 | [Dispatcher, Papyrus value conversion, signatures, and the first ops](task-17-dispatcher-papyrus-value-conversion-signatures-and-the.md) | M1 | Windows box + game | 16; spike results S2, S4 | 982 |
| 18 | [Properties, batch, and the in-flight caps](task-18-properties-batch-and-the-in-flight-caps.md) | M1 | Windows box + game | 17 | 187 |
| 19 | [Robustness and throughput baseline](task-19-robustness-and-throughput-baseline.md) | M1 | Windows box + game | 18 | 163 |
| 20 | [README and milestone tag](task-20-readme-and-milestone-tag.md) | M1 | Linux (repo) | 19 | 67 |

---

## After this plan

Write the next plan when this one is done and `spikes/RESULTS.md` is final. It covers, per spec section 9:

1. **M1.5: test ESP and engine probes.** Choose the authoring tool (Mutagen from C#, the Creation Kit on the Windows box, or xEdit scripts); author `SkyrimPythonTest.esp` with the shell quest (≥3 reference aliases, 1 location alias, stages 10/20/100/200, one objective) and a quest script with an auto property, a full property, `SetupContract(...)` and stage fragments calling `SkyrimPython.Send`. Add the property happy-path live tests deferred from Task 18. Run the three probes (three alias tags in one objective line; PO3 `SetObjectiveText` with alias tags; objective text and alias fills after save and reload) and write `docs/probes/`.
2. **M2: events and lifecycle.** Event bus with per-connection bounded queues and `dropped` counts; `events.subscribe/unsubscribe` with filters; the v1 event list in spec 4.6; mod events and `SkyrimPython.Send` (design per S5); `game.load`/`game.save` via `AddUITask` (per S6); the client's event dispatch thread, `game.events.wait`, `on_*` helpers; a live-test harness that loads a fixed save before each module; `require_unpaused` live test by opening a menu from the harness. Also place the spec 4.3 ops that no milestone names yet: `native.actors_near` and `native.console` (off by default; `enable_console` already exists in the config) — build them in M2 only if skyrim-dm's acceptance test needs them, otherwise move them to M3 in the spec.
   Also: move result encoding (`json::dump` + framing) off the main thread — post `json` to the outbox and encode on the IO thread — so a large array result does not cost the game a frame (M1 encodes on whichever thread finishes the call).
3. **skyrim-dm acceptance test** (spec section 8), using the generic call with every argument passed, with skyrim-dm depending on the `skyrim` package.

M3 (`pscgen`, typed bindings, client-side defaults) and M4 (script host, packaging) follow v1 with their own plans.
