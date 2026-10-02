"""The full SkythonTest.esp loads and holds what the ESP-based live tests expect. Needs testesp/SkythonTest.esp and
testesp/compiled/*.pex on the test machine, the plugins.txt line (tools/box/box_plugins.sh add) and s4test
loaded. The last test moves the player into SkythonTestCell with `coc`. Nothing is saved."""
import queue
import subprocess
import time

import pytest

from skython import errors
from support import REPO

ESP = "SkythonTest.esp"
CHANNEL = "skython_test"  # the channel SkythonTestQuest sends on
QUEST, LOCATION, CELL = f"{ESP}|0x000800", f"{ESP}|0x000801", f"{ESP}|0x000802"
NPC, MARKER = f"{ESP}|0x000804", f"{ESP}|0x000805"
PLAYER_ID = 0x14


def key(form) -> str:
    return (form.form_key or "").lower() if form is not None else ""


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


@pytest.fixture
def quest(game):
    return game.resolve(form_key=QUEST)


def test_plugin_and_records_resolve(game):
    assert game.call("Game", "IsPluginInstalled", ESP) is True
    for k, kind in [(QUEST, "Quest"), (LOCATION, "Location"), (CELL, "Cell"), (NPC, "Actor"), (MARKER, "ObjectReference")]:
        f = game.resolve(form_key=k)
        assert key(f) == k.lower() and f.type == kind, (k, f)
    assert key(game.resolve(editor_id="SkythonTestQuest")) == QUEST.lower()


def test_quest_scripts_are_attached(game, quest):
    # The fixed initial values the test ESP sets prove both scripts are attached and initialised.
    assert game.call_method(quest, "SkythonTestQuest", "GetCounter") == 7
    assert game.call_method(quest, "SkythonTestQuest", "GetBaseCounter") == 3
    assert game.call_method(quest, "SkythonTestQuest", "GetRatio") == 0.5
    assert game.call_method(quest, "SkythonTestQuest", "GetFlag") is True


def test_stage_fragments_send_on_skython_test(game, quest):
    q = queue.Queue()
    sub = game.on_channel(CHANNEL, q.put)
    try:
        for stage in (10, 20, 200):
            assert game.call_method(quest, "Quest", "SetStage", stage) is True
            # The engine's string pool may change case: compare case-insensitively.
            assert q.get(timeout=15).data["payload"].lower() == f"stage_{stage}"
    finally:
        sub.close()


def test_aliases_carry_their_scripts(game, quest):
    if not game.call_method(quest, "Quest", "IsRunning"):
        assert game.call_method(quest, "Quest", "Start") is True
    player = game.resolve(form_id=PLAYER_ID)
    for alias_id in (0, 1, 2):
        alias = game.alias(quest, alias_id)
        game.call_method(alias, "ReferenceAlias", "ForceRefTo", player)
        try:
            assert game.get_property(alias, "SkythonTestAlias", "Tag") == 5
        finally:
            game.call_method(alias, "ReferenceAlias", "Clear")
    place = game.alias(quest, 3, "LocationAlias")  # the default kind is ReferenceAlias: type_mismatch on alias 3
    game.call_method(place, "LocationAlias", "ForceLocationTo", game.resolve(form_key=LOCATION))
    try:
        assert game.get_property(place, "SkythonTestLocationAlias", "Tag") == 6
    finally:
        game.call_method(place, "LocationAlias", "Clear")


def parent_cell(game, ref) -> str:
    try:
        return key(game.call_method(ref, "ObjectReference", "GetParentCell"))
    except errors.NotReady:
        return ""  # the cell is loading


def test_coc_into_the_test_cell(game):
    r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc SkythonTestCell"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    player = game.resolve(form_id=PLAYER_ID)
    deadline = time.monotonic() + 180
    while parent_cell(game, player) != CELL.lower():
        assert time.monotonic() < deadline, "the player never arrived in SkythonTestCell"
        time.sleep(1)
    game.wait_ready(timeout=120)
    npc = game.resolve(form_key=NPC)
    assert parent_cell(game, npc) == CELL.lower()
    assert game.call_method(npc, "ObjectReference", "Is3DLoaded") is True
    assert game.call_method(npc, "Actor", "IsDead") is False
    assert game.call_method(npc, "Actor", "IsEssential") is False  # later live tests kill it
    # The name comes from the WEFarmerMale template (TemplateFlag.BaseData), not from SkythonTestNPCBase: record it.
    print("SkythonTestNPC display name:", game.call_method(npc, "ObjectReference", "GetDisplayName"))
