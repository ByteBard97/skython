#include <Python.h>
#include "PythonHost.h"

namespace spy::host {
std::string LinkedPythonVersion()
{
    const std::string v = Py_GetVersion();      // "3.14.8 (tags/v3.14.8:..., ...) [MSC v.19xx 64 bit (AMD64)]"
    return v.substr(0, v.find(' '));
}
}
