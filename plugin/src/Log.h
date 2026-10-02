#pragma once
#include <memory>
#include <string>

namespace spdlog { class logger; }

// Callable from translation units that must not include CommonLib (PythonHost.cpp, pymodule.cpp).
// Safe before Set(): lines are then discarded.
namespace spy::log {
void Set(std::shared_ptr<spdlog::logger> logger);  // kept in a leaked shared_ptr: threads may log during process exit
void Info(const std::string& line);
void Warn(const std::string& line);
void Error(const std::string& line);
void Debug(const std::string& line);
}
