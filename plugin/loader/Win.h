#pragma once
#include <string>

namespace spy::loader {
using LogFn = void (*)(const std::string&);
// Loads python314.dll and SkythonCore.dll by full path from <this DLL's folder>\Skython\ and calls
// SpyCore_Load(skse). Returns "" on success, else the reason Python stays disabled. Never throws.
std::string StartCore(const void* skseLoadInterface, LogFn log) noexcept;
// False only when <this DLL's folder>\<name> is known to be absent (Address Library check); true when it exists or
// when the folder cannot be resolved (StartCore then reports that). `name` is ASCII. Never throws.
bool PluginsFileExists(const std::string& name) noexcept;
// Test hook (SPY_TEST_HOOKS builds only; always false otherwise): SKYTHON_TEST_UNVERIFIED_RUNTIME=1 makes the
// loader treat the running game as an unverified runtime. Logs one TEST HOOK line. Never throws.
bool TestUnverifiedRuntime(LogFn log) noexcept;
}
