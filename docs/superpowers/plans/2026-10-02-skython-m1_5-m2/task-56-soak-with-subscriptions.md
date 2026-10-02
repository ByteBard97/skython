# Task 56: 30-minute soak with subscriptions

Spec 8's M2 soak: 30 minutes on `skythonbase` with a subscription to every v1 event (filters where the table requires one), one bridge subscriber and one in-process subscriber, a call per second, `Skython.Send` and quest stage changes, item moves, and one save to a new throwaway save plus one load in the middle. Every 30 s the run records the bridge's `in_flight`, the event hub's consumers, subscriptions and dropped/suppressed totals, the drain meter, and the game process's memory (Python runs inside it). Pass = no crash, callbacks still delivering at the end, no memory growth trend beyond the queue bounds, subscriptions back at their count, drain and latency within recorded limits (Review Focus 1).

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves; no credentials), **Contracts** (events table, C++ `EventHub`, Plugin: the four `game.state` diagnostics keys Task 39 owns, Live tests: `_testesp`) and **Review Focus** 1 and 5 first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Tasks 50 (`_testesp`) and 53 (`livetest_events`) are done (Task 55 need not be: this task uses only the deployed build and the ESP, not the acceptance scenario, so a re-planned Task 55 after a decision-8 rejection does not block it); Gate 0 (owner decision 7) accepted for the box steps (index **Owner decisions**).
>
> **Runs on:** Linux (tests), then the Windows box + game for about 35 minutes. No plugin code changes in this task.
>
> **STOP and ask the owner (do not improvise) if:** the game crashes or hangs during the soak (collect the crash log with `box_files.sh pull` and `box_shot.sh` first); a pass criterion fails (report the samples; never widen a limit to make it pass); the save name exists; a diagnostics key of Step 1 is missing (a Task 39 defect: report it, do not add it here); the game process cannot be found by `Get-Process SkyrimSE` while the bridge answers.

Limits (recorded numbers, RESULTS Task 31; not tuned here): `DRAIN_LIMIT_MS = 4.5` (2 × `max_task_drain_ms` 2.2454 for a 904-task drain); `P99_LIMIT_MS = 200.0` (2 × the remote p99 100.28 ms) for calls outside the load window; memory: working-set slope after the load ≤ `SLOPE_LIMIT_MIB_PER_MIN = 1.0`. Queues plateau, they do not climb: 40 subscriptions (20 events, from the bridge and in-process) × 1024 queued events × a few hundred bytes is about 10-20 MiB at most, reached early (and each consumer is capped at 16 MiB of queued frames, `max_queued_event_bytes_per_consumer`), so a steady climb after the load is a leak, not a queue. The slope limit is kept only if it is above the noise: the 3-minute trial records the working-set spread (`working_set_spread_mib`, max − min over its samples), and the limit is meaningful only while 15 minutes × 1.0 MiB/min = 15 MiB exceeds that spread (Step 4 checks it; never widen the limit). The test process's own peak RSS (`client_rss`, the Linux side, where the `RemoteEventStream` queues live) is recorded per sample too.

**Files:**
- Modify: `livetest/scripts/livetest_events/__init__.py` (soak mode, triggered by a mod event)
- Create: `livetest/test_m2_soak.py`
- Create: `livetest/results/soak.json` (written by the run, committed)
- Modify: `livetest/results/new_saves.txt` (appended by `new_save_name`, committed)
- Modify: `spikes/RESULTS.md` (Task 56 entry; the new save on the removal list)

**Interfaces:**
- Consumes: Task 41 `support.new_save_name(tag) -> str`, `support.wait_save_file(name, timeout=30.0)`; Task 39 `game.state` diagnostics keys `event_consumers`, `event_subscriptions`, `events_dropped_total`, `events_suppressed_total` (Task 39 owns them in `GameBackend.cpp`: `Hub().Consumers()`, `Subscriptions()`, `DroppedTotal()`, `SuppressedTotal()`; this task only reads them); Task 33 generated `skython._protocol_generated.EVENTS` (name → `{fields, filter_fields, filter_required_any_of, deliver_while_loading}`); Task 36 `Game.on(event, filter, fn, *, max_queue)`, `Event.dropped`, `Event.suppressed`; Task 41 `Game.save`, `Game.load(wait=True)`; Task 46 `game.call("Skython", "Send", channel, payload)`; Task 50 `_testesp` (`quest`, `PLAYER`, `IRON_SWORD`, `CHANNEL`); Task 53 `livetest_events` (`record`, `_write`, `_state`, `_lock`, `MOD_EVENT`, `FILE`); v1 `support.diagnostics`, `OTHER_CLIENTS_SLACK`, `smoke._read`, `tools/box/box_env.sh` (`bssh`).
- Produces: `livetest_events.SOAK_EVENT = "skython_soak"` (str_arg `start` / `stop`) and `livetest_events.json` key `"soak"` (`{"active", "subs", "counts", "dropped", "suppressed", "last_t"}`, rewritten every 10 s); `livetest/results/soak.json`; one new throwaway save per run from `new_save_name("t56")` (`skython_t56_<timestamp>`, recorded in `livetest/results/new_saves.txt`).

- [ ] **Step 1: Check the event-hub diagnostics keys (Task 39 owns them)**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -n '"event_consumers"\|"event_subscriptions"\|"events_dropped_total"\|"events_suppressed_total"' plugin/src/GameBackend.cpp
```
Expected: all four keys in `WithDiagnostics` (Task 39). A missing key is a Task 39 defect: STOP and report it; this task adds no plugin code. The deployed build is the latest one Tasks 53-55 deployed (`git log` shows no plugin or package change since).

- [ ] **Step 2: Soak mode in the in-process script**

Append to `livetest/scripts/livetest_events/__init__.py` (Task 53's file), and replace its `setup`:
```python
SOAK_EVENT = "skython_soak"
_game = None
_soak = {"active": False, "subs": 0, "counts": {}, "dropped": 0, "suppressed": 0, "last_t": None}
_soak_subs = []


def soak_filter(spec: dict):
    """A filter on the player for events that require one (hit, container_changed); None (all) otherwise."""
    req = spec.get("filter_required_any_of") or []
    return {req[0]: [t.PLAYER]} if req else None


def _soak_record(event) -> None:
    with _lock:
        _soak["counts"][event.name] = _soak["counts"].get(event.name, 0) + 1
        _soak["dropped"] += event.dropped
        _soak["suppressed"] += event.suppressed
        _soak["last_t"] = time.time()


def _soak_writer() -> None:
    while _soak["active"]:
        with _lock:
            _state["soak"] = json.loads(json.dumps(_soak))
        _write()
        time.sleep(10)


def _soak_start() -> None:
    from skython._protocol_generated import EVENTS
    try:
        for name, spec in EVENTS.items():
            _soak_subs.append(_game.on(name, soak_filter(spec), _soak_record))
        _soak.update(active=True, subs=len(_soak_subs))
        threading.Thread(target=_soak_writer, name="livetest_events-soak", daemon=True).start()
    except Exception as e:  # recorded for the live test
        _state["errors"].append(f"soak start: {type(e).__name__}: {e}")
        log.exception("livetest_events: soak start failed")
        _write()


def _soak_stop() -> None:
    for s in _soak_subs:
        s.close()
    _soak_subs.clear()
    _soak.update(active=False, subs=0)
    with _lock:
        _state["soak"] = json.loads(json.dumps(_soak))
    _write()


def _on_soak(event) -> None:
    cmd = event.data["str_arg"].lower()
    target = _soak_start if cmd == "start" and not _soak["active"] else _soak_stop if cmd == "stop" else None
    if target:
        threading.Thread(target=target, name="livetest_events-soak-cmd", daemon=True).start()


def setup(game):
    global _game
    _game = game
    game.on_mod_event(SOAK_EVENT, _on_soak)
    threading.Thread(target=_subscribe, args=(game,), name="livetest_events", daemon=True).start()
```
Check on Linux:
```bash
uv run --project python python -c "
import sys; sys.path.insert(0, 'livetest/scripts'); import livetest_events as e
from skython._protocol_generated import EVENTS
print(e.SOAK_EVENT, {n: e.soak_filter(s) for n, s in EVENTS.items() if e.soak_filter(s)})"
```
Expected: `skython_soak` and a filter on the player for exactly `hit` (`target`) and `container_changed` (`old_container`).

- [ ] **Step 3: The soak test**

Create `livetest/test_m2_soak.py`:
```python
"""30-minute soak with subscriptions (Task 56). Opt-in: SPY_SOAK=1 (SPY_SOAK_MINUTES to shorten a trial run).
Needs skythonbase (fresh_save) and livetest/scripts deployed (livetest_events soak mode). Leaves one new save,
skython_t56_<timestamp> (owner's removal list), and writes livetest/results/soak.json. Run with -s."""
import collections
import json
import os
import resource
import subprocess
import threading
import time

import pytest

import _testesp as t
from livetest_events import FILE, SOAK_EVENT, soak_filter
from skython._protocol_generated import EVENTS
from smoke import _read as read_box_json   # box_pull + json; None when absent
from support import OTHER_CLIENTS_SLACK, REPO, diagnostics, new_save_name, wait_save_file

pytestmark = pytest.mark.usefixtures("fresh_save")
SOAK = os.environ.get("SPY_SOAK") == "1"
MINUTES = float(os.environ.get("SPY_SOAK_MINUTES", "30"))
SAMPLE_S, SEND_S, ITEM_S, STAGE_S = 30, 5, 10, 120
DRAIN_LIMIT_MS, P99_LIMIT_MS, SLOPE_LIMIT_MIB_PER_MIN = 4.5, 200.0, 1.0
HUB_KEYS = ("event_consumers", "event_subscriptions", "events_dropped_total", "events_suppressed_total")
PS_MEM = ("Get-Process -Name SkyrimSE | Select-Object -First 1 | "
          "ForEach-Object { '{0} {1}' -f $_.WorkingSet64, $_.PrivateMemorySize64 }")


def box_memory() -> dict:
    r = subprocess.run(["bash", "-c", 'source "$0" && bssh "$1"', str(REPO / "tools" / "box" / "box_env.sh"), PS_MEM],
                       capture_output=True, text=True, timeout=90)
    ws, private = (int(x) for x in r.stdout.split())
    return {"working_set": ws, "private": private}


def client_rss() -> int:
    """Peak RSS of this test process (Linux: KiB), where the bridge client's per-subscription queues live."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def slope_per_min(points) -> float:
    """Least-squares slope of (seconds, bytes) points, in MiB per minute."""
    n = len(points)
    mx, my = sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return (sum((p[0] - mx) * (p[1] - my) for p in points) / den) * 60 / 2**20 if den else 0.0


def p99(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * 0.99))] if xs else 0.0


def test_diagnostics_report_event_hub_counters(session):
    d = diagnostics(session)
    assert all(k in d for k in HUB_KEYS), d


@pytest.mark.skipif(not SOAK, reason="soak (opt-in, 30 min): run with SPY_SOAK=1")
def test_soak(session, game):
    game.wait_ready(timeout=180)
    save = new_save_name("t56")                   # checks count 0 and records the name for the removal list
    quest, sword = t.quest(game), game.form(t.IRON_SWORD)
    counts, drops, lock = collections.Counter(), collections.Counter(), threading.Lock()

    def on_event(e):
        with lock:
            counts[e.name] += 1
            drops["dropped"] += e.dropped
            drops["suppressed"] += e.suppressed
            if e.name == "channel":
                counts["channel:" + e.data["payload"].lower()] += 1

    pre = diagnostics(session)                    # before any soak subscription: the count to come back to
    subs = [game.on(name, soak_filter(spec), on_event) for name, spec in EVENTS.items()]
    game.call_method(quest, "Form", "SendModEvent", SOAK_EVENT, "start", 0.0)
    time.sleep(5)
    base = diagnostics(session)
    latencies, errors, sent = [], [], [0]
    load_window = [None, None]
    stop = threading.Event()
    t0 = time.monotonic()

    def workload():
        n = 0
        while not stop.is_set():
            n += 1
            try:
                c0 = time.perf_counter()
                game.call("Utility", "RandomInt", 1, 6)
                latencies.append((time.monotonic() - t0, (time.perf_counter() - c0) * 1000))
                if n % SEND_S == 0:
                    game.call("Skython", "Send", t.CHANNEL, f"soak_{n}")
                    sent[0] += 1
                if n % ITEM_S == 0:
                    game.call_method(t.PLAYER, "ObjectReference", "AddItem", sword, 1, True)
                    game.call_method(t.PLAYER, "ObjectReference", "RemoveItem", sword, 1, True, None)
                if n % STAGE_S == 0:
                    game.call_method(quest, "Quest", "Reset")
                    game.call_method(quest, "Quest", "SetStage", 10)
                    game.call_method(quest, "Quest", "SetStage", 20)
            except Exception as e:  # recorded with its time; judged against the load window below
                errors.append((time.monotonic() - t0, f"{type(e).__name__}: {e}"))
            stop.wait(1.0)

    worker = threading.Thread(target=workload, name="soak-workload", daemon=True)
    worker.start()
    samples, saved = [], False
    try:
        while (elapsed := time.monotonic() - t0) < MINUTES * 60:
            if not saved and elapsed >= MINUTES * 30:
                load_window[0] = elapsed
                assert game.save(save) == save       # the engine started the save; the .ess can appear later
                wait_save_file(save)
                game.load(save, wait=True)
                load_window[1] = time.monotonic() - t0
                saved = True
            d = diagnostics(session)
            inproc = (read_box_json(FILE) or {}).get("soak", {})
            with lock:
                remote = {"events": sum(v for k, v in counts.items() if ":" not in k), **drops}
            samples.append({"t": round(elapsed, 1), **{k: d.get(k) for k in ("in_flight", "max_task_drain_ms", *HUB_KEYS)},
                            **box_memory(), "client_rss": client_rss(), "remote": remote, "inproc": inproc})
            print(f"\n{samples[-1]}")
            time.sleep(SAMPLE_S)
    finally:
        stop.set()
        worker.join(30)
    inproc_before = ((read_box_json(FILE) or {}).get("soak", {}).get("counts") or {}).get("channel", 0)
    before = counts["channel"]
    for i in range(3):
        game.call("Skython", "Send", t.CHANNEL, f"soak_final_{i}")
    deadline = time.monotonic() + 20
    while counts["channel"] < before + 3 and time.monotonic() < deadline:
        time.sleep(0.5)
    # The in-process script rewrites its file every 10 s: poll (bounded) until its channel count shows the three Sends.
    deadline = time.monotonic() + 25
    while True:
        inproc_end = (read_box_json(FILE) or {}).get("soak", {})
        if (inproc_end.get("counts") or {}).get("channel", 0) >= inproc_before + 3 or time.monotonic() >= deadline:
            break
        time.sleep(2.0)
    game.call_method(quest, "Form", "SendModEvent", SOAK_EVENT, "stop", 0.0)
    for s in subs:
        s.close()
    # The in-process stop runs on its own thread in the game, and each diagnostics() call is its own short bridge
    # connection (its consumer closes when the bridge sees the close): wait, bounded, for both counts to settle.
    deadline = time.monotonic() + 15
    while ((end := diagnostics(session))["event_subscriptions"], end["event_consumers"]) != \
            (pre["event_subscriptions"], pre["event_consumers"]) and time.monotonic() < deadline:
        time.sleep(1.0)
    lo, hi = load_window[0], load_window[1] + 10
    outside = [ms for ts, ms in latencies if not lo <= ts <= hi]
    bad = [e for e in errors if not lo <= e[0] <= hi]
    after_load = [(s["t"], s["working_set"]) for s in samples if s["t"] > load_window[1] + 60]
    ws = [s["working_set"] for s in samples]
    drain_case = ("end within DRAIN_LIMIT_MS" if end["max_task_drain_ms"] <= DRAIN_LIMIT_MS
                  else "base already above DRAIN_LIMIT_MS; end must equal base")
    summary = {"minutes": MINUTES, "save": save, "load_window_s": [round(lo, 1), round(hi, 1)],
               "calls": len(latencies), "p99_ms_outside_load": round(p99(outside), 1),
               "errors_outside_load": bad, "errors_in_load": len(errors) - len(bad), "sent": sent[0],
               "remote_counts": {k: v for k, v in counts.items() if ":" not in k}, "remote_drops": dict(drops),
               "inproc_before_channel": inproc_before, "inproc_end": inproc_end, "base": base, "end": end,
               "drain_ms": {"base": base["max_task_drain_ms"], "end": end["max_task_drain_ms"], "case": drain_case},
               "working_set_spread_mib": round((max(ws) - min(ws)) / 2**20, 1) if ws else None,
               "client_rss_kib": {"start": samples[0]["client_rss"], "end": samples[-1]["client_rss"]} if samples else None,
               "working_set_slope_mib_per_min": round(slope_per_min(after_load), 3) if len(after_load) > 2 else None}
    out = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "summary": summary, "samples": samples}
    (REPO / "livetest" / "results" / "soak.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    assert game.state()["interactive"] is True                                   # alive, no crash
    assert counts["channel"] >= before + 3, "the bridge subscriber stopped receiving"
    assert inproc_end.get("active") and \
        (inproc_end.get("counts") or {}).get("channel", 0) >= inproc_before + 3, (inproc_before, inproc_end)
    wait_save_file(save)                          # exactly one file matches the new name
    assert saved
    assert base["event_subscriptions"] == pre["event_subscriptions"] + 2 * len(EVENTS), (pre, base)
    assert end["event_subscriptions"] == pre["event_subscriptions"], (pre, end)   # every soak subscription closed
    assert end["event_consumers"] == pre["event_consumers"] and end["in_flight"] <= OTHER_CLIENTS_SLACK
    # max_task_drain_ms is the launch's maximum, so it never falls: within the limit, or (if the base already
    # exceeded it before the soak began) not raised by the soak at all.
    assert end["max_task_drain_ms"] <= DRAIN_LIMIT_MS or end["max_task_drain_ms"] == base["max_task_drain_ms"], \
        summary["drain_ms"]
    assert summary["p99_ms_outside_load"] <= P99_LIMIT_MS and not bad, summary
    if MINUTES >= 15:                       # a full run must judge memory; a short trial has too few post-load samples
        assert summary["working_set_slope_mib_per_min"] is not None, summary
    if summary["working_set_slope_mib_per_min"] is not None and MINUTES >= 15:
        assert summary["working_set_slope_mib_per_min"] <= SLOPE_LIMIT_MIB_PER_MIN, summary
```
The `base` check is exact: one subscription per event from the bridge and one from the in-process script (both started before `base` is read). The in-process "still delivering" check compares the script's own channel count read just before the three final `Send`s with a bounded re-read after them, so a count that only rose in minute 1 cannot pass. The subscription check compares with `pre`, read before either soak subscriber set existed; after the bridge's `close()` calls and the in-process `stop` (which runs on its own thread in the game, hence the bounded wait), the hub must be back at exactly that count.

Collect without a game:
```bash
uv run --project python pytest livetest/test_m2_soak.py -q -rs 2>&1 | tail -3
```
Expected: `2 skipped`.

- [ ] **Step 4: A 3-minute trial, then the 30-minute soak**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_plugins.sh add                      # before every launch that loads skythonbase (Global Constraints)
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600   # fresh_save's game.load is refused while "starting"
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
SPY_SOAK=1 SPY_SOAK_MINUTES=3 uv run --project python pytest livetest/test_m2_soak.py -q -rs -s
```
Expected: `2 passed` (the trial makes its own new save; with under 15 minutes the memory slope is recorded, not judged: too few samples follow the load). Read the trial's spread: `python3 -c "import json; print(json.load(open('livetest/results/soak.json'))['summary']['working_set_spread_mib'])"`; it must be below 15 (MiB, the growth the slope limit allows over 15 minutes), else the limit cannot tell a leak from noise: STOP and report the trial's samples (never widen the limit). The trial overwrites `livetest/results/soak.json`; the real run below replaces it. Then, in the same launch:
```bash
SPY_SOAK=1 uv run --project python pytest livetest/test_m2_soak.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -c "Traceback" out/box-skse/Skython/python.log
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_plugins.sh show
```
Expected: `2 passed` after about 31 minutes; `livetest/results/soak.json` with about 60 samples; the traceback count equals the deliberate `livetest_broken` failure only (1); `show` prints the backup. Record both save names (trial and real; both are also in `livetest/results/new_saves.txt`).

- [ ] **Step 5: Record in RESULTS and commit**

Append to "Plugin findings" in `spikes/RESULTS.md` (filled from `soak.json`'s summary, no bracket left), and add both saves to the removal list:
```markdown
- **Task 56, 30-minute soak with subscriptions (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase then <save>; 1 launch, SKSE plugins: <list>):** `test_m2_soak.py` 2 passed, <minutes> min. Subscriptions to all <n> v1 events from the bridge and in-process (hit and container_changed filtered on the player); <calls> calls (p99 outside the load <ms> ms), <sent> `Send`s, item moves every 10 s, stage resets every 2 min, save + load at <t> s. Remote events <total> (dropped <d>, suppressed <s>), in-process <counts>; hub totals dropped <n>, suppressed <n>. Working set <start MiB> -> <end MiB>, slope after the load <x> MiB/min (trial spread <s> MiB); test-process peak RSS <start KiB> -> <end KiB>; `max_task_drain_ms` <before> -> <after> (<drain case>); subscriptions back to <n>, consumers <n>, `in_flight` <n> at the end; callbacks still delivering. Full samples: `livetest/results/soak.json`. New saves: `<trial save>`, `<save>` (removal list).
```
```bash
grep -n "Task 56, 30-minute soak" spikes/RESULTS.md && ! grep -n "Task 56, 30-minute soak.*<" spikes/RESULTS.md && echo "entry filled"
git add livetest/scripts/livetest_events/__init__.py livetest/test_m2_soak.py \
        livetest/results/soak.json livetest/results/new_saves.txt spikes/RESULTS.md
git commit -m "test(m2): 30-minute soak with subscriptions"
```
Expected: the line number, `entry filled`, then the commit.
