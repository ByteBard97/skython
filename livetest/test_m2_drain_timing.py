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
from support import BOX, REPO, box_pull, log_text, python_log_text, raw_connect, rpc

RESULTS = pathlib.Path(__file__).parent / "results" / "drain_timing.json"
SLEEPS_MS = (0, 2, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50)
CALLS = int(os.environ.get("SPY_DRAIN_CALLS", "100"))
IN_BASE = 3_800_000_000      # far above any other client's ids (every transport counts from 1; live_dashboard polls)
REMOTE_BASE = 3_900_000_000
PLUGIN = re.compile(r"timing: id=(\d+) submit=(\d+) task_start=(\d+) callback=(\d+) done=(\d+) frame_dt=(\d+)")
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


def bridge_log_text() -> str:
    """python.log rotates (1 MiB x 3, appended across launches), and about 1300 timing lines can cross a rotation:
    read python.log.1 too, older text first. A rotated file reads as new in full, so it may hold lines of earlier
    launches with the same ids; the caller's dict keeps the last line per id, which is this run's."""
    if not BOX:
        return python_log_text()
    box_pull("Skython/python.log.1", "Skython/python.log")
    mirror = REPO / "out" / "box-skse" / "Skython"
    return "".join((mirror / n).read_text(errors="replace") for n in ("python.log.1", "python.log") if (mirror / n).exists())


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
    # About 70 ms per call (a frame plus the pause, on average): wait that long before polling the reply over ssh.
    estimate_s = len(SLEEPS_MS) * CALLS * 0.07
    inproc = command("sweep", timeout=600, first_poll_after=estimate_s, sleeps_ms=list(SLEEPS_MS), calls=CALLS,
                     id_base=IN_BASE)["calls"]
    remote = remote_sweep(session)
    tie_back = {"remote_game_p50_ms": game_p50(game), "inprocess_game_p50_ms": command("bench", calls=100)["p50_ms"],
                "task31": TASK31}

    matches = list(PLUGIN.finditer(log_text("Skython.log")))
    plugin = {int(m[1]): tuple(int(x) for x in m.groups()[1:5]) for m in matches}  # submit, task_start, callback, done
    frame_dt = {int(m[1]): int(m[6]) for m in matches}
    bridge = {int(m[1]): (int(m[2]), int(m[3])) for m in BRIDGE.finditer(bridge_log_text())}
    ids = [r[0] for r in inproc] + [r[0] for r in remote]
    non_sweep = len(set(plugin) - set(ids))  # other clients (live_dashboard, the tie-back calls) also log lines
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
        rows = sorted(rows)  # by id: call order
        # Quantization signature: a call that just misses a drain arrives a little over one frame after the previous
        # call's task started (arrival_phase just over 1.0 at pause 0).
        phase = [(r[4] - p[5]) / 1000 / frame_ms for p, r in zip(rows, rows[1:])]
        return {"client_ms": spread([r[2] for r in rows]),
                "arrival_phase": spread(phase) if phase else None,
                "server_ms": spread([ms(r[3], r[8]) for r in rows]),
                "phases_ms": {"read_to_submit": spread([ms(r[3], r[4]) for r in rows]),
                              "queued": spread([ms(r[4], r[5]) for r in rows]),
                              "vm": spread([ms(r[5], r[6]) for r in rows]),
                              "callback_to_done": spread([ms(r[6], r[7]) for r in rows]),
                              "done_to_write": spread([ms(r[7], r[8]) for r in rows]),
                              "tunnel_and_client": spread([r[2] - ms(r[3], r[8]) for r in rows])}}

    starts = [r[4] for r in in_rows if r[1] == 0]  # in-process, no pause: about one call per frame
    frame_from_starts = round(statistics.median(ms(a, b) for a, b in zip(starts, starts[1:])), 2)
    frame_ms = round(statistics.median(frame_dt[i] for i in ids) / 1000, 2)  # the engine's own frame time
    in_steps = [{"sleep_ms": s, **in_step([r for r in in_rows if r[1] == s])} for s in SLEEPS_MS]
    re_steps = [{"sleep_ms": s, **re_step([r for r in re_rows if r[1] == s])} for s in SLEEPS_MS]
    welcome = game.welcome
    out = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": welcome.get("game_version", "unknown"), "runtime": welcome.get("runtime", "unknown"),
        "calls_per_step": CALLS, "sleeps_ms": list(SLEEPS_MS), "frame_ms_estimate": frame_ms,
        "frame_ms_from_task_starts": frame_from_starts, "non_sweep_timing_lines": non_sweep, "tie_back": tie_back,
        "inprocess": {"steps": in_steps}, "remote": {"steps": re_steps},
        "raw": {"inprocess": in_rows, "remote": re_rows, "frame_dt_us": {str(i): frame_dt[i] for i in ids}},
        "notes": ["record only: trace_timing stamps, no behaviour change",
                  "each plugin timing line is written (and flushed: Log.cpp flush_on(info)) right after done, on the "
                  "thread that called done; that write is a small perturbation of the next call's queue time",
                  "frame_ms_estimate: median frame_dt (RE::GetSecondsSinceLastFrame at task start) over the sweep calls; "
                  "frame_ms_from_task_starts: median gap between task_start of consecutive in-process calls with no pause",
                  f"non_sweep_timing_lines: {non_sweep} plugin timing lines from other requests during the run "
                  "(tie-back calls, live_dashboard if it was connected)",
                  "the box's scripts/live_dashboard.py (Task 32) runs in-process on every launch and polls about once "
                  "a second (a seven-step batch plus one call); its Game counts ids from 1, far below IN_BASE and "
                  "REMOTE_BASE, so its lines never join with the sweep's, but its tasks share the SKSE drains"],
    }
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=1) + "\n")
    z_in, z_re = in_steps[0], re_steps[0]
    print("RESULTS: frame ~{} ms (frame_dt; task-start gaps {} ms); remote arrival_phase p50 at pause 0 {}; in-process total p50 by pause {}; remote client p50 by pause {}; at pause 0: queued "
          "p50 {} ms in-process vs {} ms remote, vm p50 {} vs {}, remote read_to_submit {} / done_to_write {} / tunnel "
          "and client {}; Game-based tie-back remote {} ms, in-process {} ms (Task 31: 92.39 / 46.11)".format(
              frame_ms, frame_from_starts, z_re["arrival_phase"] and z_re["arrival_phase"]["p50"],
              " ".join(f"{s['sleep_ms']}:{s['total_ms']['p50']}" for s in in_steps),
              " ".join(f"{s['sleep_ms']}:{s['client_ms']['p50']}" for s in re_steps),
              z_in["phases_ms"]["queued"]["p50"], z_re["phases_ms"]["queued"]["p50"],
              z_in["phases_ms"]["vm"]["p50"], z_re["phases_ms"]["vm"]["p50"],
              z_re["phases_ms"]["read_to_submit"]["p50"], z_re["phases_ms"]["done_to_write"]["p50"],
              z_re["phases_ms"]["tunnel_and_client"]["p50"],
              tie_back["remote_game_p50_ms"], tie_back["inprocess_game_p50_ms"]))
