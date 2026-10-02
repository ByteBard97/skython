"""In-process event smoke script for the ESP event tests. Deployed to Data/SKSE/Plugins/Skython/scripts/ by
`tools/box/box_deploy.sh --scripts livetest/scripts`. Development only: never ship it to players.

setup() subscribes, from a thread, to the test ESP's channel, to one mod event, to game_loaded and (slowly, 2 s per
event, max_queue 4) to SLOW_CHANNEL, and rewrites livetest_events.json next to python.log after every delivered event
(the last KEEP events per list). The slow callback shares this Game's dispatch thread, so while it sleeps the other
subscriptions of this script wait (in order) too: only the overflow test sends on SLOW_CHANNEL. It only listens: the
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
SLOW_CHANNEL = "skython_test_slow"   # only test_slow_callback_overflows_in_process sends on it
SLOW_QUEUE = 4
FILE = "livetest_events.json"
KEEP = 500
log = logging.getLogger("livetest_events")
_lock = threading.Lock()
_state = {"status": "starting", "events": [], "slow": [], "errors": []}


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


def _append(key: str, event) -> None:
    with _lock:
        _state[key].append({"name": event.name, "sub": event.sub, "seq": event.seq, "epoch": event.epoch,
                            "dropped": event.dropped, "suppressed": event.suppressed,
                            "data": _wire(event.data), "t": time.time()})
        del _state[key][:-KEEP]
    _write()


def record(event) -> None:
    _append("events", event)


def slow_record(event) -> None:
    """A callback that sleeps 2 s per event, so the hub's 4-slot queue overflows."""
    _append("slow", event)
    time.sleep(2.0)


def _subscribe(game) -> None:
    try:
        game.on_channel(t.CHANNEL, record)
        game.on_mod_event(MOD_EVENT, record)
        game.on_game_loaded(record)
        game.on("channel", {"channel": [SLOW_CHANNEL]}, slow_record, max_queue=SLOW_QUEUE)
        _state["status"] = "subscribed"
    except Exception as e:  # recorded for the live test, logged with its traceback
        _state["status"] = "failed"
        _state["errors"].append(f"{type(e).__name__}: {e}")
        log.exception("livetest_events: subscribe failed")
    _write()


def setup(game):
    threading.Thread(target=_subscribe, args=(game,), name="livetest_events", daemon=True).start()
