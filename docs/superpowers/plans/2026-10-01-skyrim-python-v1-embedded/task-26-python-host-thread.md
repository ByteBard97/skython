# Task 26: Python host thread, `_skython` in the game, deploy layout

New in the embedded design (it replaces the C++ loopback server of branch `plan-out-of-process`, task 16). The live-test helpers in Step 6 are adapted from that task's `livetest/support.py` and `test_m1_transport.py`; they now talk to the Python dev bridge (Task 23) running inside the game.

> Part of the [Skython v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.1, 4.2, 4.5, 4.10, 6. Steps use checkbox (`- [ ]`) syntax.

> **Depends on:** Task 25 (loader, core, settings, lifecycle), Task 1b/1c (the box harness), Task 24 (`skython._host`), Tasks 19-23 (`_skython` module source, `skython` package, bridge). **Runs on:** the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); live tests run on Linux through the session tunnel.
>
> **STOP and ask the owner (do not improvise) if:** the game does not reach the main menu within 10 minutes; the game crashes or freezes at any point in this task (record the last lines of both logs first); the build box does not answer `ssh` within 10 s; `python host: failed:` appears in the default run and the traceback does not point at an obvious packaging mistake.

**Files:**
- Modify: `plugin/src/PythonHost.h`, `plugin/src/PythonHost.cpp` (full replacements: the host thread)
- Create: `plugin/src/PyModule.cpp` (`PYBIND11_MODULE(_skython, m)` for the game build)
- Create: `plugin/src/GameBackend.h`, `plugin/src/GameBackend.cpp` (**interim**: `game.state` only; Task 27 overwrites both files with the real backend)
- Modify: `plugin/src/main.cpp` (start the host thread at `kDataLoaded`), `plugin/CMakeLists.txt` (two sources)
- Create: `livetest/support.py`, `livetest/conftest.py`, `livetest/test_m1_bridge.py`, `livetest/test_m1_startup_variants.py`
- Create: `livetest/scripts/livetest_broken/__init__.py` (a player script whose `setup` raises; deployed with every `--scripts livetest/scripts` from here on, so every live run also proves a failing script is harmless)

**Interfaces:**
- Consumes: Task 25 (`spy::log::*`, `spy::settings::{Get, OwnDir, SessionFile, GetRuntime}`, `spy::life::{BaseState, NotReadyReason, LoadsStarted}`, `spy::host::LinkedPythonVersion`, loader log phrases); Task 19 (`spy::ModuleHost`, `spy::InstallModuleHost`, `spy::DefineModule` in `pyglue/include/spy/pymodule.h`); Task 18 (`spy::Backend`, `Dispatcher`, `DispatcherOptions`, `OpResult`); Task 16 (`config_to_json`, `dump_json`); Task 24 (`skython._host.main()`); Task 22 (`skython.connect` falling back to `connect_remote`).
- Produces:
  - `spy::host::Start(const Config&)` (call once at `kDataLoaded`; returns at once; never throws) and `spy::host::State() -> std::string` (`"disabled: <why>"`, `"starting"`, `"running"`, `"failed: <why>"`).
  - Core log phrases: `python host: disabled: python_enabled=false`, `python host: starting (CPython 3.14.8)`, `python host: initialised; sys.executable = <...\python\python.exe>; sys.path = [...]`, `python host: running`, `python host: failed: <why>` (followed by the traceback when there is one; a failed `Py_InitializeFromConfig` reads `python host: failed: Py_InitializeFromConfig: <CPython's message>`).
  - `spy::game::Backend()` and `spy::game::GetDispatcher()` with the interim behaviour (Task 27 replaces the implementation, same names). Interim log line: `game backend: interim dispatcher ready (game.state only)`.
  - Deploying uses Task 1b's `tools/box/box_deploy.sh` (`out/Skython.dll [ini]`, `--python`, `--package`, `--scripts DIR`, `--move`); the game runs on the Windows box ([Task 1c](task-01c-part-c-on-the-box.md), ruling R42). `tools/deploy.sh` is not changed.
  - `livetest/support.py` (stdlib only): `Session(port, token)`, `load_session()`, `encode`, `read_frame`, `hello(session, **override)`, `raw_connect`, `rpc(sock, op, args=None, req_id=1, timeout=30.0)`, `send_request(sock, op, args=None, req_id=1)`, `send_raw`, `closed_by_server`, `frame_header`, `read_frame_or_none`, `still_serving`, `log_text(name="Skython.log")`, `python_log_text()`, `fresh(path)`, `refused(port=45977)`; box mode (`SKYTHON_BOX=1`, from `out/box-live.env`): `BOX`, `REPO`, `box_pull(*names)`; `log_text`/`python_log_text` pull the log from the box first, and `refused` asks the box (`tools/box/box_session.sh probe`). `livetest/conftest.py`: fixtures `session` and `game` (`skython.connect(...)` over the bridge); skips everything unless `SKYTHON_LIVE=1`.

Rules (Global Constraints, spec 4.5):
- `PyImport_AppendInittab("_skython", PyInit__skython)` and `InstallModuleHost` happen on the host thread **before** `Py_InitializeFromConfig`. Nothing Python-related runs at DLL load time.
- `PyConfig`: `PyConfig_InitIsolatedConfig`, then `isolated = 1`, `use_environment = 0`, `install_signal_handlers = 0`, `site_import = 1`, `write_bytecode = 0` (no `__pycache__` written into the game's Data folder), `user_site_directory = 0`, `pathconfig_warnings = 0`, `home` = the python folder, `executable` = `python\python.exe` (the embeddable zip ships it; otherwise `sys.executable` is `SkyrimSE.exe`, and `multiprocessing`'s spawn or `subprocess.run([sys.executable, ...])` would start a second copy of the game), `module_search_paths_set = 1` with exactly `python\python314.zip`, `python\`, `site-packages\`, `lib\`, `scripts\` (the zip's `python314._pth` is then ignored).
- Before `skython._host` is imported, `sys.stdout`/`sys.stderr` are replaced by `io.TextIOBase` writers (`encoding = "utf-8"`, a lock around the line buffer) that forward whole lines to `_skython.log` (a GUI process has no console; without this, `print` and early tracebacks vanish).
- After `main()` returns or fails, the host thread releases the GIL with `PyEval_SaveThread()` and parks forever. The release is a `noexcept` scope guard built right after `Py_InitializeFromConfig` succeeds, so even an exception thrown inside one of `Run`'s catch handlers (for example `std::bad_alloc` while formatting a traceback) cannot leave the thread holding the GIL. **Never `Py_Finalize`.**
- The host thread's state (`State()`'s mutex and text) and `ParkForever`'s mutex and condition variable are immortal (`new`, never static objects), per the Global Constraint: a static one is destroyed at exit while the host thread still waits on it.
- Collision variant: staged with a test-only loader build (`SPY_TEST_HOOKS=ON`, Task 25) that preloads a second copy of `python314.dll` from another folder, named by the environment variable `SKYTHON_TEST_PRELOAD`. Normal builds do not contain the hook (`build.ps1` always passes `-DSPY_TEST_HOOKS=OFF` unless `plugin/build-options.local` says `ON`).

- [ ] **Step 1: The host thread**

`plugin/src/PythonHost.h`:
```cpp
#pragma once
#include "spy/config.h"
#include <string>

namespace spy::host {
std::string LinkedPythonVersion();  // Py_GetVersion() of the linked python314.dll; safe before init
void Start(const Config& cfg);      // at kDataLoaded; starts the host thread and returns; never throws
std::string State();                // "disabled: <why>", "starting", "running", "failed: <why>"
}
```

`plugin/src/PythonHost.cpp` (Python and pybind11 headers only, never CommonLib; pybind11 must be the first include):
```cpp
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
```
`py::exec` and `py::module_::import` are used only while this thread holds the GIL; every `py::` object is destroyed before `GilRelease` calls `PyEval_SaveThread()` (they live inside the `try` block, which ends first). Check `kStdio` on Linux before building (no game needed):
```bash
python3 - <<'EOF'
import io, re, sys, traceback, types
src = open("plugin/src/PythonHost.cpp").read()
code = re.search(r'kStdio = R"PY\((.*?)\)PY";', src, re.S).group(1)
got = []
sys.modules["_skython"] = types.SimpleNamespace(log=lambda level, msg: got.append((level, msg)))
out, err = sys.stdout, sys.stderr
exec(code, {})
try:
    s = sys.stdout
    print("hello\nworld", end="")
    print("!")
    try:
        1 / 0
    except ZeroDivisionError:
        traceback.print_exc()
    checks = (isinstance(s, io.TextIOBase), s.encoding == "utf-8", s.writable(), ("info", "hello") in got,
              ("info", "world!") in got, any(l == "error" and "ZeroDivisionError" in m for l, m in got))
finally:
    sys.stdout, sys.stderr = out, err
print("kStdio OK" if all(checks) else f"kStdio FAILED {checks} {got}")
EOF
```
Expected: `kStdio OK`. `ModuleHost`'s member names are Task 19's; if Task 19 spelled one differently, use its spelling.

- [ ] **Step 2: The game build of `_skython`, the interim backend, the wiring**

`plugin/src/PyModule.cpp`:
```cpp
#include <pybind11/pybind11.h>
#include "spy/pymodule.h"

// The game build of _skython. PythonHost.cpp registers PyInit__skython with PyImport_AppendInittab before
// Py_InitializeFromConfig; nothing here runs at DLL load time.
PYBIND11_MODULE(_skython, m) { spy::DefineModule(m); }
```

`plugin/src/GameBackend.h` (the final names; Task 27 overwrites this file with its own version of the same declarations):
```cpp
#pragma once
#include "spy/dispatcher.h"

namespace spy::game {
spy::Backend& Backend();
spy::Dispatcher& GetDispatcher();  // built on first use from settings::Get(); reaper started; immortal
}
```

`plugin/src/GameBackend.cpp` (**INTERIM**, CommonLib allowed, no Python headers; Task 27 overwrites the whole file):
```cpp
#include "GameBackend.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Settings.h"

namespace spy::game {
namespace {
// INTERIM (Task 26): game.state from the lifecycle, `unsupported` for everything else. Replaced in Task 27.
class InterimBackend final : public spy::Backend
{
public:
    void Run(const std::string& op, const json&, Done done, Cancelled, Progress) override
    {
        if (op == "game.state") return done(OpResult::Ok(life::BaseState()));
        done(OpResult::Err(ErrorCode::unsupported, op + " is not served yet (the game backend arrives in Task 27)"));
    }
    json BaseState() override { return life::BaseState(); }
    std::optional<std::string> NotReadyReason(bool require_unpaused) override { return life::NotReadyReason(require_unpaused); }
    std::uint32_t LoadsStarted() override { return life::LoadsStarted(); }
};
}

spy::Backend& Backend()
{
    static InterimBackend& b = *new InterimBackend();
    return b;
}

spy::Dispatcher& GetDispatcher()
{
    static Dispatcher& d = []() -> Dispatcher& {
        const auto& c = settings::Get();
        auto* p = new Dispatcher(Backend(), DispatcherOptions{ c.default_timeout_ms, c.max_inflight_total });
        p->StartReaper();
        log::Info("game backend: interim dispatcher ready (game.state only)");
        return *p;
    }();
    return d;
}
}
```

`plugin/src/main.cpp`: add `#include "PythonHost.h"` is already there (Task 25); replace the body of `OnMessage` so the host starts after the lifecycle has seen `kDataLoaded`:
```cpp
void OnMessage(SKSE::MessagingInterface::Message* m)
{
    try {
        spy::life::OnMessage(m);
        if (m->type == SKSE::MessagingInterface::kDataLoaded) spy::host::Start(spy::settings::Get());
    } catch (const std::exception& e) {
        spy::log::Error(std::string("message handler threw: ") + e.what());
    } catch (...) {
        spy::log::Error("message handler threw");
    }
}
```

`plugin/CMakeLists.txt`: add `src/GameBackend.cpp src/PyModule.cpp` to `SPY_CORE_SOURCES`.

- [ ] **Step 3: Deploy script: none (ruling R42)**

The game runs on the Windows box; `tools/box/box_deploy.sh` (Task 1b) already has `--package` (`python/src/skython` → `Plugins\Skython\lib\skython`) and `--scripts DIR` (each entry → `Plugins\Skython\scripts\`), both without `__pycache__`, one mode per call. `tools/deploy.sh` is not changed. Deploying never removes anything: a module deleted from `python/src/skython` stays on the box until the owner approves removing it (`tools/box/box_teardown_list.sh` lists the folders).

- [ ] **Step 4: Live-test helpers**

`livetest/support.py` (standard library only; frame shapes are those of `protocol/v1/fixtures/hello.json` and `requests.json`, Task 11):
```python
import json
import os
import pathlib
import socket
import struct
import subprocess
import time
from dataclasses import dataclass

MAX_FRAME = 16 * 1024 * 1024
REPO = pathlib.Path(__file__).resolve().parent.parent
# The game runs on the Windows box (Task 1c): out/box-live.env sets SKYTHON_BOX=1, SKSE_LOG_DIR and
# SKYTHON_LOG to the local mirror out/box-skse, and SKYTHON_SESSION to the tunnel's session file.
BOX = os.environ.get("SKYTHON_BOX") == "1"


@dataclass
class Session:
    port: int
    token: str


def load_session() -> Session:
    path = os.environ.get("SKYTHON_SESSION")
    if not path:
        raise RuntimeError("SKYTHON_SESSION is not set (tools/box/box_session.sh up && source out/box-live.env)")
    data = json.loads(pathlib.Path(path).read_text())
    if data.get("active") is False:
        raise RuntimeError(f"the bridge is not serving: {data.get('reason')}")
    return Session(port=int(data["port"]), token=str(data["token"]))


def encode(obj) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack("<I", len(body)) + body


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("connection closed")
        buf += chunk
    return buf


def read_frame(sock: socket.socket) -> dict:
    (n,) = struct.unpack("<I", _read_exact(sock, 4))
    return json.loads(_read_exact(sock, n))


def hello(session: Session, **override) -> bytes:
    msg = {"type": "hello", "protocol": [1, 0], "token": session.token, "client": "livetest-raw"}
    msg.update(override)
    return encode(msg)


def raw_connect(session: Session) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(hello(session))
    assert read_frame(s)["type"] == "welcome"
    return s


def send_request(sock: socket.socket, op: str, args: dict | None = None, req_id: int = 1) -> None:
    sock.sendall(encode({"type": "request", "id": req_id, "op": op, "args": args or {}}))


def rpc(sock: socket.socket, op: str, args: dict | None = None, req_id: int = 1, timeout: float = 30.0) -> dict:
    """Send one request and return the reply with that id (replies may arrive out of order)."""
    sock.settimeout(timeout)
    send_request(sock, op, args, req_id)
    while True:
        r = read_frame(sock)
        if r.get("id") == req_id:
            return r


def send_raw(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def closed_by_server(sock: socket.socket, timeout: float = 3.0) -> bool:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if sock.recv(65536) == b"":
                return True
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except socket.timeout:
        return False
    return False


def frame_header(n: int) -> bytes:
    return struct.pack("<I", n)


def read_frame_or_none(sock: socket.socket, timeout: float = 3.0):
    sock.settimeout(timeout)
    try:
        return read_frame(sock)
    except Exception:  # noqa: BLE001 - EOF, reset, timeout or a truncated frame all mean "no frame"
        return None


def still_serving(session: Session) -> bool:
    raw_connect(session).close()
    return True


def _box_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(REPO / "tools" / "box" / script), *args], capture_output=True, text=True, timeout=180)


def box_pull(*names: str) -> None:
    """Box mode: refresh out/box-skse/<name> with the text written on the box since the last launch (empty if none)."""
    r = _box_tool("box_files.sh", "pull", *names)
    if r.returncode != 0:
        raise RuntimeError(f"box_files.sh pull {' '.join(names)} failed: {r.stderr.strip()}")


def _skse_dir() -> pathlib.Path | None:
    d = os.environ.get("SKSE_LOG_DIR")
    return pathlib.Path(d) if d else None


def fresh(path: pathlib.Path) -> bool:
    """True when the file was written after the last launch stamp (so it belongs to this run). In box mode the mirror
    out/box-skse holds only text the box wrote since its launch stamp (box_files.sh pull), and is newer than any stamp."""
    stamp = pathlib.Path(os.environ.get("SKYTHON_STATE", "")) / "launch.stamp"
    return path.exists() and (not stamp.exists() or path.stat().st_mtime >= stamp.stat().st_mtime)


def log_text(name: str = "Skython.log") -> str:
    """A log in SKSE_LOG_DIR from this run, or '' (log assertions then fail with a clear message)."""
    if BOX:
        box_pull(name)
    d = _skse_dir()
    p = d / name if d else None
    return p.read_text(errors="replace") if p and fresh(p) else ""


def python_log_text() -> str:
    if BOX:
        box_pull("Skython/python.log")
    p = pathlib.Path(os.environ.get("SKYTHON_LOG", ""))
    return p.read_text(errors="replace") if p.name and fresh(p) else ""


def refused(port: int = 45977) -> bool:
    if BOX:  # through the ssh tunnel a port nobody listens on is accepted locally, then closed: ask the box
        r = _box_tool("box_session.sh", "probe", str(port))
        if "NO_LISTENER" in r.stdout:
            return True
        if r.stdout.startswith("LISTEN"):
            return False
        raise RuntimeError(f"box_session.sh probe {port} failed: {r.stdout}{r.stderr}")
    try:
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
    except (ConnectionRefusedError, socket.timeout, OSError):
        return True
    return False
```

`livetest/conftest.py`:
```python
"""Live tests: the game runs with Skython (loader + core), the embedded Python and the dev ini.
Run from the repo root, the game on the Windows box (Task 1c):
  tools/box/box_session.sh up && source out/box-live.env && SKYTHON_LIVE=1 uv run --project python pytest livetest -q
Without SKYTHON_LIVE=1 every test is skipped (so a plain pytest run on a machine without the game is clean).
"""
import os

import pytest

from support import load_session


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SKYTHON_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SKYTHON_LIVE=1 with the game running")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def session():
    return load_session()


@pytest.fixture
def game(session):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest")
    yield g
    g.close()
```

- [ ] **Step 5: Live tests (write them before the first deploy)**

`livetest/test_m1_bridge.py` (default dev run, game at the main menu or later):
```python
import json
import os
import pathlib
import re
import socket
import threading
import time

from support import (MAX_FRAME, closed_by_server, encode, frame_header, hello, log_text, python_log_text, raw_connect,
                     read_frame, read_frame_or_none, rpc, send_raw, still_serving)

BAD_OP = {"type": "request", "id": 5, "op": "no.such.op", "args": {}}


def test_python_is_running_in_the_game():
    assert "python host: running" in log_text(), "the core log has no 'python host: running' line from this run"
    assert "SpyCore_Load returned true" in log_text("SkythonLoader.log")
    assert python_log_text(), "python.log was not written in this run"


def test_a_failing_script_is_logged_and_python_keeps_running(session):
    text = python_log_text() + log_text()
    assert "livetest_broken" in text and "RuntimeError: livetest_broken setup raises on purpose" in text
    assert "python host: running" in log_text()
    assert still_serving(session)


def test_welcome(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(hello(session))
    w = read_frame(s)
    assert w["type"] == "welcome" and w["protocol"][0] == 1
    assert w["game_version"] == "1.6.1170.0" and w["runtime"] == "AE" and w["plugin_version"]


def test_wrong_token_is_unauthorized(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(hello(session, token="0" * 32))
    reply = read_frame(s)
    assert reply["type"] == "handshake_error" and reply["code"] == "unauthorized"
    assert closed_by_server(s)


def test_malformed_hellos_never_kill_the_bridge(session):
    for override in ({"type": 1}, {"client": 5}, {"protocol": "1.0"}, {"protocol": [1]}, {"token": 12}):
        s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
        s.sendall(hello(session, **override))
        reply = read_frame_or_none(s)
        assert reply is None or reply["type"] == "handshake_error", override
    assert still_serving(session)


def test_garbage_frame_closes_only_that_connection(session):
    keep = raw_connect(session)
    bad = raw_connect(session)
    body = b"this is not json"
    send_raw(bad, frame_header(len(body)) + body)
    assert closed_by_server(bad)
    assert rpc(keep, "game.state", req_id=2)["ok"] is True  # the other connection is untouched
    assert still_serving(session)


def test_oversized_header_closes_connection(session):
    bad = raw_connect(session)
    send_raw(bad, frame_header(MAX_FRAME + 1))
    assert closed_by_server(bad)
    assert still_serving(session)


def test_deeply_nested_json_closes_only_that_connection(session):
    s = raw_connect(session)
    depth = 200_000
    body = b'{"type":"request","id":1,"op":"game.state","args":{"x":' + b"[" * depth + b"]" * depth + b"}}"
    send_raw(s, frame_header(len(body)) + body)
    assert closed_by_server(s, timeout=5.0)
    assert still_serving(session)


def test_unknown_op_is_bad_request_and_connection_stays_open(session):
    s = raw_connect(session)
    s.sendall(encode(BAD_OP))
    reply = read_frame(s)
    assert reply["id"] == 5 and reply["ok"] is False and reply["error"]["code"] == "bad_request"
    assert rpc(s, "game.state", req_id=6)["ok"] is True


def test_game_state_raw(session):
    r = rpc(raw_connect(session), "game.state", req_id=7)
    assert r["ok"] is True
    st = r["result"]
    assert st["data_ready"] is True and isinstance(st["epoch"], int) and isinstance(st["menus"], list)


def test_game_state_through_skython_remote(game):
    st = game.state()
    assert st["data_ready"] is True and "interactive" in st


def test_two_clients_get_their_own_replies(session):
    results = {}

    def worker(name, base):
        s = raw_connect(session)
        for i in range(base, base + 50):
            s.sendall(encode({**BAD_OP, "id": i}))
        results[name] = sorted(read_frame(s)["id"] for _ in range(50))
        s.close()

    threads = [threading.Thread(target=worker, args=(n, b)) for n, b in (("a", 1000), ("b", 2000))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results["a"] == list(range(1000, 1050)) and results["b"] == list(range(2000, 2050))


def test_requests_then_immediate_close(session):
    for reset in (False, True):
        s = raw_connect(session)
        for i in range(1, 21):
            s.sendall(encode({"type": "request", "id": i, "op": "game.state", "args": {}}))
        if reset:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        s.close()
    time.sleep(0.5)
    assert still_serving(session)


def test_session_file(session):
    p = pathlib.Path(os.environ["SKYTHON_SESSION"])
    data = json.loads(p.read_text())
    assert re.fullmatch(r"[0-9a-f]{32}", data["token"]) and data["port"] > 0
    assert data["protocol"] == [1, 0] and data["plugin_version"]
    raw_connect(session).close()  # the token in the file is the one the bridge accepts
```

`livetest/test_m1_startup_variants.py` (one variant per game start; Step 7 says how to stage each):
```python
import os

import pytest

from support import load_session, log_text, raw_connect, refused

VARIANT = os.environ.get("SPY_VARIANT", "")


def only(name):
    return pytest.mark.skipif(VARIANT != name, reason=f"run with SPY_VARIANT={name} (Task 26 Step 7)")


@only("no-python")
def test_missing_python_folder_disables_python():
    assert "Python disabled: missing" in log_text("SkythonLoader.log")
    assert "python314.dll" in log_text("SkythonLoader.log")
    assert refused()


@only("no-core")
def test_missing_core_dll_disables_python():
    assert "Python disabled: missing" in log_text("SkythonLoader.log")
    assert "SkythonCore.dll" in log_text("SkythonLoader.log")
    assert refused()


@only("no-package")
def test_missing_skython_package_is_a_logged_failure():
    text = log_text()
    assert "python host: failed: skython._host" in text and "No module named 'skython'" in text
    assert refused()


@only("python-off")
def test_python_enabled_false():
    assert "python host: disabled: python_enabled=false" in log_text()
    assert "python host: starting" not in log_text()
    assert refused()


@only("bridge-off")
def test_shipped_ini_opens_no_port():
    assert "python host: running" in log_text()
    assert refused()


@only("collision")
def test_another_python314_already_loaded_disables_python():
    text = log_text("SkythonLoader.log")
    assert "TEST HOOK: preloaded" in text
    assert "another python314.dll is already loaded from" in text
    assert refused()


@only("no-stdlib")
def test_missing_stdlib_zip_is_a_logged_failure():
    text = log_text()
    assert "python host: failed: Py_InitializeFromConfig" in text
    assert "python host: running" not in text
    assert refused()


@only("relaunch")
def test_bridge_listens_after_a_quick_relaunch():
    # The previous run was quit with a bridge client connected and the game relaunched within 60 s (Step 7).
    assert "python host: running" in log_text()
    raw_connect(load_session()).close()  # load_session raises if the session file says the bridge is not serving
```

`livetest/scripts/livetest_broken/__init__.py`:
```python
"""A player script that fails on purpose. Live tests check it is logged with its traceback and harms nothing."""


def setup(game):
    raise RuntimeError("livetest_broken setup raises on purpose")
```

- [ ] **Step 6: Build, deploy, run the default live tests**

Nothing here needs a save loaded; the main menu is enough. Every wait is bounded. The game runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)).
```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --python
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 \
  || echo "STOP: Python did not report within 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_files.sh pull Skython.log >/dev/null && grep -E "python host:|game backend:|py: " "$SKSE_LOG_DIR/Skython.log"
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_bridge.py -q -rs
```
Expected log lines: `game backend: interim dispatcher ready (game.state only)`, `python host: starting (CPython 3.14.8)`, `python host: initialised; sys.executable = ...\Skython\python\python.exe; sys.path = [...\python\python314.zip, ...\python, ...\site-packages, ...\lib, ...\scripts]`, `python host: running`. If `sys.executable` names `SkyrimSE.exe` instead of `python.exe`, `PyConfig.executable` was not applied: fix that before going on. Expected tests: 14 passed. Any skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure (the run did not test anything). The log also shows the `livetest_broken` traceback (expected: it fails on purpose). If `python host: failed:` appears, the traceback follows it in the log; a `ModuleNotFoundError` for a stdlib module means `python314.zip` is not on `sys.path` (check the deployed `python/` folder). The game stays responsive throughout (`tools/box/box_console.sh --tool inspect '{"kind":"state"}'` answers).

- [ ] **Step 7: Startup variants (one game start each)**

The game runs on the box: stage, wait, restore and quit exactly as [Task 1c](task-01c-part-c-on-the-box.md)'s "Startup variants" table says. Every staging change is a recorded move (`tools/box/box_deploy.sh --move`, listed in `out/box-moves.txt`), never a delete, and is moved back afterwards. Quit between runs with `tools/box/box_quit.sh` (`box_stop.sh` on `NOT_CLEAN`; `STILL_RUNNING` after it: STOP and ask the owner). For each variant: `tools/box/box_launch.sh`, `tools/box/box_wait_log.sh <log> "<phrase>" 600`, `source out/box-live.env` (`tools/box/box_session.sh env` writes it), then `SKYTHON_LIVE=1 SPY_VARIANT=<name> uv run --project python pytest livetest/test_m1_startup_variants.py -q -rs`. Expected: 1 passed and 7 skipped, every skip naming another `SPY_VARIANT`. A skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure. Then check that the game reached the main menu normally (`box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600`; for `no-python`/`no-core`, DevBench's `inspect` answers). `refused()` asks the box whether 45977 listens (`box_session.sh probe`), because through the tunnel a closed port looks open.

`no-stdlib` stands in for a partial or corrupt `python/` folder. CPython cannot find its codecs, `Py_InitializeFromConfig` returns an error status, and the host must log one `python host: failed: Py_InitializeFromConfig: ...` line and leave the game running. If instead the game process exits or hangs at startup: record the last lines of both logs (`tools/box/box_files.sh pull Skython.log SkythonLoader.log`), move the zip back, and STOP and ask the owner (a fatal CPython init error is a finding, not something to fix in place).

`relaunch` checks that the bridge's port is free again right after a quick restart (the bridge binds with `SO_EXCLUSIVEADDRUSE` on Windows, Task 23). With the normal dev set deployed, the game at the main menu and `tools/box/box_session.sh up` done:
```bash
source out/box-live.env
(cd livetest && python3 -c "import support; s = support.raw_connect(support.load_session()); s.settimeout(None); s.recv(1)") &
QUIT_WAIT=50 tools/box/box_quit.sh      # the client above stays connected until the game is gone
tools/box/box_launch.sh                 # within 60 s of the quit
tools/box/box_wait_log.sh Skython.log "python host: (running|failed)" 600
tools/box/box_session.sh up             # prints the session path; an inactive file is reported, not tunnelled
python3 -c "import json; d = json.load(open('out/box-session.json')); print({k: d[k] for k in d if k != 'token'})"
```
If `box_quit.sh` prints `NOT_CLEAN` (the box's exit hang, Task 1b Known issue), the 60 s window cannot be met without killing the game. Run `tools/box/box_stop.sh` and record `relaunch` as **not staged on the box (exit hang)** in `spikes/RESULTS.md`; do not loop. If the session file shows `"active": false` with an address-in-use reason, the port was not freed: record it in `spikes/RESULTS.md` and STOP and ask the owner (a finding for Task 23's bind options, not something to work around here).

The `collision` variant stands in for another mod that loaded its own CPython 3.14 first:
```bash
echo "SPY_TEST_HOOKS=ON" > plugin/build-options.local
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll   # build log ends with SPY_TEST_HOOKS=ON
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
for f in third_party/cpython/embed/python314.dll third_party/cpython/embed/vcruntime140*.dll; do
  tools/box/box_deploy.sh --file "$f" "collide-test/$(basename "$f")"
done
tools/box/box_launch.sh --env 'SKYTHON_TEST_PRELOAD=C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\Skython\collide-test\python314.dll'
tools/box/box_wait_log.sh SkythonLoader.log "Python disabled" 600
```
After the test: `tools/box/box_quit.sh`, `: > plugin/build-options.local` (empty: hooks off), rebuild both DLLs (the build log ends with `SPY_TEST_HOOKS=OFF`) and redeploy the dev ini. `collide-test/` stays until the owner approves removing it (it is in `out/box-deployed.txt`). If the loader log shows `TEST HOOK: SKYTHON_TEST_PRELOAD not set` (the variable did not reach the game; check `launch.log`'s `env` line, Task 1b block B10) or `TEST HOOK: preloaded ... (failed)` (the second copy did not load), record the collision row as "not staged" in `spikes/RESULTS.md` with that line, instead of a pass. No `TEST HOOK` line at all means the loader was not built with the hooks: check that the build log said `SPY_TEST_HOOKS=ON` and redeploy.

Finally redeploy the normal set (`tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini`, then `tools/box/box_deploy.sh --package`), relaunch, wait as in Step 6, run `tools/box/box_session.sh up`, and rerun Step 6's tests once (`SKYTHON_LIVE=1`, 14 passed). Quit with `tools/box/box_quit.sh`.

- [ ] **Step 8: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): embedded CPython host thread, _skython in the game, startup failure handling"
```
