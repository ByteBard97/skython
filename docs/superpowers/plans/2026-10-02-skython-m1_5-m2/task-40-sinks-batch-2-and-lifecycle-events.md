# Task 40: Sinks batch 2 and lifecycle events (hit, equip, container, cell, activate, location, menu, game_loaded/saved, new_game)

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Review Focus** (item 1 is pinned live here), **Contracts** (events table, Plugin) and Part E's brief for Task 40 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** the Windows build box (build) and the game on the box (live tests), through `tools/remote_build.sh` and `tools/box/*` ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)). Plugin code builds only on Windows: the "test" for C++ is the build (0 warnings) plus the live tests.
>
> **STOP and ask the owner (do not improvise) if:** a struct or field below differs at the pin by more than a rename; the game crashes or hangs; the player dies during a test (the session is then lost: report, do not reload a save other than `s4test`); the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s; `s4test` starts the player in an interior (the cell test needs an exterior); the game runs on anything other than AE 1.6.1170.
>
> **Box rules:** `s4test`, **never saved** (no `save` command, no `game.save`; `game_saved` is exercised by Task 41 with a new throwaway save). Spawned actors are neutralised (aggression 0, the player a ghost) and removed in `finally`. End games with `tools/box/box_stop.sh`.

**Files:**
- Replace: `plugin/src/EventSinksWorld.cpp` (Task 39 left a stub)
- Modify: `plugin/src/Lifecycle.cpp` (`game_loaded`, `game_saved`, `new_game`)
- Modify: `livetest/support.py` (append `PLAYER`, `spawn`, `cleanup`, `neutral_fight`, `box_menu`, `translate_player`)
- Modify: `livetest/scripts/livetest_smoke/__init__.py` (`hold_sub` / `release_sub` commands)
- Create: `livetest/test_m2_events_batch2.py`
- Modify: `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`, `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 39 `spy::events::Hub()`, `Push`, `FormRefJson`, `NoteSinkError`, `NoteFirstEvent`, `RegisterWorldSinks` (declared in `Events.h`), `pv::FloatToJson`, `livetest/support.py` `subscribe`, `read_event`, `read_events`, the smoke script's command loop; Task 34 hub rules (the table's `filter_required_any_of` makes `hit` and `container_changed` without a filter `bad_request`; `deliver_while_loading`); Task 36 `Game.on`, `Event`; v1 `life` state (`Lifecycle.cpp`: `State()`, `NoteInteractive`), `ops::Meter()` via `game.state` `diagnostics.max_task_drain_ms`; Task 39's `game.state` diagnostics keys `events_dropped_total` and `events_suppressed_total` (Task 39 owns them; this task only reads them), the smoke `bench` command (Task 31), `_skython.open_consumer/call(text, timeout, consumer)/next_events/close_consumer` (Task 35).
- Produces: `spy::events::RegisterWorldSinks()`; events `hit`, `equip`, `unequip`, `container_changed`, `cell_attach`, `cell_detach`, `activate`, `player_location`, `menu_open`, `menu_close`, `game_loaded`, `game_saved`, `new_game`; log lines `events: sinks registered (batch 2)` and `events: game_loaded epoch <n>` (Tasks 41, 42, 53 wait on it); `lifecycle: kSaveGame data '<name>' (<n> bytes)` (once per launch; Task 41 reads it); `livetest/support.py` `PLAYER`, `spawn(game, base_key, dx=600.0) -> Form`, `cleanup(game, *refs)`, `neutral_fight(game, a, b)`, `box_menu(action, name)`, `translate_player(game, dx, dy, dz, speed=3000.0, settle=6.0)`; smoke commands `hold_sub` and `release_sub`.

Rules this task implements:
- Sinks as in Task 39: `Wants` first, ids and values only, `Push`, never the VM, never block, catch everything. Form ids in the engine events (`FormID` fields) are turned into references with `RE::TESForm::LookupByID` (read-locked form map, any thread); a 0 id gives `null`.
- `player_location` is raised only when the event's actor is the player (`RE::PlayerCharacter::GetSingleton()`, compared by pointer).
- **`game_loaded` fires at the interactive transition, not at `kPostLoadGame`.** Spike S6: `kPostLoadGame` arrived before `Loading Menu closed` in every load seen. So `kPostLoadGame` (success) and `kNewGame` set a pending flag; `kPreLoadGame` and a failed `kPostLoadGame` clear it; `NoteInteractive()` consumes it when `interactive` flips to true and pushes `game_loaded {new_game}` (the hub stamps the new epoch). `game_saved {name}` is pushed on `kSaveGame`, `new_game {}` on `kNewGame`. All three are `deliver_while_loading: true`, so never suppressed.
- `cell_attach` can repeat for the same reference (documented quirk, events table); tests never assert a single attach per reference.

- [ ] **Step 1: Verify the engine names**

```bash
cd plugin/extern/CommonLibSSE-NG/include
grep -n "target;\|cause;\|FormID *source\|FormID *projectile\|flags;" RE/T/TESHitEvent.h
grep -n "kPowerAttack\|kSneakAttack\|kBashAttack\|kHitBlocked" RE/T/TESHitEvent.h
grep -n "bool any\|bool all" REX/REX/EnumSet.h
grep -n "actor;\|baseObject;\|originalRefr;\|equipped;" RE/T/TESEquipEvent.h
grep -n "oldContainer;\|newContainer;\|baseObj;\|itemCount;\|ObjectRefHandle *reference" RE/T/TESContainerChangedEvent.h
grep -n "NiPointer<T> get()" RE/B/BSPointerHandle.h
grep -n "reference;\|attached;" RE/T/TESCellAttachDetachEvent.h
grep -n "objectActivated;\|actionRef;" RE/T/TESActivateEvent.h
grep -n "actor;\|oldLoc;\|newLoc;" RE/T/TESActorLocationChangeEvent.h
grep -n "BSTEventSource<TESHitEvent>\|BSTEventSource<TESEquipEvent>\|BSTEventSource<TESContainerChangedEvent>\|BSTEventSource<TESCellAttachDetachEvent>\|BSTEventSource<TESActivateEvent>\|BSTEventSource<TESActorLocationChangeEvent>" RE/S/ScriptEventSourceHolder.h
cd -
```
Expected (pin 39f9d07): `TESHitEvent { NiPointer<TESObjectREFR> target; NiPointer<TESObjectREFR> cause; FormID source; FormID projectile; REX::EnumSet<Flag, std::uint8_t> flags; }` with `Flag::kPowerAttack/kSneakAttack/kBashAttack/kHitBlocked`; `TESEquipEvent { NiPointer<TESObjectREFR> actor; FormID baseObject; FormID originalRefr; std::uint16_t uniqueID; bool equipped; }`; `TESContainerChangedEvent { FormID oldContainer; FormID newContainer; FormID baseObj; std::int32_t itemCount; ObjectRefHandle reference; std::uint16_t uniqueID; }`; `TESCellAttachDetachEvent { NiPointer<TESObjectREFR> reference; bool attached; }`; `TESActivateEvent { NiPointer<TESObjectREFR> objectActivated; NiPointer<TESObjectREFR> actionRef; }`; `TESActorLocationChangeEvent { NiPointer<TESObjectREFR> actor; BGSLocation* oldLoc; BGSLocation* newLoc; }`; all six are bases of `ScriptEventSourceHolder`. Append one row per struct to `API-VERIFICATION.md` (Task 39's table; task column 40), declarations copied from your grep output. `MenuOpenCloseEvent` is already in v1's file.

- [ ] **Step 2: `EventSinksWorld.cpp`**

Replace the stub with:
```cpp
#include "Events.h"
#include "Log.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>

namespace spy::events {
namespace {
using Ctl = RE::BSEventNotifyControl;

json ById(RE::FormID id) { return id == 0 ? json(nullptr) : FormRefJson(RE::TESForm::LookupByID(id)); }

struct HitSink final : RE::BSTEventSink<RE::TESHitEvent>
{
    Ctl ProcessEvent(const RE::TESHitEvent* e, RE::BSTEventSource<RE::TESHitEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("hit")) return Ctl::kContinue;
            NoteFirstEvent("hit");
            using F = RE::TESHitEvent::Flag;
            Push("hit", json{ { "target", FormRefJson(e->target.get()) }, { "aggressor", FormRefJson(e->cause.get()) },
                              { "source", ById(e->source) }, { "projectile", ById(e->projectile) },
                              { "power_attack", e->flags.any(F::kPowerAttack) }, { "sneak_attack", e->flags.any(F::kSneakAttack) },
                              { "bash_attack", e->flags.any(F::kBashAttack) }, { "blocked", e->flags.any(F::kHitBlocked) } });
        } catch (const std::exception& x) { NoteSinkError("hit", x.what()); } catch (...) { NoteSinkError("hit", "unknown"); }
        return Ctl::kContinue;
    }
};

struct EquipSink final : RE::BSTEventSink<RE::TESEquipEvent>
{
    Ctl ProcessEvent(const RE::TESEquipEvent* e, RE::BSTEventSource<RE::TESEquipEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->equipped ? "equip" : "unequip";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "actor", FormRefJson(e->actor.get()) }, { "item", ById(e->baseObject) },
                             { "reference", ById(e->originalRefr) } });
        } catch (const std::exception& x) { NoteSinkError("equip", x.what()); } catch (...) { NoteSinkError("equip", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ContainerSink final : RE::BSTEventSink<RE::TESContainerChangedEvent>
{
    Ctl ProcessEvent(const RE::TESContainerChangedEvent* e, RE::BSTEventSource<RE::TESContainerChangedEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("container_changed")) return Ctl::kContinue;
            NoteFirstEvent("container_changed");
            const auto ref = e->reference.get();  // handle lookup (handle-manager lock); null when the handle is empty
            Push("container_changed", json{ { "old_container", ById(e->oldContainer) }, { "new_container", ById(e->newContainer) },
                                            { "item", ById(e->baseObj) }, { "count", static_cast<int>(e->itemCount) },
                                            { "reference", FormRefJson(ref.get()) } });
        } catch (const std::exception& x) { NoteSinkError("container_changed", x.what()); } catch (...) { NoteSinkError("container_changed", "unknown"); }
        return Ctl::kContinue;
    }
};

struct CellSink final : RE::BSTEventSink<RE::TESCellAttachDetachEvent>
{
    Ctl ProcessEvent(const RE::TESCellAttachDetachEvent* e, RE::BSTEventSource<RE::TESCellAttachDetachEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->attached ? "cell_attach" : "cell_detach";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "reference", FormRefJson(e->reference.get()) } });
        } catch (const std::exception& x) { NoteSinkError("cell", x.what()); } catch (...) { NoteSinkError("cell", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ActivateSink final : RE::BSTEventSink<RE::TESActivateEvent>
{
    Ctl ProcessEvent(const RE::TESActivateEvent* e, RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("activate")) return Ctl::kContinue;
            NoteFirstEvent("activate");
            Push("activate", json{ { "target", FormRefJson(e->objectActivated.get()) }, { "activator", FormRefJson(e->actionRef.get()) } });
        } catch (const std::exception& x) { NoteSinkError("activate", x.what()); } catch (...) { NoteSinkError("activate", "unknown"); }
        return Ctl::kContinue;
    }
};

struct LocationSink final : RE::BSTEventSink<RE::TESActorLocationChangeEvent>
{
    Ctl ProcessEvent(const RE::TESActorLocationChangeEvent* e, RE::BSTEventSource<RE::TESActorLocationChangeEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("player_location")) return Ctl::kContinue;
            if (e->actor.get() != RE::PlayerCharacter::GetSingleton()) return Ctl::kContinue;
            NoteFirstEvent("player_location");
            Push("player_location", json{ { "old_location", FormRefJson(e->oldLoc) }, { "new_location", FormRefJson(e->newLoc) } });
        } catch (const std::exception& x) { NoteSinkError("player_location", x.what()); } catch (...) { NoteSinkError("player_location", "unknown"); }
        return Ctl::kContinue;
    }
};

struct MenuEventSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    Ctl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->opening ? "menu_open" : "menu_close";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            const char* menu = e->menuName.c_str();
            Push(name, json{ { "menu", menu ? menu : "" } });
        } catch (const std::exception& x) { NoteSinkError("menu", x.what()); } catch (...) { NoteSinkError("menu", "unknown"); }
        return Ctl::kContinue;
    }
};
}

void RegisterWorldSinks()
{
    static auto& hit = *new HitSink();  // immortal, as in batch 1
    static auto& equip = *new EquipSink();
    static auto& container = *new ContainerSink();
    static auto& cell = *new CellSink();
    static auto& activate = *new ActivateSink();
    static auto& location = *new LocationSink();
    static auto& menu = *new MenuEventSink();
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* ui = RE::UI::GetSingleton();
    if (!holder || !ui) {
        log::Error(std::string("events: batch 2 not registered: ") + (!holder ? "ScriptEventSourceHolder" : "RE::UI") + " is null");
        return;
    }
    holder->AddEventSink<RE::TESHitEvent>(&hit);
    holder->AddEventSink<RE::TESEquipEvent>(&equip);
    holder->AddEventSink<RE::TESContainerChangedEvent>(&container);
    holder->AddEventSink<RE::TESCellAttachDetachEvent>(&cell);
    holder->AddEventSink<RE::TESActivateEvent>(&activate);
    holder->AddEventSink<RE::TESActorLocationChangeEvent>(&location);
    ui->AddEventSink<RE::MenuOpenCloseEvent>(&menu);  // its own sink: the lifecycle's MenuSink keeps its single job
    log::Info("events: sinks registered (batch 2)");
}
}
```

- [ ] **Step 3: Lifecycle events in `Lifecycle.cpp`**

Add `#include "Events.h"` and `#include <cstring>`. In the anonymous namespace, below `std::atomic_bool g_lastInteractive{ false };`, add:
```cpp
// game_loaded waits for the interactive transition (S6: kPostLoadGame comes before the Loading Menu closes).
// 0 = nothing pending, 1 = a save load succeeded, 2 = a new game started.
std::atomic_int g_pendingLoaded{ 0 };
```
Replace `NoteInteractive` with:
```cpp
void NoteInteractive()
{
    const bool now = State().interactive();
    if (g_lastInteractive.exchange(now) == now) return;
    log::Info(std::string("lifecycle: interactive = ") + (now ? "true" : "false") + ", epoch " + std::to_string(State().epoch()));
    if (!now) return;
    const int pending = g_pendingLoaded.exchange(0);
    if (pending == 0) return;
    events::Push("game_loaded", json{ { "new_game", pending == 2 } });  // the hub stamps the new epoch
    log::Info("events: game_loaded epoch " + std::to_string(State().epoch()));
}
```
In `OnMessage`, change these cases (the flag is set **before** the state call, so a `kPostLoadGame` that arrives after the Loading Menu closed still fires at the `NoteInteractive()` at the end of `OnMessage`):
```cpp
    case SKSE::MessagingInterface::kPreLoadGame:
        g_pendingLoaded.store(0);
        State().on_pre_load_game();
        log::Info("lifecycle: loading a save");
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        const bool ok = reinterpret_cast<std::uintptr_t>(m->data) != 0;  // a packed bool, not a pointer
        g_pendingLoaded.store(ok ? 1 : 0);
        State().on_post_load_game(ok);
        log::Info(std::string("lifecycle: save load ") + (ok ? "succeeded" : "FAILED") + ", epoch " + std::to_string(State().epoch()));
        break;
    }
    case SKSE::MessagingInterface::kNewGame:
        g_pendingLoaded.store(2);
        State().on_new_game();
        events::Push("new_game", json::object());
        log::Info("lifecycle: new game, epoch " + std::to_string(State().epoch()));
        break;
    case SKSE::MessagingInterface::kSaveGame: {
        // SKSE sends the save's name as data (dataLen bytes, maybe NUL-terminated). Logged raw once per launch so the
        // RESULTS entry can confirm the payload (Task 41 matches pending saves on it).
        std::string name;
        if (m->data && m->dataLen > 0) name.assign(static_cast<const char*>(m->data), strnlen(static_cast<const char*>(m->data), m->dataLen));
        static std::atomic_bool logged{ false };
        if (!logged.exchange(true)) log::Info("lifecycle: kSaveGame data '" + name + "' (" + std::to_string(m->dataLen) + " bytes)");
        events::Push("game_saved", json{ { "name", name } });
        break;
    }
```
Keep the existing `kDataLoaded` case and `default: return;`. The `kSaveGame` case falls through to `NoteInteractive()` like the others (harmless: nothing changes).

- [ ] **Step 4: Live-test helpers (support.py) and two smoke commands**

Append to `livetest/support.py`:
```python
# ---- Task 40: world helpers. Everything here runs on s4test and is never saved. ---------------------------
from skython import errors as _errors  # noqa: E402
from skython.values import Form as _Form  # noqa: E402

PLAYER = _Form(form_id=0x14)


def spawn(game, base_key: str, dx: float = 600.0):
    """A new reference of base_key, dx units from the player (PlaceAtMe takes every argument: no defaults)."""
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, False, False)
    game.call_method(ref, "ObjectReference", "MoveTo", PLAYER, dx, 0.0, 0.0, True)
    return ref


def cleanup(game, *refs) -> None:
    for r in refs:
        try:
            game.call_method(r, "ObjectReference", "Disable", False)
            game.call_method(r, "ObjectReference", "Delete")
        except _errors.SkythonError:
            pass


def neutral_fight(game, a, b, aggression: float = 0.0) -> None:
    """a and b fight each other and leave the player alone: the player is a ghost (undo with
    game.call_method(PLAYER, "Actor", "SetGhost", False)), both get `aggression`, then StartCombat both ways.
    Aggression 0 keeps them off the player but may let them drop out of a forced fight; the tests retry once with
    aggression 3 (frenzied: still harmless to a ghost) when no hit arrives."""
    game.call_method(PLAYER, "Actor", "SetGhost", True)
    for x in (a, b):
        game.call_method(x, "Actor", "SetActorValue", "Aggression", aggression)
    game.call_method(a, "Actor", "StartCombat", b)
    game.call_method(b, "Actor", "StartCombat", a)


def box_menu(action: str, name: str) -> None:
    """Open or close a menu through DevBench on the box (Task 1c); no player input."""
    body = json.dumps({"action": action, "name": name})
    subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "--tool", "menu", body], check=True,
                   capture_output=True, text=True, timeout=90)


def translate_player(game, dx: float, dy: float, dz: float, speed: float = 3000.0, settle: float = 6.0) -> tuple:
    """Glide the player by (dx, dy, dz) with TranslateTo (no MoveTo, so no Loading Menu), wait, stop. Returns the
    position before the move."""
    x, y, z = (game.call_method(PLAYER, "ObjectReference", f"GetPosition{c}") for c in "XYZ")
    game.call_method(PLAYER, "ObjectReference", "TranslateTo", x + dx, y + dy, z + dz, 0.0, 0.0, 0.0, speed, 0.0)
    time.sleep(settle)
    game.call_method(PLAYER, "ObjectReference", "StopTranslation")
    return x, y, z
```
In `livetest/scripts/livetest_smoke/__init__.py`, add `_held: dict = {}` near the other module globals, these two functions above `_handle`:
```python
def _hold_sub(cmd: dict) -> dict:
    """An in-process consumer with one subscription that nobody drains (Review Focus 1: its queue must stay bounded)."""
    consumer = _skython.open_consumer()
    args = {"event": cmd["event"], "max_queue": int(cmd.get("max_queue", 4))}
    if cmd.get("filter") is not None:
        args["filter"] = cmd["filter"]
    r = json.loads(_skython.call(json.dumps({"type": "request", "id": 1, "op": "events.subscribe", "args": args}), 5.0, consumer))
    if not r.get("ok"):
        _skython.close_consumer(consumer)
        return {"error": r.get("error")}
    _held[consumer] = r["result"]["sub"]
    return {"consumer": consumer, "sub": r["result"]["sub"]}


def _release_sub(cmd: dict) -> dict:
    consumer = int(cmd["consumer"])
    events = json.loads(_skython.next_events(consumer, 0.5))
    _skython.close_consumer(consumer)
    _held.pop(consumer, None)
    return {"count": len(events), "seqs": [e["seq"] for e in events],
            "first_dropped": events[0].get("dropped", 0) if events else 0}
```
and in `_handle`, before the final `return`:
```python
    if name == "hold_sub":
        return _hold_sub(cmd)
    if name == "release_sub":
        return _release_sub(cmd)
```

- [ ] **Step 5: The live tests**

Create `livetest/test_m2_events_batch2.py`:
```python
"""M2 batch 2 and lifecycle events in the game (Task 40). s4test, never saved."""
import queue
import subprocess
import threading
import time

import pytest

from skython.values import to_wire
from smoke import command
from support import (PLAYER, REPO, box_menu, cleanup, neutral_fight, raw_connect, read_event, rpc, spawn, subscribe,
                     translate_player)

IRON_SWORD = "Skyrim.esm|0x012EB7"
GOLD = "Skyrim.esm|0x00000F"
WOLF = "Skyrim.esm|0x023ABE"                 # NPC_ EncWolf
BANNERED_MARE_LOCATION = 0x0001F86D          # LCTN WhiterunBanneredMareLocation (cell WhiterunBanneredMare 0x1605E)


def collect(game, event, flt, **kw):
    """A queue and the Subscription that fills it."""
    got: queue.Queue = queue.Queue()
    return got, game.on(event, flt, got.put, **kw)


def take(q: queue.Queue, n: int, timeout: float) -> list:
    out, deadline = [], time.monotonic() + timeout
    while len(out) < n and (left := deadline - time.monotonic()) > 0:
        try:
            out.append(q.get(timeout=left))
        except queue.Empty:
            break
    return out


def diagnostics(game) -> dict:
    return game.state()["diagnostics"]


def test_filters_required_for_hit_and_container_changed(session):
    s = raw_connect(session)
    try:
        for i, ev in enumerate(("hit", "container_changed"), start=1):
            r = rpc(s, "events.subscribe", {"event": ev}, req_id=i)
            assert r["ok"] is False and r["error"]["code"] == "bad_request", r
    finally:
        s.close()


def test_equip_and_unequip(game):
    sword = game.form(IRON_SWORD)
    q, sub_e = collect(game, "equip", {"actor": [PLAYER], "item": [sword]})
    sub_u = game.on("unequip", {"actor": [PLAYER], "item": [sword]}, q.put)
    game.call_method(PLAYER, "ObjectReference", "AddItem", sword, 1, True)
    try:
        game.call_method(PLAYER, "Actor", "EquipItem", sword, False, True)
        game.call_method(PLAYER, "Actor", "UnequipItem", sword, False, True)
        got = take(q, 2, 10.0)
        assert [e.name for e in got] == ["equip", "unequip"], got
        for e in got:
            assert e.data["actor"].form_id == 0x14 and e.data["item"].form_key == IRON_SWORD
    finally:
        sub_e.close()
        sub_u.close()
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", sword, 1, True, None)


def test_container_changed_both_directions(game):
    gold = game.form(GOLD)
    q, sub_in = collect(game, "container_changed", {"new_container": [PLAYER], "item": [gold]})
    sub_out = game.on("container_changed", {"old_container": [PLAYER], "item": [gold]}, q.put)
    try:
        game.call_method(PLAYER, "ObjectReference", "AddItem", gold, 5, True)
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", gold, 5, True, None)
        got = take(q, 2, 10.0)
        assert len(got) == 2, got
        added, removed = got
        assert added.data["new_container"].form_id == 0x14 and added.data["old_container"] is None and added.data["count"] == 5
        assert removed.data["old_container"].form_id == 0x14 and removed.data["new_container"] is None
        assert added.data["item"].form_key == GOLD
    finally:
        sub_in.close()
        sub_out.close()


def test_activate(game):
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(IRON_SWORD), 1, False, False)
    q, sub = collect(game, "activate", {"target": [ref]})
    try:
        game.call_method(ref, "ObjectReference", "Activate", PLAYER, False)  # picks the sword up
        got = take(q, 1, 10.0)
        assert got and got[0].data["target"].form_id == ref.form_id and got[0].data["activator"].form_id == 0x14, got
    finally:
        sub.close()
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", game.form(IRON_SWORD), 1, True, None)


def test_menu_open_and_close(game):
    q, sub_o = collect(game, "menu_open", {"menu": ["inventorymenu"]})  # case-insensitive string filter
    sub_c = game.on("menu_close", {"menu": ["InventoryMenu"]}, q.put)
    try:
        box_menu("open", "InventoryMenu")
        time.sleep(1.0)
        box_menu("close", "InventoryMenu")
        got = take(q, 2, 15.0)
        assert [(e.name, e.data["menu"]) for e in got] == [("menu_open", "InventoryMenu"), ("menu_close", "InventoryMenu")], got
    finally:
        sub_o.close()
        sub_c.close()


def test_cell_attach_and_detach_without_a_loading_screen(game):
    if game.call_method(PLAYER, "ObjectReference", "IsInInterior"):
        pytest.fail("s4test starts in an interior: this test needs an exterior; STOP and ask the owner")
    attach: queue.Queue = queue.Queue()
    detach: queue.Queue = queue.Queue()
    loading: queue.Queue = queue.Queue()
    subs = [game.on("cell_attach", None, attach.put, max_queue=4096), game.on("cell_detach", None, detach.put, max_queue=4096),
            game.on("menu_open", {"menu": ["Loading Menu"]}, loading.put)]
    try:
        x, y, z = translate_player(game, 6144.0, 0.0, 600.0)
        game.call_method(PLAYER, "ObjectReference", "TranslateTo", x, y, z, 0.0, 0.0, 0.0, 3000.0, 0.0)  # glide back
        time.sleep(6.0)
        game.call_method(PLAYER, "ObjectReference", "StopTranslation")
        a, d = take(attach, 1, 5.0), take(detach, 1, 5.0)
        print(f"cell events: attach>={attach.qsize() + len(a)} detach>={detach.qsize() + len(d)} loading_menus={loading.qsize()}")
        assert loading.empty(), "TranslateTo opened a Loading Menu: record it in RESULTS and ask the owner"
        assert a and d, "no cell_attach or no cell_detach after moving 1.5 cells and back"
        assert a[0].data["reference"].form_id and a[0].suppressed == 0
    finally:
        for s in subs:
            s.close()


def test_hit_between_two_spawned_actors(game):
    a, b = spawn(game, WOLF, 1200.0), spawn(game, WOLF, 1400.0)
    q, sub = collect(game, "hit", {"target": [a]})
    try:
        neutral_fight(game, a, b)
        got = take(q, 1, 20.0)
        if not got:  # they dropped out of the forced fight: once more, frenzied (record which setting fought)
            neutral_fight(game, a, b, aggression=3.0)
            got = take(q, 1, 20.0)
            print("hit: needed aggression 3")
        assert got and got[0].data["target"].form_id == a.form_id and got[0].data["aggressor"].form_id == b.form_id, got
        assert all(isinstance(got[0].data[k], bool) for k in ("power_attack", "sneak_attack", "bash_attack", "blocked"))
    finally:
        sub.close()
        cleanup(game, a, b)
        game.call_method(PLAYER, "Actor", "SetGhost", False)


def test_hit_flood_never_stalls_the_game(game, session):
    """Review Focus 1: a drained subscription (max_queue 16) and an undrained in-process one (max_queue 4) during
    30 s of combat; the held queue stays bounded and call latency (one frame per call) does not move. The held one
    uses max_queue 4 rather than 16 so that two wolves (about one hit a second) certainly overflow it."""
    a, b = spawn(game, WOLF, 1200.0), spawn(game, WOLF, 1400.0)
    s = raw_connect(session)
    frames: list = []
    stop = threading.Event()
    try:
        subscribe(s, "hit", {"target": [to_wire(a)]}, req_id=1, max_queue=16)
        held = command("hold_sub", event="hit", filter={"target": [to_wire(a)]}, max_queue=4)
        before = command("bench", calls=100)["p50_ms"]
        drain_before = diagnostics(game)["max_task_drain_ms"]

        def reader():
            while not stop.is_set():
                if (f := read_event(s, 0.5)) is not None:
                    frames.append(f)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        neutral_fight(game, a, b)
        time.sleep(10.0)
        if not frames:  # same fallback as the single-hit test
            neutral_fight(game, a, b, aggression=3.0)
            print("flood: needed aggression 3")
        during = [command("bench", calls=100)["p50_ms"] for _ in range(5)]  # about 25 s of calls while they fight
        stop.set()
        t.join(5)
        released = command("release_sub", consumer=held["consumer"])
        diag = diagnostics(game)
        print(f"flood: frames={len(frames)} held={released} p50 before={before} during={during} "
              f"drain before={drain_before} after={diag['max_task_drain_ms']} dropped_total={diag['events_dropped_total']}")
        assert frames, "no hit events during 30 s of combat"
        assert released["count"] <= 4
        drained_total = len(frames) + sum(f.get("dropped", 0) for f in frames)  # every hit the drained sub accepted
        if released["seqs"]:
            # Both subscriptions see the same hits; the held one also counts hits after the reader stopped.
            assert released["seqs"][-1] >= drained_total and released["first_dropped"] == released["seqs"][0] - 1
        assert max(during) <= before * 1.15 + 5.0, (before, during)
        assert diag["max_task_drain_ms"] < 5.0  # Task 31 baseline: 2.25 ms at a 1024-request burst
    finally:
        stop.set()
        s.close()
        cleanup(game, a, b)
        game.call_method(PLAYER, "Actor", "SetGhost", False)


def test_player_location_after_coc(game):
    """Runs last: it moves the player indoors. Events raised during the load are suppressed by design (Contracts:
    loading rule), so the test accepts a delivered event or a rise of events_suppressed_total."""
    q, sub = collect(game, "player_location", None)
    loaded: queue.Queue = queue.Queue()
    sub_l = game.on("menu_close", {"menu": ["Loading Menu"]}, loaded.put)
    suppressed_before = diagnostics(game)["events_suppressed_total"]
    try:
        subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc WhiterunBanneredMare"], check=True,
                       capture_output=True, text=True, timeout=90)
        assert take(loaded, 1, 120.0), "no Loading Menu close within 120 s after coc"
        game.wait_ready(60.0)
        got = take(q, 1, 10.0)
        rose = diagnostics(game)["events_suppressed_total"] - suppressed_before
        print(f"player_location: delivered={[(e.data['new_location'].form_id if e.data['new_location'] else None) for e in got]} "
              f"suppressed_rise={rose}")
        assert got or rose > 0
        if got:
            assert got[0].data["new_location"].form_id == BANNERED_MARE_LOCATION
    finally:
        sub.close()
        sub_l.close()
```
Check it parses and skips without the game: `SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_events_batch2.py -q` → `9 skipped`.

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
tools/box/box_wait_log.sh Skython.log "events: sinks registered \(batch 2\)" 60
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load s4test"      # never saved; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch2.py -q -s -rs
tools/box/box_stop.sh
```
pytest runs the file in order; `test_player_location_after_coc` must run last (it is last in the file). Then a second launch for the regression run on a clean `s4test` (the first one ended indoors):
```bash
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch1.py -q -rs
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest --deselect livetest/test_m1_mainmenu.py --deselect livetest/test_m1_throughput.py --deselect livetest/test_m2_events_batch2.py -q -rs
tools/box/box_files.sh pull Skython.log >/dev/null
grep "events: \|lifecycle: kSaveGame" "$SKSE_LOG_DIR/Skython.log"
grep '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
```
Expected: launch 1: 9 passed (the flood test prints its numbers: keep them); `events: game_loaded epoch 1` appeared **after** `lifecycle: Loading Menu closed` in the log (check the order with `grep -n`; if it came before, `NoteInteractive` is wrong). Launch 2: batch 1 still 8 passed; the M1 suite 77 passed with its known skips; no `sink threw`; `no unexpected errors`. `lifecycle: kSaveGame` does not appear (nothing saved).

If `test_cell_attach_and_detach_without_a_loading_screen` fails because the player did not move (positions equal), or a Loading Menu opened: record it in RESULTS with the printed line and STOP; do not switch to `MoveTo` (that opens a Loading Menu and every cell event would be suppressed by design).

- [ ] **Step 8: Record**

Append to `spikes/RESULTS.md`: `**Task 40, sinks batch 2 and lifecycle events (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test, never saved; 2 launches; SKSE plugins: <list>):**` pass counts; every `events: first <event> event on thread ...` line; the order of `Loading Menu closed` and `events: game_loaded epoch 1`; the cell line (`attach>=.. detach>=.. loading_menus=0`) and whether attach repeated for one reference; the player_location line (delivered or suppressed: the loading rule at work); whether the wolves fought at aggression 0 or needed the aggression 3 retry; the flood line verbatim with the Review Focus 1 verdict (held queue at most 4, `first_dropped` equals the seq gap, bench p50 before/during, `max_task_drain_ms` before/after).

- [ ] **Step 9: Commit**

```bash
git add plugin/src/EventSinksWorld.cpp plugin/src/Lifecycle.cpp livetest/support.py livetest/scripts/livetest_smoke/__init__.py \
  livetest/test_m2_events_batch2.py docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md spikes/RESULTS.md
git commit -m "feat(plugin): world, menu and lifecycle event sinks with a live hit-flood check"
```
