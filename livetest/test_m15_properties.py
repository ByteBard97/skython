"""Property happy paths on SkythonTest.esp: exact reads of every auto property type against the ESP's fixed
initial values, Int/Float/Bool writes seen by Papyrus, and the parent-variable offset (SkythonTestQuest extends
SkythonTestQuestBase, which has its own variables). Needs skythonbase, loaded by fresh_save, which holds the
initial values. Every write is restored; skythonbase is never saved. Run with -s to see the values read."""
import pytest

import _testesp as t
from skython import errors
from skython.values import Form

pytestmark = pytest.mark.usefixtures("fresh_save")
CHILD = t.INITIAL[t.QUEST_SCRIPT]       # Counter 7, Ratio 0.5, Flag True, Label "initial label", Target NPC_KEY
BASE = t.INITIAL[t.BASE_SCRIPT]         # BaseCounter 3, BaseLabel "base label"


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-props")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    t.ensure_running(mgame, t.quest(mgame))
    return t.discover(mgame)


def counters(game, quest):
    """(Counter, BaseCounter) as Papyrus bytecode sees them: the getters read the variables by Papyrus's own map."""
    return (game.call_method(quest, t.QUEST_SCRIPT, "GetCounter"),
            game.call_method(quest, t.QUEST_SCRIPT, "GetBaseCounter"))


def test_reads_every_child_property_exactly(game, esp):
    for prop in ("Counter", "Ratio", "Flag", "Label"):
        v = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        print(f"\n{t.QUEST_SCRIPT}.{prop} -> {v!r}")
        assert type(v) is type(CHILD[prop]) and v == CHILD[prop], (prop, v)
    target = game.get_property(esp.quest, t.QUEST_SCRIPT, "Target")
    print(f"\n{t.QUEST_SCRIPT}.Target -> {target!r}")
    assert isinstance(target, Form) and target.type == "Actor" and t.same_key(target, CHILD["Target"]), target
    assert target.form_id == esp.npc.form_id


def test_reads_inherited_properties_exactly_through_child_and_parent_names(game, esp):
    for prop, want in BASE.items():
        via_child = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        via_base = game.get_property(esp.quest, t.BASE_SCRIPT, prop)
        print(f"\n{prop}: via {t.QUEST_SCRIPT} {via_child!r}, via {t.BASE_SCRIPT} {via_base!r}")
        assert type(via_child) is type(want) and via_child == want and via_base == want, (prop, via_child, via_base)


def test_int_reads_agree_with_papyrus(game, esp):
    assert counters(game, esp.quest) == (CHILD["Counter"], BASE["BaseCounter"])  # (7, 3)


def test_child_int_write_lands_in_the_child_slot(game, esp):
    """The discriminating case: without the parent's variable count in the index, this write would land in one of
    SkythonTestQuestBase's slots, and GetBaseCounter (or BaseLabel) would show it."""
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", 12)
        assert counters(game, esp.quest) == (12, 3)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseLabel") == BASE["BaseLabel"]  # parent slot untouched
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", CHILD["Counter"])
    assert counters(game, esp.quest) == (7, 3)


def test_inherited_int_write_lands_in_the_parent_slot(game, esp):
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", 10)
        assert counters(game, esp.quest) == (7, 10)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseCounter") == 10
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", BASE["BaseCounter"])
    assert counters(game, esp.quest) == (7, 3)


def ratio_flag(game, quest):
    """(Ratio, Flag) as Papyrus bytecode sees them (GetRatio/GetFlag), not through get_property, which reads the
    same variable index set_property wrote and so could not see a write to the wrong slot."""
    return (game.call_method(quest, t.QUEST_SCRIPT, "GetRatio"),
            game.call_method(quest, t.QUEST_SCRIPT, "GetFlag"))


def test_float_and_bool_writes_read_back(game, esp):
    assert ratio_flag(game, esp.quest) == (CHILD["Ratio"], CHILD["Flag"])  # (0.5, True)
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", 0.25)    # exact in binary: no rounding noise
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", False)
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == 0.25
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is False
        got = ratio_flag(game, esp.quest)                              # Papyrus sees the writes in their own slots
        assert got[0] == 0.25 and got[1] is False, got
        assert counters(game, esp.quest) == (7, 3)                     # neighbouring Int slots untouched
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", CHILD["Ratio"])
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", CHILD["Flag"])
    got = ratio_flag(game, esp.quest)
    assert got[0] == 0.5 and got[1] is True, got
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == 0.5
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is True


def test_string_and_object_writes_stay_unsupported(game, esp):
    """v1 writes only Int, Float and Bool. Writing the value already held cannot be a type mismatch."""
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Label", CHILD["Label"])
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Target", esp.npc)
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.BASE_SCRIPT, "BaseLabel", "x")
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Label") == "initial label"


def test_full_property_stays_unsupported(game, esp):
    # Computed would read 14 (Counter * 2) through Papyrus, but v1 refuses full properties before anything runs.
    with pytest.raises(errors.Unsupported, match="full property"):
        game.get_property(esp.quest, t.QUEST_SCRIPT, "Computed")


def test_alias_script_int_property_read_and_write(game, esp):
    alias, other = esp.ref_aliases[t.TARGET_ALIAS], esp.ref_aliases[t.ITEM_ALIAS]
    assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == t.INITIAL[t.ALIAS_SCRIPT]["Tag"]  # 5
    assert game.get_property(other, t.ALIAS_SCRIPT, "Tag") == 5
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", alias) == 5       # Papyrus agrees
    try:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", 16)
        assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == 16
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", alias) == 16  # Papyrus sees the write
        assert game.get_property(other, t.ALIAS_SCRIPT, "Tag") == 5     # each alias has its own script object
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", other) == 5
    finally:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", 5)
    assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == 5
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", alias) == 5


def test_location_alias_script_int_property_read_and_write(game, esp):
    la = esp.location_alias
    assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == t.INITIAL[t.LOCATION_ALIAS_SCRIPT]["Tag"]  # 6
    try:
        game.set_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag", 17)
        assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == 17
    finally:
        game.set_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag", 6)
    assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == 6
