# Task 38: Remote two-drain experiment: in-process sleep sweep and per-request timestamps (record only)

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Fixed paths** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.5 and 8). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 37 (the bridge with its event pump: this task adds timing to that version of `bridge.py`) and Task 33 (`Config::trace_timing`, generated `CONFIG_KEYS["trace_timing"]`). Linux first (Steps 1-7), then the Windows box and the game (Steps 8-10).
>
> **Runs on:** Linux for the code and its tests; the build box for the DLLs; the game on the box ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)) with the **existing** deploy list only (no Gate 0 needed): `s4test`, never saved.
>
> **STOP and ask the owner (do not improvise) if:** the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; the game crashes or freezes for more than 30 s during the sweep.

**The question (carry-forward from Task 31, `spikes/RESULTS.md`).** A sequential `Utility.RandomInt` through the bridge costs about two frame drains (p50 92.39 ms) while the same call made in the game costs one (46.11 ms); the ssh tunnel adds only about 2.6 ms. The reviewer's hypothesis is **frame quantization**: SKSE drains its task queue once per frame, so a request that reaches the plugin after this frame's drain has started waits for the next one; in-process the next call is submitted within microseconds of the previous result, remotely the round trip (completion poll, writer, tunnel, client, tunnel, reader) may be long enough to miss the drain every time. **Experiment:** per-request timestamps at submit, task start, VM callback and done (plugin) and at frame read and response write (bridge), all on one clock, while sequential calls are made with a pause of 0 to 50 ms before each, in-process and remotely. **Record only:** the stamps are off by default and change nothing else; no fix is made in this plan (index "After this plan").

**What is added.**
- `spy::NowUs()` (`core/include/spy/clock.h`): `std::chrono::steady_clock` microseconds, the one clock of every stamp; `_skython.now_us()` returns it to Python (index Contracts).
- `spy::CurrentRequestId()` (core `dispatcher.h`): the id of the request whose `Backend::Run` is running on this thread, 0 elsewhere. `Backend::Run` receives no request id, and the `timing:` line must name the id so it can be joined with the bridge's line. A `thread_local` set around the one `backend_.Run` call in `Dispatcher::Submit`.
- `GameBackend` with `trace_timing = true`: one `Timing` record per request (`id`, `submit` when `Run` is entered, `task_start` when the SKSE task body starts, `callback` when the VM calls back, stamped in `Ops.cpp`'s `Callback` through a `thread_local` the task body sets), and one log line per request, written right after `done` is called: `timing: id=<id> submit=<us> task_start=<us> callback=<us> done=<us>`. `callback=0` means no VM callback was made (an op without one, or an error before dispatch).
- The bridge with `trace_timing` (passed by `_host` from the config, clock `_skython.now_us`): `timing: id=<id> bridge_read=<us> bridge_write=<us>` per request, `bridge_read` once the request frame is fully read, `bridge_write` once its response frame is sent. Event frames and requests the bridge itself refuses get no line.
- `livetest_smoke` gains a `sweep` command (in-process, straight through `_skython.call` with request ids from a given base, stamped with `now_us`), and `livetest/test_m2_drain_timing.py` runs both sweeps and writes `livetest/results/drain_timing.json`.

**Files:**
- Create: `core/include/spy/clock.h`, `plugin/config/variants/trace-timing.ini`, `livetest/test_m2_drain_timing.py`, `livetest/results/drain_timing.json` (written by the run)
- Modify: `core/include/spy/dispatcher.h`, `core/src/dispatcher.cpp` (`CurrentRequestId`), `core/tests/test_dispatcher.cpp` (one test), `pyglue/src/pymodule.cpp` (`now_us`), `pyglue/tests/test_module.py` (one test), `plugin/src/Ops.h` (`Timing`, `t_timing`, `WithTiming`), `plugin/src/Ops.cpp` (`Callback` stamps `callback`), `plugin/src/GameBackend.cpp` (stamps and the `timing:` line), `python/src/skython/bridge.py` (`trace_timing`, `clock`), `python/src/skython/_host.py` (passes both), `python/tests/fake_skython.py` (`now_us`), `python/tests/test_bridge.py` and `python/tests/test_host.py` (one test each, plus one in `test_bridge.py` for the default), `livetest/scripts/livetest_smoke/__init__.py` (`sweep`), `spikes/RESULTS.md` (the Task 38 entry)

**Interfaces:**
- Consumes: Task 33 `spy::Config::trace_timing` (`settings::Get().trace_timing` in the plugin; `"trace_timing"` in `_skython.config()`), Task 37 `bridge.py` (`_Conn` with `tickets`, `out`, `_enqueue`, `_write_loop`, `_complete_loop`, `_pump_loop`; `serve(...)`), v1 `GameBackend.cpp` (`Run`'s single `AddTask`, `ops::InTask`, `ops::Measured`), `Ops.cpp` (`class Callback`), `Dispatcher::Submit`, `livetest/support.py` (`raw_connect`, `rpc`, `log_text`, `python_log_text`), `livetest/smoke.py` (`command`, `wait_smoke`), the `livetest_smoke` control loop (`_handle`), `tools/remote_build.sh`, `tools/box/*` (Task 1b/1c).
- Produces:
  - `spy::NowUs() noexcept -> std::int64_t` (`spy/clock.h`); `spy::CurrentRequestId() noexcept -> std::uint64_t` (`spy/dispatcher.h`).
  - `_skython.now_us() -> int` in both builds (index Contracts); the Python fake's `FakeSkython.now_us()` (`time.monotonic_ns() // 1000`).
  - `skython.bridge.serve(..., trace_timing: bool = False, clock: Callable[[], int] | None = None)`; without `clock` the bridge uses `time.monotonic_ns() // 1000`. `_host` passes `trace_timing=cfg["trace_timing"]`, `clock=_skython.now_us`.
  - Log lines (exact): plugin `timing: id=<id> submit=<us> task_start=<us> callback=<us> done=<us>`; bridge (logger `skython.bridge`, INFO, `python.log`) `timing: id=<id> bridge_read=<us> bridge_write=<us>`.
  - `livetest_smoke` command `sweep` (`sleeps_ms: list[int]`, `calls: int`, `id_base: int`) replying `{"calls": [[id, sleep_ms, start_us, end_us], ...]}`.
  - `livetest/results/drain_timing.json`: `date`, `game_version`, `runtime`, `calls_per_step`, `sleeps_ms`, `frame_ms_estimate`, `tie_back` (`remote_game_p50_ms`, `inprocess_game_p50_ms`, Task 31's `92.39` / `46.11`), `inprocess.steps` and `remote.steps` (one per pause: totals and phase spreads `{p10, p50, p90}` in ms), `raw.inprocess` rows `[id, sleep_ms, start, submit, task_start, callback, done, end]` and `raw.remote` rows `[id, sleep_ms, client_ms, bridge_read, submit, task_start, callback, done, bridge_write]` (µs stamps), `notes`.

- [ ] **Step 1: The core pieces, test first**

Append to `core/tests/test_dispatcher.cpp`:
```cpp
TEST_CASE("CurrentRequestId is the request's id inside Backend::Run and 0 outside it (Task 38)")
{
    struct IdBackend : spy::Backend
    {
        std::uint64_t seen = 99;
        void Run(const std::string&, const json&, spy::Done done, spy::Cancelled, spy::Progress) override
        {
            seen = spy::CurrentRequestId();
            done(OpResult::Ok(nullptr));
        }
        json BaseState() override { return json::object(); }
        std::optional<std::string> NotReadyReason(bool) override { return std::nullopt; }
        std::uint32_t LoadsStarted() override { return 0; }
    } backend;
    spy::Dispatcher d(backend, {});
    CHECK(spy::CurrentRequestId() == 0);
    const auto t = d.Submit(call_text(4242));
    CHECK(backend.seen == 4242);
    CHECK(spy::CurrentRequestId() == 0);  // reset when Run returns
    CHECK(code_of(wait_json(d, t)) == "ok");
    CHECK(spy::NowUs() > 0);
}
```
and add `#include "spy/clock.h"` to its includes.

Run: `cmake --build core/build -j 2>&1 | grep -m3 "error"`
Expected: `error: 'CurrentRequestId' is not a member of 'spy'` and `fatal error: spy/clock.h: No such file or directory`.

Create `core/include/spy/clock.h`:
```cpp
#pragma once
#include <chrono>
#include <cstdint>

namespace spy {
// Microseconds on std::chrono::steady_clock: the one clock of every trace_timing stamp (Task 38): GameBackend, the VM
// callback, and Python through _skython.now_us (the bridge), so stamps taken in any of them can be subtracted.
inline std::int64_t NowUs() noexcept
{
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
}
```
In `core/include/spy/dispatcher.h`, after `using Ticket = std::uint64_t;`:
```cpp
// The id of the request whose Backend::Run is running on this thread; 0 anywhere else (Task 38: GameBackend's
// trace_timing line names it, so it can be joined with the bridge's line). Backend::Run itself gets no id.
std::uint64_t CurrentRequestId() noexcept;
```
In `core/src/dispatcher.cpp`, at the end of the anonymous namespace inside `namespace spy` (the one that opens at the top of the file and holds the local helpers):
```cpp
thread_local std::uint64_t t_request_id = 0;
struct RequestIdScope  // set around the one Backend::Run call in Submit; reset even if Run throws
{
    explicit RequestIdScope(std::uint64_t id) { t_request_id = id; }
    ~RequestIdScope() { t_request_id = 0; }
    RequestIdScope(const RequestIdScope&) = delete;
    RequestIdScope& operator=(const RequestIdScope&) = delete;
};
```
After that anonymous namespace closes (still inside `namespace spy`), before `Ticket Dispatcher::Submit`:
```cpp
std::uint64_t CurrentRequestId() noexcept { return t_request_id; }
```
In `Submit`, replace the line `backend_.Run(req.op, req.args, done, is_cancelled, progress);` (inside its `try`) with:
```cpp
        RequestIdScope request_id(id);
        backend_.Run(req.op, req.args, done, is_cancelled, progress);
```
(Task 35's `events.*` routing returns before this point, so routed ops never set it.)

Run: `cmake --build core/build -j 2>&1 | tail -1 && core/build/spy_core_tests -tc="CurrentRequestId*" | tail -2 && core/build/spy_core_tests | tail -2`
Expected: `[doctest] test cases: 1 | 1 passed | 0 failed`, then the whole suite with Task 35's count plus 1, `0 failed`.

- [ ] **Step 2: `_skython.now_us`, test first**

Append to `pyglue/tests/test_module.py`:
```python
def test_now_us_is_steady_clock_microseconds():
    a = _skython.now_us()
    time.sleep(0.05)
    b = _skython.now_us()
    assert type(a) is int and 45_000 <= b - a < 1_000_000
    # On Linux std::chrono::steady_clock and time.monotonic are both CLOCK_MONOTONIC: the same clock.
    assert abs(_skython.now_us() - time.monotonic_ns() // 1000) < 10_000
```
Run: `cmake --build pyglue/build -j 2>&1 | tail -1 && uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q -k now_us 2>&1 | tail -1`
Expected: `1 failed` (`AttributeError: module '_skython' has no attribute 'now_us'`).

In `pyglue/src/pymodule.cpp`, add `#include "spy/clock.h"` after `#include "spy/pymodule.h"`, and after the `in_flight` definition:
```cpp
    m.def("now_us", [] { return NowUs(); },
          "steady_clock microseconds: the clock of the plugin's trace_timing stamps (Task 38).");
```
Run the same command. Expected: `[100%] Built target _skython`, then `1 passed`; then the whole file: `uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q 2>&1 | tail -1` → Task 35's count plus 1, `passed`.

- [ ] **Step 3: The bridge's timing lines, test first**

Append to `python/tests/test_bridge.py` (add `import itertools` and `import re` to its imports):
```python
TIMING = re.compile(r"timing: id=(\d+) bridge_read=(\d+) bridge_write=(\d+)")


def test_trace_timing_logs_one_line_per_request_and_none_for_events(start, fake, caplog):
    caplog.set_level(logging.INFO, logger="skython.bridge")
    ticks = itertools.count(1000, 7)  # a fake clock: every read is later than the one before
    b = start(trace_timing=True, clock=lambda: next(ticks))
    seen = []
    with remote(b) as game:
        for v in range(3):
            assert game.call("Fake", "Echo", v) == v
        game.on_channel("t", seen.append)
        fake._fake_push_event("channel", json.dumps({"channel": "t", "payload": "", "truncated": False}))
        wait_until(lambda: seen, what="the event")
    lines = [m for m in (TIMING.fullmatch(r.getMessage()) for r in caplog.records) if m]
    assert sorted(int(m[1]) for m in lines) == [1, 2, 3, 4]  # three calls and the subscribe; the event frame has none
    assert all(int(m[2]) < int(m[3]) for m in lines)


def test_no_timing_lines_by_default(bridge, caplog):
    caplog.set_level(logging.INFO, logger="skython.bridge")
    with remote(bridge) as game:
        assert game.call("Fake", "Echo", 1) == 1
    assert "timing:" not in caplog.text
```
Append to `python/tests/test_host.py`:
```python
def test_trace_timing_reaches_the_bridge_with_the_modules_clock(host, fake, tmp_path):
    fake.config_overrides.update({"bridge_enabled": True, "port": 0, "trace_timing": True})
    host.main()
    with connect_remote(tmp_path / "session.json") as game:
        assert game.call("Fake", "Echo", 1) == 1
    wait_until(lambda: "timing: id=1 bridge_read=" in log_text(tmp_path), what="the bridge's timing line in python.log")
```
Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_bridge.py tests/test_host.py -k "timing" 2>&1 | tail -1`
Expected: `2 failed, 1 passed` (`TypeError: serve() got an unexpected keyword argument 'trace_timing'`; the host test times out waiting for the line; the default test already passes and guards the change).

Add to `python/tests/fake_skython.py`, after `in_flight`:
```python
    def now_us(self) -> int:
        """Like the real module's (Task 38): steady microseconds (CLOCK_MONOTONIC, as std::chrono::steady_clock)."""
        return time.monotonic_ns() // 1000
```
In `python/src/skython/bridge.py` (the Task 37 version):
- Docstring, after the "Four threads per connection" paragraph, add: `With trace_timing (dev only, Task 38) the writer logs "timing: id=<id> bridge_read=<us> bridge_write=<us>" per request on the given clock (in the game _skython.now_us, the plugin's steady clock): read when the request frame was complete, write when its response frame was sent.`
- `_Conn.__init__`: add `self.read_us: dict[int, int] = {}  # ticket -> bridge_read stamp (trace_timing only)`, and change the `out` comment/type to `collections.deque[tuple[bytes, int | None, int | None]]` (frame, request id, bridge_read stamp).
- `_read_loop`: replace the two lines of the request loop with:
```python
            while True:
                text = _read_text(self.sock, _g.MAX_FRAME_BYTES)
                read_us = self.server.clock() if self.server.clock is not None else None
                self._on_request(text, _parse_object(text), read_us)
```
- `_on_request(self, text: str, obj: dict, read_us: int | None = None)`: in the block that records the ticket (`if not self.closed: self.tickets[ticket] = rid`), add right after that assignment:
```python
                if read_us is not None and rid is not None:
                    self.read_us[ticket] = read_us
```
- `_complete_loop`: replace `rid = self.tickets.pop(ticket)` ... `self._enqueue(text)` with:
```python
                        rid = self.tickets.pop(ticket)
                        self.ids.discard(rid)
                        read_us = self.read_us.pop(ticket, None)
                    self._enqueue(text, rid, read_us)
```
- `_enqueue(self, text: str, rid: int | None = None, read_us: int | None = None)`: append `(data, rid, read_us)` instead of `data` (the byte accounting keeps `len(data)`).
- `_write_loop`: replace `data = self.out.popleft()` with `data, rid, read_us = self.out.popleft()`, and after `self.out_bytes -= len(data)` (outside the `with`):
```python
            if read_us is not None:
                log.info("timing: id=%d bridge_read=%d bridge_write=%d", rid, read_us, self.server.clock())
```
- `close()`: add `self.read_us.clear()` beside `self.ids.clear()`.
- `BridgeServer.__init__`: add the keyword parameters `trace_timing: bool = False, clock=None` and:
```python
        # The bridge's timing clock, or None when trace_timing is off (then no stamp is taken at all).
        self.clock = (clock or (lambda: time.monotonic_ns() // 1000)) if trace_timing else None
```
- `serve(...)`: add `trace_timing: bool = False, clock=None` to its signature, pass both to `BridgeServer`, and add to its docstring: `trace_timing logs one timing line per request (Task 38; clock: a function returning microseconds, default time.monotonic_ns() // 1000).`

In `python/src/skython/_host.py`, `_start_bridge`, extend the `serve(...)` call's arguments with:
```python
                       trace_timing=bool(cfg.get("trace_timing")), clock=getattr(module, "now_us", None),
```

- [ ] **Step 4: Run the Python suites**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected twice: Task 37's total plus 3 passed, Task 37's skips, no failures.

Run: `cd python && SKYTHON_REAL_MODULE_DIR="$(cd ../pyglue/build && pwd)" uv run --managed-python --python 3.14 pytest -q tests/test_real_module.py 2>&1 | tail -1`
Expected: all passed (the real module now has `now_us`).

- [ ] **Step 5: The plugin stamps**

In `plugin/src/Ops.h`, add `#include "spy/clock.h"` and `#include <memory>` to the includes, and after `struct Measured { ... };`:
```cpp
// Task 38 (trace_timing, dev only, record only): one request's stamps, spy::NowUs() microseconds. GameBackend makes one
// per request when trace_timing is on and sets t_timing while its task body runs; a Callback built in that body keeps
// it and stamps `callback` when the VM calls back (on whatever thread). Null when trace_timing is off: nothing is taken.
struct Timing
{
    std::uint64_t id = 0;
    std::int64_t submit = 0;
    std::atomic<std::int64_t> task_start{0};
    std::atomic<std::int64_t> callback{0};
};
inline thread_local std::shared_ptr<Timing> t_timing;
struct WithTiming
{
    explicit WithTiming(std::shared_ptr<Timing> t) : prev_(std::exchange(t_timing, std::move(t))) {}
    ~WithTiming() { t_timing = std::move(prev_); }
    WithTiming(const WithTiming&) = delete;
    WithTiming& operator=(const WithTiming&) = delete;

private:
    std::shared_ptr<Timing> prev_;
};
```
In `plugin/src/Ops.cpp`, `class Callback`: change the constructor to
```cpp
    Callback(Done d, DynArgs* args) : done_(std::move(d)), args_(args), timing_(t_timing) {}
```
make the first statement inside `operator()`'s `try` block
```cpp
            if (timing_) timing_->callback.store(NowUs());  // Task 38: before anything else the callback does
```
and add the member after `DynArgs* args_;`:
```cpp
    std::shared_ptr<Timing> timing_;  // Task 38: set only with trace_timing, from the task body that built us
```
In `plugin/src/GameBackend.cpp`, add `#include "spy/clock.h"` and `#include <memory>`, and replace `Run` with:
```cpp
    void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) override
    {
        if (op == "game.state") return State(std::move(done), std::move(cancelled));
        std::shared_ptr<ops::Timing> timing;  // Task 38: only with trace_timing (dev); null costs nothing
        if (settings::Get().trace_timing) {
            timing = std::make_shared<ops::Timing>();
            timing->id = spy::CurrentRequestId();
            timing->submit = NowUs();
            done = Traced(std::move(done), timing);
        }
        // Exactly one AddTask; never block the submitting Python thread.
        SKSE::GetTaskInterface()->AddTask([op, args, done, cancelled, progress, timing] {
            ops::InTask in;
            ops::Measured measured;  // Task 31 drain meter: before the cancelled() check, a cancelled body still ran
            if (timing) timing->task_start.store(NowUs());
            ops::WithTiming traced(timing);  // a Callback built below keeps it
            if (cancelled()) return;  // timed out, cancelled or a load started while queued
            try {
                ops::Run(op, args, done, cancelled, progress);
            } catch (const std::exception& e) {
                log::Error("game backend: " + op + " threw out of Run: " + e.what());
                done(Internal());
            } catch (...) {
                log::Error("game backend: " + op + " threw an unknown exception out of Run");
                done(Internal());
            }
        });
    }
```
and add to the `private:` section:
```cpp
    // Task 38: stamp `done`, hand the result on, then write the request's one line. The line is written after done so
    // the log write is not inside the measured path; Log.cpp flushes every info line (a synchronous write on this
    // thread, which may be inside an SKSE task: a small, recorded perturbation of the next request's queue time).
    static Done Traced(Done done, std::shared_ptr<ops::Timing> t)
    {
        return [done = std::move(done), t = std::move(t)](OpResult r) {
            const std::int64_t at = NowUs();
            done(std::move(r));
            log::Info("timing: id=" + std::to_string(t->id) + " submit=" + std::to_string(t->submit) +
                      " task_start=" + std::to_string(t->task_start.load()) + " callback=" +
                      std::to_string(t->callback.load()) + " done=" + std::to_string(at));
        };
    }
```
Create `plugin/config/variants/trace-timing.ini` (the dev ini plus one key; sections are ignored by the parser):
```ini
; Skython settings for Task 38's timing experiment only: the dev ini plus trace_timing = true (one timing line per
; request in Skython.log and python.log). Redeploy plugin/config/Skython.dev.ini afterwards.
[Python]
python_enabled = true
scripts_enabled = true
[Bridge]
bridge_enabled = true
port = 45977
fixed_token =
session_file =
max_inflight_per_connection = 256
[Calls]
default_timeout_ms = 5000
max_inflight_total = 1024
enable_console = false
trace_timing = true
console_echo_errors = true
```

Run: `tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll`
Expected: both exit 0 with 0 warnings (`SPY_TEST_HOOKS=OFF`), `out/Skython.dll` and `out/SkythonCore.dll` refreshed. The Linux core and pyglue builds of Steps 1-2 already compiled `clock.h` and `CurrentRequestId`.

- [ ] **Step 6: The in-process `sweep` command**

In `livetest/scripts/livetest_smoke/__init__.py`, add before `def _handle`:
```python
def _sweep(cmd: dict) -> dict:
    """Task 38: sequential Utility.RandomInt calls, `calls` per pause in `sleeps_ms`, the pause before each call.
    Straight through _skython.call with request ids from id_base, so the plugin's timing lines join with them, and
    stamped with _skython.now_us (the plugin's clock). No Game: its ids would collide with other clients'."""
    calls, base, rows, n = int(cmd.get("calls", 100)), int(cmd["id_base"]), [], 0
    for sleep_ms in cmd["sleeps_ms"]:
        for _ in range(calls):
            n += 1
            text = json.dumps({"type": "request", "id": base + n, "op": "papyrus.call_static",
                               "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}})
            if sleep_ms:
                time.sleep(sleep_ms / 1000)
            start = _skython.now_us()
            resp = json.loads(_skython.call(text, 30.0))
            end = _skython.now_us()
            if resp.get("ok") is not True:
                return {"error": f"sweep call {base + n} failed: {resp.get('error')}"}
            rows.append([base + n, sleep_ms, start, end])
    return {"calls": rows}
```
and in `_handle`, before the final `return {"error": ...}`:
```python
    if name == "sweep":
        return _sweep(cmd)
```

- [ ] **Step 7: The live test**

`livetest/test_m2_drain_timing.py`:
```python
"""Task 38 (record only): why a sequential call through the bridge costs about two frame drains (Task 31: p50
92.39 ms) while one made in the game costs one (46.11 ms). Hypothesis (frame quantization): a request that reaches
the plugin after this frame's SKSE task drain has started waits for the next frame's drain, and the remote round trip
(completion poll, writer, tunnel, client, tunnel, reader) is long enough to miss it every time.

Method: the dev ini plus trace_timing = true (plugin/config/variants/trace-timing.ini). For each pause in SLEEPS_MS,
CALLS sequential Utility.RandomInt calls with that pause before each one: in-process through _skython.call (the
livetest_smoke "sweep" command, request ids from IN_BASE) and through the bridge on a raw socket (ids from
REMOTE_BASE). The plugin logs "timing: id= submit= task_start= callback= done=" per request and the bridge
"timing: id= bridge_read= bridge_write=", all steady_clock microseconds (_skython.now_us). Output:
livetest/results/drain_timing.json and a RESULTS line. No pass threshold beyond complete data: it records."""
import datetime
import json
import os
import pathlib
import re
import statistics
import time

import pytest

from smoke import command, wait_smoke
from support import log_text, python_log_text, raw_connect, rpc

RESULTS = pathlib.Path(__file__).parent / "results" / "drain_timing.json"
SLEEPS_MS = (0, 2, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50)
CALLS = int(os.environ.get("SPY_DRAIN_CALLS", "100"))
IN_BASE = 3_800_000_000      # far above any other client's ids (every transport counts from 1; live_dashboard polls)
REMOTE_BASE = 3_900_000_000
PLUGIN = re.compile(r"timing: id=(\d+) submit=(\d+) task_start=(\d+) callback=(\d+) done=(\d+)")
BRIDGE = re.compile(r"timing: id=(\d+) bridge_read=(\d+) bridge_write=(\d+)")
RANDOM_INT = {"script": "Utility", "function": "RandomInt", "args": [1, 6]}
TASK31 = {"remote_p50_ms": 92.39, "inprocess_p50_ms": 46.11}


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def spread(values) -> dict:
    s = sorted(values)
    at = lambda p: round(s[min(len(s) - 1, int(len(s) * p))], 2)  # noqa: E731
    return {"p10": at(0.10), "p50": round(statistics.median(s), 2), "p90": at(0.90)}


def ms(a: int, b: int) -> float:
    return (b - a) / 1000


def remote_sweep(session) -> list:
    rows, n = [], 0
    s = raw_connect(session)
    try:
        for sleep_ms in SLEEPS_MS:
            for _ in range(CALLS):
                n += 1
                if sleep_ms:
                    time.sleep(sleep_ms / 1000)
                t0 = time.perf_counter()
                r = rpc(s, "papyrus.call_static", RANDOM_INT, req_id=REMOTE_BASE + n)
                client_ms = (time.perf_counter() - t0) * 1000
                assert r["ok"] is True, r
                rows.append((REMOTE_BASE + n, sleep_ms, client_ms))
    finally:
        s.close()
    return rows


def game_p50(game, n: int = 100) -> float:
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        game.call("Utility", "RandomInt", 1, 6)
        lat.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(lat), 2)


def test_two_drain_experiment(session, game):
    wait_smoke()  # the in-process script is up and serving commands
    inproc = command("sweep", timeout=600, sleeps_ms=list(SLEEPS_MS), calls=CALLS, id_base=IN_BASE)["calls"]
    remote = remote_sweep(session)
    tie_back = {"remote_game_p50_ms": game_p50(game), "inprocess_game_p50_ms": command("bench", calls=100)["p50_ms"],
                "task31": TASK31}

    plugin = {int(m[1]): tuple(int(x) for x in m.groups()[1:]) for m in PLUGIN.finditer(log_text("Skython.log"))}
    bridge = {int(m[1]): (int(m[2]), int(m[3])) for m in BRIDGE.finditer(python_log_text())}
    ids = [r[0] for r in inproc] + [r[0] for r in remote]
    missing = [i for i in ids if i not in plugin]
    assert not missing, (f"{len(missing)} of {len(ids)} calls have no plugin timing line: is "
                         "plugin/config/variants/trace-timing.ini deployed?")
    missing = [r[0] for r in remote if r[0] not in bridge]
    assert not missing, f"{len(missing)} remote calls have no bridge timing line (python.log rotated during the run?)"

    # in-process rows: id, sleep, start, submit, task_start, callback, done, end
    in_rows = [(i, sl, start, *plugin[i], end) for i, sl, start, end in inproc]
    # remote rows: id, sleep, client_ms, bridge_read, submit, task_start, callback, done, bridge_write
    re_rows = [(i, sl, round(c, 3), bridge[i][0], *plugin[i], bridge[i][1]) for i, sl, c in remote]
    assert all(r[5] > 0 for r in in_rows) and all(r[6] > 0 for r in re_rows), "a RandomInt call had no VM callback stamp"

    def in_step(rows):
        return {"total_ms": spread([ms(r[2], r[7]) for r in rows]),
                "phases_ms": {"python_to_submit": spread([ms(r[2], r[3]) for r in rows]),
                              "queued": spread([ms(r[3], r[4]) for r in rows]),
                              "vm": spread([ms(r[4], r[5]) for r in rows]),
                              "callback_to_done": spread([ms(r[5], r[6]) for r in rows]),
                              "done_to_python": spread([ms(r[6], r[7]) for r in rows])}}

    def re_step(rows):
        return {"client_ms": spread([r[2] for r in rows]),
                "server_ms": spread([ms(r[3], r[8]) for r in rows]),
                "phases_ms": {"read_to_submit": spread([ms(r[3], r[4]) for r in rows]),
                              "queued": spread([ms(r[4], r[5]) for r in rows]),
                              "vm": spread([ms(r[5], r[6]) for r in rows]),
                              "callback_to_done": spread([ms(r[6], r[7]) for r in rows]),
                              "done_to_write": spread([ms(r[7], r[8]) for r in rows]),
                              "tunnel_and_client": spread([r[2] - ms(r[3], r[8]) for r in rows])}}

    starts = [r[4] for r in in_rows if r[1] == 0]  # in-process, no pause: about one call per frame
    frame_ms = round(statistics.median(ms(a, b) for a, b in zip(starts, starts[1:])), 2)
    in_steps = [{"sleep_ms": s, **in_step([r for r in in_rows if r[1] == s])} for s in SLEEPS_MS]
    re_steps = [{"sleep_ms": s, **re_step([r for r in re_rows if r[1] == s])} for s in SLEEPS_MS]
    welcome = game.welcome
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": welcome.get("game_version", "unknown"), "runtime": welcome.get("runtime", "unknown"),
        "calls_per_step": CALLS, "sleeps_ms": list(SLEEPS_MS), "frame_ms_estimate": frame_ms, "tie_back": tie_back,
        "inprocess": {"steps": in_steps}, "remote": {"steps": re_steps},
        "raw": {"inprocess": in_rows, "remote": re_rows},
        "notes": ["record only: trace_timing stamps, no behaviour change",
                  "each plugin timing line is written (and flushed: Log.cpp flush_on(info)) right after done, on the "
                  "thread that called done; that write is a small perturbation of the next call's queue time",
                  "frame_ms_estimate: median gap between task_start of consecutive in-process calls with no pause"],
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1) + "\n")
    z_in, z_re = in_steps[0], re_steps[0]
    print("RESULTS: frame ~{} ms; in-process total p50 by pause {}; remote client p50 by pause {}; at pause 0: queued "
          "p50 {} ms in-process vs {} ms remote, vm p50 {} vs {}, remote read_to_submit {} / done_to_write {} / tunnel "
          "and client {}; Game-based tie-back remote {} ms, in-process {} ms (Task 31: 92.39 / 46.11)".format(
              frame_ms,
              " ".join(f"{s['sleep_ms']}:{s['total_ms']['p50']}" for s in in_steps),
              " ".join(f"{s['sleep_ms']}:{s['client_ms']['p50']}" for s in re_steps),
              z_in["phases_ms"]["queued"]["p50"], z_re["phases_ms"]["queued"]["p50"],
              z_in["phases_ms"]["vm"]["p50"], z_re["phases_ms"]["vm"]["p50"],
              z_re["phases_ms"]["read_to_submit"]["p50"], z_re["phases_ms"]["done_to_write"]["p50"],
              z_re["phases_ms"]["tunnel_and_client"]["p50"],
              tie_back["remote_game_p50_ms"], tie_back["inprocess_game_p50_ms"]))
```

Run: `SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_drain_timing.py -q -rs 2>&1 | tail -1`
Expected: `1 skipped` (the live conftest skips without `SKYTHON_LIVE=1`; the file imports and collects cleanly).

Commit the code before the box run:
```bash
git add core/include/spy/clock.h core/include/spy/dispatcher.h core/src/dispatcher.cpp core/tests/test_dispatcher.cpp \
        pyglue/src/pymodule.cpp pyglue/tests/test_module.py plugin/src/Ops.h plugin/src/Ops.cpp plugin/src/GameBackend.cpp \
        plugin/config/variants/trace-timing.ini python/src/skython/bridge.py python/src/skython/_host.py \
        python/tests/fake_skython.py python/tests/test_bridge.py python/tests/test_host.py \
        livetest/scripts/livetest_smoke/__init__.py livetest/test_m2_drain_timing.py
git commit -m "feat: trace_timing stamps in the game backend and the bridge, now_us, and the drain timing sweep"
```

- [ ] **Step 8: Run the experiment on the box**

The game runs on the Windows box; every wait is bounded and counts only this launch's log text.
```bash
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/trace-timing.ini   # loader, core and the timing ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_console.sh "load s4test"   # never saved
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m2_drain_timing.py -q -s -rs 2>&1 | tee out/t38-live.txt | tail -3
tools/box/box_files.sh pull Skython.log Skython/python.log SkythonLoader.log >/dev/null
mkdir -p out/t38 && cp "$SKSE_LOG_DIR/Skython.log" "$SKSE_LOG_DIR/SkythonLoader.log" "$SKSE_LOG_DIR/Skython/python.log" out/t38/ && mv out/t38-live.txt out/t38/live.txt
grep -c "timing: id=" out/t38/Skython.log out/t38/python.log
grep '\[error\]' out/t38/Skython.log | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini (trace_timing off)
```
Expected: the launch log names the runtime 1.6.1170 and the SKSE plugins loaded (record them); the test prints one `RESULTS:` line and ends `1 passed` in about 5 minutes (1200 in-process calls, 1200 remote calls, 200 tie-back calls). `grep -c` prints at least 2600 for `Skython.log` (every sweep and tie-back call; other clients add more) and at least 1300 for `python.log` (remote sweep plus the Game-based tie-back). The error scan prints `no unexpected errors` (only the deliberate `livetest_broken` may log at error level). `box_stop.sh` prints `GONE`. `livetest/results/drain_timing.json` is written. A missing-line assertion is a setup problem (wrong ini, rotated log), never a result: fix the setup and rerun.

- [ ] **Step 9: Record**

Append to `spikes/RESULTS.md`, after the last task entry, one bullet in the v1 form:
`- **Task 38, remote two-drain experiment (box, Windows-native, <date> <start>-<end>, Skyrim 1.6.1170.0, s4test, unpaused, never saved; 1 launch, SKSE plugins: <the list from this launch's log>; record only):**` followed by the test's `RESULTS:` line verbatim, then two sentences of reading taken from `drain_timing.json`: (1) which phase holds the extra ~frame remotely at pause 0 (`queued` vs `vm` vs `done_to_write` vs `tunnel_and_client`), and (2) whether in-process `total_ms.p50 + sleep_ms` steps by multiples of `frame_ms_estimate` across the pauses (the quantization signature) and at which pause, if any, the remote `client_ms.p50` drops to one frame. End with `No behaviour change; nothing fixed (index "After this plan"). Logs: out/t38/.` Every number comes from this run's output; if the data contradicts the hypothesis, say so plainly.

- [ ] **Step 10: Commit the record**

```bash
git add livetest/results/drain_timing.json spikes/RESULTS.md
git commit -m "docs(spike): record the remote two-drain timing experiment"
```
