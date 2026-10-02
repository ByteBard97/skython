#pragma once
#include "spy/errors.h"
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>

// Inside an SKSE task only ("main thread"), except NeedsMainThread and TypeName: every other function touches
// forms or the VM.
namespace spy::pv {
RE::TESForm* ResolveForm(const FormRef& ref);            // throws not_found (missing or deleted) / stale_reference
RE::BGSBaseAlias* ResolveAlias(const AliasRef& ref);     // throws not_found / type_mismatch
FormRef DescribeForm(const RE::TESForm* form);
AliasRef DescribeAlias(const RE::BGSBaseAlias* alias);
std::string ScriptClassOf(const RE::TESForm* form);      // "Actor", "Weapon", ... in the .psc spelling; "Form" if unknown

struct Target
{
    RE::BSTSmartPointer<RE::BSScript::Object> object;  // bound script object to dispatch on
    std::string desc;
};
// The object a method or property is called on. `script` must be attached to it, or be its native
// class or an ancestor of that; otherwise type_mismatch. A cold form is bound through PackHandle (spike S7).
Target ResolveTarget(const Value& object, const std::string& script);

void ToVariable(const Value& v, const RE::BSScript::TypeInfo& declared, const std::string& param, RE::BSScript::Variable& out);  // primitives, forms, arrays, aliases
json FromVariable(const RE::BSScript::Variable& v);      // primitives, None, forms, arrays, aliases
bool NeedsMainThread(const RE::BSScript::Variable& v);   // true for a non-None object or any array
std::string TypeName(const RE::BSScript::TypeInfo& t);
json FloatToJson(float f);                               // core's encoder: shortest round-trip text; non-finite -> null
// Fills the FormType -> Papyrus class name table (canonical_script_name spelling) through the VM, once.
// Inside an SKSE task only. Idempotent; a second call rebuilds nothing.
void BuildClassTable();
// DescribeForm without the VM: safe on any engine thread (event sinks). Reads the form id, the data handler's
// load order, the base object, and the class table (type "Form" until BuildClassTable has run).
FormRef DescribeFormForEvent(const RE::TESForm* form);
}
