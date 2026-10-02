#include "Events.h"
#include "Log.h"
#include "PapyrusValues.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>

namespace spy::events {
namespace {
using Ctl = RE::BSEventNotifyControl;

struct DeathSink final : RE::BSTEventSink<RE::TESDeathEvent>
{
    Ctl ProcessEvent(const RE::TESDeathEvent* e, RE::BSTEventSource<RE::TESDeathEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->dead ? "death" : "dying";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "victim", FormRefJson(e->actorDying.get()) }, { "killer", FormRefJson(e->actorKiller.get()) } });
        } catch (const std::exception& x) { NoteSinkError("death", x.what()); } catch (...) { NoteSinkError("death", "unknown"); }
        return Ctl::kContinue;
    }
};

struct QuestStageSink final : RE::BSTEventSink<RE::TESQuestStageEvent>
{
    Ctl ProcessEvent(const RE::TESQuestStageEvent* e, RE::BSTEventSource<RE::TESQuestStageEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("quest_stage")) return Ctl::kContinue;
            NoteFirstEvent("quest_stage");
            Push("quest_stage", json{ { "quest", FormRefJson(RE::TESForm::LookupByID(e->formID)) },
                                      { "stage", static_cast<int>(e->stage) } });
        } catch (const std::exception& x) { NoteSinkError("quest_stage", x.what()); } catch (...) { NoteSinkError("quest_stage", "unknown"); }
        return Ctl::kContinue;
    }
};

struct QuestStartStopSink final : RE::BSTEventSink<RE::TESQuestStartStopEvent>
{
    Ctl ProcessEvent(const RE::TESQuestStartStopEvent* e, RE::BSTEventSource<RE::TESQuestStartStopEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->started ? "quest_start" : "quest_stop";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "quest", FormRefJson(RE::TESForm::LookupByID(e->formID)) } });
        } catch (const std::exception& x) { NoteSinkError("quest_start_stop", x.what()); } catch (...) { NoteSinkError("quest_start_stop", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ModSink final : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    Ctl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("mod_event")) return Ctl::kContinue;
            NoteFirstEvent("mod_event");
            const char* name = e->eventName.c_str();
            const char* str = e->strArg.c_str();
            Push("mod_event", json{ { "name", name ? name : "" }, { "str_arg", str ? str : "" },
                                    { "num_arg", pv::FloatToJson(e->numArg) }, { "sender", FormRefJson(e->sender) } });
        } catch (const std::exception& x) { NoteSinkError("mod_event", x.what()); } catch (...) { NoteSinkError("mod_event", "unknown"); }
        return Ctl::kContinue;
    }
};
}

void RegisterBatch1()
{
    static auto& death = *new DeathSink();  // immortal: the engine keeps the pointer for the life of the process
    static auto& stage = *new QuestStageSink();
    static auto& startStop = *new QuestStartStopSink();
    static auto& mod = *new ModSink();
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* modSource = SKSE::GetModCallbackEventSource();
    if (!holder || !modSource) {
        log::Error(std::string("events: batch 1 not registered: ") + (!holder ? "ScriptEventSourceHolder" : "ModCallbackEvent source") + " is null");
        return;
    }
    // Known and ready to attach; each is added inside an SKSE task while one of its events has a subscriber.
    Sinks().push_back({ "death", { "death", "dying" }, [holder] { holder->AddEventSink<RE::TESDeathEvent>(&death); },
                        [holder] { holder->RemoveEventSink<RE::TESDeathEvent>(&death); } });
    Sinks().push_back({ "quest_stage", { "quest_stage" }, [holder] { holder->AddEventSink<RE::TESQuestStageEvent>(&stage); },
                        [holder] { holder->RemoveEventSink<RE::TESQuestStageEvent>(&stage); } });
    Sinks().push_back({ "quest_start_stop", { "quest_start", "quest_stop" },
                        [holder] { holder->AddEventSink<RE::TESQuestStartStopEvent>(&startStop); },
                        [holder] { holder->RemoveEventSink<RE::TESQuestStartStopEvent>(&startStop); } });
    Sinks().push_back({ "mod_event", { "mod_event" }, [modSource] { modSource->AddEventSink(&mod); },
                        [modSource] { modSource->RemoveEventSink(&mod); } });
    log::Info("events: sinks registered (batch 1)");
}
}
