#include "Ops.h"
#include "Lifecycle.h"
#include "Log.h"
#include "PapyrusValues.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <format>
#include <memory>
#include <mutex>
#include <utility>

namespace spy::ops {
namespace {
using RE::BSScript::Variable;

// No stored "main thread" id (R49): SKSE tasks run on varying BSJobs worker threads, so an id comparison is meaningless.

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

// Arguments for one dispatch. The VM never frees them (spike S2, Task 4): the Callback owns this object and deletes
// it after reading the result. On a false Dispatch*Call return it is kept (R37), see KeptOnRefusal.
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
    } catch (const std::exception& e) {
        log::Error(std::string("ops: converting a result threw: ") + e.what());
        done(OpResult::Err(ErrorCode::internal, "internal error converting the result"));
    } catch (...) {
        log::Error("ops: converting a result threw an unknown exception");
        done(OpResult::Err(ErrorCode::internal, "internal error converting the result"));
    }
}

// A refused dispatch (Dispatch*Call returned false): the args are NOT freed, because the callback might still fire
// (unmeasured, R37); if it does, it frees them. With FindFunction and BindArgs first, a refusal is not expected.
void KeptOnRefusal(const std::string& qualified)
{
    static std::once_flag once;
    std::call_once(once, [&] {
        log::Warn("ops: the VM refused a dispatch (" + qualified + "); its arguments are kept, not freed (R37). "
                  "Logged once; later refusals leak silently.");
    });
}

class Callback final : public RE::BSScript::IStackCallbackFunctor
{
public:
    Callback(Done d, DynArgs* args) : done_(std::move(d)), args_(args) {}
    void operator()(Variable result) override
    {
        // This call's arguments (S2: the VM never frees them). Freed when this scope ends, after the result was read
        // and converted or queued; `result` is our own copy. The destructor does not free them (see Known limits).
        std::unique_ptr<DynArgs> owned{std::exchange(args_, nullptr)};
        // Primitives and None touch no engine state: convert here, on whatever thread the VM called us on.
        // Objects (forms, aliases, arrays of them) need LookupByID/GetFile, so they are converted inside an SKSE task:
        // here if the VM called us synchronously from inside one of our tasks (t_inTask), else one more AddTask. Never
        // test the thread id instead: tasks run on varying job threads (R49). R52.
        if (!pv::NeedsMainThread(result) || t_inTask) return Convert(done_, result);
        auto done = done_;
        SKSE::GetTaskInterface()->AddTask([done, result] { ops::InTask in; Convert(done, result); });
    }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}
    // Never written into a save (as SkyrimPlatform does): a save during a latent call does not record this functor,
    // and after loading that save the call ends NotReady or Timeout (Task 31 tests it). R38.
    bool CanSave() const override { return false; }

private:
    Done done_;
    DynArgs* args_;  // owned; freed in operator()
};

// The view from inside the task; life::Paused() is what the Dispatcher saw at submit.
bool PausedNow()
{
    auto* ui = RE::UI::GetSingleton();
    return ui && ui->GameIsPaused();
}

OpResult PausedError() { return OpResult::Err(ErrorCode::not_ready, "the game is paused", WithGameState({{"reason", "paused"}})); }

void CallStatic(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && PausedNow()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto fn = sig::FindFunction(c.script, c.function, false);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    auto* args = new DynArgs(std::move(vars));
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done, args)};  // cb owns args
    if (!VM()->DispatchStaticCall(RE::BSFixedString(c.script), RE::BSFixedString(c.function), args, cb)) {
        KeptOnRefusal(qualified);  // never `delete args` here (R37)
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified));
    }
}

json Resolve(const json& args)
{
    RE::TESForm* form = nullptr;
    if (args.contains("editor_id")) {
        if (!args["editor_id"].is_string()) throw ProtocolError(ErrorCode::bad_request, "editor_id must be a string");
        const auto eid = args["editor_id"].get<std::string>();
        // Verified at the pinned commit: LookupByEditorID tests its map pointer and returns nullptr, so no guard is
        // needed. The game keeps editor ids for only some form types: quests resolve without an editor-id-caching mod
        // (live finding, Task 29: CWCampaign), most other types need one (powerofthree's Tweaks), so null is common.
        form = RE::TESForm::LookupByEditorID(eid);
        if (!form)
            throw ProtocolError(ErrorCode::not_found, "no form with editor id " + eid +
                " (the game keeps editor ids for only some form types unless powerofthree's Tweaks is installed)");
        if (form->IsDeleted()) throw ProtocolError(ErrorCode::not_found, "form with editor id " + eid + " is deleted");  // as ResolveForm
    } else {
        json ref = args;
        ref["kind"] = "form";
        form = pv::ResolveForm(form_ref_from_json(ref));
    }
    return form_ref_to_json(pv::DescribeForm(form));
}

void CallMethod(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && PausedNow()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    // Function first, object second: a global called as a member must be bad_request ("use papyrus.call_static"),
    // not the type_mismatch that resolving the object would raise.
    auto fn = sig::FindFunction(c.script, c.function, true);
    auto target = pv::ResolveTarget(*c.object, c.script);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    auto* args = new DynArgs(std::move(vars));
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done, args)};  // cb owns args (S2)
    if (!VM()->DispatchMethodCall(target.object, RE::BSFixedString(c.function), args, cb)) {
        KeptOnRefusal(qualified);  // never `delete args` here (R37); same rule as CallStatic
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified + " on " + target.desc));
    }
}

// v1 serves auto properties only. Their value is the property's auto variable in the bound script object, read and
// written here directly (Object::variables). The plan's route, the VM's own GetPropertyValue, returned true and left
// the value None for every auto property it read on the box (Task 30, launch 1: CWCampaignScript.currentAttackDelta,
// an Int, read None), so it is not used; nor SetPropertyValue, its vtable neighbour. Object::GetProperty is not used
// either: at the pin it indexes variables[] with the declaring class's own index and ignores the parents' variables.
// A full property (Papyrus get/set code) is refused: its code would not run here. Nothing is synchronised with Papyrus
// threads, and OnPropertyChanged does not fire (see the limits in Task 30).
struct AutoProperty
{
    RE::BSScript::TypeInfo type;
    std::uint32_t index;      // into Object::variables (parents' variables first)
    std::string variableName; // the auto variable's name, for the one-time cross-check below
};

AutoProperty FindAutoProperty(const PropertyArgs& p)
{
    const auto slot = sig::FindPropertySlot(p.script, p.property);  // clear not_found before touching the object
    if (slot.info.getFunction || slot.info.setFunction)
        throw ProtocolError(ErrorCode::unsupported, p.script + "." + p.property + " is a full property (it has Papyrus "
            "get/set code); v1 reads and writes auto properties only: call a Papyrus function on the script instead");
    return {slot.info.type, slot.variableIndex, slot.variableName};
}

// The auto variable inside the bound object. The object's own type may extend `p.script`; its extra variables come
// after the declaring class's, so the index holds for it too. Range-checked against the object's own layout.
RE::BSScript::Variable& BackingVariable(const AutoProperty& a, const pv::Target& target, const PropertyArgs& p)
{
    auto* obj = target.object.get();
    const auto* type = obj ? obj->GetTypeInfo() : nullptr;
    if (!type || a.index >= type->GetTotalNumVariables())
        throw ProtocolError(ErrorCode::vm_error, "the script object of " + target.desc + " has no storage for " + p.script + "." + p.property);
    return obj->variables[a.index];
}

// Once per launch: the direct read's slot next to the slot CommonLib's Object::GetVariable finds by the auto
// variable's name (it applies the parent offset its own way), so the log shows whether the two layouts agree.
// (Task 30 launch 2 also logged the VM's GetPropertyValue here: true with a None value; no longer called.)
void LogPropertySlot(const AutoProperty& a, const pv::Target& target, const PropertyArgs& p, const Variable& slot)
{
    static std::once_flag once;
    std::call_once(once, [&] {
        const auto* byName = a.variableName.empty() ? nullptr : target.object->GetVariable(RE::BSFixedString(a.variableName));
        log::Info(std::format("ops: property slot for {}.{} on {}: index={} variable={} raw_type={:X} name_route={} initialized={}",
            p.script, p.property, target.desc, a.index, a.variableName, static_cast<std::uintptr_t>(slot.GetType().GetRawType()),
            !byName ? "not found" : (byName == &slot ? "same slot" : "DIFFERENT slot"), target.object->IsInitialized()));
    });
}

json GetProperty(const PropertyArgs& p)
{
    const auto a = FindAutoProperty(p);
    auto target = pv::ResolveTarget(p.object, p.script);
    const auto& slot = BackingVariable(a, target, p);
    LogPropertySlot(a, target, p, slot);
    return pv::FromVariable(slot);  // inside this SKSE task (Run), so an object value is converted here
}

void SetProperty(const PropertyArgs& p)
{
    const auto a = FindAutoProperty(p);
    auto target = pv::ResolveTarget(p.object, p.script);
    auto& slot = BackingVariable(a, target, p);
    Variable in;
    pv::ToVariable(*p.value, a.type, p.property + " (property of " + p.script + ")", in);  // type_mismatch before any write
    slot = in;
}

struct BatchState
{
    BatchArgs args;
    std::size_t next = 0;
    json results = json::array();
    Done done;
    Cancelled cancelled;
    Progress progress;
};
// A step's `done` can run on a VM thread (primitive results are converted there) or inside an SKSE task. BatchState is
// safe by ordering, not by thread: the next step is only queued from the previous step's `done`, so at most one step
// is in flight and each access happens-before the next (the AddTask queue hand-off). No single "main thread" touches
// it: SKSE tasks run on varying job threads (R49).
// unmeasured: whether a task queued during a drain runs in the same drain; Task 31's meter (with the batch-step wrap)
// shows it.

void BatchStep(const std::shared_ptr<BatchState>& st);

void QueueBatchStep(const std::shared_ptr<BatchState>& st)
{
    SKSE::GetTaskInterface()->AddTask([st] { ops::InTask in; BatchStep(st); });  // one task per step (spec 4.5)
}

void BatchStep(const std::shared_ptr<BatchState>& st)
{
    try {
        if (st->cancelled()) return;
        if (st->next == st->args.calls.size()) return st->done(OpResult::Ok({{"results", st->results}}));
        const auto step = st->args.calls[st->next++];
        Run(step.op, step.args, [st](OpResult r) {
            st->results.push_back(r.ok ? json{{"ok", true}, {"result", r.result}}
                                       : json{{"ok", false}, {"error", {{"code", to_string(r.code)}, {"message", r.message}, {"detail", r.detail}}}});
            // Tell the Dispatcher (Task 18) how far we got, so a timeout can report it (the steps' side effects already happened).
            if (st->progress) st->progress(json{{"completed", st->results.size()}, {"results", st->results}});
            if (!r.ok && st->args.stop_on_error) return st->done(OpResult::Ok({{"results", st->results}}));
            QueueBatchStep(st);
        }, st->cancelled, Progress{});
    } catch (const std::exception& e) {
        log::Error(std::string("ops: batch step threw: ") + e.what());
        st->done(OpResult::Err(ErrorCode::internal, "internal error in a batch step"));
    } catch (...) {  // a main-thread task never lets an exception escape (Global Constraints)
        log::Error("ops: batch step threw an unknown exception");
        st->done(OpResult::Err(ErrorCode::internal, "internal error in a batch step"));
    }
}
}

void AddStateExtras(json& s)
{
    if (!life::GameLoaded() || life::Loading()) return;  // recheck: a load may have started since submit
    if (auto* player = RE::PlayerCharacter::GetSingleton()) s["player"] = form_ref_to_json(pv::DescribeForm(player));
    if (auto* cal = RE::Calendar::GetSingleton()) s["game_days_passed"] = pv::FloatToJson(cal->GetDaysPassed());  // 32-bit float
}

json WithGameState(json detail)
{
    detail["paused"] = life::Paused();
    detail["menus"] = life::OpenMenus();
    return detail;
}

OpResult NotReady(const std::string& reason)
{
    return OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", WithGameState({{"reason", reason}}));
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress)
{
    try {
        if (auto why = life::NotReadyReason(false)) return done(NotReady(*why));  // recheck inside the task
        if (op == "papyrus.call_static") return CallStatic(parse_call_args(args, false), done);
        if (op == "form.resolve") return done(OpResult::Ok(Resolve(args)));
        if (op == "papyrus.call_method") return CallMethod(parse_call_args(args, true), done);
        if (op == "papyrus.get_property") return done(OpResult::Ok(GetProperty(parse_property_args(args, false))));
        if (op == "papyrus.set_property") {
            SetProperty(parse_property_args(args, true));
            return done(OpResult::Ok(nullptr));
        }
        if (op == "papyrus.batch") {
            auto st = std::make_shared<BatchState>();
            st->args = parse_batch_args(args);  // refuses non-batchable steps (nested batches, game.state): bad_request
            st->done = std::move(done);
            st->cancelled = std::move(cancelled);
            st->progress = std::move(progress);
            return BatchStep(st);
        }
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    } catch (const std::exception& e) {
        log::Error("ops: " + op + " threw: " + e.what());
        done(OpResult::Err(ErrorCode::internal, "internal error"));
    } catch (...) {
        log::Error("ops: " + op + " threw an unknown exception");
        done(OpResult::Err(ErrorCode::internal, "internal error"));
    }
}
}
