"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars (used by Task 29):
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
"""
import time

import pytest

from skython import errors
from skython.values import Form
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
    # The SKSE sources alone say ArmorAddon.GetMaskForSlot is bytecode; the VM says so too: a script-defined function
    # reports its .psc parameter name (`int Function GetMaskForSlot(int slot) global`), a native reports param1.
    with pytest.raises(errors.TypeMismatch, match=r"parameter slot \(argument 0 of ArmorAddon\.GetMaskForSlot\)"):
        game.call("ArmorAddon", "GetMaskForSlot", "thirty-two")
    with pytest.raises(errors.TypeMismatch, match=r"parameter param1 \(argument 0 of Armor\.GetMaskForSlot\)"):
        game.call("Armor", "GetMaskForSlot", "thirty-two")
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


def test_static_call_returns_form(game):
    p = game.call("Game", "GetPlayer")
    assert isinstance(p, Form) and p.form_id == PLAYER_ID and p.type == "Actor"


def test_form_argument_round_trip(game):
    sword = game.call("Game", "GetFormFromFile", 0x012EB7, "Skyrim.esm")
    # The VM spells some class names in capitals: GetScriptObjectType(VMTypeID).GetName() gave WEAPON and SPELL on the
    # box (Actor, ActorBase, Armor, MiscObject, Quest, Ammo, MagicEffect as in the .psc). Task 28 records it; Papyrus
    # names are case-insensitive.
    assert sword.form_key == IRON_SWORD and sword.type.lower() == "weapon"
    # A Form parameter given a Weapon. False is also what a None argument gives; the next line proves the form arrives:
    # SKSE's CreateFormArray fills the array with the argument and returns it (None if the native never ran, which is
    # what a value typed as WEAPON instead of the declared Form gave before Task 28's fix).
    assert game.call("Game", "IsObjectFavorited", sword) is False
    assert game.call("Utility", "CreateFormArray", 2, sword) == [sword, sword]


def test_form_of_the_wrong_class_is_rejected_before_dispatch(game):
    # BindObject's reject path: an ObjectReference parameter given a base Weapon.
    sword = game.call("Game", "GetFormFromFile", 0x012EB7, "Skyrim.esm")
    with pytest.raises(errors.TypeMismatch, match="argument 0 of Debug.SendAnimationEvent"):  # natives: param1..N (S7a)
        game.call("Debug", "SendAnimationEvent", sword, "x")


def test_none_for_an_object_parameter(game):
    assert game.call("Game", "IsObjectFavorited", None) is False  # a typed None Form


def test_none_for_an_array_parameter(game):
    r = game.call("Utility", "ResizeIntArray", None, 2, 0)  # a typed None Int[]
    assert r is None or isinstance(r, list)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the VM and the game carry on


def test_missing_forms_are_not_found_not_stale(game):
    for ref in (Form(form_key="Skyrim.esm|0xFFFFF0"), Form(form_key="NotAPlugin.esp|0x000800"),
                Form(form_id=0xFF123456)):  # the last is a runtime id that names nothing
        with pytest.raises(errors.NotFound):
            game.call("Game", "IsObjectFavorited", ref)


def test_primitive_arrays_round_trip(game):
    assert game.call("Utility", "CreateIntArray", 3, 7) == [7, 7, 7]
    assert game.call("Utility", "ResizeIntArray", [1, 2, 3], 5, 0) == [1, 2, 3, 0, 0]
    assert game.call("Utility", "CreateFloatArray", 2, 1.5) == [1.5, 1.5]


def test_array_wire_shape(session):
    r = raw_call(session, "Utility", "CreateIntArray", [3, 7])
    assert r["result"] == {"array": "Int", "items": [7, 7, 7]}


def test_array_argument_with_wrong_element_type(session):
    r = raw_call(session, "Utility", "ResizeIntArray", [{"array": "Int", "items": [1, "two"]}, 5, 0])
    assert r["ok"] is False and r["error"]["code"] in ("type_mismatch", "bad_request")


def test_array_argument_longer_than_128_is_type_mismatch(session):
    # Sent raw: the Python client refuses it before it leaves the process. Core refuses it while parsing the request.
    r = raw_call(session, "Utility", "ResizeIntArray", [{"array": "Int", "items": list(range(129))}, 5, 0])
    assert r["ok"] is False and r["error"]["code"] == "type_mismatch" and "128" in r["error"]["message"], r


def test_light_plugin_form_key(game):
    # A light (0xFE) id: the form key carries the plugin's name and the 12-bit local id, whichever way SourceFile
    # found the file (GetFile(0) or the light load-order index).
    name = "ccbgssse002-exoticarrows.esl"
    if not game.call("Game", "IsPluginInstalled", name):
        pytest.skip(f"{name} is not installed on this game")
    f = game.call("Game", "GetFormFromFile", 0x800, name)
    assert f.form_key == f"{name}|0x000800" and (f.form_id >> 24) == 0xFE
