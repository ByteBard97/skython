#include <pybind11/pybind11.h>
#include <pybind11/eval.h>
#include "PythonHost.h"
#include "GameBackend.h"
#include "Log.h"
#include "Settings.h"
#include "spy/pymodule.h"
#include <atomic>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <thread>

extern "C" PyObject* PyInit__skython();  // defined by PYBIND11_MODULE in PyModule.cpp

namespace spy::host {
namespace {
namespace fs = std::filesystem;
namespace py = pybind11;

// Immortal (Global Constraints): never static objects, so process exit cannot destroy them under a waiting thread.
std::mutex& Mx() { static std::mutex& m = *new std::mutex(); return m; }
std::string& StateText() { static std::string& s = *new std::string("disabled: not started"); return s; }
std::atomic_bool g_started{ false };

void SetState(std::string s) { std::lock_guard l(Mx()); StateText() = std::move(s); }
void Fail(const std::string& why) { SetState("failed: " + why); log::Error("python host: failed: " + why); }

std::string U8(const fs::path& p) { const auto s = p.u8string(); return std::string(s.begin(), s.end()); }

void Check(PyStatus s, const char* what)
{
    if (PyStatus_Exception(s))
        throw std::runtime_error(std::string(what) + ": " + (s.err_msg ? s.err_msg : "error") +
                                 (s.func ? std::string(" (in ") + s.func + ")" : ""));
}

struct ConfigGuard {  // PyConfig_Clear on every path
    PyConfig c;
    ConfigGuard() { PyConfig_InitIsolatedConfig(&c); }
    ~ConfigGuard() { PyConfig_Clear(&c); }
};

// Returns the full traceback text; the GIL is held.
std::string Traceback(py::error_already_set& e)
{
    try {
        py::list lines = py::module_::import("traceback").attr("format_exception")(e.type(), e.value(), e.trace());
        std::string out;
        for (auto l : lines) out += l.cast<std::string>();
        return out;
    } catch (...) {
        return e.what();
    }
}

std::string LastLine(const std::string& text)
{
    auto end = text.find_last_not_of("\r\n");
    if (end == std::string::npos) return text;
    auto start = text.rfind('\n', end);
    return text.substr(start == std::string::npos ? 0 : start + 1, end - (start == std::string::npos ? 0 : start + 1) + 1);
}

// Until skython._host installs its own streams: whole lines to the plugin log. A real text stream (encoding,
// errors, writable), and the line buffer is locked because several threads may print at once.
constexpr const char* kStdio = R"PY(
import io, sys, threading, _skython
class _SpyStream(io.TextIOBase):
    encoding = "utf-8"
    errors = "backslashreplace"
    def __init__(self, level):
        super().__init__()
        self.level, self.buf, self.lock = level, "", threading.Lock()
    def writable(self):
        return True
    def write(self, text):
        text = str(text)
        with self.lock:
            lines = (self.buf + text).split("\n")
            self.buf = lines.pop()
        for line in lines:
            if line.strip():
                _skython.log(self.level, line)
        return len(text)
    def flush(self):
        pass
    def isatty(self):
        return False
sys.stdout = _SpyStream("info")
sys.stderr = _SpyStream("error")
)PY";

[[noreturn]] void ParkForever()
{
    static std::mutex& m = *new std::mutex();                         // immortal (Global Constraints)
    static std::condition_variable& cv = *new std::condition_variable();
    std::unique_lock l(m);
    for (;;) cv.wait(l);  // never notified; spurious wake-ups just wait again
}

// Releases the GIL when the scope that owns it ends, by any path: the normal end, or an exception thrown inside one
// of Run's catch handlers. noexcept, so the host thread can never end holding the GIL (spec 4.5 rule 2).
struct GilRelease
{
    GilRelease() = default;
    GilRelease(const GilRelease&) = delete;
    GilRelease& operator=(const GilRelease&) = delete;
    ~GilRelease() noexcept { PyEval_SaveThread(); }
};

void Run(const Config cfg)
{
    const auto& rt = settings::GetRuntime();
    const fs::path own = settings::OwnDir();
    const fs::path pyDir = own / "python", site = own / "site-packages", lib = own / "lib", scripts = own / "scripts";
    const fs::path logDir = rt.skse_dir ? *rt.skse_dir / "Skython" : own / "logs";
    std::error_code ec;
    for (const auto& d : { logDir, site, lib, scripts }) fs::create_directories(d, ec);
    const auto session = settings::SessionFile(rt.skse_dir);

    ModuleHost host;
    host.dispatcher = &game::GetDispatcher();
    host.backend = "game";
    host.config_json = [=] {
        json j = config_to_json(cfg);
        j["python_dir"] = U8(pyDir);
        j["scripts_dir"] = U8(scripts);
        j["site_packages_dir"] = U8(site);
        j["log_dir"] = U8(logDir);
        j["session_file"] = session ? U8(*session) : std::string();
        j["plugin_version"] = rt.plugin_version;
        j["game_version"] = rt.game_version;
        j["runtime"] = rt.runtime;
        return dump_json(j);
    };
    host.log = [](const std::string& level, const std::string& msg) {
        const std::string line = "py: " + msg;
        if (level == "error" || level == "critical") log::Error(line);
        else if (level == "warning" || level == "warn") log::Warn(line);
        else if (level == "debug") log::Debug(line);
        else log::Info(line);
    };
    // interim backend: replaced in Task 27. game::GetDispatcher() is Task 26's interim GameBackend.cpp until
    // Task 27 overwrites GameBackend.{h,cpp}; this call site keeps the same name and needs no edit then.
    InstallModuleHost(std::move(host));
    if (PyImport_AppendInittab("_skython", &PyInit__skython) == -1) throw std::runtime_error("PyImport_AppendInittab failed");

    {
        ConfigGuard g;
        PyConfig& c = g.c;
        c.isolated = 1;
        c.use_environment = 0;
        c.install_signal_handlers = 0;
        c.site_import = 1;
        c.write_bytecode = 0;
        c.user_site_directory = 0;
        c.pathconfig_warnings = 0;
        Check(PyConfig_SetString(&c, &c.home, pyDir.c_str()), "PyConfig home");
        Check(PyConfig_SetString(&c, &c.executable, (pyDir / "python.exe").c_str()), "PyConfig executable");
        c.module_search_paths_set = 1;
        for (const fs::path& p : { pyDir / "python314.zip", pyDir, site, lib, scripts })
            Check(PyWideStringList_Append(&c.module_search_paths, p.c_str()), "module_search_paths");
        Check(Py_InitializeFromConfig(&c), "Py_InitializeFromConfig");
    }

    // From here this thread holds the GIL. `release` gives it up when this block ends, by any path; every py:: object
    // lives inside the try block and is destroyed before that. A normal end parks the thread below.
    {
        const GilRelease release;
        try {
            py::exec(kStdio);
            py::module_ sys = py::module_::import("sys");
            log::Info("python host: initialised; sys.executable = " + py::str(sys.attr("executable")).cast<std::string>() +
                      "; sys.path = " + py::str(sys.attr("path")).cast<std::string>());
            py::module_::import("skython._host").attr("main")();
            SetState("running");
            log::Info("python host: running");
        } catch (py::error_already_set& e) {
            const std::string tb = Traceback(e);
            Fail("skython._host: " + LastLine(tb));
            log::Error(tb);
        } catch (const std::exception& e) {
            Fail(e.what());
        } catch (...) {
            Fail("unknown exception after initialisation");
        }
    }
    ParkForever();
}
}

std::string LinkedPythonVersion()
{
    const std::string v = Py_GetVersion();
    return v.substr(0, v.find(' '));
}

void Start(const Config& cfg)
{
    try {
        if (g_started.exchange(true)) return;
        if (!cfg.python_enabled) {
            SetState("disabled: python_enabled=false");
            log::Info("python host: disabled: python_enabled=false");
            return;
        }
        SetState("starting");
        log::Info("python host: starting (CPython " + LinkedPythonVersion() + ")");
        std::thread([cfg] {
            // Run parks forever once Python is initialised. It throws only before that (no GIL taken), or if one of its
            // catch handlers threw (GilRelease has released the GIL by then). Nothing may escape this thread.
            try {
                Run(cfg);
            } catch (const std::exception& e) {
                try { Fail(e.what()); } catch (...) {}
            } catch (...) {
                try { Fail("unknown exception"); } catch (...) {}
            }
        }).detach();
    } catch (const std::exception& e) {
        Fail(std::string("could not start the host thread: ") + e.what());
    } catch (...) {
        Fail("could not start the host thread");
    }
}

std::string State() { std::lock_guard l(Mx()); return StateText(); }
}
