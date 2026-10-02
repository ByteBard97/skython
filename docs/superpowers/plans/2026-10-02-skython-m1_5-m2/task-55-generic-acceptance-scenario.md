# Task 55: Generic acceptance scenario, bridge and in-process

The v1 acceptance test, generic (owner decision 8): one `call_method` of the test quest's `SetupScenario(npc, item, place, label)` starts the quest, fills its aliases, sets and displays its objective; a persistent item is placed; killing the ESP's NPC raises `death`, the quest advances to stage 100 and its fragment's `Skython.Send("skython_test", "stage_100")` arrives as `channel`; the test reads back the stage, the objective state and the alias fills, then completes the objective and stage 200. The same scenario code runs once through the bridge and once in-process as a player script.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Contracts** (Python `skython.events`, Papyrus) and **Gates** (owner decision 8) first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 53 is done **and owner decision 8 has an answer** in `OWNER-DECISIONS-2026-10-02.md` or the owner's reply. Read it first (Step 0).
>
> **Runs on:** Linux (scenario code, tests), then the Windows box + game.
>
> **STOP and ask the owner (do not improvise) if:** decision 8 is unanswered, or rejected as framing (Step 0); `SetupScenario` in `testesp/papyrus/SkythonTestQuest.psc` does not start the quest, fill an alias with `akTarget`, one with `akItem`, the location alias with `akPlace`, and display the objective (the scenario below assumes exactly that); the scenario fails through the bridge or in-process (report the failing check and its error; never weaken a check); a live test crashes the game.

## If owner decision 8 is not accepted as proposed

- **Accepted as proposed** (generic scenario, `SetupScenario`, channel `skython_test`): this file as written.
- **Accepted with other names:** Task 48 already used the owner's names in the `.psc` sources and fragments (its own gate). Here only the two constants change, in `livetest/scripts/_testesp.py` (Task 50): `SCENARIO_FUNCTION` and `CHANNEL`. Every check below reads those constants; nothing else changes. Task 57 then writes the owner's names into the spec.
- **Framing rejected** (the owner wants a consumer-specific acceptance test, or a different scenario): STOP before Step 1. This task is re-planned with the owner; nothing below is built, and Task 57 leaves spec sections 8 and 9 and open question 5 as they are.

**Files:**
- Create: `livetest/scripts/livetest_scenario/scenario.py` (the scenario: imported by the bridge test and by the in-process script)
- Create: `livetest/scripts/livetest_scenario/__init__.py` (in-process trigger: runs the scenario when a mod event asks)
- Create: `livetest/test_acceptance_scenario.py`
- Modify: `spikes/RESULTS.md` (Task 55 entry)

**Interfaces:**
- Consumes:
  - Task 48: `SkythonTestQuest.SetupScenario(Actor akTarget, ObjectReference akItem, Location akPlace, String asLabel)` (starts the quest, fills one reference alias with `akTarget`, one with `akItem`, the location alias with `akPlace`, sets and displays the objective); fragments send `stage_<n>` on `CHANNEL`; aliases optional, the NPC killable (not essential). Whether the ESP itself sets stage 100 when the target dies is not fixed: the scenario accepts either (Step 1, `advance_to_100`).
  - Task 50: `_testesp.discover`, `location`, `stage_payload`, `PLAYER`, `IRON_SWORD`, `QUEST_SCRIPT`, `SCENARIO_FUNCTION`, `CHANNEL`.
  - Task 36: `Game.on_death`, `on_channel`, `on_quest_stage` → `Subscription.close()`; `Event.data` with `Form` values. Task 39: a `death` event's `killer` is filled for `Actor.Kill(akKiller)` (`death_event` checks it). Task 41: `support.load_save(game, name)`; Task 49: `fresh_save` (loads `skythonbase`).
  - v1: `smoke.py_dir`, `support.box_pull`, `support.fresh`, `support.BOX`, `support.python_log_text`.
- Produces: `scenario.run_scenario(game, label: str = "acceptance") -> dict` (`{"status": "pass"|"fail", "checks": [{"name", "ok", "error"?}], "values": {...}}`, never raises), `scenario.STEPS` (the check names in order); `livetest_scenario.TRIGGER = "skython_scenario_run"`, `livetest_scenario.FILE = "livetest_scenario.json"` (`{"status": "listening"|"running"|"done", "run_id", "result"}`); Task 56 and Task 57 run `test_acceptance_scenario.py`.

- [ ] **Step 0: Read owner decision 8**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
sed -n '/^\*\*8\. Acceptance test framing/,/^## /p' OWNER-DECISIONS-2026-10-02.md
grep -n -i "decision 8\|^8\b\|8 accept\|8 reject" OWNER-DECISIONS-2026-10-02.md spikes/RESULTS.md .superpowers/sdd/README/progress.md 2>/dev/null
grep -n "SetupScenario\|skython_test" testesp/papyrus/SkythonTestQuest.psc testesp/papyrus/QF_SkythonTestQuest_01000800.psc
```
Expected: a recorded answer. Apply the section above: as proposed, other names (edit the two constants, then go on), or STOP. Also read `SetupScenario`'s body and check it does what **Interfaces** says (else STOP).

- [ ] **Step 1: The scenario**

Create `livetest/scripts/livetest_scenario/scenario.py`:
```python
"""The generic acceptance scenario (plan Task 55): one Papyrus call sets a quest up; the game reports back through
events. Run through the bridge by livetest/test_acceptance_scenario.py and in-process by livetest_scenario (same
code). Development only. Needs skythonbase loaded (the player in SkythonTestCell, the target alive)."""
import threading
import time

import _testesp as t

STEPS = ("discover", "place_item", "subscribe", "setup_scenario", "state_after_setup", "kill_target", "death_event",
         "advance_to_100", "channel_stage_100", "quest_stage_event_100", "state_after_death", "complete",
         "channel_stage_200", "objective_completed")


class _Events:
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
                    raise AssertionError(f"{why} within {timeout} s (got {self.events!r})")
                self._cv.wait(left)
            return got


def _check(cond, why: str) -> None:
    if not cond:
        raise AssertionError(why)


def _payload(events, want: str):
    return next((e for e in events if e.data["payload"].lower() == want), None)


def run_scenario(game, label: str = "acceptance") -> dict:
    checks, values, subs = [], {}, []
    deaths, channel, stages = _Events(), _Events(), _Events()

    def step(name, fn):
        try:
            out = fn()
        except Exception as e:  # recorded; the remaining steps are meaningless after a failure
            checks.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"})
            raise
        checks.append({"name": name, "ok": True})
        return out

    def ref_fills(esp):
        refs = [game.call_method(a, "ReferenceAlias", "GetReference") for a in esp.ref_aliases]
        return {r.form_id for r in refs if r is not None}

    try:
        esp, place = step("discover", lambda: (t.discover(game), t.location(game)))
        item = step("place_item", lambda: game.call_method(t.PLAYER, "ObjectReference", "PlaceAtMe",
                                                           game.form(t.IRON_SWORD), 1, True, False))
        values.update(npc=esp.npc.form_key, item=item.form_id, place=place.form_key, objective=esp.objective)
        step("subscribe", lambda: subs.extend([game.on_death(esp.npc, deaths), game.on_channel(t.CHANNEL, channel),
                                               game.on_quest_stage(esp.quest, stages)]))
        step("setup_scenario", lambda: game.call_method(esp.quest, t.QUEST_SCRIPT, t.SCENARIO_FUNCTION,
                                                        esp.npc, item, place, label, timeout=30))

        def state_after_setup():
            _check(game.call_method(esp.quest, "Quest", "IsRunning") is True, "the quest is not running")
            fills = ref_fills(esp)
            _check(esp.npc.form_id in fills and item.form_id in fills, f"target/item not in the alias fills {fills}")
            loc = game.call_method(esp.location_alias, "LocationAlias", "GetLocation")
            _check(loc is not None and loc.form_id == place.form_id, f"location alias holds {loc!r}")
            _check(game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True,
                   "the objective is not displayed")
            values["stage_after_setup"] = game.call_method(esp.quest, "Quest", "GetCurrentStageID")
        step("state_after_setup", state_after_setup)
        step("kill_target", lambda: game.call_method(esp.npc, "Actor", "Kill", t.PLAYER))

        def death_event():
            e = deaths.wait(lambda evs: next((x for x in evs if x.name == "death"), None), 20, "no death event")
            _check(e.data["victim"].form_id == esp.npc.form_id, f"victim {e.data['victim']!r}")
            _check(e.data["killer"] is not None and e.data["killer"].form_id == t.PLAYER.form_id, "killer not player")
            values["death_epoch"] = e.epoch
        step("death_event", death_event)

        def advance_to_100():
            # Python reacts to the death by advancing the quest, unless the ESP's own scripts already did.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if game.call_method(esp.quest, "Quest", "IsStageDone", 100):
                    return "esp"
                time.sleep(0.25)
            game.call_method(esp.quest, "Quest", "SetStage", 100)
            return "python"
        values["stage_100_set_by"] = step("advance_to_100", advance_to_100)
        step("channel_stage_100", lambda: channel.wait(lambda evs: _payload(evs, t.stage_payload(100)), 20,
                                                       f"no {t.stage_payload(100)} on {t.CHANNEL}"))
        step("quest_stage_event_100", lambda: stages.wait(
            lambda evs: next((e for e in evs if e.data["stage"] == 100), None), 20, "no quest_stage 100"))

        def state_after_death():
            _check(game.call_method(esp.quest, "Quest", "GetCurrentStageID") == 100, "stage is not 100")
            _check(game.call_method(esp.npc, "Actor", "IsDead") is True, "the target is not dead")
            _check(game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True,
                   "the objective is no longer displayed")
            _check(esp.npc.form_id in ref_fills(esp), "the dead target left its alias")
        step("state_after_death", state_after_death)
        step("complete", lambda: (game.call_method(esp.quest, "Quest", "SetObjectiveCompleted", esp.objective, True),
                                  game.call_method(esp.quest, "Quest", "SetStage", 200)))
        step("channel_stage_200", lambda: channel.wait(lambda evs: _payload(evs, t.stage_payload(200)), 20,
                                                       f"no {t.stage_payload(200)} on {t.CHANNEL}"))
        step("objective_completed", lambda: _check(
            game.call_method(esp.quest, "Quest", "IsObjectiveCompleted", esp.objective) is True,
            "the objective is not completed"))
    except Exception:  # the failing step is already in checks
        pass
    finally:
        for s in subs:
            s.close()
    ok = [c["name"] for c in checks if c["ok"]]
    return {"status": "pass" if tuple(ok) == STEPS else "fail", "checks": checks, "values": values}
```

Create `livetest/scripts/livetest_scenario/__init__.py`:
```python
"""In-process run of the acceptance scenario (Task 55). Deployed with `tools/box/box_deploy.sh --scripts
livetest/scripts`. Development only: never ship it to players.

setup() subscribes to the mod event TRIGGER. When the live test sends it (str_arg: a run id of digits), a new thread
runs scenario.run_scenario with this script's own Game (never on the event thread: the scenario waits for events
that thread delivers) and writes livetest_scenario.json next to python.log.
"""
import json
import logging
import os
import pathlib
import threading

TRIGGER = "skython_scenario_run"
FILE = "livetest_scenario.json"
log = logging.getLogger("livetest_scenario")
_game = None


def _write(obj) -> None:
    import _skython  # only in the game: this package stays importable on Linux for its constants
    path = pathlib.Path(json.loads(_skython.config())["log_dir"]) / FILE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str))
    os.replace(tmp, path)


def _run(run_id: str) -> None:
    from .scenario import run_scenario
    _write({"status": "running", "run_id": run_id})
    try:
        result = run_scenario(_game, label=f"inprocess_{run_id}")
    except Exception as e:  # run_scenario never raises; this guards the thread anyway
        log.exception("livetest_scenario failed")
        result = {"status": "fail", "checks": [{"name": "run", "ok": False, "error": repr(e)}], "values": {}}
    _write({"status": "done", "run_id": run_id, "result": result})


def _on_trigger(event) -> None:
    threading.Thread(target=_run, args=(event.data["str_arg"],), name="livetest_scenario", daemon=True).start()


def setup(game):
    global _game
    _game = game
    game.on_mod_event(TRIGGER, _on_trigger)   # events.subscribe needs no loaded game
    _write({"status": "listening", "run_id": None})
```

Check both import on Linux:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python python -c "import sys; sys.path.insert(0, 'livetest/scripts'); import livetest_scenario as s; from livetest_scenario.scenario import STEPS; print(s.TRIGGER, len(STEPS))"
```
Expected: `skython_scenario_run 14`.

- [ ] **Step 2: The live test**

Create `livetest/test_acceptance_scenario.py`:
```python
"""The generic acceptance scenario (Task 55), through the bridge and in-process. Starts from skythonbase (fresh_save);
the in-process run reloads it first (the bridge run killed the target). Never saves. Run with -s."""
import json
import time

import pytest

import _testesp as t
from livetest_scenario import FILE, TRIGGER
from livetest_scenario.scenario import run_scenario
from smoke import py_dir
from support import BOX, box_pull, fresh, load_save, python_log_text

pytestmark = pytest.mark.usefixtures("fresh_save")


def scenario_file() -> dict | None:
    if BOX:
        box_pull(f"Skython/{FILE}")
    p = py_dir() / FILE
    try:
        return json.loads(p.read_text()) if fresh(p) else None
    except (OSError, ValueError):
        return None


def assert_pass(result: dict) -> None:
    print(json.dumps(result, indent=2, default=str))
    failed = [c for c in result["checks"] if not c["ok"]]
    assert result["status"] == "pass" and not failed, failed


def test_scenario_through_the_bridge(game):
    game.wait_ready(timeout=180)
    assert_pass(run_scenario(game, label="bridge"))


def test_scenario_in_process(game):
    load_save(game, "skythonbase")                       # the target is alive again
    game.wait_ready(timeout=180)
    d = scenario_file()
    assert d is not None and d["status"] in ("listening", "done"), f"livetest_scenario is not listening: {d!r}"
    run_id = str(time.time_ns())                         # digits only: the engine's string pool cannot change its case
    game.call_method(t.quest(game), "Form", "SendModEvent", TRIGGER, run_id, 0.0)
    deadline = time.monotonic() + 180
    while not ((d := scenario_file()) and d.get("run_id") == run_id and d["status"] == "done"):
        assert time.monotonic() < deadline, f"no in-process result for run {run_id}: {d!r} (see python.log)"
        time.sleep(1.0)
    assert_pass(d["result"])
    assert "livetest_scenario failed" not in python_log_text()
```

Collect without a game:
```bash
uv run --project python pytest livetest/test_acceptance_scenario.py -q -rs 2>&1 | tail -3
```
Expected: `2 skipped`.

- [ ] **Step 3: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_deploy.sh --scripts livetest/scripts          # adds livetest_scenario/
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_acceptance_scenario.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "livetest_scenario" out/box-skse/Skython/python.log | tail -5
tools/box/box_stop.sh
tools/box/box_plugins.sh show
```
Expected: `2 passed`; each run prints 14 checks, all `"ok": true`, and `values` with the NPC's form key, the item id, the place key, `stage_after_setup`, `death_epoch` and `stage_100_set_by` (`esp` or `python`); `python.log` shows `livetest_scenario: ok` at load and no traceback; `show` prints the backup. No save was written.

- [ ] **Step 4: Record in RESULTS and commit**

Append to "Plugin findings" in `spikes/RESULTS.md` (filled, no bracket left):
```markdown
- **Task 55, generic acceptance scenario (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase twice, never saved; 1 launch, SKSE plugins: <list>; owner decision 8: <answer>):** `test_acceptance_scenario.py` 2 passed: through the bridge and in-process (player script triggered by a mod event), all 14 checks each. One `call_method` of `SkythonTestQuest.<SCENARIO_FUNCTION>` started the quest, filled the target, item and location aliases and displayed objective <n>; killing the target gave `death` (killer the player, epoch <e>); stage 100 set by <esp/python>; `<CHANNEL>` delivered `stage_100` and `stage_200`; stage, objective state (displayed, then completed) and alias fills read back.
```
```bash
grep -n "Task 55, generic acceptance scenario" spikes/RESULTS.md && ! grep -n "Task 55, generic acceptance scenario.*<" spikes/RESULTS.md && echo "entry filled"
git add livetest/scripts/livetest_scenario/ livetest/test_acceptance_scenario.py spikes/RESULTS.md
git commit -m "test: generic acceptance scenario through the bridge and in-process"
```
Expected: the line number, `entry filled`, then the commit. (If `<SCENARIO_FUNCTION>`/`<CHANNEL>` were filled with the owner's names, write those names.)
