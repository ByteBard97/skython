# Task 31: Robustness, throughput and the in-process smoke script

Adapted from: branch `plan-out-of-process`, task 19 (`task-19-robustness-and-throughput-baseline.md`). The robustness tests and the throughput curve are kept and now run through the dev bridge. New here: an in-process smoke script that makes the same calls from inside the game (the shipping mode), a deliberately broken script (a Python exception must not touch the game), and throughput with and without a CPU-spinning Python thread inside the game (GIL hand-off cost, Review Focus 6).

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.5, 4.10, 8. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 30 is done.
>
> **Runs on:** the Windows build box plus the game; tests run on Linux. Part of it is manual: loading a save and opening a menu need a human at the keyboard under Proton.
>
> **STOP and ask the owner (do not improvise) if:** a live test crashes the game or freezes it for more than 30 s; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; the manual tests are needed and nobody is at the keyboard.

**Files:**
- Create: `livetest/scripts/livetest_smoke/__init__.py` (in-process smoke script and benchmark; deployed to the game's `scripts/` folder, never shipped to players)
- Create: `livetest/scripts/livetest_broken/__init__.py` (raises in `setup`, on purpose)
- Create: `livetest/smoke.py` (Linux side: reads the smoke results, sends commands through a control file)
- Create: `livetest/test_m1_robustness.py`, `livetest/test_m1_throughput.py`, `livetest/results/.gitkeep`

**Interfaces:**
- Consumes: the live plugin from Task 30; `skyrim` (Tasks 20-22: `Game`, `errors`, `values.Form`, `values.Fingerprint`); `_skyrim.config()["log_dir"]` and `_skyrim.log(level, msg)` (Task 19 contract); `skyrim._host` loads each package in `scripts/` and calls `setup(game)` on the host thread, `teardown()` on reload (Task 24); `tools/deploy.sh ... --scripts DIR` (Task 26); `livetest/support.py` (`raw_connect`, `rpc`, `encode`, `read_frame`, `send_request`) and fixtures `session`, `game` (Tasks 26-27); `tools/launch_game.sh` touches `~/.local/state/skyrim-python/launch.stamp` (Task 1); `tools/env.sh` exports `SKYRIM_PYTHON_SESSION` (the session file sits in the same folder as `python.log`: index Fixed paths).
- Produces:
  - In the game's Python log folder (`.../SKSE/SkyrimPython/`): `livetest_smoke.json` (`{status: "pass"|"fail", checks: [{name, ok, error?}], thread, switch_interval_s, time}`), and the control pair `livetest_control.json` (`{seq, cmd, ...}`, written by Linux) / `livetest_reply.json` (`{seq, result}`, written by the script). Commands: `spin_on`, `spin_off` (reply carries `spin_loops`), `bench` (`calls`; reply `{calls, seconds, per_s, p50_ms, p99_ms, spinning}`).
  - `livetest/results/throughput.json`: the old keys (`date`, `game_version`, `runtime`, `sequential_calls`, `sequential_seconds`, `sequential_per_s`, `p50_ms`, `p99_ms`, `curve` of `{depth, calls, seconds, per_s, busy, fps_observed}` for depths 1, 8, 32, 128, 256, `fps_note`) plus `spin`: `{switch_interval_s, remote: {no_spin: {per_s, p50_ms, p99_ms}, spin: {...}}, inprocess: {no_spin: {...}, spin: {...}}, spin_loops, fps_observed_spin}`.

- [ ] **Step 1: The in-process scripts**

`livetest/scripts/livetest_smoke/__init__.py`:
```python
"""In-process smoke test and benchmark (Task 31). Deployed to Data/SKSE/Plugins/SkyrimPython/scripts/ by
`tools/deploy.sh ... --scripts livetest/scripts`. Development only: never ship it to players.

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

import _skyrim
from skyrim import errors

log = logging.getLogger("livetest_smoke")
PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"

_stop = threading.Event()
_spin_stop = threading.Event()
_spin_thread = None
_spin_loops = 0


def _dir() -> pathlib.Path:
    return pathlib.Path(json.loads(_skyrim.config())["log_dir"])


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
            except Exception:  # no save loaded yet: keep waiting until a save is loaded or teardown
                continue
        if _stop.is_set():
            return
        checks = run_smoke(game)
        ok = all(c["ok"] for c in checks)
        _write("livetest_smoke.json", {"status": "pass" if ok else "fail", "checks": checks,
                                       "thread": threading.current_thread().name,
                                       "switch_interval_s": sys.getswitchinterval(), "time": time.time()})
        _skyrim.log("info" if ok else "error",
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

`livetest/scripts/livetest_broken/__init__.py`:
```python
"""Raises in setup, on purpose (Task 31). skyrim._host must log the traceback, mark this script failed,
and keep loading the others; the game must not notice."""


def setup(game):
    raise RuntimeError("livetest_broken: deliberate failure in setup")
```

`livetest/smoke.py`:
```python
"""Linux side of livetest_smoke: its files live next to python.log, in the same folder as the session file."""
import json
import os
import pathlib
import time

STAMP = pathlib.Path.home() / ".local/state/skyrim-python/launch.stamp"


def py_dir() -> pathlib.Path:
    return pathlib.Path(os.environ["SKYRIM_PYTHON_SESSION"]).parent


def _read(name: str):
    try:
        return json.loads((py_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def python_log_text() -> str:
    try:
        return (py_dir() / "python.log").read_text(errors="replace")
    except OSError:
        return ""


def wait_smoke(timeout: float = 180.0) -> dict:
    """The smoke results of THIS game run (written after the last tools/launch_game.sh)."""
    path = py_dir() / "livetest_smoke.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fresh = path.exists() and (not STAMP.exists() or path.stat().st_mtime >= STAMP.stat().st_mtime)
        if fresh and (data := _read(path.name)):
            return data
        time.sleep(0.5)
    raise AssertionError(f"no fresh {path} within {timeout} s: is livetest_smoke deployed "
                         "(tools/deploy.sh ... --scripts livetest/scripts) and a save loaded?")


def command(cmd: str, timeout: float = 300.0, **kw) -> dict:
    seq = 1 + max((_read("livetest_control.json") or {}).get("seq", 0), (_read("livetest_reply.json") or {}).get("seq", 0))
    path = py_dir() / "livetest_control.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": seq, "cmd": cmd, **kw}))
    os.replace(tmp, path)
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

`livetest/test_m1_robustness.py`:
```python
import os
import time

import pytest

from skyrim import errors
from skyrim.values import Fingerprint, Form
from smoke import python_log_text, wait_smoke
from support import raw_connect, read_frame, rpc, send_request

PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
MANUAL = os.environ.get("SPY_MANUAL") == "1"
manual = pytest.mark.skipif(not MANUAL, reason="manual test: run with SPY_MANUAL=1 and `pytest -s`, with someone at the keyboard")


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def late_dropped(session) -> int:
    s = raw_connect(session)
    try:
        return rpc(s, "game.state")["result"]["diagnostics"]["late_results_dropped"]
    finally:
        s.close()


def test_in_process_smoke_script_passed():
    data = wait_smoke()
    failed = [c for c in data["checks"] if not c["ok"]]
    assert data["status"] == "pass" and not failed, failed
    assert data["thread"] == "livetest_smoke"  # ran on its own thread, not the host thread or the game's


def test_a_failing_script_is_logged_and_contained(game):
    wait_smoke()  # the host has finished loading scripts by the time the smoke script reports
    text = python_log_text()
    assert "livetest_broken: deliberate failure in setup" in text and "Traceback" in text
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the bridge and the game carry on


def test_int_overflow_is_rejected_by_the_plugin(session):
    s = raw_connect(session)
    r = rpc(s, "papyrus.call_static", {"script": "Utility", "function": "RandomInt", "args": [1, 2147483648]})
    s.close()
    assert r["ok"] is False and r["error"]["code"] == "type_mismatch"


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
    before = late_dropped(session)
    s = raw_connect(session)
    for i in range(1, n + 1):
        send_request(s, "papyrus.call_static",
                     {"script": "Utility", "function": "Wait", "args": [2.0], "timeout_ms": 30000}, req_id=i)
    time.sleep(0.3)  # let the bridge submit them, so they really are in flight when we close
    s.close()  # the bridge cancels the connection's tickets (Task 23)
    time.sleep(4)  # the 32 waits finish in the game
    assert late_dropped(session) - before == n  # every late result was dropped and counted, none delivered
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6
    # The closed connection's slots were released: a new connection is not told `busy`.
    s2 = raw_connect(session)
    for i in range(1, n + 1):
        send_request(s2, "papyrus.call_static", {"script": "Utility", "function": "Wait", "args": [0.2]}, req_id=i)
    s2.settimeout(30)
    assert all(read_frame(s2)["ok"] for _ in range(n))
    s2.close()


def test_timeout_is_reported_and_late_result_dropped(session, game):
    before = late_dropped(session)
    with pytest.raises(errors.Timeout):
        game.call("Utility", "Wait", 2.0, timeout=0.5)
    time.sleep(3)
    assert late_dropped(session) == before + 1
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
The two manual tests meet the spec's `require_unpaused` and load-cancellation checks without blocking an unattended run. If `paused` is stale or wrong, `life::Paused()` (Task 25) is read at the wrong moment: record that in `spikes/RESULTS.md` and read `RE::UI::GameIsPaused()` where it is used.

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

from skyrim import errors
from smoke import command, wait_smoke

RESULTS = pathlib.Path(__file__).parent / "results" / "throughput.json"
SEQUENTIAL = int(os.environ.get("SPY_SEQ", "500"))   # 2-3 frames per call: 500 calls take 20-40 s
SPIN_CALLS = int(os.environ.get("SPY_SPIN_CALLS", "300"))
CALLS_PER_DEPTH = 2000
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
                      "per_s": round((CALLS_PER_DEPTH - busy) / secs, 1), "busy": busy,
                      # No API reports fps: read it from an overlay while this runs and set SPY_FPS (or edit the JSON).
                      "fps_observed": os.environ.get("SPY_FPS")})

    # GIL hand-off cost: the same calls with one CPU-bound Python thread spinning inside the game.
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
        "fps_note": "fps_observed is read by the owner from an overlay; null means it was not recorded",
        "spin": {
            "switch_interval_s": smoke["switch_interval_s"],
            "remote": {"no_spin": summary(seq_s, lat), "spin": remote_spin},
            "inprocess": {"no_spin": {k: inproc_no_spin[k] for k in ("per_s", "p50_ms", "p99_ms")},
                          "spin": {k: inproc_spin[k] for k in ("per_s", "p50_ms", "p99_ms")}},
            "spin_loops": spin_off["spin_loops"],
            "fps_observed_spin": os.environ.get("SPY_FPS_SPIN"),
        },
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
```
There is no pass threshold in v1 beyond "every call succeeds, with and without the spinner": this records the baseline M2 and skyrim-dm budget against. Read the curve, not one number: if `per_s` stops growing with depth, frames or the GIL are the limit, and the fps column tells them apart. Compare `p50_ms` with the frame time (16.7 ms at 60 fps): 2-3 frames is expected. Compare `spin.*.spin.p99_ms` with `no_spin.p99_ms`: the difference is what a busy Python thread (skyrim-dm's 66-second generation) costs a delivery call, which spike S9 (Task 5) measured first; if it is more than about `switch_interval_s` × 2 per call, note it for skyrim-dm (it can lower its own work's priority by sleeping between batches).

- [ ] **Step 4: Run**

Loading a save is manual (press Continue); every wait is bounded.
```bash
touch livetest/results/.gitkeep
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --package --scripts livetest/scripts
tools/launch_game.sh
source tools/env.sh
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "Main Menu" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
# Manual: press Continue. Then:
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "interactive = true" 300
uv run --project python pytest livetest/test_m1_robustness.py livetest/test_m1_throughput.py -q -s
grep '\[error\]' "$SKSE_LOG_DIR/SkyrimPython.log" | grep -v livetest_broken || echo "no unexpected errors"
# With someone at the keyboard, the two manual tests:
SPY_MANUAL=1 uv run --project python pytest livetest/test_m1_robustness.py -q -s -k "paused or save_starts_loading"
```
(Use Task 25's exact log phrases for the main menu and the interactive signal if they differ. `--scripts livetest/scripts` deploys both `livetest_smoke` and `livetest_broken`; `tools/teardown_list.sh` lists them for removal later, with the owner's approval.)

Expected: all pass; the two manual tests are skipped in the first run. The `grep` prints `no unexpected errors` (only the deliberate `livetest_broken` failure may log at error level; refused handshakes, bad frames and dropped connections are warnings or info). The game is still responsive afterwards. The manual run passes when a human opens a pausing menu, closes it, and loads a save as prompted.

- [ ] **Step 5: Commit**

```bash
git add livetest && git commit -m "test(live): robustness, throughput with a spinning thread, and the in-process smoke script"
```
