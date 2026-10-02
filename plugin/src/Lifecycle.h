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
bool Interactive();
bool Paused();
std::uint32_t Epoch();
std::uint32_t LoadsStarted();
// nullopt when interactive; else "starting", "loading", "no_game", then "paused" only when require_unpaused.
std::optional<std::string> NotReadyReason(bool require_unpaused = false);
std::vector<std::string> OpenMenus();
json BaseState();  // LifecycleState::base_state(): data_ready, game_loaded, loading, interactive, paused, menus, epoch
}
