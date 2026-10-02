# Task 44: Sink detach probe (register/unregister safety), then detach if proven

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Gates** ("Sink detach (Task 44)": implemented only if the probe passes; not a STOP), **Contracts** (Plugin: `Events.h`; `EventHub`) and Part E's brief for Task 44 first. R38 item 1 (`.superpowers/sdd/README/skyrimplatform-comparison.md`, section 4: SkyrimPlatform registers a sink on the first subscription and removes it on the last; adopt it only if registering mid-game is shown safe; never drop events silently). Steps use checkbox (`- [ ]`) syntax.
>
> **Depends on:** Task 40 (both sink batches), Task 38 (`plugin/config/variants/trace-timing.ini`, the dev ini with `trace_timing = true`: `_skython._probe_sink_cycles` exists only with it), Task 35 (`ModuleHost`, built with named fields), Task 34 (`EventHubOptions`).
>
> **Runs on:** the Windows build box and the game on the box ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)); Part 2's core change also on Linux.
>
> **STOP and ask the owner (do not improvise) if:** the probe crashes the game a second time after one clean relaunch (one crash is a FAIL result to record; two means something else is wrong); the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s. A probe FAIL is **not** a STOP: record it, skip Part 2, run Part 3 in skip-only mode.
>
> **Box rules:** `s4test`, never saved; the probe picks up swords it places, and they are removed at the end. End games with `tools/box/box_stop.sh`.

**Files:**
- Part 1 (always): Create `plugin/src/SinkProbe.h`, `plugin/src/SinkProbe.cpp`; Modify `pyglue/include/spy/pymodule.h` (`ModuleHost::probe_sink_cycles`), `pyglue/src/pymodule.cpp` (`_skython._probe_sink_cycles`, only when set), `plugin/src/PythonHost.cpp`, `plugin/CMakeLists.txt`, `livetest/scripts/livetest_smoke/__init__.py` (`probe_sink_cycles` command). Uses (does not create) `plugin/config/variants/trace-timing.ini` from Task 38
- Part 2 (only on PASS): Modify `core/include/spy/events.h`, `core/src/events.cpp`, `core/tests/test_events.cpp` (`EventHubOptions::on_wanted`); `plugin/src/Events.h`, `plugin/src/Events.cpp`, `plugin/src/EventSinks.cpp`, `plugin/src/EventSinksWorld.cpp` (sinks attach on demand)
- Part 3 (always): Create `livetest/test_m2_sink_detach.py`; Modify `spikes/RESULTS.md`, `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`

**Interfaces:**
- Consumes: Task 39/40 sinks (`EventSinks.cpp`: `RegisterBatch1`; `EventSinksWorld.cpp`: `RegisterWorldSinks`; their static sink objects), `events::Hub()`, `EventHub::Wants`; Task 34 `EventHubOptions{epoch, loading, max_queued_bytes_per_consumer}` (Task 39 sets `loading` to `life::SaveLoadInProgress`), the hub's per-event atomic subscriber count; Task 33 `Config::trace_timing`; Task 38 `plugin/config/variants/trace-timing.ini`; Task 35 `ModuleHost` (named fields); Task 40 `livetest/support.py` `PLAYER`, `box_menu(action, name)` (DevBench menu tool), the `activate` event and the batch-2 `menu` sink; v1 `ops::InTask`, `ops::Measured`, the smoke command loop and `smoke.command`, `support.log_text`.
- Produces: `_skython._probe_sink_cycles(n: int) -> str` (game build only, defined only when `trace_timing` is on; returns a JSON summary `{mode, cycles, registered, delivered, delivered_any, menu_delivered, events_outside_tasks, overlaps, errors}`). Three modes: **`n > 0`** runs n cycles; each cycle is one SKSE task that registers both spare sinks (a `TESActivateEvent` sink on `ScriptEventSourceHolder` and a `MenuOpenCloseEvent` sink on `RE::UI`) and a second SKSE task that unregisters both; the probe **ends with both unregistered**; the counters are zeroed when the run starts. **`n == -1` (arm):** one SKSE task registers the spare activate sink and zeroes its counters; from then on every player activation must reach it exactly once. **`n == 0` (finish):** reports the counters since the arm and unregisters the spare activate sink (one SKSE task). Any other `n` → `errors: 1`. Counters: `delivered` counts only activations whose `actionRef` is the player (pointer compare with `RE::PlayerCharacter::GetSingleton()`), `delivered_any` every `TESActivateEvent` (NPC doors too; for the record), `menu_delivered` the menu sink's events, `events_outside_tasks` spare-sink events delivered on a thread that is not inside one of our SKSE tasks (`ops::t_inTask == false`), `overlaps` how often an add or remove call saw a spare sink's `ProcessEvent` running at that moment (`in_process > 0`). The smoke command `probe_sink_cycles`; on PASS: `EventHubOptions::on_wanted` (`std::function<void(std::string_view event, bool wanted)>`, called outside the hub's lock on a per-event subscriber count change 0→1 or 1→0), `spy::events::OnWanted(std::string_view, bool)`, log lines `events: sinks attach on demand`, `events: sink attached <sink>`, `events: sink detached <sink>`; the RESULTS verdict.

Rules:
- A sink is only ever registered or unregistered **inside an SKSE task** (`ops::InTask`, `ops::Measured`), never on a Python thread, a VM thread or inside a sink. A task never re-queues itself; the probe's Python thread queues one task per step and waits for it (bounded, 5 s).
- **What the probe can and cannot prove.** Every add and remove runs inside one of our serialized SKSE tasks, so registering is only exercised against events that arrive *concurrently* if some events are delivered outside our tasks. The probe therefore counts `events_outside_tasks` and `overlaps`. A run whose spare sinks saw **no** event outside our tasks (`events_outside_tasks == 0`) proves nothing about concurrent registration: its verdict is **INCONCLUSIVE, not adopted** (Part 2 is skipped, as on FAIL). The probe cycles a second spare sink on `RE::UI`'s `MenuOpenCloseEvent` source while the test opens and closes a menu, because Part 2 also detaches the UI menu sink and the SKSE mod-event source; the mod-event source (`SKSE::GetModCallbackEventSource()`) is not cycled by the probe, which RESULTS states.
- On PASS, a sink is attached while any event it serves has a subscriber, detached otherwise. A count change queues one task that **reconciles** (reads `Wants` for the sink's events at that moment, then adds or removes), so callbacks that arrive out of order still end in the right state. **Events are never dropped silently**, with one documented exception: an event raised between the `events.subscribe` answer and the attach task (about one frame) is never seen by the engine sink, so it is neither queued nor counted in `dropped`/`suppressed`. This is a protocol fact, not a bug: Step 9 records it in RESULTS and Task 57 states it in PROTOCOL.md and README (only when Part 2 is in). `events.subscribe` never waits for the attach; an event's form reference may name something gone by the time it is used, and its next use raises `NotFound` (or `StaleReference`), as everywhere else.

## Part 1: the probe

- [ ] **Step 1: `SinkProbe.h` / `SinkProbe.cpp`**

`plugin/src/SinkProbe.h`:
```cpp
#pragma once
#include <string>

namespace spy::probe {
// Task 44 (dev only, trace_timing). n > 0: n cycles of registering and unregistering the spare TESActivateEvent and
// MenuOpenCloseEvent sinks, each step inside its own SKSE task; ends with both unregistered. n == -1: register the spare
// activate sink and zero its counters (arm). n == 0: report the counters since the arm and unregister it (finish).
// Called on a Python thread with the GIL released; blocks that thread only.
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

std::atomic<int> g_inProcess{ 0 };               // spare-sink ProcessEvent calls running right now (any thread)
std::atomic<std::uint64_t> g_overlaps{ 0 };      // add/remove calls that saw g_inProcess > 0
std::atomic<std::uint64_t> g_outside{ 0 };       // spare-sink events delivered outside our SKSE tasks

struct InProcess
{
    InProcess()
    {
        g_inProcess.fetch_add(1);
        if (!ops::t_inTask) g_outside.fetch_add(1);  // R52 flag, never a thread-id test (R49)
    }
    ~InProcess() { g_inProcess.fetch_sub(1); }
    InProcess(const InProcess&) = delete;
    InProcess& operator=(const InProcess&) = delete;
};

struct SpareSink final : RE::BSTEventSink<RE::TESActivateEvent>
{
    std::atomic<std::uint64_t> delivered{ 0 };      // activations by the player only (the hub's filter in the test)
    std::atomic<std::uint64_t> delivered_any{ 0 };  // every TESActivateEvent, NPC doors too (for the record)
    RE::BSEventNotifyControl ProcessEvent(const RE::TESActivateEvent* a_event,
                                          RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        InProcess in;  // nothing else: no engine call beyond the pointer compare, no allocation
        delivered_any.fetch_add(1);
        if (a_event && a_event->actionRef.get() == RE::PlayerCharacter::GetSingleton()) delivered.fetch_add(1);
        return RE::BSEventNotifyControl::kContinue;
    }
};
struct SpareMenuSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    std::atomic<std::uint64_t> delivered{ 0 };
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent*,
                                          RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        InProcess in;
        delivered.fetch_add(1);
        return RE::BSEventNotifyControl::kContinue;
    }
};
// Immortal: a source may still hold or call a sink after we return.
SpareSink* Spare() { static auto* s = new SpareSink(); return s; }
SpareMenuSink* SpareMenu() { static auto* s = new SpareMenuSink(); return s; }

void NoteOverlap() { if (g_inProcess.load() > 0) g_overlaps.fetch_add(1); }  // called right before each add/remove

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

json Summary(const char* mode, int cycles, bool registered, int errors)
{
    return json{ { "mode", mode }, { "cycles", cycles }, { "registered", registered },
                 { "delivered", Spare()->delivered.load() }, { "delivered_any", Spare()->delivered_any.load() },
                 { "menu_delivered", SpareMenu()->delivered.load() }, { "events_outside_tasks", g_outside.load() },
                 { "overlaps", g_overlaps.load() }, { "errors", errors } };
}

void ZeroCounters()
{
    Spare()->delivered.store(0);
    Spare()->delivered_any.store(0);
    SpareMenu()->delivered.store(0);
    g_outside.store(0);
    g_overlaps.store(0);
}
}

std::string SinkCycles(int n)
{
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* ui = RE::UI::GetSingleton();
    SpareSink* s = Spare();
    SpareMenuSink* m = SpareMenu();
    if (!holder || !ui || n < -1) {
        const auto out = Summary("refused", 0, false, 1);
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    if (n == -1) {  // arm: from here on every player activation must arrive exactly once
        const bool ok = RunInTask([holder, s] {
            NoteOverlap();
            holder->AddEventSink<RE::TESActivateEvent>(s);
            ZeroCounters();
        });
        const auto out = Summary("arm", 0, ok, ok ? 0 : 1);
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    if (n == 0) {  // finish: report what arrived since the arm, then remove the spare activate sink
        const auto out_before = Summary("finish", 0, false, 0);
        const bool ok = RunInTask([holder, s] {
            NoteOverlap();
            holder->RemoveEventSink<RE::TESActivateEvent>(s);
        });
        auto out = out_before;
        out["errors"] = ok ? 0 : 1;
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    ZeroCounters();
    int cycles = 0, errors = 0;
    for (int i = 0; i < n && errors == 0; ++i) {
        if (!RunInTask([holder, ui, s, m] {
                NoteOverlap();
                holder->AddEventSink<RE::TESActivateEvent>(s);
                NoteOverlap();
                ui->AddEventSink<RE::MenuOpenCloseEvent>(m);
            })) { ++errors; break; }
        if (!RunInTask([holder, ui, s, m] {
                NoteOverlap();
                holder->RemoveEventSink<RE::TESActivateEvent>(s);
                NoteOverlap();
                ui->RemoveEventSink<RE::MenuOpenCloseEvent>(m);
            })) { ++errors; break; }
        ++cycles;
    }
    // A failed step may have left a sink registered: one more task removes both (removing an absent sink is a no-op).
    if (errors) RunInTask([holder, ui, s, m] {
        holder->RemoveEventSink<RE::TESActivateEvent>(s);
        ui->RemoveEventSink<RE::MenuOpenCloseEvent>(m);
    });
    const auto out = Summary("cycles", cycles, false, errors);
    log::Info("probe: " + dump_json(out));
    return dump_json(out);
}
}
```
Verify at the pin and append the rows to `API-VERIFICATION.md` (task 44): `RemoveEventSink` (`grep -n "inline void RemoveEventSink" plugin/extern/CommonLibSSE-NG/include/RE/S/ScriptEventSourceHolder.h`; `grep -n "pendingUnregisters" plugin/extern/CommonLibSSE-NG/include/RE/B/BSTEvent.h`: it takes the same spin lock and defers to `pendingUnregisters` while the source is notifying); `UI::AddEventSink`/`RemoveEventSink` for `MenuOpenCloseEvent` (`grep -n "EventSink" plugin/extern/CommonLibSSE-NG/include/RE/U/UI.h`); `TESActivateEvent::actionRef` is a `NiPointer<TESObjectREFR>` (`grep -n "actionRef" plugin/extern/CommonLibSSE-NG/include/RE/T/TESActivateEvent.h`). Comparing `actionRef.get()` with `RE::PlayerCharacter::GetSingleton()` is a pointer compare (no engine call).

- [ ] **Step 2: Expose it to Python (dev only) and add the smoke command**

`pyglue/include/spy/pymodule.h`, last member of `ModuleHost` (with a default, so every existing initializer stays valid: `PythonHost.cpp` and `fake_module.cpp` set `ModuleHost` fields by name, never positionally; the fake build leaves this one unset):
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
The variant ini is Task 38's (never recreated here): `grep -c '^trace_timing = true$' plugin/config/variants/trace-timing.ini` → `1` (absent → STOP: Task 38 is not done).

- [ ] **Step 3: The live test file (both modes)**

`livetest/test_m2_sink_detach.py`:
```python
"""Task 44: the sink register/unregister probe, then either attach-on-demand (probe PASS) or skip-only (FAIL or
INCONCLUSIVE). Mode is read from the plugin log: 'events: sinks attach on demand' means Part 2 is in. s4test, never
saved."""
import queue
import re
import threading
import time

import pytest

from skython import errors
from smoke import command
from support import PLAYER, box_menu, log_text

IRON_SWORD = "Skyrim.esm|0x012EB7"
MENU = "InventoryMenu"


def pick_up(game, n: int, stop: threading.Event | None = None) -> int:
    """Place a sword and pick it up, n times (or until stop): one player TESActivateEvent each. Returns how many."""
    sword, done = game.form(IRON_SWORD), 0
    while done < n and not (stop and stop.is_set()):
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", sword, 1, False, False)
        game.call_method(ref, "ObjectReference", "Activate", PLAYER, False)
        done += 1
    return done


def toggle_menu(stop: threading.Event, counter: dict) -> None:
    """Open and close a menu through DevBench until stop (menu_open/menu_close for the spare UI sink). Short open
    time, so a task that waits for the menu to close still finishes well inside the probe's 5 s step bound."""
    while not stop.is_set():
        box_menu("open", MENU)
        time.sleep(0.3)
        box_menu("close", MENU)
        counter["toggles"] += 1
        time.sleep(1.0)


def remove_swords(game) -> None:
    sword = game.form(IRON_SWORD)
    n = game.call_method(PLAYER, "ObjectReference", "GetItemCount", sword)
    if n:
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", sword, n, True, None)


def detach_mode() -> bool:
    return "events: sinks attach on demand" in log_text()


def drain(q: queue.Queue) -> int:
    n = 0
    while not q.empty():
        q.get_nowait()
        n += 1
    return n


def test_probe_register_unregister_cycles(game):
    hub_seen: queue.Queue = queue.Queue()
    stop = threading.Event()
    raised = {"during": 0}
    toggles = {"toggles": 0}
    with game.on("activate", {"activator": [PLAYER]}, hub_seen.put, max_queue=65536):
        t = threading.Thread(target=lambda: raised.__setitem__("during", pick_up(game, 10**6, stop)), daemon=True)
        mt = threading.Thread(target=toggle_menu, args=(stop, toggles), daemon=True)
        t.start()
        mt.start()
        try:
            r = command("probe_sink_cycles", n=1000, timeout=600.0)  # about 2000 frames; ends unregistered
        finally:
            stop.set()
            t.join(30)
            mt.join(30)
            box_menu("close", MENU)  # never leave the menu open
        print(f"probe: {r}; activations raised during the cycles: {raised['during']}; menu toggles: "
              f"{toggles['toggles']}")
        assert r["cycles"] == 1000 and r["errors"] == 0 and r["registered"] is False, r
        # INCONCLUSIVE (not a test failure): the verdict in Step 4 reads events_outside_tasks from the log line.
        print(f"events outside our tasks: {r['events_outside_tasks']} (0 = INCONCLUSIVE), overlaps: {r['overlaps']}")
        time.sleep(2.0)                                       # late activations of the cycling phase land first
        drained = drain(hub_seen)
        armed = command("probe_sink_cycles", n=-1)            # one task: register the spare sink, zero its counters
        assert armed["registered"] is True and armed["errors"] == 0, armed
        k = pick_up(game, 20)                                 # every one of these must reach both sinks once
        got = 0
        deadline = time.monotonic() + 15
        while got < k and time.monotonic() < deadline:
            try:
                hub_seen.get(timeout=1.0)
                got += 1
            except queue.Empty:
                pass
        final = command("probe_sink_cycles", n=0)             # report, then unregister
    remove_swords(game)
    print(f"after: drained {drained}, raised {k}, spare sink {final['delivered']} "
          f"(any actor: {final['delivered_any']}), hub {got}")
    assert final["errors"] == 0, final
    assert final["delivered"] == k, "every player activation after the arm must reach the spare sink exactly once"
    assert got == k, "the product's own activate sink must keep delivering"


def test_mode_specific_behaviour(game):
    q: queue.Queue = queue.Queue()
    if not detach_mode():
        # Skip-only (probe FAIL, INCONCLUSIVE or not adopted): sinks stay registered; nothing queued without subscribers.
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


def test_rapid_subscribe_close_ends_attached(game):
    """200 rapid subscribe/close cycles queue out-of-order reconcile tasks; one final subscription must leave the
    activate sink attached and delivering (attach-on-demand mode only)."""
    if not detach_mode():
        pytest.skip("skip-only mode: sinks never detach")
    for _ in range(200):
        game.on("activate", {"activator": [PLAYER]}, lambda ev: None).close()
    q: queue.Queue = queue.Queue()
    with game.on("activate", {"activator": [PLAYER]}, q.put):
        time.sleep(2.0)                                      # every queued reconcile task has run
        lines = re.findall(r"events: sink (attached|detached) activate", log_text())
        assert lines and lines[-1] == "attached", lines[-5:]
        assert pick_up(game, 1) == 1
        q.get(timeout=10.0)
    remove_swords(game)
```
`SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_sink_detach.py -q` → `3 skipped`.

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
tools/box/box_console.sh --list | grep -q '"menu"' || echo "STOP: no DevBench menu tool"
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_sink_detach.py -q -s -rs
tools/box/box_files.sh pull Skython.log >/dev/null
grep "probe: " "$SKSE_LOG_DIR/Skython.log"
tools/box/box_stop.sh     # NO_NEW_CRASH_LOG expected
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini (both verdicts)
```
(If the menu-tool check printed `STOP`, stop there and ask the owner: the UI half of the probe cannot run without it. `test_rapid_subscribe_close_ends_attached` skips in this run: Part 2 is not in yet.)
**Verdict.** PASS = `test_probe_register_unregister_cycles` passed (1000 cycles, 0 errors, no crash, no hang, ended unregistered; after the arm the spare sink got exactly the 20 player activations, the product's sink kept delivering), `box_stop.sh` reports `NO_NEW_CRASH_LOG`, **and** the cycling run's `probe:` line has `events_outside_tasks > 0` (events really arrived outside our serialized tasks while sinks were added and removed). `events_outside_tasks == 0` with everything else passing = **INCONCLUSIVE, not adopted** (treated like FAIL for Part 2). Anything else = FAIL: a crash (relaunch once and rerun to tell a fluke from a pattern; a second crash is the STOP above), a step that timed out (`errors` 1, a hang) **while no menu was open**, or a count other than 20. A step that timed out while the test had `InventoryMenu` open (the menu pauses the game; SKSE tasks may wait) is **INCONCLUSIVE, not adopted**, not FAIL (ruling R62): the test opens the menu only between cycle batches, logs each open/close with a timestamp, and the verdict compares the timed-out step's time with those windows. Record `overlaps`, `menu_delivered` and `delivered_any` whatever the verdict. `test_mode_specific_behaviour` runs in skip-only mode here and passes.

Record the verdict now (Step 9's entry, first half). **On FAIL or INCONCLUSIVE, skip to Step 9.**

## Part 2: attach on demand (only on PASS)

- [ ] **Step 5: `EventHubOptions::on_wanted` in core (failing test first)**

Append to `core/tests/test_events.cpp`:
```cpp
TEST_CASE("event hub: on_wanted reports 0->1 and 1->0 per event, outside the lock")
{
    std::vector<std::pair<std::string, bool>> calls;
    spy::EventHub* self = nullptr;
    spy::EventHubOptions opts{ .epoch = [] { return 1u; }, .loading = [] { return false; },
                               .on_wanted = [&](std::string_view e, bool w) {
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
Run `cmake --build core/build -j && ./core/build/spy_core_tests -tc="event hub: on_wanted*"` → FAIL (no member `on_wanted` in `EventHubOptions`).

Then in `core/include/spy/events.h` add, as the **optional last** member of `EventHubOptions`, after Task 34's `max_queued_bytes_per_consumer` (index Contracts: initializers that set only `epoch`/`loading` stay valid; every initializer that sets `on_wanted` names its members with designated initializers, as above, because the third member is the byte budget; an empty `on_wanted` is never called):
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
`plugin/src/Events.cpp`: the hub gets the callback as its `on_wanted` option (designated initializers; the loading source stays Task 39's `life::SaveLoadInProgress`, and `max_queued_bytes_per_consumer` keeps its default):
```cpp
    static spy::EventHub* hub = new spy::EventHub(spy::EventHubOptions{
        .epoch = [] { return life::Epoch(); },
        .loading = [] { return life::SaveLoadInProgress(); },
        .on_wanted = [](std::string_view e, bool w) { OnWanted(e, w); } });
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
Expected: all pass (detach test in attach-on-demand mode, including `test_rapid_subscribe_close_ends_attached`; batch 1's 9 tests show the first-event window does not bite our own call order). Then one more launch on `s4test` for batch 2 (it moves the player indoors at the end):
```bash
tools/box/box_launch.sh && tools/box/box_wait_log.sh Skython.log "events: sinks attach on demand" 600
tools/box/box_session.sh up && source out/box-live.env && tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch2.py -q -s -rs
tools/box/box_stop.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini (both verdicts)
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

Append to `spikes/RESULTS.md`: `**Task 44, sink register/unregister probe (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test, never saved; <n> launches; SKSE plugins: <list>):** PASS|FAIL` with the probe JSON and the `probe:` log lines verbatim, the activations raised during the cycles, the after-arm count (spare sink vs hub, must both be 20), `events_outside_tasks`, `overlaps`, `menu_delivered`, `delivered_any`, that the SKSE mod-event source was not cycled by the probe, the exit result, and the decision: "sinks attach on demand (Part 2 in). **Protocol fact (Task 57 copies it into PROTOCOL.md and README):** an event raised between the `events.subscribe` answer and the attach task (about one frame) is not delivered and not counted in `dropped` or `suppressed`; a subscriber that must not miss the first event subscribes before triggering it and waits about one frame", or "probe FAIL: sinks stay registered and skip when nothing is subscribed (skip-only, the Task 39 behaviour); not adopted", or "probe INCONCLUSIVE (no spare-sink event arrived outside our tasks): skip-only; not adopted". Then:
```bash
git add plugin/src/SinkProbe.h plugin/src/SinkProbe.cpp pyglue/include/spy/pymodule.h pyglue/src/pymodule.cpp \
  plugin/src/PythonHost.cpp plugin/CMakeLists.txt \
  livetest/scripts/livetest_smoke/__init__.py livetest/test_m2_sink_detach.py spikes/RESULTS.md \
  docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md
git commit -m "test(live): sink register/unregister probe and its verdict"
```
(On PASS, Part 2's commit comes first; Part 1's files can go in this commit or before it.)
