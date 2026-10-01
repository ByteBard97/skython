#pragma once
#include <cstdint>
#include <functional>
#include <string>

namespace spike {
void Log(const std::string& line);  // defined in main.cpp
// Line-based TCP listener on 127.0.0.1:port. Each received line is passed to handler
// on the listener thread; handler's return value (plus '\n') is sent back.
// A handler that throws produces an "ERR exception: ..." reply; it never reaches std::terminate.
void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler);
}
