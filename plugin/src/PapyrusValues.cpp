#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Signatures.h"
#include "spy/papyrus_names.h"
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

// The plugin a non-runtime form comes from. GetFile(0) is null for some forms (the player, 0x14, on the box:
// Task 27's first live run), so fall back to the load-order index in the FormID.
const RE::TESFile* SourceFile(const RE::TESForm* form)
{
    if (const auto* file = form->GetFile(0)) return file;
    const auto id = form->GetFormID();
    auto* dh = RE::TESDataHandler::GetSingleton();
    if (!dh || is_runtime_form_id(id)) return nullptr;
    if (is_light_form_id(id)) return dh->LookupLoadedLightModByIndex(static_cast<std::uint16_t>((id >> 12) & 0xFFF));
    return dh->LookupLoadedModByIndex(static_cast<std::uint8_t>(id >> 24));
}

// local_form_id(id), not the form's own local id accessor: that one also depends on GetFile(0).
std::string KeyOrId(const RE::TESForm* form)
{
    const auto id = form->GetFormID();
    if (!is_runtime_form_id(id))
        if (const auto* file = SourceFile(form))
            return format_form_key({std::string(file->GetFilename()), local_form_id(id)});
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

// Concrete alias class and VM type id. Do not call BGSBaseAlias::GetVMTypeID(): S4 proved the skyrim_cast route.
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

void BindObject(const void* ptr, RE::VMTypeID type, const std::string& nativeClass, const std::string& desc,
    const TypeInfo& declared, const std::string& param, Variable& out)
{
    const auto* cls = declared.GetTypeInfo();  // a single object type here (IsObject), never an object array
    const std::string want = cls ? cls->GetName() : "";
    if (want.empty() || sig::IsA(nativeClass, want)) {
        RE::BSScript::PackHandle(&out, ptr, type);
        // A failed PackHandle leaves the value None (kNone), and GetObject() asserts the value is an object.
        if (!out.IsObject()) throw ProtocolError(ErrorCode::vm_error, "could not bind " + desc + " (a " + nativeClass + ") as " + (want.empty() ? "an object" : canonical_script_name(want)));
        // PackHandle types the value as the form's own class (Actor, WEAPON). Passed as is to a parameter declared as
        // a base class (Form), the native never ran and the call returned None (Task 28, launch 1: IsObjectFavorited,
        // CreateFormArray); an exact class match (IsWordUnlocked's WordOfPower) ran. So give the value the declared
        // parameter type, as the FindBound branch below does; the object keeps its own class.
        auto obj = out.GetObject();
        if (!obj) throw ProtocolError(ErrorCode::vm_error, "could not bind " + desc + " (a " + nativeClass + ") as " + (want.empty() ? "an object" : canonical_script_name(want)));
        if (!want.empty()) out.SetObject(obj, declared.GetRawType());
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
    throw ProtocolError(ErrorCode::type_mismatch, "parameter " + param + " expects " + canonical_script_name(want) + ", got " + desc + " (a " + nativeClass + ")");
}

// An alias argument. Unlike a form, an alias is never bound on demand (R45): no PackHandle, which on a plain alias
// would bind a script object to it. Only an object already bound to the alias is passed, re-typed to the declared
// parameter type as BindObject does for forms (Task 28: a value typed as its own class made natives return None).
void BindAlias(RE::BGSBaseAlias* alias, const AliasRef& a, const TypeInfo& declared, const std::string& param, Variable& out)
{
    const auto* cls = declared.GetTypeInfo();  // a single object type here (IsObject), never an object array
    const std::string want = cls ? cls->GetName() : a.alias_type;
    const auto desc = std::format("alias {} of {}", a.alias_id, Describe(a.quest));
    auto* policy = VM()->GetObjectHandlePolicy();
    const auto handle = policy->GetHandleForObject(AliasTypeId(alias), alias);
    if (handle == policy->EmptyHandle()) throw ProtocolError(ErrorCode::vm_error, "the VM has no handle for " + desc);
    RE::BSTSmartPointer<RE::BSScript::Object> bound;
    if (FindBound(handle, want, bound)) {  // also accepts a parent class of the bound object (S7d)
        if (cls) out.SetObject(bound, declared.GetRawType());
        else out.SetObject(bound);
        return;
    }
    throw ProtocolError(ErrorCode::type_mismatch, "parameter " + param + " expects " + canonical_script_name(want) + ", got " + desc +
        " (a " + a.alias_type + " with no " + canonical_script_name(want) + " script object bound; v1 drives scripted aliases only)");
}

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
                // high32 vs quest_vmtype: if they are equal, an alias whose id equals the Quest type id would pass the
                // form check above and decode as its quest (R52; S4: alias handles are (aliasID << 32) | questFormID).
                const auto high32 = static_cast<std::uint32_t>(handle >> 32);
                const auto questType = static_cast<std::uint32_t>(RE::FormType::Quest);
                log::Info(std::format("values: quest handle {:016X} high32={:08X} quest_vmtype={:08X} high32_is_quest_vmtype={} "
                    "is_ref_alias={} is_loc_alias={}", handle, high32, questType, high32 == questType,
                    policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle), policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle)));
            });
        }
        return form_ref_to_json(DescribeForm(form));
    }
    // Aliases only when the form check failed (S7f/S7g: HandleIsType(BGSRefAlias) and GetObjectForHandle resolve it).
    if (policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, handle) || policy->HandleIsType(RE::BGSLocAlias::VMTYPEID, handle))
        return AliasObjectToJson(policy, handle);
    const auto* cls = obj.GetTypeInfo();
    throw ProtocolError(ErrorCode::unsupported, std::string("the result is a ") + (cls ? canonical_script_name(cls->GetName()) : "script") +
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

// The one place a form's class name is made: DescribeForm's type, Fingerprint.type and LiveFingerprint all come from
// here, so they agree (core's fingerprint comparison is case-sensitive). The VM's GetName() gave WEAPON and SPELL on
// the box (Task 28): canonical_script_name gives the .psc spelling (R56). Lookups by name (IsA, FindBoundObject,
// GetScriptObjectType) are case-insensitive and take the names as they are.
std::string ScriptClassOf(const RE::TESForm* form)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (VM()->GetScriptObjectType(static_cast<RE::VMTypeID>(form->GetFormType()), info) && info) return canonical_script_name(info->GetName());
    return "Form";
}

RE::TESForm* ResolveForm(const FormRef& r)
{
    RE::TESForm* form = nullptr;
    const bool runtime = !r.form_key && r.form_id && is_runtime_form_id(*r.form_id);
    if (r.form_key) {
        const auto k = parse_form_key(*r.form_key).value();  // validated when the request was parsed
        auto* dh = RE::TESDataHandler::GetSingleton();
        if (!dh) throw ProtocolError(ErrorCode::not_ready, "the game's data handler is not available yet");
        form = dh->LookupForm(k.local_id, k.plugin);
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
    else if (SourceFile(form)) r.form_key = KeyOrId(form);
    return r;
}

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
    if (!alias->owningQuest) throw ProtocolError(ErrorCode::vm_error, std::format("alias {} has no owning quest", alias->aliasID));
    auto* mutableAlias = const_cast<RE::BGSBaseAlias*>(alias);  // skyrim_cast wants a non-const pointer here
    AliasRef a;
    a.quest = DescribeForm(alias->owningQuest);
    a.alias_id = static_cast<std::int32_t>(alias->aliasID);
    const char* kind = AliasKind(mutableAlias);
    a.alias_type = kind ? kind : "ReferenceAlias";
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
        // (save persistence unmeasured, see Task 29's Rules).
        Variable v;
        RE::BSScript::PackHandle(&v, ptr, type);
        if (v.IsObject())
            if (auto obj = v.GetObject()) return {obj, desc};
        throw ProtocolError(ErrorCode::vm_error, "could not bind a " + nativeClass + " script object to " + desc);
    }
    throw ProtocolError(ErrorCode::type_mismatch, desc + " is a " + nativeClass + " and has no script " + script + " attached");
}

std::string TypeName(const TypeInfo& t)
{
    // Never GetTypeInfo() on an object array: the pinned CommonLib returns a pointer 8 bytes off (crashed the game in
    // S7). The class pointer of an object array is raw & ~1.
    if (t.IsObjectArray()) {
        const auto* cls = reinterpret_cast<const RE::BSScript::ObjectTypeInfo*>(static_cast<std::uintptr_t>(t.GetRawType()) & ~std::uintptr_t{1});
        return (cls ? canonical_script_name(cls->GetName()) : std::string("Object")) + "[]";
    }
    if (t.IsObject() && t.GetTypeInfo()) return canonical_script_name(t.GetTypeInfo()->GetName());
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
        if (const auto* a = std::get_if<AliasRef>(&v.v)) {
            BindAlias(ResolveAlias(*a), *a, t, param, out);
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
    if (v.IsFloat()) return FloatToJson(v.GetFloat());
    if (v.IsString()) return std::string(v.GetString());
    if (t.IsArray()) {
        auto arr = v.GetArray();
        if (!arr) return nullptr;
        // Results are not capped at 128 (index Contracts): SKSE and PapyrusUtil natives can return longer arrays.
        // Copied without the Array's BSSpinLock, which is private in the pinned CommonLib. Acceptable because this
        // conversion runs inside an SKSE task (R49).
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
