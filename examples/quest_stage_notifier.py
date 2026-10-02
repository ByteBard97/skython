"""Quest stage notifier: a notification whenever a quest you follow reaches a new stage.

Useful while writing a quest mod (watch your own quest's stages as you play through it) or to follow progress in
any quest. List the quests by editor id below.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and edit QUESTS.
Needs: Skython 0.1 (events). Standard library only.
"""
import threading

from skython.errors import CallTimeout, ConnectionLost, NotFound, NotReady

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
        except Exception as e:            # a menu open or a save loading; the stage is in python.log anyway
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
        except (ConnectionLost, CallTimeout):  # the game (or the bridge) went away or stopped answering: nothing to watch
            return
    _notifier = Notifier(game)
    for editor_id in QUESTS:
        try:
            _notifier.watch(editor_id)
        except NotFound:
            print(f"quest_stage_notifier: no quest with editor id {editor_id!r}; check QUESTS at the top of this file")
        except Exception as e:            # any other failure skips this quest, never the rest
            print(f"quest_stage_notifier: cannot watch {editor_id!r} ({type(e).__name__}: {e})")
    print(f"quest_stage_notifier: watching {', '.join(_notifier.watched.values()) or 'nothing'}")


def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()
