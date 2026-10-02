"""A console `coc` from the main menu starts a world.

SKSE sends kNewGame only from its hook on the CharGen quest's NewGame and kPostLoadGame only for a save load, so a coc
from the main menu raises neither. Skython's world-start gate (Main Menu closed, then a Loading Menu closed, no save
load, the player in a cell) must still report the game interactive and raise one game_loaded with the next epoch.
DevBench's `inspect state` (playerLoaded = the player has 3D) is polled beside it as an independent check.

Run with the game at the main menu, no save loaded, in box mode (it drives the console through DevBench). The cell is
SPY_COC_CELL (default WhiterunBreezehome; SkythonTestCell needs `tools/box/box_plugins.sh add` before the launch).
It leaves the game in that cell, so run it first in a launch.
"""
import json
import os
import queue
import subprocess
import time

import pytest

from support import REPO, log_text

CELL = os.environ.get("SPY_COC_CELL", "WhiterunBreezehome")
WAIT_S = 120.0


def box_tool(*args: str) -> str:
    r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), *args], capture_output=True, text=True,
                       timeout=90)
    assert r.returncode == 0, f"box_console.sh {' '.join(args)} failed: {r.stdout} {r.stderr}"
    return r.stdout.strip()


def devbench_player_loaded() -> bool | None:
    try:
        return bool(json.loads(box_tool("--tool", "inspect", '{"kind":"state"}'))["playerLoaded"])
    except (AssertionError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None  # DevBench answers on the main thread; a stalled frame is "not known yet"


def test_coc_from_the_main_menu_starts_a_world(game):
    st0 = game.state()
    if st0["game_loaded"] or "Main Menu" not in st0["menus"]:
        pytest.skip("needs the main menu with no game loaded (run it first in a launch)")
    assert devbench_player_loaded() is False
    loaded: queue.Queue = queue.Queue()
    new_games: queue.Queue = queue.Queue()
    with game.on_game_loaded(loaded.put), game.on("new_game", None, new_games.put):
        box_tool(f"coc {CELL}")
        t0 = time.monotonic()
        skython_at = devbench_at = None
        st = st0
        while time.monotonic() - t0 < WAIT_S and (skython_at is None or devbench_at is None):
            st = game.state()
            now = time.monotonic() - t0
            if skython_at is None and st["interactive"]:
                skython_at = now
            if devbench_at is None and devbench_player_loaded():
                devbench_at = now
            time.sleep(0.5)
        print(f"\ncoc {CELL}: Skython interactive at {skython_at} s, DevBench playerLoaded at {devbench_at} s; "
              f"state {json.dumps({k: st[k] for k in ('interactive', 'game_loaded', 'loading', 'epoch', 'menus')})}")
        assert skython_at is not None, f"Skython not interactive {WAIT_S:g} s after coc {CELL}: {st}"
        assert devbench_at is not None, f"DevBench playerLoaded not true {WAIT_S:g} s after coc {CELL}"
        assert st["epoch"] == st0["epoch"] + 1 and st["player"] is not None
        ev = loaded.get(timeout=10.0)
        assert ev.epoch == st0["epoch"] + 1 and ev.data["new_game"] is True
        time.sleep(3.0)
        assert loaded.empty(), "a second game_loaded for the same world"
        print(f"new_game events: {new_games.qsize()} (SKSE sends kNewGame only for the CharGen quest)")
    assert devbench_player_loaded() is True and game.state()["interactive"] is True
    log = log_text()
    assert "lifecycle: world started without kNewGame or a save load" in log
    assert f"events: game_loaded epoch {st0['epoch'] + 1}" in log
