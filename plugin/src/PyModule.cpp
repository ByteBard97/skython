#include <pybind11/pybind11.h>
#include "spy/pymodule.h"

// The game build of _skython. PythonHost.cpp registers PyInit__skython with PyImport_AppendInittab before
// Py_InitializeFromConfig; nothing here runs at DLL load time.
PYBIND11_MODULE(_skython, m) { spy::DefineModule(m); }
