# Task 29: `call_method`, aliases and `form.resolve`

Adapted from: branch `plan-out-of-process`, task 17c (`task-17c-call-method-aliases-and-form-resolve.md`). The C++ is unchanged apart from task numbers (compiled and linked on the build box at the pin). Live tests run through the dev bridge.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.3-4.4. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 28 is done, **spike S7 has passed** (Task 9) and **spike S4 has passed** (Task 6), per `spikes/RESULTS.md`. **If S4 is FAIL, STOP and ask the owner**: aliases then depend on a pre-authored Papyrus helper from the M1.5 test ESP, which invalidates the alias code below; the non-alias half (`call_method` on forms, `form.resolve`) can still go ahead if the owner says so.
>
> **Runs on:** the Windows build box plus the game; live tests run on Linux through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** S4 or S7 is missing or FAIL; the cold-form route S7 recorded (`FindBoundObject` or `PackHandle`) differs from the code below by more than a rename; a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s.
>
> **Verify (prefer what S4 and S7 recorded; `API-VERIFICATION.md` checked these at the pin):** `skyrim_cast<RE::BGSRefAlias*>` / `<RE::BGSLocAlias*>` on a non-const `BGSBaseAlias*`; `RE::BGSRefAlias::VMTYPEID` (140), `RE::BGSLocAlias::VMTYPEID` (141); `TESQuest::aliases`, `TESQuest::aliasAccessLock` with `BSReadLockGuard`; `BGSBaseAlias::aliasID`, `owningQuest`; `IObjectHandlePolicy::GetObjectForHandle`, `HandleIsType`, `EmptyHandle`; `DispatchMethodCall(BSTSmartPointer<Object>&, BSFixedString, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)`; `TESForm::LookupByEditorID` (returns nullptr when the editor-id map is empty). `BGSBaseAlias::GetVMTypeID()` exists at the pin but the `skyrim_cast` + `VMTYPEID` route is the one S4 proved; keep it.

**Files:**
- Modify: `plugin/src/PapyrusValues.cpp` (alias helpers, `ResolveAlias`, `DescribeAlias`, `ResolveTarget`, alias arguments and results)
- Modify: `plugin/src/Ops.cpp` (`form.resolve`, `papyrus.call_method`)
- Modify: `livetest/test_m1_calls.py` (append)

**Interfaces:**
- Consumes: Tasks 27-28 (`pv::ResolveForm`, `BindObject`, `FindBound`, `Describe`, `sig::FindFunction`, `sig::BindArgs`, `sig::IsA`, `Callback`, `DynArgs`, `PausedNow`, `PausedError`), spikes S4 (Task 6) and S7 (Task 9), core Task 14 (`AliasRef`, `alias_ref_to_json`, `form_ref_from_json`, `form_ref_to_json`) and Task 16 (`parse_call_args`).
- Produces: `pv::ResolveAlias`, `pv::DescribeAlias`, `pv::ResolveTarget`; `ops::Run` serves `form.resolve` and `papyrus.call_method`; alias references work as call targets, arguments and results.

Rules this task implements:
- **Look up the function before resolving the object.** `CallMethod` calls `sig::FindFunction(script, function, member=true)` first, then `ResolveTarget`. A global such as `Utility.RandomInt` called on the player is then `bad_request` ("is a global function"), where resolving first would have produced `type_mismatch`. `test_method_on_wrong_kind_of_form` still gets `type_mismatch`, because the function exists on `Actor` but the target is a Weapon.
- An alias is found by quest FormID plus alias id on the main thread (spec 4.4); its class (`ReferenceAlias` or `LocationAlias`) is decided with `skyrim_cast` and the class's `VMTYPEID`, as S4 did.
- A runtime id that names nothing is `not_found`, never `stale_reference` (`ResolveForm`, Task 28).
- `form.resolve` by `editor_id` works only where the game keeps editor ids; the error says so.

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


def test_missing_runtime_id_is_not_found_not_stale(game):
    with pytest.raises(errors.NotFound):
        game.call_method(Form(form_id=0xFF123456), "ObjectReference", "GetBaseObject")


def test_editor_id_unavailable_is_a_clear_not_found(game):
    with pytest.raises(errors.NotFound, match="editor id"):
        game.resolve(editor_id="NoSuchEditorIdAnywhere")


def test_object_array_result(game):
    try:
        forms = game.call_method(Form(form_id=PLAYER_ID), "ObjectReference", "GetContainerForms")  # SKSE
    except errors.UnknownFunction:
        pytest.skip("SKSE's ObjectReference.GetContainerForms is not available")
    assert isinstance(forms, list) and all(isinstance(f, Form) for f in forms)


def test_alias_method_calls(game):
    quest_id, alias_id, expected_ref = _s4()
    quest = game.resolve(form_id=quest_id)
    alias = game.alias(quest, alias_id)
    assert game.call_method(alias, "Alias", "GetOwningQuest").form_id == quest_id
    # Spec 4.4 names GetRef; ReferenceAlias also has GetReference. Check ReferenceAlias.psc and use whichever
    # this runtime's script declares (the plan calls GetReference; both return the filled reference).
    ref = game.call_method(alias, "ReferenceAlias", "GetReference")
    assert ref.form_id == expected_ref


def test_alias_result_is_an_alias_not_its_quest(game):
    # Quest.GetAlias returns an Alias object. An alias handle carries the quest's FormID, so a form-first
    # decode would answer with the Quest; the alias-first check in ObjectToJson must win.
    quest_id, alias_id, _ = _s4()
    quest = game.resolve(form_id=quest_id)
    result = game.call_method(quest, "Quest", "GetAlias", alias_id)
    assert isinstance(result, Alias) and result.alias_id == alias_id and result.quest.form_id == quest_id

```
Change the file's `from skyrim.values import Form` (Task 28) to `from skyrim.values import Alias, Form`, and add `import os`. No vanilla function takes an alias as a *parameter*, so the argument path (`ToVariable` with an `AliasRef` through `BindObject`) has no live test in this plan; M1.5's test ESP script declares one and gets the test. Record that in `spikes/RESULTS.md`.

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
    if (FindBound(handle, script, bound)) return {bound, desc};
    if (sig::IsA(nativeClass, script)) {
        // A form no script has touched has no bound object yet; packing its handle makes the VM find or create
        // the native one. This is the route S7 (Task 9) proved on a cold base form.
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
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done)};
    auto* args = new DynArgs(std::move(vars));  // S2 (Task 4): who frees it if the dispatch fails; same rule as CallStatic
    if (!VM()->DispatchMethodCall(target.object, RE::BSFixedString(c.function), args, cb))
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified + " on " + target.desc));
}
```
In `Run`, add before the `unsupported` fallthrough:
```cpp
        if (op == "form.resolve") return done(OpResult::Ok(Resolve(args)));
        if (op == "papyrus.call_method") return CallMethod(parse_call_args(args, true), done);
```
`form_ref_from_json` comes from `spy/values.h` (already included through `PapyrusValues.h`); `Ops.cpp` also needs `#undef GetObject` after its includes only if it calls `Variable::GetObject()` (it does not in this task).

- [ ] **Step 4: Build, deploy, run**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --package
tools/launch_game.sh
source tools/env.sh
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "Main Menu" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
uv run --project python pytest livetest/test_m1_mainmenu.py -q
# Manual: press Continue (the save S4 recorded its values from, for the alias tests). Then:
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "interactive = true" 300
export SPY_S4_QUEST=0x... SPY_S4_ALIAS=... SPY_S4_REF=0x...   # from spikes/RESULTS.md; the alias tests skip when unset
uv run --project python pytest livetest/test_m1_calls.py -q -rs
```
Expected: `test_m1_mainmenu.py` passes; everything in `test_m1_calls.py` passes. The two alias tests are skipped with a message when the `SPY_S4_*` variables are unset; a skip is not a pass, so run them once with the variables set before committing. If S4 failed, STOP (see the gate) rather than marking them `xfail`. If `test_alias_result_is_an_alias_not_its_quest` gets a Quest back, `AliasObjectToJson` is not reached: re-check the order in `ObjectToJson` (Task 28). If `test_method_call_on_cold_base_form` crashes or fails with `vm_error`, the `PackHandle` route differs from S7's record: STOP.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): call_method, alias references and form.resolve"
```
