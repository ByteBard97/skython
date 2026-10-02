# Task 29: `call_method`, aliases and `form.resolve`

Adapted from: branch `plan-out-of-process`, task 17c (`task-17c-call-method-aliases-and-form-resolve.md`). The C++ is unchanged apart from task numbers (compiled and linked on the build box at the pin). Live tests run through the dev bridge.

> Part of the [Skython v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.3-4.4. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 28 is done, **spike S7 has passed** (Task 9) and **spike S4 has passed** (Task 6), per `spikes/RESULTS.md`. S7's PASS must include the S7c row with **`bound_by_class_name=true` and `dispatch_ok=true`**: a quest's own attached script (a non-native class, spec 4.3) was found by `FindBoundObject(handle, "<AttachedScript>")` and both dispatch overloads worked on that bound object. That is not the whole gate: S7c's function (`GetAliases`) is a native inherited from `Quest`, so it does not show that a **Papyrus-bytecode member function declared on the script itself** dispatches. `ResolveTarget` relies on exactly that for every call on a mod's own script (skyrim-dm's `SetupContract` path), so this task's `test_own_script_bytecode_member_function` is the rest of the gate: it must pass before Task 30. **If S4 is FAIL, STOP and ask the owner**: aliases then depend on a pre-authored Papyrus helper from the M1.5 test ESP, which invalidates the alias code below; the non-alias half (`call_method` on forms, `form.resolve`) can still go ahead if the owner says so.
>
> **Runs on:** the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); live tests run on Linux through the dev bridge and the session tunnel.
>
> **STOP and ask the owner (do not improvise) if:** S4 or S7 is missing or FAIL (S7h's FAIL, the copied-`type_info()` route for object-array arguments, concerns M2 only and does not block this task); S7c's record lacks `bound_by_class_name=true` or `dispatch_ok=true` (or either is `false`); `test_own_script_bytecode_member_function` fails, or no loaded quest script declaring a suitable function can be found (see Step 1); the cold-form route S7 recorded (`FindBoundObject` or `PackHandle`) differs from the code below by more than a rename; a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s.
>
> **Verify (prefer what S4 and S7 recorded; `API-VERIFICATION.md` checked these at the pin):** `skyrim_cast<RE::BGSRefAlias*>` / `<RE::BGSLocAlias*>` on a non-const `BGSBaseAlias*`; `RE::BGSRefAlias::VMTYPEID` (140), `RE::BGSLocAlias::VMTYPEID` (141); `TESQuest::aliases`, `TESQuest::aliasAccessLock` with `BSReadLockGuard`; `BGSBaseAlias::aliasID`, `owningQuest`; `IObjectHandlePolicy::GetObjectForHandle`, `HandleIsType`, `EmptyHandle`; `DispatchMethodCall(BSTSmartPointer<Object>&, BSFixedString, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)`; `TESForm::LookupByEditorID` (returns nullptr when the editor-id map is empty). `BGSBaseAlias::GetVMTypeID()` exists at the pin but the `skyrim_cast` + `VMTYPEID` route is the one S4 proved; keep it.

**Files:**
- Modify: `plugin/src/PapyrusValues.cpp` (alias helpers, `ResolveAlias`, `DescribeAlias`, `ResolveTarget`, alias arguments and results)
- Modify: `plugin/src/Ops.cpp` (`form.resolve`, `papyrus.call_method`)
- Modify: `livetest/test_m1_calls.py` (append)

**Interfaces:**
- Consumes: Tasks 27-28 (`pv::ResolveForm`, `BindObject`, `FindBound`, `Describe`, `sig::FindFunction`, `sig::BindArgs`, `sig::IsA`, `Callback` (owns and frees its `DynArgs`, `CanSave()` false), `DynArgs`, `KeptOnRefusal`, `PausedNow`, `PausedError`), spikes S4 (Task 6) and S7 (Task 9), core Task 14 (`AliasRef`, `alias_ref_to_json`, `form_ref_from_json`, `form_ref_to_json`) and Task 16 (`parse_call_args`).
- Produces: `pv::ResolveAlias`, `pv::DescribeAlias`, `pv::ResolveTarget`; `ops::Run` serves `form.resolve` and `papyrus.call_method`; alias references work as call targets, arguments and results; the live proof that a form argument reaches the game as that form (`test_form_argument_arrives`, and for an SKSE native `test_skse_native_with_form_argument`); the live proof that a bytecode member function declared on a mod's own script dispatches (`test_own_script_bytecode_member_function`, the rest of the S7c gate).

Rules this task implements:
- **Look up the function before resolving the object.** `CallMethod` calls `sig::FindFunction(script, function, member=true)` first, then `ResolveTarget`. A global such as `Utility.RandomInt` called on the player is then `bad_request` ("is a global function"), where resolving first would have produced `type_mismatch`. `test_method_on_wrong_kind_of_form` still gets `type_mismatch`, because the function exists on `Actor` but the target is a Weapon.
- An alias is found by quest FormID plus alias id on the main thread (spec 4.4); its class (`ReferenceAlias` or `LocationAlias`) is decided with `skyrim_cast` and the class's `VMTYPEID`, as S4 did.
- A runtime id that names nothing is `not_found`, never `stale_reference` (`ResolveForm`, Task 28).
- `form.resolve` by `editor_id` works only where the game keeps editor ids; the error says so.
- **Dispatch on a form always goes through the bound `Object&`** (S7d/S7e/S7g): `ResolveTarget` finds the bound object with `FindBoundObject` (which also accepts a parent class of the bound object: the player is found as Actor, ObjectReference and Form), and for a form with no bound object it binds one with `PackHandle`; `CallMethod` then uses the `DispatchMethodCall(BSTSmartPointer<Object>&, ...)` overload. Never the `(handle, className, ...)` overload: it returns false (no crash) on a form with no bound object, for example an unscripted quest (S7g: `handle_overload=DISPATCH_FAILED` on quest 0010FDD5).
- **Aliases are never bound on demand** (ruling R45 stands). A plain (unscripted) alias has no bound `ReferenceAlias` object; `Quest.GetAlias` on it returns, and binds, an object of class `Alias` (S7g: afterwards `find_ReferenceAlias=false find_Alias=true`), which is not dispatchable as `ReferenceAlias`. So an alias target is dispatched only through `FindBoundObject(handle, script)`; otherwise `type_mismatch`. No `PackHandle` fallback for aliases.
- **Open owner question (R45-type, not a blocker):** `PackHandle` on a cold form, and `Quest.GetAlias`/`GetAliases`, add entries to the VM's `attachedScripts` (native-class objects bound to the form, the quest and its aliases). Whether those entries persist into a save is unmeasured (S7 never saved). A bound native `Quest` object then shows up as an "attached script" (S7c pass 2: quest 0010FDD5 showed `attached_scripts=1; script Quest props=0` after S7g's PackHandle), so code that lists attached scripts must not treat a native class name as a mod script. Record the question in `spikes/RESULTS.md` under this task; do not change the code for it.

- [ ] **Step 1: Write the live tests first**

Append to `livetest/test_m1_calls.py`:
```python
def _s4():
    """The quest/alias/reference spike S4 recorded. They come from one particular throwaway save and can go stale."""
    try:
        return int(os.environ["SPY_S4_QUEST"], 16), int(os.environ["SPY_S4_ALIAS"]), int(os.environ["SPY_S4_REF"], 16)
    except KeyError as e:
        pytest.skip(f"set SPY_S4_QUEST, SPY_S4_ALIAS and SPY_S4_REF from spikes/RESULTS.md ({e.args[0]} is missing)")


def test_method_call_float(game):
    hp = game.call_method(Form(form_id=PLAYER_ID), "Actor", "GetActorValue", "Health")
    assert isinstance(hp, float) and hp > 0


def test_method_call_on_ancestor_script(game):
    name = game.call_method(Form(form_id=PLAYER_ID), "Form", "GetName")
    assert isinstance(name, str)


def test_method_call_on_cold_base_form(game):
    # A base form no script has touched has no bound script object until the server binds one.
    sword = game.form(IRON_SWORD)
    name = game.call_method(sword, "Form", "GetName")
    assert isinstance(name, str) and name
    assert game.call_method(sword, "Weapon", "GetBaseDamage") > 0


def test_resolve_by_key_and_by_id(game):
    sword = game.form(IRON_SWORD)
    assert sword.type == "Weapon" and sword.form_key == IRON_SWORD
    assert game.resolve(form_id=sword.form_id).form_key == IRON_SWORD


def test_global_called_as_member_is_bad_request(game):
    with pytest.raises(errors.BadRequest, match="global"):
        game.call_method(Form(form_id=PLAYER_ID), "Utility", "RandomInt", 1, 6)


def test_method_on_wrong_kind_of_form(game):
    sword = game.form(IRON_SWORD)
    with pytest.raises(errors.TypeMismatch, match="Weapon"):
        game.call_method(sword, "Actor", "GetActorValue", "Health")


def test_resolve_missing_form_is_not_found(game):
    with pytest.raises(errors.NotFound):
        game.form("Skyrim.esm|0xFFFFF0")
    with pytest.raises(errors.NotFound):
        game.form("NotAPlugin.esp|0x000800")


def test_editor_id_unavailable_is_a_clear_not_found(game):
    with pytest.raises(errors.NotFound, match="editor id"):
        game.resolve(editor_id="NoSuchEditorIdAnywhere")


def test_object_array_result(game):
    try:
        forms = game.call_method(Form(form_id=PLAYER_ID), "ObjectReference", "GetContainerForms")  # SKSE
    except errors.UnknownFunction:
        pytest.skip("SKSE's ObjectReference.GetContainerForms is not available")
    assert isinstance(forms, list) and forms, "the player's inventory should not be empty"
    assert all(isinstance(f, Form) for f in forms) and any(f.form_key for f in forms)


def test_form_argument_arrives(game):
    # The proof Task 28 could not give: these counts only change if the Form argument reaches the game as that form
    # (a None or empty Variable would leave GetItemCount at 0 and AddItem without effect).
    player = Form(form_id=PLAYER_ID)
    sword = game.form(IRON_SWORD)
    n = game.call_method(player, "ObjectReference", "GetItemCount", sword)
    game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
    try:
        assert game.call_method(player, "ObjectReference", "GetItemCount", sword) == n + 1
    finally:
        # RemoveItem(akItemToRemove, aiCount, abSilent, akOtherContainer): every argument is passed (no defaults), so
        # akOtherContainer is None, a typed None ObjectReference (Task 28).
        game.call_method(player, "ObjectReference", "RemoveItem", sword, 1, True, None)
    assert game.call_method(player, "ObjectReference", "GetItemCount", sword) == n


def test_alias_method_calls(game):
    quest_id, alias_id, expected_ref = _s4()
    quest = game.resolve(form_id=quest_id)
    alias = game.alias(quest, alias_id)
    # The converse of the alias-0 test below: a Quest result must decode as a Form of type Quest, not as an alias.
    owner = game.call_method(alias, "Alias", "GetOwningQuest")
    assert isinstance(owner, Form) and not isinstance(owner, Alias) and owner.form_id == quest_id and owner.type == "Quest"
    # Spec 4.4 names GetRef; ReferenceAlias also has GetReference. Check ReferenceAlias.psc and use whichever
    # this runtime's script declares (the plan calls GetReference; both return the filled reference).
    ref = game.call_method(alias, "ReferenceAlias", "GetReference")
    assert ref.form_id == expected_ref


def test_alias_result_is_an_alias_not_its_quest(game):
    # Quest.GetAlias returns an Alias object. Alias 0's handle equals the quest's FormID exactly (S7g), the hardest
    # case for ObjectToJson's form-first decode: HandleIsType(form type) must reject it and the alias check take it.
    # Side effect: GetAlias(0) binds an `Alias`-class object to plain alias 0 (S7g); harmless only because `s4test` is
    # never saved.
    quest_id, alias_id, _ = _s4()
    quest = game.resolve(form_id=quest_id)
    for aid in sorted({0, alias_id}):
        result = game.call_method(quest, "Quest", "GetAlias", aid)
        assert isinstance(result, Alias) and result.alias_id == aid and result.quest.form_id == quest_id, (aid, result)


def test_quest_result_is_a_quest_not_an_alias(game):
    # Measurement for Task 28's decode order: ObjectToJson logs, once, whether a Quest's own handle also passes
    # HandleIsType(BGSRefAlias/BGSLocAlias). Record the line in spikes/RESULTS.md; form-first is correct either way.
    quest_id, alias_id, _ = _s4()
    owner = game.call_method(game.alias(game.resolve(form_id=quest_id), alias_id), "Alias", "GetOwningQuest")
    assert isinstance(owner, Form) and not isinstance(owner, Alias) and owner.type == "Quest"
    lines = [l for l in log_text().splitlines() if "values: quest handle" in l]
    assert lines, "ObjectToJson did not log the quest-handle measurement"
    print(lines[0])


def test_alias_plain_is_not_dispatchable_as_reference_alias(game):
    # R45: an unscripted alias has no bound ReferenceAlias object, and the server never binds one on demand.
    try:
        quest_id, alias_id = int(os.environ["SPY_PLAIN_QUEST"], 16), int(os.environ["SPY_PLAIN_ALIAS"])
    except KeyError:
        pytest.skip("set SPY_PLAIN_QUEST/SPY_PLAIN_ALIAS (S7g cand 0: quest 0x0010FDD5, alias 0, plain)")
    alias = game.alias(game.resolve(form_id=quest_id), alias_id)
    with pytest.raises(errors.TypeMismatch):
        game.call_method(alias, "ReferenceAlias", "GetReference")


def test_skse_native_with_form_argument(game):
    # Actor.EquipItemEx(Form item, int equipSlot, bool preventUnequip, bool equipSound) is an SKSE native; every
    # argument is passed (no defaults). The form must arrive as that form, or IsEquipped stays False.
    player = Form(form_id=PLAYER_ID)
    sword = game.form(IRON_SWORD)
    game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
    try:
        game.call_method(player, "Actor", "EquipItemEx", sword, 1, False, False)
        time.sleep(0.5)
        assert game.call_method(player, "Actor", "IsEquipped", sword) is True
        game.call_method(player, "Actor", "UnequipItemEx", sword, 1, False)
    finally:
        game.call_method(player, "ObjectReference", "RemoveItem", sword, 1, True, None)


def _own_script():
    """A quest, a script attached to it (not a native class) and a member function declared on that script itself."""
    try:
        return (int(os.environ["SPY_OWN_QUEST"], 16), os.environ["SPY_OWN_SCRIPT"], os.environ["SPY_OWN_FUNCTION"])
    except KeyError as e:
        pytest.skip(f"set SPY_OWN_QUEST, SPY_OWN_SCRIPT and SPY_OWN_FUNCTION (see Step 1) ({e.args[0]} is missing)")


def test_own_script_bytecode_member_function(game):
    # The gate S7c could not give: a Papyrus-bytecode (non-native) member function declared on a mod's own attached
    # script, found through FindBoundObject(handle, "<script>") and dispatched on the bound object.
    quest_id, script, function = _own_script()
    quest = game.resolve(form_id=quest_id)
    first = game.call_method(quest, script, function)
    assert game.call_method(quest, script, function) == first  # a side-effect-free getter answers the same twice

```
Change the file's `from skython.values import Form` (Task 28) to `from skython.values import Alias, Form`, add `import os`, and import `log_text` from `support`. **Picking `SPY_OWN_*`:** a quest whose attached script is not a native class and not a `QF_` fragment script (S7c's `QF_TGTQ01Handler_001093E4` declares only `Fragment_N` functions, which run quest-stage code), declaring on itself a member function with **no parameters, no `native` keyword and no side effects** (it only returns a value). Confirm all three from that script's source (the mod's `.psc`, or a decompile of its `.pex`) and record the quest, script, function and source line in `spikes/RESULTS.md`. Procedure: from the S7 quest walk, list quests with attached non-`QF_` scripts; for each, decompile the `.pex` (Champollion) or read the `.psc` in the SKSE/vanilla sources, and look for a parameterless non-native getter with no side effects. **Candidates already found (2026-10-02, from the vanilla `Data/Scripts.zip` sources and the quest records in Skyrim.esm/Update.esm; notes in `.superpowers/sdd/README/task-29-spy-own-candidates.md`):** (1, use first) quest `CWCampaign` `0x0001C4A1`, script `CWCampaignScript`, `int Function getAttackDeltaMissionBonus()` (lines 1332-1336: `return attackDeltaMissionBonus`, no calls; expect 0 on an early save) → `SPY_OWN_QUEST=0x0001C4A1 SPY_OWN_SCRIPT=CWCampaignScript SPY_OWN_FUNCTION=getAttackDeltaMissionBonus`; (2) `PetFramework_ParentQuest` Update.esm `0x01002F1C`, `PetFramework_ParentQuestScript.GetCurrentPetCount()` (returns `CurrentPetCount`, expect 0; also exercises a non-zero load index); (3) `ArenaCombatQuest10Giant` `0x000967D9`, `ArenaCombatQuest.ShouldEnd()` (Bool, a real branch, expect True). None is start-game enabled: the gate relies on the quest's script being bound once the form is loaded (as S7c saw for `QF_TGTQ01Handler_001093E4`); if `FindBoundObject` fails for (1), try (2) and (3) before concluding. Check first that no script-overhaul mod on the box replaces the chosen `.pex` (a loose `Data\Scripts\CWCampaignScript.pex` would). Avoid `CompanionsBladeFragmentTracking.GetFragmentForRetrieval` (its loop never increments: the call hangs). If none of the candidates binds, STOP: the gate waits for M1.5's test ESP (a quest script of our own); report it. `SPY_PLAIN_*` is S7g's plain alias (quest `0x0010FDD5`, alias 0, on the S7 save `s4test`). No vanilla function takes an alias as a *parameter*, so the argument path (`ToVariable` with an `AliasRef` through `BindObject`) has no live test in this plan; M1.5's test ESP script declares one and gets the test. Record that in `spikes/RESULTS.md`.

- [ ] **Step 2: Alias support in `PapyrusValues.cpp`**

Four edits to Task 28's file.

(a) In the anonymous namespace, after `FindBound`, add the alias helpers (S4's route: `skyrim_cast` to the concrete class and use its `VMTYPEID`):
```cpp
// Concrete alias class and VM type id. Do not call BGSBaseAlias::GetVMTypeID(): nothing verifies it.
// skyrim_cast needs a non-const pointer (verified by compiling), hence the const_cast in DescribeAlias.
const char* AliasKind(RE::BGSBaseAlias* a)
{
    if (skyrim_cast<RE::BGSRefAlias*>(a)) return "ReferenceAlias";
    if (skyrim_cast<RE::BGSLocAlias*>(a)) return "LocationAlias";
    return nullptr;
}

RE::VMTypeID AliasTypeId(RE::BGSBaseAlias* a)
{
    return skyrim_cast<RE::BGSRefAlias*>(a) ? RE::BGSRefAlias::VMTYPEID : RE::BGSLocAlias::VMTYPEID;
}
```

(b) Replace Task 28's stub `AliasObjectToJson` with:
```cpp
json AliasObjectToJson(RE::BSScript::IObjectHandlePolicy* policy, RE::VMHandle handle)
{
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSRefAlias*>(policy->GetObjectForHandle(RE::BGSRefAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
    if (policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSLocAlias*>(policy->GetObjectForHandle(RE::BGSLocAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
    throw ProtocolError(ErrorCode::vm_error, "an alias handle in the result could not be resolved");
}
```

(c) In `ToVariable`, replace Task 28's lines
```cpp
        if (std::holds_alternative<AliasRef>(v.v))
            throw ProtocolError(ErrorCode::unsupported, "parameter " + param + ": alias arguments arrive with Task 29");
```
with
```cpp
        if (const auto* a = std::get_if<AliasRef>(&v.v)) {
            auto* alias = ResolveAlias(*a);
            BindObject(alias, AliasTypeId(alias), a->alias_type, std::format("alias {}", a->alias_id), t, param, out);
            return;
        }
```

(d) Append after `DescribeForm` (outside the anonymous namespace):
```cpp
RE::BGSBaseAlias* ResolveAlias(const AliasRef& a)
{
    auto* quest = ResolveForm(a.quest)->As<RE::TESQuest>();
    if (!quest) throw ProtocolError(ErrorCode::type_mismatch, "alias owner " + Describe(a.quest) + " is not a Quest");
    RE::BSReadLockGuard lock(quest->aliasAccessLock);
    for (auto* alias : quest->aliases) {
        if (!alias || alias->aliasID != static_cast<std::uint32_t>(a.alias_id)) continue;
        const char* kind = AliasKind(alias);
        if (!kind || a.alias_type != kind)
            throw ProtocolError(ErrorCode::type_mismatch, std::format("alias {} of {} is not a {}", a.alias_id, Describe(a.quest), a.alias_type));
        return alias;
    }
    throw ProtocolError(ErrorCode::not_found, std::format("quest {} has no alias {}", Describe(a.quest), a.alias_id));
}

AliasRef DescribeAlias(const RE::BGSBaseAlias* alias)
{
    auto* mutableAlias = const_cast<RE::BGSBaseAlias*>(alias);  // skyrim_cast wants a non-const pointer here
    AliasRef a;
    a.quest = DescribeForm(alias->owningQuest);
    a.alias_id = static_cast<std::int32_t>(alias->aliasID);
    a.alias_type = AliasKind(mutableAlias) ? AliasKind(mutableAlias) : "ReferenceAlias";
    a.epoch = life::Epoch();
    return a;
}

Target ResolveTarget(const Value& object, const std::string& script)
{
    auto* vm = VM();
    auto* policy = vm->GetObjectHandlePolicy();
    const void* ptr = nullptr;
    RE::VMTypeID type = 0;
    std::string nativeClass, desc;
    if (const auto* f = std::get_if<FormRef>(&object.v)) {
        auto* form = ResolveForm(*f);
        ptr = form;
        type = static_cast<RE::VMTypeID>(form->GetFormType());
        nativeClass = ScriptClassOf(form);
        desc = Describe(*f);
    } else if (const auto* a = std::get_if<AliasRef>(&object.v)) {
        auto* alias = ResolveAlias(*a);
        ptr = alias;
        type = AliasTypeId(alias);
        nativeClass = a->alias_type;
        desc = std::format("alias {} of {}", a->alias_id, Describe(a->quest));
    } else {
        throw ProtocolError(ErrorCode::bad_request, "object must be a form or alias reference");
    }
    const auto handle = policy->GetHandleForObject(type, ptr);
    if (handle == policy->EmptyHandle()) throw ProtocolError(ErrorCode::vm_error, "the VM has no handle for " + desc);
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    // FindBoundObject also accepts a parent class of the bound object (S7d). The caller dispatches on the returned
    // Object& overload; the (handle, className) overload returns false on a form with no bound object (S7g).
    if (FindBound(handle, script, bound)) return {bound, desc};
    // Aliases are never bound on demand (R45): a plain alias would get an `Alias`-class object (S7g), still not a
    // ReferenceAlias, and the binding adds an attachedScripts entry whose save persistence is unmeasured.
    if (std::holds_alternative<AliasRef>(object.v))
        throw ProtocolError(ErrorCode::type_mismatch, desc + " has no " + script + " script object bound (an unscripted alias; "
            "v1 drives scripted aliases only)");
    if (sig::IsA(nativeClass, script)) {
        // A form no script has touched (a cold form, or an unscripted quest) has no bound object yet; PackHandle binds
        // the native one, and the Object& overload dispatches on it (S7d/S7g). Side effect: an attachedScripts entry
        // (save persistence unmeasured, see Rules).
        Variable v;
        RE::BSScript::PackHandle(&v, ptr, type);
        if (auto obj = v.GetObject()) return {obj, desc};
        throw ProtocolError(ErrorCode::vm_error, "could not bind a " + nativeClass + " script object to " + desc);
    }
    throw ProtocolError(ErrorCode::type_mismatch, desc + " is a " + nativeClass + " and has no script " + script + " attached");
}
```

- [ ] **Step 3: `form.resolve` and `call_method` in `Ops.cpp`**

Add inside the anonymous namespace of `plugin/src/Ops.cpp`, after `CallStatic`:
```cpp
json Resolve(const json& args)
{
    RE::TESForm* form = nullptr;
    if (args.contains("editor_id")) {
        if (!args["editor_id"].is_string()) throw ProtocolError(ErrorCode::bad_request, "editor_id must be a string");
        const auto eid = args["editor_id"].get<std::string>();
        // Verified at the pinned commit: LookupByEditorID tests its map pointer and returns nullptr, so no guard is
        // needed. Editor ids are only populated when an editor-id-caching mod is installed, so null is common.
        form = RE::TESForm::LookupByEditorID(eid);
        if (!form)
            throw ProtocolError(ErrorCode::not_found, "no form with editor id " + eid +
                " (the game keeps editor ids for only some form types unless powerofthree's Tweaks is installed)");
    } else {
        json ref = args;
        ref["kind"] = "form";
        form = pv::ResolveForm(form_ref_from_json(ref));
    }
    return form_ref_to_json(pv::DescribeForm(form));
}

void CallMethod(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && PausedNow()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    // Function first, object second: a global called as a member must be bad_request ("use papyrus.call_static"),
    // not the type_mismatch that resolving the object would raise.
    auto fn = sig::FindFunction(c.script, c.function, true);
    auto target = pv::ResolveTarget(*c.object, c.script);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    auto* args = new DynArgs(std::move(vars));
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done, args)};  // cb owns args (S2)
    if (!VM()->DispatchMethodCall(target.object, RE::BSFixedString(c.function), args, cb)) {
        KeptOnRefusal(qualified);  // never `delete args` here (R37); same rule as CallStatic
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified + " on " + target.desc));
    }
}
```
In `Run`, add before the `unsupported` fallthrough:
```cpp
        if (op == "form.resolve") return done(OpResult::Ok(Resolve(args)));
        if (op == "papyrus.call_method") return CallMethod(parse_call_args(args, true), done);
```
`form_ref_from_json` comes from `spy/values.h` (already included through `PapyrusValues.h`); `Ops.cpp` also needs `#undef GetObject` after its includes only if it calls `Variable::GetObject()` (it does not in this task).

- [ ] **Step 4: Build, deploy, run**

The game runs on the Windows box ([Task 1c](task-01c-part-c-on-the-box.md)); waits count only this launch's log text.
```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_mainmenu.py -q -rs
# Load (not Continue) the named save S4 recorded its values from (spikes/RESULTS.md, e.g. s4test): the most recent
# save may be another one after Tasks 27-28, and the alias tests would then fail. Then Task 1b block B6.
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
export SPY_S4_QUEST=0x0010FDD5 SPY_S4_ALIAS=7 SPY_S4_REF=0x0001A677   # S4 row of spikes/RESULTS.md; the alias tests skip when unset
export SPY_PLAIN_QUEST=0x0010FDD5 SPY_PLAIN_ALIAS=0              # S7g's plain alias on s4test
export SPY_OWN_QUEST=0x... SPY_OWN_SCRIPT=... SPY_OWN_FUNCTION=...   # picked in Step 1; the gate test skips when unset
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_calls.py -q -rs -s
tools/box/box_quit.sh     # NOT_CLEAN with NO_NEW_CRASH_LOG: tools/box/box_stop.sh (Task 1b Known issue)
```
Expected: `test_m1_mainmenu.py` passes; everything in `test_m1_calls.py` passes. Any skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure. The alias tests are skipped with a message when the `SPY_S4_*` (or `SPY_PLAIN_*`) variables are unset, and the gate test when `SPY_OWN_*` is unset; a skip is not a pass, so run them once with the variables set before committing. If S4 failed, STOP (see the gate) rather than marking them `xfail`. **If `test_form_argument_arrives` fails (the count does not change) or crashes the game, STOP**: form arguments do not reach the game as the form (the `PackHandle`/`BindObject` route S7 recorded differs from Task 28's code); do not go on to Task 30. If `test_alias_result_is_an_alias_not_its_quest` gets a Quest back (alias 0 is the likely case: its handle equals the quest's FormID), the form check in `ObjectToJson` (Task 28) accepted an alias handle, contrary to S7g: STOP and ask the owner; do not swap the order without a ruling. Record `test_quest_result_is_a_quest_not_an_alias`'s printed line (run with `-s`) in `spikes/RESULTS.md`. In the printed `values: quest handle HHHHHHHHLLLLLLLL` line, compare `HHHHHHHH` with Quest's VM type id (`RE::FormType::Quest`, 0x4D). If they are equal, record the collision in `spikes/RESULTS.md` and flag it for Task 32's limits. Also record `is_ref_alias` and `is_loc_alias`. If `test_skse_native_with_form_argument` fails, check whether `IsEquipped` simply lags (raise the sleep once) before concluding the form did not arrive. `test_own_script_bytecode_member_function` and the alias tests must run (not skip) before commit; a failure of the first is a STOP (gate). If `test_method_call_on_cold_base_form` crashes or fails with `vm_error`, the `PackHandle` route differs from S7's record: STOP.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): call_method, alias references and form.resolve"
```
