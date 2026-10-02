# Task 43: Console echo of script failures and `scripts/<name>.toml` settings

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Contracts** (`_skython.console_echo`, `Game.settings`, `_host.py` (Tasks 36, 43), `spy::console::Echo`, config key `console_echo_errors`) and Part E's brief for Task 43 first. R38 items 2 and 3 (`.superpowers/sdd/README/skyrimplatform-comparison.md`, Top 8 and section 10 "Per-plugin settings"). Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (core, pyglue and Python with tests), then the Windows build box and the game on the box ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)).
>
> **STOP and ask the owner (do not improvise) if:** the console line does not appear in the plugin log within 60 s of the broken script's failure, or printing it crashes or hangs the game; `ConsoleLog::Print` differs at the pin; the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s.
>
> **Box rules:** `s4test`, never saved. End games with `tools/box/box_stop.sh`.

**Where the rate limiter lives.** The README places `ConsoleEcho.{h,cpp}` in `plugin/`, but the Linux tests must prove "20 failures in one second give at most 5 lines plus one summary", so the limiter is portable core code (`spy::ConsoleEcho`, with a doctest) that both builds use with their own output: the game build prints through `RE::ConsoleLog` inside one `AddTask`; the Linux fake build appends `console: <line>` to `plugin.log`. The summary line is emitted by the limiter's own worker thread (a plain C++ thread like the dispatcher's reaper: it never touches Python or the game) when the window reopens, so it appears even if no further failure happens.

**Files:**
- Create: `core/include/spy/console_echo.h`, `core/src/console_echo.cpp`, `core/tests/test_console_echo.cpp`
- Modify: `pyglue/include/spy/pymodule.h` (`ModuleHost::console_echo`), `pyglue/src/pymodule.cpp` (`_skython.console_echo`), `pyglue/linux/fake_module.cpp` (fake output)
- Create: `plugin/src/ConsoleEcho.h`, `plugin/src/ConsoleEcho.cpp`; Modify: `plugin/src/PythonHost.cpp`, `plugin/CMakeLists.txt`
- Modify: `python/src/skython/game.py` (`Game.settings`), `python/src/skython/_host.py` (settings files, echo), `python/tests/fake_skython.py` (append `console_echo`)
- Create: `python/tests/test_settings.py`
- Create: `livetest/scripts/livetest_settings/__init__.py`, `livetest/scripts/livetest_settings/settings.toml`, `livetest/test_m2_console_settings.py`
- Modify: `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md`, `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 33 `Config::console_echo_errors` (in `_skython.config()` as `console_echo_errors`); Task 35's `ModuleHost` (with its `events` field) and `fake_module.cpp`; Task 36's `Game` (this task adds one attribute and one property, nothing else) and `_host`'s script-`Game` handling; v1 `_host._load_one` (`report()`, `holder`, `_STATE["config"]`, `_STATE["scripts_dir"]`), `ops::InTask`, `ops::Measured`, `livetest/smoke.py` `_read` (box-pulled reads of files next to `python.log`), `livetest/scripts/livetest_broken` (fails on purpose), `support.log_text`, `tools/box/box_console.sh --tool menu`, `tools/box/box_shot.sh`.
- Produces: `spy::ConsoleEcho` (`core/include/spy/console_echo.h`), `ModuleHost::console_echo` (`std::function<void(const std::string&)>`), `_skython.console_echo(message: str) -> None`, `spy::console::Echo(std::string) -> void` (`plugin/src/ConsoleEcho.h`), `Game.settings: Mapping[str, Any]`, the `_host` settings rules (`scripts/<name>.toml`, then `scripts/<name>/settings.toml`; TOML error → `failed: settings: <error>`, setup skipped), log line `console: printed <line>` (game build).

- [ ] **Step 1: Write the failing core test**

`core/tests/test_console_echo.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/console_echo.h"
#include <atomic>
#include <chrono>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

namespace {
struct Lines
{
    std::mutex mx;
    std::vector<std::string> v;
    void add(const std::string& s) { std::lock_guard lk(mx); v.push_back(s); }
    std::vector<std::string> get() { std::lock_guard lk(mx); return v; }
};
}

TEST_CASE("console echo: a burst of 5, then one summary when the window reopens")
{
    Lines out;
    spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 5, 100ms });
    for (int i = 0; i < 20; ++i) echo.Echo("err " + std::to_string(i));
    std::this_thread::sleep_for(50ms);
    CHECK(out.get().size() == 5);
    std::this_thread::sleep_for(250ms);
    const auto lines = out.get();
    REQUIRE(lines.size() == 6);
    CHECK(lines[0] == "err 0");
    CHECK(lines[4] == "err 4");
    CHECK(lines[5] == "Skython: 15 more script errors; see python.log");
    CHECK(echo.Printed() == 6);
    CHECK(echo.Suppressed() == 15);
}

TEST_CASE("console echo: steady errors below the rate all print")
{
    Lines out;
    spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 1, 20ms });
    for (int i = 0; i < 5; ++i) { echo.Echo("e"); std::this_thread::sleep_for(40ms); }
    std::this_thread::sleep_for(50ms);
    CHECK(out.get().size() == 5);
    CHECK(echo.Suppressed() == 0);
}

TEST_CASE("console echo: Echo never waits for a slow sink and never throws")
{
    std::atomic<int> calls{ 0 };
    spy::ConsoleEcho echo([&](const std::string&) { ++calls; std::this_thread::sleep_for(200ms); throw std::runtime_error("sink"); }, { 5, 1000ms });
    const auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 1000; ++i) echo.Echo("x");
    CHECK(std::chrono::steady_clock::now() - t0 < 100ms);
    std::this_thread::sleep_for(300ms);
    CHECK(calls.load() >= 1);  // the throwing sink did not stop the worker
}

TEST_CASE("console echo: long lines are cut, the destructor stops the worker")
{
    Lines out;
    {
        spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 5, 100ms, 16 });
        echo.Echo(std::string(100, 'a'));
        std::this_thread::sleep_for(50ms);
    }
    REQUIRE(out.get().size() == 1);
    CHECK(out.get()[0] == std::string(16, 'a'));
}
```
Run: `cmake -S core -B core/build && cmake --build core/build -j && ./core/build/spy_core_tests -tc="console echo*"`
Expected: build FAIL (`spy/console_echo.h` not found).

- [ ] **Step 2: `spy::ConsoleEcho`**

`core/include/spy/console_echo.h`:
```cpp
#pragma once
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace spy {
// Rate-limited one-line messages (R38 item 2). A token bucket: `burst` lines at once, then one per `refill`. Lines over
// the rate are counted, and when a token is free again one summary line "Skython: N more script errors; see
// python.log" is emitted. Echo never blocks beyond one short mutex section and never throws; the sink runs on this
// object's own worker thread (never the caller's), so a slow or throwing sink cannot hold anyone up.
class ConsoleEcho
{
public:
    using Sink = std::function<void(const std::string&)>;
    struct Options
    {
        int burst = 5;
        std::chrono::milliseconds refill{ 1000 };
        std::size_t max_line = 500;  // longer lines are cut (the console shows one line)
    };
    explicit ConsoleEcho(Sink sink, Options opts = {});
    ~ConsoleEcho();
    ConsoleEcho(const ConsoleEcho&) = delete;
    ConsoleEcho& operator=(const ConsoleEcho&) = delete;

    void Echo(std::string message) noexcept;
    std::uint64_t Printed() const;
    std::uint64_t Suppressed() const;

private:
    using Clock = std::chrono::steady_clock;
    void Refill(Clock::time_point now);  // with mx_ held
    void Worker();

    Sink sink_;
    Options opts_;
    mutable std::mutex mx_;
    std::condition_variable cv_;
    std::deque<std::string> queue_;
    double tokens_;
    Clock::time_point last_;
    std::uint64_t pending_ = 0, printed_ = 0, suppressed_ = 0;
    bool stop_ = false;
    std::thread worker_;
};
}
```
`core/src/console_echo.cpp`:
```cpp
#include "spy/console_echo.h"
#include <algorithm>

namespace spy {
ConsoleEcho::ConsoleEcho(Sink sink, Options opts)
    : sink_(std::move(sink)), opts_(opts), tokens_(opts.burst), last_(Clock::now()), worker_([this] { Worker(); }) {}

ConsoleEcho::~ConsoleEcho()
{
    {
        std::lock_guard lk(mx_);
        stop_ = true;
    }
    cv_.notify_all();
    if (worker_.joinable()) worker_.join();
}

void ConsoleEcho::Refill(Clock::time_point now)
{
    const double add = std::chrono::duration<double>(now - last_) / std::chrono::duration<double>(opts_.refill);
    tokens_ = std::min<double>(opts_.burst, tokens_ + add);
    last_ = now;
}

void ConsoleEcho::Echo(std::string message) noexcept
{
    try {
        if (message.size() > opts_.max_line) message.resize(opts_.max_line);
        {
            std::lock_guard lk(mx_);
            Refill(Clock::now());
            if (tokens_ >= 1.0) {
                tokens_ -= 1.0;
                queue_.push_back(std::move(message));
            } else {
                ++pending_;
                ++suppressed_;
            }
        }
        cv_.notify_one();
    } catch (...) {}  // allocation failure: the line is lost, the caller is not disturbed
}

void ConsoleEcho::Worker()
{
    std::unique_lock lk(mx_);
    for (;;) {
        if (stop_) return;
        if (queue_.empty() && pending_ > 0) {
            Refill(Clock::now());
            if (tokens_ >= 1.0) {
                tokens_ -= 1.0;
                queue_.push_back("Skython: " + std::to_string(pending_) + " more script errors; see python.log");
                pending_ = 0;
            }
        }
        if (!queue_.empty()) {
            std::string line = std::move(queue_.front());
            queue_.pop_front();
            ++printed_;
            lk.unlock();
            try { sink_(line); } catch (...) {}
            lk.lock();
            continue;
        }
        if (pending_ > 0) cv_.wait_for(lk, opts_.refill / 4);  // wake when a token may be free for the summary
        else cv_.wait(lk);
    }
}

std::uint64_t ConsoleEcho::Printed() const { std::lock_guard lk(mx_); return printed_; }
std::uint64_t ConsoleEcho::Suppressed() const { std::lock_guard lk(mx_); return suppressed_; }
}
```
`core/CMakeLists.txt` globs `src/*.cpp` and `tests/*.cpp`, so nothing to add.

Run: `cmake --build core/build -j && ./core/build/spy_core_tests -tc="console echo*"`
Expected: `[doctest] test cases: 4 | 4 passed`. Then the whole suite: `./core/build/spy_core_tests` → all pass.

- [ ] **Step 3: `_skython.console_echo` in both builds**

`pyglue/include/spy/pymodule.h`: add `#include <functional>` (present) and, as the **last** member of `ModuleHost` (after Task 35's `events`):
```cpp
    std::function<void(const std::string& message)> console_echo;  // _skython.console_echo(); unset = no-op (Task 43)
```
`pyglue/src/pymodule.cpp`, in `DefineModule` after the `log` definition:
```cpp
    m.def("console_echo", [](const std::string& message) { if (Host().console_echo) Host().console_echo(message); },
          py::arg("message"), release(),
          "Queue one rate-limited line for the in-game console (at most 5 at once, then 1 per second; extra lines are "
          "summarised). Never blocks, never raises for rate limiting.");
```
`pyglue/linux/fake_module.cpp`: add `#include "spy/console_echo.h"`. The module init must set `ModuleHost` fields **by name**, never positionally. If the `InstallModuleHost({...})` call is still positional, replace it with named assignments that keep every value it passes (including Task 35's `events`), then add the echo:
```cpp
    static spy::ConsoleEcho* echo = new spy::ConsoleEcho([](const std::string& line) { Log("info", "console: " + line); });  // leaked like the fake
    spy::ModuleHost host;
    host.dispatcher = dispatcher;
    host.backend = "fake";
    host.config_json = ConfigJson;
    host.log = Log;
    host.fake = fake;
    // host.events = ... exactly as Task 35 set it (keep that line unchanged)
    host.console_echo = [](const std::string& m) { echo->Echo(m); };
    spy::InstallModuleHost(std::move(host));
```
Append to `python/tests/fake_skython.py`'s `FakeSkython` (methods, not a new class; keep everything else):
```python
    def console_echo(self, message: str) -> None:
        """Like _skython.console_echo; the pure-Python fake records lines (rate limiting is tested on the C++ side)."""
        with self._cond:
            self.console_lines.append(message)
```
and in `__init__`, after `self.logs = []`: `self.console_lines: list[str] = []`.

- [ ] **Step 4: Write the failing Python tests**

`python/tests/test_settings.py`:
```python
"""Task 43: scripts/<name>.toml as a read-only game.settings, and console echo of script failures (fake _skython)."""
import sys
import textwrap

import pytest

from skython.inprocess import connect_in_process
from test_host import host, script  # noqa: F401  (the host fixture and the script writer from Task 24's tests)

CAPTURE = """
SEEN = {}

def setup(game):
    SEEN["settings"] = game.settings
"""


def settings_file(tmp_path, name, text, package=False):
    folder = tmp_path / "scripts"
    path = folder / name / "settings.toml" if package else folder / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


def run_host(host, fake):
    host.main()
    return host.status()["scripts"]


def test_settings_are_read_and_frozen(host, fake, tmp_path):
    script(tmp_path, "t24_s43_a", CAPTURE)
    settings_file(tmp_path, "t24_s43_a", 'greeting = "hi"\nlimits = [1, 2]\n[nested]\nlevel = 3\n')
    assert run_host(host, fake)["t24_s43_a"] == "ok"
    s = sys.modules["t24_s43_a"].SEEN["settings"]
    assert s["greeting"] == "hi" and s["limits"] == (1, 2) and s["nested"]["level"] == 3
    with pytest.raises(TypeError):
        s["greeting"] = "changed"
    with pytest.raises(TypeError):
        s["nested"]["level"] = 4
    with pytest.raises(AttributeError):
        s["limits"].append(3)


def test_settings_attribute_is_read_only(fake):
    with connect_in_process() as g:
        assert dict(g.settings) == {}           # not a player script's Game: empty
        with pytest.raises(AttributeError):
            g.settings = {"x": 1}


def test_a_script_without_a_file_gets_an_empty_mapping(host, fake, tmp_path):
    script(tmp_path, "t24_s43_b", CAPTURE)
    assert run_host(host, fake)["t24_s43_b"] == "ok"
    assert dict(sys.modules["t24_s43_b"].SEEN["settings"]) == {}


def test_package_settings_and_module_file_wins(host, fake, tmp_path):
    script(tmp_path, "t24_s43_c", CAPTURE, package=True)
    settings_file(tmp_path, "t24_s43_c", "source = 'package'\n", package=True)
    script(tmp_path, "t24_s43_d", CAPTURE, package=True)
    settings_file(tmp_path, "t24_s43_d", "source = 'package'\n", package=True)
    settings_file(tmp_path, "t24_s43_d", "source = 'module file'\n")
    run_host(host, fake)
    assert sys.modules["t24_s43_c"].SEEN["settings"]["source"] == "package"
    assert sys.modules["t24_s43_d"].SEEN["settings"]["source"] == "module file"


def test_a_toml_error_fails_only_that_script_and_skips_setup(host, fake, tmp_path):
    script(tmp_path, "t24_s43_e", CAPTURE)
    settings_file(tmp_path, "t24_s43_e", "this is = = not toml\n")
    script(tmp_path, "t24_s43_f", CAPTURE)
    status = run_host(host, fake)
    assert status["t24_s43_e"].startswith("failed: settings: t24_s43_e.toml:"), status
    assert sys.modules["t24_s43_e"].SEEN == {}  # setup never ran
    assert status["t24_s43_f"] == "ok"


def test_reload_rereads_the_file(host, fake, tmp_path):
    script(tmp_path, "t24_s43_g", CAPTURE)
    settings_file(tmp_path, "t24_s43_g", "v = 1\n")
    run_host(host, fake)
    settings_file(tmp_path, "t24_s43_g", "v = 2\n")
    assert host.reload("t24_s43_g") == "ok"
    assert sys.modules["t24_s43_g"].SEEN["settings"]["v"] == 2


def test_a_failing_script_echoes_one_console_line(host, fake, tmp_path):
    script(tmp_path, "t24_s43_h", "def setup(game):\n    raise ValueError('boom')\n")
    run_host(host, fake)
    assert fake.console_lines == ["Skython: script t24_s43_h failed: ValueError: boom; see python.log"]


def test_console_echo_errors_false_echoes_nothing(host, fake, tmp_path):
    fake.config_overrides["console_echo_errors"] = False
    script(tmp_path, "t24_s43_i", "def setup(game):\n    raise ValueError('boom')\n")
    run_host(host, fake)
    assert fake.console_lines == []
```
Append to `python/tests/test_real_module.py` (it runs only with `SKYTHON_REAL_MODULE_DIR`, against the C++ `ConsoleEcho`):
```python
def test_console_echo_rate_limit_in_the_real_module(real):
    for i in range(20):
        real.console_echo(f"Skython: script t43_{i} failed: X; see python.log")
    time.sleep(1.6)  # one refill window plus slack: the summary comes from the limiter's own thread
    text = pathlib.Path(json.loads(real.config())["log_dir"], "plugin.log").read_text()
    lines = [l for l in text.splitlines() if " console: " in l]
    assert len(lines) == 6, lines
    assert lines[-1].endswith("console: Skython: 15 more script errors; see python.log")
```
Add `import pathlib` to that file's imports if absent. Run:
`uv run --project python pytest python/tests/test_settings.py -q`
Expected: FAIL (`AttributeError: 'Game' object has no attribute 'settings'` and the echo assertions).

- [ ] **Step 5: `Game.settings` and the `_host` changes**

`python/src/skython/game.py`: add `import types` and `from collections.abc import Mapping` (if absent); module level `_EMPTY_SETTINGS: Mapping[str, Any] = types.MappingProxyType({})`; in `Game.__init__`, last line: `self._settings: Mapping[str, Any] = _EMPTY_SETTINGS`; and the property, next to `closed`:
```python
    @property
    def settings(self) -> Mapping[str, Any]:
        """This player script's settings file (scripts/<name>.toml or scripts/<name>/settings.toml), read-only:
        tables are MappingProxyType, arrays tuples. Empty for a Game that is not a player script's (Task 43)."""
        return self._settings
```
`python/src/skython/_host.py`: add `import tomllib` and `import types`; below `_script_names` add:
```python
class _SettingsError(Exception):
    pass


def _freeze(v):
    if isinstance(v, dict):
        return types.MappingProxyType({k: _freeze(x) for k, x in v.items()})
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    return v


def _read_settings(name: str):
    """scripts/<name>.toml, else scripts/<name>/settings.toml (the first that exists wins); frozen; {} if neither."""
    folder = pathlib.Path(_STATE["scripts_dir"])
    for path in (folder / f"{name}.toml", folder / name / "settings.toml"):
        if path.is_file():
            try:
                return _freeze(tomllib.loads(path.read_text(encoding="utf-8")))
            except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
                raise _SettingsError(f"{path.name}: {e}") from e
    return _freeze({})


def _echo_failure(name: str, status: str) -> None:
    """One rate-limited console line per failed script (console_echo_errors, default true); never raises."""
    try:
        if not (_STATE.get("config") or {}).get("console_echo_errors", True):
            return
        import _skython
        _skython.console_echo(f"Skython: script {name} {status.splitlines()[0][:200]}; see python.log")
    except Exception:  # noqa: BLE001 - the console is a convenience; python.log has the full story
        log.debug("console echo failed", exc_info=True)
```
In `_load_one.run()`, replace the line `                        setup(game)` with:
```python
                        try:
                            game._settings = _read_settings(name)
                        except _SettingsError as e:
                            holder["error"] = f"settings: {e}"
                            holder["traceback"] = ""
                        else:
                            setup(game)
```
In `report()`, in the `elif status.startswith("failed"):` branch (after its `log.error`) and in the `failed: no setup` branch, add `_echo_failure(name, status)`. In tests `_STATE["config"]` is the fake's config only after `main()`; `_echo_failure` treats a missing config as "echo on" (the default). Run:
`uv run --project python pytest python/tests/test_settings.py python/tests/test_host.py -q` → all pass; then the whole suite on both versions: `uv run --project python --python 3.12 pytest python/tests -q` and `uv run --project python --python 3.14 pytest python/tests -q` → all pass. Rebuild the Linux module and run the real-module test: `cmake --build pyglue/build -j && SKYTHON_REAL_MODULE_DIR=pyglue/build uv run --project python pytest python/tests/test_real_module.py -q -k console_echo` → `1 passed`.

- [ ] **Step 6: The game build**

Verify at the pin: `grep -n "static ConsoleLog\* GetSingleton();\|void        Print(const char\* a_fmt, ...);\|void Print(const char\* a_fmt" plugin/extern/CommonLibSSE-NG/include/RE/C/ConsoleLog.h` (expected `static ConsoleLog* GetSingleton();` and `void Print(const char* a_fmt, ...);`) and append the row to `API-VERIFICATION.md` (task 43).

`plugin/src/ConsoleEcho.h`:
```cpp
#pragma once
#include <string>

namespace spy::console {
// Contracts: at most one line per second with a burst of 5; extra lines are summarised. Each line is printed inside one
// AddTask. Never blocks, never throws.
void Echo(std::string message);
}
```
`plugin/src/ConsoleEcho.cpp`:
```cpp
#include "ConsoleEcho.h"
#include "Log.h"
#include "Ops.h"
#include "spy/console_echo.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>

namespace spy::console {
namespace {
void PrintInTask(const std::string& line)
{
    SKSE::GetTaskInterface()->AddTask([line] {
        ops::InTask in;
        ops::Measured measured;
        try {
            if (auto* c = RE::ConsoleLog::GetSingleton()) {
                c->Print("%s", line.c_str());  // never the line as the format string
                log::Info("console: printed " + line);
            } else {
                log::Warn("console: no console yet; dropped: " + line);
            }
        } catch (...) {
            log::Error("console: printing threw");
        }
    });
}

spy::ConsoleEcho& Limiter()
{
    static auto* echo = new spy::ConsoleEcho(PrintInTask);  // immortal: its worker may run during process exit
    return *echo;
}
}

void Echo(std::string message) { Limiter().Echo(std::move(message)); }
}
```
`plugin/src/PythonHost.cpp`: add `#include "ConsoleEcho.h"`; after the `host.log = ...;` assignment add `host.console_echo = [](const std::string& m) { console::Echo(m); };`. `plugin/CMakeLists.txt`: add `src/ConsoleEcho.cpp` to `SPY_CORE_SOURCES` (`spy/console_echo.h` comes with `spy_core`).

- [ ] **Step 7: The live check**

`livetest/scripts/livetest_settings/settings.toml`:
```toml
# Read by skython._host before setup(game) (Task 43). Plain values only: nothing secret ever goes here.
greeting = "hello from settings.toml"
limits = [1, 2, 3]

[nested]
level = 3
```
`livetest/scripts/livetest_settings/__init__.py`:
```python
"""Writes its game.settings (made JSON-able) and a mutation check next to python.log, for test_m2_console_settings."""
import json
import os
import pathlib

import _skython


def _plain(v):
    if hasattr(v, "items"):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    return v


def setup(game) -> None:
    try:
        game.settings["greeting"] = "changed"
        frozen = False
    except TypeError:
        frozen = True
    out = pathlib.Path(json.loads(_skython.config())["log_dir"]) / "livetest_settings.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"settings": _plain(game.settings), "frozen": frozen}))
    os.replace(tmp, out)
```
`livetest/test_m2_console_settings.py`:
```python
"""Task 43 live: the broken script's failure reaches the in-game console once; a script reads its settings.toml."""
import subprocess
import time

from smoke import _read
from support import REPO, log_text

LINE = "Skython: script livetest_broken failed: RuntimeError: livetest_broken setup raises on purpose; see python.log"


def test_the_broken_script_is_echoed_once_and_the_game_runs_on(game):
    deadline = time.monotonic() + 60
    while (n := log_text().count(f"console: printed {LINE}")) == 0 and time.monotonic() < deadline:
        time.sleep(2)
    assert n == 1, f"expected exactly one console line, found {n}"
    assert game.state()["data_ready"] is True


def test_console_screenshot_for_the_record():
    # Evidence for RESULTS: open the console through DevBench, take a screenshot, close it again.
    menu = [str(REPO / "tools" / "box" / "box_console.sh"), "--tool", "menu"]
    subprocess.run(menu + ['{"action":"open","name":"Console"}'], check=True, capture_output=True, text=True, timeout=90)
    try:
        r = subprocess.run([str(REPO / "tools" / "box" / "box_shot.sh")], check=True, capture_output=True, text=True, timeout=120)
        print(r.stdout)
    finally:
        subprocess.run(menu + ['{"action":"close","name":"Console"}'], check=True, capture_output=True, text=True, timeout=90)


def test_a_script_reads_its_settings_file():
    data = _read("livetest_settings.json")
    assert data == {"settings": {"greeting": "hello from settings.toml", "limits": [1, 2, 3], "nested": {"level": 3}},
                    "frozen": True}, data
```
Build and run:
```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll   # 0 warnings
source tools/box/box_env.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_wait_log.sh Skython.log "console: printed Skython: script livetest_broken" 120 || echo "STOP: no console line; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_console.sh --list | grep -i -E "console|history|log" || echo "no DevBench console-text tool: the screenshot is the evidence"
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test" && tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_console_settings.py -q -s -rs
tools/box/box_stop.sh
```
Expected: 3 passed; Read the screenshot (`$SKYTHON_STATE/box-shot.png`) and confirm the console shows the `Skython: script livetest_broken failed: ...` line (it was printed at the main menu; the console keeps its history). If the screenshot does not show it, the plugin-log line is the evidence: record which method worked.

- [ ] **Step 8: Record and commit**

Append to `spikes/RESULTS.md`: `**Task 43, console echo and script settings (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test, never saved; SKSE plugins: <list>):**` the `console: printed ...` line verbatim, the method that showed it in the game (screenshot or a DevBench tool), the settings JSON, and the Linux results (core 4/4, Python suite counts on 3.12 and 3.14, real-module rate test).
```bash
git add core/include/spy/console_echo.h core/src/console_echo.cpp core/tests/test_console_echo.cpp \
  pyglue/include/spy/pymodule.h pyglue/src/pymodule.cpp pyglue/linux/fake_module.cpp \
  plugin/src/ConsoleEcho.h plugin/src/ConsoleEcho.cpp plugin/src/PythonHost.cpp plugin/CMakeLists.txt \
  python/src/skython/game.py python/src/skython/_host.py python/tests/fake_skython.py python/tests/test_settings.py \
  python/tests/test_real_module.py livetest/scripts/livetest_settings livetest/test_m2_console_settings.py \
  docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md spikes/RESULTS.md
git commit -m "feat: rate-limited console echo of script failures and per-script settings files"
```
