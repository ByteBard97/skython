#pragma once
#include "spy/dispatcher.h"
#include "spy/fake_backend.h"
#include <functional>
#include <string>
#include <pybind11/pybind11.h>

namespace spy {
// What the _skython module talks to. The game build (plugin core, Tasks 26-27) and the Linux fake build
// (pyglue/linux/fake_module.cpp) each fill one in and call InstallModuleHost before the module is imported.
struct ModuleHost
{
    Dispatcher* dispatcher = nullptr;
    const char* backend = "game";                                               // _skython.BACKEND
    std::function<std::string()> config_json;                                   // _skython.config()
    std::function<void(const std::string& level, const std::string& msg)> log;  // _skython.log()
    FakeBackend* fake = nullptr;  // set only by the fake build: adds the _fake_* controls
    EventHub* events = nullptr;  // open_consumer / close_consumer / next_events; null: no events in this build
    std::function<void(const std::string& message)> console_echo;  // _skython.console_echo(); unset = no-op (Task 43)
    std::function<std::string(int n)> probe_sink_cycles;  // game build with trace_timing only (Task 44); unset = absent
};

void InstallModuleHost(ModuleHost host);
// Defines every _skython function on `m`. Only strings, numbers and None cross the boundary; every call that can
// wait releases the GIL; a C++ exception reaches Python as a Python exception (ValueError, TypeError, RuntimeError).
void DefineModule(pybind11::module_& m);
}
