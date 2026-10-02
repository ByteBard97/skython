"""Own-script tests on SkythonTest.esp: a bytecode member call on our own script, alias arguments (the BindAlias
path), LocationAlias operations, and the full property refused. Needs skythonbase: the module fixture fresh_save
loads it, and _testesp.discover fails at once when another save is loaded or the ESP differs from its build.
SPY_PLAIN_QUEST/SPY_PLAIN_ALIAS (livetest/s4test.env) name a plain alias in Skyrim.esm: the same in every save.
Run with -s to see the values."""
import pytest

import _testesp as t
from skython import errors
from support import spy_env

pytestmark = pytest.mark.usefixtures("fresh_save")


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-own")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    # Start the quest first: alias scripts may bind only once the quest runs, and discover() calls Alias.GetName
    # through them, so discovering on a stopped quest could raise a false "no script object bound" STOP.
    t.ensure_running(mgame, t.quest(mgame))
    h = t.discover(mgame)
    print(f"\n{t.QUEST_KEY}: aliases {h.alias_names}, npc {h.npc.form_key}, objectives {h.objectives}")
    return h


def test_esp_is_loaded_and_the_player_is_in_its_cell(game, esp):
    assert game.call("Game", "IsPluginInstalled", t.PLUGIN) is True
    assert t.same_key(t.require_test_cell(game), t.CELL_KEY)
    assert game.call_method(esp.quest, "Quest", "IsRunning") is True


def test_bytecode_member_function_on_our_own_script(game, esp):
    # BumpCounter is Papyrus bytecode declared on SkythonTestQuest itself (not a native, not inherited).
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == t.INITIAL[t.QUEST_SCRIPT]["Counter"]  # 7
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", 3) == 10
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == 10
    finally:
        game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", -3)
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == 7


def test_alias_argument_arrives_as_that_alias(game, esp):
    # The BindAlias path: the argument binds the object already bound to the alias, re-typed to ReferenceAlias.
    # DescribeAlias answers "empty" for an unfilled alias of this quest and "filled" once it holds a reference; an
    # argument that arrived as None would answer "none", one bound to another quest "foreign".
    for a in esp.ref_aliases:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", a) == "empty", a
    target = esp.ref_aliases[t.TARGET_ALIAS]
    game.call_method(target, "ReferenceAlias", "ForceRefTo", esp.npc)
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", target) == "filled"
        others = [game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", a) for a in esp.ref_aliases[1:]]
        assert others == ["empty", "empty"], others          # the filled answer belongs to the alias passed
    finally:
        game.call_method(target, "ReferenceAlias", "Clear")
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", target) == "empty"


def test_none_alias_argument(game, esp):
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", None) == "none"


def test_unscripted_alias_argument_is_type_mismatch(game, esp):
    quest, alias = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")  # Skyrim.esm data: plain in every save
    plain = game.alias(game.resolve(form_id=int(quest, 16)), int(alias))
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", plain)


def test_location_alias_for_a_reference_alias_parameter_is_type_mismatch(game, esp):
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", esp.location_alias)


def test_location_alias_force_get_clear_round_trip(game, esp):
    la = esp.location_alias                                   # alias 3, scripted with SkythonTestLocationAlias
    assert game.call_method(la, "LocationAlias", "GetLocation") is None   # optional, empty after Start
    ours, vanilla = t.location(game), game.form(t.WHITERUN_LOCATION)
    assert vanilla.type == "Location", vanilla
    for place in (vanilla, ours):
        game.call_method(la, "LocationAlias", "ForceLocationTo", place)
        got = game.call_method(la, "LocationAlias", "GetLocation")
        assert got is not None and got.form_id == place.form_id and got.type == "Location", (place, got)
    game.call_method(la, "LocationAlias", "Clear")
    assert game.call_method(la, "LocationAlias", "GetLocation") is None


def test_location_alias_argument_arrives_as_that_alias(game, esp):
    # The BindAlias path with a LocationAlias parameter: "empty" while alias 3 holds no location, "filled" after
    # ForceLocationTo (an argument that arrived as None would answer "none", another quest's alias "foreign").
    la = esp.location_alias
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "empty"
    game.call_method(la, "LocationAlias", "ForceLocationTo", t.location(game))
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "filled"
    finally:
        game.call_method(la, "LocationAlias", "Clear")
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "empty"
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", None) == "none"


def test_alias_argument_typed_as_our_alias_script(game, esp):
    # A parameter typed SkythonTestAlias (our own alias script, not the native ReferenceAlias): the bound object is
    # re-typed to that script, so the function reads the script's own property Tag (initial 5).
    target = esp.ref_aliases[t.TARGET_ALIAS]
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", target) == t.INITIAL[t.ALIAS_SCRIPT]["Tag"]  # 5


def test_full_property_is_unsupported_before_anything_runs(game, esp):
    with pytest.raises(errors.Unsupported, match="full property"):
        game.get_property(esp.quest, t.QUEST_SCRIPT, "Computed")
    with pytest.raises(errors.Unsupported, match="full property"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Computed", 1)
