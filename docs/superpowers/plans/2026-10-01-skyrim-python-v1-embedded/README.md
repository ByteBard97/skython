# Skython v1 (M0 spikes + M1 embedded dispatcher): plan index

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **How to use this plan:** it is split into one file per task. To do a task, read **this file** (overview, Global Constraints, File Structure, Contracts) and **that task's file** only. Each task file has an **Interfaces** block naming what it consumes and produces; the Contracts section below is the single list of cross-task names, so earlier task files are not required reading. The design spec is background, not required reading for a single task.

**Goal:** Settle the unverified premises with spikes (M0), then build the SKSE plugin that embeds CPython, the generic Papyrus dispatcher behind the built-in `_skython` module, the pure-Python `skython` package, and the dev bridge that lets a Linux process use the same API (M1).

**Architecture:** A pure C++20 core (`core/`: values, form keys, request parsing, config, lifecycle state, and the `Dispatcher` with its `Backend` interface) is unit-tested on Linux. The `_skython` module (`pyglue/`, pybind11) passes only request/response JSON text between Python and the `Dispatcher`; on Linux it is built against uv's CPython 3.14 with a `FakeBackend`, in the plugin with the `GameBackend` that queues one `AddTask` per request and dispatches through the Papyrus VM. The plugin (`plugin/`, CommonLibSSE-NG, built on the Windows box) is two DLLs: a loader that SKSE loads, which loads CPython 3.14.8 and then the core DLL by full path from the plugin's folder, and the core, which links CPython and at `kDataLoaded` starts a host thread that initialises the interpreter and runs `skython._host`, which loads player scripts and, in dev builds, the dev bridge. The pure-Python `skython` package (`python/src/skython`) offers one `Game` API over two transports: in-process (`_skython`) and remote (`skython.remote` over the bridge), pinned by golden fixtures in `protocol/v1/`.

**Tech Stack:** C++20 (core, pyglue; g++ 11 on Linux / MSVC v143), C++23 (plugin), CommonLibSSE-NG (alandtse `ng`, submodule), nlohmann/json 3.11.3, doctest 2.4.11 and pybind11 3.1.0 (vendored), spdlog (vcpkg), CPython 3.14.8 (embeddable zip at runtime; NuGet `python` 3.14.8 headers and import library at build time), Python ≥3.12 for the `skython` package and its tests, pytest, uv.

**Spec:** `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4). Sections 4.1, 4.2, 4.5, 4.7, 4.10 and 9 matter most to whoever coordinates the plan.

**Scope of this plan:** M0 and M1 only. M1.5 (test ESP, engine probes), M2 (events, lifecycle, save/load, harness) and the skyrim-dm acceptance test get their own plan after M0's results (see "After this plan").

**Lineage:** this plan replaces `docs/superpowers/plans/2026-09-30-skyrim-python-v1-foundation/` (the out-of-process design). The reviewed and build-box-verified version of that plan is on git branch `plan-out-of-process`; many task files here adapt one of its tasks, named in each task file's first line ("Adapted from: branch `plan-out-of-process`, task N"). Read it with `git show plan-out-of-process:<path>`. `API-VERIFICATION.md` in this folder is copied from it unchanged: it holds the real CommonLibSSE-NG signatures, checked against the pinned clone and compiled on the build box.

## Global Constraints

- CPython **3.14.8**, standard GIL build, x64. Runtime: the unmodified `python-3.14.8-embed-amd64.zip`. Build inputs: `tools/include/` and `tools/libs/python314.lib` from the NuGet package `python` 3.14.8. Both fetched by `tools/fetch_python.sh` and checked against SHA-256 values recorded in `docs/PINS.md`. Never a free-threaded build.
- pybind11 **3.1.0**, vendored under `third_party/pybind11/` (headers only).
- **SKSE tasks, the game's own threads and the Papyrus VM's threads never execute Python and never take the GIL.** Event sinks and VM callbacks produce C++ values and JSON text only.
- **"Main thread" in the Part C task files means "inside an SKSE task"** (ruling R49, measured in spike S7, Task 9): `AddTask`/`AddUITask` tasks are serialized (observed: 0 overlapping in about 2800 probed tasks, not proven) and run on varying BSJobs job threads; the window thread (`RE::Main::threadID`, the plugin-load thread) runs none. Never identify "the main thread" by thread id.
- **Two DLLs** (spec 4.1). `python314.dll` exports data symbols, so it cannot be delay-loaded (MSVC LNK1194). The loader `Data/SKSE/Plugins/Skython.dll` has no Python headers or imports; in `SKSEPlugin_Load` it checks the runtime, checks `GetModuleHandleW(L"python314.dll")` (a module of that name from another path → Python disabled, the log names that path), then `LoadLibraryExW(<plugin dir>\Skython\python\python314.dll, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH)`, then `LoadLibraryExW(<plugin dir>\Skython\SkythonCore.dll, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH)`, then calls the core's export `extern "C" __declspec(dllexport) bool SpyCore_Load(const SKSE::LoadInterface*)`. Every failure is logged and the loader still returns `true` (the game runs without Python). The core links `python314.lib` normally and holds everything that touches Python, pybind11 or the game.
- CPython is initialised once, on the host thread, with `Py_InitializeFromConfig` and an isolated `PyConfig` (`isolated = 1`, `use_environment = 0`, `install_signal_handlers = 0`, `site_import = 1`, explicit `home`, `module_search_paths_set = 1`; the zip's `python314._pth` is then ignored). **Never `Py_Finalize`.** After `skython._host.main()` returns, the host thread calls `PyEval_SaveThread()` and blocks forever on a condition variable; it never ends while holding the GIL. That mutex and condition variable are deliberately leaked (`new`, never deleted, never static objects): a static one is destroyed at process exit while the host thread still waits on it, which hung exit in the Linux test of spike S8.
- No work in `DllMain`; the host thread starts at `kDataLoaded`; game calls run only after `kDataLoaded` and the interactive signal.
- The boundary between Python and C++ is request/response JSON text plus a few scalars (`_skython` Contract below). No `PyObject` is converted in C++ beyond `str`, `int`, `float`, `None`.
- **Papyrus dispatch rules from spike S2 (box, 2026-10-01):** (1) never call `Dispatch*Call` with a class or function that was not first found with `FindFunction` and argument-checked: an unresolvable target crashes the engine (`SkyrimSE.exe+1447D3E`, null deref); (2) the VM never frees `IFunctionArguments`: the plugin owns them, frees them in the VM callback after reading the result, never earlier (measured: phase D, 1000/1000); when `Dispatch*Call` returns false the plugin does NOT free them (unmeasured: the callback might still fire, so freeing could double-free) but logs it once as a leak — with rule (1) a false return is not expected at all; (3) the callback may run inside an SKSE task or on a VM thread: never assume either, and never test the thread id to tell (R49).
- One `AddTask` per request or batch step, plus at most one more to convert an object result (a form, an alias or an array of objects): none when the VM callback runs synchronously inside our own task (a `thread_local` flag our tasks set, R52; never a thread-id test, R49), one otherwise; the work runs in an SKSE task: serialized (observed), on varying job threads; the window thread runs none (R49). A task never re-queues itself.
- `game.load` / `game.save` use `AddUITask` (M2; not in this plan).
- Dev bridge: TCP bound to `127.0.0.1` only, default port `45977`, non-loopback peers refused, 128-bit random token as 32 lowercase hex characters, framing 4-byte little-endian length + UTF-8 JSON, max frame 16 MiB, protocol `[1, 0]` (major mismatch refused). `bridge_enabled = false` in the shipped ini, `true` in the dev ini.
- Papyrus Int is 32-bit signed: out of range is `type_mismatch`, never truncation. Papyrus arrays hold at most 128 elements.
- No raw engine pointers kept across tasks; every task re-resolves by FormID.
- No exception may escape a thread the plugin owns: every main-thread task, VM callback, sink, the reaper thread and the host thread catch `std::exception` and `...`; a failure becomes an `internal` error or disables Python, never a game crash. Every Python entry point called by the plugin or `skython._host` is wrapped in Python and logs the traceback.
- JSON text that leaves C++ is produced only by core's `spy::dump_json` (invalid UTF-8 replaced, never thrown). Request text from Python is checked with `spy::nesting_within` (depth 64) before it is parsed.
- CommonLibSSE-NG: `alandtse/CommonLibSSE-NG`, branch `ng`, pinned submodule at `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (v10.1.0). Use the signatures in `API-VERIFICATION.md`, not memory. Never use skyrim-re-toolkit's vendored copy. Known MSVC pitfalls from the build box: define `NOMINMAX` and `WIN32_LEAN_AND_MEAN`; `#undef GetObject` after the includes in every TU that calls `Variable::GetObject()`; never call `TypeInfo::GetTypeInfo()` (or `TypeAsString()`) on an object-array type: at the pin it masks with `~0xB` and returns a class pointer 8 bytes off (crashed the game in S7); use `raw & ~1` for an object array's class; `REL::Module::get().version().string(".")`; `SKSE::Init(skse, { .log = false })`.
- Verified runtime: AE 1.6.1170, Windows-native on the build box (ruling R51; Proton deferred until the owner says the Linux machine is free). The plugin refuses to start Python on an unsupported runtime (log the version and why). Release notes list only verified runtimes.
- `skython` package: Python ≥3.12, standard library only at runtime; tested on 3.12 and 3.14. Sync API only in v1.
- Licence: MIT. Copy no code from Chronicle (AGPL), SkyrimPlatform or DevBench (GPL). IntelEngine (Apache-2.0) and SkyLink (no LICENSE file) are read-only references. Bundle the CPython licence (`LICENSE.txt` from the embeddable zip) with any deployed runtime.
- Commits: conventional style, no AI attribution lines (global rule; overrides harness trailers). Never `git push` without the owner saying so.
- Never delete files with `rm`; use `gio trash` only after the owner approves an itemised list (global rule). Applies to the Windows box too (no `Remove-Item`).
- The Windows build box's default SSH shell is Windows PowerShell 5.1 (not cmd.exe); every remote command is PowerShell syntax, every ssh/scp call has a timeout and `BatchMode`, every wait loop is bounded and prints the log tail on timeout.
- The RTX 5080 is reserved for TTS: no step may use or reconfigure the GPU.
- The game runs on the Windows box (Task 1b): no step launches the game on the Linux desktop, uses xdotool, or starts Linux Steam, unless the owner says the Linux machine is free. On the box, our files go only under `Data\SKSE\Plugins` (DLLs, `Skython\`), the one file `Data\Scripts\SkyrimPython.pex`, and `C:\dev\skyrim-python-box`; never modify ChronicleDev's existing files, tasks or saves.

## Review Focus

1. **A Python exception or a hung Python script** → the game keeps running; the traceback is in `python.log`; other scripts and the bridge keep working. Pinned in Task 24 (`test_host.py`: a script that raises in import, one that raises in `setup`, one whose `setup` blocks) and Task 31 (live).
2. **A call while the game is at the main menu or loading** → `NotReady` with a `reason`, immediately, in both modes; never a hang. Pinned in Task 18 (dispatcher tests), Task 19 (`_skython` with the fake backend's not-ready switch) and Task 27 (live).
3. **Wrong arguments** (Int out of range, string where Int expected, wrong count, unknown script or function) → `TypeMismatch` / `UnknownFunction` before dispatch, naming the parameter. Pinned in Tasks 14, 16, 27, 28.
4. **A bridge client disconnects with calls in flight, or sends garbage or an oversized frame** → that connection closes, its outstanding tickets are cancelled; the game, other connections and in-process scripts are unaffected; late results are dropped. Pinned in Task 23 (Linux, real bridge over the fake `_skython`) and Task 31 (live).
5. **Python loads but cannot start** (DLL missing, another `python314.dll` already loaded, the core DLL missing, `skython._host` import error, unsupported runtime) → the game runs normally with Python disabled and one clear log line. Pinned in Task 26 (live variants).
6. **Many Python threads calling at once, or CPU-bound Python** → no deadlock, no frame stalls (the GIL is released while waiting; the game thread never runs Python); GIL hand-off latency is measured, not assumed. Deadlock freedom is pinned in Task 19 (16 threads on the fake backend) and Task 31 (live throughput with a spinning thread, which also measures call latency with and without the spinner); frame stalls are pinned by spike S9 (Task 5) only.
7. **One source of truth for the protocol** → error codes, ops, limits, config keys and client defaults are generated from `protocol/protocol.json` for C++ and Python; hand-edits of generated files fail a drift check. Pinned in Task 12.

---

## File Structure

```
skyrim-python/
  LICENSE                                   MIT
  README.md                                 (rewritten in Task 32)
  .gitignore  .gitmodules
  docs/PINS.md                              CommonLib commit, CPython/NuGet/pybind11 versions and SHA-256s (Task 0)
  third_party/nlohmann/json.hpp             vendored, MIT
  third_party/doctest/doctest.h             vendored, MIT
  third_party/pybind11/include/pybind11/    vendored headers, BSD-3 (+ LICENSE)
  third_party/cpython/                      GITIGNORED: fetched by tools/fetch_python.sh
    embed/                                  python-3.14.8-embed-amd64.zip unpacked
    nuget/                                  python.3.14.8.nupkg unpacked (tools/include, tools/libs)
  protocol/
    protocol.json                           SINGLE SOURCE OF TRUTH (Task 12)
    PROTOCOL.md  PROTOCOL.generated.md      rules (Task 11) and generated tables (Task 12)
    v1/fixtures/*.json                      golden fixtures (Task 11)
  core/                                     pure C++20; no Windows, CommonLib or Python headers
    CMakeLists.txt
    include/spy/{protocol_generated,errors,values,refs,requests,config,json_depth,lifecycle_state,dispatcher,fake_backend}.h
    src/{errors,values,refs,requests,config,json_depth,lifecycle_state,dispatcher,fake_backend}.cpp
    tests/{main,test_values,test_refs,test_requests,test_config,test_json_depth,test_lifecycle_state,test_dispatcher,test_fake_backend,test_fixtures,test_generated}.cpp
  pyglue/                                   the _skython module (pybind11); no CommonLib headers
    include/spy/pymodule.h                  DefineModule + ModuleHost
    src/pymodule.cpp
    linux/CMakeLists.txt  linux/fake_module.cpp      Linux build: _skython over FakeBackend
    tests/{conftest,test_module,test_threads}.py
  plugin/                                   SKSE plugin (Windows build only), two DLLs from one CMake project
    CMakeLists.txt  vcpkg.json  build.ps1   targets Skython (loader) and SkythonCore (core)
    extern/CommonLibSSE-NG                  submodule
    loader/main.cpp                         SKSE exports; runtime check, collision check, LoadLibraryExW x2, SpyCore_Load
    config/Skython.ini                      shipped defaults (bridge off)
    config/Skython.dev.ini                  dev defaults (bridge on; token random per run, read from session.json; no token is committed)
    config/variants/*.ini                   live-test variants
    src/main.cpp                            SpyCore_Load export: SKSE::Init, messaging listener, startup order
    src/Log.{h,cpp}  src/Settings.{h,cpp}  src/Lifecycle.{h,cpp}
    src/PythonHost.{h,cpp}                  PyConfig, Py_InitializeFromConfig, host thread
    src/PyModule.cpp                        PYBIND11_MODULE(_skython) for the game build
    src/GameBackend.{h,cpp}                 spy::Backend over AddTask + ops::Run
    src/Ops.{h,cpp}  src/PapyrusValues.{h,cpp}  src/Signatures.{h,cpp}
  python/
    pyproject.toml
    src/skython/{__init__,_protocol_generated,_framing,errors,values,refs,game,inprocess,discovery,remote,bridge,_host}.py
    tests/{support,conftest,fake_skython,test_framing,test_errors,test_values_refs,test_fixtures,test_generated,test_game,test_inprocess,test_discovery,test_remote,test_bridge,test_host}.py
  spikes/
    spike-dll/{CMakeLists.txt,vcpkg.json,build.ps1,src/main.cpp,src/Net.cpp,src/Net.h,src/PyLoad.cpp,src/PyLoad.h}
    spike-pycore/{CMakeLists.txt,src/PyCore.cpp,linux_check.cpp}   SpikePyCore.dll: links python314.lib; init, run code, _spike module (S8, S9); linux_check drives the same exports on Linux
    py/*.py  py_send.py  s9_analyze.py                                S8 echo server / sender, S9 thread test and analysis
    spike-dll/papyrus/SkyrimPython.psc
    spike_client.py  py_echo_client.py  RESULTS.md        (S8's Python echo server listens on 127.0.0.1:45979; the harness owns 45978)
    deps/requirements-game.txt
  tools/
    fetch_python.sh  gen_protocol.py  test_gen_protocol.py
    remote_build.sh  deploy.sh  launch_game.sh  env.sh  wait_for_log.sh  teardown_list.sh
  livetest/
    conftest.py  support.py
    smoke.py                                helpers to drive the in-process smoke script (Task 31)
    test_m1_bridge.py  test_m1_startup_variants.py  test_m1_calls.py  test_m1_mainmenu.py
    test_m1_batch_props.py  test_m1_robustness.py  test_m1_throughput.py  results/throughput.json
    scripts/livetest_smoke/__init__.py      in-process smoke script deployed to scripts/
    scripts/livetest_broken/__init__.py     a script whose setup raises (startup variant test)
```

Fixed paths:
- Windows box: `geoff@192.168.0.211`, checkout root `C:\dev\skyrim-python`, vcpkg `C:\vcpkg`, VS2022 Build Tools. Game on the box: `C:\ChronicleDev\Stock Game` (1.6.1170.0, SKSE 2.2.6), logs `C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE`, harness folder `C:\dev\skyrim-python-box`, launched by our scheduled task `SkyrimPythonGame` (Task 1b).
- Game: `~/Games/SimpleSkyrim/Stock Game`; plugin files under `Data/SKSE/Plugins/`. Proton prefix `~/.local/share/Steam/steamapps/compatdata/4190904831`. (Proton paths; Part C uses Task 1c's mirror)
- Deployed layout (Task 26's `tools/deploy.sh`): `Data/SKSE/Plugins/Skython.dll` (loader), `Data/SKSE/Plugins/Skython.ini`, `Data/SKSE/Plugins/Skython/SkythonCore.dll`, `.../Skython/python/` (embeddable zip), `.../Skython/site-packages/`, `.../Skython/lib/skython/`, `.../Skython/scripts/`. SKSE loads only DLLs directly in `Plugins/`, so nothing in the `Skython/` subfolder is loaded by SKSE.
- Logs (inside the prefix, `.../pfx/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE/`): C++ `Skython.log`; Python `Skython/python.log`; bridge session file `Skython/session.json`. (Proton paths; Part C uses Task 1c's mirror)
- Linux Python for builds and tests: `uv python install 3.14 3.12` (uv offers 3.14.7 locally; the exact versions are recorded in `docs/PINS.md`). The `_skython` Linux build finds it with `uv python find 3.14`. The in-game runtime stays 3.14.8 (same ABI).

---

## Contracts (cross-task names; every task uses exactly these)

### C++ core (`namespace spy`, `core/include/spy/`)
- Generated (Task 12) `protocol_generated.h`: `kProtocolMajor/Minor`; limits `kMaxFrameBytes`, `kPreAuthFrameBytes`, `kMaxNameBytes`, `kMaxBatchCalls`, `kMinTimeoutMs`, `kMaxTimeoutMs`, `kMaxErrorMessageBytes`, `kMaxClientNameBytes`, `kMaxConnections`, `kHelloTimeoutMs`, `kMaxJsonDepth` (64), `kTokenLength`, `kIntMin`, `kIntMax`, `kFloatMaxText`, `kMaxArrayItems` (128); `enum class ErrorCode` + `kErrorCodeNames`; `kNotReadyReasons`, `kAliasTypes`, `kDefaultAliasType`, `kArrayElemTypes`; `namespace form_ids`; op table `kOps`/`find_op`/`ops::k*` (`OpSpec{name, batchable, needs_game, args, exactly_one_of}`, `ArgSpec{name, type, required}`; `needs_game` is false only for `game.state`; op constants `kGameState`, `kFormResolve`, `kPapyrusCallStatic`, `kPapyrusCallMethod`, `kPapyrusGetProperty`, `kPapyrusSetProperty`, `kPapyrusBatch`); `kConfigKeys` (`ConfigKey{name, type, min, max, default_text}`), `find_config_key`, `config_defaults::{kPythonEnabled, kScriptsEnabled, kBridgeEnabled, kPort, kFixedToken, kSessionFile, kDefaultTimeoutMs, kMaxInflightPerConnection, kMaxInflightTotal, kEnableConsole}`.
- `errors.h` (Task 14): `ErrorCode` re-export, `to_string(ErrorCode)`, `error_code_from_string`, `struct ProtocolError : std::runtime_error { ErrorCode code; }`, `clip_text`, `using json = nlohmann::json`, `dump_json(const json&, int indent = -1)`, `str_or`, `describe`.
- `values.h` (Task 14): `Fingerprint`, `FormRef`, `AliasRef`, `ArrayValue`, `Value`, `value_from_json`, `value_to_json`, `form_ref_from_json/to_json`, `alias_ref_from_json/to_json`, `value_kind`, `bool is_uint(const json&, std::uint64_t max)`. (As on branch `plan-out-of-process`, Task 10, plus: an array with more than `kMaxArrayItems` items is `type_mismatch`, in both directions of this codec. The 128 limit therefore applies to arguments; the plugin's Papyrus results (`pv::FromVariable`, Task 28) do not go through `value_to_json` and pass longer arrays through. Every Float the plugin sends is encoded by `value_to_json`: shortest round-trip text, non-finite as `null`.)
- `refs.h` (Task 15): `FormKey`, `parse_form_key`, `format_form_key`, `is_valid_form_key`, `is_runtime_form_id`, `is_light_form_id`, `local_form_id`, `form_key_matches_id`, `RefStatus`, `check_fingerprint`. (As branch Task 11.)
- `json_depth.h` (Task 16): `bool nesting_within(std::string_view text, std::size_t max_depth)` (raw-text scan, O(n), strings and escapes respected).
- `requests.h` (Task 16): every request object carries `"type": "request"` (both modes; the Python `Game` always sends it); `struct Request { std::uint64_t id; std::string op; json args; }`; `Request parse_request(const json&)`; `Request parse_request_text(std::string_view text)` (depth check, then parse, then `parse_request`; throws only `ProtocolError`); `CallArgs`/`parse_call_args`, `PropertyArgs`/`parse_property_args`, `BatchStep`/`BatchArgs`/`parse_batch_args` (as branch Task 12); `json make_ok(std::uint64_t id, json result)`; `json make_error(std::uint64_t id, ErrorCode, std::string_view message, json detail = json::object())`; `const std::vector<std::string>& known_ops()`; `std::optional<std::uint64_t> peek_request_id(std::string_view text)` (best-effort id for error replies to unparseable text; nullopt → id 0).
- `config.h` (Task 16): `struct Config { bool python_enabled; bool scripts_enabled; bool bridge_enabled; std::uint16_t port; std::string fixed_token; std::string session_file; std::uint32_t default_timeout_ms; std::uint32_t max_inflight_per_connection; std::uint32_t max_inflight_total; bool enable_console; std::vector<std::string> ignored_keys; }` (defaults from `config_defaults`: true, true, false, 45977, "", "", 5000, 256, 1024, false); `Config parse_config(std::string_view ini_text)` (throws `std::invalid_argument`); `json config_to_json(const Config&)`.
- `lifecycle_state.h` (Task 17): `LifecycleState` with `on_data_loaded()`, `on_pre_load_game()`, `on_post_load_game(bool success)`, `on_new_game()`, `on_menu(const std::string& name, bool opening)`, `set_paused(bool)`; queries `data_ready()`, `game_loaded()`, `loading()`, `interactive()`, `paused()`, `epoch()`, `loads_started()` (counts world resets: every `kPreLoadGame`, failed or not, and every Main Menu open while a game was loaded; never new games), `not_ready_reason(bool require_unpaused = false) -> std::optional<std::string>` (`"starting"`, `"loading"`, `"no_game"`, then `"paused"` only when `require_unpaused`), `open_menus()` (in the order they opened, the last one on top), `base_state() -> json` (`data_ready, game_loaded, loading, interactive, paused, menus, epoch`). Thread-safe (one mutex).
- `dispatcher.h` (Task 18):
  ```cpp
  struct OpResult { bool ok; json result; ErrorCode code; std::string message; json detail;
                    static OpResult Ok(json); static OpResult Err(ErrorCode, std::string, json detail = json::object()); };
  using Done = std::function<void(OpResult)>;       // call exactly once, from any thread
  using Cancelled = std::function<bool()>;          // true once the request timed out, was cancelled, or a world reset (save load / quit to main menu) started
  using Progress = std::function<void(json)>;       // batch: partial results so far, any thread
  class Backend {
   public:
    virtual ~Backend() = default;
    virtual void Run(const std::string& op, const json& args, Done, Cancelled, Progress) = 0;  // must not block the caller
    virtual json BaseState() = 0;                                                              // any thread, never blocks
    virtual std::optional<std::string> NotReadyReason(bool require_unpaused) = 0;              // any thread
    virtual std::uint32_t LoadsStarted() = 0;                                                  // any thread; MUST be lock-free (an atomic read): Tick calls it while holding the Dispatcher's lock
  };
  using Ticket = std::uint64_t;
  struct DispatcherOptions { std::uint32_t default_timeout_ms = 5000; std::uint32_t max_inflight_total = 1024;
                            std::uint64_t abandon_after_ms = kMaxTimeoutMs; std::function<void(const std::string&)> on_late = nullptr; };
  class Dispatcher {
   public:
    Dispatcher(Backend&, DispatcherOptions);
    ~Dispatcher();                                      // stops the reaper
    Ticket Submit(std::string_view request_text);      // never throws; a bad request yields a ticket that is already done
    std::optional<std::string> Wait(Ticket, std::chrono::milliseconds max_wait);  // response text {id, ok, result|error}; nullopt if not done in time or unknown; a returned ticket is consumed
    std::vector<std::pair<Ticket, std::string>> WaitAny(const std::vector<Ticket>&, std::chrono::milliseconds max_wait);  // the done ones among them (consumed), as soon as at least one is done or max_wait passes
    void Cancel(Ticket);                                // erases the ticket; a later Wait on it returns nullopt; a late backend result is dropped
    std::optional<std::string> CancelOrTake(Ticket);   // the finished response if there is one, else cancels
    std::uint64_t Abandoned() const;                    // finished responses nobody collected within abandon_after_ms
    void Tick();                                        // deadlines and load cancellation (the reaper calls it; tests call it directly)
    void StartReaper(std::chrono::milliseconds period = std::chrono::milliseconds(50));
    std::size_t InFlight() const;
    std::uint64_t LateResults() const;
    json BaseState();                                   // forwards Backend::BaseState()
  };
  ```
  Timeout rule: every `timeout` and `not_ready` error's detail carries `paused` and `menus` from `BaseState()`; a batch's `timeout` detail also carries `completed` and `results` from the last `Progress` payload, whose shape is `{"completed": n, "results": [...]}`. Locking rule: `Backend::Run` and `done` callbacks are never invoked while the Dispatcher holds its lock (a backend may call `done` synchronously inside `Run`, e.g. `game.state`).
  Ticket rules: tickets come from a counter that is never reused; only a backend `Done` that loses (erased, unknown or already finished ticket) counts in `LateResults()` and calls `on_late`; responses nobody collects are dropped after `abandon_after_ms` and counted in `Abandoned()`; `Done` is idempotent per ticket (the first call wins).
  Rules: an op with `needs_game` gets `not_ready` (detail: `reason`, plus `BaseState()`'s `paused` and `menus`) at `Submit` without reaching the backend when `NotReadyReason(require_unpaused)` has a value; over `max_inflight_total` → `busy`; a request past its deadline (`timeout_ms` from its args via the generated range, else `default_timeout_ms`) → `timeout` (batch: detail `completed` and `results` from the last `Progress`); a `needs_game` request whose `LoadsStarted()` value (read at `Submit`, before the readiness check) no longer matches is `Cancelled` at once and answered `not_ready` with reason `NotReadyReason(false)` (else `loading`), `paused`, `menus`, and `dispatched: true` if it had reached `Run`; an exception from `Backend::Run` → `internal`. Responses are built with `make_ok`/`make_error` and serialised with `dump_json`. `timeout_ms` and `require_unpaused` count only for ops whose `OpSpec.args` declare them (else the default timeout and false), with `kMinTimeoutMs` as the lower bound. "In flight" means Dispatcher entries: a call cancelled or timed out may still be suspended in the VM (known limit).
- `fake_backend.h` (Task 18): `class FakeBackend : public Backend` running ops on its own worker thread. `papyrus.call_static` with `script == "Fake"`: `Echo(x)` → `x`; `Sleep(ms)` → sleeps then `ms`; `Fail(code, message)` → that error; `Throw()` → throws `std::runtime_error` inside `Run`; `Never()` → never completes; `Count()` → number of `Run` calls so far. `game.state` → `BaseState()` plus `"backend": "fake"`. Any other op or script → `unsupported`. An unknown `Fake` function is `unknown_function`. Controls: `SetNotReady(std::optional<std::string> reason)`, `BumpLoads()`, `SetPaused(bool)`; `RunCount()`.

### `_skython` module (Task 19; same API in the Linux fake build and the game build)
- `pyglue/include/spy/pymodule.h`: `struct ModuleHost { Dispatcher* dispatcher = nullptr; const char* backend = "game"; std::function<std::string()> config_json; std::function<void(const std::string& level, const std::string& msg)> log; FakeBackend* fake = nullptr; };` `void InstallModuleHost(ModuleHost)` (before the module is imported); `void DefineModule(pybind11::module_& m)`.
- Python-visible: `BACKEND: str` (`"game"` | `"fake"`), `PROTOCOL: tuple[int, int]`, `submit(request: str) -> int`, `wait(ticket: int, timeout: float) -> str | None` (GIL released), `wait_any(tickets: list[int], timeout: float) -> list[tuple[int, str]]` (GIL released), `cancel(ticket: int) -> None`, `call(request: str, timeout: float) -> str` (submit + wait; if `timeout` passes first, `CancelOrTake`: the finished response if one arrived, else a `timeout` error response for that id with `paused`, `menus` and `late_cancel = true`, so callers always see the `Timeout` class for an expired call), `state() -> str` (`BaseState()` JSON, never blocks; in the game it has the `game.state` keys except `player` and `game_days_passed`, which only the `game.state` op reports), `config() -> str` (`config_to_json` plus `python_dir`, `scripts_dir`, `site_packages_dir`, `log_dir`, `session_file`, `plugin_version`, `game_version`, `runtime`; the fake build returns `plugin_version "0.0.0-fake"`, `game_version "0.0.0"`, `runtime "fake"` and directories under a temporary folder chosen by the test), `log(level: str, message: str) -> None`, `in_flight() -> int`. Fake build only: `_fake_set_not_ready(reason: str | None)`, `_fake_bump_loads()`, `_fake_set_paused(flag: bool)`; environment `SKYTHON_FAKE_DIR` (`log_dir` = `<dir>/logs`, holding `session.json`; `log()` appends to `<log_dir>/plugin.log`) and `SKYTHON_FAKE_INI` (ini text read by `config()` on each call; the caps are read once at import).

### Python package `skython` (`python/src/skython/`)
- `_protocol_generated` (Task 12), `_framing`, `errors`, `values`, `refs` (Task 20): as branch Task 13, unchanged names (`encode`, `read_frame`, `MAX_FRAME`; `SkythonError`, `ConnectionLost`, `NotRunning`, `CallTimeout`, `ProtocolViolation`, `EncodeError`, `HandshakeError`/`Unauthorized`/`ProtocolMismatch`, `RemoteError` + `NotReady`, `Busy`, `BadRequest`, `UnknownFunction`, `TypeMismatch`, `StaleReference`, `NotFound`, `Timeout`, `VmError`, `Unsupported`, `InternalError`, `remote_error_from_wire`, `handshake_error_from_wire`; `Fingerprint`, `Form`, `Alias`, `PArray`, `to_wire`, `from_wire`; `parse_form_key`, `format_form_key`, `is_runtime_form_id`, `is_light_form_id`).
- `game` (Task 21): `class Transport(Protocol)`: `request(op: str, args: dict, wait_s: float) -> dict` (returns the decoded response object `{id, ok, result|error}`; raises `ConnectionLost`/`CallTimeout`/`EncodeError`), `close() -> None`, `welcome: dict`, `closed: bool`. `Game.transport` is public; remote-only counters (`unmatched_responses`) live on `RemoteTransport`. `wait_ready` raises `NotReady` (reason `wait_ready timed out`) on expiry; `retry_not_ready` never retries a `NotReady` whose detail has `dispatched: true` (a world reset cancelled it after it reached the game); `Batch.run` always sends `timeout_ms` (the sum of step timeouts, 5 s for a step without one, capped at `MAX_TIMEOUT_MS`). A call whose time runs out is the Dispatcher's `Timeout` in both modes; `CallTimeout` (remote only) means the bridge itself did not answer within the call timeout plus `GRACE_S`. `class Game(transport, *, call_timeout=CALL_TIMEOUT_S, retry_not_ready: bool | float = False)` with `call`, `call_method`, `get_property`, `set_property`, `batch() -> Batch`, `resolve(*, form_key=None, form_id=None, editor_id=None) -> Form`, `form(form_key) -> Form`, `alias(quest, alias_id, alias_type="ReferenceAlias") -> Alias`, `state() -> dict`, `wait_ready(timeout=READY_TIMEOUT_S) -> dict`, `close()`, attributes `welcome`, `epoch`, `closed`; context manager. `Batch` as branch Task 14. Timeout rules as branch Task 14 (seconds, rounded up to whole ms, at most `MAX_TIMEOUT_MS`; wait = timeout + `GRACE_S`).
- `inprocess` (Task 21): `class InProcessTransport` over `_skython.call`; `welcome` built from `_skython.config()` (plus `type` and `backend`); `connect_in_process(*, module=None, call_timeout=CALL_TIMEOUT_S, retry_not_ready=False) -> Game`. Also from Tasks 20-21: `_framing.dumps(obj) -> str`, `values.MAX_ARRAY_ITEMS`; `python/tests/conftest.py` (fixtures `fake`, `game`), `python/tests/fake_skython.py` and `support.wait_until` exist after Task 21 — later tasks append, never recreate.
- `discovery` (Task 22): as branch Task 14 (`Session`, `find_session`), plus `write_session_file(path, session) -> None` (atomic replace) and `write_inactive_session_file(path, reason, *, pid=None, plugin_version=None) -> None` used by the bridge and by `_host` when the bridge is off.
- `remote` (Task 22): `class RemoteTransport` (socket, hello/welcome, reader thread, futures; branch Task 14's client internals); `connect_remote(session=None, *, host=HOST, port=None, token=None, client_name=CLIENT_NAME, connect_timeout=CONNECT_TIMEOUT_S, call_timeout=CALL_TIMEOUT_S, retry_not_ready=False, send_timeout=SEND_TIMEOUT_S) -> Game`.
- `bridge` (Task 23): `class BridgeServer` with `.port`, `.token`, `.close()`, `.connections() -> int`; `serve(backend, *, host=HOST, port=45977, token=None, session_file=None, welcome_info: dict, max_connections=MAX_CONNECTIONS, max_inflight_per_connection=256, hello_timeout=HELLO_TIMEOUT_MS/1000, max_queued_bytes=MAX_QUEUED_BYTES_PER_CONNECTION) -> BridgeServer` (also `.host`; `bridge.check_hello`, `bridge.nesting_within` exported; three threads per connection: reader, completion, writer). Both the bridge and `RemoteTransport` set `TCP_NODELAY` on every socket (split writes otherwise stall about 40 ms on Linux loopback). `backend` is any object with `submit(text) -> int`, `wait_any(tickets, timeout) -> list[tuple[int, str]]` and `cancel(ticket)`: the `_skython` module in the game, the fake `_skython` in tests. One reader thread and one completion thread per connection: the reader submits each request text and records `ticket → connection`; the completion thread loops on `wait_any` (5 ms while tickets are outstanding) and writes each response text back as a frame. On disconnect every outstanding ticket of that connection is cancelled. The Dispatcher's reaper enforces each request's `timeout_ms`; the bridge never times calls out itself. `accept()` errors are logged and retried, never fatal; the depth scan is linear (no regex backtracking) because it runs on unauthenticated input. The welcome's `capabilities` is the list of op names from the generated `OPS`. Handshake rules as branch Task 16's server (first frame a valid hello within `HELLO_TIMEOUT_MS`, at most `PRE_AUTH_FRAME_BYTES` before it, constant-time token check, then protocol major), connection cap, per-connection in-flight cap → `busy`, garbage/oversize/too-deep frame closes that connection only.
- `skython.connect(session=None, **kw) -> Game` (`__init__`, Task 22): in-process when `_skython` is importable, `_skython.BACKEND == "game"` and no bridge argument is given (`session`, `host`, `port`, `token`, `client_name`, `connect_timeout`, `send_timeout`); otherwise `connect_remote(...)`.
- `_host` (Task 24): `main() -> None` (called by the plugin on the host thread; returns after setup and never raises; long-running work lives on threads it starts), `load_scripts(game, scripts_dir) -> dict[str, str]` (name → `"ok"`, `"failed: <reason>"` or `"timeout: <reason>"`; each script's import and `setup` run on their own thread and the host waits at most 10 s, so one blocking script cannot stop the rest), `reload(name) -> str`, `status() -> dict` (keys `scripts`, `bridge`, `bridge_port`, `bridge_connections`, `switch_interval_s`, `log_file`, `python`); `SWITCH_INTERVAL_S = None` (CPython default) until spike S9 recommends a value; logs to `python.log` in `_skython.config()["log_dir"]` (rotating, 1 MiB × 3); starts the bridge when `bridge_enabled`. Each script gets its own `Game`; a bridge that fails to start for any reason writes an inactive session file with the reason. Scripts run at `kDataLoaded`, before any game is loaded: game work belongs on a thread that calls `game.wait_ready()`.

### Plugin (Windows)
- `spy::log::Info/Warn/Error/Debug(const std::string&)`, `spy::settings::Load()`, `spy::settings::Get() -> const Config&`, `spy::settings::PluginsDir() -> std::filesystem::path` (`Data/SKSE/Plugins`, the folder of the loader DLL), `spy::settings::OwnDir() -> std::filesystem::path` (`Data/SKSE/Plugins/Skython`, the folder of the core DLL), `spy::settings::SetRuntime/GetRuntime/Runtime` (runtime string for welcome and config), `spy::host::LinkedPythonVersion()`, `spy::life::*` wrappers over one `LifecycleState` (Task 25): `OnMessage(SKSE::MessagingInterface::Message*)`, `DataReady()`, `GameLoaded()`, `Loading()`, `Interactive()`, `Paused()`, `Epoch()`, `LoadsStarted()`, `OpenMenus()`, `NotReadyReason(bool require_unpaused)`, `BaseState() -> json`. Log lines later tasks wait on (exact text): `Skython core: data loaded`, `lifecycle: interactive = true`, `lifecycle: Main Menu open`, `python host: running`, `game backend: dispatcher ready` (Task 27).
- `spy::host::Start(const Config&) -> void` (starts the host thread; returns at once), `spy::host::State() -> std::string` (`"disabled: <why>"`, `"starting"`, `"running"`, `"failed: <why>"`) (Task 26).
- `spy::game::Backend() -> spy::Backend&` (`GameBackend`), `spy::game::GetDispatcher() -> spy::Dispatcher&` (Task 27).
- `spy::ops::InTask` / `t_inTask` (`Ops.h`, Task 27): RAII guard that is the first statement of every AddTask lambda in the plugin.
- `spy::ops::Run(op, args, Done, Cancelled, Progress)` (inside an SKSE task), `spy::pv::*`, `spy::sig::*` as branch Tasks 17a-17c and 18, with `ops::OpResult` replaced by core's `spy::OpResult` (Tasks 27-30).

### Live tests (Task 26 creates; later tasks append)
- `livetest/support.py` (stdlib only, no `skython` import): `Session(port, token)`, `load_session() -> Session` (from `SKYTHON_SESSION`), `encode(obj) -> bytes`, `read_frame(sock) -> dict` (raises `EOFError`), `hello(session, **override) -> bytes`, `raw_connect(session) -> socket.socket` (handshaken, `TCP_NODELAY`), `rpc(sock, op, args, req_id=1) -> dict`, `send_request(sock, op, args, req_id) -> None`, `send_raw(sock, bytes)`, `closed_by_server(sock, timeout=3.0) -> bool`, `frame_header(n)`, `read_frame_or_none(sock, timeout)`, `still_serving(session) -> bool`, `fresh(path) -> bool` (written after this run's `launch.stamp`), `log_text() -> str` (C++ log, this run only), `python_log_text() -> str` (this run only), `refused(port=45977) -> bool`. Every request it sends carries `"type": "request"`. Every live `pytest livetest/...` command sets `SKYTHON_LIVE=1`, and every wait on a log uses `SINCE="$SKYTHON_STATE/launch.stamp"`.
- `livetest/conftest.py`: fixtures `session` and `game` (a `skython.connect()` Game over the bridge); skips everything unless `SKYTHON_LIVE=1`.
- `tools/launch_game.sh` touches `$SKYTHON_STATE/launch.stamp` (`${XDG_STATE_HOME:-~/.local/state}/skython`, Task 1) so `wait_for_log.sh SINCE=` and `support.fresh` ignore older logs.
- `_skython.config()["log_dir"]` is the folder that holds `python.log` and `session.json` (`.../SKSE/Skython/`).

### Tools
- `tools/fetch_python.sh [--check]` (Task 0) → `third_party/cpython/{embed,nuget}`; `--check` verifies SHA-256s against `docs/PINS.md` and exits non-zero on mismatch.
- `tools/remote_build.sh <cmake-source-dir> <dll-name>` → `out/<dll-name>` (one DLL per call: a plugin build is two calls, `Skython.dll` then `SkythonCore.dll`; `deploy.sh` refuses a core DLL older than the loader) (Task 1; also syncs `third_party/cpython/nuget` and `third_party/pybind11`).
- `tools/deploy.sh out/<dll-name> [ini-file]` (Task 1; extended in Task 3 with `--python`, in Task 10 with `--site-packages DIR` and in Task 26 with `--package` and `--scripts DIR`) records every copied path in `out/deployed.txt`; `tools/teardown_list.sh` LISTS what was deployed and removes nothing.
- `tools/launch_game.sh`, `tools/wait_for_log.sh <file> <regex> [seconds]`, `source tools/env.sh` (exports `SKYTHON_SESSION`, `SIMPLESKYRIM_DATA`, `SKSE_LOG_DIR`, `SKYTHON_LOG`) as branch Task 1.

---

## Tasks

**Gates.**
- S1 (Task 2) must pass before Task 3. **S8 (Task 3) must pass before Tasks 18-32 start.** If S8 fails, STOP and ask the owner: the fallback is the out-of-process plan on branch `plan-out-of-process` (Tasks 11-17 here are useful in both designs and may continue).
- S2, S9 and S4 (Tasks 4-6) gate Part C (Tasks 25-32): if any fails, STOP and ask the owner.
- S7 (Task 9) must pass before Tasks 28 and 29. S5, S6 and S10 (Tasks 7, 8, 10) gate only the M2 plan and skyrim-dm's packaging; they do not block M1, but M1 is complete only when all three are recorded.

**STOP and ask the owner (do not improvise) when:** a gating spike fails; the game does not launch within 10 minutes; the Windows build box does not answer; the game runs on any runtime other than AE 1.6.1170; a downloaded file's SHA-256 differs from `docs/PINS.md`; or a step would delete, overwrite or `git push` anything not itemised in this plan.

**Where tasks run.** Part A (M0) is sequential on the spike DLLs. Part B (Tasks 11-24) is pure Linux work and needs no game; Tasks 11-17 may run in parallel with the spikes, Tasks 18-24 after S8 passes. Part C (Tasks 25-32) needs the Windows build box and a running game.

**Task size.** Task files aim at 600 lines or fewer, so a worker reads this index plus one small file. A task that would be longer is split by topic (as 22 and 23 are).

**Where the game runs (owner ruling 2026-10-01).** The owner's Linux desktop is not used to run the game: launching it there took keyboard and mouse focus from the owner. Tasks 3-10 run the game on the Windows box through Task 1b's harness (`tools/box/*`; its "Substitutions for Tasks 3-10" table maps every local step to its box equivalent). Results recorded on the box are Windows-native; S8 run A under Proton already passed (2026-10-01). S6 under Proton and the final verification under Proton wait until the owner says the Linux machine is free. Box-native results are the v1 verdict (owner decision 2026-10-01, ruling R51): the owner is using the Linux machine for other work, so no Proton run happens in this plan; Proton testing (S6, the S8 soak and run B under Proton, a Proton acceptance launch) is deferred until the owner says the Linux machine is free, at the latest M4 packaging. Every box run's evidence lists the SKSE plugins loaded that launch (from `skse64.log`, the `loaded correctly` lines), because the box also loads plugins from other projects; named DLLs are disabled only if a specific result looks confounded, and only after the owner signs off on the list. Known issue, baseline answered by S8 (R46): the game hangs on `qqq` exit on the box even with the spike inert via the `spike-disable` marker (nothing listening on 45978, `STILL_RUNNING after 120s`, `NO_NEW_CRASH_LOG`, one thread left, `skse64.log` ends `cleared save path`), so it is a box/game issue, not ours. Box clean-exit criteria are judged against that baseline: `NOT_CLEAN` with `NO_NEW_CRASH_LOG` and the same signature is not attributable to us. End such games with `tools/box/box_stop.sh` (CIM Terminate). See Task 1b's Known issue.

**Suggested order.** 0 → 1 → 1b → 2 → 3 (S8) → 4 → 5 → 6 → 7 → 8 → 9 → 10, with 11-17 in parallel; then 18 → 19 → 20 → 21 → 22 → 23 → 24; then 25 → 26 → 27 (first real Papyrus call from Linux through the bridge) → 28 → 29 → 30 → 31 → 32.

### Part A: M0 spikes

| # | Task | Runs on | Depends on |
|---|---|---|---|
| 0 | [Repository bootstrap, vendored headers, pinned CPython](task-00-repository-bootstrap.md) | Linux | - |
| 1 | [Build tooling and a spike DLL that loads](task-01-build-tooling-and-spike-dll.md) | Windows box + game | 0 |
| 1b | [Game harness on the Windows box](task-01b-box-game-harness.md) | Windows box + game | 1 |
| 1c | [Part C on the Windows box (substitutions for Tasks 25-32)](task-01c-part-c-on-the-box.md) | (table only) | 1b |
| 2 | [Spike S1: Linux reaches a socket inside Proton (spike harness)](task-02-spike-s1-harness-socket.md) | Windows box + game | 1 |
| 3 | [Spike S8: two-DLL load and CPython embedded in the game; Python socket from Linux](task-03-spike-s8-embedded-cpython.md) | Windows box + game | 2 |
| 4 | [Spike S2: VM callback thread, nested dispatch, argument ownership](task-04-spike-s2-vm-callback-thread.md) | Windows box + game | 3 |
| 5 | [Spike S9: Python threads drive VM calls; frame time and call latency under CPU-bound Python](task-05-spike-s9-python-threads-and-frame-time.md) | Windows box + game | 3, 4 |
| 6 | [Spike S4: bind a quest alias, `ForceRefTo`, read back](task-06-spike-s4-quest-alias.md) | Windows box + game | 4 |
| 7 | [Spike S5: mod events and native `SkyrimPython.Send`](task-07-spike-s5-mod-events-and-send.md) | Windows box + game | 6 |
| 8 | [Spike S6: load a save through `AddUITask`](task-08-spike-s6-load-save.md) | Windows box + game | 7 |
| 9 | [Spike S7: function lookup, bound-object dispatch, alias handles, object arrays](task-09-spike-s7-function-lookup-and-dispatch.md) | Windows box + game | 6, 8 |
| 10 | [Spike S10: skyrim-dm's dependencies as wheels in the embedded interpreter](task-10-spike-s10-dependency-wheels.md) | Linux, then game | 3 |

### Part B: M1 on Linux

| # | Task | Runs on | Depends on |
|---|---|---|---|
| 11 | [Protocol v1 document and golden fixtures](task-11-protocol-document-and-fixtures.md) | Linux | - |
| 12 | [Protocol table generator](task-12-protocol-table-generator.md) | Linux | 11 |
| 13 | [Core library scaffold](task-13-core-scaffold.md) | Linux | 0, 12 |
| 14 | [Errors and the value codec](task-14-errors-and-value-codec.md) | Linux | 13 |
| 15 | [Form keys and the fingerprint rule](task-15-form-keys-and-fingerprints.md) | Linux | 14 |
| 16 | [Requests, op arguments, JSON depth, config](task-16-requests-op-args-config.md) | Linux | 11, 15 |
| 17 | [Lifecycle state](task-17-lifecycle-state.md) | Linux | 14 |
| 18 | [Dispatcher and fake backend](task-18-dispatcher-and-fake-backend.md) | Linux | 16, 17; S8 PASS |
| 19 | [The `_skython` module, Linux build and tests](task-19-skyrim-module-linux.md) | Linux | 18 |
| 20 | [Python package basics: framing, errors, values, form keys](task-20-python-basics.md) | Linux | 12 |
| 21 | [`Game` API and the in-process transport](task-21-game-api-and-inprocess.md) | Linux | 19, 20 |
| 22 | [Discovery and the remote transport](task-22-discovery-and-remote.md) | Linux | 21 |
| 23 | [The dev bridge server](task-23-bridge-server.md) | Linux | 22 |
| 24 | [`skython._host`: logging, script loader, reload, bridge startup](task-24-python-host.md) | Linux | 23 |

### Part C: M1 plugin

Tasks 25-32 run the game on the Windows box through Task 1c's substitution table: the box tools (`tools/box/box_deploy.sh`, `box_launch.sh`, `box_stop.sh`, `box_wait_log.sh`, `box_session.sh`, `box_files.sh`, `box_saves.sh`) replace `tools/deploy.sh`, `launch_game.sh`, `wait_for_log.sh` and `env.sh`; Linux live tests reach the dev bridge through `box_session.sh` (an ssh tunnel and a local `out/box-session.json`, mode 600, which holds the runtime token and is never committed) with `SKYTHON_BOX=1` for `livetest/support.py`'s box mode. Run `tools/box/box_setup.sh` once before the first Part C step. Every box tool refuses to reach the box when `BOX_OFFLINE=1` (the offline tests set it).

| # | Task | Runs on | Depends on |
|---|---|---|---|
| 25 | [Plugin skeleton: loader and core DLLs, logging, settings, lifecycle](task-25-plugin-skeleton.md) | Linux, then Windows box + game | 16, 17, 19; S2, S4, S8, S9 PASS |
| 26 | [Python host thread, `_skython` in the game, deploy layout](task-26-python-host-thread.md) | Windows box + game | 24, 25 |
| 27 | [Game backend, `call_static` and `game.state`](task-27-game-backend-call-static.md) | Windows box + game | 26 |
| 28 | [Papyrus value conversion and signatures](task-28-papyrus-values-and-signatures.md) | Windows box + game | 27; S7 |
| 29 | [`call_method`, aliases and `form.resolve`](task-29-call-method-aliases-resolve.md) | Windows box + game | 28; S4, S7 |
| 30 | [Properties and batch](task-30-properties-and-batch.md) | Windows box + game | 29 |
| 31 | [Robustness, throughput and the in-process smoke script](task-31-robustness-throughput-smoke.md) | Windows box + game | 30 |
| 32 | [README and milestone tag](task-32-readme-and-tag.md) | Linux | 31 |

---

## After this plan

Write the next plan when this one is done and `spikes/RESULTS.md` is final. It covers, per spec section 9:

1. **M1.5: test ESP and engine probes.** Decide with skyrim-dm's integration work which repo owns the production shell quest (spec open question 5). Choose the authoring tool; author `SkythonTest.esp` (≥3 reference aliases, 1 location alias, stages 10/20/100/200, one objective, a quest script with an auto property, a full property, `SetupContract(...)` and stage fragments calling `Skython.Send`). Add the property happy-path live tests deferred from Task 30. Run the three probes and write `docs/probes/`.
2. **M2: events and lifecycle.** C++ per-subscription bounded queues with `dropped` counts; `events.subscribe/unsubscribe` with filters; `_skython.next_events(timeout)`; the Python event dispatch thread and `on_*` helpers; bridge event frames; the v1 event list in spec 4.6; mod events and `Skython.Send` (per S5); `game.load`/`game.save` via `AddUITask` (per S6); a harness that loads a fixed save before each module.
   - **Sink detach when unsubscribed** (SkyrimPlatform comparison, section 4, ruling R38): a sink removes itself when it has no subscribers instead of only skipping work, but only if registering a sink from the main thread mid-game is first shown safe. Never drop an event silently: deliver the reference and let its next use raise `NotFound`.
   - **Console echo of script failures** (comparison Top 8, R38): a rate-limited one-line message in the in-game console when a script fails, queued with `AddTask`; Python never touches the console.
   - **Per-script settings file** (comparison section 10, R38): `scripts/<name>.toml`, read by `skython._host` with stdlib `tomllib` and handed to the script read-only.
3. **skyrim-dm acceptance test** (spec section 8), through the bridge and as an in-process player script, with skyrim-dm's `GameClient` implemented on `skython` and its dependencies vendored as S10 recorded.

M3 (`pscgen`, typed bindings, signature table, client-side defaults) and M4 (packaging, MO2/Vortex, antivirus, Nexus) follow v1 with their own plans. M3 adds a **live-VM cross-check for pscgen** (comparison Top 5, R38): `debug.dump_natives` records each bound native's name, parameters, return type, global vs member and the **latent flag** (`.psc` has no latent marker), and the result is diffed against the `.psc` table; `.psc` stays the source of defaults, non-native functions and names. Read the latent flag only through the VM's own accessor (SkyrimPlatform uses `GetIsLatent()`) if it exists at our CommonLibSSE-NG pin; otherwise drop the latent column. Never raw offsets.

The SkyrimPlatform lessons above come from `.superpowers/sdd/README/skyrimplatform-comparison.md` (ruling R38).
