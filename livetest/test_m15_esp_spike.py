"""Task 47 (the ESP/VMAD gate): a quest written by Mutagen, with a scripted alias and a QF_ stage fragment, binds in the
game. Needs testesp/spike/SkythonTest.esp in Data with its plugins.txt line (tools/box/box_plugins.sh add), Skython.pex
and the spike's two .pex in Data\\Scripts (owner decision 7), and s4test loaded. Never saves."""
import queue
import subprocess
import time

import pytest

from support import REPO

QUEST_KEY = "SkythonTest.esp|0x000800"
FRAGMENT_SCRIPT = "QF_SkythonTestQuest_01000800"
PLAYER_ID = 0x14


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


@pytest.fixture
def quest(game):
    return game.resolve(form_key=QUEST_KEY)


@pytest.fixture
def channel(game):
    q = queue.Queue()
    sub = game.on_channel("skython_test", q.put)
    yield q
    sub.close()


def wait_payload(q, payload, timeout=15.0):
    deadline, seen = time.monotonic() + timeout, []
    while (left := deadline - time.monotonic()) > 0:
        try:
            ev = q.get(timeout=left)
        except queue.Empty:
            break
        seen.append(ev.data.get("payload"))
        if ev.data.get("payload") == payload:
            return ev
    pytest.fail(f"no channel event with payload {payload!r} within {timeout} s (saw {seen})")


def test_plugin_loaded_and_quest_resolves(game, quest):
    assert quest.type == "Quest"
    assert quest.form_key.lower() == QUEST_KEY.lower()
    assert game.call("Game", "IsPluginInstalled", "SkythonTest.esp") is True


def test_console_setstage_runs_the_fragment(channel):
    r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "setstage SkythonTestQuest 10"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    wait_payload(channel, "spike_stage_10")


def test_call_method_setstage_runs_the_fragment(game, quest, channel):
    assert game.call_method(quest, "Quest", "SetStage", 10) is True   # AllowRepeatedStages: runs again
    wait_payload(channel, "spike_stage_10")
    assert game.call_method(quest, "Quest", "GetStage") == 10


def test_fragment_script_is_attached_under_its_name(game, quest):
    # S7c route: dispatch by the attached script's own class name works only if the VMAD attached it to the quest
    game.call_method(quest, "Quest", "SetStage", 10)
    assert game.call_method(quest, FRAGMENT_SCRIPT, "GetStage") == 10


def test_alias_script_is_bound(game, quest):
    if not game.call_method(quest, "Quest", "IsRunning"):
        assert game.call_method(quest, "Quest", "Start") is True
    alias = game.alias(quest, 0)
    player = game.resolve(form_id=PLAYER_ID)
    game.call_method(alias, "ReferenceAlias", "ForceRefTo", player)
    try:
        # Gate criteria: dispatch by the alias script's own class name (S7c route) and ReferenceAlias calls, which an
        # unscripted alias answers with type_mismatch (R45). The Tag read is extra: alias property reads are Task 51's.
        assert game.call_method(alias, "SkythonTestAlias", "GetOwningQuest").form_id == quest.form_id
        assert game.call_method(alias, "ReferenceAlias", "GetReference").form_id == PLAYER_ID
        assert game.get_property(alias, "SkythonTestAlias", "Tag") == 0
    finally:
        game.call_method(alias, "ReferenceAlias", "Clear")
