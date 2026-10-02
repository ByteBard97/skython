# Task 31: Robustness, throughput and the in-process smoke script

Adapted from: branch `plan-out-of-process`, task 19 (`task-19-robustness-and-throughput-baseline.md`). The robustness tests and the throughput curve are kept and now run through the dev bridge. New here: an in-process smoke script that makes the same calls from inside the game (the shipping mode) and records which transport it used and how the host loaded every script (including Task 26's deliberately broken `livetest_broken`), and throughput with and without a CPU-spinning Python thread inside the game (GIL hand-off cost, Review Focus 6). Added by ruling R38 (`.superpowers/sdd/README/skyrimplatform-comparison.md`, Tops 2 and 6): a live test that saves and loads while a latent call is in flight (Task 27's VM callback returns false from `CanSave()`, as the base class already does at the pin), and a burst of `max_inflight_total` requests at once with the longest time our queued tasks held the main thread in one frame.

> Part of the [Skython v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.5, 4.10, 8. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 30 is done.
>
> **Runs on:** the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); tests run on Linux. Saves are loaded through DevBench. The two `SPY_MANUAL` tests stay manual: someone answers their prompts and opens a menu or loads a save through DevBench from a second shell (Task 1c).
>
> **STOP and ask the owner (do not improvise) if:** a live test crashes the game or freezes it for more than 30 s; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; the manual tests are needed and nobody can answer their prompts.

**Files:**
- Create: `livetest/scripts/livetest_smoke/__init__.py` (in-process smoke script and benchmark; deployed to the game's `scripts/` folder, never shipped to players)
- Unchanged: `livetest/scripts/livetest_broken/__init__.py` exists since Task 26 (its message is asserted by Task 26's `test_m1_bridge.py`); do not create or edit it here.
- Create: `livetest/smoke.py` (Linux side: reads the smoke results, sends commands through a control file; in box mode through `tools/box/box_files.sh`)
- Modify: `livetest/support.py` (append `diagnostics(session)`, used by both test files below; this task is the first that needs it)
- Create: `livetest/test_m1_robustness.py`, `livetest/test_m1_throughput.py`, `livetest/results/.gitkeep`
- Modify: `plugin/src/Ops.h` (Task 27's header: append the passive drain meter, Step 3b), `plugin/src/GameBackend.cpp` (wrap both queued task bodies, two more `diagnostics` keys), `plugin/src/Ops.cpp` (wrap Task 30's batch-step task body)

**Interfaces:**
- Consumes: the live plugin from Task 30; `skython` (Tasks 20-22: `Game`, `errors`, `values.Form`, `values.Fingerprint`); `_skython.config()["log_dir"]` and `_skython.log(level, msg)` (Task 19 contract); `skython._host` loads each package in `scripts/` and calls `setup(game)` on the host thread, `teardown()` on reload (Task 24); `tools/box/box_deploy.sh --scripts DIR` (Task 1b); `livetest/support.py` (`raw_connect`, `rpc`, `read_frame`, `send_request`, `fresh`, and the box mode `BOX`, `REPO`, `box_pull`) and fixtures `session`, `game` (Tasks 26-27); `out/box-live.env` (Task 1c) sets `SKYTHON_LOG` to the local mirror of the box's `python.log` (the smoke files live in the same folder on the box: index Fixed paths); `tools/box/box_files.sh pull`/`put-control` and `tools/box/box_saves.sh count` (Task 1b); `skython._host.status()["scripts"]` (Task 24); Task 27's `GameBackend.cpp` and its VM callback (`CanSave()` false); `tools/box/box_console.sh` and `box_quit.sh` (Task 1b: DevBench `save`/`load`, crash-log check).
- Produces:
  - In the game's Python log folder (`.../SKSE/Skython/`): `livetest_smoke.json` (`{status: "pass"|"fail", checks: [{name, ok, error?}], backend, transport, scripts, thread, switch_interval_s, time}`; `backend` is `_skython.BACKEND`, `transport` the class name of the script's `game.transport`, `scripts` the host's `status()["scripts"]` for this run), and the control pair `livetest_control.json` (`{seq, cmd, ...}`, written by Linux; `seq` is `time.time_ns()`, so it grows across launches) / `livetest_reply.json` (`{seq, result}`, written by the script). Commands: `spin_on`, `spin_off` (reply carries `spin_loops`), `bench` (`calls`; reply `{calls, seconds, per_s, p50_ms, p99_ms, spinning}`).
  - `livetest/results/throughput.json`: `date`, `game_version`, `runtime`, `sequential_calls`, `sequential_seconds`, `sequential_per_s`, `p50_ms`, `p99_ms`, `curve` of `{depth, calls, seconds, per_s, busy}` for depths 1, 8, 32, 128, 256, `batch`: `{steps, seconds, ms_per_step}` (one 100-step batch of `RandomInt`, no spinner: the batch figure), plus `spin`: `{switch_interval_s, calls, remote: {no_spin: {per_s, p50_ms, p99_ms}, spin: {...}}, inprocess: {no_spin: {...}, spin: {...}}, spin_loops, fps_observed_spin}` (both `no_spin`/`spin` pairs use the same number of calls, measured back to back). The throughput test does not measure frame time (frame stalls under a spinning thread are pinned by spike S9, Task 5); the burst test does, for our own tasks.
  - `game.state` `diagnostics` gains `max_task_drain_ms` (float) and `max_task_drain_count` (int): since launch, the longest run of our queued task bodies within one frame and the most bodies in one run (Step 3b). Task 27's `late_results_dropped` and `in_flight` are unchanged.
  - `livetest/results/burst.json`: `date`, `requested`, `per_connection`, `connections`, `ok`, `busy`, `seconds`, `max_task_drain_ms`, `max_task_drain_count`, `max_task_drain_ms_before`, `s9_frame_ms`, `worst_frame_ms_estimate` (= `s9_frame_ms` + `max_task_drain_ms`), `per_task_drain_ms` (= `max_task_drain_ms` / `max_task_drain_count`), and, when `max_task_drain_count` is below `requested` (the cap), `drain_ms_at_cap_extrapolated` (= `per_task_drain_ms` × `requested`) and `worst_frame_ms_estimate_at_cap` (= `s9_frame_ms` + that); `unmeasured` names the object-conversion `AddTask`, when the callback runs outside our task (Task 27), which the meter does not wrap.

- [ ] **Step 1: The in-process scripts**

`livetest/scripts/livetest_smoke/__init__.py`:
```python
"""In-process smoke test and benchmark (Task 31). Deployed to Data/SKSE/Plugins/Skython/scripts/ by
`tools/box/box_deploy.sh --scripts livetest/scripts`. Development only: never ship it to players.

setup() starts one thread and returns at once. That thread waits for a loaded save, runs the same calls the
live tests make, writes livetest_smoke.json next to python.log, then serves commands from livetest_control.json.
"""
import json
import logging
import os
import pathlib
import statistics
import sys
import threading
import time

import _skython
from skython import _host, errors

log = logging.getLogger("livetest_smoke")
PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"

_stop = threading.Event()
_spin_stop = threading.Event()
_spin_thread = None
_spin_loops = 0


def _dir() -> pathlib.Path:
    return pathlib.Path(json.loads(_skython.config())["log_dir"])


def _read(name: str):
    try:
        return json.loads((_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def _write(name: str, obj) -> None:
    path = _dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def _expect(exc, fn) -> None:
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def _checks(game):
    def state():
        st = game.state()
        assert st["interactive"] is True and st["player"].form_id == PLAYER_ID, st

    def call_static_int():
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6

    def call_static_form():
        assert game.call("Game", "GetPlayer").form_id == PLAYER_ID

    def call_method_float():
        assert game.call_method(game.call("Game", "GetPlayer"), "Actor", "GetActorValue", "Health") > 0

    def resolve():
        assert game.form(IRON_SWORD).type == "Weapon"

    def latent():
        assert game.call("Utility", "Wait", 0.2) is None

    def unknown_function():
        _expect(errors.UnknownFunction, lambda: game.call("NoSuchScriptAnywhere", "Foo"))

    def type_mismatch():
        _expect(errors.TypeMismatch, lambda: game.call("Utility", "RandomInt", "one", 6))

    def threads():
        out = []
        workers = [threading.Thread(target=lambda: out.append(game.call("Utility", "RandomInt", 1, 6))) for _ in range(8)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(30)
        assert len(out) == 8 and all(1 <= x <= 6 for x in out), out

    return [state, call_static_int, call_static_form, call_method_float, resolve, latent, unknown_function,
            type_mismatch, threads]


def run_smoke(game) -> list:
    results = []
    for check in _checks(game):
        try:
            check()
            results.append({"name": check.__name__, "ok": True})
        except Exception as e:  # recorded, never raised: this runs inside the game
            results.append({"name": check.__name__, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return results


def _spin() -> None:
    global _spin_loops
    while not _spin_stop.is_set():
        x = 0
        for i in range(20000):  # pure Python: holds the GIL until the interpreter's switch interval forces a hand-off
            x += i * i
        _spin_loops += 1


def _bench(game, calls: int) -> dict:
    lat = []
    t0 = time.perf_counter()
    for _ in range(calls):
        c0 = time.perf_counter()
        game.call("Utility", "RandomInt", 1, 6)
        lat.append((time.perf_counter() - c0) * 1000)
    secs = time.perf_counter() - t0
    lat.sort()
    return {"calls": calls, "seconds": round(secs, 3), "per_s": round(calls / secs, 1),
            "p50_ms": round(statistics.median(lat), 2), "p99_ms": round(lat[int(len(lat) * 0.99)], 2),
            "spinning": bool(_spin_thread and _spin_thread.is_alive())}


def _handle(game, cmd: dict) -> dict:
    global _spin_thread, _spin_loops
    name = cmd.get("cmd")
    if name == "spin_on":
        if not (_spin_thread and _spin_thread.is_alive()):
            _spin_stop.clear()
            _spin_loops = 0
            _spin_thread = threading.Thread(target=_spin, name="livetest_spin", daemon=True)
            _spin_thread.start()
        return {"spinning": True}
    if name == "spin_off":
        _spin_stop.set()
        if _spin_thread:
            _spin_thread.join(10)
        return {"spinning": False, "spin_loops": _spin_loops}
    if name == "bench":
        return _bench(game, int(cmd.get("calls", 300)))
    return {"error": f"unknown command {name!r}"}


def _control_loop(game) -> None:
    # Commands written before this run (an older control file) are never replayed.
    last = max((_read("livetest_control.json") or {}).get("seq", 0), (_read("livetest_reply.json") or {}).get("seq", 0))
    while not _stop.wait(0.2):
        cmd = _read("livetest_control.json")
        if not cmd or cmd.get("seq", 0) <= last:
            continue
        last = cmd["seq"]
        try:
            result = _handle(game, cmd)
        except Exception as e:
            log.exception("livetest_smoke: command %r failed", cmd)
            result = {"error": f"{type(e).__name__}: {e}"}
        _write("livetest_reply.json", {"seq": last, "result": result})


def _main(game) -> None:
    try:
        while not _stop.is_set():
            try:
                game.wait_ready(timeout=30)
                break
            except Exception:  # no save loaded yet (or an error raised at once): wait, never spin holding the GIL
                if _stop.wait(1.0):
                    return
        if _stop.is_set():
            return
        checks = run_smoke(game)
        ok = all(c["ok"] for c in checks)
        _write("livetest_smoke.json", {"status": "pass" if ok else "fail", "checks": checks,
                                       # Evidence of the shipping mode: the game's _skython and the in-process transport.
                                       "backend": _skython.BACKEND, "transport": type(game.transport).__name__,
                                       "scripts": _host.status()["scripts"],  # how the host loaded every script, this run
                                       "thread": threading.current_thread().name,
                                       "switch_interval_s": sys.getswitchinterval(), "time": time.time()})
        _skython.log("info" if ok else "error",
                    f"livetest_smoke: {'PASS' if ok else 'FAIL'} ({sum(c['ok'] for c in checks)}/{len(checks)} checks)")
        _control_loop(game)
    except Exception:
        log.exception("livetest_smoke: crashed")


def setup(game) -> None:
    _stop.clear()
    threading.Thread(target=_main, args=(game,), name="livetest_smoke", daemon=True).start()


def teardown() -> None:
    _stop.set()
    _spin_stop.set()
```

`livetest/smoke.py`:
```python
"""Linux side of livetest_smoke: its files live next to python.log (SKYTHON_LOG). With the game on the box
(SKYTHON_BOX=1, out/box-live.env) that is the local mirror out/box-skse/Skython: each read pulls the
file's text written since the launch stamp, and a command is pushed with tools/box/box_files.sh put-control."""
import json
import os
import pathlib
import subprocess
import time

from support import BOX, REPO, box_pull, fresh


def py_dir() -> pathlib.Path:
    return pathlib.Path(os.environ["SKYTHON_LOG"]).parent


def _read(name: str):
    if BOX:
        box_pull(f"Skython/{name}")
    try:
        return json.loads((py_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def wait_smoke(timeout: float = 180.0) -> dict:
    """The smoke results of THIS game run (written after the last launch: support.fresh; on the box, box_pull)."""
    path = py_dir() / "livetest_smoke.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (data := _read(path.name)) and fresh(path):  # _read first: in box mode it pulls the mirror file
            return data
        time.sleep(0.5)
    raise AssertionError(f"no fresh {path} within {timeout} s: is livetest_smoke deployed "
                         "(tools/box/box_deploy.sh --scripts livetest/scripts) and a save loaded?")


def command(cmd: str, timeout: float = 300.0, **kw) -> dict:
    # Monotonic across launches: the game's control loop starts from the highest seq it finds on disk, and in box mode
    # the mirror holds only this launch's text, so a seq derived from the mirror could restart below it.
    seq = time.time_ns()
    path = py_dir() / "livetest_control.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": seq, "cmd": cmd, **kw}))
    os.replace(tmp, path)
    if BOX:  # the only file Linux writes on the box
        subprocess.run([str(REPO / "tools" / "box" / "box_files.sh"), "put-control", str(path)], check=True,
                       capture_output=True, text=True, timeout=120)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = _read("livetest_reply.json")
        if r and r.get("seq") == seq:
            assert "error" not in r["result"], r["result"]
            return r["result"]
        time.sleep(0.2)
    raise AssertionError(f"livetest_smoke did not answer {cmd!r} within {timeout} s (see python.log)")
```

- [ ] **Step 2: The robustness tests**

Append to `livetest/support.py` (both test files use it; Task 27's `raw_state` stays in its own test file):
```python
def diagnostics(session) -> dict:
    """game.state's diagnostics over a fresh raw connection (late_results_dropped, in_flight, and Task 31's drain meter)."""
    s = raw_connect(session)
    try:
        return rpc(s, "game.state")["result"]["diagnostics"]
    finally:
        s.close()
```

`livetest/test_m1_robustness.py`:
```python
import os
import pathlib
import subprocess
import threading
import time

import pytest

from skython import errors
from skython.values import Fingerprint, Form
from smoke import wait_smoke
from support import diagnostics, raw_connect, read_frame, rpc, send_request

PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
MANUAL = os.environ.get("SPY_MANUAL") == "1"
manual = pytest.mark.skipif(not MANUAL, reason="manual test: run with SPY_MANUAL=1 and `pytest -s`, with someone at the keyboard")
SAVE_TEST = os.environ.get("SPY_SAVE_TEST") == "1"   # opt-in: every run leaves one new save on the box
save_test = pytest.mark.skipif(not SAVE_TEST, reason="save test (opt-in): run with SPY_SAVE_TEST=1 (game on the Windows box, DevBench answering; leaves one new save)")
BOX_CONSOLE = pathlib.Path(__file__).resolve().parent.parent / "tools" / "box" / "box_console.sh"
BOX_SAVES = pathlib.Path(__file__).resolve().parent.parent / "tools" / "box" / "box_saves.sh"
# A new save of our own per run, never an existing one (Task 1b deploy safety); list.ps1 lists the prefix.
SAVE_DURING_CALL = f"skypy_r38_savecall_{time.strftime('%Y%m%d%H%M%S')}"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_in_process_smoke_script_passed():
    data = wait_smoke()  # written in this run (support.fresh)
    failed = [c for c in data["checks"] if not c["ok"]]
    assert data["status"] == "pass" and not failed, failed
    # The shipping mode really ran: the game's _skython and the in-process transport, not a bridge-backed Game.
    assert data["backend"] == "game" and data["transport"] == "InProcessTransport", data
    # The host loaded every script in this run: the broken one failed (Task 26's livetest_broken), the smoke one ran.
    scripts = data["scripts"]
    assert scripts["livetest_broken"].startswith("failed"), scripts
    assert scripts["livetest_smoke"] == "ok", scripts


def test_runtime_reference_fingerprint(game):
    sword = game.form(IRON_SWORD)
    # PlaceAtMe(akFormToPlace, aiCount, abForcePersist, abInitiallyDisabled[, abDeleteWhenAble]): persistent,
    # not disabled, every argument passed. Try five parameters, fall back to four (check ObjectReference.psc).
    args = (sword, 1, True, False, True)
    try:
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", *args)
    except errors.TypeMismatch as e:
        if "takes 4" not in str(e):
            raise
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", *args[:4])
    try:
        assert ref.form_id >> 24 == 0xFF and ref.form_key is None
        assert ref.fingerprint == Fingerprint(IRON_SWORD, "ObjectReference")
        assert game.call_method(ref, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
        forged = Form(form_id=ref.form_id, fingerprint=Fingerprint("Skyrim.esm|0x00000F", "ObjectReference"))
        with pytest.raises(errors.StaleReference):
            game.call_method(forged, "ObjectReference", "GetBaseObject")
    finally:
        game.call_method(ref, "ObjectReference", "Delete")


def test_disconnect_with_calls_in_flight_drops_the_results(session, game):
    n = 32  # enough to prove the point, few enough that the VM is never flooded
    before = diagnostics(session)
    s = raw_connect(session)
    for i in range(1, n + 1):
        send_request(s, "papyrus.call_static",
                     {"script": "Utility", "function": "Wait", "args": [2.0], "timeout_ms": 30000}, req_id=i)
    time.sleep(0.3)  # let the bridge submit them, so they really are in flight when we close
    assert diagnostics(session)["in_flight"] >= before["in_flight"] + n
    s.close()  # the bridge cancels the connection's tickets (Task 23)
    time.sleep(0.5)  # well before the 2 s waits finish in the game
    # Dispatcher::Cancel releases each entry at once: the closed connection holds nothing any more.
    assert diagnostics(session)["in_flight"] == before["in_flight"]
    time.sleep(4)  # the 32 waits finish in the game
    # Every late result was dropped and counted, none delivered.
    assert diagnostics(session)["late_results_dropped"] - before["late_results_dropped"] == n
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_many_short_connections(session):
    for i in range(50):
        s = raw_connect(session)
        assert rpc(s, "game.state", req_id=i + 1)["result"]["interactive"] is True
        s.close()


@manual
def test_paused_game_reports_state_and_honours_require_unpaused(session):
    """Spec 4.1: a menu that pauses the game. A human opens and closes it."""
    s = raw_connect(session)
    input("\nIn the game, open the INVENTORY or JOURNAL menu (it must pause the game), then press Enter here... ")
    st = rpc(s, "game.state")["result"]
    assert st["paused"] is True and st["menus"], st
    r = rpc(s, "papyrus.call_static", {"script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": True}, req_id=2)
    assert r["error"]["code"] == "not_ready" and r["error"]["detail"]["reason"] == "paused"
    r = rpc(s, "papyrus.call_static", {"script": "Utility", "function": "Wait", "args": [0.5], "timeout_ms": 1500}, req_id=3)
    assert r["error"]["code"] == "timeout" and r["error"]["detail"]["paused"] is True and r["error"]["detail"]["menus"]
    input("Close the menu, then press Enter here... ")
    assert rpc(s, "game.state", req_id=4)["result"]["paused"] is False
    assert rpc(s, "papyrus.call_static", {"script": "Utility", "function": "RandomInt", "args": [1, 6]}, req_id=5)["ok"]
    s.close()


def box_console(command: str) -> str:
    # DevBench reroutes `save <name>` and `load <name>` to its game tool; typed raw they would deadlock (Task 1b).
    r = subprocess.run([str(BOX_CONSOLE), command], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"box_console.sh {command!r} failed: {r.stdout}{r.stderr}"
    return r.stdout


def steady_late_count(session, settle: float = 1.0, tries: int = 10) -> int:
    """late_results_dropped once two readings `settle` seconds apart agree (Task 27's steady_late_count, via support)."""
    prev = diagnostics(session)["late_results_dropped"]
    for _ in range(tries):
        time.sleep(settle)
        now = diagnostics(session)["late_results_dropped"]
        if now == prev:
            return now
        prev = now
    raise AssertionError("late_results_dropped kept changing after the load")


def box_save_count(name: str) -> int:
    r = subprocess.run([str(BOX_SAVES), "count", name], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"box_saves.sh count {name} failed: {r.stdout}{r.stderr}"
    return int(r.stdout.strip())


@save_test
def test_save_during_a_latent_call_then_load(session, game):
    """R38: a save taken while our Utility.Wait is suspended in the VM must not record our callback. Task 27's
    functor returns false from CanSave(); the base class already does at the pin, so this pins the behaviour, not
    the override. Loading that save ends the call NotReady (the load cancels it) or Timeout, never a crash; no late
    result arrives after the load; the game keeps running."""
    assert box_save_count(SAVE_DURING_CALL) == 0, (f"a save named {SAVE_DURING_CALL}* already exists on the box: never "
                                                   "save over it; ask the owner")
    outcome = {}

    def call():
        try:
            outcome["result"] = game.call("Utility", "Wait", 5.0, timeout=30)
        except Exception as e:  # recorded, asserted below
            outcome["error"] = e

    t = threading.Thread(target=call, name="latent-call", daemon=True)
    t.start()
    time.sleep(0.5)
    assert diagnostics(session)["in_flight"] >= 1, "the 5 s wait is not in flight"
    box_console(f"save {SAVE_DURING_CALL}")
    box_console(f"load {SAVE_DURING_CALL}")
    t.join(60)
    assert not t.is_alive(), "the call neither returned nor raised within 60 s"
    assert "result" not in outcome, ("the 5 s wait finished before the load started (two DevBench round trips took too "
                                     "long): a setup problem, not a product result; do not reinterpret it.")
    assert isinstance(outcome["error"], (errors.NotReady, errors.Timeout)), outcome
    game.wait_ready(timeout=180)  # the loaded game is up
    # The baseline is taken after the load, once it is steady: the cancelled call's own late result (if the VM still
    # delivers one) is not what this test is about.
    baseline = steady_late_count(session)
    time.sleep(6)  # longer than the 5 s wait: a callback restored from the save would have fired by now
    assert diagnostics(session)["late_results_dropped"] == baseline, \
        "a late result arrived after the load: the save recorded our callback"
    assert box_save_count(SAVE_DURING_CALL) == 1, f"{SAVE_DURING_CALL}.ess was not written: no save was tested"
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


@manual
def test_inflight_call_is_failed_when_a_save_starts_loading(session):
    s = raw_connect(session)
    send_request(s, "papyrus.call_static", {"script": "Utility", "function": "Wait", "args": [60.0], "timeout_ms": 120000})
    print("\nA 60 s wait is in flight. In the game, LOAD A SAVE now (within 60 s)... ", flush=True)
    s.settimeout(90)
    r = read_frame(s)
    assert r["ok"] is False and r["error"]["code"] == "not_ready" and r["error"]["detail"]["reason"] == "loading", r
    s.close()
```
(Task 27 already pins an out-of-range Int and a timed-out call's late result; they are not repeated here.) `test_save_during_a_latent_call_then_load` differs from the manual load test: its point is a **save** taken while our callback is suspended in the VM (`CanSave()` false, Task 27), then a load of that save. It runs only with `SPY_SAVE_TEST=1` (opt-in), through `tools/box/box_console.sh` (DevBench, Task 1b). The window is tight: both DevBench round trips must start the load within the 5 s wait. If the call returns `None`, the test fails with a setup message: never reinterpret it as a product result. Each run saves under a new name, `skypy_r38_savecall_<timestamp>`, and first checks that no save with that name exists (`tools/box/box_saves.sh count`; never save over another save). After the load and `wait_ready`, it takes the `late_results_dropped` baseline once two readings 1 s apart agree. That keeps the cancelled call's own late result out of the comparison. The cost (ruling R44): a callback restored from the save that fired before the baseline settled (within about 1-2 s of `wait_ready`) would be missed. It then checks that the count has not changed 6 s later (longer than the 5 s wait), and that the `.ess` was written. Every run leaves one new save (`.ess` and `.skse`) on the box. `tools/box/win/list.ps1`'s pattern `skypy_r38_savecall` is a prefix, so `tools/box/box_teardown_list.sh` lists them all (Task 1b, ruling R42). The two manual tests meet the spec's `require_unpaused` and load-cancellation checks without blocking an unattended run. If `paused` is stale or wrong, `life::Paused()` (Task 25) is read at the wrong moment: record that in `spikes/RESULTS.md` and read `RE::UI::GameIsPaused()` where it is used.

- [ ] **Step 3: The throughput baseline, with and without a spinning Python thread**

`livetest/test_m1_throughput.py`:
```python
import datetime
import json
import os
import pathlib
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from skython import errors
from smoke import command, wait_smoke
from support import diagnostics, raw_connect, read_frame, send_request

RESULTS = pathlib.Path(__file__).parent / "results" / "throughput.json"
BURST_RESULTS = pathlib.Path(__file__).parent / "results" / "burst.json"
BURST_TOTAL = int(os.environ.get("SPY_BURST", "1024"))           # the dev ini's max_inflight_total (Task 25)
BURST_PER_CONNECTION = int(os.environ.get("SPY_BURST_PER_CONN", "256"))  # the dev ini's max_inflight_per_connection
S9_FRAME_MS = float(os.environ.get("SPY_S9_FRAME_MS", "46.1"))   # spikes/RESULTS.md S9 row: baseline median frame, box
SEQUENTIAL = int(os.environ.get("SPY_SEQ", "500"))   # S9: one drain (~46 ms on the box) per call alone: 500 calls take about 25 s
SPIN_CALLS = int(os.environ.get("SPY_SPIN_CALLS", "300"))
CALLS_PER_DEPTH = 2000
BATCH_STEPS = 100   # the batch figure Task 32's README quotes (ms per step)
DEPTHS = (1, 8, 32, 128, 256)  # 256 is the dev ini's per-connection cap: a few `busy` answers there are data


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def one_call(game):
    try:
        return game.call("Utility", "RandomInt", 1, 6, timeout=30)
    except errors.Busy:
        return None


def sequential(game, n: int) -> tuple[float, list]:
    lat = []
    t0 = time.perf_counter()
    for _ in range(n):
        c0 = time.perf_counter()
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6
        lat.append((time.perf_counter() - c0) * 1000)
    return time.perf_counter() - t0, sorted(lat)


def summary(secs: float, lat: list) -> dict:
    return {"per_s": round(len(lat) / secs, 1), "p50_ms": round(statistics.median(lat), 2),
            "p99_ms": round(lat[int(len(lat) * 0.99)], 2)}


def test_throughput_baseline(game):
    smoke = wait_smoke()  # the in-process script is up and serving commands
    seq_s, lat = sequential(game, SEQUENTIAL)

    curve = []
    for depth in DEPTHS:
        t1 = time.perf_counter()
        with ThreadPoolExecutor(depth) as pool:
            results = list(pool.map(lambda _: one_call(game), range(CALLS_PER_DEPTH)))
        secs = time.perf_counter() - t1
        busy = results.count(None)
        assert all(r is None or 1 <= r <= 6 for r in results)
        curve.append({"depth": depth, "calls": CALLS_PER_DEPTH, "seconds": round(secs, 3),
                      "per_s": round((CALLS_PER_DEPTH - busy) / secs, 1), "busy": busy})

    # The batch figure: one batch of primitive steps (Task 30), no spinner. Steps run one task each, in order.
    b = game.batch()
    for _ in range(BATCH_STEPS):
        b.call("Utility", "RandomInt", 1, 6)
    t2 = time.perf_counter()
    batch_results = b.run(timeout=60)
    batch_s = time.perf_counter() - t2
    assert len(batch_results) == BATCH_STEPS and all(1 <= r <= 6 for r in batch_results)

    # GIL hand-off cost: the same calls with one CPU-bound Python thread spinning inside the game. Each no-spin
    # baseline uses the same number of calls as its spin sample and is taken right before it.
    remote_no_spin = summary(*sequential(game, SPIN_CALLS))
    inproc_no_spin = command("bench", calls=SPIN_CALLS)
    command("spin_on")
    try:
        time.sleep(1.0)
        remote_spin = summary(*sequential(game, SPIN_CALLS))
        inproc_spin = command("bench", calls=SPIN_CALLS)
    finally:
        spin_off = command("spin_off")
    assert spin_off["spin_loops"] > 0, "the spinning thread never ran"
    assert inproc_spin["spinning"] is True and inproc_no_spin["spinning"] is False

    welcome = game.welcome
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": welcome.get("game_version", "unknown"),
        "runtime": welcome.get("runtime", "unknown"),
        "sequential_calls": SEQUENTIAL,
        "sequential_seconds": round(seq_s, 3),
        "sequential_per_s": round(SEQUENTIAL / seq_s, 1),
        "p50_ms": round(statistics.median(lat), 2),
        "p99_ms": round(lat[int(len(lat) * 0.99)], 2),
        "curve": curve,
        "batch": {"steps": BATCH_STEPS, "seconds": round(batch_s, 3), "ms_per_step": round(batch_s * 1000 / BATCH_STEPS, 2)},
        "spin": {
            "switch_interval_s": smoke["switch_interval_s"],
            "calls": SPIN_CALLS,
            "remote": {"no_spin": remote_no_spin, "spin": remote_spin},
            "inprocess": {"no_spin": {k: inproc_no_spin[k] for k in ("per_s", "p50_ms", "p99_ms")},
                          "spin": {k: inproc_spin[k] for k in ("per_s", "p50_ms", "p99_ms")}},
            "spin_loops": spin_off["spin_loops"],
            # One reading from an overlay while the spinner runs (SPY_FPS_SPIN); null if not taken. Not a frame-time
            # measurement: frame stalls are pinned by spike S9 (Task 5).
            "fps_observed_spin": os.environ.get("SPY_FPS_SPIN"),
        },
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


def test_burst_worst_frame(session, game):
    """R38 (comparison Top 6): max_inflight_total requests at once. Records the longest time our queued task bodies
    held the main thread in one frame (Step 3b's drain meter), and the worst frame estimated from it."""
    before = diagnostics(session)
    n_conn = -(-BURST_TOTAL // BURST_PER_CONNECTION)  # the bridge caps each connection: spread over several
    counts = [min(BURST_PER_CONNECTION, BURST_TOTAL - i * BURST_PER_CONNECTION) for i in range(n_conn)]
    socks = [raw_connect(session) for _ in range(n_conn)]
    try:
        t0 = time.perf_counter()
        for s, count in zip(socks, counts):
            for i in range(count):
                send_request(s, "papyrus.call_static",
                             {"script": "Utility", "function": "RandomInt", "args": [1, 6], "timeout_ms": 60000},
                             req_id=i + 1)
        replies = []
        for s, count in zip(socks, counts):
            s.settimeout(90)
            replies += [read_frame(s) for _ in range(count)]
        secs = time.perf_counter() - t0
    finally:
        for s in socks:
            s.close()
    ok = sum(1 for r in replies if r["ok"])
    busy = sum(1 for r in replies if not r["ok"] and r["error"]["code"] == "busy")
    assert ok + busy == BURST_TOTAL, [r for r in replies if not r["ok"] and r["error"]["code"] != "busy"][:5]
    time.sleep(0.5)
    # One more task in a later frame closes the burst's last drain (the meter folds a run when the next one starts).
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6
    after = diagnostics(session)
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "requested": BURST_TOTAL, "per_connection": BURST_PER_CONNECTION, "connections": n_conn,
        "ok": ok, "busy": busy, "seconds": round(secs, 3),
        "max_task_drain_ms": after["max_task_drain_ms"], "max_task_drain_count": after["max_task_drain_count"],
        "max_task_drain_ms_before": before["max_task_drain_ms"],
        "s9_frame_ms": S9_FRAME_MS,
        "worst_frame_ms_estimate": round(S9_FRAME_MS + after["max_task_drain_ms"], 2),
        "unmeasured": "the object-conversion AddTask, when the callback runs outside our task (Task 27), is not metered",
    }
    count = after["max_task_drain_count"]
    per_task = after["max_task_drain_ms"] / count if count else None
    out["per_task_drain_ms"] = round(per_task, 4) if per_task is not None else None
    if per_task is not None and count < BURST_TOTAL:  # the worst run drained fewer than the cap: scale it up
        out["drain_ms_at_cap_extrapolated"] = round(per_task * BURST_TOTAL, 2)
        out["worst_frame_ms_estimate_at_cap"] = round(S9_FRAME_MS + per_task * BURST_TOTAL, 2)
    BURST_RESULTS.parent.mkdir(exist_ok=True)
    BURST_RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
```
There is no pass threshold in v1 beyond "every call succeeds, with and without the spinner": this records the baseline M2 and skyrim-dm budget against. Read the curve, not one number: if `per_s` stops growing with depth, frames or the GIL are the limit (this test does not tell which; spike S9's frame-time record does). Compare `p50_ms` with S9: one drain (~46 ms on the box) per primitive call alone, two with a spinning Python thread; this task records the batch figure (`batch.ms_per_step`). Compare `spin.*.spin.p99_ms` with `no_spin.p99_ms`: the difference is what a busy Python thread (skyrim-dm's 66-second generation) costs a delivery call, which spike S9 (Task 5) measured first; if it is more than about `switch_interval_s` × 2 per call, note it for skyrim-dm (it can lower its own work's priority by sleeping between batches).

`test_burst_worst_frame` has no pass threshold: it records. `max_task_drain_ms` is the time our task bodies (lookup, argument check, dispatch) held the main thread in the worst frame; the VM's own work for those calls runs in Papyrus's per-frame budget, not in our tasks, and other plugins' tasks between ours split a run, so `worst_frame_ms_estimate` is an estimate (S9's baseline frame plus our longest drain), not a sampled frame time. If `max_task_drain_ms_before` is already the maximum, the burst was not the worst drain since launch: say so in the report. SKSE usually drains the burst over several frames, so `max_task_drain_count` is below the cap. The report then extrapolates from the per-task drain (`per_task_drain_ms` × `requested` → `drain_ms_at_cap_extrapolated`, `worst_frame_ms_estimate_at_cap`): the worst case, in which every queued task lands in one frame. It is an extrapolation, not a measurement, and the report says so. The object-conversion `AddTask`, when the callback runs outside our task (at most one more task for an object result or array of objects, none when the VM calls back synchronously inside our task, Task 27 and R52), is not wrapped by the meter, and the burst's `RandomInt` calls never take it. Task 30's batch-step tasks are wrapped (Step 3b). Its cost is **unmeasured**; the report says that too. **The cap may be lowered from these numbers:** if `worst_frame_ms_estimate` (or the extrapolation at the cap) exceeds the baseline by more than about one frame, report it to the owner with a proposed `max_inflight_total`. The drain scales with the count. The owner sets the value in Task 25's ini defaults, not this task.

- [ ] **Step 3b: The drain meter (passive; no task re-queues itself, the README's 'One `AddTask` per request' constraint)**

SKSE runs every queued task in one go each frame, so our task bodies cluster by frame: spike S9's frame-sampling idea, applied to our own tasks without adding any. Append to `plugin/src/Ops.h` (Task 27), inside `namespace spy::ops` after `InTask` (it lives in the header so Task 30's batch-step task in `Ops.cpp` is measured too; `inline` gives one meter for the whole DLL):
```cpp
// Task 31 (ruling R38): how long our queued task bodies hold the main thread in one frame. Bodies that start less than
// kGap after the previous one ended belong to the same run (one frame's drain). A run is folded into the two maxima
// when the next run starts, so the latest run shows up after one more task. Written only from inside our SKSE tasks,
// which are serialized (observed, R49), on varying threads; the maxima are atomics because BaseState() reads them
// from any thread. No I/O, no allocation.
class DrainMeter
{
public:
    void Begin()
    {
        const auto now = Clock::now();
        if (count_ == 0 || now - lastEnd_ > kGap) {
            Fold();
            start_ = now;
            count_ = 0;
        }
        ++count_;
    }
    void End() { lastEnd_ = Clock::now(); }
    double MaxMs() const { return maxMs_.load(); }
    std::uint32_t MaxCount() const { return maxCount_.load(); }

private:
    using Clock = std::chrono::steady_clock;
    static constexpr auto kGap = std::chrono::milliseconds(1);
    void Fold()
    {
        if (count_ == 0) return;
        const double ms = std::chrono::duration<double, std::milli>(lastEnd_ - start_).count();
        if (ms > maxMs_.load()) maxMs_.store(ms);
        if (count_ > maxCount_.load()) maxCount_.store(count_);
    }
    Clock::time_point start_{}, lastEnd_{};
    std::uint32_t count_ = 0;
    std::atomic<double> maxMs_{0.0};
    std::atomic<std::uint32_t> maxCount_{0};
};

inline DrainMeter& Meter()
{
    static auto* meter = new DrainMeter();  // immortal, like the dispatcher
    return *meter;
}

struct Measured
{
    Measured() { Meter().Begin(); }
    ~Measured() { Meter().End(); }
};
```
Add `#include <atomic>`, `#include <chrono>` and `#include <cstdint>` to `Ops.h`'s includes. Put `ops::Measured measured;` right after `ops::InTask in;` in **both** `AddTask` lambdas in `GameBackend.cpp` (the one in `Run`, before `if (cancelled()) return;`, and the one in `State`) and in Task 30's batch-step lambda in `Ops.cpp` (`QueueBatchStep`: `[st] { ops::InTask in; ops::Measured measured; BatchStep(st); }`). The object-conversion `AddTask` in `Callback` stays unwrapped (the `unmeasured` note). In `GameBackend.cpp`'s `WithDiagnostics`, extend the object:
```cpp
        s["diagnostics"] = {{"late_results_dropped", d.LateResults()}, {"in_flight", d.InFlight()},
                            {"max_task_drain_ms", ops::Meter().MaxMs()}, {"max_task_drain_count", ops::Meter().MaxCount()}};
```
Task 27's `test_state_shape_is_stable` checks only the two keys it knows, so it still passes.

- [ ] **Step 4: Run**

The game runs on the Windows box ([Task 1c](task-01c-part-c-on-the-box.md)); the save is loaded through DevBench; every wait is bounded and counts only this launch's log text.
```bash
mkdir -p livetest/results && touch livetest/results/.gitkeep
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll  # Step 3b changed the core
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --python
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load spikebase"     # stands in for Continue; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_robustness.py livetest/test_m1_throughput.py -q -s -rs
tools/box/box_files.sh pull Skython.log >/dev/null
grep '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken || echo "no unexpected errors"
# The save-during-a-call test (DevBench answering, a save loaded):
SKYTHON_LIVE=1 SPY_SAVE_TEST=1 uv run --project python pytest livetest/test_m1_robustness.py -q -s -rs -k save_during_a_latent_call
# The two manual tests: answer each prompt after acting from a second shell through DevBench (Task 1c), e.g.
#   tools/box/box_console.sh --tool menu '{"action":"open","name":"InventoryMenu"}'   (then "close")
#   tools/box/box_console.sh "load spikebase"
SKYTHON_LIVE=1 SPY_MANUAL=1 uv run --project python pytest livetest/test_m1_robustness.py -q -s -rs -k "paused or save_starts_loading"
tools/box/box_quit.sh   # must print NO_NEW_CRASH_LOG; NOT_CLEAN without a crash log is the box's exit hang (Task 1b): box_stop.sh
```
(`--scripts livetest/scripts` deploys both `livetest_smoke` and Task 26's `livetest_broken`; `tools/box/box_teardown_list.sh` lists them for removal later, with the owner's approval.)

Expected: all pass; the two manual tests and the opt-in save test are skipped in the first run. `livetest/results/burst.json` is written (record `max_task_drain_ms`, `worst_frame_ms_estimate`, `per_task_drain_ms` and, when present, `drain_ms_at_cap_extrapolated` / `worst_frame_ms_estimate_at_cap` marked as extrapolated, plus the `unmeasured` note, in the report). With `SPY_SAVE_TEST=1` the save-during-a-call test passes and `box_quit.sh` prints `NO_NEW_CRASH_LOG`; a crash, or a hang during the test, is a STOP (`NOT_CLEAN` at exit with no crash log is the box's known exit hang, Task 1b). Any skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure. The `grep` prints `no unexpected errors` (only the deliberate `livetest_broken` failure may log at error level; refused handshakes, bad frames and dropped connections are warnings or info). The game is still responsive afterwards. The manual run passes when the operator opens a pausing menu, closes it, and loads a save as prompted (through DevBench, Task 1c; record "driven through DevBench, no player input").

- [ ] **Step 5: Commit**

```bash
git add livetest plugin/src/GameBackend.cpp plugin/src/Ops.h plugin/src/Ops.cpp && git commit -m "test(live): robustness, throughput with a spinning thread, and the in-process smoke script"
```
