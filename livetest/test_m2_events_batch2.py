"""M2 batch 2 and lifecycle events in the game (Task 40). s4test, never saved."""
import queue
import subprocess
import threading
import time

import pytest

from skython.values import to_wire
from smoke import command
from support import (PLAYER, REPO, box_menu, cleanup, neutral_fight, raw_connect, read_event, rpc, spawn, subscribe,
                     translate_player)

IRON_SWORD = "Skyrim.esm|0x012EB7"
GOLD = "Skyrim.esm|0x00000F"
WOLF = "Skyrim.esm|0x023ABE"                 # NPC_ EncWolf
BANNERED_MARE_LOCATION = 0x0001F86D          # LCTN WhiterunBanneredMareLocation (cell WhiterunBanneredMare 0x1605E)


def collect(game, event, flt, **kw):
    """A queue and the Subscription that fills it."""
    got: queue.Queue = queue.Queue()
    return got, game.on(event, flt, got.put, **kw)


def take(q: queue.Queue, n: int, timeout: float) -> list:
    out, deadline = [], time.monotonic() + timeout
    while len(out) < n and (left := deadline - time.monotonic()) > 0:
        try:
            out.append(q.get(timeout=left))
        except queue.Empty:
            break
    return out


def diagnostics(game) -> dict:
    return game.state()["diagnostics"]


def test_filters_required_for_hit_and_container_changed(session):
    s = raw_connect(session)
    try:
        for i, ev in enumerate(("hit", "container_changed"), start=1):
            r = rpc(s, "events.subscribe", {"event": ev}, req_id=i)
            assert r["ok"] is False and r["error"]["code"] == "bad_request", r
    finally:
        s.close()


def test_equip_and_unequip(game):
    sword = game.form(IRON_SWORD)
    if game.call_method(PLAYER, "Actor", "IsEquipped", sword):
        pytest.fail("STOP: the player already has an Iron Sword equipped on s4test; ask the owner (pick no other item yourself)")
    print(f"equip: iron swords carried before the test = {game.call_method(PLAYER, 'ObjectReference', 'GetItemCount', sword)}")
    q, sub_e = collect(game, "equip", {"actor": [PLAYER], "item": [sword]})
    sub_u = game.on("unequip", {"actor": [PLAYER], "item": [sword]}, q.put)
    game.call_method(PLAYER, "ObjectReference", "AddItem", sword, 1, True)
    try:
        game.call_method(PLAYER, "Actor", "EquipItem", sword, False, True)
        game.call_method(PLAYER, "Actor", "UnequipItem", sword, False, True)
        got = take(q, 2, 10.0)
        assert [e.name for e in got] == ["equip", "unequip"], got
        for e in got:
            assert e.data["actor"].form_id == 0x14 and e.data["item"].form_key == IRON_SWORD
    finally:
        sub_e.close()
        sub_u.close()
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", sword, 1, True, None)


def test_container_changed_both_directions(game):
    gold = game.form(GOLD)
    q, sub_in = collect(game, "container_changed", {"new_container": [PLAYER], "item": [gold]})
    sub_out = game.on("container_changed", {"old_container": [PLAYER], "item": [gold]}, q.put)
    try:
        game.call_method(PLAYER, "ObjectReference", "AddItem", gold, 5, True)
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", gold, 5, True, None)
        got = take(q, 2, 10.0)
        assert len(got) == 2, got
        added, removed = got
        assert added.data["new_container"].form_id == 0x14 and added.data["old_container"] is None and added.data["count"] == 5
        assert removed.data["old_container"].form_id == 0x14 and removed.data["new_container"] is None
        assert added.data["item"].form_key == GOLD
    finally:
        sub_in.close()
        sub_out.close()


def test_activate(game):
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(IRON_SWORD), 1, False, False)
    q, sub = collect(game, "activate", {"target": [ref]})
    try:
        game.call_method(ref, "ObjectReference", "Activate", PLAYER, False)  # picks the sword up
        got = take(q, 1, 10.0)
        assert got and got[0].data["target"].form_id == ref.form_id and got[0].data["activator"].form_id == 0x14, got
    finally:
        sub.close()
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", game.form(IRON_SWORD), 1, True, None)


def test_menu_open_and_close(game):
    q, sub_o = collect(game, "menu_open", {"menu": ["inventorymenu"]})  # case-insensitive string filter
    sub_c = game.on("menu_close", {"menu": ["InventoryMenu"]}, q.put)
    opened = False
    try:
        opened = True  # set before the call: a failure partway through may still have opened it
        box_menu("open", "InventoryMenu")
        time.sleep(1.0)
        box_menu("close", "InventoryMenu")
        opened = False
        got = take(q, 2, 15.0)
        assert [(e.name, e.data["menu"]) for e in got] == [("menu_open", "InventoryMenu"), ("menu_close", "InventoryMenu")], got
    finally:
        if opened:  # never leave the game paused in the inventory
            try:
                box_menu("close", "InventoryMenu")
            except Exception as exc:  # noqa: BLE001
                print(f"menu: cleanup close failed: {exc!r}")
        sub_o.close()
        sub_c.close()


def test_cell_attach_and_detach_without_a_loading_screen(game):
    if game.call_method(PLAYER, "ObjectReference", "IsInInterior"):
        pytest.fail("s4test starts in an interior: this test needs an exterior; STOP and ask the owner")
    attach: queue.Queue = queue.Queue()
    detach: queue.Queue = queue.Queue()
    loading: queue.Queue = queue.Queue()
    subs = [game.on("cell_attach", None, attach.put, max_queue=4096), game.on("cell_detach", None, detach.put, max_queue=4096),
            game.on("menu_open", {"menu": ["Loading Menu"]}, loading.put)]
    try:
        x, y, z = translate_player(game, 6144.0, 0.0, 600.0)
        game.call_method(PLAYER, "ObjectReference", "TranslateTo", x, y, z, 0.0, 0.0, 0.0, 3000.0, 0.0)  # glide back
        time.sleep(6.0)
        game.call_method(PLAYER, "ObjectReference", "StopTranslation")
        a, d = take(attach, 1, 5.0), take(detach, 1, 5.0)
        print(f"cell events: attach>={attach.qsize() + len(a)} detach>={detach.qsize() + len(d)} loading_menus={loading.qsize()}")
        assert loading.empty(), "TranslateTo opened a Loading Menu: record it in RESULTS and ask the owner"
        assert a and d, "no cell_attach or no cell_detach after moving 1.5 cells and back"
        assert a[0].data["reference"].form_id and a[0].suppressed == 0
    finally:
        for s in subs:
            s.close()


def test_hit_between_two_spawned_actors(game):
    a, b = spawn(game, WOLF, 1200.0), spawn(game, WOLF, 1400.0)
    # target AND aggressor (filters AND across fields), both directions: a hit by or on a guard or another creature
    # never matches, and either wolf may land the first hit.
    q, sub = collect(game, "hit", {"target": [a, b], "aggressor": [a, b]})
    try:
        neutral_fight(game, a, b)
        got = take(q, 1, 20.0)
        if not got:  # they dropped out of the forced fight: once more, frenzied (record which setting fought)
            neutral_fight(game, a, b, aggression=3.0)
            got = take(q, 1, 20.0)
            print("hit: needed aggression 3")
        assert got, "no hit between the two wolves"
        target, aggressor = got[0].data["target"].form_id, got[0].data["aggressor"].form_id
        assert {target, aggressor} == {a.form_id, b.form_id} and target != aggressor, got
        assert all(isinstance(got[0].data[k], bool) for k in ("power_attack", "sneak_attack", "bash_attack", "blocked"))
    finally:
        sub.close()
        cleanup(game, a, b)
        game.call_method(PLAYER, "Actor", "SetGhost", False)


def test_hit_flood_never_stalls_the_game(game, session):
    """Review Focus 1: a drained subscription (max_queue 16) and an undrained in-process one (max_queue 4) during
    30 s of combat; the held queue stays bounded and call latency (one frame per call) does not move. The held one
    uses max_queue 4 rather than 16 so that two wolves (about one hit a second) certainly overflow it.
    The baseline (bench p50, drain meter) is taken while the wolves already fight and nothing is subscribed, so their
    AI cost is in both numbers and only the subscriptions differ. max_task_drain_ms is the launch maximum (it already
    holds the class-table build at the main menu), so the check is that it does not rise above max(baseline, 5 ms).
    Sinks run outside SKSE tasks, so the drain meter never sees their cost; it would show in the bench latency."""
    a, b = spawn(game, WOLF, 1200.0), spawn(game, WOLF, 1400.0)
    s = raw_connect(session)
    frames: list = []
    stop = threading.Event()
    # AND across fields: a hit by or on a guard or anyone else never matches. Both directions, because near Whiterun
    # the wolves also fight guards and townsfolk, and one direction alone came back empty in a 35 s run (Task 40 live).
    flt = {"target": [to_wire(a), to_wire(b)], "aggressor": [to_wire(a), to_wire(b)]}
    held = None
    try:
        neutral_fight(game, a, b)
        time.sleep(5.0)
        if not game.call_method(a, "Actor", "IsInCombat"):  # same fallback as the single-hit test
            neutral_fight(game, a, b, aggression=3.0)
            print("flood: needed aggression 3 for the baseline")
            time.sleep(5.0)
        targets = [game.call_method(x, "Actor", "GetCombatTarget") for x in (a, b)]
        print(f"flood: combat targets at baseline {[t.form_id if t is not None else None for t in targets]} "
              f"(a={a.form_id} b={b.form_id})")
        before = command("bench", calls=100)["p50_ms"]          # wolves fighting, nothing subscribed
        drain_before = diagnostics(game)["max_task_drain_ms"]
        # The held one first: then its window covers the drained one's, so its last seq is at least every hit the
        # drained one accepted (the smoke command takes a second or two; created second, it missed early hits).
        held = command("hold_sub", event="hit", filter=flt, max_queue=4)
        assert "consumer" in held, held
        subscribe(s, "hit", flt, req_id=1, max_queue=16)

        def reader():
            while not stop.is_set():
                if (f := read_event(s, 0.5)) is not None:
                    frames.append(f)
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        time.sleep(10.0)
        if not frames:  # they dropped out of the fight: once more, frenzied
            neutral_fight(game, a, b, aggression=3.0)
            print("flood: needed aggression 3")
        during = [command("bench", calls=100)["p50_ms"] for _ in range(5)]  # about 25 s of calls while they fight
        stop.set()
        t.join(5)
        released = command("release_sub", consumer=held["consumer"])
        held = None  # released: nothing for finally to do
        diag = diagnostics(game)
        print(f"flood: frames={len(frames)} held={released} p50 before={before} during={during} "
              f"drain before={drain_before} after={diag['max_task_drain_ms']} dropped_total={diag['events_dropped_total']}")
        assert frames, "no hit events during 30 s of combat"
        assert released["count"] <= 4
        drained_total = len(frames) + sum(f.get("dropped", 0) for f in frames)  # every hit the drained sub accepted
        if released["seqs"]:
            # Both subscriptions see the same hits; the held one also counts hits after the reader stopped.
            assert released["seqs"][-1] >= drained_total and released["first_dropped"] == released["seqs"][0] - 1
        assert max(during) <= before * 1.15 + 5.0, (before, during)
        # Task 31 baseline: 2.25 ms at a 1024-request burst; the launch maximum may already be higher (class table).
        assert diag["max_task_drain_ms"] <= max(drain_before, 5.0), (drain_before, diag["max_task_drain_ms"])
    finally:
        stop.set()
        if held and "consumer" in held:  # an assert or error before the release: never leave it subscribed
            try:
                command("release_sub", consumer=held["consumer"])
            except Exception as exc:  # noqa: BLE001
                print(f"flood: cleanup release_sub failed: {exc!r}")
        s.close()
        cleanup(game, a, b)
        game.call_method(PLAYER, "Actor", "SetGhost", False)


def test_coc_delivers_player_location_and_cell_events(game):
    """Runs last: it moves the player indoors. A coc shows a Loading Menu but is not a save load, so nothing is
    suppressed (ruling R60: the hub suppresses only from kPreLoadGame/kNewGame to the game_loaded push).
    player_location must arrive with the location Actor.GetCurrentLocation reports after the move, and cell_attach /
    cell_detach must arrive too, none of them with suppressed set."""
    before = game.call_method(PLAYER, "Actor", "GetCurrentLocation")  # a Location Form, or None in the wilderness
    loc_q, sub = collect(game, "player_location", None)
    attach, sub_a = collect(game, "cell_attach", None, max_queue=4096)
    detach, sub_d = collect(game, "cell_detach", None, max_queue=4096)
    loaded: queue.Queue = queue.Queue()
    sub_l = game.on("menu_close", {"menu": ["Loading Menu"]}, loaded.put)
    suppressed_before = diagnostics(game)["events_suppressed_total"]
    try:
        subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc WhiterunBanneredMare"], check=True,
                       capture_output=True, text=True, timeout=90)
        assert take(loaded, 1, 120.0), "no Loading Menu close within 120 s after coc"
        game.wait_ready(60.0)
        after = game.call_method(PLAYER, "Actor", "GetCurrentLocation")
        got = take(loc_q, 8, 10.0)   # one change, or several if the move passes through more than one location
        a, d = take(attach, 1, 10.0), take(detach, 1, 10.0)
        rose = diagnostics(game)["events_suppressed_total"] - suppressed_before
        fid = lambda f: f.form_id if f is not None else None  # noqa: E731
        print(f"player_location: before={fid(before)} after={fid(after)} "
              f"delivered={[(fid(e.data['old_location']), fid(e.data['new_location'])) for e in got]} "
              f"cell attach>={len(a) + attach.qsize()} detach>={len(d) + detach.qsize()} suppressed_rise={rose}")
        assert fid(after) == BANNERED_MARE_LOCATION and fid(before) != fid(after), (before, after)
        assert got, "no player_location across the coc: a loading screen must not suppress it (R60)"
        assert fid(got[0].data["old_location"]) == fid(before)
        assert fid(got[-1].data["new_location"]) == fid(after)
        assert all(e.suppressed == 0 for e in got)
        assert a and d, "no cell_attach or no cell_detach across the coc"
        assert a[0].suppressed == 0 and d[0].suppressed == 0
        assert rose == 0, f"events_suppressed_total rose by {rose} across a coc, which is not a save load (R60)"
    finally:
        for x in (sub, sub_a, sub_d, sub_l):
            x.close()
