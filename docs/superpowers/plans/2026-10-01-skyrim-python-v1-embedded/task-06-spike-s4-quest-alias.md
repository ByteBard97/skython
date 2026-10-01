# Task 6: Spike S4 — bind a quest alias, `ForceRefTo` it, read it back

Adapted from: branch `plan-out-of-process`, task 4 (`task-04-spike-s4-bind-a-quest-alias-forcerefto.md`). The C++ is unchanged; gates, task numbers and the consumers of the findings changed.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; **S2, S9 and S4 gate Part C (Tasks 25-32)**; S7 gates Tasks 28 and 29; S5, S6 and S10 gate only the M2 plan. **S4 FAIL: STOP and ask the owner.** An S4 failure routes alias calls (spec 4.4) through a pre-authored Papyrus helper, which needs the M1.5 test ESP and **invalidates Task 29's alias code** (`ResolveAlias`, `AliasRef` handling, alias calls in `ResolveTarget`); do not start Part C until the owner has decided.

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Task 4): `spike::OnMainThread`, `spike::Functor`, `spike::NoArgs`, `spike::Log`, `spike::ParseInt`. No new includes are needed (Task 1's block covers them).
- Produces for Tasks 9 and 29: the quest FormID, alias ID and original reference (`SPY_S4_QUEST`, `SPY_S4_ALIAS`, `SPY_S4_REF`), the working read-back function name (`GetReference` or `GetRef`), the alias handle layout, whether a scripted and an unscripted alias both dispatch, and a LocationAlias yes/no, all in `spikes/RESULTS.md`.
- Produces for Task 9 (code): `spike::AliasCand`, `spike::AliasPick` (`quest`, `alias`, `original`, `attached`, `candidates`), `spike::PickAlias(isLoc)` (first candidate in `quest`/`alias`/`original`; up to `kMaxAliasCandidates` = 50 in `candidates`), `spike::AliasCall`.

Spec S4 criterion: `ForceRefTo` on an alias bound by quest + alias ID, then `GetRef` returns the same reference. There is no test ESP until M1.5, so the spike borrows a running vanilla quest's filled alias in a **throwaway save**, forces it to the player, reads it back, and restores the original. What makes the test meaningful: the original is **not** the player (otherwise "forced to the player, reads back the player" proves nothing), and the alias is re-resolved from quest FormID plus alias ID inside every main-thread task (the spec 4.4 path), with no engine pointer kept across tasks. Each step is its own main-thread task (no dispatch from inside a callback), so S4 does not depend on S2's nested-dispatch answer.

- [ ] **Step 0: Check the gate, and prepare a throwaway save**

```bash
grep -E '^\| S2 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S2 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
```
Expected: the S2 row prints (Task 4 adds the helpers this task uses). If it does not, **STOP and ask the owner**.

Load a save (the S2 throwaway, or any disposable one), then in the console type `save s4test`. After S4 runs, **do not save the game again** (the test forces a vanilla quest's alias; if a step fails part-way the alias stays forced in memory). Finish by quitting to the main menu without saving, or reload `s4test`.

- [ ] **Step 1: Add the S4 handlers**

Add as a new `namespace spike { ... }` block above `Handle`:
```cpp
namespace spike {
struct AliasCand
{
    RE::FormID quest = 0, original = 0;  // ids only: nothing here is an engine pointer
    std::uint32_t alias = 0;
    bool attached = false;  // the alias has a script attached (vm->attachedScripts has its handle)
};
struct AliasPick
{
    RE::FormID quest = 0, original = 0;  // the first candidate; ids only
    std::uint32_t alias = 0;
    bool attached = false;
    std::string note;
    std::vector<AliasCand> candidates;  // up to kMaxAliasCandidates, in data-handler order
};
constexpr std::size_t kMaxAliasCandidates = 50;

// Re-resolves quest -> alias by (quest FormID, alias ID) on the main thread, dispatches
// <ReferenceAlias|LocationAlias>.<fn>(arg) on the alias handle, and returns the result's form id as hex
// ("00000000" for None). argId 0 = no argument. Null-safe: a missing quest, alias or argument form
// returns an error string and dispatches nothing (in particular never ForceRefTo with no argument).
std::string AliasCall(RE::FormID questId, std::uint32_t aliasId, bool isLoc, std::string fn, RE::FormID argId, std::chrono::seconds wait = std::chrono::seconds(10))
{
    return OnMainThread([=](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto* quest = RE::TESForm::LookupByID<RE::TESQuest>(questId);
        if (!quest) return finish("QUEST_NOT_FOUND");

        RE::BGSBaseAlias* base = nullptr;
        {
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* a : quest->aliases)
                if (a && a->aliasID == aliasId) { base = a; break; }
        }
        if (!base) return finish("ALIAS_NOT_FOUND");

        // skyrim_cast + VMTYPEID, as Task 29 does (GetVMTypeID() exists per API-VERIFICATION.md; Task 9's S7f checks it agrees).
        void* obj = nullptr;
        RE::VMTypeID type = 0;
        if (isLoc) {
            obj = skyrim_cast<RE::BGSLocAlias*>(base);
            type = RE::BGSLocAlias::VMTYPEID;
        } else {
            obj = skyrim_cast<RE::BGSRefAlias*>(base);
            type = RE::BGSRefAlias::VMTYPEID;
        }
        if (!obj) return finish("WRONG_ALIAS_KIND");
        const auto handle = policy->GetHandleForObject(type, obj);
        if (handle == policy->EmptyHandle()) return finish("NO_HANDLE");

        RE::BSScript::IFunctionArguments* args = nullptr;
        if (argId == 0) {
            args = RE::MakeFunctionArguments();
        } else if (isLoc) {
            auto* loc = RE::TESForm::LookupByID<RE::BGSLocation>(argId);
            if (!loc) return finish("ARG_NOT_FOUND");
            args = RE::MakeFunctionArguments(std::move(loc));
        } else {
            auto* ref = RE::TESForm::LookupByID<RE::TESObjectREFR>(argId);  // 0x14 is the player, resolved here on the main thread
            if (!ref) return finish("ARG_NOT_FOUND");
            args = RE::MakeFunctionArguments(std::move(ref));
        }
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish](RE::BSScript::Variable v) {
            std::uint32_t id = 0;
            if (v.IsObject() && v.GetObject()) id = static_cast<std::uint32_t>(v.GetObject()->GetHandle() & 0xFFFFFFFF);
            finish(std::format("{:08X}", id));
        }) };
        if (!vm->DispatchMethodCall(handle, RE::BSFixedString(isLoc ? "LocationAlias" : "ReferenceAlias"),
                                    RE::BSFixedString(fn), args, cb))
            finish("DISPATCH_FAILED");
    }, wait);
}

// Collects up to kMaxAliasCandidates aliases of running quests: for references, filled and NOT the player;
// for locations, any location alias (filled or not: there is no C++ getter, S4Loc asks Papyrus). The first
// candidate is copied into quest/alias/original. Ids only leave the main thread.
std::shared_ptr<AliasPick> PickAlias(bool isLoc)
{
    auto pick = std::make_shared<AliasPick>();
    auto r = OnMainThread([pick, isLoc](std::function<void(std::string)> finish) {
        auto* dh = RE::TESDataHandler::GetSingleton();
        std::vector<std::pair<AliasCand, void*>> found;  // engine pointers stay inside this one main-thread task
        for (auto* quest : dh->GetFormArray<RE::TESQuest>()) {
            if (found.size() >= kMaxAliasCandidates) break;
            if (!quest || !quest->IsRunning()) continue;
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* base : quest->aliases) {
                if (!base || found.size() >= kMaxAliasCandidates) continue;
                RE::FormID original = 0;
                void* obj = nullptr;
                if (isLoc) {
                    // BGSLocAlias has no C++ location getter (verified: its members are unnamed). Any location alias
                    // qualifies here; S4Loc reads the current location through Papyrus (LocationAlias.GetLocation).
                    obj = skyrim_cast<RE::BGSLocAlias*>(base);
                    if (obj) original = 1;  // sentinel for "found"; not a FormID
                } else {
                    auto* ra = skyrim_cast<RE::BGSRefAlias*>(base);
                    auto* target = ra ? ra->GetReference() : nullptr;
                    if (target) original = target->GetFormID();
                    obj = ra;
                }
                if (original == 0 || original == 0x14) continue;  // empty, or the player: proves nothing
                found.push_back({ AliasCand{ quest->GetFormID(), original, base->aliasID, false }, obj });
            }
        }
        // Scripted or not: an attached script gives the alias a bound VM object of its own (checked after the
        // alias locks are released, so no alias lock is held while the VM lock is taken).
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        for (auto& [cand, obj] : found) {
            const auto h = policy->GetHandleForObject(isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID, obj);
            RE::BSSpinLockGuard lock(vm->attachedScriptsLock);
            const auto it = vm->attachedScripts.find(h);
            cand.attached = it != vm->attachedScripts.end();
            pick->candidates.push_back(cand);
        }
        finish(found.empty() ? "none" : "found");
    }, std::chrono::seconds(10));
    if (r != "found") {
        pick->note = r;
        return pick;
    }
    pick->quest = pick->candidates[0].quest;
    pick->alias = pick->candidates[0].alias;
    pick->original = pick->candidates[0].original;
    pick->attached = pick->candidates[0].attached;
    return pick;
}

struct S4Run
{
    bool pass = false;
    std::string text;
};
// Force one reference alias to the player, read it back both ways, restore it.
S4Run S4On(const AliasCand& c)
{
    const auto q = c.quest;
    const auto a = c.alias;
    const auto orig = std::format("{:08X}", c.original);

    const auto before = AliasCall(q, a, false, "GetReference", 0);
    const auto beforeRef = AliasCall(q, a, false, "GetRef", 0);  // spec 4.4 names GetRef, the plan used GetReference: record which exist
    const auto forced = AliasCall(q, a, false, "ForceRefTo", 0x14);
    const auto after = AliasCall(q, a, false, "GetReference", 0);
    const auto afterRef = AliasCall(q, a, false, "GetRef", 0);
    const auto restored = AliasCall(q, a, false, "ForceRefTo", c.original);  // null-safe: ARG_NOT_FOUND dispatches nothing
    const auto last = AliasCall(q, a, false, "GetReference", 0);

    const bool getReferenceWorks = before == orig && after == "00000014";
    const bool getRefWorks = beforeRef == orig && afterRef == "00000014";
    const bool restoredOk = last == orig;
    const bool pass = (getReferenceWorks || getRefWorks) && restoredOk;
    return { pass, std::format("quest={:08X} alias={} original={} attached={} before={} beforeRef={} forced={} after={} afterRef={} restored={} last={} "
                               "getReference_works={} getRef_works={} restore_ok={}{}",
        q, a, orig, c.attached, before, beforeRef, forced, after, afterRef, restored, last,
        getReferenceWorks, getRefWorks, restoredOk,
        restoredOk ? "" : " RESTORE_FAILED: quit WITHOUT saving or reload s4test") };
}

std::string S4()
{
    auto pick = PickAlias(false);
    if (pick->original == 0)
        return "S4 FAIL no running quest with a filled reference alias whose target is not the player (" + pick->note + ")";
    const auto first = S4On(pick->candidates[0]);
    // A scripted alias has a bound VM object of its own, so it does not show that a plain (unscripted) alias is
    // dispatchable: when the first one is scripted, also run on the first unscripted candidate.
    std::string second = " unscripted=not_needed";
    bool secondOk = true;
    if (pick->attached) {
        const auto it = std::find_if(pick->candidates.begin(), pick->candidates.end(), [](const AliasCand& c) { return !c.attached; });
        if (it == pick->candidates.end()) {
            second = std::format(" unscripted=none_among_{}_candidates", pick->candidates.size());
        } else {
            const auto run = S4On(*it);
            secondOk = run.pass;
            second = std::format(" | unscripted: {} {}", run.pass ? "PASS" : "FAIL", run.text);
        }
    }
    return std::format("S4 {} {}{}", first.pass && secondOk ? "PASS" : "FAIL", first.text, second);
}

// LocationAlias case: find a FILLED location alias (up to kMaxAliasCandidates tried through Papyrus), force it
// to a different Location, read back, restore.
std::string S4Loc()
{
    auto pick = PickAlias(true);
    if (pick->original == 0) return "S4LOC NOT_RUN no running quest with a location alias (" + pick->note + ")";
    RE::FormID q = 0, origId = 0;
    std::uint32_t a = 0;
    std::string before;
    std::size_t tried = 0;
    for (const auto& c : pick->candidates) {
        ++tried;
        const auto got = AliasCall(c.quest, c.alias, true, "GetLocation", 0);
        const auto id = static_cast<RE::FormID>(std::strtoul(got.c_str(), nullptr, 16));
        if (id != 0) {
            q = c.quest;
            a = c.alias;
            origId = id;
            before = got;
            break;
        }
    }
    if (origId == 0) return std::format("S4LOC NOT_RUN all {} location aliases tried are empty", tried);
    // A different Location: the first BGSLocation in the data handler whose id differs from the original.
    auto other = std::make_shared<RE::FormID>(0);
    (void)OnMainThread([other, orig = origId](std::function<void(std::string)> finish) {
        for (auto* loc : RE::TESDataHandler::GetSingleton()->GetFormArray<RE::BGSLocation>())
            if (loc && loc->GetFormID() != orig) { *other = loc->GetFormID(); break; }
        finish("done");
    }, std::chrono::seconds(10));
    if (*other == 0) return "S4LOC NOT_RUN no second Location found";
    const auto orig = std::format("{:08X}", origId);
    const auto want = std::format("{:08X}", *other);

    const auto forced = AliasCall(q, a, true, "ForceLocationTo", *other);
    const auto after = AliasCall(q, a, true, "GetLocation", 0);
    const auto restored = AliasCall(q, a, true, "ForceLocationTo", origId);
    const auto last = AliasCall(q, a, true, "GetLocation", 0);
    const bool pass = before == orig && after == want && last == orig;
    return std::format("S4LOC {} quest={:08X} alias={} original={} other={} before={} forced={} after={} restored={} last={} tried={}",
        pass ? "PASS" : "FAIL", q, a, orig, want, before, forced, after, restored, last, tried);
}
}
```
And in `Handle`: `if (line == "s4") return S4(); if (line == "s4loc") return S4Loc();`

Names to confirm by grepping `plugin/extern/CommonLibSSE-NG` before building (record in `docs/PINS.md`; fix compile errors by matching the header): `TESQuest::aliases`/`aliasAccessLock`/`IsRunning`, `BGSBaseAlias::aliasID`, `BGSRefAlias::GetReference`, `BGSLocAlias` location accessor, `BGSRefAlias::VMTYPEID`/`BGSLocAlias::VMTYPEID`, `TESDataHandler::GetFormArray`, `MakeFunctionArguments` with a `BGSLocation*`, `Variable::IsObject`/`GetObject`, and that `LookupByID<T>` exists with that template form.

- [ ] **Step 2: Build, deploy, relaunch, run** (with `s4test` loaded; unpaused, no menu)

Only the spike DLL changes; the Python runtime and `SpikePyCore.dll` deployed by Task 3 stay where they are.

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game (qqq), then relaunch and load s4test:
tools/launch_game.sh
source tools/env.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "listening on 127.0.0.1:45978" 600
python3 spikes/spike_client.py s4
python3 spikes/spike_client.py s4loc
```
Expected PASS: `S4 PASS quest=Q alias=A original=R attached=... ... after=00000014 ... last=R getReference_works=1 getRef_works=...`; the original `R` is not `00000014` (the spike refuses to run otherwise). If the first alias is scripted (`attached=true`), the line continues with `| unscripted: PASS quest=... attached=false ...` (the same test on the first unscripted alias; a plain shell-quest alias has no bound object of its own, which is the case skyrim-dm relies on) or `unscripted=none_among_N_candidates` (record that the unscripted case is unverified). `S4LOC PASS` with `after` equal to `other` and `tried=` the number of location aliases probed (up to 50) until a filled one was found; if none of them is filled, `S4LOC NOT_RUN all N location aliases tried are empty` is acceptable, but then **LocationAlias support in Task 29 is unverified**: say so in the RESULTS.md row and tell the owner. Then quit to the main menu **without saving**.

Record for Task 29: `quest`, `alias` and `original` (Task 29's live test reads them as `SPY_S4_QUEST`, `SPY_S4_ALIAS`, `SPY_S4_REF`; they belong to this throwaway save and go stale if the save changes, so the Task 29 tests must skip when the variables are unset or the quest is not running); which read-back name worked (`GetReference` and/or `GetRef`; the spec and Task 29 must then use that name); and the `handle` layout (whether the high 32 bits are a type id or the quest FormID; Task 9 decodes it properly).

- [ ] **Step 3: Record and commit**

Fill the S4 row in `spikes/RESULTS.md` (S4 and S4LOC outputs, which read-back name works, whether the tested alias was scripted (`attached`) and whether the unscripted case ran). **If S4 FAILS: record the exact output, STOP and ask the owner.** Spec 4.4 then routes alias calls through a pre-authored Papyrus helper (the M1.5 test ESP), Task 29's alias support becomes `unsupported` until then, and the owner decides whether Part C proceeds without alias support.
```bash
git add spikes && git commit -m "test(spike): S4 bind ReferenceAlias and LocationAlias by quest and alias id, ForceRefTo and read back"
```
