# Task 8: Spike S6 — load a save through `AddUITask` under Proton

Adapted from: branch `plan-out-of-process`, task 6 (`task-06-spike-s6-load-a-save-through-adduitask.md`). The C++ is unchanged; gates, task numbers and the consumers of the findings changed.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.1 and 4.3). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; S2, S9 and S4 gate Part C (Tasks 25-32); S7 gates Tasks 28 and 29; **S5, S6 and S10 gate only the M2 plan**: an S6 FAIL is recorded and M1 continues. Run on a throwaway save; this spike loads saves, so never point it at a save that matters.

**Embedded-design note:** the embedded interpreter keeps running across loads (spec 5). If Task 3's spike has initialised Python in this session (`py_init`), a load is also a check that it survives one: after the S6 runs, `python3 spikes/spike_client.py py_exec "1+1"` (Task 3's command) must still answer. Record the answer as a finding; it is not an S6 criterion.

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Tasks 1, 4): `spike::Log`, `spike::WaitUntil`, `spike::ParseInt`, the `OnMessage` switch in `main.cpp`. No new includes.
- Produces: the S6 row in `spikes/RESULTS.md`: whether the load worked via `AddUITask`, whether the same call via `AddTask` (the control) did nothing, which save-name form works, and the order of `kPostLoadGame` and `Loading Menu closed` (Task 17's `LifecycleState` and Task 25's lifecycle wiring depend on it; the `LifecycleState` tests cover both orders, so this is a confirmation, not a design input).

- [ ] **Step 0: Check the gate**

```bash
grep -E '^\| S2 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S2 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
```
Expected: the S2 row prints (this task uses Task 4's `WaitUntil`).

- [ ] **Step 1: Add the S6 handler and lifecycle logging**

```cpp
namespace spike {
std::atomic_int g_postLoadOk{0}, g_postLoadFail{0}, g_loadingClosed{0};
std::atomic_int g_seq{0}, g_postLoadSeq{0}, g_loadingClosedSeq{0};  // which event came first in the last load

struct MenuSink : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (e && e->menuName == RE::LoadingMenu::MENU_NAME) {
            Log(std::format("spike: Loading Menu {}", e->opening ? "opened" : "closed"));
            if (!e->opening) {
                g_loadingClosedSeq = ++g_seq;
                g_loadingClosed.fetch_add(1);
            }
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

// viaUi = true: AddUITask (the design). viaUi = false: AddTask, the CONTROL run (DevBench's AddTask load
// silently does nothing under Proton; the control shows whether that is still true here).
std::string S6(const std::string& save, bool viaUi)
{
    const int ok0 = g_postLoadOk, fail0 = g_postLoadFail, lc0 = g_loadingClosed;
    g_seq = 0; g_postLoadSeq = 0; g_loadingClosedSeq = 0;
    auto body = [save, viaUi] {
        Log(std::format("spike: S6 calling BGSSaveLoadManager::Load({}) via {}", save, viaUi ? "AddUITask" : "AddTask"));
        // The Load signature differs between CommonLibSSE-NG commits (an extra bool such as a_checkForMods on some).
        // Verify it by grepping plugin/extern/CommonLibSSE-NG (docs/PINS.md) and match the header; do not guess.
        RE::BGSSaveLoadManager::GetSingleton()->Load(save.c_str());
    };
    if (viaUi) SKSE::GetTaskInterface()->AddUITask(body); else SKSE::GetTaskInterface()->AddTask(body);
    const bool landed = WaitUntil([&] { return g_loadingClosed > lc0 && (g_postLoadOk + g_postLoadFail) > (ok0 + fail0); },
                                  std::chrono::seconds(viaUi ? 120 : 30));
    const int okD = g_postLoadOk - ok0, failD = g_postLoadFail - fail0, lcD = g_loadingClosed - lc0;
    const bool pass = landed && okD >= 1 && failD == 0 && lcD >= 1;
    return std::format("S6 {} via={} save={} postLoadGame_ok_delta={} postLoadGame_fail_delta={} loadingClosed_delta={} first={}",
        viaUi ? (pass ? "PASS" : "FAIL") : (pass ? "CONTROL_LOADED" : "CONTROL_DID_NOTHING"), viaUi ? "AddUITask" : "AddTask", save,
        okD, failD, lcD, g_postLoadSeq < g_loadingClosedSeq ? "kPostLoadGame" : "LoadingMenuClosed");
}
}
```
In `OnMessage` add (packed data, not a pointer: the spec says `kPostLoadGame`'s data is a packed value; confirm in the SKSE messaging header that a non-zero value means success, record the raw value in the log either way):
```cpp
    case SKSE::MessagingInterface::kPostLoadGame: {
        const auto raw = reinterpret_cast<std::uintptr_t>(m->data);
        spike::Log(std::format("kPostLoadGame data={:#x} ({})", raw, raw ? "success" : "FAILED load"));
        spike::g_postLoadSeq = ++spike::g_seq;
        (raw ? spike::g_postLoadOk : spike::g_postLoadFail).fetch_add(1);
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame: spike::Log("kPreLoadGame"); break;
    case SKSE::MessagingInterface::kNewGame: spike::Log("kNewGame"); break;
```
In `kDataLoaded`: `static spike::MenuSink menuSink; RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(&menuSink);`
In `Handle` (two commands, `ParseInt` is not needed; the argument is a string):
```cpp
    if (line.rfind("s6 ", 0) == 0) return S6(line.substr(3), true);
    if (line.rfind("s6task ", 0) == 0) return S6(line.substr(7), false);
```

- [ ] **Step 2: Build, deploy, relaunch; make a named save; run the control first, then the real run**

```bash
# Only the spike DLL changes; the Python runtime and SpikePyCore.dll deployed by Task 3 stay where they are.
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game (qqq); relaunch; wait for the listener line as in Task 4 Step 2; load a throwaway save
```
In game: open the console and type `save spytest`, then close the console (unpaused, no menu). Then:
```bash
python3 spikes/spike_client.py s6task spytest    # CONTROL via AddTask, 30 s: expected CONTROL_DID_NOTHING (postLoadGame deltas 0)
python3 spikes/spike_client.py s6 spytest        # via AddUITask, up to 120 s
python3 spikes/spike_client.py py_init           # only if Python is not initialised yet in this session (Task 3); an "already" answer is fine
python3 spikes/spike_client.py py_exec "1+1"     # after the load: Python still answers (finding, not a criterion)
```
Expected PASS: `S6 PASS via=AddUITask ... postLoadGame_ok_delta=1 postLoadGame_fail_delta=0 loadingClosed_delta=1 first=...`. `postLoadGame_fail_delta=1` means the game reported a failed load (the packed `data` was 0): that is a FAIL even though the event fired. If `CONTROL_LOADED` appears, `AddTask` also works here and `AddUITask` is not strictly needed, but it is still the design; record it.

If the load does nothing, try the other save-name form once and record which works: the plain name (`spytest`) or with the extension (`spytest.ess`). The result decides what `game.load {save}` passes to `Load`. Also try from the main menu (quit to the main menu, run `s6 spytest` again). The spike log shows the order of `kPostLoadGame` and `Loading Menu closed`: record which comes first.

- [ ] **Step 3: Record and commit**

Fill the S6 row in `spikes/RESULTS.md` (control result, real result, accepted name form, event order, main-menu result, and whether `py_exec` still answered after the load). An S6 FAIL: write the change from spec section 9's "If it fails, change" column into the row's last cell, tell the owner, and do not edit the spec until they agree. M1 continues.
```bash
git add spikes && git commit -m "test(spike): S6 AddUITask load under Proton with an AddTask control run"
```
M0 is complete only after Tasks 9 (S7) and 10 (S10) and their records; Task 10's last step reviews every row.
