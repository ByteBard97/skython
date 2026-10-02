#include "Lifecycle.h"
#include "Log.h"
#include "spy/lifecycle_state.h"
#include <RE/Skyrim.h>
#include <atomic>

namespace spy::life {
namespace {
// Immortal: read from Python threads (through the Dispatcher) that can outlive static destruction.
// Task 17's default menu names are "Loading Menu" and "Main Menu" (RE::LoadingMenu/MainMenu::MENU_NAME).
LifecycleState& State()
{
    static LifecycleState& s = *new LifecycleState();
    return s;
}

std::atomic_bool g_lastInteractive{ false };
std::atomic_bool g_saveLoadInProgress{ false };  // Task 39 (R60): see SaveLoadInProgress in Lifecycle.h

void NoteInteractive()
{
    const bool now = State().interactive();
    if (g_lastInteractive.exchange(now) == now) return;
    log::Info(std::string("lifecycle: interactive = ") + (now ? "true" : "false") + ", epoch " + std::to_string(State().epoch()));
    if (now) g_saveLoadInProgress.store(false);  // the save load or new game is over (Task 40 moves this to game_loaded)
}

struct MenuSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        try {
            if (!e) return RE::BSEventNotifyControl::kContinue;
            const std::string name = e->menuName.c_str();
            State().on_menu(name, e->opening);
            // Task 31's menu test checks this is not stale (GameIsPaused may update after the event returns).
            if (auto* ui = RE::UI::GetSingleton()) State().set_paused(ui->GameIsPaused());
            if (name == RE::LoadingMenu::MENU_NAME)
                log::Info(std::string("lifecycle: Loading Menu ") + (e->opening ? "opened" : "closed"));
            else if (name == RE::MainMenu::MENU_NAME && e->opening)
                log::Info("lifecycle: Main Menu opened; no game loaded");
            NoteInteractive();
        } catch (...) {
            log::Error("lifecycle: menu sink threw");
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kDataLoaded: {
        static MenuSink& sink = *new MenuSink();
        auto* ui = RE::UI::GetSingleton();
        if (ui) ui->AddEventSink<RE::MenuOpenCloseEvent>(&sink);
        State().on_data_loaded();
        log::Info("Skython core: data loaded");
        if (!ui) {
            log::Error("lifecycle: RE::UI singleton is null at kDataLoaded; menu events will not be tracked");
            break;
        }
        // The sink sees only events from now on. If the main menu opened before kDataLoaded, record it here
        // (on_menu is idempotent, so a sink event for the same opening does no harm).
        if (ui->IsMenuOpen(RE::MainMenu::MENU_NAME)) {
            State().on_menu(std::string(RE::MainMenu::MENU_NAME), true);
            State().set_paused(ui->GameIsPaused());
            log::Info("lifecycle: Main Menu opened; no game loaded (already open at kDataLoaded)");
        }
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame:
        g_saveLoadInProgress.store(true);
        State().on_pre_load_game();
        log::Info("lifecycle: loading a save");
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        const bool ok = reinterpret_cast<std::uintptr_t>(m->data) != 0;  // a packed bool, not a pointer
        if (!ok) g_saveLoadInProgress.store(false);  // a failed load raises no interactive signal of its own
        State().on_post_load_game(ok);
        log::Info(std::string("lifecycle: save load ") + (ok ? "succeeded" : "FAILED") + ", epoch " + std::to_string(State().epoch()));
        break;
    }
    case SKSE::MessagingInterface::kNewGame:
        g_saveLoadInProgress.store(true);
        State().on_new_game();
        log::Info("lifecycle: new game, epoch " + std::to_string(State().epoch()));
        break;
    default:
        return;
    }
    NoteInteractive();
}

bool DataReady() { return State().data_ready(); }
bool GameLoaded() { return State().game_loaded(); }
bool Loading() { return State().loading(); }
bool Interactive() { return State().interactive(); }
bool Paused() { return State().paused(); }
std::uint32_t Epoch() { return State().epoch(); }
std::uint32_t LoadsStarted() { return State().loads_started(); }
std::optional<std::string> NotReadyReason(bool require_unpaused) { return State().not_ready_reason(require_unpaused); }
std::vector<std::string> OpenMenus() { return State().open_menus(); }
json BaseState() { return State().base_state(); }
bool SaveLoadInProgress() noexcept { return g_saveLoadInProgress.load(); }
}
