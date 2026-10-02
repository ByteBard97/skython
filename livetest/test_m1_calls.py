"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars (used by Task 29):
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
"""
import time

import pytest

from skython import errors
from support import raw_connect, rpc

PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def raw_state(session) -> dict:
    s = raw_connect(session)
    try:
        return rpc(s, "game.state")["result"]
    finally:
        s.close()


def raw_call(session, script, function, args, **extra) -> dict:
    s = raw_connect(session)
    try:
        return rpc(s, "papyrus.call_static", {"script": script, "function": function, "args": args, **extra}, timeout=30)
    finally:
        s.close()


def test_state_reports_player(game):
    st = game.state()
    assert st["interactive"] is True and st["loading"] is False and st["epoch"] >= 1
    assert st["player"].form_id == PLAYER_ID and st["player"].form_key == "Skyrim.esm|0x000014"
    assert st["player"].type == "Actor" and st["game_days_passed"] > 0


def test_static_int_call(game):
    for _ in range(20):
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_primitive_result_types(game):
    assert game.call("Game", "IsPluginInstalled", "Skyrim.esm") is True                  # Bool
    assert 0.0 <= game.call("Utility", "RandomFloat", 0.0, 1.0) <= 1.0                   # Float
    assert game.call("StringUtil", "Substring", "hello", 1, 3) == "ell"                  # String (SKSE)
    assert game.call("StringUtil", "GetLength", "hello") == 5                            # Int


def test_float_results_do_not_widen_noisily(game):
    assert game.call("Math", "Sqrt", 0.0001) == 0.01  # not 0.009999999776482582


def test_latent_function_completes_with_none(game):
    t0 = time.monotonic()
    assert game.call("Utility", "Wait", 0.5) is None
    assert time.monotonic() - t0 >= 0.4


def test_unknown_script_and_function(game):
    # Twice: after the first failed lookup the VM keeps an empty stub type that the next lookup returns as found
    # (spike S7b); TypeOf's NoLoad cross-check must still answer unknown_function.
    for _ in range(2):
        with pytest.raises(errors.UnknownFunction):
            game.call("NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.UnknownFunction):
        game.call("Utility", "NoSuchFunction")


def test_wrong_argument_type_names_the_parameter(game):
    # A native reports its parameters as param1..N (S7a), not aiMin: the position is what the message must carry.
    with pytest.raises(errors.TypeMismatch, match=r"argument 0 of Utility\.RandomInt"):
        game.call("Utility", "RandomInt", "one", 6)


def test_script_defined_global_function(game):
    # ArmorAddon.GetMaskForSlot and Game.GetSkillLegendaryLevel are Papyrus bytecode in SKSE's scripts (no `native`
    # keyword); Armor.GetMaskForSlot is the SKSE native the first one wraps. (The brief's Game.GetCameraState wraps
    # Camera.GetCameraState, which SKSE 2.2.6 never registers: papyrusCamera::RegisterFuncs has no caller, the native
    # returns None and the wrapper casts it to 0. Substituted in Task 27, recorded in spikes/RESULTS.md.)
    assert game.call("ArmorAddon", "GetMaskForSlot", 32) == game.call("Armor", "GetMaskForSlot", 32) == 4
    assert isinstance(game.call("Game", "GetSkillLegendaryLevel", "OneHanded"), int)
    # Unmeasured: whether a script-defined function reports its .psc parameter name (S7 sampled natives only).
    with pytest.raises(errors.TypeMismatch, match="argument 0 of Game.GetSkillLegendaryLevel") as ei:
        game.call("Game", "GetSkillLegendaryLevel", 5)
    print("script-defined parameter name reported:", "actorValue" in str(ei.value))  # record in spikes/RESULTS.md


def test_wrong_argument_count(game):
    with pytest.raises(errors.TypeMismatch, match="takes 2"):
        game.call("Utility", "RandomInt", 1)


def test_int_out_of_range_is_rejected_by_the_plugin(session):
    # Sent raw: the Python client would refuse it before it left the process.
    assert raw_call(session, "Utility", "RandomInt", [1, 2147483648])["error"]["code"] == "type_mismatch"


def test_object_results_are_unsupported_until_task_28(game):
    # Replaced in Task 28 by test_static_call_returns_form.
    with pytest.raises(errors.Unsupported):
        game.call("Game", "GetPlayer")


def test_timeout_carries_game_state(game):
    with pytest.raises(errors.Timeout) as ei:
        game.call("Utility", "Wait", 3.0, timeout=0.5)
    assert "paused" in ei.value.detail and isinstance(ei.value.detail["menus"], list)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the bridge and the game carry on


@pytest.mark.parametrize("bad", ["soon", -5, 0, 1.5, None, 10**12])
def test_odd_timeout_values_never_hang_or_crash(session, bad):
    r = raw_call(session, "Utility", "RandomInt", [1, 6], timeout_ms=bad)
    if r["ok"]:
        assert 1 <= r["result"] <= 6
    else:  # core rejects the value (bad_request) or a 1 ms deadline expires (timeout)
        assert r["error"]["code"] in ("bad_request", "timeout")
    assert raw_state(session)["data_ready"] is True


def steady_late_count(session, settle: float = 3.5, tries: int = 5) -> int:
    """late_results_dropped once nothing is still on its way: two equal readings `settle` seconds apart."""
    prev = raw_state(session)["diagnostics"]["late_results_dropped"]
    for _ in range(tries):
        time.sleep(settle)
        now = raw_state(session)["diagnostics"]["late_results_dropped"]
        if now == prev:
            return now
        prev = now
    raise AssertionError("late_results_dropped kept changing: late results from earlier tests are still arriving")


def test_late_result_after_timeout_is_dropped_and_counted(session, game):
    # test_timeout_carries_game_state left a 3 s Utility.Wait running in the game; its late result must be counted
    # before `before` is read, or this test sees two drops instead of one.
    before = steady_late_count(session)
    with pytest.raises(errors.Timeout):
        game.call("Utility", "Wait", 1.0, timeout=0.2)
    time.sleep(2.0)  # the wait finishes in the game; its result must be dropped, not delivered
    assert raw_state(session)["diagnostics"]["late_results_dropped"] == before + 1
