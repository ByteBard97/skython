#include "Signatures.h"
#include "PapyrusValues.h"
#include "spy/errors.h"
#include <algorithm>
#include <cctype>
#include <format>

namespace spy::sig {
namespace {
RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

// Lookup is case-insensitive in the VM (S7b: "actor" finds Actor). A failed GetScriptObjectType leaves an empty stub
// type that the NEXT GetScriptObjectType of that name returns as found; GetScriptObjectTypeNoLoad stays false for the
// stub (S7b2), so both must succeed. Do not reject on "no parent, 0 functions, 0 properties": a legitimate empty base
// script matches that too.
RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> TypeOf(const std::string& script)
{
    const RE::BSFixedString name(script);
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (!VM()->GetScriptObjectType(name, info) || !info) return {};
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> loaded;
    if (!VM()->GetScriptObjectTypeNoLoad(name, loaded) || !loaded) return {};  // the stub a failed load left behind
    return info;
}
}

bool IEquals(std::string_view a, std::string_view b)
{
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
        return std::tolower(static_cast<unsigned char>(x)) == std::tolower(static_cast<unsigned char>(y));
    });
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
            "{} takes {} argument(s), got {} (the plugin does not fill in Papyrus default values)", qualified, n, args.size()));
    std::vector<RE::BSScript::Variable> out(n);
    for (std::uint32_t i = 0; i < n; ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        fn.GetParam(i, name, type);  // a native's name is param1..N (S7a): the position is the reliable part
        pv::ToVariable(args[i], type, std::format("{} (argument {} of {})", name.c_str(), i, qualified), out[i]);
    }
    return out;
}
}
