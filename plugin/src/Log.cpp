#include "Log.h"
#include <spdlog/spdlog.h>

namespace spy::log {
namespace {
std::shared_ptr<spdlog::logger>* g_logger = nullptr;  // leaked on purpose
}
void Set(std::shared_ptr<spdlog::logger> logger) { g_logger = new std::shared_ptr<spdlog::logger>(std::move(logger)); }
void Info(const std::string& line) { if (g_logger) (*g_logger)->info("{}", line); }
void Warn(const std::string& line) { if (g_logger) (*g_logger)->warn("{}", line); }
void Error(const std::string& line) { if (g_logger) (*g_logger)->error("{}", line); }
void Debug(const std::string& line) { if (g_logger) (*g_logger)->debug("{}", line); }
}
