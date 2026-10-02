"""Papyrus to Python: your own Papyrus script reports to Python in one line.

Skython comes with a six-line Papyrus script, `Skython`, with one native function, `Skython.Send(channel, payload)`.
Call it from any of your scripts and every Python subscriber of that channel gets the payload at once. For example,
a chest that reports who opened it (compile it with Skython.psc on the import path and attach it to a container in
your plugin):

    Scriptname MyModChestReporter extends ObjectReference

    Event OnActivate(ObjectReference akActionRef)
        Skython.Send("my_mod", "chest_opened|" + akActionRef.GetDisplayName())
    EndEvent

This script shows each message as a notification and counts messages by kind (the text before "|"). A payload is a
string of up to 64 KiB (a longer one is cut and the event says `truncated`); JSON works well for structured data.
The engine may change the case of a string it has seen before, so compare names case-insensitively.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ (Skython.pex goes in Data/Scripts/ with Skython).
Needs: Skython 0.1 (events). Standard library only.
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
        except Exception as e:                   # the message counts even when the game can't show it
            print(f"papyrus_to_python: notification skipped ({e})")


_reporter = None


def setup(game):
    global _reporter
    _reporter = Reporter(game)
    game.on_channel(CHANNEL, _reporter.on_message)
