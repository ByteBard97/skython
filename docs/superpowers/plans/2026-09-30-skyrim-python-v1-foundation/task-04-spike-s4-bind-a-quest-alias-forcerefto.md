# Task 4: Spike S4 — bind a quest alias, `ForceRefTo` it, read it back

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

Spec S4 criterion: `ForceRefTo` on an alias bound by quest + alias ID, then `GetReference` returns the same reference. There is no test ESP until M1.5, so the spike borrows a running vanilla quest's filled `ReferenceAlias` in a throwaway save, forces it to the player, reads it back, and restores the original. Each step is its own main-thread task (no dispatch from inside a callback), so S4 does not depend on S2's nested-dispatch answer.

- [ ] **Step 1: Add the S4 handler**

```cpp
namespace spike {
struct AliasPick
{
    RE::VMHandle handle = 0;
    RE::FormID quest = 0, original = 0;
    std::uint32_t alias = 0;
};

// Dispatches ReferenceAlias.<fn>(arg) on the alias handle; returns the result's form id as hex ("00000000" for None).
std::string AliasCall(RE::VMHandle handle, std::string fn, RE::TESObjectREFR* arg)
{
    return OnMainThread([handle, fn, arg](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish](RE::BSScript::Variable v) {
            std::uint32_t id = 0;
            if (v.IsObject() && v.GetObject()) id = static_cast<std::uint32_t>(v.GetObject()->GetHandle() & 0xFFFFFFFF);
            finish(std::format("{:08X}", id));
        }) };
        RE::BSScript::IFunctionArguments* args = arg ? RE::MakeFunctionArguments(std::move(arg)) : RE::MakeFunctionArguments();
        if (!vm->DispatchMethodCall(handle, "ReferenceAlias"sv, RE::BSFixedString(fn), args, cb)) finish("DISPATCH_FAILED");
    }, std::chrono::seconds(10));
}

std::string S4()
{
    auto pick = std::make_shared<AliasPick>();
    auto found = OnMainThread([pick](std::function<void(std::string)> finish) {
        auto* dh = RE::TESDataHandler::GetSingleton();
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (auto* quest : dh->GetFormArray<RE::TESQuest>()) {
            if (!quest || !quest->IsRunning()) continue;
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* base : quest->aliases) {
                auto* ref = base ? skyrim_cast<RE::BGSRefAlias*>(base) : nullptr;
                auto* target = ref ? ref->GetReference() : nullptr;
                if (!target) continue;
                pick->handle = vm->GetObjectHandlePolicy()->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ref);
                pick->quest = quest->GetFormID();
                pick->alias = ref->aliasID;
                pick->original = target->GetFormID();
                return finish("found");
            }
        }
        finish("none");
    }, std::chrono::seconds(10));
    if (found != "found") return "S4 FAIL no running quest with a filled reference alias";

    auto* player = RE::PlayerCharacter::GetSingleton();
    const auto before = AliasCall(pick->handle, "GetReference", nullptr);
    const auto forced = AliasCall(pick->handle, "ForceRefTo", player);
    const auto after = AliasCall(pick->handle, "GetReference", nullptr);
    const auto restored = AliasCall(pick->handle, "ForceRefTo", RE::TESForm::LookupByID<RE::TESObjectREFR>(pick->original));
    const auto last = AliasCall(pick->handle, "GetReference", nullptr);
    const bool pass = before == std::format("{:08X}", pick->original) && after == "00000014" && last == before;
    return std::format("S4 {} quest={:08X} alias={} handle={:016X} before={} forced={} after={} restored={} last={}",
        pass ? "PASS" : "FAIL", pick->quest, pick->alias, pick->handle, before, forced, after, restored, last);
}
}
```
And in `Handle`: `if (line == "s4") return S4();`

- [ ] **Step 2: Build, deploy, relaunch, run** (with a throwaway save loaded: make one with console `save s4test` first)

Run: `python3 spikes/spike_client.py s4`
Expected PASS: `S4 PASS quest=Q alias=A handle=H before=R ... after=00000014 ... last=R`. Record `quest`, `alias` and `before` (the original reference): Task 17's live test uses them as `SPY_S4_QUEST`, `SPY_S4_ALIAS`, `SPY_S4_REF`. Also record the `handle` layout (high 32 bits) in `RESULTS.md`. If it FAILS, record the output; spec 4.4 then routes alias calls through a pre-authored Papyrus helper (M1.5), and Task 17's alias support becomes `unsupported`.

- [ ] **Step 3: Record and commit**

```bash
git add spikes && git commit -m "test(spike): S4 bind ReferenceAlias by handle, ForceRefTo and read back"
```
