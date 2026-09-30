# Task 19: Robustness and throughput baseline

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `livetest/test_m1_robustness.py`, `livetest/test_m1_throughput.py`, `livetest/results/.gitkeep`

**Interfaces:**
- Consumes: the live plugin from Task 18 and the Python client.
- Produces: `livetest/results/throughput.json` with keys `date`, `game_version`, `sequential_calls`, `sequential_seconds`, `sequential_per_s`, `p50_ms`, `p99_ms`, `pipelined_calls`, `pipelined_threads`, `pipelined_seconds`, `pipelined_per_s`.

- [ ] **Step 1: Write the robustness tests**

`livetest/test_m1_robustness.py`:
```python
import time

import pytest

import skyrim
from support import raw_connect
from skyrim import _framing, errors
from skyrim.values import Fingerprint, Form

PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_int_overflow_is_rejected_by_the_server(session):
    s = raw_connect(session)
    s.sendall(_framing.encode({"type": "request", "id": 1, "op": "papyrus.call_static",
                               "args": {"script": "Utility", "function": "RandomInt", "args": [1, 2147483648]}}))
    r = _framing.read_frame(s)
    assert r["ok"] is False and r["error"]["code"] == "type_mismatch"


def test_runtime_reference_fingerprint(game):
    sword = game.form(IRON_SWORD)
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", sword, 1, False, True)
    try:
        assert ref.form_id >> 24 == 0xFF and ref.form_key is None
        assert ref.fingerprint == Fingerprint(IRON_SWORD, "ObjectReference")
        assert game.call_method(ref, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
        forged = Form(form_id=ref.form_id, fingerprint=Fingerprint("Skyrim.esm|0x000F", "ObjectReference"))
        with pytest.raises(errors.StaleReference):
            game.call_method(forged, "ObjectReference", "GetBaseObject")
    finally:
        game.call_method(ref, "ObjectReference", "Delete")


def test_disconnect_with_calls_in_flight(session, game):
    s = raw_connect(session)
    for i in range(1, 51):
        s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                   "args": {"script": "Utility", "function": "Wait", "args": [2.0]}}))
    time.sleep(0.2)  # let the server dispatch them, so they really are in flight when we close
    s.close()
    time.sleep(3)  # let the 50 waits finish in the game and their results be dropped
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6
    # The closed connection's slots were released: a new connection gets the full per-connection cap.
    s2 = raw_connect(session)
    for i in range(1, 257):
        s2.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                    "args": {"script": "Utility", "function": "Wait", "args": [0.2]}}))
    s2.settimeout(30)
    codes = [_framing.read_frame(s2)["ok"] for _ in range(256)]
    s2.close()
    assert all(codes)


def test_timeout_is_reported_and_late_result_dropped(game):
    with pytest.raises(errors.Timeout):
        game.call("Utility", "Wait", 3.0, timeout=0.5)
    time.sleep(3)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_many_short_connections(session):
    for _ in range(50):
        with skyrim.connect(port=session.port, token=session.token) as g:
            assert g.state()["interactive"] is True
```

- [ ] **Step 2: Write the throughput baseline**

`livetest/test_m1_throughput.py`:
```python
import datetime
import json
import pathlib
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

RESULTS = pathlib.Path(__file__).parent / "results" / "throughput.json"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_throughput_baseline(game):
    lat = []
    t0 = time.perf_counter()
    for _ in range(2000):
        c0 = time.perf_counter()
        game.call("Utility", "RandomInt", 1, 6)
        lat.append((time.perf_counter() - c0) * 1000)
    seq_s = time.perf_counter() - t0

    threads = 32
    t1 = time.perf_counter()
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(lambda _: game.call("Utility", "RandomInt", 1, 6), range(10000)))
    pipe_s = time.perf_counter() - t1
    assert all(1 <= r <= 6 for r in results)

    lat.sort()
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": game.welcome["game_version"],
        "sequential_calls": 2000,
        "sequential_seconds": round(seq_s, 3),
        "sequential_per_s": round(2000 / seq_s, 1),
        "p50_ms": round(statistics.median(lat), 2),
        "p99_ms": round(lat[int(len(lat) * 0.99)], 2),
        "pipelined_calls": 10000,
        "pipelined_threads": threads,
        "pipelined_seconds": round(pipe_s, 3),
        "pipelined_per_s": round(10000 / pipe_s, 1),
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
```
There is no pass threshold in v1: this records the baseline that M2 and skyrim-dm budget against. Compare `p50_ms` with the frame time (about 16.7 ms at 60 fps): a p50 of 2–3 frames is expected (one frame for the `AddTask`, one for the VM, one more if the callback is off the main thread).

- [ ] **Step 3: Run**

```bash
source tools/env.sh
uv run --project python pytest livetest/test_m1_robustness.py livetest/test_m1_throughput.py -q -s
grep -ciE "error|exception" "$SKSE_LOG_DIR/SkyrimPython.log"
```
Expected: all pass; the grep shows only the errors the tests provoked on purpose (none at `error` level from the server: refused handshakes and bad frames are `warning`). The game is still responsive afterwards.

- [ ] **Step 4: Commit**

```bash
git add livetest && git commit -m "test(live): robustness and throughput baseline for the M1 dispatcher"
```
