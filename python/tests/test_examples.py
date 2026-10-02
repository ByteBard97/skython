"""The event examples in examples/: their handlers, fed synthetic events through a stub game."""
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
