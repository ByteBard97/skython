# Task 3: Spike S8: two-DLL load and CPython embedded in the game; Python socket from Linux

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **File Structure**, **Contracts** and **Fixed paths** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.1, 4.5 and 9 (spike S8). New in this plan (no counterpart on branch `plan-out-of-process`). Steps use checkbox (`- [ ]`) syntax.

> **M0 spike rules:** spike code is throwaway, written for speed and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S8 decides the whole design: if it FAILS, STOP and ask the owner.** The fallback is the out-of-process design on git branch `plan-out-of-process`; do not start Tasks 18-32, do not edit the spec, and do not improvise a workaround. Also STOP if the game does not reach the log line within 10 minutes, the build box does not answer, the runtime is not AE 1.6.1170, or `tools/fetch_python.sh --check` fails.

> **Deploy safety:** the harness port 45978 is unauthenticated and, after this task, runs **arbitrary Python** in the game (`py_exec`). Use the throwaway save only (`spikebase`, Task 1), never save over a real playthrough while the spike is deployed, and remove nothing without the owner's sign-off on `tools/teardown_list.sh`'s list.

**What this proves (spec 9, S8):**
1. **Two-DLL load:** the spike DLL (no Python imports) loads `SkyrimPython/python/python314.dll` by full path, then `SkyrimPython/SpikePyCore.dll` (linked normally against `python314.lib`), and the core's import of `python314.dll` resolves to that same module.
2. **CPython initialises inside the game** under GE-Proton, on a host thread started after `kDataLoaded`, with the isolated `PyConfig` from the Global Constraints, and imports `json, ssl, socket, sqlite3, threading, ctypes`.
3. **A Linux process reaches a Python socket inside the game:** a `socketserver` started by Python in the game answers 1000 round trips, a 1 MiB line and 4 parallel connections from Linux.
4. The game keeps running (with Python threads alive) and **exits cleanly** with no `Py_Finalize`.

**Files:**
- Create: `spikes/spike-pycore/CMakeLists.txt`, `spikes/spike-pycore/build.ps1`, `spikes/spike-pycore/src/PyCore.cpp`, `spikes/spike-pycore/linux_check.cpp`
- Create: `spikes/spike-dll/src/PyLoad.h`, `spikes/spike-dll/src/PyLoad.cpp`
- Modify: `spikes/spike-dll/CMakeLists.txt` (one source added), `spikes/spike-dll/src/main.cpp` (one include, one flag, four commands, autostart: load in `SKSEPluginLoad`, init at `kDataLoaded`), `tools/deploy.sh` (`--python` mode)
- Create: `spikes/py_send.py`, `spikes/py_echo_client.py`, `spikes/py/s8_imports.py`, `spikes/py/echo_server.py`
- Modify: `spikes/RESULTS.md` (S8 row)

**Interfaces:**
- Consumes: Task 0 (`third_party/cpython/{embed,nuget}`, `tools/fetch_python.sh --check`); Task 1 (`tools/remote_build.sh` syncing `third_party/cpython/nuget`, `tools/deploy.sh`, `tools/launch_game.sh`, `tools/wait_for_log.sh`, `tools/env.sh`, `spike::Log`, `spike::Handle`, the `OnMessage` listener in `main.cpp`); Task 2 (`spikes/spike_client.py`, `spikes/RESULTS.md`, S1 PASS). Nothing from Task 4.
- Produces (Task 5 and Task 10 rely on these):
  - `SpikePyCore.dll` exports (C ABI): `void SpikePy_SetLog(void (*)(const char*))`, `void SpikePy_SetCaller(int (*)(int timeout_ms, double* out))`, `int SpikePy_Start(const wchar_t* home, const wchar_t* const* paths, int n)` (1 started, 0 already started; returns at once), `int SpikePy_Status(char*, int)` (`not_started`, `starting`, `running`, `failed: ...`), `int SpikePy_Run(const char* code, char* out, int cap)` (`OK [result]` or `ERR <traceback>`), `int SpikePy_PythonModule(wchar_t*, int, void** module)`.
  - The built-in module `_spike` inside the game's interpreter: `log(msg)`, `call_rt(timeout_ms=5000) -> float` (GIL released while waiting; raises `RuntimeError` until Task 5 registers the caller), `native_tid() -> int`.
  - `spike::py::{Load, Init, Status, Exec, SetCaller, AutostartRequested}` and `using spike::py::CallerFn = int (*)(int, double*)` (`PyLoad.h`).
  - Harness commands: `py_load`, `py_init`, `py_status`, `py_exec <one line of Python>`. `py_exec` runs in one persistent namespace (`__main__`); a variable named `result` is returned.
  - `spikes/py_send.py FILE.py | -c CODE` (exit 0 on `OK`), `spikes/py_echo_client.py [soak SEC]`, `tools/deploy.sh --python [out/<core>.dll]`.
  - Marker file `Data/SKSE/Plugins/SkyrimPython/spike-autostart`: when present, the spike runs `py_load` itself in `SKSEPluginLoad` (plugin-load time) and `py_init` at `kDataLoaded`.
  - Findings for Tasks 25-26: whether the two-DLL load works under Proton; CPython's `sys.path`, `sys.executable`, OpenSSL and SQLite versions in the game; and the rule that the host thread blocks on a **leaked** condition variable (Step 2 shows why a static one hangs process exit).

- [ ] **Step 0: Check the gate and the inputs**

```bash
grep -E '^\| S1 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S1 PASS row: STOP and ask the owner"; false; }
tools/fetch_python.sh --check
ls third_party/cpython/nuget/tools/include/Python.h third_party/cpython/nuget/tools/libs/python314.lib \
   third_party/cpython/embed/python314.dll third_party/cpython/embed/python314.zip third_party/cpython/embed/LICENSE.txt
uv python find 3.14
```
Expected: the S1 row, the fetch check passing, five paths listed, and a 3.14 interpreter path. If `fetch_python.sh --check` fails, STOP and ask the owner (never fetch "a close version").

- [ ] **Step 1: Write the spike core DLL**

`spikes/spike-pycore/CMakeLists.txt` (no vcpkg, no CommonLib: this DLL knows nothing about the game):
```cmake
cmake_minimum_required(VERSION 3.21)
project(SpikePyCore VERSION 0.0.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(PY_NUGET "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/cpython/nuget/tools")
if(NOT EXISTS "${PY_NUGET}/include/Python.h" OR NOT EXISTS "${PY_NUGET}/libs/python314.lib")
  message(FATAL_ERROR "missing ${PY_NUGET}/include or libs/python314.lib: run tools/fetch_python.sh on Linux; tools/remote_build.sh syncs it")
endif()
add_library(SpikePyCore SHARED src/PyCore.cpp)
target_include_directories(SpikePyCore PRIVATE "${PY_NUGET}/include")
# pyconfig.h also asks for python314.lib with #pragma comment(lib); the directory satisfies it.
target_link_directories(SpikePyCore PRIVATE "${PY_NUGET}/libs")
target_link_libraries(SpikePyCore PRIVATE "${PY_NUGET}/libs/python314.lib")
target_compile_definitions(SpikePyCore PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
set_property(TARGET SpikePyCore PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
```

`spikes/spike-pycore/build.ps1` (always Release: a Debug build would ask for `python314_d.lib`, which the NuGet package does not ship):
```powershell
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot; $build = Join-Path $src "build"
cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build $build --config Release --parallel
if ($LASTEXITCODE -ne 0) { throw "build failed" }
```

`spikes/spike-pycore/src/PyCore.cpp`. The `#ifdef _WIN32` lines exist only so Step 2 can compile and run the same file on Linux; the game build takes the `_WIN32` side. The bootstrap Python (the raw string `kBootstrap`) sends `print` and tracebacks to the spike log and defines `_spike_exec`, which turns every exception, including `SystemExit`, into an `ERR` reply instead of letting it reach the interpreter's top level.
```cpp
// SpikePyCore.dll: throwaway M0 spike code (S8, S9). Links python314.lib normally; no CommonLib, no game headers.
// Loaded by SkyrimPythonSpike.dll AFTER python314.dll has been loaded by full path (spec 4.1, two DLLs).
// Python runs only on the host thread this file starts and on threads Python creates; the game's threads
// reach Python only through SpikePy_Run (harness thread, PyGILState) and never from the main or VM thread.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#ifdef _WIN32
#include <windows.h>
#define SPIKE_EXPORT extern "C" __declspec(dllexport)
#else  // Linux check build only (Step 2): same code against uv's CPython 3.14
#include <pthread.h>
#define SPIKE_EXPORT extern "C" __attribute__((visibility("default")))
#endif

#include <atomic>
#include <condition_variable>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using SpikeLogFn = void (*)(const char* utf8);
using SpikeCallerFn = int (*)(int timeout_ms, double* out);  // 0 ok, 1 timeout, 2 dispatch refused, 3 other

namespace {
std::atomic<SpikeLogFn> g_log{ nullptr };
std::atomic<SpikeCallerFn> g_caller{ nullptr };
std::atomic_bool g_started{ false };
std::mutex g_statusMx;
std::string g_status = "not_started";

void Log(const std::string& s)
{
    if (auto f = g_log.load()) f(s.c_str());
}
void SetStatus(const std::string& s)
{
    {
        std::lock_guard l(g_statusMx);
        g_status = s;
    }
    Log("pycore: status " + s);
}
std::string Status()
{
    std::lock_guard l(g_statusMx);
    return g_status;
}
unsigned long NativeTid()
{
#ifdef _WIN32
    return GetCurrentThreadId();
#else
    return static_cast<unsigned long>(pthread_self());
#endif
}

// ---- the built-in module _spike (registered with PyImport_AppendInittab before init) ----
PyObject* SpikeLogPy(PyObject*, PyObject* args)
{
    const char* m = nullptr;
    if (!PyArg_ParseTuple(args, "s", &m)) return nullptr;
    Log(std::string("py: ") + m);
    Py_RETURN_NONE;
}
// S9: one round trip into the game. The GIL is released for the whole wait.
PyObject* SpikeCallRt(PyObject*, PyObject* args)
{
    int timeoutMs = 5000;
    if (!PyArg_ParseTuple(args, "|i", &timeoutMs)) return nullptr;
    SpikeCallerFn fn = g_caller.load();
    if (!fn) {
        PyErr_SetString(PyExc_RuntimeError, "no caller registered (send the harness command py_arm_rt first)");
        return nullptr;
    }
    double v = 0;
    int rc = 3;
    Py_BEGIN_ALLOW_THREADS
    rc = fn(timeoutMs, &v);
    Py_END_ALLOW_THREADS
    if (rc == 0) return PyFloat_FromDouble(v);
    if (rc == 1) {
        PyErr_SetString(PyExc_TimeoutError, "call_rt timed out");
        return nullptr;
    }
    PyErr_Format(PyExc_RuntimeError, "call_rt failed, rc=%d", rc);
    return nullptr;
}
PyObject* SpikeNativeTid(PyObject*, PyObject*) { return PyLong_FromUnsignedLong(NativeTid()); }

PyMethodDef kMethods[] = {
    { "log", SpikeLogPy, METH_VARARGS, "Write one line to the spike log." },
    { "call_rt", SpikeCallRt, METH_VARARGS, "Utility.GetCurrentRealTime through AddTask; GIL released while waiting." },
    { "native_tid", SpikeNativeTid, METH_NOARGS, "OS thread id of the caller." },
    { nullptr, nullptr, 0, nullptr },
};
PyModuleDef kModule = { PyModuleDef_HEAD_INIT, "_spike", "S8/S9 spike module", -1, kMethods, nullptr, nullptr, nullptr, nullptr };
PyObject* PyInit__spike() { return PyModule_Create(&kModule); }

// Runs in __main__ right after init: stdout/stderr to the spike log, and _spike_exec for py_exec.
const char* kBootstrap = R"PY(import sys, traceback, __main__
import _spike
class _SpikeLogWriter:
    def __init__(self, tag):
        self.tag, self.buf = tag, ""
    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            _spike.log(self.tag + line)
        return len(s)
    def flush(self):
        pass
sys.stdout = _SpikeLogWriter("stdout: ")
sys.stderr = _SpikeLogWriter("stderr: ")
def _spike_exec(src):
    ns = __main__.__dict__
    ns.pop("result", None)
    try:
        exec(compile(src, "<py_exec>", "exec"), ns)
        r = ns.pop("result", None)
        return "OK" if r is None else "OK " + str(r)
    except BaseException:
        return "ERR " + traceback.format_exc()
__main__._spike_exec = _spike_exec
_spike.log("python %s isolated=%d path=%r" % (sys.version.split()[0], sys.flags.isolated, sys.path))
)PY";

// Leaked on purpose: the host thread waits on these until the process ends, and destroying a condition
// variable that still has a waiter blocks (glibc) or is undefined behaviour (C++), e.g. in static destructors.
std::mutex& g_foreverMx = *new std::mutex;
std::condition_variable& g_foreverCv = *new std::condition_variable;

bool Check(PyStatus st, const char* what)
{
    if (!PyStatus_Exception(st)) return true;
    std::string m = std::string("failed: ") + what + ": " + (st.err_msg ? st.err_msg : "?");
    if (st.func) m += std::string(" (in ") + st.func + ")";
    // An "exit" status asks the embedder to end the process (Py_ExitStatusException). Never do that in a game.
    if (PyStatus_IsExit(st)) m += " exit status " + std::to_string(st.exitcode) + " ignored";
    SetStatus(m);
    return false;
}

void HostMain(std::wstring home, std::vector<std::wstring> paths)
{
    try {
        SetStatus("starting");
        Log("pycore: host thread " + std::to_string(NativeTid()) + ", headers " PY_VERSION ", runtime " + Py_GetVersion());
        if (PyImport_AppendInittab("_spike", &PyInit__spike) == -1) {
            SetStatus("failed: PyImport_AppendInittab");
            return;
        }
        PyConfig c;
        PyConfig_InitIsolatedConfig(&c);
        c.isolated = 1;
        c.use_environment = 0;
        c.install_signal_handlers = 0;
        c.site_import = 1;
        bool ok = Check(PyConfig_SetString(&c, &c.home, home.c_str()), "home") &&
                  Check(PyConfig_SetString(&c, &c.program_name, L"SkyrimPythonSpike"), "program_name");
        c.module_search_paths_set = 1;
        for (const auto& p : paths) ok = ok && Check(PyWideStringList_Append(&c.module_search_paths, p.c_str()), "module_search_paths");
        if (!ok) {
            PyConfig_Clear(&c);
            return;
        }
        PyStatus st = Py_InitializeFromConfig(&c);
        PyConfig_Clear(&c);
        if (!Check(st, "Py_InitializeFromConfig")) return;
        if (PyRun_SimpleString(kBootstrap) != 0) {
            PyEval_SaveThread();  // never leave this thread holding the GIL
            SetStatus("failed: bootstrap raised (traceback above in the log)");
            return;
        }
        PyEval_SaveThread();      // release the GIL for good: Python threads and SpikePy_Run take it as needed
        SetStatus("running");
        std::unique_lock l(g_foreverMx);
        g_foreverCv.wait(l, [] { return false; });  // block forever; process exit ends this thread. No Py_Finalize.
    } catch (...) {
        SetStatus("failed: C++ exception on the host thread");
    }
}

int CopyOut(const std::string& s, char* out, int cap)
{
    if (!out || cap <= 0) return 0;
    const int n = static_cast<int>(std::min<std::size_t>(s.size(), static_cast<std::size_t>(cap - 1)));
    std::memcpy(out, s.data(), static_cast<std::size_t>(n));
    out[n] = '\0';
    return n;
}
}  // namespace

SPIKE_EXPORT void SpikePy_SetLog(SpikeLogFn f) { g_log.store(f); }
SPIKE_EXPORT void SpikePy_SetCaller(SpikeCallerFn f) { g_caller.store(f); }

// Starts the host thread and returns at once. 1 = started, 0 = already started.
SPIKE_EXPORT int SpikePy_Start(const wchar_t* home, const wchar_t* const* paths, int npaths)
{
    if (g_started.exchange(true)) return 0;
    std::vector<std::wstring> p;
    for (int i = 0; i < npaths; ++i) p.emplace_back(paths[i]);
    std::thread(HostMain, std::wstring(home), std::move(p)).detach();
    return 1;
}

SPIKE_EXPORT int SpikePy_Status(char* out, int cap) { return CopyOut(Status(), out, cap); }

// Runs Python source in __main__ through _spike_exec. Called on the harness thread (not a game thread).
SPIKE_EXPORT int SpikePy_Run(const char* code, char* out, int cap)
{
    if (Status() != "running") return CopyOut("ERR python is not running: " + Status(), out, cap);
    std::string reply;
    PyGILState_STATE g = PyGILState_Ensure();
    PyObject* mainMod = PyImport_AddModuleRef("__main__");
    PyObject* fn = mainMod ? PyObject_GetAttrString(mainMod, "_spike_exec") : nullptr;
    PyObject* r = fn ? PyObject_CallFunction(fn, "s", code) : nullptr;
    if (r && PyUnicode_Check(r)) {
        Py_ssize_t n = 0;
        const char* s = PyUnicode_AsUTF8AndSize(r, &n);
        reply = s ? std::string(s, static_cast<std::size_t>(n)) : "ERR reply is not UTF-8";
    } else {
        reply = "ERR _spike_exec failed";
    }
    if (PyErr_Occurred()) PyErr_Clear();
    Py_XDECREF(r);
    Py_XDECREF(fn);
    Py_XDECREF(mainMod);
    PyGILState_Release(g);
    return CopyOut(reply, out, cap);
}

#ifdef _WIN32
// Which module do this DLL's Python imports resolve to? Py_None is a DATA import (_Py_NoneStruct), so its
// address lies inside the real python314.dll, not in an import thunk of this DLL.
SPIKE_EXPORT int SpikePy_PythonModule(wchar_t* out, int cap, void** module)
{
    HMODULE h = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(Py_None), &h)) return 0;
    if (module) *module = h;
    return static_cast<int>(GetModuleFileNameW(h, out, static_cast<DWORD>(cap)));
}
#endif
```

- [ ] **Step 2: Check the core on Linux before touching the build box**

`spikes/spike-pycore/linux_check.cpp` drives the exports the way the spike DLL will, with a fake caller standing in for the game:
```cpp
// Linux check of SpikePyCore's exports against uv's CPython 3.14 (Task 3 Step 2). Not part of any DLL.
// Usage: linux_check <python prefix>   Exit 0 and "LINUX CHECK PASS" when every check holds.
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

extern "C" {
void SpikePy_SetLog(void (*)(const char*));
void SpikePy_SetCaller(int (*)(int, double*));
int SpikePy_Start(const wchar_t*, const wchar_t* const*, int);
int SpikePy_Status(char*, int);
int SpikePy_Run(const char*, char*, int);
}

static int g_failures = 0;
static void LogFn(const char* s) { std::printf("  log: %s\n", s); }
static int FakeCaller(int, double* out)  // stands in for the game: 2 ms per call, value 1.5
{
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    *out = 1.5;
    return 0;
}
static std::string Run(const char* code)
{
    static char buf[65536];
    SpikePy_Run(code, buf, sizeof buf);
    return buf;
}
static void Expect(const char* what, const std::string& got, const char* prefix)
{
    const bool ok = got.rfind(prefix, 0) == 0;
    if (!ok) ++g_failures;
    std::printf("%s %s: %.120s\n", ok ? "ok  " : "FAIL", what, got.c_str());
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::wstring home(argv[1], argv[1] + std::strlen(argv[1]));
    const std::wstring lib = home + L"/lib/python3.14", dyn = lib + L"/lib-dynload";
    const wchar_t* paths[] = { lib.c_str(), dyn.c_str() };
    SpikePy_SetLog(LogFn);
    Expect("run before start", Run("result = 1"), "ERR python is not running");
    const int first = SpikePy_Start(home.c_str(), paths, 2);
    const int second = SpikePy_Start(home.c_str(), paths, 2);
    Expect("start once", std::to_string(first) + std::to_string(second), "10");
    char st[256] = "";
    for (int i = 0; i < 250; ++i) {
        SpikePy_Status(st, sizeof st);
        if (std::strcmp(st, "starting") != 0 && std::strcmp(st, "not_started") != 0) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    Expect("status", st, "running");
    Expect("isolated, site, stdout to log",
           Run("import json, ssl, socket, sqlite3, threading, ctypes, sys\n"
               "result = json.dumps([sys.flags.isolated, sys.flags.ignore_environment, 'site' in sys.modules, type(sys.stdout).__name__])"),
           "OK [1, 1, true, \"_SpikeLogWriter\"]");
    Expect("call_rt without a caller", Run("import _spike\n_spike.call_rt(10)"), "ERR Traceback");
    SpikePy_SetCaller(FakeCaller);
    Expect("8 threads x 100 call_rt",
           Run("import _spike, threading\nout = []\n"
               "def w():\n    for _ in range(100): out.append(_spike.call_rt(1000))\n"
               "ts = [threading.Thread(target=w) for _ in range(8)]\n[t.start() for t in ts]\n[t.join() for t in ts]\n"
               "result = (len(out), sorted(set(out)))"),
           "OK (800, [1.5])");
    Run("keep = 7");
    Expect("namespace persists", Run("result = keep * 6"), "OK 42");
    Expect("exception is a reply", Run("1/0"), "ERR Traceback");
    Expect("SystemExit is a reply", Run("raise SystemExit(5)"), "ERR Traceback");
    Expect("still running", Run("result = 'alive'"), "OK alive");
    std::printf(g_failures ? "LINUX CHECK FAIL (%d)\n" : "LINUX CHECK PASS\n", g_failures);
    return g_failures ? 1 : 0;  // returns while the host thread is still blocked: exit must not hang
}
```
Run (outputs go to `out/`, which is gitignored; `PYTHONHOME`/`PYTHONPATH` are set to junk on purpose: the isolated config must ignore them):
```bash
PY=$(uv python find 3.14); PREFIX=$(dirname "$(dirname "$PY")")
INC=$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')
mkdir -p out
g++ -std=c++20 -O1 -Wall -Wextra -I"$INC" spikes/spike-pycore/src/PyCore.cpp spikes/spike-pycore/linux_check.cpp \
    -L"$PREFIX/lib" -lpython3.14 -Wl,-rpath,"$PREFIX/lib" -lpthread -o out/spike_pycore_linux_check
PYTHONHOME=/bogus PYTHONPATH=/bogus timeout 60 out/spike_pycore_linux_check "$PREFIX" | grep -v '^  log:'
```
Expected: ten `ok` lines and `LINUX CHECK PASS`, and the process exits (exit code 0) although the host thread is still blocked. This was run while writing the plan (uv CPython 3.14.7, g++ 11.4): all ten checks passed. An earlier draft with a **static** `std::condition_variable` passed every check and then **hung at process exit**: glibc's `pthread_cond_destroy` waits for the blocked host thread. Hence the leaked mutex and condition variable in `PyCore.cpp`; Task 26's host thread must do the same. A `timeout` (exit 124) here is a FAIL of this step: fix it before going to the box.

- [ ] **Step 3: Add the loader to the spike DLL**

`spikes/spike-dll/src/PyLoad.h`:
```cpp
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
}
```

`spikes/spike-dll/src/PyLoad.cpp` (own translation unit with its own includes; no Python and no CommonLib headers):
```cpp
// Throwaway M0 spike code (S8). No Python headers in this file: the spike DLL must not import python314.dll.
#include "PyLoad.h"

#include <windows.h>

#include <filesystem>
#include <format>
#include <mutex>
#include <string>
#include <system_error>
#include <vector>

namespace spike {
void Log(const std::string& line);  // main.cpp
}

namespace spike::py {
namespace {
using SetLogFn = void (*)(void (*)(const char*));
using SetCallerFn = void (*)(CallerFn);
using StartFn = int (*)(const wchar_t*, const wchar_t* const*, int);
using StatusFn = int (*)(char*, int);
using RunFn = int (*)(const char*, char*, int);
using ModuleFn = int (*)(wchar_t*, int, void**);

std::mutex g_mx;
HMODULE g_py = nullptr;
HMODULE g_core = nullptr;
SetCallerFn g_setCaller = nullptr;
StartFn g_start = nullptr;
StatusFn g_status = nullptr;
RunFn g_run = nullptr;
CallerFn g_pendingCaller = nullptr;

void LogFromCore(const char* s) { spike::Log(s ? s : ""); }

std::filesystem::path ModulePath(HMODULE h)
{
    std::wstring buf(32768, L'\0');
    const DWORD n = GetModuleFileNameW(h, buf.data(), static_cast<DWORD>(buf.size()));
    buf.resize(n);
    return std::filesystem::path(buf);
}
std::filesystem::path OwnDir()  // <Data>/SKSE/Plugins/SkyrimPython, next to this DLL's folder
{
    HMODULE self = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&LogFromCore), &self);
    return ModulePath(self).parent_path() / L"SkyrimPython";
}
std::string U8(const std::filesystem::path& p)
{
    const auto s = p.u8string();
    return std::string(s.begin(), s.end());
}
bool SamePath(const std::filesystem::path& a, const std::filesystem::path& b)
{
    std::error_code ec;
    const bool eq = std::filesystem::equivalent(a, b, ec);
    return !ec && eq;
}
std::string OneLine(std::string s)  // the harness is line based
{
    std::string out;
    out.reserve(s.size());
    for (char c : s) {
        if (c == '\r') continue;
        if (c == '\n') out += " | ";
        else out += c;
    }
    return out;
}
template <class F>
F Proc(const char* name) { return reinterpret_cast<F>(GetProcAddress(g_core, name)); }
}  // namespace

std::string Load()
{
    std::lock_guard l(g_mx);
    if (g_core) return "PY_LOAD ALREADY_LOADED";
    const auto own = OwnDir();
    const auto pyPath = own / L"python" / L"python314.dll";
    const auto corePath = own / L"SpikePyCore.dll";
    if (HMODULE existing = GetModuleHandleW(L"python314.dll")) {
        const auto found = ModulePath(existing);
        if (!SamePath(found, pyPath))
            return "PY_LOAD COLLISION another python314.dll is already loaded: " + U8(found) + " (ours: " + U8(pyPath) + "); Python stays disabled";
    }
    g_py = LoadLibraryExW(pyPath.wstring().c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!g_py) return std::format("PY_LOAD FAIL python314.dll error={} path={}", GetLastError(), U8(pyPath));
    HMODULE core = LoadLibraryExW(corePath.wstring().c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!core) return std::format("PY_LOAD FAIL SpikePyCore.dll error={} path={}", GetLastError(), U8(corePath));
    g_core = core;
    auto setLog = Proc<SetLogFn>("SpikePy_SetLog");
    auto module = Proc<ModuleFn>("SpikePy_PythonModule");
    g_setCaller = Proc<SetCallerFn>("SpikePy_SetCaller");
    g_start = Proc<StartFn>("SpikePy_Start");
    g_status = Proc<StatusFn>("SpikePy_Status");
    g_run = Proc<RunFn>("SpikePy_Run");
    if (!setLog || !module || !g_setCaller || !g_start || !g_status || !g_run) {
        g_core = nullptr;  // never unloaded; a rebuilt core is needed
        return "PY_LOAD FAIL SpikePyCore.dll lacks an export (SpikePy_SetLog/PythonModule/SetCaller/Start/Status/Run)";
    }
    setLog(&LogFromCore);
    if (g_pendingCaller) g_setCaller(g_pendingCaller);
    std::wstring seen(32768, L'\0');
    void* seenModule = nullptr;
    const int n = module(seen.data(), static_cast<int>(seen.size()), &seenModule);
    seen.resize(n > 0 ? static_cast<std::size_t>(n) : 0);
    const bool same = seenModule == static_cast<void*>(g_py);
    auto r = std::format("PY_LOAD {} same_module={} ours={} core_imports={} ours_handle={} core_import_handle={} core={}",
                         same ? "OK" : "MISMATCH", same, U8(ModulePath(g_py)), U8(std::filesystem::path(seen)),
                         static_cast<const void*>(g_py), static_cast<const void*>(seenModule), U8(ModulePath(g_core)));
    spike::Log("spike: " + r);
    return r;
}

std::string Init(bool dataLoaded)
{
    std::lock_guard l(g_mx);
    if (!dataLoaded) return "PY_INIT REFUSED before kDataLoaded";
    if (!g_start) return "PY_INIT REFUSED: run py_load first";
    const auto own = OwnDir();
    const std::wstring home = (own / L"python").wstring();
    const std::vector<std::wstring> paths = {
        (own / L"python" / L"python314.zip").wstring(),  // the stdlib, as .pyc files
        home,                                             // the .pyd extension modules
        (own / L"site-packages").wstring(),
        (own / L"lib").wstring(),
    };
    std::vector<const wchar_t*> raw;
    for (const auto& p : paths) raw.push_back(p.c_str());
    const int started = g_start(home.c_str(), raw.data(), static_cast<int>(raw.size()));  // copies the strings
    return started ? "PY_INIT STARTED (poll py_status)" : "PY_INIT ALREADY_STARTED";
}

std::string Status()
{
    std::lock_guard l(g_mx);
    if (!g_status) return "PY_STATUS not_loaded";
    char buf[4096];
    g_status(buf, sizeof buf);
    return std::string("PY_STATUS ") + buf;
}

std::string Exec(const std::string& code)
{
    RunFn run = nullptr;
    {
        std::lock_guard l(g_mx);
        run = g_run;
    }
    if (!run) return "ERR python is not loaded (py_load)";
    std::string out(1 << 20, '\0');
    const int n = run(code.c_str(), out.data(), static_cast<int>(out.size()));
    out.resize(n > 0 ? static_cast<std::size_t>(n) : 0);
    return OneLine(out);
}

void SetCaller(CallerFn fn)
{
    std::lock_guard l(g_mx);
    g_pendingCaller = fn;
    if (g_setCaller) g_setCaller(fn);
}

bool AutostartRequested()
{
    std::error_code ec;
    return std::filesystem::exists(OwnDir() / L"spike-autostart", ec);
}
}  // namespace spike::py
```

`spikes/spike-dll/CMakeLists.txt`: add `src/PyLoad.cpp` to the `add_library(SkyrimPythonSpike SHARED ...)` source list (after `src/Net.cpp`). Nothing else changes; in particular the spike DLL does **not** link `python314.lib`.

`spikes/spike-dll/src/main.cpp`, five edits:
1. At the top, next to `#include "Net.h"`: `#include "PyLoad.h"`.
2. A new block directly above the `namespace spike` block that defines `Handle`:
   ```cpp
   namespace spike {
   std::atomic_bool g_dataLoaded{ false };  // set in OnMessage at kDataLoaded; py_init refuses before it
   }
   ```
3. In `Handle`, before the fallback `return "ERR unknown command";`:
   ```cpp
       if (line == "py_load") return py::Load();
       if (line == "py_init") return py::Init(g_dataLoaded.load());
       if (line == "py_status") return py::Status();
       if (line.rfind("py_exec ", 0) == 0) return py::Exec(line.substr(8));
   ```
4. In `OnMessage`, replace the `kDataLoaded` case body with:
   ```cpp
       case SKSE::MessagingInterface::kDataLoaded:
           spike::Log("kDataLoaded");
           spike::g_dataLoaded.store(true);
           if (spike::py::AutostartRequested())  // run B (Step 7): the real plugin's timing; py::Load ran in SKSEPluginLoad
               spike::Log("spike: autostart at kDataLoaded " + spike::py::Init(true));
           break;
   ```
5. In `SKSEPluginLoad`, directly after the `spike::Log(std::format("SkyrimPythonSpike loading on ...` line (so after `SKSE::Init` and `SetupLog()`, and the result line reaches the spike log), load `python314.dll` and the core at plugin-load time, where spec 4.1 and the Global Constraints put it:
   ```cpp
       if (spike::py::AutostartRequested())  // run B (Step 7): python314.dll + SpikePyCore.dll at plugin load
           spike::Log("spike: autostart at plugin load " + spike::py::Load());
   ```

- [ ] **Step 4: Deploy mode for the Python runtime**

In `tools/deploy.sh`, insert this block **immediately after the line that sources `tools/env.sh`** (before the existing `[ -f "${1:-}" ]` check, which would reject `--python`). Keep everything else Task 1 wrote.
```bash
# --python [out/<Core>.dll]: copy the pinned embeddable CPython to Data/SKSE/Plugins/SkyrimPython/python/
# (with its LICENSE.txt) and, if given, a core DLL to Data/SKSE/Plugins/SkyrimPython/.
if [ "${1:-}" = "--python" ]; then
  shift
  "$ROOT/tools/fetch_python.sh" --check || { echo "deploy: third_party/cpython does not match docs/PINS.md" >&2; exit 1; }
  DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"; OWN="$DEST/SkyrimPython"; PYDIR="$OWN/python"
  [ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
  mkdir -p "$ROOT/out"; touch "$ROOT/out/deployed.txt"
  if [ -e "$PYDIR" ] && ! grep -qxF "$PYDIR" "$ROOT/out/deployed.txt"; then
    echo "deploy: $PYDIR exists but was not deployed by this repo; not overwriting it. Ask the owner." >&2; exit 1
  fi
  mkdir -p "$PYDIR"
  cp -a "$ROOT/third_party/cpython/embed/." "$PYDIR/"
  grep -qxF "$PYDIR" "$ROOT/out/deployed.txt" || echo "$PYDIR" >> "$ROOT/out/deployed.txt"
  [ -f "$PYDIR/python314.dll" ] && [ -f "$PYDIR/LICENSE.txt" ] || { echo "deploy: copy incomplete" >&2; exit 1; }
  echo "deployed CPython runtime to $PYDIR"
  if [ $# -ge 1 ]; then
    case "$(basename "$1")" in
      *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || { echo "deploy: $1 is spike code; re-run with CONFIRM_THROWAWAY_SAVE=1 on a throwaway save" >&2; exit 1; } ;;
    esac
    [ -f "$1" ] || { echo "deploy: no such file: $1" >&2; exit 1; }
    cp -v "$1" "$OWN/"
    echo "$OWN/$(basename "$1")" >> "$ROOT/out/deployed.txt"
  fi
  exit 0
fi
```
Check: `bash -n tools/deploy.sh` prints nothing.

- [ ] **Step 5: The Linux-side scripts, tested against a local stand-in**

`spikes/py_send.py`:
```python
#!/usr/bin/env python3
"""Send Python source to the spike's embedded interpreter: py_send.py FILE.py | py_send.py -c "CODE".

The harness is line based, so the source travels base64-wrapped in one `py_exec` line. It runs in one
persistent namespace (__main__); a variable `result` is returned. Prints the reply ("OK <result>" or
"ERR <traceback>", newlines folded to " | "); exit 0 for OK. Env: SPIKE_PORT (45978), PY_SEND_TIMEOUT (300 s).
"""
import base64
import os
import socket
import sys

HOST = "127.0.0.1"
PORT = int(os.environ.get("SPIKE_PORT", "45978"))
TIMEOUT = float(os.environ.get("PY_SEND_TIMEOUT", "300"))


def wrap(source: str) -> str:
    b64 = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return f"py_exec exec(__import__('base64').b64decode('{b64}').decode('utf-8'))"


def send(line: str) -> str:
    with socket.create_connection((HOST, PORT), timeout=10) as s:
        s.settimeout(TIMEOUT)
        s.sendall((line + "\n").encode("utf-8"))
        f = s.makefile("r", encoding="utf-8", newline="\n")
        reply = f.readline()
    if not reply:
        raise ConnectionError("the spike DLL closed the connection without a reply")
    return reply.rstrip("\n")


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "-c":
        source = argv[2]
    elif len(argv) == 2 and argv[1] not in ("-h", "--help"):
        with open(argv[1], encoding="utf-8") as fh:
            source = fh.read()
    else:
        print(__doc__)
        return 2
    try:
        reply = send(wrap(source))
    except (OSError, ConnectionError) as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    print(reply)
    return 0 if reply.startswith("OK") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

`spikes/py/s8_imports.py`:
```python
# S8: stdlib modules a real consumer needs, imported inside the game's embedded interpreter.
import ctypes
import json
import os
import socket
import sqlite3
import ssl
import sys
import threading

_con = sqlite3.connect(":memory:")
_con.execute("create table t(x)")
_con.execute("insert into t values (42)")
_ctx = ssl.create_default_context()
_s = socket.socket()
_s.close()
result = json.dumps({
    "version": sys.version,
    "hexversion": hex(sys.hexversion),          # 3.14.8 final is 0x30e08f0
    "isolated": sys.flags.isolated,             # 1
    "ignore_environment": sys.flags.ignore_environment,  # 1
    "prefix": sys.prefix,
    "executable": sys.executable,
    "path": sys.path,
    "site_imported": "site" in sys.modules,
    "openssl": ssl.OPENSSL_VERSION,
    "sqlite": sqlite3.sqlite_version,
    "sqlite_value": _con.execute("select x from t").fetchone()[0],   # 42
    "pointer_bytes": ctypes.sizeof(ctypes.c_void_p),                 # 8
    "threads": threading.active_count(),
    "stdout": type(sys.stdout).__name__,
    "pythonpath_env": os.environ.get("PYTHONPATH"),
}, sort_keys=True)
```

`spikes/py/echo_server.py`:
```python
# S8: a line-echo server inside the game's interpreter, on 127.0.0.1:45979 (the harness owns 45978).
# Safe to send twice: the second send reports the running server.
import socketserver
import threading


class _EchoHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            self.wfile.write(line)
            self.wfile.flush()


class _EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if "_spike_echo" not in globals():
    _spike_echo = _EchoServer(("127.0.0.1", 45979), _EchoHandler)
    threading.Thread(target=_spike_echo.serve_forever, name="spike-echo", daemon=True).start()
result = "listening 127.0.0.1:%d" % _spike_echo.server_address[1]
```

`spikes/py_echo_client.py`:
```python
#!/usr/bin/env python3
"""S8: Linux client of spikes/py/echo_server.py in the game. No args: 1000 round trips, a 1 MiB line,
4 parallel connections x 250. `soak SEC`: round trips for SEC seconds. Env: PY_ECHO_PORT (45979)."""
import os
import socket
import statistics
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("PY_ECHO_PORT", "45979"))


def _conn():
    s = socket.create_connection((HOST, PORT), timeout=10)
    s.settimeout(30)
    return s, s.makefile("rb")


def round_trips(n: int, tag: str) -> list[float]:
    s, f = _conn()
    lat = []
    with s:
        for i in range(n):
            msg = f"{tag}-{i}\n".encode()
            t0 = time.perf_counter()
            s.sendall(msg)
            got = f.readline()
            lat.append((time.perf_counter() - t0) * 1000)
            if got != msg:
                raise AssertionError(f"{tag}: round trip {i} got {got!r}, expected {msg!r}")
    return lat


def large(size: int = 1024 * 1024) -> float:
    payload = b"x" * (size - 1) + b"\n"
    s, f = _conn()
    with s:
        t0 = time.perf_counter()
        s.sendall(payload)
        got = f.readline()
    if got != payload:
        raise AssertionError(f"large: got {len(got)} bytes, expected {len(payload)}")
    return (time.perf_counter() - t0) * 1000


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "soak":
            end = time.monotonic() + float(argv[2])
            n, worst = 0, 0.0
            while time.monotonic() < end:
                worst = max(worst, max(round_trips(50, f"soak{n}")))
                n += 50
            print(f"S8 soak PASS: {n} round trips, worst {worst:.1f} ms")
            return 0
        lat = round_trips(1000, "ping")
        print(f"S8 socket basic PASS: 1000 round trips, median {statistics.median(lat):.2f} ms, max {max(lat):.2f} ms")
        print(f"S8 socket large PASS: 1 MiB echoed in {large():.1f} ms")
        errs = []  # an exception in a thread target is otherwise swallowed: collect it and fail below

        def worker(k: int) -> None:
            try:
                round_trips(250, f"par{k}")
            except BaseException as e:
                errs.append(f"par{k}: {e!r}")

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        if any(t.is_alive() for t in threads):
            raise AssertionError("parallel: a connection did not finish in 60 s")
        if errs:
            raise AssertionError("parallel: " + "; ".join(errs))
        print("S8 socket parallel PASS: 4 connections x 250 round trips")
        print("S8 socket PASS")
        return 0
    except ConnectionRefusedError:
        print(f"S8 socket FAIL: connection refused on {HOST}:{PORT} (did py_send.py spikes/py/echo_server.py reply OK?)")
        return 1
    except (OSError, AssertionError) as e:
        print(f"S8 socket FAIL: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

Test them on Linux against a stand-in harness that runs the same `_spike_exec` logic in-process (this proves the scripts, not S8):
```bash
chmod +x spikes/py_send.py spikes/py_echo_client.py
python3 - <<'PYEOF' &
import socketserver, sys, time, traceback, __main__, types
def _spike_exec(src):  # same logic as kBootstrap in PyCore.cpp
    ns = __main__.__dict__; ns.pop("result", None)
    try:
        exec(compile(src, "<py_exec>", "exec"), ns); r = ns.pop("result", None)
        return "OK" if r is None else "OK " + str(r)
    except BaseException:
        return "ERR " + traceback.format_exc()
class H(socketserver.StreamRequestHandler):
    def handle(self):
        for raw in self.rfile:
            line = raw.decode().rstrip("\n")
            rep = _spike_exec(line[8:]) if line.startswith("py_exec ") else "ERR unknown command"
            self.wfile.write((rep.replace("\r", "").replace("\n", " | ") + "\n").encode())
socketserver.ThreadingTCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 45997), H); srv.daemon_threads = True; srv.timeout = 1
end = time.time() + 40
while time.time() < end: srv.handle_request()
PYEOF
sleep 1
SPIKE_PORT=45997 python3 spikes/py_send.py spikes/py/s8_imports.py >/dev/null && echo "imports ok"
SPIKE_PORT=45997 python3 spikes/py_send.py spikes/py/echo_server.py
SPIKE_PORT=45997 python3 spikes/py_send.py spikes/py/echo_server.py
python3 spikes/py_echo_client.py && python3 spikes/py_echo_client.py soak 2
SPIKE_PORT=45997 python3 spikes/py_send.py -c "raise SystemExit(3)"; echo "rc=$? (expect 1)"
wait
```
Expected: `imports ok`, `OK listening 127.0.0.1:45979` twice, the four `S8 socket ... PASS` lines, `S8 soak PASS`, an `ERR Traceback ... SystemExit: 3` line and `rc=1`. (Verified while writing the plan.)

The parallel sub-check must also be able to fail: an exception inside a worker thread does not stop the thread from finishing, so without the `errs` list it would be swallowed and `S8 socket PASS` printed anyway. Prove it against a server that echoes correctly on the first two connections and corrupts every later one (the 1000-round-trip and 1 MiB checks are those first two connections, so only the parallel phase sees the corruption):
```bash
python3 - <<'PYEOF' &
import socketserver, threading, time
n = {"c": 0}; lock = threading.Lock()
class H(socketserver.StreamRequestHandler):
    def handle(self):
        with lock: n["c"] += 1; k = n["c"]
        for line in self.rfile:
            self.wfile.write(line if k <= 2 else b"corrupt\n"); self.wfile.flush()
socketserver.ThreadingTCPServer.allow_reuse_address = True
srv = socketserver.ThreadingTCPServer(("127.0.0.1", 45996), H); srv.daemon_threads = True; srv.timeout = 1
end = time.time() + 20
while time.time() < end: srv.handle_request()
PYEOF
sleep 1
PY_ECHO_PORT=45996 python3 spikes/py_echo_client.py; echo "rc=$? (expect 1)"
wait
```
Expected: `S8 socket basic PASS`, `S8 socket large PASS`, then `S8 socket FAIL: AssertionError: parallel: par0: AssertionError(...)` naming all four `par` connections, no `S8 socket PASS` line, and `rc=1`.

- [ ] **Step 6: Build, deploy, and run A (commands at the main menu)**

```bash
tools/remote_build.sh spikes/spike-pycore SpikePyCore.dll
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
objdump -p out/SpikePyCore.dll | grep -E 'DLL Name: python314.dll|SpikePy_'
objdump -p out/SkyrimPythonSpike.dll | grep -c 'DLL Name: python' || true
```
Expected: `DLL Name: python314.dll` and the six `SpikePy_` exports for the core; `0` for the spike DLL (it must not import Python). Then (quit the game first, console `qqq`):
```bash
CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh --python out/SpikePyCore.dll
tools/launch_game.sh
source tools/env.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "kDataLoaded" 600
python3 spikes/spike_client.py py_status          # PY_STATUS not_loaded
python3 spikes/spike_client.py py_load            # PY_LOAD OK same_module=true ...
python3 spikes/spike_client.py py_init            # PY_INIT STARTED (poll py_status)
for i in $(seq 1 30); do r=$(python3 spikes/spike_client.py py_status); echo "$r"; case "$r" in *running*|*failed*) break;; esac; sleep 1; done
python3 spikes/py_send.py spikes/py/s8_imports.py
python3 spikes/py_send.py spikes/py/echo_server.py
python3 spikes/py_echo_client.py
grep -E 'pycore:|py: python|PY_LOAD' "$SKSE_LOG_DIR/SkyrimPythonSpike.log"
```
Expected, in order: `PY_STATUS not_loaded`; `PY_LOAD OK same_module=true ours=...\SkyrimPython\python\python314.dll core_imports=<the same path> ...`; `PY_INIT STARTED`; `PY_STATUS running` within 30 s; an `OK {...}` JSON line with `"hexversion": "0x30e08f0"`, `"isolated": 1`, `"ignore_environment": 1`, `"sqlite_value": 42`, `"pointer_bytes": 8`; `OK listening 127.0.0.1:45979`; `S8 socket PASS`. The log shows the host thread id, `headers 3.14.8, runtime 3.14.8 ...` and the `sys.path` (four entries from `PyLoad.cpp`, plus whatever `site` adds). Save the full outputs for the evidence column.

`PY_LOAD MISMATCH` (the core's import resolved to another `python314.dll`), `PY_LOAD FAIL`, `PY_STATUS failed: ...`, or the game crashing at any point is an **S8 FAIL**: record it and STOP.

- [ ] **Step 7: Keep it running, then run B (load at plugin load, init at `kDataLoaded`) and a clean exit**

1. Load the throwaway save (`spikebase`), play for about a minute (walk, open and close the inventory), while from Linux: `python3 spikes/py_echo_client.py soak 60`. Expected `S8 soak PASS` and no visible hitch beyond normal; then `python3 spikes/spike_client.py echo still-here` prints `still-here`.
2. Clean exit: in the game console type `qqq`, then on Linux:
   ```bash
   timeout 120 bash -c 'while pgrep -x SkyrimSE.exe >/dev/null; do sleep 2; done' && echo "game exited"
   tail -n 20 "$SKYRIM_PYTHON_STATE/launch.log"
   find "$SKSE_LOG_DIR" -newer "$SKYRIM_PYTHON_STATE/launch.stamp" -iname 'crash*' -print
   ```
   Expected `game exited`, no `Unhandled exception` / `page fault` lines in the tail, and the `find` prints **nothing** (a crash logger such as Crash Logger writes `crash-*.log` under the SKSE log folder, and a crash at exit may leave its only record there). A hang on exit (still running after 120 s), a crash dialog or a new `crash*` file is an **S8 FAIL** (record it; STOP).
3. Run B, the real plugin's timing: `python314.dll` and the core are loaded in `SKSEPluginLoad` (plugin-load time, spec 4.1), and the interpreter is started at the main thread's `kDataLoaded` message:
   ```bash
   touch "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/spike-autostart"
   echo "$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython/spike-autostart" >> out/deployed.txt
   tools/launch_game.sh
   SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "pycore: status (running|failed)" 600
   grep -E 'loading on Skyrim|autostart at (plugin load|kDataLoaded)|kDataLoaded' "$SKSE_LOG_DIR/SkyrimPythonSpike.log"
   python3 spikes/spike_client.py py_status
   python3 spikes/py_send.py spikes/py/s8_imports.py && python3 spikes/py_send.py spikes/py/echo_server.py && python3 spikes/py_echo_client.py
   ```
   Expected, in this order in the log: `SkyrimPythonSpike loading on Skyrim 1.6.1170.0`, `spike: autostart at plugin load PY_LOAD OK same_module=true ...` (before `kDataLoaded`), `kDataLoaded`, `spike: autostart at kDataLoaded PY_INIT STARTED (poll py_status)`; then `PY_STATUS running`, the imports JSON and `S8 socket PASS`. `PY_LOAD FAIL`/`MISMATCH` at plugin load, or a crash during loading, is an **S8 FAIL** (record it; STOP). Quit with `qqq` and repeat the clean-exit check of item 2. Leave the marker file in place only if the owner wants autostart for later spikes; Task 5 does not need it.

   What run B does **not** cover: the real core DLL calls `SKSE::Init` itself with the loader's `LoadInterface` and registers the messaging listener, Papyrus functions and `AddTask` from a second module (`API-VERIFICATION.md` Part 3 lists this as unverified). No spike tests that; it is first exercised by Task 25's first live launch, which is a STOP gate for it. Write that into the S8 row (template below).

- [ ] **Step 8: Record the result**

Fill the S8 row of `spikes/RESULTS.md` (add the row below S1 if the table has none):
```markdown
| S8 two-DLL load, embedded CPython, Python socket from Linux (Task 3) | <date> | PASS/FAIL | run A: `<PY_LOAD line>`, `<PY_STATUS>`, imports JSON (hexversion, isolated, openssl, sqlite, sys.path, executable), `<S8 socket lines>`; soak `<line>`; clean exit yes/no; run B (Load in SKSEPluginLoad, Init at kDataLoaded): `<lines>`; clean exit B yes/no, crash logs none; runtime `<version from log>`. Not covered: SKSE::Init from the core DLL (second module); first tested by Task 25's live launch (a STOP gate) | Whole design (FAIL: STOP; fallback is branch `plan-out-of-process`, spec draft 3). Socket-only failure: the dev bridge connects outbound to a Linux listener (spec 4.2) |
```
**PASS needs all of:** `same_module=true` in both runs (run B's from the plugin-load-time `PY_LOAD`), `PY_STATUS running` in both runs, every import in `s8_imports.py` with `"site_imported": true` and a `"path"` whose **first four entries** are exactly the four `PyLoad.cpp` entries in order (`...\SkyrimPython\python\python314.zip`, `...\SkyrimPython\python`, `...\SkyrimPython\site-packages`, `...\SkyrimPython\lib`; they prove the isolated `PyConfig` took effect, and S10 and Task 26 rely on `site-packages` and `lib` being on the path), `S8 socket PASS` (which now fails if any of the 4 parallel connections fails) and `S8 soak PASS`, and a clean exit in both runs (no hang, no crash dialog, no new `crash*` log). If only the socket part fails (Python runs, Linux cannot connect to 45979), record `FAIL (socket only)`: that is still a STOP, but tell the owner the embedding itself worked.

- [ ] **Step 9: Commit**

```bash
git add spikes/spike-pycore spikes/spike-dll spikes/py_send.py spikes/py_echo_client.py spikes/py tools/deploy.sh spikes/RESULTS.md
git commit -m "test(spike): S8 two-DLL load, embedded CPython 3.14 and a Python socket reachable from Linux"
```
