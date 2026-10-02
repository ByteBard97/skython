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
}

// A Papyrus Float is 32-bit. Widening it prints 0.1 as 0.10000000149011612. Core's encoder (value_to_json) prints the
// shortest text that round-trips the float, and sends NaN and infinity as null (PROTOCOL.md): one rule for every Float.
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
    if (t.IsArray() || t.IsObject())
        throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " is a " + TypeName(t) +
            "; objects and arrays can be sent from Task 28 on");
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
    throw ProtocolError(ErrorCode::unsupported, "the result is an object or array, which this build cannot return yet (Task 28)");
}
}
