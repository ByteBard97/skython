#include "Ops.h"
#include "Lifecycle.h"
#include "Log.h"
#include "PapyrusValues.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
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
        // needed. Editor ids are only populated when an editor-id-caching mod is installed, so null is common.
        form = RE::TESForm::LookupByEditorID(eid);
        if (!form)
            throw ProtocolError(ErrorCode::not_found, "no form with editor id " + eid +
                " (the game keeps editor ids for only some form types unless powerofthree's Tweaks is installed)");
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
        // Task 30 adds properties and batch.
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
    (void)cancelled;  // used by papyrus.batch (Task 30)
    (void)progress;
}
}
