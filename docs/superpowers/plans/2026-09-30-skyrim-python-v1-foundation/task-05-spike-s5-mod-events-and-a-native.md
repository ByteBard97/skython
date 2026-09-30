# Task 5: Spike S5 — mod events and a native Papyrus function reach C++

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Create: `spikes/spike-dll/papyrus/SkyrimPython.psc`
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Declare the native script**

`spikes/spike-dll/papyrus/SkyrimPython.psc`:
```papyrus
ScriptName SkyrimPython Hidden

; Delivers {channel, payload} to Python subscribers of `channel`.
Function Send(String channel, String payload) global native
```

- [ ] **Step 2: Compile it to `.pex`**

Follow the working Papyrus-compiler-under-Proton recipe in `/home/geoff/skse-http-probe/STATUS-2026-08-23.md` (lines 12–25), with `spikes/spike-dll/papyrus` as the input folder and `Scripts.zip`-free flags (the script imports nothing). Output: `SkyrimPython.pex`. Copy it to `"$SIMPLESKYRIM_DATA/Scripts/SkyrimPython.pex"`.

- [ ] **Step 3: Register the native function and the mod-event sink**

Add to `main.cpp`:
```cpp
namespace spike {
std::atomic_int g_sends{0}, g_modEvents{0};
std::string g_lastSend, g_lastMod;
std::mutex g_mx;

void NativeSend(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    std::lock_guard l(g_mx);
    g_lastSend = std::format("{}|{}", channel.c_str(), payload.c_str());
    g_sends.fetch_add(1);
}

bool RegisterPapyrus(RE::BSScript::IVirtualMachine* vm)
{
    vm->RegisterFunction("Send"sv, "SkyrimPython"sv, NativeSend);
    return true;
}

struct ModSink : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        if (e && e->eventName == "spyTest") {
            std::lock_guard l(g_mx);
            g_lastMod = std::format("{}|{}|{}", e->eventName.c_str(), e->strArg.c_str(), e->numArg);
            g_modEvents.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S5()
{
    auto r = OnMainThread([](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto handle = policy->GetHandleForObject(static_cast<RE::VMTypeID>(RE::FormType::ActorCharacter), player);
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> none;
        vm->DispatchMethodCall(handle, "Form"sv, "SendModEvent"sv,
            RE::MakeFunctionArguments(RE::BSFixedString("spyTest"), RE::BSFixedString("hello"), 42.0f), none);
        vm->DispatchStaticCall("SkyrimPython"sv, "Send"sv,
            RE::MakeFunctionArguments(RE::BSFixedString("chan"), RE::BSFixedString("{\"x\":1}")), none);
        finish("dispatched");
    }, std::chrono::seconds(10));
    std::this_thread::sleep_for(std::chrono::seconds(2));  // spike only: let both land
    std::lock_guard l(g_mx);
    return std::format("S5 {} dispatch={} mod_events={} last_mod={} sends={} last_send={}",
        (g_modEvents > 0 && g_sends > 0) ? "PASS" : "FAIL", r, g_modEvents.load(), g_lastMod, g_sends.load(), g_lastSend);
}
}
```
In `SKSEPluginLoad`, after `SKSE::Init`: `SKSE::GetPapyrusInterface()->Register(spike::RegisterPapyrus);`
In `OnMessage` `kDataLoaded`: `static spike::ModSink sink; SKSE::GetModCallbackEventSource()->AddEventSink(&sink);`
In `Handle`: `if (line == "s5") return S5();`

- [ ] **Step 4: Build, deploy, relaunch, run** (save loaded)

Run: `python3 spikes/spike_client.py s5`
Expected PASS: `mod_events=1 last_mod=spyTest|hello|42.000000 sends=1 last_send=chan|{"x":1}`.

- [ ] **Step 5: Record and commit**

```bash
git add spikes && git commit -m "test(spike): S5 ModCallbackEvent sink and native SkyrimPython.Send"
```
