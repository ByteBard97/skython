#pragma once
#include <string>

namespace spy::host {
// Py_GetVersion() of the linked python314.dll (safe before Py_Initialize). Proves the core's import resolved.
std::string LinkedPythonVersion();
}
