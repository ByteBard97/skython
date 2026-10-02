#pragma once
#include "spy/config.h"
#include <string>

namespace spy::host {
std::string LinkedPythonVersion();  // Py_GetVersion() of the linked python314.dll; safe before init
void Start(const Config& cfg);      // at kDataLoaded; starts the host thread and returns; never throws
std::string State();                // "disabled: <why>", "starting", "running", "failed: <why>"
}
