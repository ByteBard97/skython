#pragma once
#include "spy/errors.h"
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>

// Inside an SKSE task only ("main thread", R49), except NeedsMainThread and TypeName: every other function touches
// forms or the VM.
namespace spy::pv {
RE::TESForm* ResolveForm(const FormRef& ref);            // Task 28. throws not_found (missing or deleted) / stale_reference
RE::BGSBaseAlias* ResolveAlias(const AliasRef& ref);     // Task 29. throws not_found / type_mismatch
FormRef DescribeForm(const RE::TESForm* form);           // Task 27
AliasRef DescribeAlias(const RE::BGSBaseAlias* alias);   // Task 29
std::string ScriptClassOf(const RE::TESForm* form);      // Task 27. "Actor", "Weapon", ...; "Form" if unknown

struct Target
{
    RE::BSTSmartPointer<RE::BSScript::Object> object;  // bound script object to dispatch on
    std::string desc;
};
// Task 29. The object a method or property is called on. `script` must be attached to it, or be its native
// class or an ancestor of that; otherwise type_mismatch. A cold form is bound through PackHandle (spike S7).
Target ResolveTarget(const Value& object, const std::string& script);

void ToVariable(const Value& v, const RE::BSScript::TypeInfo& declared, const std::string& param, RE::BSScript::Variable& out);  // 27 primitives; 28 forms, arrays; 29 aliases
json FromVariable(const RE::BSScript::Variable& v);      // 27 primitives and None; 28 forms and arrays; 29 aliases
bool NeedsMainThread(const RE::BSScript::Variable& v);   // Task 27. true for an object or an array of objects
std::string TypeName(const RE::BSScript::TypeInfo& t);   // Task 27
json FloatToJson(float f);                               // Task 27. core's encoder: shortest round-trip text; non-finite -> null
}
