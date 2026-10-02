# Task 54: Modder examples: kill tracker, Papyrus → Python with `Skython.Send`, quest-stage notifier

Three event examples for mod authors, in the style of the M1 examples (one Python file each, `setup(game)`, standard library only, settings at the top): a kill tracker on `on_death`, a modder's own Papyrus script reporting to Python through `Skython.Send`, and a quest-stage notifier on `on_quest_stage`. General-purpose: no example is written for, or names, any one consumer.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (general-purpose naming), **Contracts** (Python `skython.events`, events table, Papyrus `Skython.psc`) first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 46 (`Skython.Send`) and Task 53 (events live on the ESP) are done.
>
> **Runs on:** Linux (examples, offline tests), then the Windows box + game (each example through the bridge and in-process).
>
> **STOP and ask the owner (do not improvise) if:** a Papyrus call in an example fails with a wrong argument count or an unknown function in the game (fix the example first; do not ship it unrun); `game.resolve(editor_id="SkythonTestQuest")` is `not_found` (quest editor ids are kept by the game per Task 29; if not for plugin quests, report it); a live test crashes the game.

**Files:**
- Create: `examples/kill_tracker.py`, `examples/papyrus_to_python.py`, `examples/quest_stage_notifier.py`
- Modify: `examples/README.md` (three table rows, an events paragraph, the Status paragraph)
- Create: `python/tests/test_examples.py` (offline: the handlers fed synthetic events through a stub game)
- Create: `livetest/test_m2_examples.py` (live: each example through the bridge; the deployed copies in-process). Not in the index's File Structure: it is this task's live check.
- Modify: `spikes/RESULTS.md` (Task 54 entry; the three deployed scripts on the removal list)

**Interfaces:**
- Consumes: Task 36 `Game.on_death(actor | None, fn)`, `Game.on_channel(channel, fn)`, `Game.on_quest_stage(quest, fn, *, stage=None)`, `skython.events.Event(name, sub, seq, epoch, data, dropped=0, suppressed=0)`; events table data: `death` `{victim, killer?}` (Task 39 fills `killer` for `Actor.Kill(akKiller)`; the live kill-tracker check depends on it), `channel` `{channel, payload, truncated}`, `quest_stage` `{quest, stage}`; Task 46 `Skython.Send(string channel, string payload)` and `max_channel_payload_bytes` = 65536; Task 50 `_testesp` (`quest`, `PLAYER`, `discover`); v1 `Game.resolve(editor_id=...)`, `call`, `call_method`, `wait_ready`; `support.python_log_text`.
- Produces: the three examples, each exposing a testable class (`kill_tracker.KillTracker(game)` with `on_death(event)`, `total`, `by_name`, `summary() -> str`; `papyrus_to_python.Reporter(game)` with `on_message(event)`, `counts`; `quest_stage_notifier.Notifier(game)` with `watch(editor_id) -> Form`, `on_stage(event)`, `watched`) and a module global for the live check (`_tracker`, `_reporter`, `_notifier`).

- [ ] **Step 1: Write the offline tests first**

Create `python/tests/test_examples.py`:
```python
"""The event examples in examples/ (Task 54): their handlers, fed synthetic events through a stub game."""
import importlib.util
import pathlib

from skython.events import Event
from skython.values import Form

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"
PLAYER = Form(form_id=0x14)


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubGame:
    def __init__(self, names=None):
        self.calls, self.subs, self.names = [], [], names or {}

    def call(self, script, function, *args, **kw):
        self.calls.append((script, function, *args))

    def call_method(self, obj, script, function, *args, **kw):
        return self.names.get(obj.form_id, "Thing")

    def resolve(self, *, editor_id=None, **kw):
        return Form(form_id={"MQ101": 0x3372B, "MQ102": 0x3372A}.get(editor_id, 0x800), type="Quest")

    def wait_ready(self, timeout=None):
        return {}

    def on_death(self, actor, fn):
        self.subs.append(("death", actor, fn))

    def on_channel(self, channel, fn):
        self.subs.append(("channel", channel, fn))

    def on_quest_stage(self, quest, fn, *, stage=None):
        self.subs.append(("quest_stage", quest, fn))


def ev(name, data):
    return Event(name=name, sub=1, seq=1, epoch=1, data=data)


def notes(game):
    return [c[2] for c in game.calls if c[:2] == ("Debug", "Notification")]


def test_kill_tracker_counts_only_the_players_kills():
    m = load("kill_tracker")
    g = StubGame({0x100: "Bandit"})
    tracker = m.KillTracker(g)
    tracker.on_death(ev("death", {"victim": Form(form_id=0x100), "killer": PLAYER}))
    tracker.on_death(ev("death", {"victim": Form(form_id=0x101), "killer": Form(form_id=0x200)}))
    tracker.on_death(ev("death", {"victim": Form(form_id=0x102), "killer": None}))
    assert tracker.total == 1 and dict(tracker.by_name) == {"Bandit": 1}
    assert notes(g) == ["Kill #1: Bandit"]
    assert tracker.summary() == "kill_tracker: 1 kills this session: Bandit x1"


def test_kill_tracker_setup_subscribes_to_every_death():
    m = load("kill_tracker")
    g = StubGame()
    m.setup(g)
    assert [(kind, actor) for kind, actor, _ in g.subs] == [("death", None)]


def test_papyrus_to_python_reports_each_message_kind_case_insensitively():
    m = load("papyrus_to_python")
    g = StubGame()
    r = m.Reporter(g)
    r.on_message(ev("channel", {"channel": "my_mod", "payload": "chest_opened|Lydia", "truncated": False}))
    r.on_message(ev("channel", {"channel": "MY_MOD", "payload": "Chest_Opened|Ralof", "truncated": False}))
    r.on_message(ev("channel", {"channel": "my_mod", "payload": "ping", "truncated": True}))
    assert dict(r.counts) == {"chest_opened": 2, "ping": 1}
    assert notes(g) == ["chest_opened: Lydia", "Chest_Opened: Ralof", "ping"]   # shown as sent, counted lower-case


def test_papyrus_to_python_setup_subscribes_to_its_channel():
    m = load("papyrus_to_python")
    g = StubGame()
    m.setup(g)
    assert [(kind, ch) for kind, ch, _ in g.subs] == [("channel", m.CHANNEL)]


def test_quest_stage_notifier_names_the_quest_and_stage():
    m = load("quest_stage_notifier")
    g = StubGame({0x3372B: "Unbound"})
    n = m.Notifier(g)
    quest = n.watch("MQ101")
    assert g.subs[0][:2] == ("quest_stage", quest) and n.watched == {quest.form_id: "Unbound"}
    n.on_stage(ev("quest_stage", {"quest": quest, "stage": 10}))
    assert notes(g) == ["Unbound: stage 10"]
```
Run it (fails: the examples do not exist yet):
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python/python && uv run pytest tests/test_examples.py -q 2>&1 | tail -3
```
Expected: FAIL (`FileNotFoundError` for `examples/kill_tracker.py`).

- [ ] **Step 2: Write the three examples**

Create `examples/kill_tracker.py`:
```python
"""Kill tracker: a running tally of everything the player kills.

Each time an actor dies with the player as its killer, a notification shows the session's kill count and what you
killed. A summary by name goes to python.log every few minutes and when the script stops.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/.
Needs: Skython M2 (events). Standard library only.
"""
import collections
import threading
import time

PLAYER_ID = 0x14          # the player's form id, the same in every game
SUMMARY_EVERY_S = 300


class KillTracker:
    def __init__(self, game):
        self.game = game
        self.total = 0
        self.by_name = collections.Counter()
        self._lock = threading.Lock()

    def on_death(self, event):
        """Runs on Skython's event thread for every death; game calls are allowed here."""
        killer = event.data.get("killer")
        if killer is None or killer.form_id != PLAYER_ID:
            return
        try:   # GetDisplayName (SKSE) gives the name shown in game, also for renamed actors
            name = self.game.call_method(event.data["victim"], "ObjectReference", "GetDisplayName") or "something"
        except Exception:                        # the body may already be gone (a cell unloaded)
            name = "something"
        with self._lock:
            self.total += 1
            self.by_name[name] += 1
            total = self.total
        print(f"kill_tracker: kill #{total}: {name}")
        try:
            self.game.call("Debug", "Notification", f"Kill #{total}: {name}")
        except Exception as e:                   # paused, menu open, loading: the count is kept anyway
            print(f"kill_tracker: notification skipped ({e})")

    def summary(self) -> str:
        with self._lock:
            parts = ", ".join(f"{name} x{n}" for name, n in self.by_name.most_common())
            return f"kill_tracker: {self.total} kills this session" + (f": {parts}" if parts else "")


_tracker = None


def _summaries():
    while True:
        time.sleep(SUMMARY_EVERY_S)
        print(_tracker.summary())


def setup(game):
    global _tracker
    _tracker = KillTracker(game)
    game.on_death(None, _tracker.on_death)     # None: every actor; the filter is applied inside the game
    threading.Thread(target=_summaries, daemon=True).start()


def teardown():
    if _tracker:
        print(_tracker.summary())
```

Create `examples/papyrus_to_python.py`:
```python
"""Papyrus to Python: your own Papyrus script reports to Python in one line.

Skython comes with a tiny Papyrus script, `Skython`, with one native function, `Skython.Send(channel, payload)`.
Call it from any of your scripts and every Python subscriber of that channel gets the payload at once: no polling,
no mod event to register. For example, a chest that reports who opened it (compile it with Skython.psc on the import
path and attach it to a container in your plugin):

    Scriptname MyModChestReporter extends ObjectReference

    Event OnActivate(ObjectReference akActionRef)
        Skython.Send("my_mod", "chest_opened|" + akActionRef.GetDisplayName())
    EndEvent

This script shows each message as a notification and counts messages by kind (the text before "|"). A payload is a
string of up to 64 KiB (a longer one is cut and the event says `truncated`); JSON works well for structured data.
The engine may change the case of a string it has seen before, so compare names case-insensitively.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ (Skython.pex goes in Data/Scripts/ with Skython).
Needs: Skython M2 (events). Standard library only.
"""
import collections

CHANNEL = "my_mod"        # pick a name unique to your mod; channel names match case-insensitively


class Reporter:
    def __init__(self, game):
        self.game = game
        self.counts = collections.Counter()

    def on_message(self, event):
        payload = event.data["payload"]
        kind, _, rest = payload.partition("|")
        self.counts[kind.lower()] += 1
        print(f"papyrus_to_python: {payload}" + (" (cut at 64 KiB)" if event.data["truncated"] else ""))
        try:
            self.game.call("Debug", "Notification", f"{kind}: {rest}" if rest else kind)
        except Exception as e:                   # paused, menu open, loading
            print(f"papyrus_to_python: notification skipped ({e})")


_reporter = None


def setup(game):
    global _reporter
    _reporter = Reporter(game)
    game.on_channel(CHANNEL, _reporter.on_message)
```

Create `examples/quest_stage_notifier.py`:
```python
"""Quest stage notifier: a notification whenever a quest you follow reaches a new stage.

Useful while writing a quest mod (watch your own quest's stages as you play through it) or to follow progress in
any quest. List the quests by editor id below.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and edit QUESTS.
Needs: Skython M2 (events). Standard library only.
"""
import threading

from skython.errors import NotFound, NotReady

QUESTS = ["MQ101", "MQ102", "MQ103"]   # editor ids: Unbound, Before the Storm, Bleak Falls Barrow


class Notifier:
    def __init__(self, game):
        self.game = game
        self.watched = {}                 # quest form id -> quest name

    def watch(self, editor_id: str):
        quest = self.game.resolve(editor_id=editor_id)
        self.watched[quest.form_id] = self.game.call_method(quest, "Form", "GetName") or editor_id
        self.game.on_quest_stage(quest, self.on_stage)
        return quest

    def on_stage(self, event):
        quest, stage = event.data["quest"], event.data["stage"]
        name = self.watched.get(quest.form_id, "A quest")
        print(f"quest_stage_notifier: {name} reached stage {stage}")
        try:
            self.game.call("Debug", "Notification", f"{name}: stage {stage}")
        except Exception as e:            # paused, menu open, loading
            print(f"quest_stage_notifier: notification skipped ({e})")


_notifier = None


def run(game):
    global _notifier
    while True:                           # quests can be looked up only once a save is loaded
        try:
            game.wait_ready()
            break
        except NotReady:
            continue
    _notifier = Notifier(game)
    for editor_id in QUESTS:
        try:
            _notifier.watch(editor_id)
        except NotFound:
            print(f"quest_stage_notifier: no quest with editor id {editor_id!r}")
    print(f"quest_stage_notifier: watching {', '.join(_notifier.watched.values()) or 'nothing'}")


def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()
```

Run the offline tests (3.12 and 3.14):
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python/python
uv run --python 3.12 pytest tests/test_examples.py -q && uv run --python 3.14 pytest tests/test_examples.py -q
grep -rni 'skyrim-dm\|skyrim_dm' ../examples/ || echo "generic"
```
Expected: `5 passed` twice, then `generic`.

- [ ] **Step 3: Update `examples/README.md`**

Add three rows at the end of the table (line counts from `wc -l examples/<file>.py`, rounded to the nearest 10):
```markdown
| [`kill_tracker.py`](kill_tracker.py) | A running tally of everything you kill, as notifications, with a summary by name in `python.log`. Uses the `death` event. | ~60 |
| [`papyrus_to_python.py`](papyrus_to_python.py) | Your own Papyrus script reports to Python with one line, `Skython.Send("my_mod", ...)`; the docstring has the `.psc` side. | ~50 |
| [`quest_stage_notifier.py`](quest_stage_notifier.py) | A notification whenever a quest you follow reaches a new stage; handy while writing a quest mod. | ~60 |
```
After the "The API in five lines" section, add:
````markdown
## Events in four lines

```python
game.on_death(None, lambda e: print(e.data["victim"], "killed by", e.data["killer"]))   # every death
game.on_quest_stage(game.resolve(editor_id="MQ101"), lambda e: print("stage", e.data["stage"]))
game.on_channel("my_mod", lambda e: print(e.data["payload"]))      # Skython.Send("my_mod", ...) from Papyrus
game.on("hit", {"target": [game.form("Skyrim.esm|0x14")]}, lambda e: print("hit by", e.data["aggressor"]))
```

Callbacks run in order on one Skython thread per script, and may make game calls. If a callback is slower than its events, the oldest waiting events are dropped and the next one says how many (`event.dropped`); events raised during a loading screen are not queued (`event.suppressed`).
````
Replace the Status paragraph with:
```markdown
## Status

Written for Skython M2. `live_dashboard.py` and the three event examples have been run in the game (through the dev bridge and as scripts); the argument count of every Papyrus call in the others was checked against the running game.
```

- [ ] **Step 4: Live check through the bridge and in-process**

Create `livetest/test_m2_examples.py`:
```python
"""The event examples (Task 54) live: each through the bridge (imported here, setup() given a recording proxy of the
bridge Game), and the copies deployed to scripts/ in-process (their python.log lines). Needs skythonbase (fresh_save)
and the three examples deployed (task file Step 4)."""
import importlib.util
import time

import pytest

import _testesp as t
from support import REPO, python_log_text

pytestmark = pytest.mark.usefixtures("fresh_save")
MUDCRAB = "Skyrim.esm|0x0E4010"   # the base Mudcrab actor (examples/twitch_mudcrabs.py)


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"example_{name}", REPO / "examples" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Recording:
    """The bridge Game, recording the notification texts an example shows."""

    def __init__(self, game):
        self._game, self.notes = game, []

    def call(self, script, function, *args, **kw):
        if (script, function) == ("Debug", "Notification"):
            self.notes.append(args[0])
        return self._game.call(script, function, *args, **kw)

    def __getattr__(self, name):
        return getattr(self._game, name)


def wait_until(pred, timeout: float, why: str):
    deadline = time.monotonic() + timeout
    while not (got := pred()):
        assert time.monotonic() < deadline, f"{why} within {timeout} s"
        time.sleep(0.5)
    return got


def test_kill_tracker(game):
    m = load("kill_tracker")
    rec = Recording(game)
    m.setup(rec)
    crab = game.call_method(t.PLAYER, "ObjectReference", "PlaceAtMe", game.form(MUDCRAB), 1, False, False)
    game.call_method(crab, "Actor", "Kill", t.PLAYER)
    wait_until(lambda: m._tracker.total >= 1, 20, "no kill counted")
    assert any(n.lower().startswith("kill #1: mudcrab") for n in rec.notes), rec.notes
    wait_until(lambda: "kill_tracker: kill #" in python_log_text(), 30, "the in-process kill_tracker logged no kill")


def test_papyrus_to_python(game):
    m = load("papyrus_to_python")
    rec = Recording(game)
    m.setup(rec)
    game.call("Skython", "Send", m.CHANNEL, "chest_opened|Livetest")
    wait_until(lambda: m._reporter.counts["chest_opened"] == 1, 20, "no message counted")
    assert [n.lower() for n in rec.notes] == ["chest_opened: livetest"], rec.notes
    wait_until(lambda: "papyrus_to_python: chest_opened|livetest" in python_log_text().lower(), 30,
               "the in-process papyrus_to_python logged nothing")


def test_quest_stage_notifier(game):
    m = load("quest_stage_notifier")
    m.QUESTS = ["SkythonTestQuest"]       # the test ESP's quest: never a vanilla quest's stages in a live test
    rec = Recording(game)
    m.setup(rec)
    quest = t.quest(game)
    wait_until(lambda: m._notifier and quest.form_id in m._notifier.watched, 60, "the notifier never watched the quest")
    game.call_method(quest, "Quest", "SetStage", 10)
    wait_until(lambda: any(n.endswith(": stage 10") for n in rec.notes), 20, f"no stage notification: {rec.notes}")
    assert "quest_stage_notifier: watching " in python_log_text()      # the in-process copy (MQ101-MQ103) started
```
Collect without a game: `uv run --project python pytest livetest/test_m2_examples.py -q -rs 2>&1 | tail -3` (expected `3 skipped`).

Deploy the examples as scripts (they then run on every later launch, like `live_dashboard.py`; they go on the removal list), then run:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
mkdir -p out/example-scripts && cp examples/kill_tracker.py examples/papyrus_to_python.py examples/quest_stage_notifier.py out/example-scripts/
tools/box/box_deploy.sh --scripts out/example-scripts
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m2_examples.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "kill_tracker|papyrus_to_python|quest_stage_notifier" out/box-skse/Skython/python.log
tools/box/box_stop.sh
tools/box/box_plugins.sh show
```
Expected: `3 passed`; `python.log` shows the three scripts `ok` at load, `kill_tracker: kill #1: Mudcrab`, `papyrus_to_python: chest_opened|Livetest` (any case), `quest_stage_notifier: watching Unbound, Before the Storm, Bleak Falls Barrow` and no traceback; `show` prints the backup. If a call fails with an argument-count or unknown-function error, fix the example and rerun Steps 2 and 4.

- [ ] **Step 5: Record in RESULTS and commit**

Append to "Plugin findings" in `spikes/RESULTS.md` (filled, no bracket left), and to the removal list `- Data\SKSE\Plugins\Skython\scripts\kill_tracker.py, papyrus_to_python.py, quest_stage_notifier.py (Task 54; run on every launch)`:
```markdown
- **Task 54, event examples live (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase, never saved; 1 launch, SKSE plugins: <list>):** `test_m2_examples.py` 3 passed. Through the bridge and in-process: `kill_tracker` counted the player's kill of a placed mudcrab (`<notification text>`), `papyrus_to_python` received `Skython.Send("my_mod", ...)` (`<python.log line>`), `quest_stage_notifier` notified `<text>` for the test quest's stage 10 and, in-process, watched `<list>`. No Papyrus call failed.
```
```bash
grep -n "Task 54, event examples live" spikes/RESULTS.md && ! grep -n "Task 54, event examples live.*<" spikes/RESULTS.md && echo "entry filled"
git add examples/kill_tracker.py examples/papyrus_to_python.py examples/quest_stage_notifier.py examples/README.md \
        python/tests/test_examples.py livetest/test_m2_examples.py spikes/RESULTS.md
git commit -m "docs(examples): kill tracker, Papyrus to Python with Skython.Send, quest stage notifier"
```
Expected: the line number, `entry filled`, then the commit.
