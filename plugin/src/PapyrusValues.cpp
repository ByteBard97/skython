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

void BindObject(const void* ptr, RE::VMTypeID type, const std::string& nativeClass, const std::string& desc,
    const TypeInfo& declared, const std::string& param, Variable& out)
{
    const auto* cls = declared.GetTypeInfo();  // a single object type here (IsObject), never an object array
    const std::string want = cls ? cls->GetName() : "";
    if (want.empty() || sig::IsA(nativeClass, want)) {
        RE::BSScript::PackHandle(&out, ptr, type);
        // PackHandle types the value as the form's own class (Actor, WEAPON). Passed as is to a parameter declared as
        // a base class (Form), the native never ran and the call returned None (Task 28, launch 1: IsObjectFavorited,
        // CreateFormArray); an exact class match (IsWordUnlocked's WordOfPower) ran. So give the value the declared
        // parameter type, as the FindBound branch below does; the object keeps its own class.
        auto obj = out.GetObject();
        if (!obj) throw ProtocolError(ErrorCode::vm_error, "could not bind " + desc + " (a " + nativeClass + ") as " + (want.empty() ? "an object" : want));
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
    else if (SourceFile(form)) r.form_key = KeyOrId(form);
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
