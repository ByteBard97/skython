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
from support import OTHER_CLIENTS_SLACK, diagnostics, raw_connect, read_frame, send_request, steady_late_count

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
    steady_late_count(session)  # nothing from earlier tests still on its way
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
    # Every burst entry has left the Dispatcher once the late counter is steady: in_flight is back at its baseline.
    steady_late_count(session)
    assert diagnostics(session)["in_flight"] <= before["in_flight"] + OTHER_CLIENTS_SLACK, "burst entries are still in flight"
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "requested": BURST_TOTAL, "per_connection": BURST_PER_CONNECTION, "connections": n_conn,
        "ok": ok, "busy": busy, "seconds": round(secs, 3),
        # max_task_drain_ms_count: the tasks in the run that set max_task_drain_ms (so per_task_drain_ms is that
        # run's own cost); max_task_drain_count: the most tasks in any one run since launch.
        "max_task_drain_ms": after["max_task_drain_ms"], "max_task_drain_ms_count": after["max_task_drain_ms_count"],
        "max_task_drain_count": after["max_task_drain_count"],
        "max_task_drain_ms_before": before["max_task_drain_ms"],
        # The maximum is since launch: only a burst that raised it is what max_task_drain_ms describes.
        "attributable": after["max_task_drain_ms"] > before["max_task_drain_ms"],
        "s9_frame_ms": S9_FRAME_MS,
        "worst_frame_ms_estimate": round(S9_FRAME_MS + after["max_task_drain_ms"], 2),
    }
    count = after["max_task_drain_ms_count"]
    per_task = after["max_task_drain_ms"] / count if count else None
    out["per_task_drain_ms"] = round(per_task, 4) if per_task is not None else None
    if per_task is not None and count < BURST_TOTAL:  # the worst run drained fewer than the cap: scale it up
        out["drain_ms_at_cap_extrapolated"] = round(per_task * BURST_TOTAL, 2)
        out["worst_frame_ms_estimate_at_cap"] = round(S9_FRAME_MS + per_task * BURST_TOTAL, 2)
        out["at_cap_note"] = "approximate: a linear extrapolation from one run's per-task cost, not a measurement"
    BURST_RESULTS.parent.mkdir(exist_ok=True)
    BURST_RESULTS.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
