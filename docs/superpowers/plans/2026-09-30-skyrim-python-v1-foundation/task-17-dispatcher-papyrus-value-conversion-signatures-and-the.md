# Task 17: Dispatcher, Papyrus value conversion, signatures, and the first ops

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `plugin/src/Dispatch.h`, `plugin/src/Dispatch.cpp`, `plugin/src/PapyrusValues.h`, `plugin/src/PapyrusValues.cpp`, `plugin/src/Signatures.h`, `plugin/src/Signatures.cpp`, `plugin/src/Ops.h`, `plugin/src/Ops.cpp`
- Modify: `plugin/src/main.cpp` (three edits, shown below)
- Create: `livetest/test_m1_calls.py`, `livetest/test_m1_mainmenu.py`

**Interfaces:**
- Consumes: Tasks 10–12 (`Value`, `FormRef`, `AliasRef`, `ArrayValue`, `parse_call_args`, `make_ok`, `make_error`, `InflightLimiter`, `Outbox`, `form_ref_to_json`, `alias_ref_to_json`, `parse_form_key`, `format_form_key`, `is_runtime_form_id`, `check_fingerprint`), Task 15 (`spy::life`), Task 16 (`spy::Frame`, `spy::net::Callbacks`).
- Consumes from spikes: S2 (VM owns the `IFunctionArguments` passed to a dispatch; which thread runs the callback), S4 (alias handles work with `DispatchMethodCall`). If S2 showed the VM does *not* free the arguments, `DispatchStatic`/`DispatchMethod` in `Ops.cpp` must `delete` the `DynArgs` after the dispatch call returns.
- Produces:
  - `spy::dispatch::Init(Outbox&, const Config&)`, `OnRequest(ConnId, Request)`, `OnDisconnect(ConnId)`, `OnTick()`
  - `spy::ops::OpResult { bool ok; json result; ErrorCode code; std::string message; json detail; static Ok(json); static Err(ErrorCode, std::string, json = object) }`, `using Done = std::function<void(OpResult)>`, `using Cancelled = std::function<bool()>`, `void spy::ops::Run(const std::string& op, const json& args, Done, Cancelled)` (main thread), `json spy::ops::StartingState()`
  - `spy::pv::ResolveForm(const FormRef&) -> RE::TESForm*`, `ResolveAlias(const AliasRef&) -> RE::BGSBaseAlias*`, `DescribeForm(const RE::TESForm*) -> FormRef`, `DescribeAlias(const RE::BGSBaseAlias*) -> AliasRef`, `ScriptClassOf(const RE::TESForm*) -> std::string`, `struct Target { RE::BSTSmartPointer<RE::BSScript::Object> object; std::string desc; }`, `ResolveTarget(const Value& object, const std::string& script) -> Target`, `ToVariable(const Value&, const RE::BSScript::TypeInfo&, const std::string& param, RE::BSScript::Variable&)`, `FromVariable(const RE::BSScript::Variable&) -> json`, `TypeName(const RE::BSScript::TypeInfo&) -> std::string`
  - `spy::sig::IsA(const std::string& script, const std::string& ancestor) -> bool`, `FindFunction(const std::string& script, const std::string& function, bool member) -> RE::BSTSmartPointer<RE::BSScript::IFunction>`, `BindArgs(const RE::BSScript::IFunction&, const std::vector<Value>&, const std::string& qualified) -> std::vector<RE::BSScript::Variable>`, `FindProperty(const std::string& script, const std::string& property) -> RE::BSScript::TypeInfo`, `IEquals(std::string_view, std::string_view) -> bool`

Rules this task implements (spec 4.3–4.5):
- IO thread: cap check (`busy`), readiness check (`not_ready` with `reason`, answered immediately, never queued), then exactly one `AddTask` per request. `game.state` is answered on the IO thread before `kDataLoaded`, and on the main thread afterwards.
- Main thread: re-check readiness, resolve references, find the function, check every argument against the declared parameter types (the server fills no default values: argument count must match), dispatch.
- VM callback (thread recorded by S2): copy the raw `Variable`; convert it to JSON on the main thread (inline if the callback is already on the main thread, otherwise one more `AddTask`).
- IO thread, every tick: any request past its deadline gets `timeout`; its late result is dropped. A disconnect cancels the connection's queued work and forgets its in-flight count.
- Member functions are looked up in the script's empty state only (state-specific overrides are not considered).

- [ ] **Step 1: Write the live tests first** (they fail until the plugin is rebuilt)

`livetest/test_m1_calls.py`:
```python
"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars:
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
"""
import os
import time

import pytest

from skyrim import errors
from skyrim.values import Form

PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_state_reports_player(game):
    st = game.state()
    assert st["interactive"] is True and st["loading"] is False
    assert st["player"].form_id == PLAYER_ID
    assert st["player"].form_key == "Skyrim.esm|0x000014"
    assert st["player"].type == "Actor"
    assert st["game_days_passed"] > 0


def test_static_call(game):
    for _ in range(20):
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_static_call_returns_form(game):
    p = game.call("Game", "GetPlayer")
    assert p.form_id == PLAYER_ID and p.type == "Actor"


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


def test_latent_function_completes(game):
    t0 = time.monotonic()
    assert game.call("Utility", "Wait", 0.5) is None
    assert time.monotonic() - t0 >= 0.4


def test_resolve_by_key_and_by_id(game):
    sword = game.form(IRON_SWORD)
    assert sword.type == "Weapon" and sword.form_key == IRON_SWORD
    assert game.resolve(form_id=sword.form_id).form_key == IRON_SWORD


def test_array_result_and_argument(game):
    assert game.call("Utility", "CreateIntArray", 3, 7) == [7, 7, 7]
    assert game.call("Utility", "ResizeIntArray", [1, 2, 3], 5, 0) == [1, 2, 3, 0, 0]


def test_unknown_script_and_function(game):
    with pytest.raises(errors.UnknownFunction):
        game.call("NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.UnknownFunction):
        game.call("Utility", "NoSuchFunction")


def test_wrong_argument_type_names_the_parameter(game):
    with pytest.raises(errors.TypeMismatch, match="aiMin"):
        game.call("Utility", "RandomInt", "one", 6)


def test_wrong_argument_count(game):
    with pytest.raises(errors.TypeMismatch, match="takes 2"):
        game.call("Utility", "RandomInt", 1)


def test_global_called_as_member_is_bad_request(game):
    with pytest.raises(errors.BadRequest, match="global"):
        game.call_method(Form(form_id=PLAYER_ID), "Utility", "RandomInt", 1, 6)


def test_method_on_wrong_kind_of_form(game):
    sword = game.form(IRON_SWORD)
    with pytest.raises(errors.TypeMismatch, match="Weapon"):
        game.call_method(sword, "Actor", "GetActorValue", "Health")


def test_missing_form_is_not_found(game):
    with pytest.raises(errors.NotFound):
        game.form("Skyrim.esm|0xFFFFF0")
    with pytest.raises(errors.NotFound):
        game.form("NotAPlugin.esp|0x000800")


def test_alias_method_calls(game):
    quest_id = int(os.environ["SPY_S4_QUEST"], 16)
    alias_id = int(os.environ["SPY_S4_ALIAS"])
    quest = game.resolve(form_id=quest_id)
    alias = game.alias(quest, alias_id)
    assert game.call_method(alias, "Alias", "GetOwningQuest").form_id == quest_id
    ref = game.call_method(alias, "ReferenceAlias", "GetReference")
    assert ref.form_id == int(os.environ["SPY_S4_REF"], 16)
```

`livetest/test_m1_mainmenu.py`:
```python
"""Run with the game at the main menu (launched, no save loaded)."""
import pytest

from skyrim import errors


def test_state_at_main_menu(game):
    st = game.state()
    assert st["data_ready"] is True
    assert st["game_loaded"] is False and st["interactive"] is False
    assert st["player"] is None


def test_calls_are_not_ready_with_reason(game):
    with pytest.raises(errors.NotReady) as ei:
        game.call("Utility", "RandomInt", 1, 6)
    assert ei.value.detail["reason"] == "no_game"
    with pytest.raises(errors.NotReady):
        game.form("Skyrim.esm|0x012EB7")
```

- [ ] **Step 2: Signatures**

`plugin/src/Signatures.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>
#include <string_view>
#include <vector>

namespace spy::sig {
bool IEquals(std::string_view a, std::string_view b);  // Papyrus names are case-insensitive
// True if `script` is `ancestor` or extends it. False if `script` is not a loaded script.
bool IsA(const std::string& script, const std::string& ancestor);
// Throws unknown_function (no such script or function) or bad_request (global vs member mix-up).
RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member);
// Converts arguments to the declared parameter types. Throws type_mismatch naming the parameter.
std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified);
// Declared type of a property on `script` or its parents. Throws not_found.
RE::BSScript::TypeInfo FindProperty(const std::string& script, const std::string& property);
}
```

`plugin/src/Signatures.cpp`:
```cpp
#include "Signatures.h"
#include "PapyrusValues.h"
#include <algorithm>
#include <cctype>
#include <format>

namespace spy::sig {
namespace {
RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> TypeOf(const std::string& script)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) return {};
    return info;
}
}

bool IEquals(std::string_view a, std::string_view b)
{
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
        return std::tolower(static_cast<unsigned char>(x)) == std::tolower(static_cast<unsigned char>(y));
    });
}

bool IsA(const std::string& script, const std::string& ancestor)
{
    auto info = TypeOf(script);
    for (auto* t = info.get(); t; t = t->GetParent())
        if (IEquals(t->GetName(), ancestor)) return true;
    return false;
}

RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::unknown_function, "no script named " + script + " is loaded");
    bool otherKind = false;
    // Global functions belong to the declaring script only; member functions are inherited.
    for (auto* t = info.get(); t; t = member ? t->GetParent() : nullptr) {
        for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
            const auto& f = t->GetMemberFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (member) return f;
                otherKind = true;
            }
        }
        for (std::uint32_t i = 0; i < t->GetNumGlobalFuncs(); ++i) {
            const auto& f = t->GetGlobalFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (!member) return f;
                otherKind = true;
            }
        }
    }
    if (otherKind)
        throw ProtocolError(ErrorCode::bad_request, script + "." + function +
            (member ? " is a global function; use papyrus.call_static" : " is a member function; use papyrus.call_method on an object"));
    throw ProtocolError(ErrorCode::unknown_function, "script " + script + " has no function " + function);
}

std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified)
{
    const auto n = fn.GetParamCount();
    if (args.size() != n)
        throw ProtocolError(ErrorCode::type_mismatch, std::format(
            "{} takes {} argument(s), got {} (the server does not fill in Papyrus default values)", qualified, n, args.size()));
    std::vector<RE::BSScript::Variable> out(n);
    for (std::uint32_t i = 0; i < n; ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        fn.GetParam(i, name, type);
        pv::ToVariable(args[i], type, std::format("{} (argument {} of {})", name.c_str(), i, qualified), out[i]);
    }
    return out;
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
}
```

- [ ] **Step 3: Papyrus values**

`plugin/src/PapyrusValues.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>

// Main thread only: every function here touches forms or the VM.
namespace spy::pv {
RE::TESForm* ResolveForm(const FormRef& ref);            // throws not_found / stale_reference
RE::BGSBaseAlias* ResolveAlias(const AliasRef& ref);     // throws not_found / type_mismatch
FormRef DescribeForm(const RE::TESForm* form);
AliasRef DescribeAlias(const RE::BGSBaseAlias* alias);
std::string ScriptClassOf(const RE::TESForm* form);       // "Actor", "Weapon", ...; "Form" if unknown

struct Target
{
    RE::BSTSmartPointer<RE::BSScript::Object> object;  // bound script object to dispatch on
    std::string desc;
};
// The object a method or property is called on. `script` must be a script attached to it, or its
// native class or an ancestor of that; otherwise type_mismatch. A form no script has touched yet has
// no bound object: one is created and bound for its native class (as CommonLib does for arguments).
Target ResolveTarget(const Value& object, const std::string& script);

void ToVariable(const Value& v, const RE::BSScript::TypeInfo& declared, const std::string& param, RE::BSScript::Variable& out);
json FromVariable(const RE::BSScript::Variable& v);
std::string TypeName(const RE::BSScript::TypeInfo& t);
}
```

`plugin/src/PapyrusValues.cpp`:
```cpp
#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <format>

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

TypeInfo ElementType(const TypeInfo& arrayType)
{
    const auto raw = static_cast<std::uintptr_t>(arrayType.GetRawType());
    if (arrayType.IsObjectArray()) return TypeInfo(static_cast<Raw>(raw & ~std::uintptr_t{1}));
    return TypeInfo(static_cast<Raw>(raw - (static_cast<std::uintptr_t>(Raw::kNoneArray) - static_cast<std::uintptr_t>(Raw::kNone))));
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
    if (VM()->FindBoundObject(policy->GetHandleForObject(type, ptr), want.c_str(), bound) && bound) {
        out.SetObject(bound, declared.GetRawType());
        return;
    }
    throw ProtocolError(ErrorCode::type_mismatch, "parameter " + param + " expects " + want + ", got " + desc + " (a " + nativeClass + ")");
}

json ObjectToJson(const RE::BSScript::Object& obj)
{
    auto* policy = VM()->GetObjectHandlePolicy();
    const auto handle = obj.GetHandle();
    if (auto* form = RE::TESForm::LookupByID(static_cast<RE::FormID>(handle & 0xFFFFFFFF));
        form && policy->HandleIsType(static_cast<RE::VMTypeID>(form->GetFormType()), handle))
        return form_ref_to_json(DescribeForm(form));
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSRefAlias*>(policy->GetObjectForHandle(RE::BGSRefAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
    if (policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        if (auto* a = static_cast<RE::BGSLocAlias*>(policy->GetObjectForHandle(RE::BGSLocAlias::VMTYPEID, handle)))
            return alias_ref_to_json(DescribeAlias(a));
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
        if (!form)
            throw ProtocolError(runtime ? ErrorCode::stale_reference : ErrorCode::not_found,
                "no form " + Describe(r) + (runtime ? " (the runtime reference no longer exists)" : ""));
    }
    if (form->IsDeleted())
        throw ProtocolError(runtime ? ErrorCode::stale_reference : ErrorCode::not_found, "form " + Describe(r) + " is deleted");
    if (runtime && r.fingerprint && check_fingerprint(r, LiveFingerprint(form)) == RefStatus::stale)
        throw ProtocolError(ErrorCode::stale_reference, "form " + Describe(r) + " is now a different object (expected base " +
            r.fingerprint->base + ", " + r.fingerprint->type + ")");
    return form;
}

RE::BGSBaseAlias* ResolveAlias(const AliasRef& a)
{
    auto* quest = ResolveForm(a.quest)->As<RE::TESQuest>();
    if (!quest) throw ProtocolError(ErrorCode::type_mismatch, "alias owner " + Describe(a.quest) + " is not a Quest");
    const auto want = a.alias_type == "ReferenceAlias" ? RE::BGSRefAlias::VMTYPEID : RE::BGSLocAlias::VMTYPEID;
    RE::BSReadLockGuard lock(quest->aliasAccessLock);
    for (auto* alias : quest->aliases) {
        if (!alias || alias->aliasID != static_cast<std::uint32_t>(a.alias_id)) continue;
        if (alias->GetVMTypeID() != want)
            throw ProtocolError(ErrorCode::type_mismatch, std::format("alias {} of {} is not a {}", a.alias_id, Describe(a.quest), a.alias_type));
        return alias;
    }
    throw ProtocolError(ErrorCode::not_found, std::format("quest {} has no alias {}", Describe(a.quest), a.alias_id));
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

AliasRef DescribeAlias(const RE::BGSBaseAlias* alias)
{
    AliasRef a;
    a.quest = DescribeForm(alias->owningQuest);
    a.alias_id = static_cast<std::int32_t>(alias->aliasID);
    a.alias_type = alias->GetVMTypeID() == RE::BGSRefAlias::VMTYPEID ? "ReferenceAlias" : "LocationAlias";
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
        type = alias->GetVMTypeID();
        nativeClass = a->alias_type;
        desc = std::format("alias {} of {}", a->alias_id, Describe(a->quest));
    } else {
        throw ProtocolError(ErrorCode::bad_request, "object must be a form or alias reference");
    }
    const auto handle = policy->GetHandleForObject(type, ptr);
    if (handle == policy->EmptyHandle()) throw ProtocolError(ErrorCode::vm_error, "the VM has no handle for " + desc);
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (vm->FindBoundObject(handle, script.c_str(), bound) && bound) return {bound, desc};
    if (sig::IsA(nativeClass, script)) {
        // Finds the native object bound to this handle, or creates and binds one (cold forms).
        Variable v;
        RE::BSScript::PackHandle(&v, ptr, type);
        if (auto obj = v.GetObject()) return {obj, desc};
        throw ProtocolError(ErrorCode::vm_error, "could not bind a " + nativeClass + " script object to " + desc);
    }
    throw ProtocolError(ErrorCode::type_mismatch, desc + " is a " + nativeClass + " and has no script " + script + " attached");
}

std::string TypeName(const TypeInfo& t)
{
    if (t.IsObjectArray()) return std::string(ElementType(t).GetTypeInfo()->GetName()) + "[]";
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
    default: return "None";
    }
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
            out.SetNone();
            return;
        }
        const auto* a = std::get_if<ArrayValue>(&v.v);
        if (!a) mismatch();
        const auto elem = ElementType(t);
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
            out.SetObject(nullptr, t.GetRawType());
            return;
        }
        if (const auto* f = std::get_if<FormRef>(&v.v)) {
            auto* form = ResolveForm(*f);
            BindObject(form, static_cast<RE::VMTypeID>(form->GetFormType()), ScriptClassOf(form), Describe(*f), t, param, out);
            return;
        }
        if (const auto* a = std::get_if<AliasRef>(&v.v)) {
            auto* alias = ResolveAlias(*a);
            BindObject(alias, alias->GetVMTypeID(), a->alias_type, std::format("alias {}", a->alias_id), t, param, out);
            return;
        }
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
    if (v.IsFloat()) return static_cast<double>(v.GetFloat());
    if (v.IsString()) return std::string(v.GetString());
    if (t.IsArray()) {
        auto arr = v.GetArray();
        if (!arr) return nullptr;
        json items = json::array();
        for (std::uint32_t i = 0; i < arr->size(); ++i) items.push_back(FromVariable((*arr)[i]));
        std::string elem;
        const auto& et = arr->type_info();
        if (et.IsBool()) elem = "Bool";
        else if (et.IsInt()) elem = "Int";
        else if (et.IsFloat()) elem = "Float";
        else if (et.IsString()) elem = "String";
        else {
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

- [ ] **Step 4: Ops**

`plugin/src/Ops.h`:
```cpp
#pragma once
#include "spy/messages.h"
#include <functional>

namespace spy::ops {
struct OpResult
{
    bool ok = true;
    json result;
    ErrorCode code = ErrorCode::internal;
    std::string message;
    json detail = json::object();

    static OpResult Ok(json r)
    {
        OpResult o;
        o.result = std::move(r);
        return o;
    }
    static OpResult Err(ErrorCode c, std::string m, json d = json::object())
    {
        OpResult o;
        o.ok = false;
        o.code = c;
        o.message = std::move(m);
        o.detail = std::move(d);
        return o;
    }
};

using Done = std::function<void(OpResult)>;  // called once per op; from the main thread
using Cancelled = std::function<bool()>;     // true once the request was answered (timeout or disconnect)

// Main thread only. Validation failures become Err results; nothing throws out of Run.
void Run(const std::string& op, const json& args, Done done, Cancelled cancelled);
// game.state before kDataLoaded (answered on the IO thread; touches no game objects).
json StartingState();
}
```

`plugin/src/Ops.cpp`:
```cpp
#include "Ops.h"
#include "Lifecycle.h"
#include "PapyrusValues.h"
#include "Signatures.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <thread>

namespace spy::ops {
namespace {
using RE::BSScript::Variable;

std::thread::id g_mainThread;  // set by Run, which only ever runs on the main thread

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

// Arguments for one dispatch. The VM takes ownership of this object (spike S2).
class DynArgs final : public RE::BSScript::IFunctionArguments
{
public:
    explicit DynArgs(std::vector<Variable> v) : v_(std::move(v)) {}
    bool operator()(RE::BSScrapArray<Variable>& dst) const override
    {
        dst.resize(static_cast<std::uint32_t>(v_.size()));
        for (std::size_t i = 0; i < v_.size(); ++i) dst[static_cast<std::uint32_t>(i)] = v_[i];
        return true;
    }

private:
    std::vector<Variable> v_;
};

void Convert(const Done& done, const Variable& result)
{
    try {
        done(OpResult::Ok(pv::FromVariable(result)));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
}

class Callback final : public RE::BSScript::IStackCallbackFunctor
{
public:
    explicit Callback(Done d) : done_(std::move(d)) {}
    void operator()(Variable result) override
    {
        if (std::this_thread::get_id() == g_mainThread) return Convert(done_, result);
        auto done = done_;
        SKSE::GetTaskInterface()->AddTask([done, result] { Convert(done, result); });
    }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}

private:
    Done done_;
};

bool Paused()
{
    auto* ui = RE::UI::GetSingleton();
    return ui && ui->GameIsPaused();
}

OpResult PausedError() { return OpResult::Err(ErrorCode::not_ready, "the game is paused", {{"reason", "paused"}}); }

json GameState()
{
    json s = {{"data_ready", life::DataReady()}, {"game_loaded", life::GameLoaded()}, {"loading", life::Loading()},
              {"interactive", life::Interactive()}, {"epoch", life::Epoch()}, {"menus", life::OpenMenus()},
              {"paused", Paused()}, {"player", nullptr}, {"game_days_passed", nullptr}};
    if (life::GameLoaded() && !life::Loading()) {
        if (auto* player = RE::PlayerCharacter::GetSingleton()) s["player"] = form_ref_to_json(pv::DescribeForm(player));
        if (auto* cal = RE::Calendar::GetSingleton()) s["game_days_passed"] = cal->GetDaysPassed();
    }
    return s;
}

json Resolve(const json& args)
{
    RE::TESForm* form = nullptr;
    if (args.contains("editor_id")) {
        const auto eid = args["editor_id"].get<std::string>();
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

void CallStatic(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && Paused()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto fn = sig::FindFunction(c.script, c.function, false);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done)};
    if (!VM()->DispatchStaticCall(RE::BSFixedString(c.script), RE::BSFixedString(c.function), new DynArgs(std::move(vars)), cb))
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified));
}

void CallMethod(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && Paused()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto target = pv::ResolveTarget(*c.object, c.script);
    auto fn = sig::FindFunction(c.script, c.function, true);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done)};
    if (!VM()->DispatchMethodCall(target.object, RE::BSFixedString(c.function), new DynArgs(std::move(vars)), cb))
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified + " on " + target.desc));
}
}

json StartingState()
{
    return {{"data_ready", false}, {"game_loaded", false}, {"loading", false}, {"interactive", false},
            {"epoch", 0}, {"menus", json::array()}, {"paused", false}, {"player", nullptr}, {"game_days_passed", nullptr}};
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled)
{
    g_mainThread = std::this_thread::get_id();
    try {
        if (op == "game.state") return done(OpResult::Ok(GameState()));
        if (auto why = life::NotReadyReason())
            return done(OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", {{"reason", *why}}));
        if (op == "form.resolve") return done(OpResult::Ok(Resolve(args)));
        if (op == "papyrus.call_static") return CallStatic(parse_call_args(args, false), done);
        if (op == "papyrus.call_method") return CallMethod(parse_call_args(args, true), done);
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
    (void)cancelled;  // used by papyrus.batch (Task 18)
}
}
```

- [ ] **Step 5: Dispatch**

`plugin/src/Dispatch.h`:
```cpp
#pragma once
#include "spy/config.h"
#include "spy/messages.h"
#include "spy/outbox.h"

namespace spy::dispatch {
void Init(Outbox& outbox, const Config& cfg);
void OnRequest(ConnId conn, Request req);  // IO thread
void OnDisconnect(ConnId conn);            // IO thread
void OnTick();                             // IO thread
}
```

`plugin/src/Dispatch.cpp`:
```cpp
#include "Dispatch.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Wire.h"
#include <SKSE/SKSE.h>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <unordered_map>

namespace spy::dispatch {
namespace {
using Clock = std::chrono::steady_clock;

Outbox* g_outbox = nullptr;
const Config* g_cfg = nullptr;
std::unique_ptr<InflightLimiter> g_limiter;

struct PendingCall
{
    ConnId conn = 0;
    std::uint64_t id = 0;
    std::uint32_t timeoutMs = 0;
    Clock::time_point deadline;
    std::atomic_bool done{false};

    // Once only: the first of result / timeout / cancel wins; later ones are dropped.
    bool Finish(const ops::OpResult& r)
    {
        if (done.exchange(true)) return false;
        g_outbox->post(conn, Frame(r.ok ? make_ok(id, r.result) : make_error(id, r.code, r.message, r.detail)));
        g_limiter->release(conn);
        return true;
    }
};

std::mutex g_mx;
std::unordered_map<PendingCall*, std::shared_ptr<PendingCall>> g_pending;

void Forget(PendingCall* p)
{
    std::lock_guard l(g_mx);
    g_pending.erase(p);
}

void Complete(const std::shared_ptr<PendingCall>& call, const ops::OpResult& r)
{
    if (call->Finish(r)) Forget(call.get());
}
}

void Init(Outbox& outbox, const Config& cfg)
{
    g_outbox = &outbox;
    g_cfg = &cfg;
    g_limiter = std::make_unique<InflightLimiter>(cfg.max_inflight_per_connection, cfg.max_inflight_total);
}

void OnRequest(ConnId conn, Request req)
{
    if (!g_limiter->try_acquire(conn)) {
        g_outbox->post(conn, Frame(make_error(req.id, ErrorCode::busy, "too many calls in flight",
            {{"max_per_connection", g_cfg->max_inflight_per_connection}, {"max_total", g_cfg->max_inflight_total}})));
        return;
    }
    auto call = std::make_shared<PendingCall>();
    call->conn = conn;
    call->id = req.id;
    call->timeoutMs = req.args.value("timeout_ms", g_cfg->default_timeout_ms);
    call->deadline = Clock::now() + std::chrono::milliseconds(call->timeoutMs);
    {
        std::lock_guard l(g_mx);
        g_pending.emplace(call.get(), call);
    }
    if (req.op == "game.state" && !life::DataReady()) return Complete(call, ops::OpResult::Ok(ops::StartingState()));
    if (req.op != "game.state")
        if (auto why = life::NotReadyReason())
            return Complete(call, ops::OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", {{"reason", *why}}));

    SKSE::GetTaskInterface()->AddTask([call, op = std::move(req.op), args = std::move(req.args)] {
        if (call->done) return;  // timed out or disconnected while queued
        ops::Run(op, args, [call](ops::OpResult r) { Complete(call, r); }, [call] { return call->done.load(); });
    });
}

void OnDisconnect(ConnId conn)
{
    g_limiter->forget(conn);
    std::lock_guard l(g_mx);
    for (auto it = g_pending.begin(); it != g_pending.end();) {
        if (it->second->conn == conn) {
            it->second->done = true;  // cancels queued work; its result, if any, is dropped
            it = g_pending.erase(it);
        } else {
            ++it;
        }
    }
}

void OnTick()
{
    const auto now = Clock::now();
    std::vector<std::shared_ptr<PendingCall>> expired;
    {
        std::lock_guard l(g_mx);
        for (auto& [p, call] : g_pending)
            if (now >= call->deadline) expired.push_back(call);
    }
    for (auto& call : expired)
        Complete(call, ops::OpResult::Err(ErrorCode::timeout,
            "no result within " + std::to_string(call->timeoutMs) + " ms (the call may still finish in the game; "
            "Papyrus does not run while the game is paused in a menu)"));
}
}
```

- [ ] **Step 6: Wire the dispatcher into `main.cpp`**

In `plugin/src/main.cpp`:
1. Add `#include "Dispatch.h"` after `#include "Lifecycle.h"`.
2. Replace the stub `cb.onRequest = [](spy::ConnId conn, spy::Request req) { ... };` block with:
```cpp
    spy::dispatch::Init(g_outbox, cfg);
    cb.onRequest = spy::dispatch::OnRequest;
    cb.onDisconnect = spy::dispatch::OnDisconnect;
    cb.onTick = spy::dispatch::OnTick;
```
3. Nothing else changes (`cfg` is the `const Config&` already in `StartServer`; `settings::Get()` returns a reference to a static, so the pointer `Init` keeps stays valid).

- [ ] **Step 7: Build, deploy, run the live tests**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh
source tools/env.sh
until grep -q "listening on 127.0.0.1:45977" "$SKSE_LOG_DIR/SkyrimPython.log" 2>/dev/null; do sleep 2; done
until grep -q "lifecycle: Main Menu opened" "$SKSE_LOG_DIR/SkyrimPython.log"; do sleep 2; done
uv run --project python pytest livetest/test_m1_mainmenu.py livetest/test_m1_transport.py -q
# Load a save from the main menu (Continue), then, with the values recorded by spike S4:
export SPY_S4_QUEST=0x... SPY_S4_ALIAS=... SPY_S4_REF=0x...
uv run --project python pytest livetest/test_m1_calls.py -q
```
Expected: main-menu and transport tests pass before the save is loaded; all of `test_m1_calls.py` passes after. If `test_array_result_and_argument` fails with `UnknownFunction`, the runtime's `Utility` lacks those functions: check `Scripts/Source/Utility.psc` and replace them with another array function listed there (record which). If S4 failed, `test_alias_method_calls` is expected to fail with `vm_error`; mark it `xfail(reason="S4 failed; aliases via M1.5 helper")` and note it in `spikes/RESULTS.md`.

- [ ] **Step 8: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): generic Papyrus dispatcher with typed argument checks"
```
