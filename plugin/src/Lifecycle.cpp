#include "Lifecycle.h"
#include "ConsoleEcho.h"
#include "Events.h"
#include "Log.h"
#include "Ops.h"
#include "SaveLoad.h"
#include "spy/lifecycle_state.h"
#include <RE/Skyrim.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <format>
#include <thread>

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
// game_loaded waits for the interactive transition (S6: kPostLoadGame comes before the Loading Menu closes).
// 0 = nothing pending, 1 = a save load succeeded, 2 = a new game started, 3 = the world-start gate started a world
// (Task 48b: a console coc from the main menu, which raises neither kPostLoadGame nor kNewGame).
std::atomic_int g_pendingLoaded{ 0 };

// Task 48b (R70): the world-start check. While core's gate is open, one SKSE task at a time checks that the player
// has a parent cell and 3D. A task never re-queues itself (Global Constraints; SKSE drains its queue until it is
// empty, so it would spin): if the player is not in the world yet, a short-lived thread queues the next check
// kWorldCheckDelay later, at most kWorldCheckAttempts times per gate opening. Any later menu event also queues one.
constexpr auto kWorldCheckDelay = std::chrono::milliseconds(100);
constexpr int kWorldCheckAttempts = 100;  // about 10 s
std::atomic_bool g_worldCheckQueued{ false };
std::atomic_int g_worldCheckMisses{ 0 };
void QueueWorldCheck() noexcept;

void NoteInteractive()
{
    const bool now = State().interactive();
    if (g_lastInteractive.exchange(now) == now) return;
    log::Info(std::string("lifecycle: interactive = ") + (now ? "true" : "false") + ", epoch " + std::to_string(State().epoch()));
    if (!now) return;
    const int pending = g_pendingLoaded.exchange(0);
    g_saveLoadInProgress.store(false);  // R60: the save load / new game ends here, right before game_loaded is pushed
    if (pending == 0) return;
    // No lifecycle lock is held here (Lifecycle.cpp has none): Push takes only the hub's mutex.
    // A gate start (3) has no save behind it either, so it reports new_game true; it raises no new_game event (that
    // event is kNewGame's, and SKSE sends kNewGame only for the CharGen quest).
    events::Push("game_loaded", json{ { "new_game", pending != 1 } });  // the hub stamps the new epoch
    log::Info("events: game_loaded epoch " + std::to_string(State().epoch()));
}

void CheckWorld()
{
    g_worldCheckQueued.store(false);
    if (!State().world_start_pending()) {
        g_worldCheckMisses.store(0);
        return;
    }
    auto* pc = RE::PlayerCharacter::GetSingleton();
    auto* cell = pc ? pc->GetParentCell() : nullptr;
    const bool has3D = pc && pc->Get3D() != nullptr;
    // In the world = a parent cell and 3D (DevBench's playerLoaded is the 3D test; it is false at the main menu).
    if (!cell || !has3D) {
        const int misses = ++g_worldCheckMisses;
        if (misses == 1)
            log::Info(std::format("lifecycle: world-start gate open, player not in the world yet (cell {}, 3D {}); retrying",
                cell ? "yes" : "no", has3D ? "yes" : "no"));
        if (misses >= kWorldCheckAttempts) {
            log::Warn("lifecycle: world-start gate open but the player is not in the world after " + std::to_string(misses) +
                      " checks; waiting for the next menu event");
            g_worldCheckMisses.store(0);
            return;
        }
        std::thread([] {
            std::this_thread::sleep_for(kWorldCheckDelay);
            QueueWorldCheck();
        }).detach();
        return;
    }
    g_worldCheckMisses.store(0);
    // Pending first, then the state change (as for kNewGame): a racing menu event's NoteInteractive then pushes it.
    g_pendingLoaded.store(3);
    if (!State().on_player_in_world()) {  // a load, a new game or a menu got there first
        int expected = 3;
        g_pendingLoaded.compare_exchange_strong(expected, 0);
        return;
    }
    log::Info(std::format("lifecycle: world started without kNewGame or a save load (console coc from the main menu); "
                          "player cell {:08X}, epoch {}",
        cell->GetFormID(), State().epoch()));
    NoteInteractive();
}

void QueueWorldCheck() noexcept
{
    try {
        if (!State().world_start_pending() || g_worldCheckQueued.exchange(true)) return;
        SKSE::GetTaskInterface()->AddTask([] {
            ops::InTask in;
            ops::Measured measured;
            try {
                CheckWorld();
            } catch (...) {
                g_worldCheckQueued.store(false);
                log::Error("lifecycle: world-start check threw");
            }
        });
    } catch (...) {
        g_worldCheckQueued.store(false);
    }
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
            else if (name == RE::MainMenu::MENU_NAME && e->opening) {
                log::Info("lifecycle: Main Menu opened; no game loaded");
                console::FlushHeld();  // queues one SKSE task for lines held before the console existed (Task 43)
            } else if (name == RE::MainMenu::MENU_NAME)
                log::Info("lifecycle: Main Menu closed");
            NoteInteractive();
            QueueWorldCheck();  // Task 48b: no-op unless the world-start gate is open
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
            console::FlushHeld();
        }
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame:
        g_pendingLoaded.store(0);
        g_saveLoadInProgress.store(true);  // Task 39 (R60)
        State().on_pre_load_game();
        log::Info("lifecycle: loading a save");
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        const bool ok = reinterpret_cast<std::uintptr_t>(m->data) != 0;  // a packed bool, not a pointer
        g_pendingLoaded.store(ok ? 1 : 0);
        if (!ok) g_saveLoadInProgress.store(false);  // Task 39 (R60): a failed load raises no game_loaded
        State().on_post_load_game(ok);
        log::Info(std::string("lifecycle: save load ") + (ok ? "succeeded" : "FAILED") + ", epoch " + std::to_string(State().epoch()));
        break;
    }
    case SKSE::MessagingInterface::kNewGame:
        g_pendingLoaded.store(2);
        g_saveLoadInProgress.store(true);  // Task 39 (R60)
        if (!State().on_new_game()) {
            // The world-start gate already started this world and pushed its game_loaded (Task 48b): undo.
            int expected = 2;
            g_pendingLoaded.compare_exchange_strong(expected, 0);
            g_saveLoadInProgress.store(false);
            log::Info("lifecycle: kNewGame after the world-start gate started this world; no second game_loaded");
        }
        events::Push("new_game", json::object());
        log::Info("lifecycle: new game, epoch " + std::to_string(State().epoch()));
        break;
    case SKSE::MessagingInterface::kSaveGame: {
        // SKSE sends the save's name as data (dataLen bytes, maybe NUL-terminated). Logged raw once per launch so the
        // RESULTS entry can confirm the payload (Task 41 matches pending saves on it).
        std::string name;
        if (m->data && m->dataLen > 0) name.assign(static_cast<const char*>(m->data), strnlen(static_cast<const char*>(m->data), m->dataLen));
        static std::atomic_bool logged{ false };
        if (!logged.exchange(true)) log::Info("lifecycle: kSaveGame data '" + name + "' (" + std::to_string(m->dataLen) + " bytes)");
        events::Push("game_saved", json{ { "name", name } });
        saveload::OnSaveGame(name);
        break;
    }
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
