# Task 53: Live event tests on the test ESP: fragment `Send`, overflow, unsubscribe, ESP NPC across a load

The M2 event path end to end on our own plugin: a quest stage fragment's `Skython.Send` reaches a remote subscriber and an in-process one; a mod event sent from Papyrus carries an ESP form as sender; a slow callback overflows its queue while the game and the other subscriptions carry on (Review Focus 2); unsubscribing mid-stream stops delivery; and the ESP's persistent NPC survives a save and reload, and its death after the reload carries the new epoch (Review Focus 5).

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves), **Contracts** (event frame, `seq`/`dropped`, Python `skython.events`, Papyrus) and **Review Focus** 2 and 5 first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Tasks 42 (save/load lifecycle), 46 (`Skython.Send` native) and 49 (`skythonbase`), and Task 50 (`_testesp.py`) are done.
>
> **Runs on:** Linux (tests, in-process script), then the Windows box + game.
>
> **STOP and ask the owner (do not improvise) if:** a stage fragment's `Send` never arrives (the Task 47 spike proved the path; a failure here is a regression to report with the logs); `Actor.Kill` on the ESP NPC produces no `death` event within 20 s and `Actor.IsDead` stays False (the NPC is essential or protected: a Task 48 change, not this task's); the save name exists already; the overflow test's `seq`/`dropped` accounting does not add up (an `EventHub` bug: report, never relax the assertion); frame drain or call latency exceeds the limits below; a live test crashes the game.

**Files:**
- Create: `livetest/scripts/livetest_events/__init__.py` (in-process event smoke script)
- Create: `livetest/test_m2_events_esp.py`
- Modify: `spikes/RESULTS.md` (Task 53 entry; the new save on the removal list)

**Interfaces:**
- Consumes:
  - Task 36 (`skython.events` / `Game`): `Event(name, sub, seq, epoch, data, dropped=0, suppressed=0)`; `Game.on(event, filter, fn, *, max_queue)`, `on_death(actor, fn)`, `on_mod_event(name, fn)`, `on_channel(channel, fn)`, `on_game_loaded(fn)` → `Subscription` (`close()`, `closed`); one dispatch thread per `Game`, callbacks in event order.
  - Task 41: `Game.save(name) -> str`, `Game.load(name, *, wait=True) -> dict` (new `epoch`).
  - Task 46: `Skython.Send(string channel, string payload)` callable as `game.call("Skython", "Send", channel, payload)`; `channel` event data `{"channel", "payload", "truncated"}`.
  - Task 39: `mod_event` data `{"name", "str_arg", "num_arg", "sender"}`; `death` data `killer` is filled for a Papyrus `Actor.Kill(akKiller)` (the brief promises the victim only: a `None` killer here is traced to Task 39's sink, not loosened in the test); Task 40: `game_loaded` data `{"new_game"}`.
  - Task 48: every stage fragment sends `stage_<n>` on `skython_test` (`_testesp.stage_payload`, `CHANNEL`).
  - Task 50: `_testesp.discover`, `ensure_running`, `PLAYER`, `CHANNEL`, `stage_payload`.
  - v1: `support.diagnostics(session)["max_task_drain_ms"]`, `support.box_pull`, `support.fresh`, `support.BOX`, `smoke.py_dir()`.
- Produces:
  - `livetest/scripts/livetest_events/__init__.py`: `MOD_EVENT = "skython_esp_live"`, `FILE = "livetest_events.json"`; at setup it subscribes (own `Game`) to `channel` (`CHANNEL`), `mod_event` (`MOD_EVENT`) and `game_loaded`, and rewrites `livetest_events.json` (next to `python.log`) after every event: `{"status": "subscribed"|"failed", "events": [{"name", "sub", "seq", "epoch", "dropped", "suppressed", "data", "t"}], "errors": [...]}`, last 500 events. Importable on Linux (it imports `_skython` only inside functions). Task 56 extends it.
  - `livetest/test_m2_events_esp.py`; one new throwaway save `skython_t53_<timestamp>`.

Limits used below (from recorded numbers, RESULTS Task 31): `DRAIN_LIMIT_MS = 4.5` (twice the measured `max_task_drain_ms` 2.2454 for a 904-task burst); a bridge call during the burst must return within 1 s (Task 31 remote p99: 100.28 ms).

- [ ] **Step 1: In-process event script**

Create `livetest/scripts/livetest_events/__init__.py`:
```python
"""In-process event smoke script (Task 53). Deployed to Data/SKSE/Plugins/Skython/scripts/ by
`tools/box/box_deploy.sh --scripts livetest/scripts`. Development only: never ship it to players.

setup() subscribes, from a thread, to the test ESP's channel, to one mod event and to game_loaded, and rewrites
livetest_events.json next to python.log after every delivered event (the last KEEP events). It only listens: the
live tests drive the game through the bridge and read the file (livetest/test_m2_events_esp.py).
"""
import json
import logging
import os
import pathlib
import threading
import time

import _testesp as t
from skython.values import to_wire

MOD_EVENT = "skython_esp_live"
FILE = "livetest_events.json"
KEEP = 500
log = logging.getLogger("livetest_events")
_lock = threading.Lock()
_state = {"status": "starting", "events": [], "errors": []}


def _dir() -> pathlib.Path:
    import _skython  # only in the game: this module stays importable on Linux for its constants
    return pathlib.Path(json.loads(_skython.config())["log_dir"])


def _write() -> None:
    with _lock:
        text = json.dumps(_state)
    path = _dir() / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _wire(data: dict) -> dict:
    try:
        return {k: to_wire(v) for k, v in data.items()}
    except Exception:  # recorded, never raised: this runs on the dispatch thread inside the game
        return {"repr": repr(data)}


def record(event) -> None:
    with _lock:
        _state["events"].append({"name": event.name, "sub": event.sub, "seq": event.seq, "epoch": event.epoch,
                                 "dropped": event.dropped, "suppressed": event.suppressed,
                                 "data": _wire(event.data), "t": time.time()})
        del _state["events"][:-KEEP]
    _write()


def _subscribe(game) -> None:
    try:
        game.on_channel(t.CHANNEL, record)
        game.on_mod_event(MOD_EVENT, record)
        game.on_game_loaded(record)
        _state["status"] = "subscribed"
    except Exception as e:  # recorded for the live test, logged with its traceback
        _state["status"] = "failed"
        _state["errors"].append(f"{type(e).__name__}: {e}")
        log.exception("livetest_events: subscribe failed")
    _write()


def setup(game):
    threading.Thread(target=_subscribe, args=(game,), name="livetest_events", daemon=True).start()
```

Check it imports on Linux:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python python -c "import sys; sys.path.insert(0, 'livetest/scripts'); import livetest_events as e; print(e.MOD_EVENT, e.FILE)"
```
Expected: `skython_esp_live livetest_events.json`.

- [ ] **Step 2: Write the live tests**

Create `livetest/test_m2_events_esp.py`:
```python
"""M2 events on SkythonTest.esp (Task 53): a stage fragment's Skython.Send reaches a remote and an in-process
subscriber, a Papyrus mod event with an ESP sender, overflow under a slow callback, unsubscribe mid-stream, and the
ESP NPC across a save and reload. Needs skythonbase (fresh_save) and livetest/scripts deployed (livetest_events).
The last test leaves one new save, skython_t53_<timestamp> (owner's removal list). Run with -s."""
import json
import subprocess
import threading
import time

import pytest

import _testesp as t
from livetest_events import FILE, MOD_EVENT
from smoke import py_dir
from support import BOX, REPO, box_pull, diagnostics, fresh

pytestmark = pytest.mark.usefixtures("fresh_save")
DRAIN_LIMIT_MS = 4.5   # 2 x Task 31's max_task_drain_ms (2.2454 ms, 904 tasks in one drain)
SAVE = f"skython_t53_{time.strftime('%Y%m%d%H%M%S')}"


class Recorder:
    """A callback that keeps every Event and lets the test wait for a condition on them."""

    def __init__(self):
        self.events, self._cv = [], threading.Condition()

    def __call__(self, event):
        with self._cv:
            self.events.append(event)
            self._cv.notify_all()

    def wait(self, pred, timeout: float, why: str):
        deadline = time.monotonic() + timeout
        with self._cv:
            while not (got := pred(list(self.events))):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{why} within {timeout} s; events: {self.events!r}")
                self._cv.wait(left)
            return got


def payloads(events) -> list:
    return [e.data["payload"].lower() for e in events]


def inproc() -> dict | None:
    if BOX:
        box_pull(f"Skython/{FILE}")
    p = py_dir() / FILE
    try:
        return json.loads(p.read_text()) if fresh(p) else None
    except (OSError, ValueError):
        return None


def wait_inproc(pred, timeout: float, why: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = inproc()
        if d and (got := pred(d["events"])):
            return got
        time.sleep(1.0)
    raise AssertionError(f"in-process livetest_events: {why} within {timeout} s; last file: {inproc()!r}")


def save_count(name: str) -> int:
    r = subprocess.run([str(REPO / "tools" / "box" / "box_saves.sh"), "count", name],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return int(r.stdout.strip())


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m2-esp")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    h = t.discover(mgame)
    t.ensure_running(mgame, h.quest)
    return h


def test_inprocess_script_is_subscribed():
    deadline = time.monotonic() + 60
    while (d := inproc()) is None or d["status"] == "starting":
        assert time.monotonic() < deadline, "livetest_events never wrote its file: is livetest/scripts deployed?"
        time.sleep(1.0)
    assert d["status"] == "subscribed" and not d["errors"], d


def test_stage_fragment_send_reaches_remote_and_inprocess(game, esp):
    rec = Recorder()
    sub = game.on_channel(t.CHANNEL, rec)
    try:
        game.call_method(esp.quest, "Quest", "SetStage", 10)
        want = t.stage_payload(10)
        ev = rec.wait(lambda evs: next((e for e in evs if e.data["payload"].lower() == want), None), 20,
                      f"no {want} on {t.CHANNEL}")
        assert ev.name == "channel" and ev.data["channel"].lower() == t.CHANNEL and ev.data["truncated"] is False
        assert ev.epoch == game.state()["epoch"] and ev.dropped == 0
        mine = wait_inproc(lambda evs: [e for e in evs if e["name"] == "channel"
                                        and e["data"]["payload"].lower() == want], 30, f"no {want}")
        assert mine[-1]["epoch"] == ev.epoch
    finally:
        sub.close()


def test_papyrus_mod_event_with_an_esp_sender(game, esp):
    rec = Recorder()
    sub = game.on_mod_event(MOD_EVENT, rec)
    try:
        game.call_method(esp.quest, "Form", "SendModEvent", MOD_EVENT, "esp", 2.5)
        ev = rec.wait(lambda evs: evs[0] if evs else None, 20, f"no mod_event {MOD_EVENT}")
        d = ev.data
        assert d["name"].lower() == MOD_EVENT and d["str_arg"].lower() == "esp" and d["num_arg"] == 2.5, d
        assert d["sender"].form_id == esp.quest.form_id and d["sender"].form_key == t.QUEST_KEY, d
        wait_inproc(lambda evs: [e for e in evs if e["name"] == "mod_event"], 30, f"no mod_event {MOD_EVENT}")
    finally:
        sub.close()


def test_slow_callback_overflows_while_the_game_and_other_subscriptions_carry_on(session, game, esp):
    slow_events = []

    def slow(event):
        slow_events.append(event)
        time.sleep(2.0)

    fast = Recorder()
    s_slow = game.on("channel", {"channel": [t.CHANNEL]}, slow, max_queue=4)
    s_fast = game.on_channel(t.CHANNEL, fast)
    burst = [f"burst_{i}" for i in range(20)]
    stages = [t.stage_payload(s) for s in (20, 100, 200)]
    drain_before = diagnostics(session)["max_task_drain_ms"]
    latencies = []

    def probe():
        for _ in range(10):
            t0 = time.perf_counter()
            game.call("Utility", "RandomInt", 1, 6)
            latencies.append(time.perf_counter() - t0)

    prober = threading.Thread(target=probe, name="latency-probe", daemon=True)
    prober.start()
    try:
        for stage in (20, 100, 200):
            game.call_method(esp.quest, "Quest", "SetStage", stage)
        b = game.batch()
        for p in burst:
            b.call("Skython", "Send", t.CHANNEL, p)
        b.run(timeout=30)
        prober.join(60)
        total = len(burst) + len(stages)
        fast.wait(lambda evs: len(evs) >= total, 120, f"the fast subscription did not get all {total}")
        quiet_since, seen = time.monotonic(), 0
        while time.monotonic() - quiet_since < 5:              # the slow queue has drained when nothing new for 5 s
            if len(slow_events) != seen:
                seen, quiet_since = len(slow_events), time.monotonic()
            time.sleep(0.5)
    finally:
        s_slow.close()
        s_fast.close()
    got = payloads(fast.events)
    print(f"\nfast: {got}\nslow: {[(e.seq, e.dropped, e.data['payload']) for e in slow_events]}")
    assert sorted(got) == sorted(burst + stages) and all(e.dropped == 0 for e in fast.events), got
    assert [p for p in got if p.startswith("burst_")] == burst          # the burst keeps its order
    prev = 0
    for e in slow_events:                                              # gaps in seq are exactly the drops
        assert e.seq - prev - 1 == e.dropped, (prev, e)
        prev = e.seq
    assert prev == total, "drop-oldest keeps the newest: the last delivered event is the last pushed"
    assert sum(e.dropped for e in slow_events) > 0 and len(slow_events) + sum(e.dropped for e in slow_events) == total
    assert len(latencies) == 10 and max(latencies) < 1.0, latencies
    assert diagnostics(session)["max_task_drain_ms"] <= max(drain_before, DRAIN_LIMIT_MS)


def test_unsubscribe_mid_stream(game, esp):
    rec, control = Recorder(), Recorder()
    sub = game.on_channel(t.CHANNEL, rec)
    ctl = game.on_channel(t.CHANNEL, control)
    try:
        for i in range(3):
            game.call("Skython", "Send", t.CHANNEL, f"u_{i}")
        rec.wait(lambda evs: len(evs) >= 3, 20, "the first three did not arrive")
        sub.close()
        assert sub.closed
        for i in range(3, 6):
            game.call("Skython", "Send", t.CHANNEL, f"u_{i}")
        control.wait(lambda evs: len(evs) >= 6, 20, "the control subscription did not get all six")
        time.sleep(2)
        assert payloads(rec.events) == ["u_0", "u_1", "u_2"]
        assert payloads(control.events) == [f"u_{i}" for i in range(6)]
    finally:
        ctl.close()


def test_esp_npc_survives_save_and_reload_and_its_death_has_the_new_epoch(game, esp):
    assert save_count(SAVE) == 0, f"a save named {SAVE}* already exists on the box: STOP, never save over it"
    deaths, loaded = Recorder(), Recorder()
    s_death = game.on_death(esp.npc, deaths)
    s_loaded = game.on_game_loaded(loaded)
    try:
        epoch0 = game.state()["epoch"]
        assert game.save(SAVE) == SAVE
        assert save_count(SAVE) == 1, f"{SAVE}.ess was not written"
        epoch1 = game.load(SAVE, wait=True)["epoch"]
        assert epoch1 > epoch0
        ev = loaded.wait(lambda evs: next((e for e in evs if e.epoch == epoch1), None), 30, "no game_loaded")
        assert ev.data["new_game"] is False
        assert game.call_method(esp.npc, "Actor", "IsDead") is False       # the plugin-defined NPC is still valid
        assert game.call_method(esp.npc, "ObjectReference", "GetBaseObject") is not None
        game.call_method(esp.npc, "Actor", "Kill", t.PLAYER)
        death = deaths.wait(lambda evs: next((e for e in evs if e.name == "death"), None), 20,
                            "no death event after Kill (is the NPC essential or protected? STOP)")
        assert death.data["victim"].form_id == esp.npc.form_id and death.epoch == epoch1, death
        assert death.data["killer"] is not None and death.data["killer"].form_id == t.PLAYER.form_id, death
        wait_inproc(lambda evs: [e for e in evs if e["name"] == "game_loaded" and e["epoch"] == epoch1], 30,
                    f"no game_loaded with epoch {epoch1}")
    finally:
        s_death.close()
        s_loaded.close()
    print(f"\nsave {SAVE}: epoch {epoch0} -> {epoch1}; death {death!r}")
```

Collect without a game:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m2_events_esp.py -q -rs 2>&1 | tail -3
```
Expected: `6 skipped` (no import error: `livetest_events` imports on Linux).

- [ ] **Step 3: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_deploy.sh --scripts livetest/scripts          # adds livetest_events/ (runs on every later launch)
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m2_events_esp.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "livetest_events|skython.events" out/box-skse/Skython/python.log | tail -20
tools/box/box_stop.sh
tools/box/box_plugins.sh show
```
Expected: `6 passed`, no skips; the overflow test prints the slow subscription's `(seq, dropped, payload)` triples (several with `dropped > 0`) and the fast list with all 23 payloads; `python.log` has no traceback from `livetest_events` or `skython.events`; `show` prints the backup with no `SkythonTest.esp` line. Note the save name printed by the last test.

- [ ] **Step 4: Record in RESULTS**

Append to "Plugin findings" in `spikes/RESULTS.md` (filled, no bracket left) and add the save to the removal list:
```markdown
- **Task 53, events on the test ESP (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase then <save>; 1 launch, SKSE plugins: <list>):** `test_m2_events_esp.py` 6 passed. Stage 10's fragment `Skython.Send` reached a remote `on_channel` and the in-process `livetest_events` script with the same epoch <n>. `SendModEvent` from the ESP quest: sender `SkythonTest.esp|0x000800`. Overflow (`max_queue` 4, 2 s callback, 23 events: 3 stage fragments + 20 `Send` in a batch): slow subscription delivered <k> events with dropped <list> (seq gaps equal drops, last seq 23); the fast subscription on the same Game got all 23 with no drops; bridge call latency max <ms> ms during the burst; `max_task_drain_ms` <before> -> <after>. Unsubscribe mid-stream: nothing after `close()`. ESP NPC across `<save>`: epoch <e0> -> <e1>, still valid, its `death` after the reload carried epoch <e1>; `game_loaded` reached the in-process script. New save: `<save>` (removal list).
```
Removal list line: `- <save>.ess (Task 53)`; and under box files: `- Data\SKSE\Plugins\Skython\scripts\livetest_events\ and scripts\_testesp.py (Tasks 50, 53; run on every launch)`.

Check:
```bash
grep -n "Task 53, events on the test ESP" spikes/RESULTS.md && ! grep -n "Task 53, events on the test ESP.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 5: Commit**

```bash
git add livetest/scripts/livetest_events/__init__.py livetest/test_m2_events_esp.py spikes/RESULTS.md
git commit -m "test(m2): live events on the test ESP: fragment Send, overflow, unsubscribe, NPC across a load"
```
