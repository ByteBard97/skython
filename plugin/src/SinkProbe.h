#pragma once
#include <string>

namespace spy::probe {
// Task 44 (dev only, trace_timing). n > 0: n cycles of registering and unregistering the spare TESActivateEvent and
// MenuOpenCloseEvent sinks, each step inside its own SKSE task; ends with both unregistered. n == -1: register the spare
// activate sink and zero its counters (arm). n == 0: report the counters since the arm and unregister it (finish).
// Called on a Python thread with the GIL released; blocks that thread only.
std::string SinkCycles(int n);
}
