# Task 44: Sink detach probe (register/unregister safety), then detach if proven

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Gates** ("Sink detach (Task 44)": implemented only if the probe passes; not a STOP), **Contracts** (Plugin: `Events.h`; `EventHub`) and Part E's brief for Task 44 first. R38 item 1 (`.superpowers/sdd/README/skyrimplatform-comparison.md`, section 4: SkyrimPlatform registers a sink on the first subscription and removes it on the last; adopt it only if registering mid-game is shown safe; never drop events silently). Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** the Windows build box and the game on the box ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)); Part 2's core change also on Linux.
>
> **STOP and ask the owner (do not improvise) if:** the probe crashes the game a second time after one clean relaunch (one crash is a FAIL result to record; two means something else is wrong); the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s. A probe FAIL is **not** a STOP: record it, skip Part 2, run Part 3 in skip-only mode.
>
> **Box rules:** `s4test`, never saved; the probe picks up swords it places, and they are removed at the end. End games with `tools/box/box_stop.sh`.

**Files:**
- Part 1 (always): Create `plugin/src/SinkProbe.h`, `plugin/src/SinkProbe.cpp`; Modify `pyglue/include/spy/pymodule.h` (`ModuleHost::probe_sink_cycles`), `pyglue/src/pymodule.cpp` (`_skython._probe_sink_cycles`, only when set), `plugin/src/PythonHost.cpp`, `plugin/CMakeLists.txt`, `livetest/scripts/livetest_smoke/__init__.py` (`probe_sink_cycles` command); Create (if absent) `plugin/config/variants/trace-timing.ini`
- Part 2 (only on PASS): Modify `core/include/spy/events.h`, `core/src/events.cpp`, `core/tests/test_events.cpp` (`EventHubOptions::on_wanted`); `plugin/src/Events.h`, `plugin/src/Events.cpp`, `plugin/src/EventSinks.cpp`, `plugin/src/EventSinksWorld.cpp` (sinks attach on demand)
- Part 3 (always): Create `livetest/test_m2_sink_detach.py`; Modify `spikes/RESULTS.md`, `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`

**Interfaces:**
- Consumes: Task 39/40 sinks (`EventSinks.cpp`: `RegisterBatch1`; `EventSinksWorld.cpp`: `RegisterWorldSinks`; their static sink objects), `events::Hub()`, `EventHub::Wants`; Task 34 `EventHubOptions{epoch, loading}`, the hub's per-event atomic subscriber count; Task 33 `Config::trace_timing`; Task 35 `ModuleHost` (named fields); Task 40 `livetest/support.py` `PLAYER`, the `activate` event; v1 `ops::InTask`, `ops::Measured`, the smoke command loop and `smoke.command`, `support.log_text`.
- Produces: `_skython._probe_sink_cycles(n: int) -> str` (game build only, defined only when `trace_timing` is on; JSON `{cycles, registered, delivered, errors}`; `n > 0`: n register/unregister cycles, each step inside its own SKSE task, then the spare sink is left registered with its counter reset; `n == 0`: reports `delivered` since then and unregisters it); the smoke command `probe_sink_cycles`; on PASS: `EventHubOptions::on_wanted` (`std::function<void(std::string_view event, bool wanted)>`, called outside the hub's lock on a per-event subscriber count change 0→1 or 1→0), `spy::events::OnWanted(std::string_view, bool)`, log lines `events: sinks attach on demand`, `events: sink attached <sink>`, `events: sink detached <sink>`; the RESULTS verdict.

Rules:
- A sink is only ever registered or unregistered **inside an SKSE task** (`ops::InTask`, `ops::Measured`), never on a Python thread, a VM thread or inside a sink. A task never re-queues itself; the probe's Python thread queues one task per step and waits for it (bounded, 5 s).
- On PASS, a sink is attached while any event it serves has a subscriber, detached otherwise. A count change queues one task that **reconciles** (reads `Wants` for the sink's events at that moment, then adds or removes), so callbacks that arrive out of order still end in the right state. **Events are never dropped silently**: an event raised before the attach task ran (at most one frame after `events.subscribe` answered) is not seen, which is documented; an event's form reference may name something gone by the time it is used, and its next use raises `NotFound` (or `StaleReference`), as everywhere else.

## Part 1: the probe

- [ ] **Step 1: `SinkProbe.h` / `SinkProbe.cpp`**

`plugin/src/SinkProbe.h`:
```cpp
#pragma once
#include <string>

namespace spy::probe {
// Task 44 (dev only, trace_timing): n cycles of registering and unregistering a spare TESActivateEvent sink, each step
// inside its own SKSE task. Called on a Python thread with the GIL released; blocks that thread only.
std::string SinkCycles(int n);
}
```
`plugin/src/SinkProbe.cpp`:
```cpp
#include "SinkProbe.h"
#include "Log.h"
#include "Ops.h"
#include "spy/errors.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>

namespace spy::probe {
namespace {
using namespace std::chrono_literals;

struct SpareSink final : RE::BSTEventSink<RE::TESActivateEvent>
{
    std::atomic<std::uint64_t> delivered{ 0 };
    RE::BSEventNotifyControl ProcessEvent(const RE::TESActivateEvent*, RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        delivered.fetch_add(1);  // nothing else: no engine call, no allocation
        return RE::BSEventNotifyControl::kContinue;
    }
};
SpareSink* Spare() { static auto* s = new SpareSink(); return s; }  // immortal: the source may hold it after we return

struct Wait
{
    std::mutex mx;
    std::condition_variable cv;
    bool done = false, failed = false;
};

// Queue fn as one SKSE task and wait for it. fn captures only immortal pointers, so a task that runs after a timeout
// is still safe.
bool RunInTask(std::function<void()> fn)
{
    auto w = std::make_shared<Wait>();
    SKSE::GetTaskInterface()->AddTask([w, fn = std::move(fn)] {
        ops::InTask in;
        ops::Measured measured;
        bool failed = false;
        try { fn(); } catch (...) { failed = true; }
        { std::lock_guard lk(w->mx); w->done = true; w->failed = failed; }
        w->cv.notify_all();
    });
    std::unique_lock lk(w->mx);
    return w->cv.wait_for(lk, 5s, [&] { return w->done; }) && !w->failed;
}
}

std::string SinkCycles(int n)
{
    json out{ { "cycles", 0 }, { "registered", false }, { "delivered", 0 }, { "errors", 0 } };
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    SpareSink* s = Spare();
    if (!holder) {
        out["errors"] = 1;
        return dump_json(out);
    }
    if (n <= 0) {  // finish: what arrived since the last run left the spare sink registered, then remove it
        const bool ok = RunInTask([holder, s] { holder->RemoveEventSink<RE::TESActivateEvent>(s); });
        out["delivered"] = s->delivered.load();
        out["errors"] = ok ? 0 : 1;
        log::Info("probe: spare sink removed, " + std::to_string(s->delivered.load()) + " events since the last register");
        return dump_json(out);
    }
    int cycles = 0, errors = 0;
    for (int i = 0; i < n && errors == 0; ++i) {
        if (!RunInTask([holder, s] { holder->AddEventSink<RE::TESActivateEvent>(s); })) { ++errors; break; }
        if (!RunInTask([holder, s] { holder->RemoveEventSink<RE::TESActivateEvent>(s); })) { ++errors; break; }
        ++cycles;
    }
    const auto during = s->delivered.load();
    const bool registered = errors == 0 && RunInTask([holder, s] {
        holder->AddEventSink<RE::TESActivateEvent>(s);
        s->delivered.store(0);  // from here on every activation must arrive exactly once
    });
    out["cycles"] = cycles;
    out["registered"] = registered;
    out["delivered"] = during;
    out["errors"] = errors + (registered || errors ? 0 : 1);
    log::Info("probe: " + dump_json(out));
    return dump_json(out);
}
}
```
Verify `RemoveEventSink` at the pin (`grep -n "inline void RemoveEventSink" plugin/extern/CommonLibSSE-NG/include/RE/S/ScriptEventSourceHolder.h`; `grep -n "pendingUnregisters" plugin/extern/CommonLibSSE-NG/include/RE/B/BSTEvent.h`) and append the row to `API-VERIFICATION.md` (task 44): `RemoveEventSink` takes the same spin lock and defers to `pendingUnregisters` while the source is notifying.

- [ ] **Step 2: Expose it to Python (dev only) and add the smoke command**

`pyglue/include/spy/pymodule.h`, last member of `ModuleHost`:
```cpp
    std::function<std::string(int n)> probe_sink_cycles;  // game build with trace_timing only (Task 44); unset = absent
```
`pyglue/src/pymodule.cpp`, at the end of `DefineModule`:
```cpp
    if (h.probe_sink_cycles) {
        m.def("_probe_sink_cycles", [](int n) { return Host().probe_sink_cycles(n); }, py::arg("n"), release(),
              "Dev only (trace_timing): register/unregister cycles of a spare activate sink; JSON summary.");
    }
```
`plugin/src/PythonHost.cpp`: add `#include "SinkProbe.h"`; after the other `host.` assignments:
```cpp
    if (cfg.trace_timing) host.probe_sink_cycles = [](int n) { return probe::SinkCycles(n); };
```
`plugin/CMakeLists.txt`: add `src/SinkProbe.cpp`. The Linux fake build sets nothing, so `_probe_sink_cycles` is absent there (check: `SKYTHON_REAL_MODULE_DIR=pyglue/build uv run --project python python -c "import sys; sys.path.insert(0,'pyglue/build'); import _skython; print(hasattr(_skython, '_probe_sink_cycles'))"` → `False` after `cmake --build pyglue/build -j`).

In `livetest/scripts/livetest_smoke/__init__.py`, in `_handle` before the final `return`:
```python
    if name == "probe_sink_cycles":
        probe = getattr(_skython, "_probe_sink_cycles", None)
        if probe is None:
            return {"error": "_skython._probe_sink_cycles is absent: deploy plugin/config/variants/trace-timing.ini"}
        return json.loads(probe(int(cmd["n"])))
```
The variant ini: `test -f plugin/config/variants/trace-timing.ini || sed 's/^trace_timing = false$/trace_timing = true/' plugin/config/Skython.dev.ini > plugin/config/variants/trace-timing.ini`, then `grep -c '^trace_timing = true$' plugin/config/variants/trace-timing.ini` → `1`.

- [ ] **Step 3: The live test file (both modes)**

`livetest/test_m2_sink_detach.py`:
```python
"""Task 44: the sink register/unregister probe, then either attach-on-demand (probe PASS) or skip-only (FAIL).
Mode is read from the plugin log: 'events: sinks attach on demand' means Part 2 is in. s4test, never saved."""
import queue
import threading
import time

import pytest

from skython import errors
from smoke import command
from support import PLAYER, log_text

IRON_SWORD = "Skyrim.esm|0x012EB7"


def pick_up(game, n: int, stop: threading.Event | None = None) -> int:
    """Place a sword and pick it up, n times (or until stop): one TESActivateEvent each. Returns how many."""
    sword, done = game.form(IRON_SWORD), 0
    while done < n and not (stop and stop.is_set()):
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", sword, 1, False, False)
        game.call_method(ref, "ObjectReference", "Activate", PLAYER, False)
        done += 1
    return done


def remove_swords(game) -> None:
    sword = game.form(IRON_SWORD)
    n = game.call_method(PLAYER, "ObjectReference", "GetItemCount", sword)
    if n:
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", sword, n, True, None)


def detach_mode() -> bool:
    return "events: sinks attach on demand" in log_text()


def test_probe_register_unregister_cycles(game):
    hub_seen: queue.Queue = queue.Queue()
    stop = threading.Event()
    raised = {"during": 0}
    with game.on("activate", {"activator": [PLAYER]}, hub_seen.put, max_queue=65536):
        t = threading.Thread(target=lambda: raised.__setitem__("during", pick_up(game, 10**6, stop)), daemon=True)
        t.start()
        try:
            r = command("probe_sink_cycles", n=1000, timeout=600.0)  # about 2000 frames
        finally:
            stop.set()
            t.join(30)
        print(f"probe: {r}; activations raised during the cycles: {raised['during']}")
        assert r["cycles"] == 1000 and r["errors"] == 0 and r["registered"] is True, r
        drained = 0
        while not hub_seen.empty():
            hub_seen.get_nowait()
            drained += 1
        k = pick_up(game, 20)                                # after the last register
        got = 0
        deadline = time.monotonic() + 15
        while got < k and time.monotonic() < deadline:
            try:
                hub_seen.get(timeout=1.0)
                got += 1
            except queue.Empty:
                pass
        final = command("probe_sink_cycles", n=0)
    remove_swords(game)
    print(f"after: raised {k}, spare sink {final['delivered']}, hub {got}")
    assert final["delivered"] == k, "every activation after the last register must reach the spare sink exactly once"
    assert got == k, "the product's own activate sink must keep delivering"


def test_mode_specific_behaviour(game):
    q: queue.Queue = queue.Queue()
    if not detach_mode():
        # Skip-only (probe FAIL or not adopted): sinks stay registered; nothing is queued without subscribers.
        assert "events: sink detached" not in log_text()
        before = game.state()["diagnostics"]["events_dropped_total"]
        pick_up(game, 3)
        assert game.state()["diagnostics"]["events_dropped_total"] == before
        remove_swords(game)
        return
    # Attach on demand: subscribe -> attached, unsubscribe -> detached, subscribe again -> attached and delivering.
    for round_ in range(2):
        sub = game.on("activate", {"activator": [PLAYER]}, q.put)
        try:
            assert pick_up(game, 1) == 1
            ev = q.get(timeout=10.0)                        # our own call is queued after the attach task
            target = ev.data["target"]
        finally:
            sub.close()
        time.sleep(1.0)
        text = log_text()
        assert text.count("events: sink attached activate") >= round_ + 1
        assert text.count("events: sink detached activate") >= round_ + 1
    # The reference an event named is gone (picked up): its next use raises, never crashes, never vanishes silently.
    with pytest.raises((errors.NotFound, errors.StaleReference)):
        game.call_method(target, "ObjectReference", "GetBaseObject")
    remove_swords(game)
```
`SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_sink_detach.py -q` → `2 skipped`.

- [ ] **Step 4: Build and run the probe**

```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll   # 0 warnings
source tools/box/box_env.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/trace-timing.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_sink_detach.py -q -s -rs
tools/box/box_files.sh pull Skython.log >/dev/null
grep "probe: " "$SKSE_LOG_DIR/Skython.log"
tools/box/box_stop.sh     # NO_NEW_CRASH_LOG expected
```
**Verdict.** PASS = `test_probe_register_unregister_cycles` passed (1000 cycles, 0 errors, no crash, no hang, the spare sink got exactly the 20 later activations, the product's sink kept delivering) and `box_stop.sh` reports `NO_NEW_CRASH_LOG`. Anything else = FAIL: a crash (relaunch once and rerun to tell a fluke from a pattern; a second crash is the STOP above), a step that timed out (`errors` 1, a hang), or a count other than 20. `test_mode_specific_behaviour` runs in skip-only mode here and passes.

Record the verdict now (Step 9's entry, first half). **On FAIL, skip to Step 9.**

## Part 2: attach on demand (only on PASS)

- [ ] **Step 5: `EventHubOptions::on_wanted` in core (failing test first)**

Append to `core/tests/test_events.cpp`:
```cpp
TEST_CASE("event hub: on_wanted reports 0->1 and 1->0 per event, outside the lock")
{
    std::vector<std::pair<std::string, bool>> calls;
    spy::EventHub* self = nullptr;
    spy::EventHubOptions opts{ [] { return 1u; }, [] { return false; },
                               [&](std::string_view e, bool w) {
                                   (void)self->Wants(e);  // re-entering the hub here must not deadlock
                                   calls.emplace_back(std::string(e), w);
                               } };
    spy::EventHub hub(opts);
    self = &hub;
    const auto c = hub.OpenConsumer();
    const auto s1 = hub.Subscribe(c, { { "event", "activate" } })["sub"].get<spy::SubId>();
    const auto s2 = hub.Subscribe(c, { { "event", "activate" } })["sub"].get<spy::SubId>();
    CHECK(calls == std::vector<std::pair<std::string, bool>>{ { "activate", true } });
    hub.Unsubscribe(c, s1);
    CHECK(calls.size() == 1);
    hub.Unsubscribe(c, s2);
    CHECK(calls.back() == std::pair<std::string, bool>{ "activate", false });
    hub.Subscribe(c, { { "event", "death" } });
    hub.CloseConsumer(c);  // drops the death subscription: 1 -> 0
    CHECK(calls.back() == std::pair<std::string, bool>{ "death", false });
}
```
Run `cmake --build core/build -j && ./core/build/spy_core_tests -tc="event hub: on_wanted*"` → FAIL (no third member in `EventHubOptions`).

Then in `core/include/spy/events.h` add, as the **last** member of `EventHubOptions`:
```cpp
    std::function<void(std::string_view event, bool wanted)> on_wanted;  // Task 44: subscriber count 0->1 (true) / 1->0 (false); optional
```
In `core/src/events.cpp`, wherever Task 34 changes an event's subscriber count (in `Subscribe`, `Unsubscribe` and `CloseConsumer`, under the hub's lock), record the transitions in a local `std::vector<std::pair<std::string, bool>> changes` (push `{event, true}` when the count was 0 before the increment, `{event, false}` when it is 0 after the decrement), and after the lock is released:
```cpp
    if (opts_.on_wanted)
        for (const auto& [event, wanted] : changes) {
            try { opts_.on_wanted(event, wanted); } catch (...) {}  // a callback failure never breaks the hub
        }
```
(Use the member name Task 34 gave the options; the rule is "called outside the lock, never throws out of the hub".) Run the new test and the whole core suite → all pass; update the `events.h` comment block to state the callback's rule.

- [ ] **Step 6: Sinks attach on demand in the plugin**

`plugin/src/Events.h`, add:
```cpp
#include <functional>
#include <vector>
// Task 44: each engine sink, the hub events it serves, and how to add/remove it (inside an SKSE task only).
struct SinkEntry
{
    const char* sink;
    std::vector<const char*> events;
    std::function<void()> add, remove;
    bool attached = false;  // touched only inside our SKSE tasks (serialized, observed: R49)
};
std::vector<SinkEntry>& Sinks();
void OnWanted(std::string_view event, bool wanted);
```
`plugin/src/Events.cpp`: the hub gets the callback as its third option:
```cpp
    static spy::EventHub* hub = new spy::EventHub(spy::EventHubOptions{
        [] { return life::Epoch(); }, [] { return life::Loading(); },
        [](std::string_view e, bool w) { OnWanted(e, w); } });
```
and add:
```cpp
std::vector<SinkEntry>& Sinks() { static auto* v = new std::vector<SinkEntry>; return *v; }  // filled at kDataLoaded, then fixed

namespace {
void Reconcile(std::size_t i)
{
    auto& e = Sinks()[i];
    const bool want = std::ranges::any_of(e.events, [](const char* n) { return Hub().Wants(n); });
    if (want == e.attached) return;
    (want ? e.add : e.remove)();
    e.attached = want;
    log::Info(std::string("events: sink ") + (want ? "attached " : "detached ") + e.sink);
}
}

void OnWanted(std::string_view event, bool)
{
    // Any order of callbacks ends right: the task reads Wants() when it runs.
    for (std::size_t i = 0; i < Sinks().size(); ++i) {
        const auto& names = Sinks()[i].events;
        if (std::ranges::none_of(names, [&](const char* n) { return event == n; })) continue;
        SKSE::GetTaskInterface()->AddTask([i] {
            ops::InTask in;
            ops::Measured measured;
            try { Reconcile(i); } catch (...) { NoteSinkError("reconcile", "unknown"); }
        });
    }
}
```
(add `#include <algorithm>`). In `RegisterSinks`, after `RegisterWorldSinks();` add `log::Info("events: sinks attach on demand");`.

`plugin/src/EventSinks.cpp`, `RegisterBatch1`: replace the four `AddEventSink` lines with entries (keep the null checks and the `events: sinks registered (batch 1)` line, whose meaning becomes "known and ready to attach"):
```cpp
    Sinks().push_back({ "death", { "death", "dying" }, [holder] { holder->AddEventSink<RE::TESDeathEvent>(&death); },
                        [holder] { holder->RemoveEventSink<RE::TESDeathEvent>(&death); } });
    Sinks().push_back({ "quest_stage", { "quest_stage" }, [holder] { holder->AddEventSink<RE::TESQuestStageEvent>(&stage); },
                        [holder] { holder->RemoveEventSink<RE::TESQuestStageEvent>(&stage); } });
    Sinks().push_back({ "quest_start_stop", { "quest_start", "quest_stop" },
                        [holder] { holder->AddEventSink<RE::TESQuestStartStopEvent>(&startStop); },
                        [holder] { holder->RemoveEventSink<RE::TESQuestStartStopEvent>(&startStop); } });
    Sinks().push_back({ "mod_event", { "mod_event" }, [modSource] { modSource->AddEventSink(&mod); },
                        [modSource] { modSource->RemoveEventSink(&mod); } });
```
`plugin/src/EventSinksWorld.cpp`, `RegisterWorldSinks`: the same for the seven sinks, names `hit` {hit}, `equip` {equip, unequip}, `container_changed` {container_changed}, `cell` {cell_attach, cell_detach}, `activate` {activate}, `player_location` {player_location}, `menu` {menu_open, menu_close} (the last one's lambdas call `ui->AddEventSink<RE::MenuOpenCloseEvent>(&menu)` / `ui->RemoveEventSink<RE::MenuOpenCloseEvent>(&menu)`; verify `UI::RemoveEventSink` at the pin with `grep -n "RemoveEventSink" plugin/extern/CommonLibSSE-NG/include/RE/U/UI.h` and add the row to `API-VERIFICATION.md`). `Sinks()` is filled once at `kDataLoaded` before any subscription can exist (Python starts its bridge and scripts after), and never resized later, so the index a task captures stays valid. A subscription made in a player script's `setup` (main menu) attaches on the next frame like any other.

- [ ] **Step 7: Build, unit tests, live run in attach-on-demand mode**

```bash
cmake --build core/build -j && ./core/build/spy_core_tests            # all pass
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll   # 0 warnings
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/trace-timing.ini
tools/box/box_deploy.sh --package && tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "events: sinks attach on demand" 600
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_sink_detach.py livetest/test_m2_events_batch1.py -q -s -rs
tools/box/box_stop.sh
```
Expected: all pass (detach test in attach-on-demand mode; batch 1's 8 tests show the first-event window does not bite our own call order). Then one more launch on `s4test` for batch 2 (it moves the player indoors at the end):
```bash
tools/box/box_launch.sh && tools/box/box_wait_log.sh Skython.log "events: sinks attach on demand" 600
tools/box/box_session.sh up && source out/box-live.env && tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch2.py -q -s -rs
tools/box/box_stop.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini
```
Expected: 9 passed. `game_loaded` is raised by `Lifecycle.cpp` without a sink, so Task 42's lifecycle test is unaffected.

- [ ] **Step 8: Commit Part 2** (only on PASS)

```bash
git add core/include/spy/events.h core/src/events.cpp core/tests/test_events.cpp plugin/src/Events.h plugin/src/Events.cpp \
  plugin/src/EventSinks.cpp plugin/src/EventSinksWorld.cpp
git commit -m "feat(plugin): attach event sinks only while something is subscribed"
```

## Part 3: record

- [ ] **Step 9: RESULTS and commit**

Append to `spikes/RESULTS.md`: `**Task 44, sink register/unregister probe (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test, never saved; <n> launches; SKSE plugins: <list>):** PASS|FAIL` with the probe JSON and the `probe:` log lines verbatim, the activations raised during the cycles, the after-register count (spare sink vs hub, must both be 20), the exit result, and the decision: "sinks attach on demand (Part 2 in), first-event window at most one frame after `events.subscribe` answers" or "probe FAIL: sinks stay registered and skip when nothing is subscribed (skip-only, the Task 39 behaviour); not adopted". Then:
```bash
git add plugin/src/SinkProbe.h plugin/src/SinkProbe.cpp pyglue/include/spy/pymodule.h pyglue/src/pymodule.cpp \
  plugin/src/PythonHost.cpp plugin/CMakeLists.txt plugin/config/variants/trace-timing.ini \
  livetest/scripts/livetest_smoke/__init__.py livetest/test_m2_sink_detach.py spikes/RESULTS.md \
  docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md
git commit -m "test(live): sink register/unregister probe and its verdict"
```
(On PASS, Part 2's commit comes first; Part 1's files can go in this commit or before it.)
