# Task 26: Python host thread, `_skyrim` in the game, deploy layout

New in the embedded design (it replaces the C++ loopback server of branch `plan-out-of-process`, task 16). The live-test helpers in Step 6 are adapted from that task's `livetest/support.py` and `test_m1_transport.py`; they now talk to the Python dev bridge (Task 23) running inside the game.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.1, 4.2, 4.5, 4.10, 6. Steps use checkbox (`- [ ]`) syntax.

> **Depends on:** Task 25 (loader, core, settings, lifecycle, `deploy.sh`), Task 24 (`skyrim._host`), Tasks 19-23 (`_skyrim` module source, `skyrim` package, bridge). **Runs on:** the Windows build box plus the game; live tests run on Linux.
>
> **STOP and ask the owner (do not improvise) if:** the game does not reach the main menu within 10 minutes; the game crashes or freezes at any point in this task (record the last lines of both logs first); the build box does not answer `ssh` within 10 s; `python host: failed:` appears in the default run and the traceback does not point at an obvious packaging mistake.

**Files:**
- Modify: `plugin/src/PythonHost.h`, `plugin/src/PythonHost.cpp` (full replacements: the host thread)
- Create: `plugin/src/PyModule.cpp` (`PYBIND11_MODULE(_skyrim, m)` for the game build)
- Create: `plugin/src/GameBackend.h`, `plugin/src/GameBackend.cpp` (**interim**: `game.state` only; Task 27 overwrites both files with the real backend)
- Modify: `plugin/src/main.cpp` (start the host thread at `kDataLoaded`), `plugin/CMakeLists.txt` (two sources)
- Modify: `tools/deploy.sh` (full replacement: adds `--package` and `--scripts DIR`)
- Create: `livetest/support.py`, `livetest/conftest.py`, `livetest/test_m1_bridge.py`, `livetest/test_m1_startup_variants.py`
- Create: `livetest/scripts/livetest_broken/__init__.py` (a player script whose `setup` raises; deployed with every `--scripts livetest/scripts` from here on, so every live run also proves a failing script is harmless)

**Interfaces:**
- Consumes: Task 25 (`spy::log::*`, `spy::settings::{Get, OwnDir, SessionFile, GetRuntime}`, `spy::life::{BaseState, NotReadyReason, LoadsStarted}`, `spy::host::LinkedPythonVersion`, loader log phrases); Task 19 (`spy::ModuleHost`, `spy::InstallModuleHost`, `spy::DefineModule` in `pyglue/include/spy/pymodule.h`); Task 18 (`spy::Backend`, `Dispatcher`, `DispatcherOptions`, `OpResult`); Task 16 (`config_to_json`, `dump_json`); Task 24 (`skyrim._host.main()`); Task 22 (`skyrim.connect` falling back to `connect_remote`).
- Produces:
  - `spy::host::Start(const Config&)` (call once at `kDataLoaded`; returns at once; never throws) and `spy::host::State() -> std::string` (`"disabled: <why>"`, `"starting"`, `"running"`, `"failed: <why>"`).
  - Core log phrases: `python host: disabled: python_enabled=false`, `python host: starting (CPython 3.14.8)`, `python host: initialised; sys.path = [...]`, `python host: running`, `python host: failed: <why>` (followed by the traceback when there is one).
  - `spy::game::Backend()` and `spy::game::GetDispatcher()` with the interim behaviour (Task 27 replaces the implementation, same names). Interim log line: `game backend: interim dispatcher ready (game.state only)`.
  - `tools/deploy.sh out/SkyrimPython.dll [ini] [--python] [--package] [--scripts DIR]`.
  - `livetest/support.py` (stdlib only): `Session(port, token)`, `load_session()`, `encode`, `read_frame`, `hello(session, **override)`, `raw_connect`, `rpc(sock, op, args=None, req_id=1, timeout=30.0)`, `send_request(sock, op, args=None, req_id=1)`, `send_raw`, `closed_by_server`, `frame_header`, `read_frame_or_none`, `still_serving`, `log_text(name="SkyrimPython.log")`, `python_log_text()`, `fresh(path)`, `refused(port=45977)`. `livetest/conftest.py`: fixtures `session` and `game` (`skyrim.connect(...)` over the bridge); skips everything unless `SKYRIM_LIVE=1`.

Rules (Global Constraints, spec 4.5):
- `PyImport_AppendInittab("_skyrim", PyInit__skyrim)` and `InstallModuleHost` happen on the host thread **before** `Py_InitializeFromConfig`. Nothing Python-related runs at DLL load time.
- `PyConfig`: `PyConfig_InitIsolatedConfig`, then `isolated = 1`, `use_environment = 0`, `install_signal_handlers = 0`, `site_import = 1`, `write_bytecode = 0` (no `__pycache__` written into the game's Data folder), `user_site_directory = 0`, `pathconfig_warnings = 0`, `home` = the python folder, `module_search_paths_set = 1` with exactly `python\python314.zip`, `python\`, `site-packages\`, `lib\`, `scripts\` (the zip's `python314._pth` is then ignored).
- Before `skyrim._host` is imported, `sys.stdout`/`sys.stderr` are replaced by writers that forward whole lines to `_skyrim.log` (a GUI process has no console; without this, `print` and early tracebacks vanish).
- After `main()` returns or fails, the host thread calls `PyEval_SaveThread()` and parks forever. It never ends while holding the GIL. **Never `Py_Finalize`.**
- Collision variant: staged with a test-only loader build (`SPY_TEST_HOOKS=ON`, Task 25) that preloads a second copy of `python314.dll` from another folder, named by the environment variable `SKYRIM_PYTHON_TEST_PRELOAD`. Normal builds do not contain the hook (`build.ps1` always passes `-DSPY_TEST_HOOKS=OFF` unless `plugin/build-options.local` says `ON`).

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

extern "C" PyObject* PyInit__skyrim();  // defined by PYBIND11_MODULE in PyModule.cpp

namespace spy::host {
namespace {
namespace fs = std::filesystem;
namespace py = pybind11;

std::mutex g_mx;
std::string g_state = "disabled: not started";
std::atomic_bool g_started{ false };

void SetState(std::string s) { std::lock_guard l(g_mx); g_state = std::move(s); }
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

constexpr const char* kStdio = R"PY(
import sys, _skyrim
class _SpyStream:
    def __init__(self, level):
        self.level, self.buf = level, ""
    def write(self, text):
        text = str(text)
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                _skyrim.log(self.level, line)
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
    static std::mutex m;
    static std::condition_variable cv;
    std::unique_lock l(m);
    for (;;) cv.wait(l);  // never notified; spurious wake-ups just wait again
}

void Run(const Config cfg)
{
    const auto& rt = settings::GetRuntime();
    const fs::path own = settings::OwnDir();
    const fs::path pyDir = own / "python", site = own / "site-packages", lib = own / "lib", scripts = own / "scripts";
    const fs::path logDir = rt.skse_dir ? *rt.skse_dir / "SkyrimPython" : own / "logs";
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
    if (PyImport_AppendInittab("_skyrim", &PyInit__skyrim) == -1) throw std::runtime_error("PyImport_AppendInittab failed");

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
        c.module_search_paths_set = 1;
        for (const fs::path& p : { pyDir / "python314.zip", pyDir, site, lib, scripts })
            Check(PyWideStringList_Append(&c.module_search_paths, p.c_str()), "module_search_paths");
        Check(Py_InitializeFromConfig(&c), "Py_InitializeFromConfig");
    }

    // From here this thread holds the GIL; every path below ends in PyEval_SaveThread + ParkForever.
    try {
        py::exec(kStdio);
        log::Info("python host: initialised; sys.path = " + py::str(py::module_::import("sys").attr("path")).cast<std::string>());
        py::module_::import("skyrim._host").attr("main")();
        SetState("running");
        log::Info("python host: running");
    } catch (py::error_already_set& e) {
        const std::string tb = Traceback(e);
        Fail("skyrim._host: " + LastLine(tb));
        log::Error(tb);
    } catch (const std::exception& e) {
        Fail(e.what());
    } catch (...) {
        Fail("unknown exception after initialisation");
    }
    PyEval_SaveThread();
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
            try {
                Run(cfg);  // returns only if it failed before Py_InitializeFromConfig succeeded (no GIL held)
            } catch (const std::exception& e) {
                Fail(e.what());
            } catch (...) {
                Fail("unknown exception");
            }
        }).detach();
    } catch (const std::exception& e) {
        Fail(std::string("could not start the host thread: ") + e.what());
    } catch (...) {
        Fail("could not start the host thread");
    }
}

std::string State() { std::lock_guard l(g_mx); return g_state; }
}
```
`py::exec` and `py::module_::import` are used only while this thread holds the GIL; every `py::` object is destroyed before `PyEval_SaveThread()` (they live inside the `try` block). `ModuleHost`'s member names are Task 19's; if Task 19 spelled one differently, use its spelling.

- [ ] **Step 2: The game build of `_skyrim`, the interim backend, the wiring**

`plugin/src/PyModule.cpp`:
```cpp
#include <pybind11/pybind11.h>
#include "spy/pymodule.h"

// The game build of _skyrim. PythonHost.cpp registers PyInit__skyrim with PyImport_AppendInittab before
// Py_InitializeFromConfig; nothing here runs at DLL load time.
PYBIND11_MODULE(_skyrim, m) { spy::DefineModule(m); }
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

- [ ] **Step 3: Deploy script (full replacement of `tools/deploy.sh`)**

Task 25's script plus `--package` and `--scripts DIR` (both skip `__pycache__`):
```bash
cat > tools/deploy.sh <<'EOF'
#!/usr/bin/env bash
# Usage: tools/deploy.sh out/<Dll>.dll [path/to/ini] [--python] [--package] [--scripts DIR]
#   SkyrimPython.dll (the loader) also deploys out/SkyrimPythonCore.dll into Plugins/SkyrimPython/.
#   --python   embeddable CPython (third_party/cpython/embed) -> Plugins/SkyrimPython/python/
#   --package  python/src/skyrim -> Plugins/SkyrimPython/lib/skyrim/
#   --scripts  every entry of DIR -> Plugins/SkyrimPython/scripts/
# Every copied path is appended to out/deployed.txt (tools/teardown_list.sh lists them; nothing is ever removed).
# A *Spike* DLL needs CONFIRM_THROWAWAY_SAVE=1.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/tools/env.sh"
DLL="${1:-}"; shift || true
[ -f "$DLL" ] || { echo "deploy: no such file: ${DLL:-<none>}" >&2; exit 1; }
INI=""; PYTHON=0; PACKAGE=0; SCRIPTS=""
if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then INI="$1"; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --python) PYTHON=1 ;;
    --package) PACKAGE=1 ;;
    --scripts) shift; SCRIPTS="${1:?deploy: --scripts needs a directory}" ;;
    *) echo "deploy: unknown option $1" >&2; exit 1 ;;
  esac; shift
done
case "$(basename "$DLL")" in
  *Spike*) [ "${CONFIRM_THROWAWAY_SAVE:-0}" = 1 ] || {
      echo "deploy: $DLL is a spike DLL; use a throwaway save and re-run with CONFIRM_THROWAWAY_SAVE=1." >&2; exit 1; } ;;
esac
DEST="$SIMPLESKYRIM_DATA/SKSE/Plugins"; OWN="$DEST/SkyrimPython"
[ -d "$DEST" ] || { echo "deploy: $DEST does not exist" >&2; exit 1; }
mkdir -p "$ROOT/out"
rec() { echo "$1" >> "$ROOT/out/deployed.txt"; }
copy_tree() {  # copy_tree <src-dir> <dest-dir>: files only, no __pycache__
  local src="$1" dst="$2"
  (cd "$src" && find . -name __pycache__ -prune -o -type f -print) | while IFS= read -r f; do
    install -D -m 644 "$src/$f" "$dst/$f"
  done
}
cp -v "$DLL" "$DEST/"; rec "$DEST/$(basename "$DLL")"
if [ "$(basename "$DLL")" = SkyrimPython.dll ]; then
  CORE="$(dirname "$DLL")/SkyrimPythonCore.dll"
  [ -f "$CORE" ] || { echo "deploy: $CORE missing: run tools/remote_build.sh plugin SkyrimPythonCore.dll" >&2; exit 1; }
  if [ "$DLL" -nt "$CORE" ]; then
    echo "deploy: $CORE is older than $DLL: run tools/remote_build.sh plugin SkyrimPythonCore.dll" >&2; exit 1
  fi
  mkdir -p "$OWN"; cp -v "$CORE" "$OWN/"; rec "$OWN/SkyrimPythonCore.dll"
fi
if [ -n "$INI" ]; then
  cp -v "$INI" "$DEST/$(basename "${DLL%.dll}").ini"; rec "$DEST/$(basename "${DLL%.dll}").ini"
fi
if [ "$PYTHON" = 1 ]; then
  EMBED="$ROOT/third_party/cpython/embed"
  [ -f "$EMBED/python314.dll" ] || { echo "deploy: $EMBED has no python314.dll: run tools/fetch_python.sh" >&2; exit 1; }
  mkdir -p "$OWN/python" "$OWN/site-packages" "$OWN/lib" "$OWN/scripts"
  cp -rv "$EMBED/." "$OWN/python/"; rec "$OWN/python"
  rec "$OWN/site-packages"; rec "$OWN/lib"; rec "$OWN/scripts"
fi
if [ "$PACKAGE" = 1 ]; then
  [ -f "$ROOT/python/src/skyrim/__init__.py" ] || { echo "deploy: python/src/skyrim is missing" >&2; exit 1; }
  copy_tree "$ROOT/python/src/skyrim" "$OWN/lib/skyrim"; rec "$OWN/lib/skyrim"
  echo "deploy: skyrim package -> $OWN/lib/skyrim"
fi
if [ -n "$SCRIPTS" ]; then
  [ -d "$SCRIPTS" ] || { echo "deploy: no such directory: $SCRIPTS" >&2; exit 1; }
  copy_tree "$SCRIPTS" "$OWN/scripts"
  for e in "$SCRIPTS"/*; do [ -e "$e" ] && [ "$(basename "$e")" != __pycache__ ] && rec "$OWN/scripts/$(basename "$e")"; done
  echo "deploy: scripts from $SCRIPTS -> $OWN/scripts"
fi
EOF
chmod +x tools/deploy.sh && bash -n tools/deploy.sh
```
Deploying never removes anything: a module deleted from `python/src/skyrim` stays in the game until the owner approves removing it (`tools/teardown_list.sh` lists the folders).

- [ ] **Step 4: Live-test helpers**

`livetest/support.py` (standard library only; frame shapes are those of `protocol/v1/fixtures/hello.json` and `requests.json`, Task 11):
```python
import json
import os
import pathlib
import socket
import struct
import time
from dataclasses import dataclass

MAX_FRAME = 16 * 1024 * 1024


@dataclass
class Session:
    port: int
    token: str


def load_session() -> Session:
    path = os.environ.get("SKYRIM_PYTHON_SESSION")
    if not path:
        raise RuntimeError("SKYRIM_PYTHON_SESSION is not set (source tools/env.sh)")
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


def _skse_dir() -> pathlib.Path | None:
    d = os.environ.get("SKSE_LOG_DIR")
    return pathlib.Path(d) if d else None


def fresh(path: pathlib.Path) -> bool:
    """True when the file was written after the last tools/launch_game.sh (so it belongs to this run)."""
    stamp = pathlib.Path(os.environ.get("SKYRIM_PYTHON_STATE", "")) / "launch.stamp"
    return path.exists() and (not stamp.exists() or path.stat().st_mtime >= stamp.stat().st_mtime)


def log_text(name: str = "SkyrimPython.log") -> str:
    """A log in SKSE_LOG_DIR from this run, or '' (log assertions then fail with a clear message)."""
    d = _skse_dir()
    p = d / name if d else None
    return p.read_text(errors="replace") if p and fresh(p) else ""


def python_log_text() -> str:
    p = pathlib.Path(os.environ.get("SKYRIM_PYTHON_LOG", ""))
    return p.read_text(errors="replace") if p.name and fresh(p) else ""


def refused(port: int = 45977) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
    except (ConnectionRefusedError, socket.timeout, OSError):
        return True
    return False
```

`livetest/conftest.py`:
```python
"""Live tests: the game runs with SkyrimPython (loader + core), the embedded Python and the dev ini.
Run from the repo root:  source tools/env.sh && SKYRIM_LIVE=1 uv run --project python pytest livetest -q
Without SKYRIM_LIVE=1 every test is skipped (so a plain pytest run on a machine without the game is clean).
"""
import os

import pytest

from support import load_session


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SKYRIM_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SKYRIM_LIVE=1 with the game running")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def session():
    return load_session()


@pytest.fixture
def game(session):
    import skyrim
    g = skyrim.connect(port=session.port, token=session.token, client_name="livetest")
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
    assert "SpyCore_Load returned true" in log_text("SkyrimPythonLoader.log")
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


def test_game_state_through_skyrim_remote(game):
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
    p = pathlib.Path(os.environ["SKYRIM_PYTHON_SESSION"])
    data = json.loads(p.read_text())
    assert re.fullmatch(r"[0-9a-f]{32}", data["token"]) and data["port"] > 0
    assert data["protocol"] == [1, 0] and data["plugin_version"]
    raw_connect(session).close()  # the token in the file is the one the bridge accepts
```

`livetest/test_m1_startup_variants.py` (one variant per game start; Step 7 says how to stage each):
```python
import os

import pytest

from support import log_text, python_log_text, raw_connect, load_session, refused

VARIANT = os.environ.get("SPY_VARIANT", "")


def only(name):
    return pytest.mark.skipif(VARIANT != name, reason=f"run with SPY_VARIANT={name} (Task 26 Step 7)")


@only("no-python")
def test_missing_python_folder_disables_python():
    assert "Python disabled: missing" in log_text("SkyrimPythonLoader.log")
    assert "python314.dll" in log_text("SkyrimPythonLoader.log")
    assert refused()


@only("no-core")
def test_missing_core_dll_disables_python():
    assert "Python disabled: missing" in log_text("SkyrimPythonLoader.log")
    assert "SkyrimPythonCore.dll" in log_text("SkyrimPythonLoader.log")
    assert refused()


@only("no-package")
def test_missing_skyrim_package_is_a_logged_failure():
    text = log_text()
    assert "python host: failed: skyrim._host" in text and "No module named 'skyrim'" in text
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
    text = log_text("SkyrimPythonLoader.log")
    assert "TEST HOOK: preloaded" in text
    assert "another python314.dll is already loaded from" in text
    assert refused()
```

`livetest/scripts/livetest_broken/__init__.py`:
```python
"""A player script that fails on purpose. Live tests check it is logged with its traceback and harms nothing."""


def setup(game):
    raise RuntimeError("livetest_broken setup raises on purpose")
```

- [ ] **Step 6: Build, deploy, run the default live tests**

Nothing here needs a save loaded; the main menu is enough. Every wait is bounded.
```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --python --package --scripts livetest/scripts
tools/launch_game.sh
source tools/env.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "python host: (running|failed|disabled)" 600 \
  || echo "STOP: Python did not report within 10 minutes; ask the owner"
grep -E "python:|game backend:" "$SKSE_LOG_DIR/SkyrimPython.log"
SKYRIM_LIVE=1 uv run --project python pytest livetest/test_m1_bridge.py -q
```
Expected log lines: `game backend: interim dispatcher ready (game.state only)`, `python host: starting (CPython 3.14.8)`, `python host: initialised; sys.path = [...\python\python314.zip, ...\python, ...\site-packages, ...\lib, ...\scripts]`, `python host: running`. Expected tests: 14 passed. The log also shows the `livetest_broken` traceback (expected: it fails on purpose). If `python host: failed:` appears, the traceback follows it in the log; a `ModuleNotFoundError` for a stdlib module means `python314.zip` is not on `sys.path` (check the deployed `python/` folder). The game stays responsive throughout (open and close the console once to check).

- [ ] **Step 7: Startup variants (one game start each)**

Quit the game from its main menu between runs; if it will not exit, STOP and ask the owner before killing anything. Each variant changes the deployed files by **moving** them, never deleting, and moves them back afterwards. `OWN="$SIMPLESKYRIM_DATA/SKSE/Plugins/SkyrimPython"`. For each, launch with `tools/launch_game.sh`, wait with `SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh <log> <phrase> 600`, then run `SKYRIM_LIVE=1 SPY_VARIANT=<name> uv run --project python pytest livetest/test_m1_startup_variants.py -q` (expected: 1 passed, 5 skipped) and check the game reached the main menu normally.

| Variant | Stage | Wait for (log, phrase) | Restore |
|---|---|---|---|
| `no-python` | `mv "$OWN/python" "$OWN/python.off"` | loader log, `Python disabled` | `mv "$OWN/python.off" "$OWN/python"` |
| `no-core` | `mv "$OWN/SkyrimPythonCore.dll" "$OWN/SkyrimPythonCore.dll.off"` | loader log, `Python disabled` | move it back |
| `no-package` | `mv "$OWN/lib/skyrim" "$OWN/lib/skyrim.off"` | core log, `python host: failed` | move it back |
| `python-off` | `tools/deploy.sh out/SkyrimPython.dll plugin/config/variants/python-off.ini` | core log, `python host: disabled` | redeploy the dev ini |
| `bridge-off` | `tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.ini` | core log, `python host: running` | redeploy the dev ini |
| `collision` | see below | loader log, `Python disabled` | see below |

The `collision` variant stands in for another mod that loaded its own CPython 3.14 first:
```bash
echo "SPY_TEST_HOOKS=ON" > plugin/build-options.local
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll   # build log ends with SPY_TEST_HOOKS=ON
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
source tools/env.sh
OTHER="$SIMPLESKYRIM_DATA/SKSE/SpyCollideTest"; mkdir -p "$OTHER"
cp -v third_party/cpython/embed/python314.dll third_party/cpython/embed/vcruntime140*.dll "$OTHER/" && echo "$OTHER" >> out/deployed.txt
export SKYRIM_PYTHON_TEST_PRELOAD="Z:$(printf '%s' "$OTHER/python314.dll" | tr '/' '\\')"
tools/launch_game.sh
unset SKYRIM_PYTHON_TEST_PRELOAD
```
After the test: quit the game, `: > plugin/build-options.local` (empty: hooks off), rebuild both DLLs (the build log ends with `SPY_TEST_HOOKS=OFF`) and redeploy the dev ini. `SpyCollideTest/` stays until the owner approves removing it (it is listed in `out/deployed.txt`). If the loader log shows `TEST HOOK: preloaded ... (failed)`, Wine did not pass the variable through: record the collision row as "not staged" in `spikes/RESULTS.md` instead of a pass.

Finally redeploy the normal set (`tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --package`), relaunch, and rerun Step 6's tests once (14 passed).

- [ ] **Step 8: Commit**

```bash
git add plugin tools/deploy.sh livetest && git commit -m "feat(plugin): embedded CPython host thread, _skyrim in the game, startup failure handling"
```
