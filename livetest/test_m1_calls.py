"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars (used by Task 29):
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
SPY_PLAIN_QUEST=0x<quest form id> SPY_PLAIN_ALIAS=<id of an unscripted alias>
SPY_OWN_QUEST=0x<quest form id> SPY_OWN_SCRIPT=<its attached script> SPY_OWN_FUNCTION=<parameterless bytecode getter>
Values used on the box (s4test): spikes/RESULTS.md, S4 row and the Task 29 entry.
"""
import os
import time

import pytest

from skython import errors
from skython.values import Alias, Form
from support import log_text, raw_connect, rpc

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
    # Game strings come back in the casing the engine's string pool already holds (CreateStringArray(2, "x") gave
    # ['X', 'X'], Task 28), so a string the game built is compared case-insensitively.
    s = game.call("StringUtil", "Substring", "hello", 1, 3)                              # String (SKSE)
    assert isinstance(s, str) and s.lower() == "ell"
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
    # The VM's GetScriptObjectType(VMTypeID).GetName() gave WEAPON and SPELL on the box (Task 28); the plugin maps
    # engine class names to their .psc spelling (R56), so type is exact.
    assert sword.form_key == IRON_SWORD and sword.type == "Weapon"
    assert game.call("Game", "GetFormFromFile", 0x012FCD, "Skyrim.esm").type == "Spell"  # Flames
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


def test_actor_for_an_object_reference_parameter(game):
    # A non-Form ancestor: SendAnimationEvent's parameter is declared ObjectReference and gets an Actor, so BindObject
    # re-types the value to ObjectReference. No TypeMismatch, no crash; an unknown event does nothing and returns None.
    player = game.call("Game", "GetPlayer")
    assert game.call("Debug", "SendAnimationEvent", player, "SkythonNoSuchEvent") is None
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the VM and the game carry on
    # SendAnimationEvent returns nothing, so None cannot tell a re-typed argument from a native that never ran. GetDistance
    # returns a Float: an Actor for its ObjectReference parameter gives 0.0 to itself, a native that never ran gives None.
    d = game.call_method(player, "ObjectReference", "GetDistance", player)
    assert isinstance(d, float) and d == 0.0, d


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
    assert game.call("Utility", "CreateFormArray", 1, f) == [f]  # a light form as an argument and back


def _s4():
    """The quest/alias/reference spike S4 recorded. They come from one particular throwaway save and can go stale."""
    try:
        return int(os.environ["SPY_S4_QUEST"], 16), int(os.environ["SPY_S4_ALIAS"]), int(os.environ["SPY_S4_REF"], 16)
    except KeyError as e:
        pytest.skip(f"set SPY_S4_QUEST, SPY_S4_ALIAS and SPY_S4_REF from spikes/RESULTS.md ({e.args[0]} is missing)")


def test_method_call_float(game):
    hp = game.call_method(Form(form_id=PLAYER_ID), "Actor", "GetActorValue", "Health")
    assert isinstance(hp, float) and hp > 0


def test_method_call_on_ancestor_script(game):
    name = game.call_method(Form(form_id=PLAYER_ID), "Form", "GetName")
    assert isinstance(name, str)


def test_method_call_on_cold_base_form(game):
    # A base form no script has touched has no bound script object until the server binds one.
    sword = game.form(IRON_SWORD)
    name = game.call_method(sword, "Form", "GetName")
    assert isinstance(name, str) and name
    assert game.call_method(sword, "Weapon", "GetBaseDamage") > 0


def test_resolve_by_key_and_by_id(game):
    sword = game.form(IRON_SWORD)
    assert sword.type == "Weapon" and sword.form_key == IRON_SWORD
    assert game.resolve(form_id=sword.form_id).form_key == IRON_SWORD


def test_global_called_as_member_is_bad_request(game):
    with pytest.raises(errors.BadRequest, match="global"):
        game.call_method(Form(form_id=PLAYER_ID), "Utility", "RandomInt", 1, 6)


def test_method_on_wrong_kind_of_form(game):
    sword = game.form(IRON_SWORD)
    with pytest.raises(errors.TypeMismatch, match="Weapon"):
        game.call_method(sword, "Actor", "GetActorValue", "Health")


def test_resolve_missing_form_is_not_found(game):
    with pytest.raises(errors.NotFound):
        game.form("Skyrim.esm|0xFFFFF0")
    with pytest.raises(errors.NotFound):
        game.form("NotAPlugin.esp|0x000800")


def test_editor_id_unavailable_is_a_clear_not_found(game):
    with pytest.raises(errors.NotFound, match="editor id"):
        game.resolve(editor_id="NoSuchEditorIdAnywhere")


def test_object_array_result(game):
    try:
        forms = game.call_method(Form(form_id=PLAYER_ID), "ObjectReference", "GetContainerForms")  # SKSE
    except errors.UnknownFunction:
        pytest.skip("SKSE's ObjectReference.GetContainerForms is not available")
    assert isinstance(forms, list) and forms, "the player's inventory should not be empty"
    assert all(isinstance(f, Form) for f in forms) and any(f.form_key for f in forms)


def test_form_argument_arrives(game):
    # The proof Task 28 could not give: these counts only change if the Form argument reaches the game as that form
    # (a None or empty Variable would leave GetItemCount at 0 and AddItem without effect).
    player = Form(form_id=PLAYER_ID)
    sword = game.form(IRON_SWORD)
    n = game.call_method(player, "ObjectReference", "GetItemCount", sword)
    game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
    try:
        assert game.call_method(player, "ObjectReference", "GetItemCount", sword) == n + 1
    finally:
        # RemoveItem(akItemToRemove, aiCount, abSilent, akOtherContainer): every argument is passed (no defaults), so
        # akOtherContainer is None, a typed None ObjectReference (Task 28).
        game.call_method(player, "ObjectReference", "RemoveItem", sword, 1, True, None)
    assert game.call_method(player, "ObjectReference", "GetItemCount", sword) == n


def test_alias_method_calls(game):
    quest_id, alias_id, expected_ref = _s4()
    quest = game.resolve(form_id=quest_id)
    alias = game.alias(quest, alias_id)
    # The converse of the alias-0 test below: a Quest result must decode as a Form of type Quest, not as an alias.
    owner = game.call_method(alias, "Alias", "GetOwningQuest")
    assert isinstance(owner, Form) and not isinstance(owner, Alias) and owner.form_id == quest_id and owner.type == "Quest"
    # Spec 4.4 names GetRef; ReferenceAlias also has GetReference. Check ReferenceAlias.psc and use whichever
    # this runtime's script declares (the plan calls GetReference; both return the filled reference).
    ref = game.call_method(alias, "ReferenceAlias", "GetReference")
    assert ref.form_id == expected_ref


def test_alias_result_is_an_alias_not_its_quest(game):
    # Quest.GetAlias returns an Alias object. Alias 0's handle equals the quest's FormID exactly (S7g), the hardest
    # case for ObjectToJson's form-first decode: HandleIsType(form type) must reject it and the alias check take it.
    # Side effect: GetAlias(0) binds an `Alias`-class object to plain alias 0 (S7g); harmless only because `s4test` is
    # never saved.
    quest_id, alias_id, _ = _s4()
    quest = game.resolve(form_id=quest_id)
    for aid in sorted({0, alias_id}):
        result = game.call_method(quest, "Quest", "GetAlias", aid)
        assert isinstance(result, Alias) and result.alias_id == aid and result.quest.form_id == quest_id, (aid, result)


def test_quest_result_is_a_quest_not_an_alias(game):
    # Measurement for Task 28's decode order: ObjectToJson logs, once, whether a Quest's own handle also passes
    # HandleIsType(BGSRefAlias/BGSLocAlias). Record the line in spikes/RESULTS.md; form-first is correct either way.
    quest_id, alias_id, _ = _s4()
    owner = game.call_method(game.alias(game.resolve(form_id=quest_id), alias_id), "Alias", "GetOwningQuest")
    assert isinstance(owner, Form) and not isinstance(owner, Alias) and owner.type == "Quest"
    lines = [l for l in log_text().splitlines() if "values: quest handle" in l]
    assert lines, "ObjectToJson did not log the quest-handle measurement"
    print(lines[0])


def test_alias_plain_is_not_dispatchable_as_reference_alias(game):
    # R45: an unscripted alias has no bound ReferenceAlias object, and the server never binds one on demand.
    try:
        quest_id, alias_id = int(os.environ["SPY_PLAIN_QUEST"], 16), int(os.environ["SPY_PLAIN_ALIAS"])
    except KeyError:
        pytest.skip("set SPY_PLAIN_QUEST/SPY_PLAIN_ALIAS (S7g cand 0: quest 0x0010FDD5, alias 0, plain)")
    alias = game.alias(game.resolve(form_id=quest_id), alias_id)
    with pytest.raises(errors.TypeMismatch):
        game.call_method(alias, "ReferenceAlias", "GetReference")


def test_skse_native_with_form_argument(game):
    # Actor.EquipItemEx(Form item, int equipSlot, bool preventUnequip, bool equipSound) is an SKSE native; every
    # argument is passed (no defaults). The form must arrive as that form, or IsEquipped stays False.
    player = Form(form_id=PLAYER_ID)
    sword = game.form(IRON_SWORD)
    game.call_method(player, "ObjectReference", "AddItem", sword, 1, True)
    try:
        game.call_method(player, "Actor", "EquipItemEx", sword, 1, False, False)
        time.sleep(0.5)
        assert game.call_method(player, "Actor", "IsEquipped", sword) is True
        game.call_method(player, "Actor", "UnequipItemEx", sword, 1, False)
    finally:
        game.call_method(player, "ObjectReference", "RemoveItem", sword, 1, True, None)


def _own_script():
    """A quest, a script attached to it (not a native class) and a member function declared on that script itself."""
    try:
        return (int(os.environ["SPY_OWN_QUEST"], 16), os.environ["SPY_OWN_SCRIPT"], os.environ["SPY_OWN_FUNCTION"])
    except KeyError as e:
        pytest.skip(f"set SPY_OWN_QUEST, SPY_OWN_SCRIPT and SPY_OWN_FUNCTION (see Step 1) ({e.args[0]} is missing)")


def test_own_script_bytecode_member_function(game):
    # The gate S7c could not give: a Papyrus-bytecode (non-native) member function declared on a mod's own attached
    # script, found through FindBoundObject(handle, "<script>") and dispatched on the bound object.
    quest_id, script, function = _own_script()
    quest = game.resolve(form_id=quest_id)
    first = game.call_method(quest, script, function)
    assert game.call_method(quest, script, function) == first  # a side-effect-free getter answers the same twice
