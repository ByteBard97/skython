# Task 8: Spike S6 — load a save through `AddUITask` under Proton

Adapted from: branch `plan-out-of-process`, task 6 (`task-06-spike-s6-load-a-save-through-adduitask.md`). The C++ is unchanged; gates, task numbers and the consumers of the findings changed.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.1 and 4.3). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; S2, S9 and S4 gate Part C (Tasks 25-32); S7 gates Tasks 28 and 29; **S5, S6 and S10 gate only the M2 plan**: an S6 FAIL is recorded and M1 continues. Run on a throwaway save; this spike loads saves, so never point it at a save that matters.

**Embedded-design note:** the embedded interpreter and its scripts keep running across loads (spec 5), and Task 26 assumes it; M0 has no other check of this. So Step 2 starts Python and Task 3's in-game echo server **before** the loads, and after them checks that the interpreter still answers (`py_status`, `py_exec`) and that the Python socket server it started is still serving (`py_echo_client.py`). Record the three answers as a finding; they are not an S6 criterion, but a failure there must be reported to the owner (it contradicts spec 5).

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Tasks 1, 3, 4): `spike::Log`, `spike::WaitUntil`, `spike::ParseInt`, the `OnMessage` switch in `main.cpp`; Task 3's harness commands (`py_load`, `py_init`, `py_status`, `py_exec`), `spikes/py_send.py`, `spikes/py/echo_server.py` and `spikes/py_echo_client.py`. No new includes.
- Produces: the S6 row in `spikes/RESULTS.md`: whether the load worked via `AddUITask`, whether the same call via `AddTask` (the control) did nothing or started a load, whether Python (interpreter and a socket server it started) survives a load, which save-name form works, and the order of `kPostLoadGame` and `Loading Menu closed` (Task 17's `LifecycleState` and Task 25's lifecycle wiring depend on it; the `LifecycleState` tests cover both orders, so this is a confirmation, not a design input).

- [ ] **Step 0: Check the gate**

```bash
grep -E '^\| S2 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S2 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
```
Expected: the S2 row prints (this task uses Task 4's `WaitUntil`).

- [ ] **Step 1: Add the S6 handler and lifecycle logging**

```cpp
namespace spike {
std::atomic_int g_postLoadOk{0}, g_postLoadFail{0}, g_loadingClosed{0};
std::atomic_int g_preLoad{0}, g_loadingOpened{0};  // a load STARTED (kPreLoadGame / Loading Menu opened)
std::atomic_bool g_loadingMenuOpen{false};
std::atomic_int g_seq{0}, g_postLoadSeq{0}, g_loadingClosedSeq{0};  // which event came first in the last load

struct MenuSink : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (e && e->menuName == RE::LoadingMenu::MENU_NAME) {
            Log(std::format("spike: Loading Menu {}", e->opening ? "opened" : "closed"));
            g_loadingMenuOpen = e->opening;
            if (e->opening) {
                g_loadingOpened.fetch_add(1);
            } else {
                g_loadingClosedSeq = ++g_seq;
                g_loadingClosed.fetch_add(1);
            }
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

// Empty string when no load is in progress: the Loading Menu is not open (sink state and UI::IsMenuOpen on the
// main thread) and the kPreLoadGame count stays unchanged for 10 s. Otherwise the reason.
std::string LoadInProgress()
{
    const int pre0 = g_preLoad;
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < end) {
        if (g_loadingMenuOpen) return "loading_menu_open";
        if (g_preLoad != pre0) return std::format("kPreLoadGame_fired_during_10s_check(delta={})", g_preLoad - pre0);
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    auto menu = std::make_shared<std::atomic_bool>(false);
    const auto r = OnMainThread([menu](std::function<void(std::string)> finish) {
        auto* ui = RE::UI::GetSingleton();
        *menu = ui && ui->IsMenuOpen(RE::LoadingMenu::MENU_NAME);
        finish("ok");
    }, std::chrono::seconds(10));
    if (r != "ok") return "main_thread_check_" + r;
    return *menu ? "loading_menu_open(UI)" : "";
}

// viaUi = true: AddUITask (the design). viaUi = false: AddTask, the CONTROL run (DevBench's AddTask load
// silently does nothing under Proton; the control shows whether that is still true here). The control counts a
// load as STARTED when kPreLoadGame or Loading Menu opened fires, so a slow load still in flight after its 30 s
// is never mistaken for "did nothing"; the AddUITask run refuses to start while any load is in progress, so the
// control's late events cannot satisfy it.
std::string S6(const std::string& save, bool viaUi)
{
    if (viaUi) {
        if (const auto busy = LoadInProgress(); !busy.empty())
            return "S6 REFUSED load in progress (" + busy + "): wait for it to finish (the control may have started one), then retry";
    }
    const int ok0 = g_postLoadOk, fail0 = g_postLoadFail, lc0 = g_loadingClosed, pre0 = g_preLoad, lo0 = g_loadingOpened;
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
    const int preD = g_preLoad - pre0, loD = g_loadingOpened - lo0;
    const bool pass = landed && okD >= 1 && failD == 0 && lcD >= 1;
    const bool started = preD > 0 || loD > 0;
    const int pls = g_postLoadSeq, lcs = g_loadingClosedSeq;
    const char* first = (pls == 0 && lcs == 0) ? "none" : pls == 0 ? "LoadingMenuClosed" : lcs == 0 ? "kPostLoadGame"
                      : pls < lcs ? "kPostLoadGame" : "LoadingMenuClosed";
    const char* verdict = viaUi ? (pass ? "PASS" : "FAIL")
                        : pass ? "CONTROL_LOADED" : started ? "CONTROL_STARTED_LOAD" : "CONTROL_DID_NOTHING";
    return std::format("S6 {} via={} save={} preLoadGame_delta={} loadingOpened_delta={} postLoadGame_ok_delta={} postLoadGame_fail_delta={} loadingClosed_delta={} first={}",
        verdict, viaUi ? "AddUITask" : "AddTask", save, preD, loD, okD, failD, lcD, first);
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
    case SKSE::MessagingInterface::kPreLoadGame:
        spike::Log("kPreLoadGame");
        spike::g_preLoad.fetch_add(1);
        break;
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
In game: open the console and type `save spytest`, then close the console (unpaused, no menu). Then start Python and its echo server **before** any load, so the loads below also show whether they survive one:
```bash
python3 spikes/spike_client.py py_load           # "PY_LOAD ALREADY_LOADED" is fine (autostart marker from Task 3)
python3 spikes/spike_client.py py_init           # "PY_INIT ALREADY_STARTED" is fine
for i in $(seq 1 30); do r=$(python3 spikes/spike_client.py py_status); case "$r" in *running*|*failed*) echo "$r"; break;; esac; sleep 1; done
python3 spikes/py_send.py spikes/py/echo_server.py   # OK listening 127.0.0.1:45979
python3 spikes/spike_client.py s6task spytest    # CONTROL via AddTask, 30 s
python3 spikes/spike_client.py s6 spytest        # via AddUITask, up to 120 s (refuses while a load is in progress)
python3 spikes/spike_client.py py_status         # after the loads: PY_STATUS running
python3 spikes/spike_client.py py_exec "result = 1+1"   # OK 2
python3 spikes/py_echo_client.py                 # S8 socket PASS: the Python server started before the loads still serves
```
Expected before the loads: `PY_STATUS running` and `OK listening 127.0.0.1:45979` (if Python does not start, record it and continue with the S6 lines; the survival check is then `not run`). Control: `S6 CONTROL_DID_NOTHING via=AddTask ... preLoadGame_delta=0 loadingOpened_delta=0 postLoadGame_ok_delta=0 ...`. `CONTROL_STARTED_LOAD` means `AddTask` did start a load (`kPreLoadGame` or the Loading Menu fired) that had not finished in 30 s: AddTask is **not** inert here; record it. If the real run then answers `S6 REFUSED load in progress (...)`, wait for that load to finish (Loading Menu closed in the spike log), make sure you are back in `spytest`'s gameplay, and send `s6 spytest` again; it refuses until no Loading Menu is open and `kPreLoadGame` has not fired for 10 s.

Expected PASS: `S6 PASS via=AddUITask ... preLoadGame_delta=1 loadingOpened_delta=1 postLoadGame_ok_delta=1 postLoadGame_fail_delta=0 loadingClosed_delta=1 first=...`. `postLoadGame_fail_delta=1` means the game reported a failed load (the packed `data` was 0): that is a FAIL even though the event fired. `first=none` means neither `kPostLoadGame` nor `Loading Menu closed` fired. If `CONTROL_LOADED` appears, `AddTask` also works here and `AddUITask` is not strictly needed, but it is still the design; record it. After the loads, expected `PY_STATUS running`, `OK 2` and `S8 socket PASS`; anything else contradicts spec 5 (the interpreter and its scripts keep running across loads): record the exact output and tell the owner (Task 26 assumes it).

If the load does nothing, try the other save-name form once and record which works: the plain name (`spytest`) or with the extension (`spytest.ess`). The result decides what `game.load {save}` passes to `Load`. Also try from the main menu (quit to the main menu, run `s6 spytest` again). The spike log shows the order of `kPostLoadGame` and `Loading Menu closed`: record which comes first.

- [ ] **Step 3: Record and commit**

Fill the S6 row in `spikes/RESULTS.md` (control result with its `preLoadGame_delta`, real result with its `preLoadGame_delta`, accepted name form, event order, main-menu result, and the three Python-across-the-load answers: `py_status`, `py_exec "result = 1+1"`, `py_echo_client.py`). An S6 FAIL: write the change from spec section 9's "If it fails, change" column into the row's last cell, tell the owner, and do not edit the spec until they agree. M1 continues.
```bash
git add spikes && git commit -m "test(spike): S6 AddUITask load under Proton with an AddTask control run"
```
M0 is complete only after Tasks 9 (S7) and 10 (S10) and their records; Task 10's last step reviews every row.
