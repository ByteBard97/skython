# Task 6: Spike S6 — load a save through `AddUITask` under Proton

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Add the S6 handler and lifecycle logging**

```cpp
namespace spike {
std::atomic_int g_postLoad{0}, g_loadingClosed{0};

struct MenuSink : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (e && e->menuName == RE::LoadingMenu::MENU_NAME) {
            Log(std::format("spike: Loading Menu {}", e->opening ? "opened" : "closed"));
            if (!e->opening) g_loadingClosed.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S6(const std::string& save)
{
    const int pl0 = g_postLoad, lc0 = g_loadingClosed;
    SKSE::GetTaskInterface()->AddUITask([save] {
        Log("spike: S6 calling BGSSaveLoadManager::Load(" + save + ")");
        RE::BGSSaveLoadManager::GetSingleton()->Load(save.c_str());
    });
    for (int i = 0; i < 120 && (g_postLoad == pl0 || g_loadingClosed == lc0); ++i)
        std::this_thread::sleep_for(std::chrono::seconds(1));
    return std::format("S6 {} postLoadGame_delta={} loadingClosed_delta={}",
        (g_postLoad > pl0 && g_loadingClosed > lc0) ? "PASS" : "FAIL", g_postLoad - pl0, g_loadingClosed - lc0);
}
}
```
In `OnMessage`: add `case SKSE::MessagingInterface::kPostLoadGame: spike::Log("kPostLoadGame"); spike::g_postLoad.fetch_add(1); break;` and `kPreLoadGame`/`kNewGame` log lines. In `kDataLoaded`: `static spike::MenuSink menuSink; RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(&menuSink);`
In `Handle`: `if (line.rfind("s6 ", 0) == 0) return S6(line.substr(3));`

- [ ] **Step 2: Build, deploy, relaunch; make a named save; run**

In game: open the console and type `save spytest`. Then:
Run: `python3 spikes/spike_client.py s6 spytest`
Expected PASS: `S6 PASS postLoadGame_delta=1 loadingClosed_delta=1`. The spike log shows the order of `kPostLoadGame` and `Loading Menu closed` — record which comes first (Task 15's interactive signal depends on it). Also try from the main menu (quit to main menu, run again).

- [ ] **Step 3: Record, gate, commit**

Fill the S6 row in `spikes/RESULTS.md`. Then review every row: for any FAIL, apply the "If it fails, change" column of spec section 9 to the spec before continuing.
```bash
git add spikes docs/specs && git commit -m "test(spike): S6 AddUITask load under Proton; record M0 results"
```
