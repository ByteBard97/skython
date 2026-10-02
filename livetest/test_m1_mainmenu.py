"""Run with the game launched and sitting at the main menu (no save loaded)."""
import pytest

from skython import errors
from support import raw_connect, rpc


@pytest.fixture
def main_menu(game):
    st = game.state()
    if st["game_loaded"]:
        pytest.skip("needs the main menu with no save loaded")
    return st


def test_state_at_main_menu(game, main_menu):
    assert main_menu["data_ready"] is True and main_menu["interactive"] is False
    assert main_menu["player"] is None and main_menu["game_days_passed"] is None
    assert "Main Menu" in main_menu["menus"]


def test_state_shape_is_stable(session, main_menu):
    s = raw_connect(session)
    try:
        st = rpc(s, "game.state")["result"]
    finally:
        s.close()
    for key in ("data_ready", "game_loaded", "loading", "interactive", "epoch", "menus", "paused", "player",
                "game_days_passed", "diagnostics"):
        assert key in st, key
    assert st["diagnostics"]["late_results_dropped"] >= 0 and st["diagnostics"]["in_flight"] >= 0


def test_calls_are_not_ready_with_reason(game, main_menu):
    with pytest.raises(errors.NotReady) as ei:
        game.call("Utility", "RandomInt", 1, 6)
    d = ei.value.detail
    assert d["reason"] == "no_game" and d["paused"] in (True, False) and "Main Menu" in d["menus"]
    # Readiness is checked by the Dispatcher before any op runs, so ops built later are already not_ready.
    with pytest.raises(errors.NotReady):
        game.form("Skyrim.esm|0x012EB7")
