"""The three engine probes on SkythonTest.esp: three <Alias=...> tags in one objective line, powerofthree's
SetObjectiveText with alias tags, and objective text plus alias fills across a save and reload. The tests check the
mechanics (fills, display state, save/load) and collect the evidence for the rendered text: HUD screenshots
(box_shot.sh) and the `sqo` console reply (box_console.sh). The verdict on the rendered text is made from that
evidence, not by a test. Writes out/probes/probes.json and out/probes/*.png. Each probe can run alone (-k probe2): the module
fixture `filled` makes the shared fills. Run with -s; probe 3 leaves one new save (new_save_name("t52"), recorded in
livetest/results/new_saves.txt for the removal list)."""
import json
import subprocess
import time

import pytest

import _testesp as t
from support import REPO, new_save_name, wait_save_file

pytestmark = pytest.mark.usefixtures("fresh_save")
OUT = REPO / "out" / "probes"
SHOT_TRIES = 3
RAW: dict = {}


def _tool(script: str, *args: str) -> str:
    r = subprocess.run([str(REPO / "tools" / "box" / script), *args], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"{script} {args} failed: {r.stdout}{r.stderr}"
    return r.stdout


def sqo() -> str:
    return _tool("box_console.sh", "sqo SkythonTestQuest")


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    OUT.mkdir(parents=True, exist_ok=True)
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-probes")
    g.wait_ready(timeout=180)
    yield g
    g.close()
    (OUT / "probes.json").write_text(json.dumps(RAW, indent=2, default=str))


@pytest.fixture(scope="module")
def esp(mgame):
    t.ensure_running(mgame, t.quest(mgame))
    h = t.discover(mgame)
    mgame.call_method(h.quest, "Quest", "SetActive", True)
    return h


@pytest.fixture(scope="module")
def filled(mgame, esp):
    """The fills every probe uses, made once per module (so `-k probe2` or `-k probe3` alone starts from them):
    alias 0 <- SkythonTestNPC, alias 1 <- an iron sword placed at the player, alias 3 <- SkythonTestLocation, and
    objective 10 displayed. Alias 2 stays empty."""
    sword = mgame.call_method(t.PLAYER, "ObjectReference", "PlaceAtMe", mgame.form(t.IRON_SWORD), 1, True, False)
    place = t.location(mgame)
    mgame.call_method(esp.ref_aliases[t.TARGET_ALIAS], "ReferenceAlias", "ForceRefTo", esp.npc)
    mgame.call_method(esp.ref_aliases[t.ITEM_ALIAS], "ReferenceAlias", "ForceRefTo", sword)
    mgame.call_method(esp.location_alias, "LocationAlias", "ForceLocationTo", place)
    mgame.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, True, True)
    return {"sword": sword, "place": place}


def show_and_shoot(game, esp, tag: str) -> list:
    """Force the objective banner and screenshot it, up to SHOT_TRIES times. Returns the PNG paths."""
    paths = []
    for i in range(SHOT_TRIES):
        game.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, False, False)
        game.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, True, True)
        png = OUT / f"{tag}_try{i + 1}.png"
        print(_tool("box_shot.sh", str(png)))
        paths.append(str(png))
        time.sleep(2)
    return paths


def fills(game, esp) -> list:
    """The three aliases the objective's tags name, in tag order: 0 SkythonTarget, 1 SkythonItem, 3 SkythonPlace."""
    out = []
    for i in (t.TARGET_ALIAS, t.ITEM_ALIAS):
        a = esp.ref_aliases[i]
        ref = game.call_method(a, "ReferenceAlias", "GetReference")
        name = game.call_method(ref, "ObjectReference", "GetDisplayName") if ref is not None else None
        out.append({"alias_id": i, "alias_name": t.ALIAS_NAMES[i],
                    "ref": None if ref is None else {"form_id": ref.form_id, "form_key": ref.form_key},
                    "display_name": name})
    loc = game.call_method(esp.location_alias, "LocationAlias", "GetLocation")
    out.append({"alias_id": t.PLACE_ALIAS, "alias_name": t.ALIAS_NAMES[t.PLACE_ALIAS],
                "ref": None if loc is None else {"form_id": loc.form_id, "form_key": loc.form_key},
                "display_name": game.call_method(loc, "Form", "GetName") if loc is not None else None})
    return out


def test_probe1_three_alias_tags_in_one_objective(game, esp, filled):
    sword, place = filled["sword"], filled["place"]
    got = fills(game, esp)
    assert [f["ref"]["form_id"] if f["ref"] else None for f in got] == [esp.npc.form_id, sword.form_id,
                                                                        place.form_id], got
    assert all(f["display_name"] for f in got), got        # every tag has a non-empty name to show
    shots = show_and_shoot(game, esp, "probe1")
    assert game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True
    RAW["probe1"] = {"objective": esp.objective, "fills": got, "sqo": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe1"], indent=2))


def test_probe2_po3_set_objective_text_with_alias_tags(game, esp, filled):
    names = [t.ALIAS_NAMES[i] for i in (t.TARGET_ALIAS, t.ITEM_ALIAS, t.PLACE_ALIAS)]
    text = "Probe two: " + " / ".join(f"<Alias={n}>" for n in names)
    game.call("PO3_SKSEFunctions", "SetObjectiveText", esp.quest, text, esp.objective)
    shots = show_and_shoot(game, esp, "probe2")
    assert game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True
    RAW["probe2"] = {"text_set": text, "fills": fills(game, esp), "sqo": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe2"], indent=2))


def test_probe3_objective_and_fills_survive_save_and_reload(game, esp, filled):
    save = new_save_name("t52")              # checks box_saves.sh count is 0; records it for the removal list
    RAW.setdefault("saves", []).append(save)
    before = fills(game, esp)
    epoch = game.state()["epoch"]
    assert game.save(save) == save           # the engine started the save
    wait_save_file(save)                     # the .ess can appear a moment after game.save answers
    st = game.load(save, wait=True)
    assert st["epoch"] > epoch, (epoch, st)
    after = fills(game, esp)
    displayed = game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective)
    text_after_load = sqo()                                    # before anything re-displays the objective
    shots = show_and_shoot(game, esp, "probe3")
    RAW["probe3"] = {"epoch_before": epoch, "epoch_after": st["epoch"], "fills_before": before,
                     "fills_after": after, "displayed_after_load": displayed, "sqo_after_load": text_after_load,
                     "sqo_after_redisplay": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe3"], indent=2))
    assert [f["ref"] for f in after] == [f["ref"] for f in before], (before, after)
    assert displayed is True
