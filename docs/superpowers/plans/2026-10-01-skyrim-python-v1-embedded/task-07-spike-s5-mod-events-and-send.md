# Task 7: Spike S5 — mod events and a native Papyrus function reach C++

Adapted from: branch `plan-out-of-process`, task 5 (`task-05-spike-s5-mod-events-and-a-native.md`). The C++ and the compile recipe are unchanged; gates, task numbers and the consumers of the findings changed.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 4.6). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; S2, S9 and S4 gate Part C (Tasks 25-32); S7 gates Tasks 28 and 29; **S5, S6 and S10 gate only the M2 plan**: an S5 FAIL is recorded, the spec section 9 change is noted, and M1 continues. Run on a throwaway save.

**Why this still matters in the embedded design:** in M2, mod events and `SkyrimPython.Send` reach Python through C++ event queues that a Python dispatch thread drains (spec 4.6). The sink and the native function run on engine threads that must never run Python (Global Constraints), so they only copy the arguments and push. S5 records which threads those are.

**Files:**
- Create: `spikes/spike-dll/papyrus/SkyrimPython.psc`
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Tasks 1, 4): `spike::OnMainThread`, `spike::WaitUntil`, `spike::Tid`, `spike::TidOf`, `spike::g_mainTid`, `spike::NoArgs`, `spike::Log`. No new includes.
- Produces: the S5 row in `spikes/RESULTS.md`, including which thread delivers a mod event and which runs the native `Send` (spec 4.6 and the M2 event queues depend on it), and the working Papyrus compile command (M1.5 reuses it). `spikes/spike-dll/papyrus/SkyrimPython.psc` is the declaration the real plugin ships in M2.

- [ ] **Step 0: Check the gate**

```bash
grep -E '^\| S4 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || echo "no S4 PASS row yet: S5 may still run (it does not use aliases), but Part C stays blocked until S4 passes"
grep -E '^\| S2 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S2 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
```
Expected: the S2 row prints (this task uses Task 4's helpers).

- [ ] **Step 1: Declare the native script**

`spikes/spike-dll/papyrus/SkyrimPython.psc`. Do **not** add the `Hidden` flag: this install has no Papyrus flags file, and the compiler rejects `Hidden` without one (the owner's compile notes record this). Nothing here needs it.
```papyrus
ScriptName SkyrimPython

; Delivers {channel, payload} to Python subscribers of `channel`.
Function Send(String channel, String payload) global native
```

- [ ] **Step 2: Compile it to `.pex`**

Confirm the recipe before writing any command. The owner's working notes are in `/home/geoff/skse-http-probe/STATUS-2026-08-23.md` ("The compile recipe") and possibly `/home/geoff/projects/games/skyrim/Chronicle/notes/inbox/` (search for "PapyrusCompiler"); read them. **Two facts in those notes differ from this plan and must be resolved first, not assumed:**
- The notes compile with a Proton prefix ending `...compatdata/4190904829` and the compiler under `~/Games/NGVO/Stock Game/Papyrus Compiler/`, while this plan's game prefix is `...4190904831` (SimpleSkyrim). A compiler also exists at `~/Games/SimpleSkyrim/Stock Game/Papyrus Compiler/PapyrusCompiler.exe`. **Determine which prefix and which compiler actually work** by trying the smallest compile (this script, below) with `WINEPREFIX` set to the prefix you think is right; a wrong prefix fails at wine start-up or cannot find the compiler. Do not write the final command into this plan or a script until one run has produced `SkyrimPython.pex`.
- The notes say there is no flags file in the install. If the compiler asks for one (an error naming `TESV_Papyrus_Flags.flg`), find it with `find ~/Games -iname 'TESV_Papyrus_Flags.flg'` and pass it with `-f=`.

Template (fill in the two values you confirmed; `-i` must contain the folder with `SkyrimPython.psc`, and because the script imports nothing no vanilla sources are needed):
```bash
export WINEPREFIX="<the prefix you confirmed, ending in /pfx>"
export WINE="$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-14/files/bin/wine64"
COMPILER="<the PapyrusCompiler.exe you confirmed>"
mkdir -p spikes/spike-dll/papyrus/compiled
"$WINE" "$COMPILER" "SkyrimPython.psc" \
  -i="Z:\\home\\geoff\\projects\\games\\skyrim\\skyrim-python\\spikes\\spike-dll\\papyrus" \
  -o="Z:\\home\\geoff\\projects\\games\\skyrim\\skyrim-python\\spikes\\spike-dll\\papyrus\\compiled"
ls -l spikes/spike-dll/papyrus/compiled/SkyrimPython.pex
```
Expected: `SkyrimPython.pex` exists. Then record the exact working command (with the confirmed prefix and compiler) in `spikes/RESULTS.md` under the S5 row, so M1.5 reuses it. Copy the `.pex` to the game and append its path to `out/deployed.txt` (Task 1 teardown lists it):
```bash
source tools/env.sh
cp spikes/spike-dll/papyrus/compiled/SkyrimPython.pex "$SIMPLESKYRIM_DATA/Scripts/SkyrimPython.pex"
echo "$SIMPLESKYRIM_DATA/Scripts/SkyrimPython.pex" >> out/deployed.txt
```
(`*.pex` is git-ignored; the `.psc` is the committed source.) If no working compile can be produced, **STOP and ask the owner**: S5 then cannot test the native `Send`, and the M2 plan cannot rely on it.

- [ ] **Step 3: Register the native function and the mod-event sink**

Add as a new `namespace spike { ... }` block above `Handle` (no new includes):
```cpp
namespace spike {
std::atomic_int g_sends{0}, g_modEvents{0};
std::string g_lastSend, g_lastMod, g_sendTid, g_modTid;  // guarded by g_mx
std::mutex g_mx;

void NativeSend(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    std::lock_guard l(g_mx);
    g_lastSend = std::format("{}|{}", channel.c_str(), payload.c_str());
    g_sendTid = Tid();
    g_sends.fetch_add(1);
}

bool RegisterPapyrus(RE::BSScript::IVirtualMachine* vm)
{
    vm->RegisterFunction("Send"sv, "SkyrimPython"sv, NativeSend);  // verify the RegisterFunction overload by grepping the submodule
    return true;
}

struct ModSink : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        if (e && e->eventName == "spyTest") {
            std::lock_guard l(g_mx);
            // {:.6f}: std::format prints a float 42.0f as "42", not "42.000000"
            g_lastMod = std::format("{}|{}|{:.6f}", e->eventName.c_str(), e->strArg.c_str(), e->numArg);
            g_modTid = Tid();
            g_modEvents.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S5()
{
    // Counters are never reset by earlier runs: compare against the values at the start of THIS run.
    const int ev0 = g_modEvents.load(), sends0 = g_sends.load();
    {
        std::lock_guard l(g_mx);
        g_lastMod.clear(); g_lastSend.clear(); g_modTid.clear(); g_sendTid.clear();
    }
    auto r = OnMainThread([](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        // Handle type for an Actor form: verify by grepping how CommonLibSSE-NG maps form types to VMTypeID.
        auto handle = policy->GetHandleForObject(static_cast<RE::VMTypeID>(RE::FormType::ActorCharacter), player);
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> none;
        const bool a = vm->DispatchMethodCall(handle, RE::BSFixedString("Form"), RE::BSFixedString("SendModEvent"),
            RE::MakeFunctionArguments(RE::BSFixedString("spyTest"), RE::BSFixedString("hello"), 42.0f), none);
        const bool b = vm->DispatchStaticCall(RE::BSFixedString("SkyrimPython"), RE::BSFixedString("Send"),
            RE::MakeFunctionArguments(RE::BSFixedString("chan"), RE::BSFixedString("{\"x\":1}")), none);
        finish(std::format("dispatched(modEvent={},send={})", a, b));
    }, std::chrono::seconds(10));
    // Bounded wait for both to land (was a fixed sleep).
    WaitUntil([&] { return g_modEvents.load() > ev0 && g_sends.load() > sends0; }, std::chrono::seconds(10));
    std::lock_guard l(g_mx);
    const int evD = g_modEvents.load() - ev0, sendD = g_sends.load() - sends0;
    const bool pass = evD == 1 && sendD == 1 && g_lastMod == "spyTest|hello|42.000000" && g_lastSend == "chan|{\"x\":1}";
    const auto mainTid = TidOf(g_mainTid.load());
    return std::format("S5 {} dispatch={} mod_events_delta={} last_mod={} mod_tid={} sends_delta={} last_send={} send_tid={} main_tid={}",
        pass ? "PASS" : "FAIL", r, evD, g_lastMod, g_modTid, sendD, g_lastSend, g_sendTid, mainTid);
}
}
```
In the spike's SKSE load entry point (`SKSEPluginLoad` / `SKSE_PLUGIN_LOAD` in `main.cpp`, Task 1), after `SKSE::Init`: `SKSE::GetPapyrusInterface()->Register(spike::RegisterPapyrus);`
In `OnMessage` `kDataLoaded`: `static spike::ModSink sink; SKSE::GetModCallbackEventSource()->AddEventSink(&sink);`
In `Handle`: `if (line == "s5") return S5();`

- [ ] **Step 4: Build, deploy, relaunch, run** (throwaway save loaded, unpaused)

```bash
# Only the spike DLL changes; the Python runtime and SpikePyCore.dll deployed by Task 3 stay where they are.
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game (qqq); relaunch; wait for the listener line as in Task 4 Step 2; load the save; then:
python3 spikes/spike_client.py s5
python3 spikes/spike_client.py s5    # run it twice: deltas must be 1 each time, so a rerun cannot pass on stale counters
```
Expected PASS (both runs): `S5 PASS dispatch=dispatched(modEvent=1,send=1) mod_events_delta=1 last_mod=spyTest|hello|42.000000 mod_tid=... sends_delta=1 last_send=chan|{"x":1} send_tid=... main_tid=...`. Record `mod_tid` and `send_tid` against `main_tid`: spec 4.6 needs to know which thread delivers events (the M2 event bus must not touch engine state from a thread that is not the main thread). If `dispatch=` shows `send=0`, the `.pex` is not where the game looks or the native was not registered: check the spike log and the `Scripts` folder.

- [ ] **Step 5: Record and commit**

Fill the S5 row in `spikes/RESULTS.md` (both runs' output, `mod_tid` and `send_tid` against `main_tid`, the confirmed compile command). FAIL here changes spec 4.6 (`Send` only, or `ModCallbackEvent` only) and does not block M1.
```bash
git add spikes && git commit -m "test(spike): S5 ModCallbackEvent sink and native SkyrimPython.Send"
```
