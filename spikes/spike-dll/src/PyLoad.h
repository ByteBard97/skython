#pragma once
#include <string>

// S8/S9 (Tasks 3 and 5): load python314.dll and SpikePyCore.dll by full path, then forward harness
// commands to SpikePyCore's exports. This header and PyLoad.cpp use no Python headers, so the spike
// DLL itself never imports python314.dll (spec 4.1, two DLLs).
namespace spike::py {
using CallerFn = int (*)(int timeout_ms, double* out);  // 0 ok, 1 timeout, 2 dispatch refused, 3 other

std::string Load();                          // harness: py_load
std::string Init(bool dataLoaded);           // harness: py_init (refused before kDataLoaded)
std::string Status();                        // harness: py_status
std::string Exec(const std::string& code);   // harness: py_exec <one line of Python>
void SetCaller(CallerFn fn);                 // Task 5: forwarded to SpikePy_SetCaller now or at load
bool AutostartRequested();                   // <Plugins>/SkyrimPython/spike-autostart exists
bool DisableRequested();                     // <Plugins>/SkyrimPython/spike-disable exists (R46 exit baseline)
}
