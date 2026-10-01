# Task 19: The `_skyrim` module, Linux build and tests

New in the embedded design (no counterpart on branch `plan-out-of-process`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts** (the `_skyrim` entry is this task's interface). Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2, 4.5). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 18 (`Dispatcher`, `FakeBackend`), Task 16 (`parse_config`, `config_to_json`), Task 0 (pybind11 3.1.0 vendored in `third_party/pybind11/`, uv's CPython 3.14 installed). Linux only; no game.

**What this is.** `_skyrim` is the only boundary between Python and the game (spec 4.2): request text in, response text out, plus the lifecycle state, the settings and a log line. `pyglue/src/pymodule.cpp` is the one source of the module; this task builds it on Linux as a real CPython 3.14 extension over core's `FakeBackend`, and the plugin core builds the same file into `SkyrimPythonCore.dll` over the game backend (Tasks 26-27). Every function that can wait releases the GIL (`py::call_guard<py::gil_scoped_release>`): arguments are converted to C++ before the release and results back to Python after the GIL is taken again, so only C++ work runs without it. That is what lets other Python threads (the bridge, player scripts, skyrim-dm's 66-second generation) run while one thread waits for the game (Review Focus 6). C++ exceptions reach Python as Python exceptions: `std::invalid_argument` as `ValueError` (a negative or NaN timeout), wrong argument types as `TypeError`, anything else as `RuntimeError`.

`call(request, timeout)` is `submit` plus `wait`. Normally the Dispatcher's own deadline (the request's `timeout_ms`) ends a slow call first, with a `timeout` response; if the caller's `timeout` (seconds) runs out before that, `call` cancels the ticket and returns a `timeout` response for the same id with `detail.late_cancel = true`, so a caller always sees the `Timeout` class for an expired call, never `internal`.

**The fake build** (`pyglue/linux/fake_module.cpp`) adds `_fake_set_not_ready(reason | None)`, `_fake_bump_loads()` and `_fake_set_paused(flag)`, and reads two environment variables: `SKYRIM_FAKE_DIR` (the folder under which `config()` puts `python_dir`, `scripts_dir`, `site_packages_dir`, `log_dir` = `<dir>/logs` and `session_file` = `<dir>/logs/session.json`; default `<temp>/skyrim-fake`), and `SKYRIM_FAKE_INI` (ini text parsed like `SkyrimPython.ini`; `config()` reads it on every call, so a test can set `bridge_enabled = true`; the dispatcher reads `default_timeout_ms` and `max_inflight_total` from it **once, at import**, so the busy-cap test sets it for a fresh interpreter). `log(level, message)` appends `<level> <message>` to `<log_dir>/plugin.log`. The fake's backend and dispatcher are deliberately never destroyed (their threads live until the process exits), as in the game, which never finalises Python.

**Files:**
- Create: `pyglue/include/spy/pymodule.h`, `pyglue/src/pymodule.cpp`
- Create: `pyglue/linux/CMakeLists.txt`, `pyglue/linux/fake_module.cpp`
- Create: `pyglue/tests/conftest.py`, `pyglue/tests/test_module.py`, `pyglue/tests/test_threads.py`
- The build directory `pyglue/build/` is ignored by the `build/` rule in `.gitignore` (Task 0).

**Interfaces:**
- Consumes: `spy::Dispatcher` (including `BaseState()`), `spy::FakeBackend`, `spy::Ticket` (Task 18); `spy::parse_config`, `spy::config_to_json`, `spy::Config`, `spy::peek_request_id`, `spy::make_error`, `spy::dump_json` (Task 16); `spy::kProtocolMajor/Minor` (Task 12).
- Produces:
  - `pyglue/include/spy/pymodule.h`: `struct spy::ModuleHost { Dispatcher* dispatcher = nullptr; const char* backend = "game"; std::function<std::string()> config_json; std::function<void(const std::string&, const std::string&)> log; FakeBackend* fake = nullptr; }`, `void spy::InstallModuleHost(ModuleHost)` (call before the module is imported), `void spy::DefineModule(pybind11::module_&)`. Task 26 compiles `pymodule.cpp` into the plugin core with `PYBIND11_MODULE(_skyrim, m) { spy::DefineModule(m); }` and registers `PyInit__skyrim` with `PyImport_AppendInittab`.
  - The Python-visible `_skyrim` API exactly as in the index Contracts: `BACKEND`, `PROTOCOL`, `submit`, `wait`, `wait_any`, `cancel`, `call`, `state`, `config`, `log`, `in_flight`; fake build only `_fake_set_not_ready`, `_fake_bump_loads`, `_fake_set_paused`.
  - A built module at `pyglue/build/_skyrim.cpython-314-x86_64-linux-gnu.so`. The Python package tests (Tasks 21-24) use the pure-Python stand-in `python/tests/fake_skyrim.py` (Task 21) instead, so they need no C++ build; this module is what that stand-in imitates.

- [ ] **Step 1: Write the failing tests**

`pyglue/tests/conftest.py`:
```python
"""Shared setup for the _skyrim module tests (Task 19).

The module under test is the real C++ extension, built by pyglue/linux/CMakeLists.txt against uv's CPython 3.14 with
the fake backend. Its build directory goes on sys.path; SKYRIM_PYGLUE_BUILD overrides where it is.
"""
import json
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = pathlib.Path(os.environ.get("SKYRIM_PYGLUE_BUILD", ROOT / "pyglue" / "build"))
sys.path.insert(0, str(BUILD))

# _skyrim.config() and _skyrim.log() use this folder; set it before the first import.
FAKE_DIR = pathlib.Path(tempfile.mkdtemp(prefix="skyrim-fake-"))
os.environ["SKYRIM_FAKE_DIR"] = str(FAKE_DIR)

import _skyrim  # noqa: E402  (needs the path above)


def request(req_id, function, *args, op="papyrus.call_static", **extra):
    """Request text for Fake.<function>(*args); extra keys (timeout_ms, require_unpaused) go into args."""
    if op == "papyrus.call_static":
        body = {"script": "Fake", "function": function, "args": list(args), **extra}
    else:
        body = dict(extra)
    return json.dumps({"type": "request", "id": req_id, "op": op, "args": body})


def response(text):
    """Parse response text and check the envelope every response has."""
    r = json.loads(text)
    assert r["type"] == "response"
    assert isinstance(r["ok"], bool)
    return r


def code(r):
    return "ok" if r["ok"] else r["error"]["code"]


@pytest.fixture(autouse=True)
def ready_game():
    """Every test starts and ends with a ready, unpaused fake game."""
    _skyrim._fake_set_not_ready(None)
    _skyrim._fake_set_paused(False)
    yield
    _skyrim._fake_set_not_ready(None)
    _skyrim._fake_set_paused(False)
```

`pyglue/tests/test_module.py`:
```python
"""The _skyrim module API (index Contracts), against the fake backend."""
import json
import math
import os
import subprocess
import sys
import time

import pytest

import _skyrim
from conftest import BUILD, FAKE_DIR, code, request, response


def test_surface():
    assert _skyrim.BACKEND == "fake"
    assert _skyrim.PROTOCOL == (1, 0)
    for name in ("submit", "wait", "wait_any", "cancel", "call", "state", "config", "log", "in_flight",
                 "_fake_set_not_ready", "_fake_bump_loads", "_fake_set_paused"):
        assert callable(getattr(_skyrim, name)), name


def test_call_round_trip():
    r = response(_skyrim.call(request(1, "Echo", "hello"), 5.0))
    assert r == {"type": "response", "id": 1, "ok": True, "result": "hello"}
    form = {"kind": "form", "form_id": 20}
    assert response(_skyrim.call(request(2, "Echo", form), 5.0))["result"] == form


def test_wait_returns_none_until_done_then_the_result_once():
    t = _skyrim.submit(request(3, "Sleep", 300))
    assert isinstance(t, int)
    assert _skyrim.wait(t, 0.05) is None
    assert response(_skyrim.wait(t, 5.0))["result"] == 300
    assert _skyrim.wait(t, 0.01) is None  # consumed


def test_wait_any_returns_only_the_finished_ones():
    slow = _skyrim.submit(request(4, "Sleep", 2000))
    fast = _skyrim.submit(request(5, "Echo", 5))
    got = _skyrim.wait_any([slow, fast], 5.0)
    assert [t for t, _ in got] == [fast]
    assert response(got[0][1])["id"] == 5
    assert _skyrim.wait_any([slow], 0.01) == []
    _skyrim.cancel(slow)
    assert _skyrim.wait_any([], 0.01) == []


def test_cancel_frees_the_slot_and_the_ticket():
    before = _skyrim.in_flight()
    t = _skyrim.submit(request(6, "Never"))
    assert _skyrim.in_flight() == before + 1
    _skyrim.cancel(t)
    assert _skyrim.in_flight() == before
    assert _skyrim.wait(t, 0.05) is None
    _skyrim.cancel(t)  # twice is harmless


def test_call_whose_wait_expires_is_a_timeout_with_late_cancel():
    before = _skyrim.in_flight()
    start = time.monotonic()
    r = response(_skyrim.call(request(7, "Never", timeout_ms=60000), 0.2))
    assert 0.15 < time.monotonic() - start < 2.0
    assert r["id"] == 7
    assert code(r) == "timeout"
    assert r["error"]["detail"]["late_cancel"] is True
    assert {"paused", "menus"} <= set(r["error"]["detail"])
    assert _skyrim.in_flight() == before


def test_the_dispatcher_times_out_a_call_by_its_timeout_ms():
    r = response(_skyrim.call(request(8, "Never", timeout_ms=100), 5.0))
    assert code(r) == "timeout"
    assert "late_cancel" not in r["error"]["detail"]
    assert {"paused", "menus"} <= set(r["error"]["detail"])


def test_not_ready_answers_at_once_but_game_state_still_works():
    _skyrim._fake_set_not_ready("loading")
    start = time.monotonic()
    r = response(_skyrim.call(request(9, "Echo", 1), 5.0))
    assert time.monotonic() - start < 1.0
    assert code(r) == "not_ready"
    assert r["error"]["detail"]["reason"] == "loading"
    s = response(_skyrim.call(json.dumps({"type": "request", "id": 10, "op": "game.state", "args": {}}), 5.0))
    assert s["ok"] and s["result"]["backend"] == "fake" and s["result"]["loading"] is True
    _skyrim._fake_set_not_ready(None)
    assert code(response(_skyrim.call(request(11, "Echo", 1), 5.0))) == "ok"


def test_paused_only_refuses_calls_that_require_unpaused():
    _skyrim._fake_set_paused(True)
    r = response(_skyrim.call(request(12, "Echo", 1, require_unpaused=True), 5.0))
    assert code(r) == "not_ready" and r["error"]["detail"]["reason"] == "paused"
    assert code(response(_skyrim.call(request(13, "Echo", 1), 5.0))) == "ok"
    assert json.loads(_skyrim.state())["paused"] is True


def test_a_load_starting_ends_calls_in_flight():
    t = _skyrim.submit(request(14, "Never"))
    _skyrim._fake_bump_loads()
    r = response(_skyrim.wait(t, 2.0))
    assert code(r) == "not_ready" and r["error"]["detail"]["reason"] == "loading"


def test_backend_exception_is_internal_and_errors_keep_their_codes():
    assert code(response(_skyrim.call(request(15, "Throw"), 5.0))) == "internal"
    r = response(_skyrim.call(request(16, "Fail", "vm_error", "no"), 5.0))
    assert code(r) == "vm_error" and r["error"]["message"] == "no"


def test_bad_request_text_is_bad_request_not_an_exception():
    r = response(_skyrim.call("this is not json", 5.0))
    assert code(r) == "bad_request" and r["id"] == 0
    r = response(_skyrim.call(json.dumps({"type": "request", "id": 17, "op": "nope", "args": {}}), 5.0))
    assert code(r) == "bad_request" and r["id"] == 17


@pytest.mark.parametrize("bad", [-1.0, math.nan])
def test_bad_timeouts_are_value_errors(bad):
    with pytest.raises(ValueError):
        _skyrim.wait(1, bad)
    with pytest.raises(ValueError):
        _skyrim.call(request(18, "Echo", 1), bad)


def test_wrong_argument_types_are_type_errors():
    with pytest.raises(TypeError):
        _skyrim.submit(42)
    with pytest.raises(TypeError):
        _skyrim.wait(-1, 1.0)


def test_state_is_the_base_state():
    s = json.loads(_skyrim.state())
    assert set(s) == {"data_ready", "game_loaded", "loading", "interactive", "paused", "menus", "epoch"}


def test_config_has_the_ini_fields_and_the_fake_paths(monkeypatch):
    monkeypatch.setenv("SKYRIM_FAKE_INI", "bridge_enabled = true\nport = 0\n")
    c = json.loads(_skyrim.config())
    assert c["bridge_enabled"] is True and c["port"] == 0
    assert c["python_enabled"] is True and c["default_timeout_ms"] == 5000
    assert (c["plugin_version"], c["game_version"], c["runtime"]) == ("0.0.0-fake", "0.0.0", "fake")
    for key in ("python_dir", "scripts_dir", "site_packages_dir", "log_dir", "session_file"):
        assert c[key].startswith(str(FAKE_DIR)), key
    assert c["session_file"] == os.path.join(c["log_dir"], "session.json")


def test_log_appends_to_the_fake_plugin_log():
    _skyrim.log("info", "hello from the test")
    log_dir = json.loads(_skyrim.config())["log_dir"]
    with open(os.path.join(log_dir, "plugin.log"), encoding="utf-8") as f:
        assert "info hello from the test" in f.read()


def test_busy_cap_from_the_fake_ini():
    # The fake build reads max_inflight_total once, when the module is imported, so this runs in a fresh interpreter.
    code_text = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(BUILD)!r})\n"
        "import _skyrim\n"
        "req = lambda i: json.dumps({'type': 'request', 'id': i, 'op': 'papyrus.call_static',"
        " 'args': {'script': 'Fake', 'function': 'Never', 'args': []}})\n"
        "a, b = _skyrim.submit(req(1)), _skyrim.submit(req(2))\n"
        "print(json.loads(_skyrim.call(req(3), 5.0))['error']['code'])\n"
    )
    env = dict(os.environ, SKYRIM_FAKE_INI="max_inflight_total = 2\n")
    out = subprocess.run([sys.executable, "-c", code_text], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "busy"
```

`test_threads.py` holds the two tests that matter most: if `call` kept the GIL while it waits, the counting loop in the first test could not run during the 500 ms sleep (checked while writing this plan: removing the `release()` guard from `call` makes that test fail with `count` near zero).

`pyglue/tests/test_threads.py`:
```python
"""The GIL is released while _skyrim waits, and many Python threads can call at once (Review Focus 6)."""
import threading
import time

import _skyrim
from conftest import request, response


def test_other_python_threads_run_while_a_call_waits():
    done = threading.Event()
    result = {}

    def caller():
        result["r"] = response(_skyrim.call(request(1, "Sleep", 500), 5.0))
        done.set()

    t = threading.Thread(target=caller)
    t.start()
    time.sleep(0.05)  # let the caller get into the wait
    count = 0
    start = time.monotonic()
    while not done.is_set():
        count += 1
    elapsed = time.monotonic() - start
    t.join()
    assert result["r"]["result"] == 500
    assert elapsed > 0.3
    # If call() held the GIL for the 500 ms, this loop could not have run during it. A free loop does millions of
    # iterations in that time; the bound is far below that to stay robust on a slow or busy machine.
    assert count > 10_000, count


def test_sixteen_threads_two_hundred_calls_each():
    errors = []

    def worker(n):
        try:
            for k in range(200):
                value = n * 1000 + k
                r = response(_skyrim.call(request(value, "Echo", value), 10.0))
                if r["id"] != value or r["result"] != value:
                    errors.append((n, k, r))
        except Exception as e:  # noqa: BLE001 - report everything from the thread
            errors.append((n, repr(e)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert _skyrim.in_flight() == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q 2>&1 | tail -2`
Expected: FAIL, `E   ModuleNotFoundError: No module named '_skyrim'` (from `conftest.py`).

- [ ] **Step 3: Write the module**

`pyglue/include/spy/pymodule.h`:
```cpp
#pragma once
#include "spy/dispatcher.h"
#include "spy/fake_backend.h"
#include <functional>
#include <string>
#include <pybind11/pybind11.h>

namespace spy {
// What the _skyrim module talks to. The game build (plugin core, Tasks 26-27) and the Linux fake build
// (pyglue/linux/fake_module.cpp) each fill one in and call InstallModuleHost before the module is imported.
struct ModuleHost
{
    Dispatcher* dispatcher = nullptr;
    const char* backend = "game";                                               // _skyrim.BACKEND
    std::function<std::string()> config_json;                                   // _skyrim.config()
    std::function<void(const std::string& level, const std::string& msg)> log;  // _skyrim.log()
    FakeBackend* fake = nullptr;  // set only by the fake build: adds the _fake_* controls
};

void InstallModuleHost(ModuleHost host);
// Defines every _skyrim function on `m`. Only strings, numbers and None cross the boundary; every call that can
// wait releases the GIL; a C++ exception reaches Python as a Python exception (ValueError, TypeError, RuntimeError).
void DefineModule(pybind11::module_& m);
}
```

`pyglue/src/pymodule.cpp`:
```cpp
#include "spy/pymodule.h"
#include <algorithm>
#include <cmath>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace spy {
namespace {
ModuleHost& Host()
{
    static ModuleHost host;
    return host;
}

Dispatcher& Disp()
{
    if (Host().dispatcher == nullptr) throw std::runtime_error("_skyrim: no dispatcher installed (InstallModuleHost was not called)");
    return *Host().dispatcher;
}

// Seconds (Python float) to milliseconds, rounded up. Negative or NaN is a ValueError; longer than a week is clamped.
std::chrono::milliseconds ToMs(double seconds)
{
    if (std::isnan(seconds) || seconds < 0.0) throw std::invalid_argument("timeout must be a number of seconds, 0 or more");
    constexpr double kMaxSeconds = 7.0 * 24 * 3600;
    return std::chrono::milliseconds(static_cast<std::int64_t>(std::ceil(std::min(seconds, kMaxSeconds) * 1000.0)));
}

// The reply call() gives when its own wait runs out before the dispatcher answered: always the timeout class.
std::string LateCancelReply(std::string_view request, double seconds)
{
    json detail = json::object();
    try {
        const json base = Disp().BaseState();
        detail["paused"] = base.value("paused", false);
        detail["menus"] = base.value("menus", json::array());
    } catch (...) {
        detail["paused"] = false;
        detail["menus"] = json::array();
    }
    detail["late_cancel"] = true;
    return dump_json(make_error(peek_request_id(request).value_or(0), ErrorCode::timeout,
                                "no response within " + std::to_string(seconds) + " s; the call was cancelled", std::move(detail)));
}
}

void InstallModuleHost(ModuleHost host)
{
    Host() = std::move(host);
}

void DefineModule(py::module_& m)
{
    const ModuleHost& h = Host();
    m.doc() = "skyrim-python's boundary between Python and the game: request and response JSON text (spec 4.2).";
    m.attr("BACKEND") = std::string(h.backend != nullptr ? h.backend : "game");
    m.attr("PROTOCOL") = py::make_tuple(kProtocolMajor, kProtocolMinor);

    // Arguments are converted before the guard releases the GIL and results after it is taken back, so only the C++
    // work in between runs without the GIL.
    using release = py::call_guard<py::gil_scoped_release>;

    m.def("submit", [](const std::string& request) { return Disp().Submit(request); }, py::arg("request"), release(),
          "Submit request text; returns a ticket. Never blocks on the game.");
    m.def("wait", [](Ticket ticket, double timeout) { return Disp().Wait(ticket, ToMs(timeout)); },
          py::arg("ticket"), py::arg("timeout"), release(),
          "Response text, or None if not finished within timeout seconds. A returned ticket is consumed.");
    m.def("wait_any", [](const std::vector<Ticket>& tickets, double timeout) { return Disp().WaitAny(tickets, ToMs(timeout)); },
          py::arg("tickets"), py::arg("timeout"), release(),
          "[(ticket, response text)] for the finished ones, as soon as one is finished or timeout seconds pass.");
    m.def("cancel", [](Ticket ticket) { Disp().Cancel(ticket); }, py::arg("ticket"), release(),
          "Forget a ticket; a late result is dropped.");
    m.def("call",
          [](const std::string& request, double timeout) -> std::string {
              const auto ms = ToMs(timeout);
              Dispatcher& d = Disp();
              const Ticket t = d.Submit(request);
              if (auto text = d.Wait(t, ms)) return std::move(*text);
              d.Cancel(t);
              return LateCancelReply(request, timeout);
          },
          py::arg("request"), py::arg("timeout"), release(),
          "submit + wait. If timeout seconds pass first, the call is cancelled and a timeout response with "
          "detail.late_cancel = true is returned.");
    m.def("state", [] { return dump_json(Disp().BaseState()); }, release(), "The lifecycle state as JSON text; never blocks.");
    m.def("config", [] { return Host().config_json ? Host().config_json() : std::string("{}"); }, release(),
          "The plugin's settings and paths as JSON text.");
    m.def("log", [](const std::string& level, const std::string& message) { if (Host().log) Host().log(level, message); },
          py::arg("level"), py::arg("message"), release(), "Write one line to the plugin log.");
    m.def("in_flight", [] { return Disp().InFlight(); }, release(), "Calls submitted and not yet finished.");

    if (h.fake != nullptr) {
        FakeBackend* fake = h.fake;
        m.def("_fake_set_not_ready", [fake](std::optional<std::string> reason) { fake->SetNotReady(std::move(reason)); },
              py::arg("reason"), "Fake build only: 'starting', 'loading', 'no_game', or None for ready.");
        m.def("_fake_bump_loads", [fake] { fake->BumpLoads(); }, "Fake build only: a save starts loading.");
        m.def("_fake_set_paused", [fake](bool paused) { fake->SetPaused(paused); }, py::arg("flag"), "Fake build only.");
    }
}
}
```

- [ ] **Step 4: Write the Linux build with the fake backend**

`pyglue/linux/fake_module.cpp`:
```cpp
// The Linux build of _skyrim: the real module code (pyglue/src/pymodule.cpp) over core's FakeBackend.
// Environment, read by the tests:
//   SKYRIM_FAKE_DIR  folder for config()'s paths and the log (default: <temp>/skyrim-fake)
//   SKYRIM_FAKE_INI  ini text parsed like SkyrimPython.ini; read by config() on every call, and once at import for the
//                    dispatcher's default_timeout_ms and max_inflight_total
#include "spy/config.h"
#include "spy/pymodule.h"
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>

namespace fs = std::filesystem;

namespace {
std::string Env(const char* name)
{
    const char* v = std::getenv(name);
    return v != nullptr ? std::string(v) : std::string();
}

fs::path BaseDir()
{
    const std::string d = Env("SKYRIM_FAKE_DIR");
    return d.empty() ? fs::temp_directory_path() / "skyrim-fake" : fs::path(d);
}

spy::Config FakeConfig() { return spy::parse_config(Env("SKYRIM_FAKE_INI")); }

std::string ConfigJson()
{
    const spy::Config cfg = FakeConfig();
    spy::json j = spy::config_to_json(cfg);
    const fs::path base = BaseDir();
    const fs::path logs = base / "logs";  // holds python.log and session.json, like .../SKSE/SkyrimPython/ in the game
    j["python_dir"] = (base / "python").string();
    j["scripts_dir"] = (base / "scripts").string();
    j["site_packages_dir"] = (base / "site-packages").string();
    j["log_dir"] = logs.string();
    j["session_file"] = cfg.session_file.empty() ? (logs / "session.json").string() : cfg.session_file;
    j["plugin_version"] = "0.0.0-fake";
    j["game_version"] = "0.0.0";
    j["runtime"] = "fake";
    return spy::dump_json(j);
}

void Log(const std::string& level, const std::string& message)
{
    static std::mutex mx;
    std::lock_guard lk(mx);
    const fs::path dir = BaseDir() / "logs";
    std::error_code ec;
    fs::create_directories(dir, ec);
    std::ofstream(dir / "plugin.log", std::ios::app) << level << ' ' << message << '\n';
}
}

PYBIND11_MODULE(_skyrim, m)
{
    // Leaked on purpose: the fake's worker and the dispatcher's reaper live until the process exits, as in the game,
    // which never finalises Python. Destroying them during interpreter shutdown would join threads at a bad time.
    static spy::FakeBackend* fake = new spy::FakeBackend();
    static spy::Dispatcher* dispatcher = [] {
        const spy::Config cfg = FakeConfig();
        auto* d = new spy::Dispatcher(*fake, {cfg.default_timeout_ms, cfg.max_inflight_total});
        d->StartReaper();
        return d;
    }();
    spy::InstallModuleHost({dispatcher, "fake", ConfigJson, Log, fake});
    spy::DefineModule(m);
}
```

`pyglue/linux/CMakeLists.txt`:
```cmake
# Linux build of the _skyrim extension module with the fake backend (Task 19). The game build of the same module
# source is part of the plugin core (Task 26).
cmake_minimum_required(VERSION 3.21)
project(skyrim_pyglue_linux LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Pass -DPython_EXECUTABLE="$(uv python find 3.14)" so the module matches the interpreter the tests run on.
find_package(Python 3.14 REQUIRED COMPONENTS Interpreter Development.Module)

set(SPY_CORE_TESTS OFF CACHE BOOL "core unit tests are built from core/ itself" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../core ${CMAKE_CURRENT_BINARY_DIR}/core)

Python_add_library(_skyrim MODULE WITH_SOABI ../src/pymodule.cpp fake_module.cpp)
target_include_directories(_skyrim PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../include
  ${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/pybind11/include)
target_link_libraries(_skyrim PRIVATE spy_core)
set_target_properties(_skyrim PROPERTIES CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
```

- [ ] **Step 5: Build the module**

Run: `cmake -S pyglue/linux -B pyglue/build -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE="$(uv python find 3.14)" | grep "Found Python" && cmake --build pyglue/build -j 2>&1 | tail -1 && ls pyglue/build/*.so`
Expected: `-- Found Python: .../cpython-3.14.../bin/python3.14 (found suitable version "3.14.x", minimum required is "3.14") found components: Interpreter Development.Module`, then `[100%] Built target _skyrim` and `pyglue/build/_skyrim.cpython-314-x86_64-linux-gnu.so`. (The configure also builds `spy_core` from `core/`, with its unit tests switched off; they run from `core/build` as before.)

- [ ] **Step 6: Run to verify it passes**

Run: `uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q 2>&1 | tail -1`
Expected: PASS, `21 passed in 1.3s` (about; the sleeps in the tests add up to a little over a second). Run it three times: the result must not change.

Run: `cmake -S pyglue/linux -B pyglue/build/warn -DCMAKE_BUILD_TYPE=Debug -DPython_EXECUTABLE="$(uv python find 3.14)" -DCMAKE_CXX_FLAGS="-Wall -Wextra" >/dev/null && cmake --build pyglue/build/warn -j --clean-first 2>&1 | grep -c "warning:"`
Expected: `0`. (`pyglue/build/warn/` sits inside the ignored build directory, like `core/build/asan`.)

- [ ] **Step 7: Commit**

```bash
git status --short pyglue        # only the seven files below; no build directories
git add pyglue/include/spy/pymodule.h pyglue/src/pymodule.cpp pyglue/linux/CMakeLists.txt pyglue/linux/fake_module.cpp \
        pyglue/tests/conftest.py pyglue/tests/test_module.py pyglue/tests/test_threads.py
git commit -m "feat(pyglue): _skyrim module over the dispatcher, Linux build with the fake backend"
```
