#pragma once
#include <string>

namespace spy::console {
// At most one line per second with a burst of 5; extra lines are summarised. Each line is printed inside one
// AddTask. Never blocks, never throws.
void Echo(std::string message);
// A line printed before the console exists (a script failing at kDataLoaded, before the main menu) is held (at most 5);
// Lifecycle's Main Menu sink calls this once the main menu opens, and one AddTask prints the held lines. Never throws.
void FlushHeld() noexcept;
}
