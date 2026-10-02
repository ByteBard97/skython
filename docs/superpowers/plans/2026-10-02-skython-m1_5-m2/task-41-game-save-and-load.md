# Task 41: `game.save` through SKSE's `Game.SaveGame`, `game.load` through `AddUITask`

> **Ruling R63 (rework after the first live run):** `game.save` no longer calls `BGSSaveLoadManager::Save_Impl`. The first live `game.save` (Save_Impl from an `AddUITask`) hung the game: `saveload: save requested`, logged after the engine call returns, never appeared, no `.ess` was written, no crash log, the process sat idle (a deadlock). Root cause: a synchronous save from an SKSE task deadlocks against the main thread's Papyrus VM lock (`SkyrimVM` freeze vs the blocked main loop). Evidence: DevBench, which saves on this box, documents that raw console save/load can deadlock the engine, and its save path does not call `BGSSaveLoadManager::Save`; it queues a Papyrus call to SKSE's native `Game.SaveGame(string name)` (SKSE 2.2.6 `Game.psc` line 314 `Function SaveGame(string name) native global`, `LoadGame` at 315). (DevBench is GPL: the idea only, no code.) So `game.save` dispatches `Game.SaveGame(name)` through the plugin's own validated `papyrus.call_static` path (`ops::DispatchStatic`: `FindFunction` then `BindArgs` then `DispatchStaticCall`, S2) from one `AddTask` (`ops::InTask` then `ops::Measured`, R52). Completion stays on `kSaveGame`. `game.load` keeps the `AddUITask` + `Load_Impl` path spike S6 proved. Measured live on 2026-10-02 (RESULTS, Task 41).

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves), **Contracts** (`game.save`/`game.load` rows and the paragraph under them, Plugin: `spy::saveload`, Live tests) and Part E's brief for Task 41 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (helpers), then the Windows build box (build) and the game on the box (live tests) through `tools/remote_build.sh` and `tools/box/*` ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)). Plugin code builds only on Windows: the "test" for C++ is the build (0 warnings) plus the live tests.
>
> **STOP and ask the owner (do not improvise) if:** a name below differs at the pin; the first `game.save` does not produce its `.ess` within 30 s, or the game crashes or hangs while saving or loading (R63: a synchronous `Save_Impl` from an SKSE task deadlocked; the `Game.SaveGame` request is this task's measurement); a new save name already exists (`box_saves.sh count` not 0); the save folder the plugin logs is not the box's `BOX_SAVES_DIR`; `game.save` times out while its `.ess` appears on the box (SKSE did not send `kSaveGame` for the `Game.SaveGame` request: the request can never complete that way, so stop rather than lengthen the timeout); the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s.
>
> **Saves (Global Constraints):** this task creates exactly one new throwaway save per run, `skython_t41_<YYYYmmddHHMMSS>`, checked unused first (count 0) and confirmed after (count 1), and loads only that save and `s4test`. Never `save` over anything. Every name made goes on the owner's removal list in RESULTS. End games with `tools/box/box_stop.sh`.

**Files:**
- Create: `plugin/src/SaveLoad.h`, `plugin/src/SaveLoad.cpp`
- Modify: `plugin/src/Ops.h`, `plugin/src/Ops.cpp` (R63: `ops::DispatchStatic`, the validated static-call path `CallStatic` now uses, public so `game.save` can dispatch `Game.SaveGame`)
- Modify: `plugin/src/GameBackend.cpp` (route both ops), `plugin/src/Lifecycle.cpp` (`kSaveGame` → `saveload::OnSaveGame`), `plugin/src/main.cpp` (log the save folder at `kDataLoaded`), `plugin/CMakeLists.txt`
- Modify: `livetest/support.py` (append `box_save_count`, `new_save_name`, `wait_save_file`, `load_save`), `livetest/conftest.py` (append the `fresh_save` fixture)
- Create: `livetest/test_m2_saveload.py`, `livetest/results/new_saves.txt` (written by the helper)
- Modify: `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`, `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 33's `not_ready_reasons` entry `"saving"` (a load of a save whose `game.save` was accepted but whose file is not on disk yet); Task 33 error texts `a save named <name> already exists` (`detail.reason` `save_exists`) and `no save named <name>` (`responses.json` ids 64-65; this task's plugin code produces them exactly); Task 33 ops `game.save {name, timeout_ms?}` / `game.load {save, timeout_ms?}` (`save_name` validated by the Dispatcher before `Run`; `game.load` is `needs_game: false`, so the Dispatcher skips its readiness check and never cancels it on the load it starts); Task 35 Dispatcher (deadline → `timeout`); Task 36 `Game.save(name, *, timeout=60.0) -> str`, `Game.load(name, *, timeout=120.0, wait=True) -> dict`, `Game.on`; Task 40 `kSaveGame` case in `Lifecycle.cpp` (the name parsed from `m->data`, the `game_saved` push, the one-shot `lifecycle: kSaveGame data` line), `game_loaded` and `events: game_loaded epoch <n>`; v1 `life::NotReadyReason`, `ops::NotReady(reason)`, `ops::InTask`, `settings::GetRuntime().skse_dir`.
- Produces: `spy::saveload::Run(const std::string& op, const json& args, spy::Done, spy::Cancelled)`, `spy::saveload::OnSaveGame(std::string_view name)`, `spy::saveload::SaveDir() -> std::filesystem::path` (empty when the save folder is unknown) (`SaveLoad.h`); the answers `bad_request` `a save named <name> is already in progress` (`reason: "save_exists"`, a name pending or announced), `not_ready` `save <name> is still being written` (`detail.reason: "saving"`, plus `paused`/`menus`), `busy` `a game.save is in progress; load after it completes` and `busy` `another game.load is already queued`, `internal` `save folder unknown (...)`; log lines `saveload: save folder <path>`, `saveload: saved <name>`, `saveload: load started <name>`; `livetest/support.py` `box_save_count(name) -> int`, `new_save_name(tag) -> str` (checks count 0 and records the name in `livetest/results/new_saves.txt`), `wait_save_file(name, timeout=30.0)`, `load_save(game, name, timeout=120.0) -> dict`; `livetest/conftest.py` module fixture `fresh_save` (loads `skythonbase` when it exists on the box, else `s4test`; returns `{"save", "state"}`).

Rules this task implements:
- **`game.save` requests the save through SKSE's Papyrus native `Game.SaveGame(name)` (R63)**, dispatched by `ops::DispatchStatic("Game", "SaveGame", {name}, ...)` from one `AddTask` whose lambda starts with `ops::InTask in; ops::Measured measured;`. All name checks (pattern by the Dispatcher, existing `.ess`, pending/announced) run **before** the dispatch. `FindFunction` failing with `unknown_function` (SKSE's scripts missing) → `unsupported` `game.save needs SKSE's Papyrus native Game.SaveGame, which is not loaded (...)`; any other `ProtocolError` keeps its code; a refused dispatch → `vm_error`. The VM callback (`Game.SaveGame` returns None once the save is requested) logs `saveload: save requested <name>` and answers nothing: the request completes on `kSaveGame`. A failure answers only if `Forget(key)` erased the pending entry. Never call `BGSSaveLoadManager::Save`/`Save_Impl` from an SKSE task: it deadlocked the game (R63).
- **`game.load` runs its engine call in one `AddUITask`** (spike S6). A UI-task lambda starts with `ops::InTask in;` and **not** `ops::Measured`: the drain meter's fields are written without a lock because our `AddTask` bodies are serialized (observed, R49); UI tasks run on the UI thread and may overlap them. This is the Global Constraints rule "`ops::InTask` then `ops::Measured` first in every `AddTask` lambda; `AddUITask` lambdas take `ops::InTask` only".
- **The engine's bool.** `BGSSaveLoadManager::Load(const char*)` returns `void` at the pin; it calls the protected `Load_Impl(name, -1, 0, true)`, which returns `bool`. The plugin calls it directly (same arguments as CommonLib's own wrapper) through a pointer to member taken via a derived struct, which is legal C++ access to a protected member. `Load_Impl` false → `internal` naming the save.
- **`game.save`**: an existing `<name>.ess` in the save folder → `bad_request` with the message exactly `a save named <name> already exists` and `detail.reason = "save_exists"` (never overwrites; the text is `responses.json` id 64, Task 33, and what `FakeBackend` and the Python fake produce). The request completes when the game's `kSaveGame` message names that save. **The result (and the `game_saved` event) mean "the engine started the save", not "the file is written":** SKSE sends `kSaveGame` from its save hook, which may be **before** the `.ess` is on disk. So `OnSaveGame` records the name in an **announced** set (case-folded, with the time), dropped lazily once the `.ess` exists or after 120 s. A second `game.save` of a name that is pending (no `kSaveGame` yet) **or announced** (file not written yet) → `bad_request` `a save named <name> is already in progress`, also `reason: "save_exists"` (without the announced set it would pass both checks and overwrite). A pending save whose request was cancelled (timeout, disconnect, a world reset) forgets its callbacks, not its name's reservation (fix round 1): cancelled **before** its `Game.SaveGame` was dispatched, it is dropped and nothing is requested; cancelled **after** the dispatch, its name moves to the reserved (announced) set, not yet announced, with the time, so a second `game.save` of it stays `already in progress` and every `game.load` stays `busy` until its `kSaveGame` arrives, its `.ess` appears or 120 s pass. The task marks the pending entry dispatched under `Mx()` immediately before the dispatch and dispatches nothing when the entry is gone; pending entries carry a per-request id and `Forget` erases by id, so a stale task never forgets a retry of the same name. The disk check (`.ess`, or an orphan `.skse` cosave alone: never overwritten either) runs under `Mx()` after the prune, in `game.save` and `game.load` alike, so a name the prune just dropped because its file appeared answers `already exists` (the prune's `exists()` calls under the lock are acceptable at these set sizes). Names are compared case-insensitively (pending and announced alike). `done` is never called while `Mx()` is held, and exactly once: when the dispatch fails or throws after `kSaveGame` or a cancel already ended the request, `Forget` reports that nothing was erased and no second answer is sent; an `AddTask`/`AddUITask` that throws answers `internal` after `Forget(id)` (save) or clearing the queued-load flag (load). Log lines `saveload: save dispatched <name>` (after `DispatchStatic` returned) and `saveload: save <name> cancelled after dispatch; name stays reserved`.
- **`game.load`**: refused with `not_ready` while `NotReadyReason(false)` is `"starting"` or `"loading"` (a `"no_game"` main menu is allowed, S6); refused with `busy` while a `game.save` is pending (`a game.save is in progress; load after it completes`) or another `game.load` is queued (`another game.load is already queued`); an announced name whose `.ess` is not on disk yet → `not_ready` `save <name> is still being written` with `detail.reason = "saving"` (Task 33 adds `"saving"` to `not_ready_reasons`; it has no `dispatched`, so `retry_not_ready` may retry it); no such `.ess` → `not_found` with the message exactly `no save named <name>` (`responses.json` id 65; the save folder is in the `saveload: save folder` log line, not in the message); else the UI task loads and answers `{"save": name, "started": true}` once `Load_Impl` returned true.
- **Save folder**: `<Documents>/My Games/<game>` (the SKSE log folder's parent) joined with the game INI's `sLocalSavePath:General` (default `Saves\`). Read once in the `kDataLoaded` handler and logged. When the runtime settings have no SKSE log folder (`skse_dir` is `nullopt`), the plugin logs an error once and both ops answer `internal` `save folder unknown (...)`.

- [ ] **Step 1: Verify the names**

```bash
cd plugin/extern/CommonLibSSE-NG
grep -n "void Save(const char\* a_fileName);\|void Load(const char\* a_fileName);\|bool Save_Impl\|bool Load_Impl\|protected:" include/RE/B/BGSSaveLoadManager.h
grep -n "Save_Impl(2, 0, a_fileName)\|Load_Impl(a_fileName, -1, 0, true)" src/RE/B/BGSSaveLoadManager.cpp
grep -n "class BGSSaveLoadManager" include/RE/B/BGSSaveLoadManager.h     # must not be marked final
grep -n "Setting\* GetSetting(std::string_view a_name);\|static INISettingCollection\* GetSingleton" include/RE/I/INISettingCollection.h
grep -n "kString = 6\|const char\* *GetString() const;\|Type *GetType() const;" include/RE/S/Setting.h
grep -n "kSaveGame" include/SKSE/Interfaces.h
cd -
```
Expected: `void Save(const char* a_fileName); void Load(const char* a_fileName);` public; `protected: bool Save_Impl(std::int32_t a_deviceID, std::uint32_t a_outputStats, const char* a_fileName); bool Load_Impl(const char* a_fileName, std::int32_t a_deviceID, std::uint32_t a_outputStats, bool a_checkForMods);`; the `.cpp` wrappers call exactly `Save_Impl(2, 0, a_fileName)` and `Load_Impl(a_fileName, -1, 0, true)`; the class is not `final`; `INISettingCollection::GetSetting(std::string_view)`; `Setting::Type::kString`, `GetString()`, `GetType()`. Append these rows to `API-VERIFICATION.md` (task column 41) with the verbatim declarations. R63: also confirm SKSE's natives `Function SaveGame(string name) native global` / `Function LoadGame(string name) native global` (`Game.psc` 314-315, SKSE 2.2.6) and that the box's `Data\Scripts\Game.pex` is SKSE's (contains `SaveGame`), and add that row.

- [ ] **Step 2: `SaveLoad.h` and `SaveLoad.cpp`**

`plugin/src/SaveLoad.h`:
```cpp
#pragma once
#include "spy/dispatcher.h"
#include <filesystem>
#include <string>
#include <string_view>

// game.save / game.load (Contracts). Run is called on the submitting thread. game.save requests the save through SKSE's
// Papyrus Game.SaveGame from one AddTask (R63); game.load calls the engine's load in one AddUITask (spike S6).
namespace spy::saveload {
void Run(const std::string& op, const json& args, spy::Done done, spy::Cancelled cancelled);
void OnSaveGame(std::string_view name);  // from life::OnMessage on kSaveGame; completes the pending game.save of that name
std::filesystem::path SaveDir();         // read once (first call at kDataLoaded), logged
}
```
`plugin/src/SaveLoad.cpp`:
```cpp
#include "SaveLoad.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Settings.h"
#include "spy/errors.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <map>
#include <mutex>
#include <optional>
#include <system_error>
#include <vector>

namespace spy::saveload {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
namespace {
// Legal access to the protected bool-returning Load_Impl (a pointer to member named through a derived class). Saving
// does not use the save manager directly (R63): a synchronous Save_Impl from an SKSE task deadlocked the game.
struct Access : RE::BGSSaveLoadManager
{
    using RE::BGSSaveLoadManager::Load_Impl;
};

bool EngineLoad(RE::BGSSaveLoadManager* m, const std::string& name)
{
    auto fn = &Access::Load_Impl;
    return (m->*fn)(name.c_str(), -1, 0, true);  // the arguments of CommonLib's Load(const char*) (S6 used that one)
}

std::string U8(const fs::path& p)
{
    const auto s = p.u8string();
    return std::string(reinterpret_cast<const char*>(s.data()), s.size());
}

// Save names are compared case-insensitively: Windows file names are, and the engine may hand a name back in
// another case. Names Skython accepts are [A-Za-z0-9_]; other saves (the player's) only need a stable key.
std::string Fold(std::string_view s)
{
    std::string r(s);
    std::ranges::transform(r, r.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return r;
}

struct Pending
{
    std::uint64_t id;  // this request: a retry of the same name gets a new id, so a stale task never forgets it
    std::string name;  // as requested (the result echoes it)
    std::string key;   // Fold(name)
    Done done;
    Cancelled cancelled;
    bool dispatched = false;  // set under Mx() right before Game.SaveGame is dispatched
    Clock::time_point dispatchedAt{};  // when it was set: a cancel after dispatch reserves the name from here
};
std::mutex& Mx() { static auto* m = new std::mutex; return *m; }  // immortal, like the dispatcher
std::vector<Pending>& Saves() { static auto* v = new std::vector<Pending>; return *v; }
std::uint64_t g_nextId = 0;  // under Mx()
// Reserved names, key -> Reserved. Two kinds, both refuse a second game.save of the name:
// - announced: kSaveGame arrived, the engine started writing the save, but the .ess may not be on disk yet (kSaveGame
//   can precede the file); a game.load of the name answers not_ready "saving".
// - not announced: a pending save cancelled (timeout, disconnect, world reset) after its Game.SaveGame was dispatched;
//   the engine may still write it, so the name stays reserved and every game.load is busy until kSaveGame arrives.
// Dropped lazily once the file exists or kAnnouncedMaxAge after `since` (kSaveGame, or the dispatch for a cancelled
// save: the engine's write is timed from the request, not from the cancel).
struct Reserved
{
    Clock::time_point since;
    bool announced;
};
std::map<std::string, Reserved>& Announced() { static auto* m = new std::map<std::string, Reserved>; return *m; }
constexpr auto kAnnouncedMaxAge = std::chrono::seconds(120);
std::atomic_bool g_loadQueued{ false };  // a game.load's UI task is queued or running

// A loadable save is its .ess; the .skse cosave alone (an orphan) cannot be loaded.
bool EssOnDisk(const std::string& name)
{
    std::error_code ec;  // never throws: an unreadable folder counts as "not there"
    return fs::exists(SaveDir() / (name + ".ess"), ec);
}

// A save is there when its .ess is, or its .skse cosave alone (an orphan): game.save never writes over either.
bool OnDisk(const std::string& name)
{
    std::error_code ec;  // never throws: an unreadable folder counts as "not there"
    const fs::path dir = SaveDir();
    if (fs::exists(dir / (name + ".ess"), ec)) return true;
    return fs::exists(dir / (name + ".skse"), ec);
}

// Under Mx(): forget reserved names whose .ess is now on disk or that are older than kAnnouncedMaxAge. The .ess, not
// the .skse: a cosave written first must not end the reservation while game.load would still find no .ess (game.save
// refuses the name either way, through OnDisk). This does file system I/O (one exists() per entry) under the lock;
// acceptable while the set holds a handful of names.
void PruneAnnouncedLocked()
{
    const auto now = Clock::now();
    std::erase_if(Announced(), [&](const auto& kv) { return now - kv.second.since > kAnnouncedMaxAge || EssOnDisk(kv.first); });
}

// Under Mx(): drop pending saves nobody waits for any more. One cancelled before its dispatch is simply dropped (its
// task sees cancelled() or a missing entry and dispatches nothing). One cancelled after its Game.SaveGame was
// dispatched loses only its callbacks: its name moves to Announced() (not announced yet) and stays reserved.
void DropCancelledLocked()
{
    for (auto it = Saves().begin(); it != Saves().end();) {
        if (!it->cancelled()) { ++it; continue; }
        if (it->dispatched) {
            Announced().try_emplace(it->key, Reserved{ it->dispatchedAt, false });
            log::Info("saveload: save " + it->name + " cancelled after dispatch; name stays reserved");
        }
        it = Saves().erase(it);
    }
}

// Erases the pending save with that id. True only when this call erased it, so `done` runs exactly once: kSaveGame
// may complete the request (OnSaveGame) before the Game.SaveGame dispatch returns, throws or calls back.
bool Forget(std::uint64_t id)
{
    std::lock_guard lk(Mx());
    return std::erase_if(Saves(), [&](const Pending& p) { return p.id == id; }) > 0;
}

// Marks the pending save with that id as dispatched. False when it is gone (a cancel was swept, or kSaveGame came):
// then nothing may be dispatched for it, since a retry of the same name may already own the name.
bool MarkDispatched(std::uint64_t id)
{
    std::lock_guard lk(Mx());
    const auto it = std::ranges::find_if(Saves(), [&](const Pending& p) { return p.id == id; });
    if (it == Saves().end()) return false;
    it->dispatched = true;
    it->dispatchedAt = Clock::now();
    return true;
}

OpResult Internal(std::string message) { return OpResult::Err(ErrorCode::internal, std::move(message)); }
OpResult NoSaveFolder() { return Internal("save folder unknown (no SKSE log folder at kDataLoaded; see the plugin log)"); }
OpResult InProgress(const std::string& name)
{
    return OpResult::Err(ErrorCode::bad_request, "a save named " + name + " is already in progress", json{ { "reason", "save_exists" } });
}

void Save(const std::string& name, Done done, Cancelled cancelled)
{
    if (SaveDir().empty()) return done(NoSaveFolder());
    const std::string key = Fold(name);
    bool exists = false;
    bool refused = false;
    std::uint64_t id = 0;
    {
        std::lock_guard lk(Mx());
        DropCancelledLocked();
        PruneAnnouncedLocked();
        // The disk is checked here, after the prune: a reserved name the prune just dropped because its file appeared
        // must answer "already exists", never pass as free.
        exists = OnDisk(name);
        // Pending (no kSaveGame yet) or reserved (kSaveGame came, or a dispatched save was cancelled; file not written
        // yet): either way a second save of that name could overwrite the first, so it is refused.
        if (!exists)
            refused = Announced().contains(key) || std::ranges::any_of(Saves(), [&](const Pending& p) { return p.key == key; });
        if (!exists && !refused) {
            id = ++g_nextId;
            Saves().push_back({ id, name, key, done, cancelled });  // before the engine call: kSaveGame may arrive inside it
        }
    }
    // Answers after the lock is released: done may complete the request at once.
    if (exists)
        return done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " already exists",
                                  json{ { "reason", "save_exists" } }));
    if (refused) return done(InProgress(name));
    // R63: the save is requested through SKSE's Papyrus native Game.SaveGame(name), dispatched like papyrus.call_static
    // from one AddTask. A synchronous BGSSaveLoadManager Save_Impl from an SKSE task deadlocks against the main
    // thread's Papyrus VM lock (Task 41, first live run). The VM call returns once the save is requested; the request
    // completes on kSaveGame (OnSaveGame), as before.
    // A cancel before the dispatch drops the request; after it, the name stays reserved (DropCancelledLocked).
    auto task = [name, id, done, cancelled] {
        ops::InTask in;
        ops::Measured measured;
        if (cancelled()) { Forget(id); return; }
        // Called by the VM callback once Game.SaveGame returned (or with vm_error if the VM refused the dispatch).
        // Success answers nothing: kSaveGame completes the request. `done` is never called under Mx().
        Done onCall = [name, id, done](OpResult r) {
            if (r.ok) { log::Info("saveload: save requested " + name); return; }
            if (Forget(id)) done(std::move(r));
            else log::Error("saveload: save " + name + " failed after kSaveGame or a cancel had already ended it");
        };
        std::optional<OpResult> failure;
        try {
            if (auto why = life::NotReadyReason(false)) failure = ops::NotReady(*why);  // recheck inside the task
            else if (!MarkDispatched(id)) return;  // cancelled and dropped meanwhile: nothing is requested
            else {
                // Marked first: if the request is cancelled from here on, the name stays reserved even when the
                // dispatch below then fails (harmless: the reservation ends after kAnnouncedMaxAge).
                ops::DispatchStatic("Game", "SaveGame", { Value{ name } }, onCall);
                log::Info("saveload: save dispatched " + name);
            }
        } catch (const ProtocolError& e) {
            if (e.code == ErrorCode::unknown_function)
                failure = OpResult::Err(ErrorCode::unsupported, std::string("game.save needs SKSE's Papyrus native Game.SaveGame, "
                                        "which is not loaded (are SKSE's scripts installed?): ") + e.what());
            else failure = OpResult::Err(e.code, std::string("game.save: ") + e.what());
        } catch (const std::exception& e) {
            failure = Internal(std::string("requesting the save threw: ") + e.what());
        } catch (...) {
            failure = Internal("requesting the save threw");
        }
        if (!failure) return;
        if (Forget(id)) done(std::move(*failure));  // still pending: answer the error
        else log::Error("saveload: save " + name + " failed after kSaveGame or a cancel had already ended it");
    };
    try {
        SKSE::GetTaskInterface()->AddTask(std::move(task));
    } catch (const std::exception& e) {
        if (Forget(id)) done(Internal(std::string("queueing the save task threw: ") + e.what()));
    } catch (...) {
        if (Forget(id)) done(Internal("queueing the save task threw"));
    }
}

void Load(const std::string& name, Done done, Cancelled cancelled)
{
    // needs_game is false for game.load (Contracts), so this is the only readiness check: a main menu is fine.
    if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
    if (SaveDir().empty()) return done(NoSaveFolder());
    const std::string key = Fold(name);
    std::optional<OpResult> refusal;
    {
        std::lock_guard lk(Mx());
        DropCancelledLocked();
        PruneAnnouncedLocked();
        // After the prune, like Save: the prune drops a name once its file is there. Only the .ess counts here: an
        // orphan .skse is not a save the game can load (not_found), while game.save still refuses its name.
        const bool onDisk = EssOnDisk(name);
        // A pending save, or a dispatched save that was cancelled before its kSaveGame, may still be written: busy.
        if (!Saves().empty() || std::ranges::any_of(Announced(), [](const auto& kv) { return !kv.second.announced; }))
            refusal = OpResult::Err(ErrorCode::busy, "a game.save is in progress; load after it completes");
        else if (!onDisk && Announced().contains(key))  // announced (the busy check above took the others)
            refusal = OpResult::Err(ErrorCode::not_ready, "save " + name + " is still being written",
                                    ops::WithGameState({ { "reason", "saving" } }));
        else if (!onDisk)
            refusal = OpResult::Err(ErrorCode::not_found, "no save named " + name);
        else if (g_loadQueued.exchange(true))
            refusal = OpResult::Err(ErrorCode::busy, "another game.load is already queued");
    }
    if (refusal) return done(std::move(*refusal));  // outside the lock
    auto task = [name, done, cancelled] {
        ops::InTask in;
        struct ClearQueued { ~ClearQueued() { g_loadQueued.store(false); } } clearQueued;  // whatever happens below
        if (cancelled()) return;  // the caller gave up before the UI thread got here: do not start a load nobody waits for
        try {
            if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
            auto* mgr = RE::BGSSaveLoadManager::GetSingleton();
            if (!mgr) return done(Internal("the game's save manager is not available"));
            if (!EngineLoad(mgr, name)) return done(Internal("the game's load call returned false for save " + name));
            log::Info("saveload: load started " + name);
            done(OpResult::Ok(json{ { "save", name }, { "started", true } }));
        } catch (const std::exception& e) {
            done(Internal(std::string("loading threw: ") + e.what()));
        } catch (...) {
            done(Internal("loading threw"));
        }
    };
    try {
        SKSE::GetTaskInterface()->AddUITask(std::move(task));
    } catch (const std::exception& e) {
        g_loadQueued.store(false);
        done(Internal(std::string("queueing the load task threw: ") + e.what()));
    } catch (...) {
        g_loadQueued.store(false);
        done(Internal("queueing the load task threw"));
    }
}

// The engine may pass a bare name, a name with ".ess", or a path: compare on the bare stem.
std::string Stem(std::string_view raw)
{
    std::string s(raw);
    if (const auto slash = s.find_last_of("\\/"); slash != std::string::npos) s.erase(0, slash + 1);
    if (s.size() > 4 && _stricmp(s.c_str() + s.size() - 4, ".ess") == 0) s.resize(s.size() - 4);
    return s;
}
}

fs::path SaveDir()
{
    static std::once_flag once;
    static fs::path* dir = new fs::path;  // stays empty when the folder is unknown: both ops then answer internal
    std::call_once(once, [] {
        const auto& rt = settings::GetRuntime();
        if (!rt.skse_dir) {
            log::Error("saveload: save folder unknown (no SKSE log folder); game.save and game.load will answer internal");
            return;
        }
        std::string rel = "Saves\\";
        if (auto* ini = RE::INISettingCollection::GetSingleton())
            if (auto* s = ini->GetSetting("sLocalSavePath:General"); s && s->GetType() == RE::Setting::Type::kString && s->GetString())
                rel = s->GetString();
        *dir = rt.skse_dir->parent_path() / fs::path(rel);
        if (!dir->has_filename()) *dir = dir->parent_path();  // "Saves\" -> "...\Saves": the logged folder ends in its name
        log::Info("saveload: save folder " + U8(*dir));
    });
    return *dir;
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled)
{
    try {
        if (op == "game.save") return Save(args.at("name").get<std::string>(), std::move(done), std::move(cancelled));
        if (op == "game.load") return Load(args.at("save").get<std::string>(), std::move(done), std::move(cancelled));
        done(Internal("saveload: unexpected op " + op));
    } catch (const std::exception& e) {
        done(Internal(std::string("saveload: ") + e.what()));
    }
}

void OnSaveGame(std::string_view raw)
{
    const std::string name = Stem(raw);
    const std::string key = Fold(name);
    std::vector<Pending> hit;
    {
        std::lock_guard lk(Mx());
        Announced()[key] = Reserved{ Clock::now(), true };  // the engine started this save; its .ess may not be on disk yet
        for (auto it = Saves().begin(); it != Saves().end();) {
            if (it->key == key) { hit.push_back(std::move(*it)); it = Saves().erase(it); }
            else ++it;
        }
    }
    for (auto& p : hit) {  // outside the lock
        if (p.cancelled()) continue;
        log::Info("saveload: saved " + p.name);  // the engine started the save (Contracts); the file may follow
        p.done(OpResult::Ok(json{ { "name", p.name } }));
    }
}
}
```
(`std::erase_if` for `std::vector` and `std::map` comes with `<vector>` and `<map>` in C++20; `ops::WithGameState` adds `paused` and `menus` to the `not_ready` detail like every other `not_ready`.) If MSVC rejects `&Access::Load_Impl` (it should not: the class is not `final` and the using-declaration makes the member public in `Access`), use the same relocation CommonLib uses instead, and record which in RESULTS:
```cpp
bool EngineLoad(RE::BGSSaveLoadManager* m, const std::string& name)
{
    using func_t = bool (*)(RE::BGSSaveLoadManager*, const char*, std::int32_t, std::uint32_t, bool);
    static REL::Relocation<func_t> func{ RELOCATION_ID(34819, 35728) };  // BGSSaveLoadManager::Load_Impl at the pin
    return func(m, name.c_str(), -1, 0, true);
}
```
`plugin/src/Ops.h` declares, and `Ops.cpp` defines outside its anonymous namespace (after `AddStateExtras`), the validated static-call path; `CallStatic` keeps its `require_unpaused` check and then calls it:
```cpp
// papyrus.call_static's validated path (inside an SKSE task only): FindFunction (throws unknown_function or
// bad_request), BindArgs (throws type_mismatch), then DispatchStaticCall; `done` gets the converted result from the VM
// callback, or vm_error when the VM refuses the dispatch (R37). game.save uses it for SKSE's Game.SaveGame (R63).
void DispatchStatic(const std::string& script, const std::string& function, const std::vector<Value>& args, const Done& done);
```

- [ ] **Step 3: Wire it in**

`plugin/src/GameBackend.cpp`: add `#include "SaveLoad.h"`; first line of `GameBackend::Run`, before the `game.state` line:
```cpp
        if (op == "game.save" || op == "game.load") return saveload::Run(op, args, std::move(done), std::move(cancelled));
```
`plugin/src/Lifecycle.cpp`: add `#include "SaveLoad.h"`; in Task 40's `kSaveGame` case, right after `events::Push("game_saved", ...)`, add `saveload::OnSaveGame(name);`.

`plugin/src/main.cpp`: add `#include "SaveLoad.h"`; in the `kDataLoaded` block, after `spy::events::RegisterSinks();`, add `spy::saveload::SaveDir();  // read the INI once, in the kDataLoaded handler, and log the folder`.

`plugin/CMakeLists.txt`: add `src/SaveLoad.cpp` to `SPY_CORE_SOURCES`.

- [ ] **Step 4: Live-test helpers and the `fresh_save` fixture**

Append to `livetest/support.py`:
```python
# ---- Task 41: saves. New names only (skython_*), each checked unused first and recorded for the owner. -------
import re as _re  # noqa: E402

LOADABLE = _re.compile(r"s4test|spikebase|skythonbase|skython_[A-Za-z0-9_]{1,56}")


def box_save_count(name: str) -> int:
    """How many saves on the box match NAME*.ess (tools/box/box_saves.sh: a prefix match, so no new name may be a
    prefix of another: every name made here ends in a 14-digit timestamp)."""
    r = subprocess.run([str(REPO / "tools" / "box" / "box_saves.sh"), "count", name], check=True,
                       capture_output=True, text=True, timeout=90)
    return int(r.stdout.strip())


def new_save_name(tag: str) -> str:
    """skython_<tag>_<timestamp>, checked unused on the box (count 0) and appended to livetest/results/new_saves.txt
    for the owner's removal list."""
    name = f"skython_{tag}_{time.strftime('%Y%m%d%H%M%S')}"
    assert _re.fullmatch(r"[A-Za-z0-9_]{1,64}", name), name
    n = box_save_count(name)
    assert n == 0, f"STOP: a save named {name} already exists on the box (count {n}); never save over a save"
    out = REPO / "livetest" / "results" / "new_saves.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(name + "\n")
    return name


def wait_save_file(name: str, timeout: float = 30.0) -> None:
    """kSaveGame may come before the .ess is written: poll the box until exactly one file matches."""
    deadline = time.monotonic() + timeout
    while (n := box_save_count(name)) != 1:
        assert n == 0, f"{n} saves match {name}*"
        assert time.monotonic() < deadline, f"STOP: {name}.ess did not appear within {timeout} s"
        time.sleep(2.0)


def load_save(game, name: str, timeout: float = 120.0) -> dict:
    """Load one of the saves this plan or the M0/M1 spikes made (Global Constraints: Saves) through Game.load and
    wait until the game is interactive again with a higher epoch. Returns that state."""
    if not LOADABLE.fullmatch(name):
        raise ValueError(f"tests load only s4test, spikebase, skythonbase or skython_* saves, not {name!r}")
    return game.load(name, timeout=timeout, wait=True)
```
Append to `livetest/conftest.py`:
```python
@pytest.fixture(scope="module")
def fresh_save(session):
    """Spec 8 / v1 'After this plan' item 2: load a fixed save before the module runs. skythonbase (Task 49) when it
    exists on the box, else s4test. Returns {"save": name, "state": the interactive state after the load}.
    skythonbase holds SkythonTest.esp, so a launch that may load it is preceded by `tools/box/box_plugins.sh add`
    (Global Constraints, after Gate 0); the quit/stop tools restore plugins.txt."""
    import skython
    from skython import errors
    from support import box_save_count, load_save
    # box_save_count is a prefix match (skythonbase*.ess); the exact name is checked by the plugin's game.load, which
    # answers not_found unless skythonbase.ess itself exists.
    name = "skythonbase" if box_save_count("skythonbase") >= 1 else "s4test"
    with skython.connect(port=session.port, token=session.token, client_name="livetest-fresh-save") as g:
        try:
            state = load_save(g, name)
        except errors.NotFound:
            if name != "skythonbase":
                raise
            name = "s4test"  # only other saves start with skythonbase: no base save yet
            state = load_save(g, name)
    return {"save": name, "state": state}
```

- [ ] **Step 5: The live tests**

Create `livetest/test_m2_saveload.py`:
```python
"""game.save / game.load in the game (Task 41). Starts on s4test; makes one new throwaway save per run."""
import queue
import threading
import time

import pytest

from skython import errors
from support import LOADABLE, box_save_count, load_save, log_text, new_save_name, raw_connect, rpc, wait_save_file


@pytest.fixture(scope="module")
def saved(session):
    """One new save for the whole module, made from s4test gameplay. game.save returns once the engine started the
    save; the .ess may not be on disk yet (Contracts). Right then, a second save of that name and a load of it are
    tried: the save is refused (save_exists, "already in progress" or, if the file is there, "already exists"), and
    the load either finds the file and loads it or answers not_ready with reason "saving". Both outcomes are
    recorded; the test below checks each is one of the allowed ones."""
    import skython
    with skython.connect(port=session.port, token=session.token, client_name="livetest-t41") as g:
        got: queue.Queue = queue.Queue()
        with g.on("game_saved", None, got.put):
            name = new_save_name("t41")
            assert g.save(name) == name
            try:
                g.save(name)
                again = None  # a second save of the same name must never succeed: the test fails on this
            except errors.BadRequest as e:
                again = e
            ev = got.get(timeout=30.0)
        assert LOADABLE.fullmatch(name), name  # tests load only their own saves (load_save's rule)
        try:
            g.load(name, timeout=120.0, wait=True)
            immediate_load = "loaded"
        except errors.NotReady as e:
            if (e.detail or {}).get("reason") != "saving":
                raise
            immediate_load = "saving"
        wait_save_file(name)
    print(f"t41: immediate second save -> {again and again.message!r}; immediate load -> {immediate_load}")
    return {"name": name, "event": ev, "again": again, "immediate_load": immediate_load}


def test_save_then_save_and_load_at_once(saved):
    again = saved["again"]
    assert again is not None, "a second game.save of the same name succeeded: it would overwrite the first"
    assert again.detail.get("reason") == "save_exists"
    assert again.message in (f"a save named {saved['name']} is already in progress",
                             f"a save named {saved['name']} already exists"), again.message
    assert saved["immediate_load"] in ("loaded", "saving")


def test_save_creates_exactly_one_new_file(saved):
    assert saved["event"].data["name"] == saved["name"]
    text = log_text()
    assert f"saveload: saved {saved['name']}" in text
    assert "lifecycle: kSaveGame data '" in text  # the raw payload, for RESULTS
    assert box_save_count(saved["name"]) == 1


def test_saving_the_same_name_again_is_refused(game, saved):
    with pytest.raises(errors.BadRequest) as e:
        game.save(saved["name"])
    assert e.value.detail.get("reason") == "save_exists"
    assert e.value.message == f"a save named {saved['name']} already exists"


def test_bad_save_names_are_refused(game):
    # Refused by the client (Task 36 may check SAVE_NAME_PATTERN locally: ValueError/EncodeError) or by the plugin
    # (bad_request); either way nothing is saved.
    for bad in ("", "a" * 65, "has space", "dots.ess", "../up"):
        with pytest.raises((errors.BadRequest, ValueError)):
            game.save(bad)


def test_load_missing_save_is_not_found(game):
    with pytest.raises(errors.NotFound) as e:
        game.load("no_such_save_skython", wait=False)
    assert e.value.message == "no save named no_such_save_skython"


def test_load_from_gameplay_raises_epoch_and_game_loaded(game, saved):
    before = game.state()["epoch"]
    got: queue.Queue = queue.Queue()
    with game.on_game_loaded(got.put):
        state = load_save(game, saved["name"])
        ev = got.get(timeout=30.0)
    assert state["epoch"] == before + 1 and state["interactive"] is True
    assert ev.name == "game_loaded" and ev.data["new_game"] is False and ev.epoch == before + 1
    assert f"saveload: load started {saved['name']}" in log_text()


def test_a_call_in_flight_during_the_load_ends_not_ready_dispatched(game, saved):
    outcome: dict = {}

    def slow():
        try:
            outcome["result"] = game.call("Utility", "Wait", 5.0, timeout=15.0)
        except errors.SkythonError as e:
            outcome["error"] = e

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.5)  # the Wait is running in the VM
    load_save(game, saved["name"])
    t.join(20.0)
    err = outcome.get("error")
    assert isinstance(err, errors.NotReady) and err.detail.get("dispatched") is True, outcome


def test_a_save_cancelled_after_dispatch_keeps_its_name_reserved(game):
    """Fix round 1: a game.save whose request ends (here: a tiny timeout) after its Game.SaveGame was dispatched keeps
    its name reserved, so an immediate second save of that name is refused instead of dispatching a second
    Game.SaveGame that would overwrite the first. The ~50 ms window between dispatch and kSaveGame cannot be hit on
    purpose (the dispatcher's reaper ticks every 50 ms), so each attempt checks what must hold on every branch and
    the log says which branch ran; at most 3 attempts (3 new saves) try to hit the post-dispatch one."""
    branches = []
    timeout = 0.03
    for _attempt in range(3):
        name = new_save_name("t41c")
        try:
            game.save(name, timeout=timeout)
            first = "ok"
        except errors.Timeout:
            first = "timeout"
        second = None
        if first == "timeout":
            try:
                game.save(name)
                second = "saved"  # the first was cancelled before its dispatch: nothing was requested for it
            except errors.BadRequest as e:
                assert e.detail.get("reason") == "save_exists", e
                assert e.message in (f"a save named {name} is already in progress",
                                     f"a save named {name} already exists"), e.message
                second = "refused"
        wait_save_file(name)
        with pytest.raises(errors.BadRequest) as e:
            game.save(name)
        assert e.value.detail.get("reason") == "save_exists"
        assert e.value.message == f"a save named {name} already exists"
        assert box_save_count(name) == 1
        text = log_text()
        assert text.count(f"saveload: save dispatched {name}\n") == 1, "Game.SaveGame was dispatched more than once"
        reserved = f"saveload: save {name} cancelled after dispatch; name stays reserved" in text
        if second == "saved":
            assert not reserved
        branches.append((name, timeout, first, second, reserved))
        if second == "refused" and reserved:
            break  # the post-dispatch branch ran
        # Cancelled before the dispatch: wait longer next time; cancelled too late (or not at all): shorter.
        timeout = timeout * 2 if second == "saved" else max(0.001, timeout / 3)
    print(f"t41c: {branches}")
    if not any(second == "refused" and reserved for _, _, _, second, reserved in branches):
        pytest.skip(f"the post-dispatch cancel branch was not hit in {len(branches)} attempts: {branches}")


def test_load_from_the_main_menu(session, game, saved):
    try:
        game.call("Game", "QuitToMainMenu")
    except errors.NotReady:
        pass  # the quit is a world reset: the call itself may be ended by it
    deadline = time.monotonic() + 120.0
    while game.state().get("game_loaded"):
        assert time.monotonic() < deadline, "the game did not reach the main menu"
        time.sleep(0.5)
    before = game.state()["epoch"]
    assert LOADABLE.fullmatch(saved["name"]), saved["name"]
    s = raw_connect(session)
    try:
        r = rpc(s, "game.load", {"save": saved["name"]}, req_id=1, timeout=60.0)
        assert r["ok"] is True and r["result"] == {"save": saved["name"], "started": True}, r
    finally:
        s.close()
    deadline = time.monotonic() + 120.0
    while True:
        st = game.state()
        if st["interactive"] and st["epoch"] > before:
            break
        assert time.monotonic() < deadline, f"not interactive after the main-menu load: {st}"
        time.sleep(0.5)
```
`SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_saveload.py -q` → `9 skipped` (8 before fix round 1).

- [ ] **Step 6: Build**

```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
```
Expected: both succeed, 0 warnings.

- [ ] **Step 7: Run in the game**

```bash
source tools/box/box_env.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_wait_log.sh Skython.log "saveload: save folder" 60
tools/box/box_files.sh pull Skython.log >/dev/null && grep "saveload: save folder" "$SKSE_LOG_DIR/Skython.log"   # must end in \Saves (BOX_SAVES_DIR)
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_saveload.py -q -s -rs
cat livetest/results/new_saves.txt
tools/box/box_files.sh pull Skython.log >/dev/null
grep "saveload: \|lifecycle: kSaveGame\|events: game_loaded" "$SKSE_LOG_DIR/Skython.log"
grep '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
```
Expected: 9 passed (8 before fix round 1; the printed `t41:` and `t41c:` lines say what the immediate second saves and load answered: keep them); `new_saves.txt` gained one `skython_t41_<timestamp>` line and one to three `skython_t41c_<timestamp>` lines, and `tools/box/box_saves.sh count <each name>` prints 1; the log shows `saveload: save requested`, `lifecycle: kSaveGame data '<name>' (...)`, `saveload: saved <name>`, then `saveload: load started <name>` twice (gameplay and main menu) or three times (also the fixture's immediate load, when the file was already there) each followed by `events: game_loaded epoch <n>` with n rising; `no unexpected errors`. If the kSaveGame payload is not the bare name, `Stem` already handles a path or `.ess`; record the verbatim payload either way. A crash or hang while saving: STOP (R63: the `Game.SaveGame` request replaced the `Save_Impl` call that hung).

Then re-run the earlier Part E suites on a fresh launch to show nothing regressed (`s4test`, never saved):
```bash
tools/box/box_launch.sh && tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch1.py livetest/test_m1_calls.py -q -rs
tools/box/box_stop.sh
```
Expected: all pass (batch 1: 8, calls: 46).

- [ ] **Step 8: Record**

Append to `spikes/RESULTS.md`: `**Task 41, game.save / game.load (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test; <n> launches; SKSE plugins: <list>):**` pass counts; the save folder line; the `kSaveGame` payload verbatim and how long after `saveload: save requested` the `.ess` appeared (the poll count); the `t41:` line (whether a load right after `game.save` found the file or answered `not_ready` `saving`); that the save went through SKSE's `Game.SaveGame` (R63, with the root cause of the first run's hang) and whether `Access::Load_Impl` or the relocation route was used; the two loads' `game_loaded` epochs; "first save through `Game.SaveGame`: no crash, no hang" (or what happened). Add a line **Owner removal list (Task 41):** with every name in `livetest/results/new_saves.txt` from this task (`skython_t41_*`), stating none is removed without itemised sign-off.

- [ ] **Step 9: Commit**

```bash
git add plugin/src/SaveLoad.h plugin/src/SaveLoad.cpp plugin/src/Ops.h plugin/src/Ops.cpp plugin/src/GameBackend.cpp plugin/src/Lifecycle.cpp plugin/src/main.cpp \
  plugin/CMakeLists.txt livetest/support.py livetest/conftest.py livetest/test_m2_saveload.py livetest/results/new_saves.txt \
  docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md spikes/RESULTS.md
git commit -m "feat(plugin): game.save through SKSE's Game.SaveGame request, game.load through a UI task"
```
