# Task 41: `game.save` and `game.load` through `AddUITask`

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves), **Contracts** (`game.save`/`game.load` rows and the paragraph under them, Plugin: `spy::saveload`, Live tests) and Part E's brief for Task 41 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (helpers), then the Windows build box (build) and the game on the box (live tests) through `tools/remote_build.sh` and `tools/box/*` ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)). Plugin code builds only on Windows: the "test" for C++ is the build (0 warnings) plus the live tests.
>
> **STOP and ask the owner (do not improvise) if:** a name below differs at the pin; the first `game.save` does not produce its `.ess` within 30 s, or the game crashes or hangs while saving or loading (saving from a UI task has no spike: this task is its first measurement); a new save name already exists (`box_saves.sh count` not 0); the save folder the plugin logs is not the box's `BOX_SAVES_DIR`; the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s.
>
> **Saves (Global Constraints):** this task creates exactly one new throwaway save per run, `skython_t41_<YYYYmmddHHMMSS>`, checked unused first (count 0) and confirmed after (count 1), and loads only that save and `s4test`. Never `save` over anything. Every name made goes on the owner's removal list in RESULTS. End games with `tools/box/box_stop.sh`.

**Files:**
- Create: `plugin/src/SaveLoad.h`, `plugin/src/SaveLoad.cpp`
- Modify: `plugin/src/GameBackend.cpp` (route both ops), `plugin/src/Lifecycle.cpp` (`kSaveGame` → `saveload::OnSaveGame`), `plugin/src/main.cpp` (log the save folder at `kDataLoaded`), `plugin/CMakeLists.txt`
- Modify: `livetest/support.py` (append `box_save_count`, `new_save_name`, `wait_save_file`, `load_save`), `livetest/conftest.py` (append the `fresh_save` fixture)
- Create: `livetest/test_m2_saveload.py`, `livetest/results/new_saves.txt` (written by the helper)
- Modify: `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`, `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 33 error texts `a save named <name> already exists` (`detail.reason` `save_exists`) and `no save named <name>` (`responses.json` ids 64-65; this task's plugin code produces them exactly); Task 33 ops `game.save {name, timeout_ms?}` / `game.load {save, timeout_ms?}` (`save_name` validated by the Dispatcher before `Run`; `game.load` is `needs_game: false`, so the Dispatcher skips its readiness check and never cancels it on the load it starts); Task 35 Dispatcher (deadline → `timeout`); Task 36 `Game.save(name, *, timeout=60.0) -> str`, `Game.load(name, *, timeout=120.0, wait=True) -> dict`, `Game.on`; Task 40 `kSaveGame` case in `Lifecycle.cpp` (the name parsed from `m->data`, the `game_saved` push, the one-shot `lifecycle: kSaveGame data` line), `game_loaded` and `events: game_loaded epoch <n>`; v1 `life::NotReadyReason`, `ops::NotReady(reason)`, `ops::InTask`, `settings::GetRuntime().skse_dir`.
- Produces: `spy::saveload::Run(const std::string& op, const json& args, spy::Done, spy::Cancelled)`, `spy::saveload::OnSaveGame(std::string_view name)`, `spy::saveload::SaveDir() -> std::filesystem::path` (`SaveLoad.h`); log lines `saveload: save folder <path>`, `saveload: saved <name>`, `saveload: load started <name>`; `livetest/support.py` `box_save_count(name) -> int`, `new_save_name(tag) -> str` (checks count 0 and records the name in `livetest/results/new_saves.txt`), `wait_save_file(name, timeout=30.0)`, `load_save(game, name, timeout=120.0) -> dict`; `livetest/conftest.py` module fixture `fresh_save` (loads `skythonbase` when it exists on the box, else `s4test`; returns `{"save", "state"}`).

Rules this task implements:
- **Both ops run their engine call in one `AddUITask`** (spike S6). A UI-task lambda starts with `ops::InTask in;` and **not** `ops::Measured`: the drain meter's fields are written without a lock because our `AddTask` bodies are serialized (observed, R49); UI tasks run on the UI thread and may overlap them. This is the Global Constraints rule "`ops::InTask` then `ops::Measured` first in every `AddTask` lambda; `AddUITask` lambdas take `ops::InTask` only".
- **The engine's bools.** `BGSSaveLoadManager::Save(const char*)` and `Load(const char*)` return `void` at the pin; they call the protected `Save_Impl(2, 0, name)` and `Load_Impl(name, -1, 0, true)`, which return `bool`. The plugin calls those two directly (same arguments as CommonLib's own wrappers) through a pointer to member taken via a derived struct, which is legal C++ access to a protected member. `Load_Impl` false → `internal` naming the save; `Save_Impl` false → `internal`.
- **`game.save`**: an existing `<name>.ess` in the save folder → `bad_request` with the message exactly `a save named <name> already exists` and `detail.reason = "save_exists"` (never overwrites; the text is `responses.json` id 64, Task 33, and what `FakeBackend` and the Python fake produce). A second `game.save` of a name whose save is still pending → `bad_request` `a save named <name> is already in progress`, also `reason: "save_exists"`. The request completes when the game's `kSaveGame` message names that save. SKSE sends `kSaveGame` from its save hook, which may be **before** the `.ess` is on disk: the live test polls the file count (bounded) instead of expecting it at once. A pending save whose request was cancelled (timeout, a load) is forgotten.
- **`game.load`**: refused with `not_ready` while `NotReadyReason(false)` is `"starting"` or `"loading"` (a `"no_game"` main menu is allowed, S6); no such `.ess` → `not_found` with the message exactly `no save named <name>` (`responses.json` id 65; the save folder is in the `saveload: save folder` log line, not in the message); else the UI task loads and answers `{"save": name, "started": true}` once `Load_Impl` returned true.
- **Save folder**: `<Documents>/My Games/<game>` (the SKSE log folder's parent) joined with the game INI's `sLocalSavePath:General` (default `Saves\`). Read once at `kDataLoaded` and logged.

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
Expected: `void Save(const char* a_fileName); void Load(const char* a_fileName);` public; `protected: bool Save_Impl(std::int32_t a_deviceID, std::uint32_t a_outputStats, const char* a_fileName); bool Load_Impl(const char* a_fileName, std::int32_t a_deviceID, std::uint32_t a_outputStats, bool a_checkForMods);`; the `.cpp` wrappers call exactly `Save_Impl(2, 0, a_fileName)` and `Load_Impl(a_fileName, -1, 0, true)`; the class is not `final`; `INISettingCollection::GetSetting(std::string_view)`; `Setting::Type::kString`, `GetString()`, `GetType()`. Append these rows to `API-VERIFICATION.md` (task column 41) with the verbatim declarations.

- [ ] **Step 2: `SaveLoad.h` and `SaveLoad.cpp`**

`plugin/src/SaveLoad.h`:
```cpp
#pragma once
#include "spy/dispatcher.h"
#include <filesystem>
#include <string>
#include <string_view>

// game.save / game.load (Contracts). Run is called on the submitting thread; the engine call runs in one AddUITask.
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
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <mutex>
#include <vector>

namespace spy::saveload {
namespace fs = std::filesystem;
namespace {
// Legal access to the protected bool-returning implementations (a pointer to member named through a derived class).
struct Access : RE::BGSSaveLoadManager
{
    using RE::BGSSaveLoadManager::Load_Impl;
    using RE::BGSSaveLoadManager::Save_Impl;
};

bool EngineSave(RE::BGSSaveLoadManager* m, const std::string& name)
{
    auto fn = &Access::Save_Impl;  // bool (RE::BGSSaveLoadManager::*)(std::int32_t, std::uint32_t, const char*)
    return (m->*fn)(2, 0, name.c_str());  // the arguments of CommonLib's Save(const char*)
}

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

struct Pending
{
    std::string name;
    Done done;
    Cancelled cancelled;
};
std::mutex& Mx() { static auto* m = new std::mutex; return *m; }  // immortal, like the dispatcher
std::vector<Pending>& Saves() { static auto* v = new std::vector<Pending>; return *v; }

void Forget(const std::string& name)
{
    std::lock_guard lk(Mx());
    std::erase_if(Saves(), [&](const Pending& p) { return p.name == name; });
}

OpResult Internal(std::string message) { return OpResult::Err(ErrorCode::internal, std::move(message)); }

void Save(const std::string& name, Done done, Cancelled cancelled)
{
    if (fs::exists(SaveDir() / (name + ".ess")))
        return done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " already exists",
                                  json{ { "reason", "save_exists" } }));
    {
        std::lock_guard lk(Mx());
        std::erase_if(Saves(), [](const Pending& p) { return p.cancelled(); });  // drop requests nobody waits for
        if (std::ranges::any_of(Saves(), [&](const Pending& p) { return p.name == name; }))
            return done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " is already in progress",
                                      json{ { "reason", "save_exists" } }));
        Saves().push_back({ name, done, cancelled });  // before the engine call: kSaveGame may arrive inside it
    }
    SKSE::GetTaskInterface()->AddUITask([name, done, cancelled] {
        ops::InTask in;  // no ops::Measured in a UI task (see Rules)
        if (cancelled()) return Forget(name);
        try {
            auto* mgr = RE::BGSSaveLoadManager::GetSingleton();
            if (!mgr) { Forget(name); return done(Internal("the game's save manager is not available")); }
            if (!EngineSave(mgr, name)) { Forget(name); return done(Internal("the game refused to save " + name)); }
            log::Info("saveload: save requested " + name);  // completes on kSaveGame (OnSaveGame)
        } catch (const std::exception& e) {
            Forget(name);
            done(Internal(std::string("saving threw: ") + e.what()));
        } catch (...) {
            Forget(name);
            done(Internal("saving threw"));
        }
    });
}

void Load(const std::string& name, Done done, Cancelled cancelled)
{
    // needs_game is false for game.load (Contracts), so this is the only readiness check: a main menu is fine.
    if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
    if (!fs::exists(SaveDir() / (name + ".ess")))
        return done(OpResult::Err(ErrorCode::not_found, "no save named " + name));
    SKSE::GetTaskInterface()->AddUITask([name, done, cancelled] {
        ops::InTask in;
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
    });
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
    static fs::path* dir = new fs::path;
    std::call_once(once, [] {
        std::string rel = "Saves\\";
        if (auto* ini = RE::INISettingCollection::GetSingleton())
            if (auto* s = ini->GetSetting("sLocalSavePath:General"); s && s->GetType() == RE::Setting::Type::kString && s->GetString())
                rel = s->GetString();
        const auto& rt = settings::GetRuntime();
        const fs::path base = rt.skse_dir ? rt.skse_dir->parent_path() : fs::path{};
        *dir = base / fs::path(rel);
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
    std::vector<Pending> hit;
    {
        std::lock_guard lk(Mx());
        for (auto it = Saves().begin(); it != Saves().end();) {
            if (it->name == name) { hit.push_back(std::move(*it)); it = Saves().erase(it); }
            else ++it;
        }
    }
    for (auto& p : hit) {
        if (p.cancelled()) continue;
        log::Info("saveload: saved " + name);
        p.done(OpResult::Ok(json{ { "name", name } }));
    }
}
}
```
Add `#include <algorithm>` and `#include <cstring>` to the includes (`std::ranges::any_of`, `std::erase_if` comes with `<vector>`). If MSVC rejects `&Access::Save_Impl` (it should not: the class is not `final` and the using-declarations make the members public in `Access`), use the same relocation CommonLib uses instead, and record which in RESULTS:
```cpp
bool EngineSave(RE::BGSSaveLoadManager* m, const std::string& name)
{
    using func_t = bool (*)(RE::BGSSaveLoadManager*, std::int32_t, std::uint32_t, const char*);
    static REL::Relocation<func_t> func{ RELOCATION_ID(34818, 35727) };  // BGSSaveLoadManager::Save_Impl at the pin
    return func(m, 2, 0, name.c_str());
}
bool EngineLoad(RE::BGSSaveLoadManager* m, const std::string& name)
{
    using func_t = bool (*)(RE::BGSSaveLoadManager*, const char*, std::int32_t, std::uint32_t, bool);
    static REL::Relocation<func_t> func{ RELOCATION_ID(34819, 35728) };  // BGSSaveLoadManager::Load_Impl at the pin
    return func(m, name.c_str(), -1, 0, true);
}
```

- [ ] **Step 3: Wire it in**

`plugin/src/GameBackend.cpp`: add `#include "SaveLoad.h"`; first line of `GameBackend::Run`, before the `game.state` line:
```cpp
        if (op == "game.save" || op == "game.load") return saveload::Run(op, args, std::move(done), std::move(cancelled));
```
`plugin/src/Lifecycle.cpp`: add `#include "SaveLoad.h"`; in Task 40's `kSaveGame` case, right after `events::Push("game_saved", ...)`, add `saveload::OnSaveGame(name);`.

`plugin/src/main.cpp`: add `#include "SaveLoad.h"`; in the `kDataLoaded` block, after `spy::events::RegisterSinks();`, add `spy::saveload::SaveDir();  // read the INI once, on the main thread, and log the folder`.

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
    from support import box_save_count, load_save
    name = "skythonbase" if box_save_count("skythonbase") == 1 else "s4test"
    with skython.connect(port=session.port, token=session.token, client_name="livetest-fresh-save") as g:
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
from support import load_save, log_text, new_save_name, raw_connect, rpc, wait_save_file


@pytest.fixture(scope="module")
def saved(session):
    """One new save for the whole module, made from s4test gameplay."""
    import skython
    with skython.connect(port=session.port, token=session.token, client_name="livetest-t41") as g:
        got: queue.Queue = queue.Queue()
        with g.on("game_saved", None, got.put):
            name = new_save_name("t41")
            assert g.save(name) == name
            ev = got.get(timeout=30.0)
        wait_save_file(name)
    return {"name": name, "event": ev}


def test_save_creates_exactly_one_new_file(saved):
    assert saved["event"].data["name"] == saved["name"]
    text = log_text()
    assert f"saveload: saved {saved['name']}" in text
    assert "lifecycle: kSaveGame data '" in text  # the raw payload, for RESULTS


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
`SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_saveload.py -q` → `7 skipped`.

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
Expected: 7 passed; `new_saves.txt` gained one `skython_t41_<timestamp>` line and `tools/box/box_saves.sh count <that name>` prints 1; the log shows `saveload: save requested`, `lifecycle: kSaveGame data '<name>' (...)`, `saveload: saved <name>`, then `saveload: load started <name>` twice (gameplay and main menu) each followed by `events: game_loaded epoch <n>` with n rising; `no unexpected errors`. If the kSaveGame payload is not the bare name, `Stem` already handles a path or `.ess`; record the verbatim payload either way. A crash or hang while saving: STOP (no spike covered saving from a UI task).

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

Append to `spikes/RESULTS.md`: `**Task 41, game.save / game.load (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test; <n> launches; SKSE plugins: <list>):**` pass counts; the save folder line; the `kSaveGame` payload verbatim and how long after `saveload: save requested` the `.ess` appeared (the poll count); whether `Access::Save_Impl` or the relocation route was used; the two loads' `game_loaded` epochs; "first save from an `AddUITask`: no crash, no hang" (or what happened). Add a line **Owner removal list (Task 41):** with every name in `livetest/results/new_saves.txt` from this task (`skython_t41_*`), stating none is removed without itemised sign-off.

- [ ] **Step 9: Commit**

```bash
git add plugin/src/SaveLoad.h plugin/src/SaveLoad.cpp plugin/src/GameBackend.cpp plugin/src/Lifecycle.cpp plugin/src/main.cpp \
  plugin/CMakeLists.txt livetest/support.py livetest/conftest.py livetest/test_m2_saveload.py livetest/results/new_saves.txt \
  docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md spikes/RESULTS.md
git commit -m "feat(plugin): game.save and game.load through AddUITask"
```
