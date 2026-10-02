#include "Events.h"
#include "Log.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>

namespace spy::events {
namespace {
using Ctl = RE::BSEventNotifyControl;

json ById(RE::FormID id) { return id == 0 ? json(nullptr) : FormRefJson(RE::TESForm::LookupByID(id)); }

struct HitSink final : RE::BSTEventSink<RE::TESHitEvent>
{
    Ctl ProcessEvent(const RE::TESHitEvent* e, RE::BSTEventSource<RE::TESHitEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("hit")) return Ctl::kContinue;
            NoteFirstEvent("hit");
            using F = RE::TESHitEvent::Flag;
            Push("hit", json{ { "target", FormRefJson(e->target.get()) }, { "aggressor", FormRefJson(e->cause.get()) },
                              { "source", ById(e->source) }, { "projectile", ById(e->projectile) },
                              { "power_attack", e->flags.any(F::kPowerAttack) }, { "sneak_attack", e->flags.any(F::kSneakAttack) },
                              { "bash_attack", e->flags.any(F::kBashAttack) }, { "blocked", e->flags.any(F::kHitBlocked) } });
        } catch (const std::exception& x) { NoteSinkError("hit", x.what()); } catch (...) { NoteSinkError("hit", "unknown"); }
        return Ctl::kContinue;
    }
};

struct EquipSink final : RE::BSTEventSink<RE::TESEquipEvent>
{
    Ctl ProcessEvent(const RE::TESEquipEvent* e, RE::BSTEventSource<RE::TESEquipEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->equipped ? "equip" : "unequip";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "actor", FormRefJson(e->actor.get()) }, { "item", ById(e->baseObject) },
                             { "reference", ById(e->originalRefr) } });
        } catch (const std::exception& x) { NoteSinkError("equip", x.what()); } catch (...) { NoteSinkError("equip", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ContainerSink final : RE::BSTEventSink<RE::TESContainerChangedEvent>
{
    Ctl ProcessEvent(const RE::TESContainerChangedEvent* e, RE::BSTEventSource<RE::TESContainerChangedEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("container_changed")) return Ctl::kContinue;
            NoteFirstEvent("container_changed");
            const auto ref = e->reference.get();  // handle lookup (handle-manager lock); null when the handle is empty
            Push("container_changed", json{ { "old_container", ById(e->oldContainer) }, { "new_container", ById(e->newContainer) },
                                            { "item", ById(e->baseObj) }, { "count", static_cast<int>(e->itemCount) },
                                            { "reference", FormRefJson(ref.get()) } });
        } catch (const std::exception& x) { NoteSinkError("container_changed", x.what()); } catch (...) { NoteSinkError("container_changed", "unknown"); }
        return Ctl::kContinue;
    }
};

struct CellSink final : RE::BSTEventSink<RE::TESCellAttachDetachEvent>
{
    Ctl ProcessEvent(const RE::TESCellAttachDetachEvent* e, RE::BSTEventSource<RE::TESCellAttachDetachEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->attached ? "cell_attach" : "cell_detach";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            Push(name, json{ { "reference", FormRefJson(e->reference.get()) } });
        } catch (const std::exception& x) { NoteSinkError("cell", x.what()); } catch (...) { NoteSinkError("cell", "unknown"); }
        return Ctl::kContinue;
    }
};

struct ActivateSink final : RE::BSTEventSink<RE::TESActivateEvent>
{
    Ctl ProcessEvent(const RE::TESActivateEvent* e, RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("activate")) return Ctl::kContinue;
            NoteFirstEvent("activate");
            Push("activate", json{ { "target", FormRefJson(e->objectActivated.get()) }, { "activator", FormRefJson(e->actionRef.get()) } });
        } catch (const std::exception& x) { NoteSinkError("activate", x.what()); } catch (...) { NoteSinkError("activate", "unknown"); }
        return Ctl::kContinue;
    }
};

struct LocationSink final : RE::BSTEventSink<RE::TESActorLocationChangeEvent>
{
    Ctl ProcessEvent(const RE::TESActorLocationChangeEvent* e, RE::BSTEventSource<RE::TESActorLocationChangeEvent>*) override
    {
        try {
            if (!e || !Hub().Wants("player_location")) return Ctl::kContinue;
            if (e->actor.get() != RE::PlayerCharacter::GetSingleton()) return Ctl::kContinue;
            NoteFirstEvent("player_location");
            Push("player_location", json{ { "old_location", FormRefJson(e->oldLoc) }, { "new_location", FormRefJson(e->newLoc) } });
        } catch (const std::exception& x) { NoteSinkError("player_location", x.what()); } catch (...) { NoteSinkError("player_location", "unknown"); }
        return Ctl::kContinue;
    }
};

struct MenuEventSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    Ctl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        try {
            if (!e) return Ctl::kContinue;
            const char* name = e->opening ? "menu_open" : "menu_close";
            if (!Hub().Wants(name)) return Ctl::kContinue;
            NoteFirstEvent(name);
            const char* menu = e->menuName.c_str();
            Push(name, json{ { "menu", menu ? menu : "" } });
        } catch (const std::exception& x) { NoteSinkError("menu", x.what()); } catch (...) { NoteSinkError("menu", "unknown"); }
        return Ctl::kContinue;
    }
};
}

void RegisterWorldSinks()
{
    static auto& hit = *new HitSink();  // immortal, as in batch 1
    static auto& equip = *new EquipSink();
    static auto& container = *new ContainerSink();
    static auto& cell = *new CellSink();
    static auto& activate = *new ActivateSink();
    static auto& location = *new LocationSink();
    static auto& menu = *new MenuEventSink();
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* ui = RE::UI::GetSingleton();
    if (!holder || !ui) {
        log::Error(std::string("events: batch 2 not registered: ") + (!holder ? "ScriptEventSourceHolder" : "RE::UI") + " is null");
        return;
    }
    // Known and ready to attach; each is added inside an SKSE task while one of its events has a subscriber.
    Sinks().push_back({ "hit", { "hit" }, [holder] { holder->AddEventSink<RE::TESHitEvent>(&hit); },
                        [holder] { holder->RemoveEventSink<RE::TESHitEvent>(&hit); } });
    Sinks().push_back({ "equip", { "equip", "unequip" }, [holder] { holder->AddEventSink<RE::TESEquipEvent>(&equip); },
                        [holder] { holder->RemoveEventSink<RE::TESEquipEvent>(&equip); } });
    Sinks().push_back({ "container_changed", { "container_changed" },
                        [holder] { holder->AddEventSink<RE::TESContainerChangedEvent>(&container); },
                        [holder] { holder->RemoveEventSink<RE::TESContainerChangedEvent>(&container); } });
    Sinks().push_back({ "cell", { "cell_attach", "cell_detach" },
                        [holder] { holder->AddEventSink<RE::TESCellAttachDetachEvent>(&cell); },
                        [holder] { holder->RemoveEventSink<RE::TESCellAttachDetachEvent>(&cell); } });
    Sinks().push_back({ "activate", { "activate" }, [holder] { holder->AddEventSink<RE::TESActivateEvent>(&activate); },
                        [holder] { holder->RemoveEventSink<RE::TESActivateEvent>(&activate); } });
    Sinks().push_back({ "player_location", { "player_location" },
                        [holder] { holder->AddEventSink<RE::TESActorLocationChangeEvent>(&location); },
                        [holder] { holder->RemoveEventSink<RE::TESActorLocationChangeEvent>(&location); } });
    // Its own sink: the lifecycle's MenuSink keeps its single job and stays registered.
    Sinks().push_back({ "menu", { "menu_open", "menu_close" }, [ui] { ui->AddEventSink<RE::MenuOpenCloseEvent>(&menu); },
                        [ui] { ui->RemoveEventSink<RE::MenuOpenCloseEvent>(&menu); } });
    log::Info("events: sinks registered (batch 2)");
}
}
