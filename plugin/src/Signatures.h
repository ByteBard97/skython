#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>
#include <string_view>
#include <vector>

namespace spy::sig {
bool IEquals(std::string_view a, std::string_view b);  // Papyrus names are case-insensitive; GetName() gives "utility" (S7a)
// True if `script` is `ancestor` or extends it. False if `script` is not a loaded script.
bool IsA(const std::string& script, const std::string& ancestor);
// Throws unknown_function (no such script or function) or bad_request (global vs member mix-up).
// Callers run this BEFORE resolving any object, so "global called as member" is bad_request, not type_mismatch.
RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member);
// Converts positional arguments to the declared parameter types. Throws type_mismatch naming the parameter and its
// position (natives report names param1..N, S7a; there are no named arguments in v1).
std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified);
// The property `property` of `script` or its parents (`info`: its declared `type`; `getFunction`/`setFunction` set
// means a full property), plus where an auto property's value lives in a bound Object. `variableIndex` indexes
// Object::variables, which hold the root parent's variables first and the most derived class's last (CommonLib's
// Object::GetVariable). It is found by name: the pex links an auto property to its backing variable as
// `::<property>_var`. The linked property's autoVarIndex is NOT relative to the declaring class's own table; it is
// already absolute, parents included (live on 1.6.1170: a child script whose parent has its own variables showed
// autoVarIndex Counter=2, Ratio=5, Target=6 - the absolute slots - so adding the parent counts again read two slots
// late, Counter landing on ::Label_var's value). `variableName` is the backing variable's name in the declaring class (empty when the
// class has no such variable). Both are meaningful only when the property has no get/set function. Throws not_found.
struct PropertySlot { RE::BSScript::PropertyTypeInfo info; std::uint32_t variableIndex = 0; std::string variableName; };
PropertySlot FindPropertySlot(const std::string& script, const std::string& property);
}
