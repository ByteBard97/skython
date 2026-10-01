# Task 9: Spike S7 — function lookup, bound-object dispatch, alias handles and object arrays

Adapted from: branch `plan-out-of-process`, task 6b (`task-06b-spike-s7-function-lookup-and-bound-object-dispatch.md`). The C++ is unchanged (it already carries the build-box fixes from `API-VERIFICATION.md`); gates, task numbers and the consumers of the findings changed. The old plan's dispatcher Task 17 is split here into Task 27 (dispatch skeleton, `Signatures.cpp`: `TypeOf`, `FindFunction`, `BindArgs`), Task 28 (value conversion: `ResolveForm`, `FindBound`, `BindObject`, `ToVariable`, `FromVariable`/`ObjectToJson`, `IsA`, `FindProperty`), Task 29 (`call_method`, aliases: `ResolveAlias`, `DescribeAlias`, `ResolveTarget`, `CallMethod`) and Task 30 (properties, batch).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.3, 4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; S2, S9 and S4 gate Part C (Tasks 25-32); **this spike (S7) gates Tasks 28 and 29** (it checks the engine APIs the dispatcher code was first written from memory against; Task 27's lookup code uses the same calls); S5, S6 and S10 gate only the M2 plan. **An S7 sub-test FAIL does not mean stop the project: it names the exact Task 27-30 code to change (table in Step 3). If a FAIL cannot be resolved by one of the listed changes, STOP and ask the owner.** Run on a throwaway save; the spike port is unauthenticated.

**Why this task exists:** S2 and S4 only exercise `DispatchStaticCall` and the handle-plus-className `DispatchMethodCall` overload. The dispatcher (Tasks 27-30) depends most on things no other spike covers: `FindFunction`/`GetParam` over `ObjectTypeInfo`, `PropertyIter`, `ResolveTarget` through `FindBoundObject`/`PackHandle` on a form the VM has never bound, the `DispatchMethodCall(BSTSmartPointer<Object>&, ...)` overload, alias handle decoding, `BGSBaseAlias::GetVMTypeID()`, and object arrays. Run this spike before Part C, and Tasks 27-30 then consume the answers instead of the recalled APIs.

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Tasks 4, 6): `spike::OnMainThread`, `spike::WaitUntil`, `spike::Functor`, `spike::CountedArgs`, `spike::NoArgs`, `spike::IntArgs`, `spike::TidOf`, `spike::PickAlias` (Task 6), `spike::Log`. No new includes (Task 1's block covers them).
- Produces for Tasks 27-30 (recorded in the S7 row of `spikes/RESULTS.md`): the real `FindBoundObject` signature; whether `PackHandle` binds a cold form; whether the object-pointer `DispatchMethodCall` overload works; whether a quest's own attached script (a non-native class, spec 4.3's "a mod's own script") is found by `FindBoundObject(h, "<AttachedScript>")` and dispatches a member function (S7c; Task 29's `ResolveTarget`); whether `GetVMTypeID()` is reliable; how an alias result is encoded; whether object arrays can be built from a computed element type or need the copied `TypeInfo` (Task 28 rejects object-array *arguments* as `unsupported` in v1 either way; this answer decides whether M2 can lift that).

- [ ] **Step 0: Check the gates, then read the real signatures (no assumptions)**

```bash
grep -E '^\| S4 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S4 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
grep -E '^\| S6 [^|]*\|[^|]*\| (PASS|FAIL) ' spikes/RESULTS.md || { echo "the S6 row is not filled yet: finish Task 8 first"; false; }
```
Expected: both rows print (S6 only has to be recorded, PASS or FAIL; it does not gate S7).

The first draft of the dispatcher code (and the plan review) disagreed about several signatures; `API-VERIFICATION.md` in this plan folder records the answers found by reading and compiling against the pinned commit, so this step confirms them. Grep the pinned checkout (`plugin/extern/CommonLibSSE-NG`) and write each finding into `docs/PINS.md` before writing any S7 code:
```bash
cd plugin/extern/CommonLibSSE-NG
grep -rn "FindBoundObject" include/RE/I/IVirtualMachine.h include/RE/V/VirtualMachine.h   # exact parameters and constness
grep -rn "PackHandle" include/RE/P/PackUnpack.h                                          # (Variable*, const void*, VMTypeID) ?
grep -n "DispatchMethodCall" include/RE/I/IVirtualMachine.h include/RE/V/VirtualMachine.h  # the Object& overload AND the handle+className overload
grep -n "GetVMTypeID\|VMTYPEID" include/RE/B/BGSBaseAlias.h include/RE/B/BGSRefAlias.h include/RE/B/BGSLocAlias.h
grep -n "GetReturnType\|GetParam\b\|GetParamCount" include/RE/I/IFunction.h
grep -n "GetMemberFuncIter\|GetGlobalFuncIter\|GetPropertyIter\|GetParent\|GetNumProperties" include/RE/O/ObjectTypeInfo.h
grep -n "type_info\|size()" include/RE/A/Array.h
grep -n "attachedScripts\|CreateArray\|GetScriptObjectType" include/RE/V/VirtualMachine.h include/RE/I/IVirtualMachine.h
grep -n "Resolve\|GetHandle\|GetProperty" include/RE/O/Object.h
grep -n "GetAllForms" include/RE/T/TESForm.h
cd ../../..
```
For orientation only (not authoritative): another CommonLibSSE-NG copy on this machine (the one vendored in `skyrim-re-toolkit`, which the plan forbids building against) declares `FindBoundObject(VMHandle, const char* a_className, BSTSmartPointer<Object>& a_result) const` (three parameters), while the plan review recalled a five-parameter form. **The pinned checkout decides.** Every API call in the code below that does not match the pinned headers must be changed to match the header, and the change noted in `docs/PINS.md`.

- [ ] **Step 1: Add the S7 handlers**

Add as a new `namespace spike { ... }` block above `Handle`. It relies on `PickAlias` from Task 6 and the helpers from Task 4.
```cpp
namespace spike {
using VMT = RE::BSScript::Internal::VirtualMachine;
using Raw = RE::BSScript::TypeInfo::RawType;
using Cb = RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor>;
VMT* VM() { return VMT::GetSingleton(); }

std::string TI(const RE::BSScript::TypeInfo& t)
{
    std::string cls;
    if (t.IsObject() || t.IsObjectArray())
        if (auto* ci = t.GetTypeInfo()) cls = ci->GetName();
    // raw is GetRawType(): for a class type that is the class POINTER (+1 for an object array), not kObject;
    // unmangled is GetUnmangledRawType(): kObject (1) / kObjectArray (11) for class types (API-VERIFICATION.md, TypeInfo).
    return std::format("raw={:#x} unmangled={:#x} obj={} arr={} objarr={} cls={}", static_cast<std::uintptr_t>(t.GetRawType()),
                       static_cast<std::uintptr_t>(t.GetUnmangledRawType()), t.IsObject(), t.IsArray(), t.IsObjectArray(), cls);
}

// The element-type formula the first dispatcher draft used (it is not in Tasks 27-30: they reject object-array
// arguments). It maps an ARRAY type to its element type. S7h compares its result with the real array's
// type_info() (the element type) by raw value only: a wrong result is not a valid TypeInfo, so it is never
// passed to TI(), GetTypeInfo() or CreateArray.
RE::BSScript::TypeInfo ElementTypeFormula(const RE::BSScript::TypeInfo& arrayType)
{
    const auto raw = static_cast<std::uintptr_t>(arrayType.GetRawType());
    if (arrayType.IsObjectArray()) return RE::BSScript::TypeInfo(static_cast<Raw>(raw & ~std::uintptr_t{1}));
    return RE::BSScript::TypeInfo(static_cast<Raw>(raw - (static_cast<std::uintptr_t>(Raw::kNoneArray) - static_cast<std::uintptr_t>(Raw::kNone))));
}

// A dispatch whose callback only copies the Variable; decode objects/arrays afterwards on the main thread.
struct Captured
{
    std::mutex mx;
    RE::BSScript::Variable v;
    bool have = false;
};
using Issue = std::function<bool(VMT*, Cb&)>;
std::shared_ptr<Captured> CaptureCall(Issue issue, std::string& status, std::chrono::seconds wait = std::chrono::seconds(10))
{
    auto cap = std::make_shared<Captured>();
    status = OnMainThread([cap, issue](std::function<void(std::string)> finish) {
        Cb cb{ new Functor([cap, finish](RE::BSScript::Variable v) {
            { std::lock_guard l(cap->mx); cap->v = v; cap->have = true; }
            finish("got");
        }) };
        if (!issue(VM(), cb)) finish("DISPATCH_FAILED");
    }, wait);
    return cap;
}

bool IEq(const char* a, const std::string& b) { return _stricmp(a, b.c_str()) == 0; }

RE::BGSBaseAlias* FindAliasBase(RE::TESQuest* quest, std::uint32_t aliasId)
{
    RE::BSReadLockGuard lock(quest->aliasAccessLock);
    for (auto* a : quest->aliases)
        if (a && a->aliasID == aliasId) return a;
    return nullptr;
}

// ---- S7a: FindFunction / GetParam over ObjectTypeInfo (Task 27: sig::FindFunction, BindArgs)
struct FnInfo { std::string member = "-", global = "-", text; };
std::string Params(const RE::BSScript::IFunction& f)
{
    std::string s = std::format("params={} [", f.GetParamCount());
    for (std::uint32_t i = 0; i < f.GetParamCount(); ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        f.GetParam(i, name, type);
        s += std::format("{}{}:{{{}}}", i ? ", " : "", name.c_str(), TI(type));
    }
    return s + "] ret={" + TI(f.GetReturnType()) + "}";  // GetReturnType: confirm in IFunction.h (Step 0)
}
FnInfo FindFn(const std::string& script, const std::string& fn)
{
    auto out = std::make_shared<FnInfo>();
    auto r = OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
        if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) { out->text = "NO_SCRIPT"; return finish("ok"); }
        for (auto* t = info.get(); t; t = t->GetParent()) {
            for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
                const auto& f = t->GetMemberFuncIter()[i].func;
                if (f && IEq(f->GetName().c_str(), fn) && out->member == "-") { out->member = t->GetName(); out->text += "member " + Params(*f) + "; "; }
            }
            for (std::uint32_t i = 0; i < t->GetNumGlobalFuncs(); ++i) {
                const auto& f = t->GetGlobalFuncIter()[i].func;
                if (f && IEq(f->GetName().c_str(), fn) && out->global == "-") { out->global = t->GetName(); out->text += "global " + Params(*f) + "; "; }
            }
        }
        finish("ok");
    }, std::chrono::seconds(10));
    if (r != "ok") out->text = r;
    return *out;
}
std::string S7a()
{
    const auto ri = FindFn("Utility", "RandomInt");             // global, 2 parameters
    const auto en = FindFn("Actor", "Enable");                  // member declared on a PARENT (ObjectReference)
    const auto wrong = FindFn("Actor", "RandomInt");            // a global asked for as a member: must not be a member
    const auto cf = FindFn("Utility", "CreateFormArray");       // used by S7h; its return type is an array
    const auto rf = FindFn("Utility", "ResizeFormArray");       // used by S7h
    const auto mv = FindFn("ObjectReference", "MoveTo");        // many object/float parameters
    const bool pass = ri.global == "Utility" && ri.text.find("params=2") != std::string::npos && en.member != "-" && wrong.member == "-";
    return std::format("S7a {} RandomInt[member={} global={} {}] Enable[member={} global={} {}] ActorRandomInt[member={} global={}] "
                       "CreateFormArray[global={} {}] ResizeFormArray[global={} {}] MoveTo[member={} {}]",
        pass ? "PASS" : "FAIL", ri.member, ri.global, ri.text, en.member, en.global, en.text, wrong.member, wrong.global,
        cf.global, cf.text, rf.global, rf.text, mv.member, mv.text);
}

// ---- S7b: ObjectTypeInfo enumeration and lookup case-sensitivity (sig::TypeOf, IsA)
std::string Chain(const std::string& script)
{
    auto out = std::make_shared<std::string>();
    OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
        if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) { *out = script + ": NOT_FOUND"; return finish("ok"); }
        for (auto* t = info.get(); t; t = t->GetParent())
            *out += std::format("{}(members={} globals={} props={})->", t->GetName(), t->GetNumMemberFuncs(), t->GetNumGlobalFuncs(), t->GetNumProperties());
        finish("ok");
    }, std::chrono::seconds(10));
    return *out;
}
std::string S7b()
{
    const auto exact = Chain("Actor"), lower = Chain("actor"), none = Chain("NoSuchScriptX");
    const bool pass = exact.find("Form") != std::string::npos && none.find("NOT_FOUND") != std::string::npos;
    return std::format("S7b {} Actor={} | actor(lowercase)={} | NoSuchScriptX={}  (lowercase NOT_FOUND means Task 27 must canonicalise the script name before TypeOf)",
        pass ? "PASS" : "FAIL", exact, lower, none);
}

// ---- S7c: PropertyIter on a script that really has properties, and Object::GetProperty (sig::FindProperty, get/set_property);
// plus a mod's OWN attached script as a call_method target (spec 4.3; Task 29 ResolveTarget with a non-native class):
// FindBoundObject(h, "<AttachedScript>") must return the same Object, and a parameterless member function must
// dispatch through both the Object& overload and the handle + "<AttachedScript>" overload.
struct S7cTarget
{
    RE::BSTSmartPointer<RE::BSScript::Object> obj;
    RE::VMHandle h = 0;
    std::string cls, fn = "-", fnOwner = "-";
    bool fnNative = false, boundByName = false;
};
std::string S7c(const std::string& scriptArg)
{
    auto pick = PickAlias(false);
    if (pick->quest == 0) return "S7c NOT_RUN no running quest (run Task 6's S4 first)";
    auto out = std::make_shared<std::string>();
    auto props = std::make_shared<int>(0);
    auto tgt = std::make_shared<S7cTarget>();
    auto r = OnMainThread([=, quest = pick->quest](std::function<void(std::string)> finish) {
        auto* vm = VM();
        auto* policy = vm->GetObjectHandlePolicy();
        auto* q = RE::TESForm::LookupByID<RE::TESQuest>(quest);
        if (!q) return finish("QUEST_NOT_FOUND");
        const auto h = policy->GetHandleForObject(static_cast<RE::VMTypeID>(q->GetFormType()), q);
        std::vector<RE::BSTSmartPointer<RE::BSScript::Object>> objs;
        {
            RE::BSSpinLockGuard lock(vm->attachedScriptsLock);  // name and element shape: confirm in VirtualMachine.h (Step 0)
            auto it = vm->attachedScripts.find(h);
            if (it != vm->attachedScripts.end())
                for (auto& s : it->second) objs.emplace_back(s.get());   // AttachedScript is BSTPointerAndFlags<BSTSmartPointer<Object>,1>: get() yields Object*, and BSTSmartPointer(Object*) is explicit
        }
        *out = std::format("quest={:08X} attached_scripts={}; ", quest, objs.size());
        for (auto& obj : objs) {
            if (!obj || !obj->GetTypeInfo()) continue;
            if (!scriptArg.empty() && !IEq(obj->GetTypeInfo()->GetName(), scriptArg)) continue;
            const auto* ti = obj->GetTypeInfo();
            if (!tgt->obj) {  // the first attached script object: resolve it by its OWN class name, pick a function
                tgt->obj = obj;
                tgt->h = h;
                tgt->cls = ti->GetName();
                RE::BSTSmartPointer<RE::BSScript::Object> byName;
                tgt->boundByName = vm->FindBoundObject(h, ti->GetName(), byName) && byName && byName.get() == obj.get();
                // A parameterless, non-static getter-looking function (Get*/Is*/Has*), never an event (On*): the
                // script's own functions first, then its parents' (Quest has IsRunning/GetStage). Reads only.
                for (const auto* t = ti; t && tgt->fn == "-"; t = t->GetParent()) {
                    for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
                        const auto& f = t->GetMemberFuncIter()[i].func;
                        if (!f || f->GetIsStatic() || f->GetParamCount() != 0) continue;
                        const std::string name = f->GetName().c_str();
                        auto starts = [&](const char* pre) { return _strnicmp(name.c_str(), pre, std::string_view(pre).size()) == 0; };
                        if (starts("On") || !(starts("Get") || starts("Is") || starts("Has"))) continue;
                        tgt->fn = name;
                        tgt->fnOwner = t->GetName();
                        tgt->fnNative = f->GetIsNative();
                        break;
                    }
                }
            }
            *out += std::format("script {} props={}: ", ti->GetName(), ti->GetNumProperties());
            for (std::uint32_t i = 0; i < ti->GetNumProperties() && i < 8; ++i) {
                const auto& p = ti->GetPropertyIter()[i];
                auto* val = obj->GetProperty(p.name);
                *out += std::format("{}:{{{}}}={} ", p.name.c_str(), TI(p.info.type), val ? TI(val->GetType()) : std::string("null"));
                ++*props;
            }
        }
        finish("ok");
    }, std::chrono::seconds(10));
    // Dispatch the picked function on the attached script both ways (each dispatch is its own main-thread task).
    std::string s1 = "NOT_RUN", s2 = "NOT_RUN";
    std::shared_ptr<Captured> viaObj, viaName;
    if (r == "ok" && tgt->obj && tgt->fn != "-") {
        viaObj = CaptureCall([tgt](VMT* vm, Cb& cb) {
            return vm->DispatchMethodCall(tgt->obj, RE::BSFixedString(tgt->fn), NoArgs(), cb);   // Object& overload
        }, s1);
        viaName = CaptureCall([tgt](VMT* vm, Cb& cb) {
            return vm->DispatchMethodCall(tgt->h, RE::BSFixedString(tgt->cls), RE::BSFixedString(tgt->fn), NoArgs(), cb);  // handle + script class
        }, s2);
    }
    auto shape = [](const std::shared_ptr<Captured>& c) {
        if (!c || !c->have) return std::string("-");
        std::lock_guard l(c->mx);
        return c->v.IsInt() ? std::format("int:{}", c->v.GetSInt()) : c->v.IsBool() ? std::format("bool:{}", c->v.GetBool())
             : c->v.IsFloat() ? std::format("float:{}", c->v.GetFloat()) : std::string(c->v.IsObject() ? "object" : "other");
    };
    const bool dispatchOk = s1 == "got" && s2 == "got";
    const bool pass = r == "ok" && *props > 0 && tgt->boundByName && dispatchOk;
    return std::format("S7c {} {} {}| attached_script={} bound_by_class_name={} fn={} declared_on={} native={} "
                       "object_overload={} result={} class_name_overload={} result={} dispatch_ok={}  "
                       "(pass an explicit script name as 's7c <Script>' if the quest has none with properties)",
        pass ? "PASS" : "FAIL", r, *out, tgt->cls.empty() ? "-" : tgt->cls, tgt->boundByName, tgt->fn, tgt->fnOwner,
        tgt->fnNative, s1, shape(viaObj), s2, shape(viaName), dispatchOk);
}

// ---- S7d: ResolveTarget: FindBoundObject / PackHandle on a form the VM has not bound yet
std::string S7d()
{
    auto out = std::make_shared<std::string>();
    auto r = OnMainThread([=](std::function<void(std::string)> finish) {
        auto* vm = VM();
        auto* policy = vm->GetObjectHandlePolicy();
        // Collect candidate ids under the forms lock, then release it BEFORE touching the VM (no lock held across PackHandle).
        std::vector<RE::FormID> ids;
        {
            const auto& [map, lock] = RE::TESForm::GetAllForms();
            RE::BSReadLockGuard g(lock);
            for (auto& [id, form] : *map) {
                if (form && form->Is(RE::FormType::Reference)) ids.push_back(id);
                if (ids.size() >= 400) break;
            }
        }
        // Warm baseline and the mismatch case on the player.
        if (auto* player = RE::PlayerCharacter::GetSingleton()) {
            const auto ph = policy->GetHandleForObject(static_cast<RE::VMTypeID>(player->GetFormType()), player);
            RE::BSTSmartPointer<RE::BSScript::Object> b1, b2;
            const bool asActor = vm->FindBoundObject(ph, "Actor", b1) && b1;
            const bool asQuest = vm->FindBoundObject(ph, "Quest", b2) && b2;
            *out += std::format("player: FindBoundObject(Actor)={} FindBoundObject(Quest)={} (Quest must be false: that is the type_mismatch path); ", asActor, asQuest);
        }
        int cold = 0;
        for (auto id : ids) {
            auto* ref = RE::TESForm::LookupByID<RE::TESObjectREFR>(id);
            if (!ref) continue;
            const auto type = static_cast<RE::VMTypeID>(ref->GetFormType());
            const auto h = policy->GetHandleForObject(type, ref);
            if (h == policy->EmptyHandle()) continue;
            RE::BSTSmartPointer<RE::BSScript::Object> before;
            if (vm->FindBoundObject(h, "ObjectReference", before) && before) continue;  // already bound: not cold
            ++cold;
            RE::BSScript::Variable v;
            RE::BSScript::PackHandle(&v, ref, type);
            auto obj = v.GetObject();
            RE::BSTSmartPointer<RE::BSScript::Object> after;
            const bool found = vm->FindBoundObject(h, "ObjectReference", after) && after;
            *out += std::format("cold form {:08X}: FindBoundObject-before=false PackHandle->object={} class={} FindBoundObject-after={} same_object={}",
                id, obj ? 1 : 0, (obj && obj->GetTypeInfo()) ? obj->GetTypeInfo()->GetName() : "-", found, found && obj && after.get() == obj.get());
            return finish(found && obj ? "PASS" : "FAIL");
        }
        *out += std::format("no cold reference found among {} candidates", ids.size());
        finish("INCONCLUSIVE");
    }, std::chrono::seconds(20));
    return "S7d " + r + " " + *out;
}

// ---- S7e: DispatchMethodCall(BSTSmartPointer<Object>&, fn, args, cb) versus the handle+className overload (S4's)
std::string S7e()
{
    struct Holder { RE::BSTSmartPointer<RE::BSScript::Object> obj; RE::VMHandle h = 0; };
    auto held = std::make_shared<Holder>();
    auto r0 = OnMainThread([held](std::function<void(std::string)> finish) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        const auto type = static_cast<RE::VMTypeID>(player->GetFormType());
        held->h = VM()->GetObjectHandlePolicy()->GetHandleForObject(type, player);
        RE::BSScript::Variable v;
        RE::BSScript::PackHandle(&v, player, type);
        held->obj = v.GetObject();
        finish(held->obj ? "ok" : "NO_OBJECT");
    }, std::chrono::seconds(10));
    if (r0 != "ok") return "S7e FAIL could not get a bound Object for the player: " + r0;
    std::string s1, s2;
    auto viaObj = CaptureCall([held](VMT* vm, Cb& cb) {
        return vm->DispatchMethodCall(held->obj, RE::BSFixedString("GetPositionX"), NoArgs(), cb);   // Object& overload
    }, s1);
    auto viaHandle = CaptureCall([held](VMT* vm, Cb& cb) {
        return vm->DispatchMethodCall(held->h, RE::BSFixedString("ObjectReference"), RE::BSFixedString("GetPositionX"), NoArgs(), cb);
    }, s2);
    const bool ok = s1 == "got" && s2 == "got" && viaObj->v.IsFloat() && viaHandle->v.IsFloat() && viaObj->v.GetFloat() == viaHandle->v.GetFloat();
    return std::format("S7e {} object_overload={} x={} handle_overload={} x={}", ok ? "PASS" : "FAIL", s1,
        viaObj->have && viaObj->v.IsFloat() ? viaObj->v.GetFloat() : -1.0f, s2, viaHandle->have && viaHandle->v.IsFloat() ? viaHandle->v.GetFloat() : -1.0f);
}

// ---- S7f: alias kind detection: skyrim_cast<BGSRefAlias*> versus BGSBaseAlias::GetVMTypeID()
std::string S7f()
{
    auto pr = PickAlias(false), pl = PickAlias(true);
    auto out = std::make_shared<std::string>();
    auto pass = std::make_shared<bool>(true);
    OnMainThread([=](std::function<void(std::string)> finish) {
        for (const auto& [pick, isLoc] : { std::pair{ pr, false }, std::pair{ pl, true } }) {
            if (pick->quest == 0) { *out += isLoc ? "LocationAlias: none in this save; " : "ReferenceAlias: none in this save; "; continue; }
            auto* q = RE::TESForm::LookupByID<RE::TESQuest>(pick->quest);
            auto* base = q ? FindAliasBase(q, pick->alias) : nullptr;
            if (!base) { *out += "alias not found; "; *pass = false; continue; }
            const bool castRef = skyrim_cast<RE::BGSRefAlias*>(base) != nullptr, castLoc = skyrim_cast<RE::BGSLocAlias*>(base) != nullptr;
            const auto vmType = base->GetVMTypeID();  // delete this line and record "absent" if the pinned header has no GetVMTypeID
            const bool vmtypeRight = vmType == (isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID);
            *out += std::format("{}: cast_to_RefAlias={} cast_to_LocAlias={} GetVMTypeID={} expected_VMTYPEID={} GetVMTypeID_correct={}; ",
                isLoc ? "LocationAlias" : "ReferenceAlias", castRef, castLoc, static_cast<int>(vmType),
                static_cast<int>(isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID), vmtypeRight);
            if (castRef == isLoc || castLoc != isLoc) *pass = false;  // the skyrim_cast route must be right (GetVMTypeID is informational)
        }
        finish("ok");
    }, std::chrono::seconds(10));
    return std::format("S7f {} {}", *pass ? "PASS" : "FAIL", *out);
}

// ---- S7g: how an ALIAS returned by Papyrus is encoded (ObjectToJson). Quest.GetAlias(id) returns an Alias object.
std::string S7g()
{
    auto pick = PickAlias(false);
    if (pick->quest == 0) return "S7g NOT_RUN no running quest with a filled reference alias (run Task 6's S4 first)";
    const auto questId = pick->quest, aliasId = pick->alias;
    struct Ctx { RE::VMHandle qh = 0, aliasHandle = 0; std::uintptr_t refPtr = 0; };
    auto ctx = std::make_shared<Ctx>();
    auto r0 = OnMainThread([=](std::function<void(std::string)> finish) {
        auto* q = RE::TESForm::LookupByID<RE::TESQuest>(questId);
        auto* base = q ? FindAliasBase(q, aliasId) : nullptr;
        auto* ref = base ? skyrim_cast<RE::BGSRefAlias*>(base) : nullptr;
        if (!ref) return finish("NO_ALIAS");
        auto* policy = VM()->GetObjectHandlePolicy();
        ctx->qh = policy->GetHandleForObject(static_cast<RE::VMTypeID>(q->GetFormType()), q);
        ctx->aliasHandle = policy->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ref);
        ctx->refPtr = reinterpret_cast<std::uintptr_t>(ref);
        finish("ok");
    }, std::chrono::seconds(10));
    if (r0 != "ok") return "S7g FAIL " + r0;
    std::string st;
    auto cap = CaptureCall([ctx, aliasId](VMT* vm, Cb& cb) {
        std::vector<RE::BSScript::Variable> v(1);
        v[0].SetSInt(static_cast<std::int32_t>(aliasId));
        return vm->DispatchMethodCall(ctx->qh, RE::BSFixedString("Quest"), RE::BSFixedString("GetAlias"), new CountedArgs(std::move(v)), cb);
    }, st);
    if (st != "got" || !cap->v.IsObject() || !cap->v.GetObject()) return "S7g FAIL Quest.GetAlias returned no object: " + st;
    auto out = std::make_shared<std::string>();
    auto pass = std::make_shared<bool>(false);
    auto obj = cap->v.GetObject();
    OnMainThread([=](std::function<void(std::string)> finish) {
        auto* policy = VM()->GetObjectHandlePolicy();
        const auto h2 = obj->GetHandle();
        const auto low = static_cast<RE::FormID>(h2 & 0xFFFFFFFF);
        auto* asForm = RE::TESForm::LookupByID(low);
        // Task 28's ObjectToJson checks this FIRST: if true for an alias handle, an alias result comes back as a Quest.
        const bool naiveSelectsForm = asForm && policy->HandleIsType(static_cast<RE::VMTypeID>(asForm->GetFormType()), h2);
        const bool isRefAlias = policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, h2);
        auto* viaPolicy = isRefAlias ? policy->GetObjectForHandle(RE::BGSRefAlias::VMTYPEID, h2) : nullptr;
        auto* viaResolve = obj->Resolve(RE::BGSRefAlias::VMTYPEID);
        *out = std::format("alias_handle_from_policy={:016X} handle_from_result={:016X} equal={} low32={:08X} quest={:08X} low32_is_quest={} "
                           "naive_form_check_selects_a_form={} HandleIsType(RefAlias)={} GetObjectForHandle_matches={} Object::Resolve_matches={} script_class={}",
            ctx->aliasHandle, h2, ctx->aliasHandle == h2, low, questId, low == questId, naiveSelectsForm, isRefAlias,
            reinterpret_cast<std::uintptr_t>(viaPolicy) == ctx->refPtr, reinterpret_cast<std::uintptr_t>(viaResolve) == ctx->refPtr,
            obj->GetTypeInfo() ? obj->GetTypeInfo()->GetName() : "-");
        *pass = isRefAlias && (reinterpret_cast<std::uintptr_t>(viaPolicy) == ctx->refPtr || reinterpret_cast<std::uintptr_t>(viaResolve) == ctx->refPtr);
        finish("ok");
    }, std::chrono::seconds(10));
    return std::format("S7g {} {}  (PASS = the alias is recoverable; naive_form_check_selects_a_form=1 means Task 28 must test the alias handle types BEFORE the form lookup)",
        *pass ? "PASS" : "FAIL", *out);
}

// ---- S7h: object-array round trip. Utility.CreateFormArray(3, player) -> Form[3]; send a built Form[2] to ResizeFormArray.
std::string S7h()
{
    std::string st;
    auto cap = CaptureCall([](VMT* vm, Cb& cb) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("CreateFormArray"),
            RE::MakeFunctionArguments(static_cast<std::int32_t>(3), std::move(player)), cb);
    }, st);
    if (st != "got") return "S7h FAIL Utility.CreateFormArray: " + st + " (the function may be absent: see S7a)";
    auto arrFromGame = cap->v.GetArray();
    if (!arrFromGame) return "S7h FAIL CreateFormArray did not return an array";

    // What does the engine's own array say about its element type, and does the formula reproduce it?
    // Array::type_info() is the ELEMENT type (Form: a class pointer, bit 0 clear). The formula maps an ARRAY type
    // to its element type, so it is applied to the array value's own type (Form[]: class pointer | 1) and its
    // result is compared by raw value only (never passed to TI() or CreateArray).
    auto info = std::make_shared<std::string>();
    auto formulaWorks = std::make_shared<bool>(false);
    OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSScript::TypeInfo arrType;
        {
            std::lock_guard l(cap->mx);
            arrType = cap->v.GetType();
        }
        const auto& real = arrFromGame->type_info();
        const auto formulaRaw = static_cast<std::uintptr_t>(ElementTypeFormula(arrType).GetRawType());
        const auto realRaw = static_cast<std::uintptr_t>(real.GetRawType());
        *formulaWorks = arrType.IsObjectArray() && formulaRaw == realRaw;
        *info = std::format("real array: size={} array_type{{{}}} elem_type{{{}}}; ElementTypeFormula(array_type) raw={:#x} elem_type raw={:#x} formula_matches_real={}",
            arrFromGame->size(), TI(arrType), TI(real), formulaRaw, realRaw, *formulaWorks);
        finish("ok");
    }, std::chrono::seconds(10));

    // Build a Form[2] from a copy of the real array's element TypeInfo and send it to ResizeFormArray. The formula
    // gets no engine call: "formula_works" is only whether its result equals the real element type (above).
    std::string st2;
    auto cap2 = CaptureCall([arrFromGame](VMT* vm, Cb& cb) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        const auto ptype = static_cast<RE::VMTypeID>(player->GetFormType());
        const RE::BSScript::TypeInfo et = arrFromGame->type_info();
        RE::BSTSmartPointer<RE::BSScript::Array> mine;
        if (!vm->CreateArray(et, 2, mine) || !mine) return false;
        for (std::uint32_t i = 0; i < 2; ++i) RE::BSScript::PackHandle(&(*mine)[i], player, ptype);
        std::vector<RE::BSScript::Variable> v(3);
        v[0].SetArray(mine);
        v[1].SetSInt(4);
        RE::BSScript::PackHandle(&v[2], player, ptype);
        return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("ResizeFormArray"), new CountedArgs(std::move(v)), cb);
    }, st2);
    bool copiedWorks = false;
    std::string detail = st2;
    if (st2 == "got" && cap2->v.IsArray() && cap2->v.GetArray()) {
        auto a = cap2->v.GetArray();
        copiedWorks = a->size() == 4;
        detail = std::format("size={} (expected 4)", a->size());
    }
    return std::format("S7h {} {} | copied TypeInfo round trip: {}  copied_TypeInfo_works={} formula_works={}  (PASS = the copied-TypeInfo route works; M2 may then accept object-array arguments by copying a real array's type_info(); formula_works=false means the formula must not be used)",
        copiedWorks ? "PASS" : "FAIL", *info, detail, copiedWorks, *formulaWorks);
}
}
```
In `Handle` add:
```cpp
    if (line == "s7a") return S7a();
    if (line == "s7b") return S7b();
    if (line == "s7c" || line.rfind("s7c ", 0) == 0) return S7c(line.size() > 4 ? line.substr(4) : "");
    if (line == "s7d") return S7d();
    if (line == "s7e") return S7e();
    if (line == "s7f") return S7f();
    if (line == "s7g") return S7g();
    if (line == "s7h") return S7h();
```
Notes on the code above: it compiled and linked on the build box against the pinned commit (`API-VERIFICATION.md`, Part 2, with the `emplace_back(s.get())` and three-argument `FindBoundObject` fixes already applied), but it has never run in the game. Wherever the pinned headers disagree with a call (names, constness, the `AttachedScript` element shape, `GetReturnType`, `Array::operator[]`, `Variable::IsArray`), **change the call to match the header and note the difference in `docs/PINS.md`**: a header mismatch is itself a finding for Tasks 27-30. The `ElementTypeFormula` function is deliberately the first dispatcher draft's own formula, so S7h compares it with the copied-`TypeInfo` route.

- [ ] **Step 2: Build, deploy, relaunch, run** (throwaway save loaded, unpaused, no menu; Task 6's `s4` must already have passed in this same session type so `PickAlias` finds a filled alias)

```bash
# Only the spike DLL changes; the Python runtime and SpikePyCore.dll deployed by Task 3 stay where they are.
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game (qqq); relaunch; wait for the listener line as in Task 4 Step 2; load the throwaway save; then:
for t in s7a s7b s7c s7d s7e s7f s7g s7h; do python3 spikes/spike_client.py "$t" | tee -a spikes/s7-output.txt; done
```
(`spikes/s7-output.txt` is scratch evidence; paste the relevant lines into `RESULTS.md`; do not commit the scratch file unless the owner wants it.) Run the loop twice: the second pass shows warm-versus-cold differences in S7d/S7e (a form that was cold the first time is bound the second). Then quit to the main menu without saving.

- [ ] **Step 3: Pass criteria, and exactly which Task 27-30 code each result changes**

| Sub-test | PASS means | If it FAILS, change |
|---|---|---|
| S7a | `Utility.RandomInt` found as a global with 2 named parameters; `Actor.Enable` found as a member through the parent chain; `Actor.RandomInt` not found as a member. `GetParam` names are the `.psc` names. | Task 27: `sig::FindFunction`, `sig::BindArgs` (the iterators `GetMemberFuncIter`/`GetGlobalFuncIter`, `GetParam`); the "global used as member" error path |
| S7b | The `Actor` chain ends at `Form`; an unknown script is not found. Note whether lowercase `actor` is found. | Task 27 `sig::TypeOf` and Task 28 `sig::IsA`: canonicalise the script name first if lowercase is NOT_FOUND |
| S7c | Properties enumerate by name with types, and `Object::GetProperty(name)` returns a `Variable*` for a live object. **And** the quest's first attached script (a mod's own script, spec 4.3) resolves by its own class name: `bound_by_class_name=true` (`FindBoundObject(h, "<AttachedScript>")` returns the same `Object`), and the picked parameterless getter (`fn=`, `declared_on=`) dispatches through both overloads: `dispatch_ok=true`. Record `fn`, `declared_on` and both results. | Task 28 `sig::FindProperty`; Task 30 `GetProperty`/`SetProperty` (use `VirtualMachine::GetPropertyValue`/`SetPropertyValue` with the smart pointer if `Object::GetProperty` does not see the property). `bound_by_class_name=false` or `dispatch_ok=false`: Task 29 `ResolveTarget`/`CallMethod` with a non-native script class (skyrim-dm's `SetupContract` path) must change before Task 29 starts: STOP and ask the owner if neither overload dispatches |
| S7d | The real `FindBoundObject` signature compiles as written in Step 0; a cold form reports `FindBoundObject-before=false`, then `PackHandle` produces a non-null object and `FindBoundObject-after=true`. The Quest lookup on the player is false. | Task 28 `FindBound`/`BindObject` and Task 29 `ResolveTarget`: the `FindBoundObject` call (argument list as found in the header), and the `PackHandle` fallback. If `PackHandle` yields no object on a cold form, dispatch through the handle+className overload (S4's) for native classes instead of `v.GetObject()`. |
| S7e | The `Object&` overload and the handle+className overload return the same `GetPositionX`. | Task 29 `DispatchMethod`/`CallMethod`: dispatch with the handle+className overload (the S4-proven one) if the Object overload fails |
| S7f | `skyrim_cast<BGSRefAlias*>` is non-null only for reference aliases and `skyrim_cast<BGSLocAlias*>` only for location aliases. Record whether `GetVMTypeID()` agrees. | Task 29 `ResolveAlias`, `DescribeAlias`, `ResolveTarget`, and Task 28's `ToVariable` alias branch: keep the `skyrim_cast` route (never `GetVMTypeID()` if it disagrees) |
| S7g | The alias returned by `Quest.GetAlias` is recoverable through `HandleIsType(BGSRefAlias::VMTYPEID, h)` plus `GetObjectForHandle` (or `Object::Resolve`). Record `naive_form_check_selects_a_form`. | Task 28 `ObjectToJson`: test the alias handle types first when the naive form check selects a form (otherwise an alias comes back as its Quest); Task 29's `test_alias_method_calls` must check an alias result, not only a Quest |
| S7h | An object array built from a copy of the real array's `type_info()` (the element type) round-trips through `ResizeFormArray` (size 4). Record `formula_matches_real` (the first draft's formula applied to the array's own type, compared by raw value; it is never sent to the engine). | Nothing in M1: Task 28 rejects object-array arguments as `unsupported` and only reads object-array results. Record the answer for the M2 plan. |

The `FindBoundObject` row also depends on Step 0: record the signature you found. **A FAIL in S7a, S7d or S7e affects the core of method calls: fix Tasks 27-29 as listed before starting them; if a fix is not one of the listed changes, STOP and ask the owner.**

- [ ] **Step 4: Record, teardown listing, commit**

Fill the S7 row in `spikes/RESULTS.md`: date, PASS/FAIL per sub-test (for example `a PASS, b PASS, c PASS, d PASS, e PASS, f PASS, g PASS, h FAIL`), the evidence lines, and the Task 27-30 changes to make in the last column. M0 is complete only after Task 10 (S10); Task 10's last step reviews every row and lists what was deployed for teardown.
```bash
git add spikes/spike-dll spikes/RESULTS.md docs/PINS.md
git commit -m "test(spike): S7 function lookup, bound-object dispatch, alias handles and object arrays"
```
