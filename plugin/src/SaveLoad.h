#pragma once
#include "spy/dispatcher.h"
#include <filesystem>
#include <string>
#include <string_view>

// game.save / game.load (Contracts). Run is called on the submitting thread. game.save requests the save through SKSE's
// Papyrus Game.SaveGame from one AddTask (R63); game.load calls the engine's load in one AddUITask (spike S6).
namespace spy::saveload {
void Run(const std::string& op, const json& args, spy::Done done, spy::Cancelled cancelled);
void OnSaveGame(std::string_view name);  // from life::OnMessage on kSaveGame; completes the pending game.save of that name
std::filesystem::path SaveDir();         // read once (first call at kDataLoaded), logged
}
