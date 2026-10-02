#pragma once
#include "spy/errors.h"  // spy::json
#include <SKSE/SKSE.h>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace spy::life {
void OnMessage(SKSE::MessagingInterface::Message* m);  // from the core's messaging listener
bool DataReady();
bool GameLoaded();
bool Loading();
// True from kPreLoadGame or kNewGame until that load or new game is over (the interactive signal; it is cleared
// right before the game_loaded push) or a failed kPostLoadGame. Never true for a door, fast-travel or coc
// loading screen. The event hub's `loading` source (Events.cpp).
bool SaveLoadInProgress() noexcept;
bool Interactive();
bool Paused();
std::uint32_t Epoch();
std::uint32_t LoadsStarted();
// nullopt when interactive; else "starting", "loading", "no_game", then "paused" only when require_unpaused.
std::optional<std::string> NotReadyReason(bool require_unpaused = false);
std::vector<std::string> OpenMenus();
json BaseState();  // LifecycleState::base_state(): data_ready, game_loaded, loading, interactive, paused, menus, epoch
}
