"""The base save `skythonbase` (made once from the main menu with `coc SkythonTestCell`) and the fresh_save
fixture that prefers it. Needs SkythonTest.esp and its scripts on the test machine with the plugins.txt line added
(tools/box/box_plugins.sh add): skythonbase depends on the plugin. Loads saves; never saves."""
from support import load_save, log_text

CELL = "skythontest.esp|0x000802"
QUEST = "SkythonTest.esp|0x000800"
NPC = "SkythonTest.esp|0x000804"
PLAYER_ID = 0x14
# The initial values skythonbase must hold (nothing writes a property before the save).
INITIAL = {"SkythonTestQuest": {"Counter": 7, "Ratio": 0.5, "Flag": True, "Label": "initial label"},
           "SkythonTestQuestBase": {"BaseCounter": 3, "BaseLabel": "base label"}}


def cell_of(game, ref) -> str:
    c = game.call_method(ref, "ObjectReference", "GetParentCell")
    return (c.form_key or "").lower() if c is not None else ""


def test_fresh_save_prefers_skythonbase(fresh_save, game):
    game.wait_ready(timeout=180)
    assert cell_of(game, game.resolve(form_id=PLAYER_ID)) == CELL
    assert "saveload: load started skythonbase" in log_text()


def test_skythonbase_is_pristine(game):
    load_save(game, "skythonbase")
    game.wait_ready(timeout=180)
    assert cell_of(game, game.resolve(form_id=PLAYER_ID)) == CELL
    npc = game.resolve(form_key=NPC)
    assert cell_of(game, npc) == CELL
    assert game.call_method(npc, "Actor", "IsDead") is False
    quest = game.resolve(form_key=QUEST)
    assert game.call_method(quest, "Quest", "IsRunning") is False
    assert game.call_method(quest, "Quest", "GetStage") == 0
    for script, props in INITIAL.items():
        for prop, value in props.items():
            assert game.get_property(quest, script, prop) == value, (script, prop)
    target = game.get_property(quest, "SkythonTestQuest", "Target")
    assert (target.form_key or "").lower() == NPC.lower()
    assert game.call_method(quest, "SkythonTestQuest", "GetCounter") == 7
    assert game.call_method(quest, "SkythonTestQuest", "GetBaseCounter") == 3
