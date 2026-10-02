#pragma once
#include "spy/config.h"
#include <filesystem>
#include <optional>
#include <string>

namespace spy::settings {
// Reads Data/SKSE/Plugins/Skython.ini (the loader's folder, the parent of the core's). Missing file: defaults.
// Invalid value: defaults (bridge off) and an error in the log.
void Load();
const Config& Get();
std::filesystem::path OwnDir();      // Data/SKSE/Plugins/Skython (folder of SkythonCore.dll)
std::filesystem::path PluginsDir();  // Data/SKSE/Plugins
// The ini's session_file if set, else <skseDir>/Skython/session.json; nullopt when neither exists.
std::optional<std::filesystem::path> SessionFile(const std::optional<std::filesystem::path>& skseDir);

// Facts only CommonLib can read, stored by main.cpp so that CommonLib-free files (PythonHost.cpp) can use them.
struct Runtime {
    std::string plugin_version, game_version, runtime;  // "0.1.0", "1.6.1170.0", "AE"
    std::optional<std::filesystem::path> skse_dir;      // Documents/My Games/<game>/SKSE
};
void SetRuntime(Runtime);
const Runtime& GetRuntime();
}
