# Task 28: Papyrus value conversion and signatures

Adapted from: branch `plan-out-of-process`, task 17b (`task-17b-papyrus-value-conversion-and-signatures.md`). The C++ is unchanged apart from task numbers (it compiled and linked on the build box at the pinned CommonLibSSE-NG, with the `API-VERIFICATION.md` fixes applied). The live tests now go through the dev bridge.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), section 4.4. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 27 is done **and spike S7 has passed** (Task 9; `spikes/RESULTS.md` has an S7 PASS row recording the exact `FindBoundObject`, `PackHandle`, `GetParam` and `ObjectTypeInfo` calls that work on the pinned checkout). **If S4 (Task 6) is FAIL, STOP and ask the owner** before going on to Task 29.
>
> **Runs on:** the Windows build box plus the game; live tests run on Linux through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** S7 is missing or FAIL; the form-argument route S7 recorded differs from the code below by more than a rename; a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s.
>
> **Verified against CommonLibSSE-NG 39f9d07 (see `API-VERIFICATION.md`; this code compiled and linked on the build box).** `FindBoundObject(VMHandle, const char*, BSTSmartPointer<Object>&) const` (3 parameters); `Array::MAX_SIZE` is 128 but private (hence the local constant); `GetRawType()` exists on `TypeInfo`, not on `Variable`; `#undef GetObject` is needed after the includes (windows.h macro). Still re-check against S7's record: `GetObjectHandlePolicy`, `GetHandleForObject`, `HandleIsType`, `GetObjectForHandle`, `EmptyHandle`; `RE::BSScript::PackHandle`; `Variable::SetObject(nullptr, rawType)` / `SetNone`, `SetArray`, `GetArray`, `GetObject`; `Object::GetHandle`; `VirtualMachine::CreateArray(TypeInfo, size, BSTSmartPointer<Array>&)`; `Array::size`, `operator[]`; `TESDataHandler::LookupForm(localId, pluginName)`; `TESForm::LookupByID`, `IsDeleted`, `GetFile(0)`, `GetLocalFormID`.

**Files:**
- Modify: `plugin/src/Signatures.cpp` (add `IsA`, `FindProperty`)
- Modify: `plugin/src/PapyrusValues.cpp` (full replacement below)
- Modify: `livetest/test_m1_calls.py` (replace one test, append others)

**Interfaces:**
- Consumes: Task 27 (all headers, `ops::Run`, `Callback`, `pv::NeedsMainThread`, `life::Epoch()`), spike S7 (Task 9, verified calls), core Tasks 14-15 (`Value`, `FormRef`, `ArrayValue`, `form_ref_to_json`, `check_fingerprint`, `RefStatus`, `parse_form_key`, `is_runtime_form_id`, `format_form_key`).
- Produces (bodies for names declared in Task 27): `pv::ResolveForm`, `pv::ToVariable` (Bool, Int, Float, String, `None`, form arguments, arrays of Bool/Int/Float/String; arrays of objects are rejected `unsupported`), `pv::FromVariable` (everything except aliases), `sig::IsA`, `sig::FindProperty`, and the file-local helpers `FindBound`, `BindObject`, `Describe`, `AliasObjectToJson` (stub) that Task 29 edits.

Rules this task implements:
- **Missing is `not_found`; different is `stale_reference`** (spec 4.4): a form id that no longer exists, or a deleted form, is `not_found` for plugin and runtime forms alike. `stale_reference` is only for a runtime (`0xFF`) form that still exists but whose base form or type differs from the fingerprint.
- **Arrays:** arrays of Bool/Int/Float/String are sent and returned; at most 128 elements (`unsupported` beyond that, before `CreateArray`). Sending an array of objects is `unsupported` in v1 (building one needs the element's script `TypeInfo`, which no spike verifies). Reading an object array result is supported; the element kind comes from the items (`Form` for an empty one), never from `arr->type_info()`.
- **Alias handles are tested before form handles** in `ObjectToJson`: an alias handle carries its quest's FormID in the low 32 bits, so a form-first decode would answer `Quest`. Alias results are recognised here and converted in Task 29 (until then `unsupported`).
- A parameter typed as a script (not a native class) requires that script to be attached to the object (`FindBoundObject`); otherwise `type_mismatch` naming the parameter.

- [ ] **Step 1: Write the live tests first**

In `livetest/test_m1_calls.py`, delete `test_object_results_are_unsupported_until_task_28`, add `from skyrim.values import Form` to the imports, and append:
```python
def test_static_call_returns_form(game):
    p = game.call("Game", "GetPlayer")
    assert isinstance(p, Form) and p.form_id == PLAYER_ID and p.type == "Actor"


def test_form_argument_round_trip(game):
    sword = game.call("Game", "GetFormFromFile", 0x012EB7, "Skyrim.esm")
    assert sword.form_key == IRON_SWORD and sword.type == "Weapon"
    assert game.call("Game", "IsObjectFavorited", sword) is False  # a Form parameter given a Weapon reference


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


def test_array_longer_than_128_is_unsupported(session):
    r = raw_call(session, "Utility", "ResizeIntArray", [{"array": "Int", "items": list(range(129))}, 5, 0])
    assert r["ok"] is False and r["error"]["code"] in ("unsupported", "bad_request")
```
(`Game.GetFormFromFile`, `Game.IsObjectFavorited`, `Utility.CreateFloatArray` are in vanilla `Game.psc`/`Utility.psc`; if this runtime's scripts lack one, substitute a function of the same shape and record it in `spikes/RESULTS.md`. A `Form` built locally is sent as given; `game.form(...)`, which asks the plugin to resolve it, arrives with `form.resolve` in Task 29. Core may already refuse a 129-element array while parsing the request, hence `bad_request` is accepted there.)

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

RE::BSScript::TypeInfo FindProperty(const std::string& script, const std::string& property)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::not_found, "no script named " + script + " is loaded");
    for (auto* t = info.get(); t; t = t->GetParent())
        for (std::uint32_t i = 0; i < t->GetNumProperties(); ++i)
            if (IEquals(t->GetPropertyIter()[i].name.c_str(), property)) return t->GetPropertyIter()[i].info.type;
    throw ProtocolError(ErrorCode::not_found, "script " + script + " has no property " + property);
}
```
`GetNumProperties`, `GetPropertyIter()[i].name` and `.info.type` are verified at the pin (`API-VERIFICATION.md`); use S7's recorded form if it differs.

- [ ] **Step 3: Papyrus values**

Replace `plugin/src/PapyrusValues.cpp` with (everything from Task 27 is kept; added: `ResolveForm`, `FindBound`, `BindObject`, arrays, object results, alias detection):
```cpp
#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <cmath>
#include <cstdlib>
#include <format>

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

// A Papyrus Float is a 32-bit float. Widening it to double prints 0.1 as 0.10000000149011612; print the
// shortest text that round-trips the float and parse that instead. NaN and infinity have no JSON form.
json FloatToJson(float f)
{
    if (!std::isfinite(f)) throw ProtocolError(ErrorCode::unsupported, "the result is a non-finite Float, which JSON cannot carry");
    return std::strtod(std::format("{}", f).c_str(), nullptr);
}

// Element type of an array of Bool/Int/Float/String. Spelled out, not computed from enum arithmetic.
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
    const auto* cls = declared.GetTypeInfo();
    const std::string want = cls ? cls->GetName() : "";
    if (want.empty() || sig::IsA(nativeClass, want)) {
        RE::BSScript::PackHandle(&out, ptr, type);
        return;
    }
    // The parameter's type is a script (for example a quest script): it must be attached to this object.
    auto* policy = VM()->GetObjectHandlePolicy();
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (FindBound(policy->GetHandleForObject(type, ptr), want, bound)) {
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
    // Aliases first. An alias handle carries its quest's FormID in the low 32 bits, so the form decode below
    // would find the Quest and the caller would be told it got a quest.
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle) || policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        return AliasObjectToJson(policy, handle);
    if (auto* form = RE::TESForm::LookupByID(static_cast<RE::FormID>(handle & 0xFFFFFFFF));
        form && policy->HandleIsType(static_cast<RE::VMTypeID>(form->GetFormType()), handle))
        return form_ref_to_json(DescribeForm(form));
    const auto* cls = obj.GetTypeInfo();
    throw ProtocolError(ErrorCode::unsupported, std::string("the result is a ") + (cls ? cls->GetName() : "script") +
        " object, which protocol v1 cannot return (for example ActiveMagicEffect)");
}
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
    if (t.IsArray()) {
        if (isNull) {
            out.SetNone();  // verify (S7, Task 9): or SetArray(nullptr)
            return;
        }
        if (t.IsObjectArray())
            throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " is an array of objects; protocol v1 cannot send those "
                "(pass the objects one at a time, or call a Papyrus helper that takes them individually)");
        const auto* a = std::get_if<ArrayValue>(&v.v);
        if (!a) mismatch();
        const auto elem = ElementType(t);
        // Papyrus arrays hold at most 128 elements (Array::MAX_SIZE, which is private, so it is repeated here); reject longer ones instead of asking the VM.
        constexpr std::size_t kMaxPapyrusArray = 128;
        if (a->items.size() > kMaxPapyrusArray)
            throw ProtocolError(ErrorCode::unsupported, std::format("parameter {}: Papyrus arrays hold at most {} elements, got {}", param, kMaxPapyrusArray, a->items.size()));
        RE::BSTSmartPointer<RE::BSScript::Array> arr;
        if (!VM()->CreateArray(elem, static_cast<std::uint32_t>(a->items.size()), arr) || !arr)
            throw ProtocolError(ErrorCode::vm_error, "could not create an array for parameter " + param);
        for (std::size_t i = 0; i < a->items.size(); ++i)
            ToVariable(a->items[i], elem, std::format("{}[{}]", param, i), (*arr)[static_cast<std::uint32_t>(i)]);
        out.SetArray(arr);
        return;
    }
    if (t.IsObject()) {
        if (isNull) {
            out.SetObject(nullptr, t.GetRawType());  // verify (S7, Task 9): a null object of the declared type
            return;
        }
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
        // Verify whether BSScript::Array exposes a lock; if so hold it while copying (a Papyrus thread may resize it).
        json items = json::array();
        for (std::uint32_t i = 0; i < arr->size(); ++i) items.push_back(FromVariable((*arr)[i]));
        std::string elem;
        switch (t.GetRawType()) {  // the element kind comes from the declared array type, never from arr->type_info()
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

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --package
tools/launch_game.sh
source tools/env.sh
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "Main Menu" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
uv run --project python pytest livetest/test_m1_mainmenu.py -q
# Manual: press Continue. Then:
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "interactive = true" 300
uv run --project python pytest livetest/test_m1_calls.py -q
```
(Use Task 25's exact log phrases for the main menu and the interactive signal if they differ.)

Expected: `test_m1_mainmenu.py` passes (3); `test_m1_calls.py` passes in full. `test_form_argument_round_trip` is the first live proof that a form argument reaches a native `Form` parameter. If it fails with `vm_error` or the game crashes, STOP: the `PackHandle` route S7 recorded differs from the code here. If `test_primitive_arrays_round_trip` fails on `CreateFloatArray`, check `Utility.psc` and substitute.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): Papyrus value conversion, form arguments and signature checks"
```
