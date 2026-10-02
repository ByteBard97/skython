"""Kill tracker: a running tally of everything the player kills.

Each time an actor dies with the player as its killer, a notification shows the session's kill count and what you
killed. A summary by name goes to python.log every few minutes and when the script stops.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/.
Needs: Skython 0.1 (events). Standard library only.
"""
import collections
import threading

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
        except Exception as e:                   # game paused or a save loading; the count stands
            print(f"kill_tracker: notification skipped ({e})")

    def summary(self) -> str:
        with self._lock:
            parts = ", ".join(f"{name} x{n}" for name, n in self.by_name.most_common())
            return f"kill_tracker: {self.total} kills this session" + (f": {parts}" if parts else "")


_tracker = None
_stop = threading.Event()


def _summaries():
    while not _stop.wait(SUMMARY_EVERY_S):   # returns True (and the loop ends) once teardown sets _stop
        print(_tracker.summary())


def setup(game):
    global _tracker
    _tracker = KillTracker(game)
    _stop.clear()
    game.on_death(None, _tracker.on_death)     # None: every actor (no filter); on_death keeps the player's kills
    threading.Thread(target=_summaries, name="kill_tracker-summary", daemon=True).start()


def teardown():
    _stop.set()                                # a reload must not leave the old summary thread running
    if _tracker:
        print(_tracker.summary())
