# Task 28: Papyrus value conversion and signatures

Adapted from: branch `plan-out-of-process`, task 17b (`task-17b-papyrus-value-conversion-and-signatures.md`). The C++ is the branch's (it compiled and linked on the build box at the pinned CommonLibSSE-NG, with the `API-VERIFICATION.md` fixes applied) plus plan review F's fixes: a typed None via `Variable(t)`, `type_mismatch` for an array argument over 128, core's float encoder, and `FindProperty` returning the whole `PropertyTypeInfo`. The live tests now go through the dev bridge.

> Part of the [Skython v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), section 4.4. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 27 is done **and spike S7 has passed** (Task 9; `spikes/RESULTS.md` has an S7 PASS row recording the exact `FindBoundObject`, `PackHandle`, `GetParam` and `ObjectTypeInfo` calls that work on the pinned checkout). **If S4 (Task 6) is FAIL, STOP and ask the owner** before going on to Task 29.
>
> **Runs on:** the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); live tests run on Linux through the dev bridge and the session tunnel.
>
> **STOP and ask the owner (do not improvise) if:** S7 is missing or FAIL (S7h's FAIL, the copied-`type_info()` route for object-array arguments, concerns M2 only and does not block this task: v1 refuses object-array arguments); the form-argument route S7 recorded differs from the code below by more than a rename; a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s.
>
> **Verified against CommonLibSSE-NG 39f9d07 (see `API-VERIFICATION.md`; this code compiled and linked on the build box).** `FindBoundObject(VMHandle, const char*, BSTSmartPointer<Object>&) const` (3 parameters); `Array::MAX_SIZE` is 128 but private (hence the local constant); `GetRawType()` exists on `TypeInfo`, not on `Variable`; `#undef GetObject` is needed after the includes (windows.h macro). Still re-check against S7's record: `GetObjectHandlePolicy`, `GetHandleForObject`, `HandleIsType`, `GetObjectForHandle`, `EmptyHandle`; `RE::BSScript::PackHandle`; `Variable(const TypeInfo&)` (a typed None) and `Variable& operator=(Variable&&)`, `SetArray`, `GetArray`, `GetObject`; `Object::GetHandle`; `VirtualMachine::CreateArray(TypeInfo, size, BSTSmartPointer<Array>&)`; `Array::size`, `operator[]`; `TESDataHandler::LookupForm(localId, pluginName)`; `TESForm::LookupByID`, `IsDeleted`, `GetFile(0)`, `GetLocalFormID`.

**Files:**
- Modify: `plugin/src/Signatures.cpp` (add `IsA`, `FindProperty`)
- Modify: `plugin/src/PapyrusValues.cpp` (full replacement below, **but carry over Task 27's `SourceFile(form)` fallback**: `TESForm::GetFile(0)` is null for the player (0x14) and other forms, so Task 27 (97532fd) added `SourceFile()` — `GetFile(0)`, else for a non-runtime id `TESDataHandler::LookupLoadedLightModByIndex((id >> 12) & 0xFFF)` for a light id or `LookupLoadedModByIndex(id >> 24)` — and made `KeyOrId` use it with core's `local_form_id(id)` (not `GetLocalFormID()`) and `DescribeForm` test `SourceFile(form)`. Every place the code below calls `GetFile(0)` or `GetLocalFormID()` to name a form must use those instead; `test_state_reports_player` must still give `Skyrim.esm|0x000014`. The light-plugin branch is untested live: if a light-plugin form is at hand on the box, add one assertion.)
- Modify: `livetest/test_m1_calls.py` (replace one test, append others)

**Interfaces:**
- Consumes: Task 27 (all headers, `ops::Run`, `Callback`, `pv::NeedsMainThread`, `life::Epoch()`), spike S7 (Task 9, verified calls), core Tasks 14-15 (`Value`, `FormRef`, `ArrayValue`, `form_ref_to_json`, `check_fingerprint`, `RefStatus`, `parse_form_key`, `is_runtime_form_id`, `format_form_key`).
- Produces (bodies for names declared in Task 27): `pv::ResolveForm`, `pv::ToVariable` (Bool, Int, Float, String, `None` as a typed None, form arguments, arrays of Bool/Int/Float/String; arrays of objects are rejected `unsupported`), `pv::FromVariable` (everything except aliases), `pv::FloatToJson` (unchanged from Task 27), `sig::IsA`, `sig::FindProperty` (returns the `PropertyTypeInfo`), and the file-local helpers `FindBound`, `BindObject`, `Describe`, `AliasObjectToJson` (stub) that Task 29 edits.

Rules this task implements:
- **Missing is `not_found`; different is `stale_reference`** (spec 4.4): a form id that no longer exists, or a deleted form, is `not_found` for plugin and runtime forms alike. `stale_reference` is only for a runtime (`0xFF`) form that still exists but whose base form or type differs from the fingerprint.
- **Arrays:** arrays of Bool/Int/Float/String are sent and returned. An **argument** holds at most 128 elements: a longer one is `type_mismatch` naming the limit, refused by core while it parses the request (`value_from_json`, before dispatch); the plugin's own check before `CreateArray` is only a second line of defence and gives the same code. **Results are not capped**: `FromVariable` does not go through core's codec, and SKSE or PapyrusUtil natives can return longer arrays (for example `GetContainerForms` on a player with more than 128 item types), which are passed through. Sending an array of objects is `unsupported` in v1. For M2 (recorded, not built here): S7h showed an array made with `CreateArray(TypeInfo(array_raw & ~1), n)`, where `array_raw` is the declared parameter's raw type, is accepted as a `Form[]` argument (round-trip size 4), but only with unfilled (None) elements was it measured; an array built from a copied `type_info()` (0) and an SKSE-returned array passed back are refused. Reading an object array result is supported; the element kind comes from the items (`Form` for an empty one), never from `arr->type_info()`.
- **Element types come from the declared type, never from `Array::type_info()`.** Arrays returned by SKSE natives (`Utility.CreateFormArray`, `ResizeFormArray`) report element type 0 (None) in `type_info()` (S7h). `ElementType` reads the declared parameter type; `FromVariable` reads the result Variable's own array type (`GetType()`); neither uses `Array::type_info()`.
- **Never call `TypeInfo::GetTypeInfo()` on an object-array type.** The pinned CommonLib (39f9d07) masks the raw value with `~kObjectArray` (~0xB), which also clears bit 3 of the class pointer; `ObjectTypeInfo` pointers are only 8-byte aligned, so it returns a pointer 8 bytes off. In S7 it read a garbage name once and crashed the game the next launch (AV in strlen); DevBench's `describe` crashed the same way. For an object array the class pointer is `reinterpret_cast<ObjectTypeInfo*>(raw & ~1)`; call `GetTypeInfo()` only when `IsObject()` is true (a single object, not an array); `TypeInfo::TypeAsString()` calls `GetTypeInfo()` for an object array too, so do not use it either.
- **`None` arguments** become a typed None, `Variable(declared type)`: the declared object or array type with no value. Never `SetObject(nullptr, ...)`, which asserts a non-null object in CommonLib.
- **Object results decode form-first; alias detection runs only when the form check fails** (`ObjectToJson`). An alias handle carries its quest's FormID in the low 32 bits (alias 0's handle equals the quest's FormID exactly, S7g), but the form check is `LookupByID(low 32)` **and** `HandleIsType(form type, handle)`, and in every S7 run that check did not select a form for an alias handle (`naive_form_check_selects_a_form=false`). Alias-first decoding is therefore not shown necessary. The converse is unmeasured: whether `HandleIsType(BGSRefAlias/BGSLocAlias VMTYPEID, <a Quest's own handle>)` is false. `ObjectToJson` logs it once, the first time a Quest result is decoded (`values: quest handle ... is_ref_alias=... is_loc_alias=...`), and Task 29's `test_quest_result_is_a_quest_not_an_alias` records it. Form-first is correct whatever that line says; alias-first would be safe only if both are false. Keep form-first unless the owner rules otherwise. Unmeasured edge (R52): S4 found alias handles are `(aliasID << 32) | questFormID`; if a form's handle carries its VM type id in the same high bits, an alias whose id equals the Quest type id would pass the form check and decode as its quest. Log the high 32 bits of a decoded Quest handle in the same one-time line so Task 29 can tell; if they equal the Quest type id, record the collision (aliases with that id on a quest return the quest) in the README limits. Alias results are recognised here and converted in Task 29 (until then `unsupported`).
- A parameter typed as a script (not a native class) requires that script to be attached to the object (`FindBoundObject`); otherwise `type_mismatch` naming the parameter.

- [ ] **Step 1: Write the live tests first**

In `livetest/test_m1_calls.py`, delete `test_object_results_are_unsupported_until_task_28`, add `from skython.values import Form` to the imports, and append:
```python
def test_static_call_returns_form(game):
    p = game.call("Game", "GetPlayer")
    assert isinstance(p, Form) and p.form_id == PLAYER_ID and p.type == "Actor"


def test_form_argument_round_trip(game):
    sword = game.call("Game", "GetFormFromFile", 0x012EB7, "Skyrim.esm")
    assert sword.form_key == IRON_SWORD and sword.type == "Weapon"
    # A Form parameter given a Weapon. False is also what a None argument would give, so this alone does not prove the
    # form arrived: Task 29's GetItemCount/AddItem/RemoveItem round trip does.
    assert game.call("Game", "IsObjectFavorited", sword) is False


def test_form_of_the_wrong_class_is_rejected_before_dispatch(game):
    # BindObject's reject path: an ObjectReference parameter given a base Weapon.
    sword = game.call("Game", "GetFormFromFile", 0x012EB7, "Skyrim.esm")
    with pytest.raises(errors.TypeMismatch, match="argument 0 of Debug.SendAnimationEvent"):  # natives: param1..N (S7a)
        game.call("Debug", "SendAnimationEvent", sword, "x")


def test_none_for_an_object_parameter(game):
    assert game.call("Game", "IsObjectFavorited", None) is False  # a typed None Form


def test_none_for_an_array_parameter(game):
    r = game.call("Utility", "ResizeIntArray", None, 2, 0)  # a typed None Int[]
    assert r is None or isinstance(r, list)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the VM and the game carry on


def test_missing_forms_are_not_found_not_stale(game):
    for ref in (Form(form_key="Skyrim.esm|0xFFFFF0"), Form(form_key="NotAPlugin.esp|0x000800"),
                Form(form_id=0xFF123456)):  # the last is a runtime id that names nothing
        with pytest.raises(errors.NotFound):
            game.call("Game", "IsObjectFavorited", ref)


def test_primitive_arrays_round_trip(game):
    assert game.call("Utility", "CreateIntArray", 3, 7) == [7, 7, 7]
    assert game.call("Utility", "ResizeIntArray", [1, 2, 3], 5, 0) == [1, 2, 3, 0, 0]
    assert game.call("Utility", "CreateFloatArray", 2, 1.5) == [1.5, 1.5]


def test_array_wire_shape(session):
    r = raw_call(session, "Utility", "CreateIntArray", [3, 7])
    assert r["result"] == {"array": "Int", "items": [7, 7, 7]}


def test_array_argument_with_wrong_element_type(session):
    r = raw_call(session, "Utility", "ResizeIntArray", [{"array": "Int", "items": [1, "two"]}, 5, 0])
    assert r["ok"] is False and r["error"]["code"] in ("type_mismatch", "bad_request")


def test_array_argument_longer_than_128_is_type_mismatch(session):
    # Sent raw: the Python client refuses it before it leaves the process. Core refuses it while parsing the request.
    r = raw_call(session, "Utility", "ResizeIntArray", [{"array": "Int", "items": list(range(129))}, 5, 0])
    assert r["ok"] is False and r["error"]["code"] == "type_mismatch" and "128" in r["error"]["message"], r
```
(`Game.GetFormFromFile`, `Game.IsObjectFavorited`, `Utility.CreateFloatArray`, `Debug.SendAnimationEvent(ObjectReference arRef, string asEventName)` are in vanilla `Game.psc`/`Utility.psc`/`Debug.psc`; if this runtime's scripts lack one, substitute a function of the same shape and record it in `spikes/RESULTS.md`. A `Form` built locally is sent as given; `game.form(...)`, which asks the plugin to resolve it, arrives with `form.resolve` in Task 29.)

- [ ] **Step 2: Signatures: `IsA` and `FindProperty`**

Append to `plugin/src/Signatures.cpp` inside `namespace spy::sig` (they use `TypeOf` and `IEquals` already defined there):
```cpp
bool IsA(const std::string& script, const std::string& ancestor)
{
    auto info = TypeOf(script);
    for (auto* t = info.get(); t; t = t->GetParent())
        if (IEquals(t->GetName(), ancestor)) return true;
    return false;
}

RE::BSScript::PropertyTypeInfo FindProperty(const std::string& script, const std::string& property)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::not_found, "no script named " + script + " is loaded");
    for (auto* t = info.get(); t; t = t->GetParent())
        for (std::uint32_t i = 0; i < t->GetNumProperties(); ++i)
            if (IEquals(t->GetPropertyIter()[i].name.c_str(), property)) return t->GetPropertyIter()[i].info;  // a copy
    throw ProtocolError(ErrorCode::not_found, "script " + script + " has no property " + property);
}
```
`GetNumProperties`, `GetPropertyIter()[i].name` and `.info` (a `PropertyTypeInfo`: `type`, `getFunction`, `setFunction`, `autoVarIndex`) are verified at the pin (`API-VERIFICATION.md`); use S7's recorded form if it differs.

- [ ] **Step 3: Papyrus values**

Replace `plugin/src/PapyrusValues.cpp` with (everything from Task 27 is kept, plus Task 27's `SourceFile()` fallback from the Files note above, which this listing predates; added: `ResolveForm`, `FindBound`, `BindObject`, arrays, object results, alias detection):
```cpp
#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <cstdint>
#include <format>
#include <mutex>

// RE/Skyrim.h pulls in windows.h, whose GetObject macro (-> GetObjectA) breaks Variable::GetObject() (compile error C2039).
// Every plugin .cpp that calls GetObject() needs this line after its includes; Task 29's Ops.cpp edits and Task 30 too.
#undef GetObject

namespace spy::pv {
namespace {
using RE::BSScript::TypeInfo;
using RE::BSScript::Variable;
using Raw = TypeInfo::RawType;

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

std::string KeyOrId(const RE::TESForm* form)
{
    const auto id = form->GetFormID();
    if (!is_runtime_form_id(id))
        if (const auto* file = form->GetFile(0))
            return format_form_key({std::string(file->GetFilename()), form->GetLocalFormID()});
    return std::to_string(id);
}

Fingerprint LiveFingerprint(const RE::TESForm* form)
{
    const RE::TESForm* base = form;
    if (const auto* ref = form->As<RE::TESObjectREFR>())
        if (const auto* b = ref->GetBaseObject()) base = b;
    return {KeyOrId(base), ScriptClassOf(form)};
}

std::string Describe(const FormRef& r)
{
    return r.form_key ? *r.form_key : std::format("0x{:08X}", r.form_id.value_or(0));
}

// Element type of an array of Bool/Int/Float/String. Spelled out, not computed from enum arithmetic. `arrayType` is
// always the DECLARED parameter type, never Array::type_info() (0 on arrays from SKSE natives, S7h).
TypeInfo ElementType(const TypeInfo& arrayType)
{
    switch (arrayType.GetRawType()) {
    case Raw::kIntArray: return TypeInfo(Raw::kInt);
    case Raw::kFloatArray: return TypeInfo(Raw::kFloat);
    case Raw::kBoolArray: return TypeInfo(Raw::kBool);
    case Raw::kStringArray: return TypeInfo(Raw::kString);
    default: throw ProtocolError(ErrorCode::unsupported, "arrays of this element type are not supported");
    }
}

// The script object of type `script` attached to the object with this handle, if any. The body is exactly
// the call spike S7 (Task 9) proved. The pinned header declares exactly three parameters (verified, API-VERIFICATION.md):
//   bool FindBoundObject(VMHandle, const char* className, BSTSmartPointer<Object>& out) const
bool FindBound(RE::VMHandle handle, const std::string& script, RE::BSTSmartPointer<RE::BSScript::Object>& out)
{
    return VM()->FindBoundObject(handle, script.c_str(), out) && out;
}

void BindObject(const void* ptr, RE::VMTypeID type, const std::string& nativeClass, const std::string& desc,
    const TypeInfo& declared, const std::string& param, Variable& out)
{
    const auto* cls = declared.GetTypeInfo();  // a single object type here (IsObject), never an object array
    const std::string want = cls ? cls->GetName() : "";
    if (want.empty() || sig::IsA(nativeClass, want)) {
        RE::BSScript::PackHandle(&out, ptr, type);
        return;
    }
    // The parameter's type is a script (for example a quest script): it must be attached to this object.
    auto* policy = VM()->GetObjectHandlePolicy();
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (FindBound(policy->GetHandleForObject(type, ptr), want, bound)) {
        // GetRawType() on purpose: for a class type it is the class-typed value (the ObjectTypeInfo pointer), as
        // PackHandle sets it. GetUnmangledRawType() would drop the class and pass a plain kObject (API-VERIFICATION.md).
        out.SetObject(bound, declared.GetRawType());
        return;
    }
    throw ProtocolError(ErrorCode::type_mismatch, "parameter " + param + " expects " + want + ", got " + desc + " (a " + nativeClass + ")");
}

// Task 29 replaces this with the real conversion (it needs DescribeAlias).
json AliasObjectToJson(RE::BSScript::IObjectHandlePolicy*, RE::VMHandle)
{
    throw ProtocolError(ErrorCode::unsupported, "the result is an alias, which this build cannot return yet (Task 29)");
}

json ObjectToJson(const RE::BSScript::Object& obj)
{
    auto* policy = VM()->GetObjectHandlePolicy();
    const auto handle = obj.GetHandle();
    // Forms first. An alias handle carries its quest's FormID in the low 32 bits (alias 0: exactly the quest's
    // FormID), but HandleIsType(form type) did not accept an alias handle in any S7 run
    // (naive_form_check_selects_a_form=false), so this check does not mistake an alias for its quest.
    if (auto* form = RE::TESForm::LookupByID(static_cast<RE::FormID>(handle & 0xFFFFFFFF));
        form && policy->HandleIsType(static_cast<RE::VMTypeID>(form->GetFormType()), handle)) {
        if (form->Is(RE::FormType::Quest)) {  // measurement for Task 29: would alias-first misread a Quest's handle?
            static std::once_flag once;
            std::call_once(once, [&] {
                log::Info(std::format("values: quest handle {:016X} is_ref_alias={} is_loc_alias={}", handle,
                    policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle), policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle)));
            });
        }
        return form_ref_to_json(DescribeForm(form));
    }
    // Aliases only when the form check failed (S7f/S7g: HandleIsType(BGSRefAlias) and GetObjectForHandle resolve it).
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle) || policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        return AliasObjectToJson(policy, handle);
    const auto* cls = obj.GetTypeInfo();
    throw ProtocolError(ErrorCode::unsupported, std::string("the result is a ") + (cls ? cls->GetName() : "script") +
        " object, which protocol v1 cannot return (for example ActiveMagicEffect)");
}
}

// Unchanged from Task 27: core's encoder; shortest round-trip text, non-finite -> null (PROTOCOL.md).
json FloatToJson(float f)
{
    Value v;
    v.v = f;
    return value_to_json(v);
}

std::string ScriptClassOf(const RE::TESForm* form)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (VM()->GetScriptObjectType(static_cast<RE::VMTypeID>(form->GetFormType()), info) && info) return info->GetName();
    return "Form";
}

RE::TESForm* ResolveForm(const FormRef& r)
{
    RE::TESForm* form = nullptr;
    const bool runtime = !r.form_key && r.form_id && is_runtime_form_id(*r.form_id);
    if (r.form_key) {
        const auto k = parse_form_key(*r.form_key).value();  // validated when the request was parsed
        form = RE::TESDataHandler::GetSingleton()->LookupForm(k.local_id, k.plugin);
        if (!form) throw ProtocolError(ErrorCode::not_found, "no form " + *r.form_key + " (is " + k.plugin + " loaded?)");
    } else {
        form = RE::TESForm::LookupByID(*r.form_id);
        // Missing is not_found, also for a runtime id (spec 4.4). stale_reference is only for "exists but is something else".
        if (!form) throw ProtocolError(ErrorCode::not_found, "no form " + Describe(r) + (runtime ? " (the runtime reference no longer exists)" : ""));
    }
    if (form->IsDeleted()) throw ProtocolError(ErrorCode::not_found, "form " + Describe(r) + " is deleted");
    if (runtime && r.fingerprint && check_fingerprint(r, LiveFingerprint(form)) == RefStatus::stale)
        throw ProtocolError(ErrorCode::stale_reference, "form " + Describe(r) + " is now a different object (expected base " +
            r.fingerprint->base + ", " + r.fingerprint->type + ")");
    return form;
}

FormRef DescribeForm(const RE::TESForm* form)
{
    FormRef r;
    r.form_id = form->GetFormID();
    r.type = ScriptClassOf(form);
    r.epoch = life::Epoch();
    if (is_runtime_form_id(*r.form_id)) r.fingerprint = LiveFingerprint(form);
    else if (form->GetFile(0)) r.form_key = KeyOrId(form);
    return r;
}

std::string TypeName(const TypeInfo& t)
{
    // Never GetTypeInfo() on an object array: the pinned CommonLib returns a pointer 8 bytes off (crashed the game in
    // S7). The class pointer of an object array is raw & ~1.
    if (t.IsObjectArray()) {
        const auto* cls = reinterpret_cast<const RE::BSScript::ObjectTypeInfo*>(static_cast<std::uintptr_t>(t.GetRawType()) & ~std::uintptr_t{1});
        return std::string(cls ? cls->GetName() : "Object") + "[]";
    }
    if (t.IsObject() && t.GetTypeInfo()) return t.GetTypeInfo()->GetName();
    switch (t.GetRawType()) {
    case Raw::kInt: return "Int";
    case Raw::kFloat: return "Float";
    case Raw::kBool: return "Bool";
    case Raw::kString: return "String";
    case Raw::kIntArray: return "Int[]";
    case Raw::kFloatArray: return "Float[]";
    case Raw::kBoolArray: return "Bool[]";
    case Raw::kStringArray: return "String[]";
    default: return t.IsArray() ? "Object[]" : "None";
    }
}

bool NeedsMainThread(const Variable& v)
{
    const auto t = v.GetType();
    if (t.IsObjectArray()) return true;  // conservative: any array of objects
    return t.IsObject() && !v.IsNoneObject();
}

void ToVariable(const Value& v, const TypeInfo& t, const std::string& param, Variable& out)
{
    auto mismatch = [&]() -> void {
        throw ProtocolError(ErrorCode::type_mismatch,
            "parameter " + param + " expects " + TypeName(t) + ", got " + std::string(value_kind(v)));
    };
    const bool isNull = std::holds_alternative<std::monostate>(v.v);
    // None for an object or array parameter: a typed None, the declared type with no value. (SetObject(nullptr, ...)
    // asserts a non-null object in CommonLib; SetNone() would drop the declared type.)
    if (isNull && (t.IsArray() || t.IsObject())) {
        out = Variable(t);
        return;
    }
    if (t.IsArray()) {
        if (t.IsObjectArray())
            throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " is an array of objects; protocol v1 cannot send those "
                "(pass the objects one at a time, or call a Papyrus helper that takes them individually)");
        const auto* a = std::get_if<ArrayValue>(&v.v);
        if (!a) mismatch();
        const auto elem = ElementType(t);
        // Second line of defence only: core already refused a longer argument while parsing the request (type_mismatch,
        // kMaxArrayItems). Papyrus arrays hold at most 128 elements (Array::MAX_SIZE is private, so it is repeated here).
        constexpr std::size_t kMaxPapyrusArray = 128;
        if (a->items.size() > kMaxPapyrusArray)
            throw ProtocolError(ErrorCode::type_mismatch, std::format("parameter {}: a Papyrus array holds at most {} items, got {}", param, kMaxPapyrusArray, a->items.size()));
        RE::BSTSmartPointer<RE::BSScript::Array> arr;
        if (!VM()->CreateArray(elem, static_cast<std::uint32_t>(a->items.size()), arr) || !arr)
            throw ProtocolError(ErrorCode::vm_error, "could not create an array for parameter " + param);
        for (std::size_t i = 0; i < a->items.size(); ++i)
            ToVariable(a->items[i], elem, std::format("{}[{}]", param, i), (*arr)[static_cast<std::uint32_t>(i)]);
        out.SetArray(arr);
        return;
    }
    if (t.IsObject()) {
        if (const auto* f = std::get_if<FormRef>(&v.v)) {
            auto* form = ResolveForm(*f);
            BindObject(form, static_cast<RE::VMTypeID>(form->GetFormType()), ScriptClassOf(form), Describe(*f), t, param, out);
            return;
        }
        if (std::holds_alternative<AliasRef>(v.v))
            throw ProtocolError(ErrorCode::unsupported, "parameter " + param + ": alias arguments arrive with Task 29");
        mismatch();
    }
    switch (t.GetRawType()) {
    case Raw::kInt:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetSInt(*i);
        break;
    case Raw::kFloat:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetFloat(static_cast<float>(*i));
        if (const auto* f = std::get_if<float>(&v.v)) return out.SetFloat(*f);
        break;
    case Raw::kBool:
        if (const auto* b = std::get_if<bool>(&v.v)) return out.SetBool(*b);
        break;
    case Raw::kString:
        if (const auto* s = std::get_if<std::string>(&v.v)) return out.SetString(*s);
        break;
    default:
        throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " has a type protocol v1 cannot send: " + TypeName(t));
    }
    mismatch();
}

json FromVariable(const Variable& v)
{
    const auto t = v.GetType();
    if (t.GetRawType() == Raw::kNone || v.IsNoneObject()) return nullptr;
    if (v.IsBool()) return v.GetBool();
    if (v.IsInt()) return v.GetSInt();
    if (v.IsFloat()) return FloatToJson(v.GetFloat());
    if (v.IsString()) return std::string(v.GetString());
    if (t.IsArray()) {
        auto arr = v.GetArray();
        if (!arr) return nullptr;
        // Results are not capped at 128 (index Contracts): SKSE and PapyrusUtil natives can return longer arrays.
        // Verify whether BSScript::Array exposes a lock; if so hold it while copying (a Papyrus thread may resize it).
        json items = json::array();
        for (std::uint32_t i = 0; i < arr->size(); ++i) items.push_back(FromVariable((*arr)[i]));
        std::string elem;
        switch (t.GetRawType()) {  // the element kind comes from the Variable's own array type, never arr->type_info() (0 from SKSE natives, S7h)
        case Raw::kBoolArray: elem = "Bool"; break;
        case Raw::kIntArray: elem = "Int"; break;
        case Raw::kFloatArray: elem = "Float"; break;
        case Raw::kStringArray: elem = "String"; break;
        default:  // an array of objects: look at the items; an empty one is reported as Form
            elem = "Form";
            for (const auto& item : items)
                if (item.is_object()) {
                    elem = item.value("kind", "") == "alias" ? "Alias" : "Form";
                    break;
                }
        }
        return json{{"array", elem}, {"items", items}};
    }
    if (t.IsObject()) {
        auto obj = v.GetObject();
        if (!obj) return nullptr;
        return ObjectToJson(*obj);
    }
    throw ProtocolError(ErrorCode::unsupported, "the result has a type protocol v1 cannot return");
}
}
```
`ResolveAlias`, `DescribeAlias` and `ResolveTarget` are still undefined (Task 29). `LookupByEditorID` is not used until `form.resolve` in Task 29.

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
tools/box/box_console.sh "load spikebase"     # stands in for Continue; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_calls.py -q -rs
tools/box/box_quit.sh     # NOT_CLEAN with NO_NEW_CRASH_LOG: tools/box/box_stop.sh (Task 1b Known issue)
```

Expected: `test_m1_mainmenu.py` passes (3); `test_m1_calls.py` passes in full. Any skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure. `test_form_argument_round_trip` shows a form argument is accepted for a native `Form` parameter, and `test_form_of_the_wrong_class_is_rejected_before_dispatch` that `BindObject` refuses the wrong class; the proof that the form itself arrives (not a None) is Task 29's `GetItemCount` round trip, which carries the STOP for the form-argument route. If any form-argument test fails with `vm_error` or the game crashes, STOP: the `PackHandle` route S7 recorded differs from the code here. If a `None` test crashes the game or fails with `vm_error`, STOP: the typed None (`Variable(t)`) is not what the VM accepts; record what happened. If `test_primitive_arrays_round_trip` fails on `CreateFloatArray`, check `Utility.psc` and substitute.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): Papyrus value conversion, form arguments and signature checks"
```
