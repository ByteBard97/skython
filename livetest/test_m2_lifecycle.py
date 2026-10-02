"""Spec 8 save/load lifecycle on new throwaway saves (Task 42), Review Focus 3 and 5, and the PackHandle/GetAlias
save-persistence measurement (record only, R45 stands). Loads only s4test and the saves this module makes."""
import json
import queue
import subprocess
import time

import pytest

from skython import errors
from skython.values import Alias, Form
from support import (PLAYER, REPO, load_save, log_text, new_save_name, spy_env, translate_player, wait_save_file)

IRON_SWORD = "Skyrim.esm|0x012EB7"
GLASS_BOW = "Skyrim.esm|0x0139A5"
# Cold base forms: no bound script object on a fresh s4test load. The Steel War Axe (0x013983) and Iron Dagger (0x01397E)
# the plan named read "bound before call: true" on their first touch (live, Task 42): vanilla script properties point at
# them, so the VM binds them at load. A probe of 30 base forms picked these two, both "false" on a fresh s4test load.
STEEL_MACE = "Skyrim.esm|0x013988"      # the cold form, bound before the save
IRON_MACE = "Skyrim.esm|0x013982"       # the control: a second cold form, first touched only after the save
STATE: dict = {}
RESULT_FILE = REPO / "livetest" / "results" / "binding_persistence.json"


def place(game, base_key: str, persist: bool) -> Form:
    return game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, persist, False)


def save_new(game, tag: str) -> str:
    name = new_save_name(tag)
    assert game.save(name) == name
    wait_save_file(name)
    return name


def take(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def coc_away(game) -> None:
    """Move the player indoors with a coc (a loading screen, not a save load) so the next save load attaches the
    exterior cells again: a save loaded in the cell the player stands in raised no cell_attach at all (live, Task 42)."""
    closed: queue.Queue = queue.Queue()
    with game.on("menu_close", {"menu": ["Loading Menu"]}, closed.put):
        subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc WhiterunBanneredMare"], check=True,
                       capture_output=True, text=True, timeout=90)
        assert take(closed, 120.0) is not None, "no Loading Menu close within 120 s after coc"
    game.wait_ready()


def test_references_and_subscriptions_across_a_save_and_load(game):
    st0 = load_save(game, "s4test")
    (npc_hex,) = spy_env("SPY_S4_REF")
    npc = Form(form_id=int(npc_hex, 16))
    item = place(game, IRON_SWORD, persist=True)                          # abForcePersist = true
    loose = place(game, IRON_SWORD, persist=False)
    acts: queue.Queue = queue.Queue()
    with game.on("activate", {"target": [loose]}, acts.put):
        game.call_method(loose, "ObjectReference", "Activate", PLAYER, False)  # picks it up: the ref goes away
        old_event = take(acts, 10.0)
    assert old_event is not None and old_event.epoch == st0["epoch"]
    name_a = save_new(game, "t42a")
    STATE.update(a=name_a, item=item)
    coc_away(game)

    loading, loaded, attach = queue.Queue(), queue.Queue(), queue.Queue()
    subs = [game.on("menu_open", {"menu": ["Loading Menu"]}, loading.put), game.on_game_loaded(loaded.put),
            game.on("cell_attach", None, attach.put, max_queue=4096)]
    try:
        suppressed_before = game.state()["diagnostics"]["events_suppressed_total"]
        st1 = load_save(game, name_a)
        assert st1["epoch"] == st0["epoch"] + 1
        # Review Focus 5: subscriptions survive the load and later frames carry the new epoch.
        gl = take(loaded, 30.0)
        assert gl is not None and gl.epoch == st1["epoch"] and gl.data["new_game"] is False
        lm = take(loading, 5.0)
        assert lm is not None and lm.data["menu"] == "Loading Menu" and lm.suppressed == 0  # deliver_while_loading
        # Review Focus 3: cell_attach raised during the load was not queued; it is counted and reported on the next
        # delivered cell_attach. Glide 1.5 cells (no Loading Menu) if nothing attached by itself.
        rose = game.state()["diagnostics"]["events_suppressed_total"] - suppressed_before
        first = take(attach, 5.0)
        if first is None:
            translate_player(game, 6144.0, 0.0, 600.0)
            first = take(attach, 10.0)
        print(f"load: epoch {st0['epoch']}->{st1['epoch']} suppressed_rise={rose} "
              f"first_cell_attach suppressed={first.suppressed if first else None} epoch={first.epoch if first else None}")
        assert rose > 0, "no event was suppressed during the load (cell_attach should have been)"
        assert first is not None and first.suppressed > 0 and first.epoch == st1["epoch"]
    finally:
        for s in subs:
            s.close()
    # The persistent runtime item (fingerprint matches) and the vanilla NPC still work.
    assert game.call_method(item, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
    assert isinstance(game.call_method(npc, "Form", "GetName"), str)
    # A reference from an old-epoch event: works, or raises StaleReference / NotFound; never a crash.
    try:
        base = game.call_method(old_event.data["target"], "ObjectReference", "GetBaseObject")
        outcome = f"works ({base.form_key})"
    except (errors.StaleReference, errors.NotFound) as e:
        outcome = type(e).__name__
    print(f"old-epoch event reference after the load: {outcome}")
    STATE["old_event_ref"] = outcome


def test_runtime_reference_from_another_save_is_stale(game):
    assert "a" in STATE, "needs the save from the previous test"
    load_save(game, "s4test")
    bow = place(game, GLASS_BOW, persist=True)
    name_b = save_new(game, "t42b")
    same_id = bow.form_id == STATE["item"].form_id
    print(f"runtime ids: iron sword in A {STATE['item'].form_id:#x}, glass bow in B {bow.form_id:#x}, same={same_id}")
    load_save(game, STATE["a"])
    with pytest.raises((errors.StaleReference, errors.NotFound)) as e_in_a:
        game.call_method(bow, "ObjectReference", "GetBaseObject")
    load_save(game, name_b)
    with pytest.raises((errors.StaleReference, errors.NotFound)) as e_in_b:
        game.call_method(STATE["item"], "ObjectReference", "GetBaseObject")
    if same_id:  # the id names a different object in the other save: exactly the stale case
        assert e_in_a.type is errors.StaleReference and e_in_b.type is errors.StaleReference
    print(f"bow from B used in A: {e_in_a.type.__name__}; sword from A used in B: {e_in_b.type.__name__}")


def _owning_quest(game, alias: Alias) -> str:
    try:
        q = game.call_method(alias, "Alias", "GetOwningQuest")
        return f"works ({q.form_id:#x})"
    except errors.TypeMismatch:
        return "type_mismatch"


def _bound_lines(desc_part: str) -> list[str]:
    return [l for l in log_text().splitlines() if "bound before call:" in l and desc_part in l]


def test_packhandle_and_getalias_binding_across_a_save(game):
    """Record only (owner question from Task 29; R45 stands either way)."""
    quest_hex, alias_s = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")
    load_save(game, "s4test")
    quest = game.resolve(form_id=int(quest_hex, 16))
    alias = game.alias(quest, int(alias_s))
    cold = game.form(STEEL_MACE)
    if _owning_quest(game, alias) != "type_mismatch":
        pytest.fail("the plain alias is already bound on a fresh s4test load: STOP and ask the owner")
    assert isinstance(game.call_method(quest, "Quest", "GetAlias", int(alias_s)), Alias)  # binds an `Alias` object (S7g)
    before_save = _owning_quest(game, alias)
    assert before_save.startswith("works"), before_save
    assert isinstance(game.call_method(cold, "Form", "GetName"), str)       # cold: PackHandle binds it
    name_c = save_new(game, "t42bind")
    # Control: a second cold form bound only AFTER the save. Its binding is not in the save, so after the load its
    # line must read false. If it reads true, the VM kept bindings in memory across the load and the in-session cold
    # result says nothing about the save: "inconclusive" (the fresh-launch run below decides).
    control_form = game.form(IRON_MACE)
    assert isinstance(game.call_method(control_form, "Form", "GetName"), str)
    load_save(game, name_c)
    after_load = _owning_quest(game, alias)                                # first call after the load
    game.call_method(cold, "Form", "GetName")
    game.call_method(control_form, "Form", "GetName")
    cold_lines = _bound_lines("0x013988") or _bound_lines("013988")
    control_lines = _bound_lines("0x013982") or _bound_lines("013982")
    control_ok = len(control_lines) >= 2 and control_lines[-1].endswith("bound before call: false")
    result = {"save": name_c, "alias": {"quest": quest_hex, "alias_id": int(alias_s), "before_save": before_save,
                                        "after_load": after_load},
              "cold_form": {"form": STEEL_MACE, "log": cold_lines},
              "control": {"form": IRON_MACE, "log": control_lines,
                          "in_session_result": "valid" if control_ok else "inconclusive"},
              "fresh_launch": None,                                      # filled by test_binding_after_a_fresh_launch
              "old_epoch_event_reference": STATE.get("old_event_ref"), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    RESULT_FILE.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    # The cold form must show "false" before the save and one more line after the load (a new epoch).
    assert len(cold_lines) >= 2 and cold_lines[0].endswith("bound before call: false"), cold_lines
    assert len(control_lines) >= 2 and control_lines[0].endswith("bound before call: false"), control_lines


def test_binding_after_a_fresh_launch(game):
    """The definitive measurement: a new game process that loads the t42bind save from the main menu (Step 5b), so
    nothing can survive in memory. Runs only when SKYTHON_T42_FRESH names that save; loads nothing itself."""
    import os
    name_c = os.environ.get("SKYTHON_T42_FRESH")
    if not name_c:
        pytest.skip("Step 5b only: set SKYTHON_T42_FRESH to the t42bind save loaded at launch")
    quest_hex, alias_s = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")
    quest = game.resolve(form_id=int(quest_hex, 16))
    alias = game.alias(quest, int(alias_s))
    first = _owning_quest(game, alias)                                     # the very first call of this process
    assert isinstance(game.call_method(game.form(STEEL_MACE), "Form", "GetName"), str)
    cold_lines = _bound_lines("0x013988") or _bound_lines("013988")
    result = json.loads(RESULT_FILE.read_text())
    assert result["save"] == name_c, (result["save"], name_c)
    result["fresh_launch"] = {"alias_first_call": first, "cold_form_log": cold_lines,
                              "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    RESULT_FILE.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["fresh_launch"], indent=2))
    assert cold_lines, "no 'bound before call' line for the cold form after the fresh launch"
