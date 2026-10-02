#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>
#include <string_view>
#include <vector>

namespace spy::sig {
bool IEquals(std::string_view a, std::string_view b);  // Papyrus names are case-insensitive; GetName() gives "utility" (S7a)
// True if `script` is `ancestor` or extends it. False if `script` is not a loaded script. (Task 28)
bool IsA(const std::string& script, const std::string& ancestor);
// Throws unknown_function (no such script or function) or bad_request (global vs member mix-up).
// Callers run this BEFORE resolving any object, so "global called as member" is bad_request, not type_mismatch.
RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member);
// Converts positional arguments to the declared parameter types. Throws type_mismatch naming the parameter and its
// position (natives report names param1..N, S7a; there are no named arguments in v1).
std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified);
// The property `property` of `script` or its parents (its declared `type`; `getFunction`/`setFunction` set means a
// full property). Throws not_found. (Task 28)
RE::BSScript::PropertyTypeInfo FindProperty(const std::string& script, const std::string& property);
}
