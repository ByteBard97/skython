# Task 39: Plugin event hub and sinks batch 1 (death/dying, quest stage/start/stop, mod events, channel queue)

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Contracts** (events table, `EventHub`, `_skython` additions, Plugin) and Part E's brief for Task 39 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (code, the API check), then the Windows build box (build) and the game on the box (live tests), exactly as v1 Part C did ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md): `tools/remote_build.sh`, `tools/box/*`; never the Linux desktop). Plugin code builds only on Windows, so this task's "test" for C++ is the build (0 warnings) plus the live tests.
>
> **STOP and ask the owner (do not improvise) if:** a struct or field named below differs at the pin by more than a rename; the game crashes or hangs during a live test; the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s; quest `dunKarthspireRedoubtQST` (Skyrim.esm|0x0357E5) is already running or completed on `s4test` (pick nothing else yourself: report it); the game runs on anything other than AE 1.6.1170.
>
> **Box rules:** `s4test` is loaded and **never saved** in this task (no `save` command, no `game.save`). End every game with `tools/box/box_stop.sh` (not `qqq`); `NOT_CLEAN` + `NO_NEW_CRASH_LOG` with the R46 signature is the box baseline, not ours. List the SKSE plugins loaded in the RESULTS entry.

**Files:**
- Create: `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`
- Create: `plugin/src/Events.h`, `plugin/src/Events.cpp`, `plugin/src/EventSinks.cpp`
- Modify: `plugin/src/PapyrusValues.h`, `plugin/src/PapyrusValues.cpp` (a VM-free form describer for sinks)
- Modify: `plugin/src/main.cpp` (`RegisterSinks()` at `kDataLoaded`), `plugin/src/GameBackend.cpp` (`DispatcherOptions::events`, hub totals in diagnostics), `plugin/src/PythonHost.cpp` (`ModuleHost::events`; the README's File Structure says `PyModule.cpp`, but the `ModuleHost` is filled in `PythonHost.cpp`), `plugin/CMakeLists.txt`
- Modify: `livetest/support.py` (append `subscribe`, `read_event`), `livetest/scripts/livetest_smoke/__init__.py` (a `mod_event` command)
- Create: `livetest/test_m2_events_batch1.py`
- Modify: `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 34 `spy::EventHub`, `spy::EventHubOptions{epoch, loading}`, `EventHub::Wants/Push/Consumers/Subscriptions/DroppedTotal/SuppressedTotal` (`core/include/spy/events.h`); Task 35 `DispatcherOptions::events` (`EventHub*`), `ModuleHost::events` (`EventHub*`), `_skython.open_consumer/close_consumer/next_events`; Task 36 `Game.on(event, filter, fn, *, max_queue)`, `Game.on_mod_event(name, fn)`, `Subscription` (context manager, `close()`), `Event` (`name, sub, seq, epoch, data, dropped, suppressed`); Task 37 remote event delivery (the same `Game.on` over the bridge); v1: `life::Epoch()`, `life::Loading()` (`Lifecycle.h`), `pv::DescribeForm` internals (`SourceFile`, `KeyOrId`, `is_runtime_form_id`, `canonical_script_name`), `ops::InTask`, `ops::Measured`, `ops::Meter()` (`Ops.h`), `form_ref_to_json` (`spy/values.h`), `pv::FloatToJson`.
- Produces: `spy::events::Hub() -> spy::EventHub&`, `spy::events::Push(std::string_view, json) noexcept`, `spy::events::FormRefJson(const RE::TESForm*) -> json`, `spy::events::RegisterSinks() -> void` (`Events.h`); `spy::events::RegisterWorldSinks() -> void` is **declared** here and defined by Task 40 (`EventSinksWorld.cpp`); `pv::BuildClassTable() -> void`, `pv::DescribeFormForEvent(const RE::TESForm*) -> FormRef` (VM-free; safe on any engine thread); the log line `events: sinks registered (batch 1)`; `game.state` `diagnostics` gains `event_consumers`, `event_subscriptions`, `events_dropped_total`, `events_suppressed_total` (Task 42 and Task 56 read them); `livetest/support.py` `subscribe(sock, event, filter=None, req_id=1, max_queue=None) -> int`, `read_event(sock, timeout) -> dict | None`, `read_events(sock, n, timeout) -> list[dict]`; the smoke command `mod_event`.

Rules this task implements:
- **Sinks never call into the VM, never take the GIL, never block.** A sink checks `Hub().Wants(name)` first and returns at once when nothing is subscribed; otherwise it copies form ids and plain values, builds the event's `json`, and calls `events::Push`. `pv::DescribeForm` is **not** usable from a sink: its `ScriptClassOf` calls `IVirtualMachine::GetScriptObjectType`, and a sink can run on a VM thread (S5: sinks run on varying engine threads). Sinks use `pv::DescribeFormForEvent`, which reads the form id, the data handler's load order (`SourceFile`) and the base object only, and takes the Papyrus class name from a table built once inside an SKSE task (`pv::BuildClassTable`, same `canonical_script_name` spelling as R56, so `type` matches what calls return). Until the table is built (the first SKSE task after `kDataLoaded`, at the main menu, before any gameplay event) the type is `"Form"`.
- **Sinks register once, at `kDataLoaded`, directly** (as the existing `MenuSink` does). Registering or unregistering mid-game is Task 44's probe, not this task's assumption.
- Every sink catches `std::exception` and `...`; the first failure per sink is logged once (`events: <sink> sink threw: ...`), later ones are counted silently.
- `channel` has no producer yet (Task 46 adds the native `Skython.Send`); the hub already accepts subscriptions to it from the events table.

- [ ] **Step 1: Verify every engine name at the pin and start this plan's `API-VERIFICATION.md`**

Grep the pinned clone, never memory. Each command must print the line shown (field names and order); a difference beyond a rename is a STOP.
```bash
cd plugin/extern/CommonLibSSE-NG/include
grep -n "actorDying\|actorKiller\|bool *dead" RE/T/TESDeathEvent.h          # NiPointer<TESObjectREFR> actorDying; actorKiller; bool dead
grep -n "FormID *formID\|std::uint16_t *stage" RE/T/TESQuestStageEvent.h    # FormID formID; std::uint16_t stage
grep -n "FormID *formID\|bool *started" RE/T/TESQuestStartStopEvent.h       # FormID formID; bool started
grep -n "eventName\|strArg\|numArg\|sender" SKSE/Events.h                   # ModCallbackEvent: BSFixedString eventName, strArg; float numArg; TESForm* sender
grep -n "GetModCallbackEventSource" SKSE/API.h                              # RE::BSTEventSource<ModCallbackEvent>* GetModCallbackEventSource() noexcept
grep -n "static ScriptEventSourceHolder\* GetSingleton\|inline void AddEventSink\|inline void RemoveEventSink" RE/S/ScriptEventSourceHolder.h
grep -n "BSTEventSource<TESDeathEvent>\|BSTEventSource<TESQuestStageEvent>\|BSTEventSource<TESQuestStartStopEvent>" RE/S/ScriptEventSourceHolder.h
grep -n "pendingRegisters\|BSSpinLockGuard" RE/B/BSTEvent.h | head -4        # AddEventSink is spin-locked and defers during notify
grep -n "^\s*Max" RE/F/FormTypes.h                                          # FormType::Max (0x8A at the pin)
cd -
```
Create `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`:
```markdown
# CommonLibSSE-NG API verification for the M1.5/M2 plan

Every signature not listed here is in the v1 plan's file: [`../2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md`](../2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md). Same pin: `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (v10.1.0), read from `plugin/extern/CommonLibSSE-NG`. Each task appends the names it checked, with the header path and the verbatim declaration.

| Name | Task | Declaration at the pin (header under `include/`) | Notes |
|---|---|---|---|
| `TESDeathEvent` | 39 | `NiPointer<TESObjectREFR> actorDying; NiPointer<TESObjectREFR> actorKiller; bool dead;` (`RE/T/TESDeathEvent.h`) | `dead == false` → `dying`, `true` → `death` |
| `TESQuestStageEvent` | 39 | `void* finishedCallback; FormID formID; std::uint16_t stage; std::uint8_t itemIndex;` (`RE/T/TESQuestStageEvent.h`) | quest looked up with `TESForm::LookupByID(formID)` |
| `TESQuestStartStopEvent` | 39 | `FormID formID; bool started; bool failed;` (`RE/T/TESQuestStartStopEvent.h`) | `started` → `quest_start`, else `quest_stop` |
| `SKSE::ModCallbackEvent` | 39 | `BSFixedString eventName; BSFixedString strArg; float numArg; TESForm* sender;` (`SKSE/Events.h`) | source: `SKSE::GetModCallbackEventSource()` (`SKSE/API.h`) |
| `ScriptEventSourceHolder` | 39 | `static ScriptEventSourceHolder* GetSingleton(); template <class T> void AddEventSink(BSTEventSink<T>*); template <class T> void RemoveEventSink(BSTEventSink<T>*);` (`RE/S/ScriptEventSourceHolder.h`) | `BSTEventSource::AddEventSink` takes a spin lock and defers to `pendingRegisters` while notifying (`RE/B/BSTEvent.h`) |
| `RE::FormType::Max` | 39 | last enumerator of `enum class FormType` (`RE/F/FormTypes.h`, value 0x8A) | class table size |
```
Fill each row's declaration from what your grep printed (verbatim), not from this text, if they differ in spelling.

- [ ] **Step 2: A VM-free form describer in `PapyrusValues`**

Append to `plugin/src/PapyrusValues.h`, inside `namespace spy::pv`:
```cpp
// Task 39. Fills the FormType -> Papyrus class name table (canonical_script_name spelling, R56) through the VM, once.
// Inside an SKSE task only. Idempotent; a second call rebuilds nothing.
void BuildClassTable();
// Task 39. DescribeForm without the VM: safe on any engine thread (event sinks). Reads the form id, the data handler's
// load order, the base object, and the class table (type "Form" until BuildClassTable has run).
FormRef DescribeFormForEvent(const RE::TESForm* form);
```
In `plugin/src/PapyrusValues.cpp`, add `#include <array>` and `#include <atomic>` to the includes and put this in the anonymous namespace, **after** `KeyOrId` (it uses it):
```cpp
// Task 39: written once inside an SKSE task, then published; sinks read it from any thread without a lock.
using ClassTable = std::array<std::string, 256>;
std::atomic<const ClassTable*> g_classTable{ nullptr };

std::string CachedClassOf(const RE::TESForm* form)
{
    const ClassTable* t = g_classTable.load(std::memory_order_acquire);
    if (!t) return "Form";
    const auto& name = (*t)[static_cast<std::uint8_t>(form->GetFormType())];
    return name.empty() ? std::string("Form") : name;
}
```
And outside it, at the end of `namespace spy::pv`:
```cpp
void BuildClassTable()
{
    if (g_classTable.load(std::memory_order_acquire)) return;
    auto* table = new ClassTable();  // immortal: sinks may read it during process exit
    std::size_t filled = 0;
    for (std::size_t t = 0; t < static_cast<std::size_t>(RE::FormType::Max); ++t) {
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
        if (VM()->GetScriptObjectType(static_cast<RE::VMTypeID>(t), info) && info) {
            (*table)[t] = canonical_script_name(info->GetName());
            ++filled;
        }
    }
    g_classTable.store(table, std::memory_order_release);
    log::Info(std::format("events: class table built ({} form types; Actor = {})", filled,
                          (*table)[static_cast<std::size_t>(RE::FormType::ActorCharacter)]));
}

FormRef DescribeFormForEvent(const RE::TESForm* form)
{
    FormRef r;
    r.form_id = form->GetFormID();
    r.type = CachedClassOf(form);
    r.epoch = life::Epoch();
    if (is_runtime_form_id(*r.form_id)) {
        const RE::TESForm* base = form;
        if (const auto* ref = form->As<RE::TESObjectREFR>())
            if (const auto* b = ref->GetBaseObject()) base = b;
        r.fingerprint = Fingerprint{ KeyOrId(base), r.type };
    } else if (SourceFile(form)) {
        r.form_key = KeyOrId(form);
    }
    return r;
}
```
`LiveFingerprint` takes its type from `ScriptClassOf` (the VM); the event fingerprint takes it from the table, which holds the same `canonical_script_name` result, so `ResolveForm`'s fingerprint check accepts an event's form reference later. Do not change `DescribeForm` or `LiveFingerprint`.

- [ ] **Step 3: `Events.h` and `Events.cpp`**

`plugin/src/Events.h`:
```cpp
#pragma once
#include "spy/errors.h"  // spy::json
#include "spy/events.h"
#include <string_view>

namespace RE { class TESForm; }

// The plugin's event plumbing (Contracts: Plugin). Sinks run on engine threads that vary (S5): they never touch the VM
// or Python, never block, and only copy ids and values into Push.
namespace spy::events {
spy::EventHub& Hub();                                  // immortal; epoch = life::Epoch, loading = life::Loading
void Push(std::string_view event, json data) noexcept;
json FormRefJson(const RE::TESForm* form);             // a form reference, or null for a null form
void RegisterSinks();                                  // once, at kDataLoaded: batch 1 here, then RegisterWorldSinks
void RegisterWorldSinks();                             // batch 2 (Task 40, EventSinksWorld.cpp)
void NoteSinkError(const char* sink, const char* what) noexcept;  // logs the first failure per sink only
void NoteFirstEvent(const char* event) noexcept;                  // one line per event name: the S5-style thread note
}
```
`plugin/src/Events.cpp`:
```cpp
#include "Events.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "PapyrusValues.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <format>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>

namespace spy::events {
spy::EventHub& Hub()
{
    static spy::EventHub* hub = new spy::EventHub(spy::EventHubOptions{
        [] { return life::Epoch(); },
        [] { return life::Loading(); } });
    return *hub;
}

void Push(std::string_view event, json data) noexcept
{
    try {
        Hub().Push(event, std::move(data));
    } catch (...) {  // Push is documented never to throw; this is the belt to that brace
        static std::atomic_bool once{ false };
        if (!once.exchange(true)) log::Error("events: EventHub::Push threw; further failures are not logged");
    }
}

json FormRefJson(const RE::TESForm* form)
{
    if (!form) return nullptr;
    return form_ref_to_json(pv::DescribeFormForEvent(form));
}

void NoteSinkError(const char* sink, const char* what) noexcept
{
    try {
        static std::mutex* mx = new std::mutex;
        static std::set<std::string>* seen = new std::set<std::string>;
        std::lock_guard lk(*mx);
        if (seen->insert(sink).second) log::Error(std::format("events: {} sink threw: {}; later failures of this sink are not logged", sink, what));
    } catch (...) {}
}

void NoteFirstEvent(const char* event) noexcept
{
    try {
        static std::mutex* mx = new std::mutex;
        static std::set<std::string>* seen = new std::set<std::string>;
        std::lock_guard lk(*mx);
        if (!seen->insert(event).second) return;
        std::ostringstream tid;
        tid << std::this_thread::get_id();
        // A note for RESULTS only (S5 style). Nothing in the plugin keys on a thread id (R49).
        log::Info(std::format("events: first {} event on thread {} (inside our SKSE task: {})", event, tid.str(),
                              ops::t_inTask ? "yes" : "no"));
    } catch (...) {}
}

void RegisterBatch1();  // EventSinks.cpp

void RegisterSinks()
{
    // The class table needs the VM, so it is built inside one SKSE task (the first frame after kDataLoaded, at the main
    // menu). Sinks register now, directly, like the lifecycle's MenuSink; until the table exists an event's type is "Form".
    SKSE::GetTaskInterface()->AddTask([] {
        ops::InTask in;
        ops::Measured measured;
        try {
            pv::BuildClassTable();
        } catch (const std::exception& e) {
            log::Error(std::string("events: class table failed: ") + e.what());
        } catch (...) {
            log::Error("events: class table failed");
        }
    });
    RegisterBatch1();
    RegisterWorldSinks();
}
}
```
`RegisterWorldSinks` does not exist until Task 40. So that this task links on its own, create `plugin/src/EventSinksWorld.cpp` now with only:
```cpp
#include "Events.h"

namespace spy::events {
void RegisterWorldSinks() {}  // Task 40 replaces this file
}
```

- [ ] **Step 4: `EventSinks.cpp` (batch 1)**

```cpp
#include "Events.h"
#include "Log.h"
#include "PapyrusValues.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>

namespace spy::events {
namespace {
using Ctl = RE::BSEventNotifyControl;

struct DeathSink final : RE::BSTEventSink<RE::TESDeathEvent>
{
    Ctl ProcessEvent(const RE::TESDeathEvent* e, RE::BSTEventSource<RE::TESDeathEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->dead ? "death" : "dying";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "victim", FormRefJson(e->actorDying.get()) }, { "killer", FormRefJson(e->actorKiller.get()) } });
        } catch (const std::exception& x) { NoteSinkError("death", x.what()); } catch (...) { NoteSinkError("death", "unknown"); }
        return Ctl::kContinue;
    }
};

struct QuestStageSink final : RE::BSTEventSink<RE::TESQuestStageEvent>
{
    Ctl ProcessEvent(const RE::TESQuestStageEvent* e, RE::BSTEventSource<RE::TESQuestStageEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("quest_stage")) return Ctl::kContinue;
            NoteFirstEvent("quest_stage");
            Push("quest_stage", json{ { "quest", FormRefJson(RE::TESForm::LookupByID(e->formID)) },
                                      { "stage", static_cast<int>(e->stage) } });
        } catch (const std::exception& x) { NoteSinkError("quest_stage", x.what()); } catch (...) { NoteSinkError("quest_stage", "unknown"); }
        return Ctl::kContinue;
    }
};

struct QuestStartStopSink final : RE::BSTEventSink<RE::TESQuestStartStopEvent>
{
    Ctl ProcessEvent(const RE::TESQuestStartStopEvent* e, RE::BSTEventSource<RE::TESQuestStartStopEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->started ? "quest_start" : "quest_stop";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "quest", FormRefJson(RE::TESForm::LookupByID(e->formID)) } });
        } catch (const std::exception& x) { NoteSinkError("quest_start_stop", x.what()); } catch (...) { NoteSinkError("quest_start_stop", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ModSink final : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    Ctl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("mod_event")) return Ctl::kContinue;
            NoteFirstEvent("mod_event");
            const char* name = e->eventName.c_str();
            const char* str = e->strArg.c_str();
            Push("mod_event", json{ { "name", name ? name : "" }, { "str_arg", str ? str : "" },
                                    { "num_arg", pv::FloatToJson(e->numArg) }, { "sender", FormRefJson(e->sender) } });
        } catch (const std::exception& x) { NoteSinkError("mod_event", x.what()); } catch (...) { NoteSinkError("mod_event", "unknown"); }
        return Ctl::kContinue;
    }
};
}

void RegisterBatch1()
{
    static auto& death = *new DeathSink();  // immortal: the engine keeps the pointer for the life of the process
    static auto& stage = *new QuestStageSink();
    static auto& startStop = *new QuestStartStopSink();
    static auto& mod = *new ModSink();
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* modSource = SKSE::GetModCallbackEventSource();
    if (!holder || !modSource) {
        log::Error(std::string("events: batch 1 not registered: ") + (!holder ? "ScriptEventSourceHolder" : "ModCallbackEvent source") + " is null");
        return;
    }
    holder->AddEventSink<RE::TESDeathEvent>(&death);
    holder->AddEventSink<RE::TESQuestStageEvent>(&stage);
    holder->AddEventSink<RE::TESQuestStartStopEvent>(&startStop);
    modSource->AddEventSink(&mod);
    log::Info("events: sinks registered (batch 1)");
}
}
```
`json{...}` with a `json` value of `nullptr` (from `FormRefJson(nullptr)`) gives a JSON `null`, as the events table's `form?` fields require.

- [ ] **Step 5: Wire the hub into the dispatcher, `_skython` and the load sequence**

`plugin/src/GameBackend.cpp`: add `#include "Events.h"`. In `GetDispatcher()`, after `opts.on_late = ...;`:
```cpp
        opts.events = &events::Hub();  // events.subscribe / events.unsubscribe are answered by the hub (Task 35)
```
In `WithDiagnostics`, after the existing `s["diagnostics"] = {...};` line:
```cpp
        auto& hub = events::Hub();
        s["diagnostics"]["event_consumers"] = hub.Consumers();
        s["diagnostics"]["event_subscriptions"] = hub.Subscriptions();
        s["diagnostics"]["events_dropped_total"] = hub.DroppedTotal();
        s["diagnostics"]["events_suppressed_total"] = hub.SuppressedTotal();
```
`plugin/src/PythonHost.cpp`: add `#include "Events.h"` and, after `host.dispatcher = &game::GetDispatcher();`, the line `host.events = &events::Hub();`. If Task 35 placed `events` elsewhere in `ModuleHost`, still set it by name (never positional).

`plugin/src/main.cpp`: add `#include "Events.h"` and `#include "GameBackend.h"` and replace the `kDataLoaded` line in `OnMessage` with:
```cpp
        if (m->type == SKSE::MessagingInterface::kDataLoaded) {
            spy::game::GetDispatcher();      // the dispatcher (and through it the hub) exists first
            spy::events::RegisterSinks();    // before Python starts: no subscription can race the sink setup (Task 44 relies on it)
            spy::host::Start(spy::settings::Get());
        }
```
`plugin/CMakeLists.txt`: add `src/Events.cpp src/EventSinks.cpp src/EventSinksWorld.cpp` to `SPY_CORE_SOURCES`.

- [ ] **Step 6: Live-test helpers and the in-process command**

Append to `livetest/support.py`:
```python
def subscribe(sock: socket.socket, event: str, filter: dict | None = None, req_id: int = 1,
              max_queue: int | None = None) -> int:
    """events.subscribe on a raw connection; returns the sub id. Event frames that arrive meanwhile are dropped by rpc,
    so subscribe before triggering anything."""
    args: dict = {"event": event}
    if filter is not None:
        args["filter"] = filter
    if max_queue is not None:
        args["max_queue"] = max_queue
    r = rpc(sock, "events.subscribe", args, req_id)
    assert r["ok"] is True, r
    return r["result"]["sub"]


def read_event(sock: socket.socket, timeout: float) -> dict | None:
    """The next event frame within timeout seconds (responses are skipped), or None."""
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            f = read_frame(sock)
        except (socket.timeout, TimeoutError):
            return None
        if f.get("type") == "event":
            return f
    return None


def read_events(sock: socket.socket, n: int, timeout: float) -> list[dict]:
    """Up to n event frames within timeout seconds in total."""
    out: list[dict] = []
    deadline = time.monotonic() + timeout
    while len(out) < n and (left := deadline - time.monotonic()) > 0:
        if (f := read_event(sock, left)) is None:
            break
        out.append(f)
    return out
```
In `livetest/scripts/livetest_smoke/__init__.py`, add `from skython.values import Form` to the imports (if absent), this function above `_handle`:
```python
def _mod_event(game, event_name: str) -> dict:
    """In-process: subscribe through Game.on_mod_event, raise the event with Form.SendModEvent, report what arrived."""
    got: list = []
    done = threading.Event()

    def on_event(ev) -> None:
        sender = ev.data.get("sender")
        got.append({"name": ev.data["name"], "str_arg": ev.data["str_arg"], "num_arg": ev.data["num_arg"],
                    "sender": sender.form_id if sender is not None else None, "seq": ev.seq, "epoch": ev.epoch})
        done.set()

    with game.on_mod_event(event_name, on_event):
        game.call_method(Form(form_id=0x14), "Form", "SendModEvent", event_name, "in-process", 2.5)
        done.wait(10)
    return {"received": got}
```
and in `_handle`, before the final `return {"error": ...}`:
```python
    if name == "mod_event":
        return _mod_event(game, str(cmd.get("event", "skython_smoke")))
```

- [ ] **Step 7: The live tests**

Create `livetest/test_m2_events_batch1.py`:
```python
"""M2 batch 1 events in the game (Task 39): death/dying, quest stage/start/stop, mod events, the channel queue.
s4test loaded and never saved. Raw frames check the wire shape; Game.on checks the Python path over the bridge."""
import queue
import time

import pytest

from skython import errors
from skython.values import Form, to_wire
from smoke import command
from support import raw_connect, read_event, read_events, rpc, subscribe

PLAYER = Form(form_id=0x14)
CHICKEN = "Skyrim.esm|0x0A91A0"       # NPC_ EncChicken (checked in Skyrim.esm, 2026-10-02)
QUEST = "Skyrim.esm|0x0357E5"         # QUST dunKarthspireRedoubtQST: no script, no aliases, stages 10 and 100


def spawn(game, base_key: str, dx: float = 600.0) -> Form:
    """A new actor from base_key, placed dx units from the player (never saved: s4test)."""
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, False, False)
    game.call_method(ref, "ObjectReference", "MoveTo", PLAYER, dx, 0.0, 0.0, True)
    return ref


def cleanup(game, *refs: Form) -> None:
    for r in refs:
        try:
            game.call_method(r, "ObjectReference", "Disable", False)
            game.call_method(r, "ObjectReference", "Delete")
        except errors.SkythonError:
            pass


def frames_for(frames: list[dict], sub: int) -> list[dict]:
    return [f for f in frames if f["sub"] == sub]


def test_death_and_dying_with_victim_filter(game, session):
    a, b = spawn(game, CHICKEN, 600.0), spawn(game, CHICKEN, -600.0)
    s = raw_connect(session)
    try:
        dying = subscribe(s, "dying", {"victim": [to_wire(a)]}, req_id=1)
        death = subscribe(s, "death", {"victim": [to_wire(a)]}, req_id=2)
        epoch = rpc(s, "game.state", req_id=3)["result"]["epoch"]
        game.call_method(a, "Actor", "Kill", None)
        game.call_method(b, "Actor", "Kill", None)  # filtered out: must never arrive
        frames = read_events(s, 2, timeout=10.0) + read_events(s, 5, timeout=3.0)
        assert [f["event"] for f in frames_for(frames, dying)] == ["dying"], frames
        assert [f["event"] for f in frames_for(frames, death)] == ["death"], frames
        for f in frames:
            assert f["type"] == "event" and f["seq"] == 1 and f["epoch"] == epoch
            assert f["data"]["victim"]["form_id"] == a.form_id and f["data"]["victim"]["type"] == "Actor"
            assert f["data"]["killer"] is None  # Kill(None)
            assert "dropped" not in f and "suppressed" not in f
    finally:
        s.close()
        cleanup(game, a, b)


def test_quest_start_stage_stop(game):
    quest = game.form(QUEST)
    if game.call_method(quest, "Quest", "IsRunning") or game.call_method(quest, "Quest", "IsCompleted"):
        pytest.fail("dunKarthspireRedoubtQST is running or completed on this save: STOP and ask the owner")
    got: queue.Queue = queue.Queue()
    subs = [game.on(name, {"quest": [quest]}, got.put) for name in ("quest_start", "quest_stage", "quest_stop")]
    try:
        assert game.call_method(quest, "Quest", "Start") is True            # Start first: SetStage would also start it
        assert game.call_method(quest, "Quest", "SetStage", 10) is True
        game.call_method(quest, "Quest", "Stop")
        seen = [got.get(timeout=10.0) for _ in range(3)]
        assert [e.name for e in seen] == ["quest_start", "quest_stage", "quest_stop"], seen
        assert all(e.data["quest"].form_id == quest.form_id for e in seen)
        assert seen[1].data["stage"] == 10
    finally:
        for sub in subs:
            sub.close()


def test_mod_event_fields_and_case_insensitive_name_filter(game, session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "mod_event", {"name": ["SKYTHON_LIVE"]}, req_id=1)
        game.call_method(PLAYER, "Form", "SendModEvent", "skython_live", "s", 4.5)
        f = read_event(s, 10.0)
        assert f and f["sub"] == sub, f
        d = f["data"]
        assert (d["name"], d["str_arg"], d["num_arg"]) == ("skython_live", "s", 4.5)
        assert d["sender"]["form_id"] == 0x14
        game.call_method(PLAYER, "Form", "SendModEvent", "someone_else", "", 0.0)  # filtered out
        assert read_event(s, 3.0) is None
    finally:
        s.close()


def test_unsubscribe_stops_delivery(game, session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "mod_event", {"name": ["skython_live"]}, req_id=1)
        r = rpc(s, "events.unsubscribe", {"sub": sub}, req_id=2)
        assert r["ok"] is True and r["result"] is None
        game.call_method(PLAYER, "Form", "SendModEvent", "skython_live", "after", 1.0)
        assert read_event(s, 3.0) is None
        assert rpc(s, "events.unsubscribe", {"sub": sub}, req_id=3)["error"]["code"] == "not_found"
    finally:
        s.close()


def test_channel_subscription_is_accepted(session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "channel", {"channel": ["skython_live"]}, req_id=1)
        assert rpc(s, "events.unsubscribe", {"sub": sub}, req_id=2)["ok"] is True
    finally:
        s.close()


def test_hub_totals_in_diagnostics(session):
    s = raw_connect(session)
    try:
        d = rpc(s, "game.state", req_id=1)["result"]["diagnostics"]
        for key in ("event_consumers", "event_subscriptions", "events_dropped_total", "events_suppressed_total"):
            assert isinstance(d[key], int) and d[key] >= 0, (key, d)
        assert d["event_consumers"] >= 1  # this connection's consumer
    finally:
        s.close()


def test_in_process_mod_event():
    r = command("mod_event", event="skython_smoke")
    assert len(r["received"]) == 1, r
    got = r["received"][0]
    assert (got["name"], got["str_arg"], got["num_arg"], got["sender"], got["seq"]) == ("skython_smoke", "in-process", 2.5, 0x14, 1)


def test_no_sink_work_without_subscribers(session):
    # Wants() is false with no subscribers: nothing is queued and the totals do not move.
    s = raw_connect(session)
    try:
        before = rpc(s, "game.state", req_id=1)["result"]["diagnostics"]
        time.sleep(1.0)
        after = rpc(s, "game.state", req_id=2)["result"]["diagnostics"]
        assert after["events_dropped_total"] == before["events_dropped_total"]
    finally:
        s.close()
```
Before the box run, check the file at least imports: `uv run --project python python -c "import ast,sys; ast.parse(open('livetest/test_m2_events_batch1.py').read())"` (prints nothing), then `SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_events_batch1.py -q` → `8 skipped`.

- [ ] **Step 8: Build on the box**

```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
```
Expected: both builds succeed with **0 warnings** (as v1's final build); `out/Skython.dll` and `out/SkythonCore.dll` are fresh. A compile error on an engine name: re-grep it (Step 1); a difference beyond a rename is a STOP.

- [ ] **Step 9: Run in the game**

```bash
source tools/box/box_env.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_wait_log.sh Skython.log "events: sinks registered \(batch 1\)" 60
tools/box/box_wait_log.sh Skython.log "events: class table built" 120
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load s4test"      # never saved in this task; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch1.py -q -s -rs
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest --deselect livetest/test_m1_mainmenu.py --deselect livetest/test_m1_throughput.py -q -rs
tools/box/box_files.sh pull Skython.log >/dev/null
grep "events: " "$SKSE_LOG_DIR/Skython.log"
grep '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
```
Expected: `test_m2_events_batch1.py` 8 passed; the M1 suite still 77 passed with only its known skips (no regression from the hub); the `grep "events: "` shows `class table built (... form types; Actor = Actor)`, `sinks registered (batch 1)` and one `first <event> event on thread ...` line per event name raised; no `sink threw` line; `no unexpected errors`. A skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure.

- [ ] **Step 10: Record**

Append to `spikes/RESULTS.md` under "Plugin findings": `**Task 39, event hub and sinks batch 1 (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test, never saved; <n> launches; SKSE plugins: <list from skse64.log>):**` with the pass counts, the class-table line verbatim, every `first <event> event on thread <id> (inside our SKSE task: ...)` line verbatim and the S5-style note: "sinks ran on these threads; nothing in the plugin assumes one (R49)", the M1 suite count, and any surprise (e.g. `dying` not raised by `Kill(None)` on a chicken). Complete `API-VERIFICATION.md` with any spelling your grep showed.

- [ ] **Step 11: Commit**

```bash
git add docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md plugin/src/Events.h plugin/src/Events.cpp \
  plugin/src/EventSinks.cpp plugin/src/EventSinksWorld.cpp plugin/src/PapyrusValues.h plugin/src/PapyrusValues.cpp \
  plugin/src/main.cpp plugin/src/GameBackend.cpp plugin/src/PythonHost.cpp plugin/CMakeLists.txt \
  livetest/support.py livetest/scripts/livetest_smoke/__init__.py livetest/test_m2_events_batch1.py spikes/RESULTS.md
git commit -m "feat(plugin): event hub and first sinks (death, dying, quest stage/start/stop, mod events)"
```
