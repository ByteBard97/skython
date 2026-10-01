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
