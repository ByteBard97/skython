"""M2 batch 1 events in the game (Task 39): death/dying, quest stage/start/stop, mod events, the channel queue.
s4test loaded and never saved. Raw frames check the wire shape; Game.on checks the Python path over the bridge."""
import queue

import pytest

from skython import errors
from skython.values import Form, to_wire
from smoke import command
from support import raw_connect, read_event, read_events, rpc, subscribe

PLAYER = Form(form_id=0x14)
CHICKEN = "Skyrim.esm|0x0A91A0"       # NPC_ EncChicken (checked in Skyrim.esm, 2026-10-02)
# QUST PerkInvestor: Papyrus Start() starts it (no Story Manager event, ENAM empty), not start-game-enabled, not
# run-once, no aliases, one stage (10) with no fragment and no log entry; checked on s4test twice in one launch (Task 39 fix round 1)
QUEST = "Skyrim.esm|0x0ABD94"
CRIME_WHITERUN = "Skyrim.esm|0x0267EA"  # FACT CrimeFactionWhiterun: a chicken killed by the player near Whiterun may be a crime


def crime_gold(game) -> int:
    return game.call_method(game.form(CRIME_WHITERUN), "Faction", "GetCrimeGold")


def no_new_bounty(game, before: int) -> None:
    """Kill(player) makes the player the killer; if that counted as a crime the session is no longer clean."""
    after = crime_gold(game)
    if after > before:
        pytest.fail(f"STOP: the Whiterun bounty rose from {before} to {after} after Kill(player) on a chicken; "
                    "ask the owner (s4test is never saved, so only this session is affected)")


def spawn(game, base_key: str, dx: float = 600.0) -> Form:
    """A new actor from base_key, placed dx units from the player (never saved: s4test)."""
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, False, False)
    game.call_method(ref, "ObjectReference", "MoveTo", PLAYER, dx, 0.0, 0.0, True)
    return ref


def cleanup(game, *refs: Form) -> None:
    for r in refs:
        try:
            game.call_method(r, "ObjectReference", "Disable", False)
            game.call_method(r, "ObjectReference", "Delete")
        except errors.SkythonError:
            pass


def frames_for(frames: list[dict], sub: int) -> list[dict]:
    return [f for f in frames if f["sub"] == sub]


def test_death_and_dying_with_victim_filter(game, session):
    """Kill(player): both frames carry killer = the player (index Contracts: Task 39 fills killer from
    TESDeathEvent::actorKiller; Tasks 53-55 rely on it). A null killer here is a sink defect, never loosened."""
    bounty = crime_gold(game)
    a, b = spawn(game, CHICKEN, 600.0), spawn(game, CHICKEN, -600.0)
    s = raw_connect(session)
    try:
        dying = subscribe(s, "dying", {"victim": [to_wire(a)]}, req_id=1)
        death = subscribe(s, "death", {"victim": [to_wire(a)]}, req_id=2)
        epoch = rpc(s, "game.state", req_id=3)["result"]["epoch"]
        game.call_method(a, "Actor", "Kill", PLAYER)
        game.call_method(b, "Actor", "Kill", PLAYER)  # filtered out: must never arrive
        frames = read_events(s, 2, timeout=10.0) + read_events(s, 5, timeout=3.0)
        assert [f["event"] for f in frames_for(frames, dying)] == ["dying"], frames
        assert [f["event"] for f in frames_for(frames, death)] == ["death"], frames
        for f in frames:
            assert f["type"] == "event" and f["seq"] == 1 and f["epoch"] == epoch
            assert f["data"]["victim"]["form_id"] == a.form_id and f["data"]["victim"]["type"] == "Actor"
            assert f["data"]["killer"] is not None and f["data"]["killer"]["form_id"] == PLAYER.form_id  # Kill(player)
            assert "dropped" not in f and "suppressed" not in f
        no_new_bounty(game, bounty)
    finally:
        s.close()
        cleanup(game, a, b)


def test_death_killer_is_null_for_kill_none(game, session):
    c = spawn(game, CHICKEN, 600.0)
    s = raw_connect(session)
    try:
        death = subscribe(s, "death", {"victim": [to_wire(c)]}, req_id=1)
        game.call_method(c, "Actor", "Kill", None)
        frames = frames_for(read_events(s, 1, timeout=10.0), death)
        assert len(frames) == 1 and frames[0]["data"]["killer"] is None, frames  # Kill(None): no killer
    finally:
        s.close()
        cleanup(game, c)


def test_quest_start_stage_stop(game):
    quest = game.form(QUEST)
    if game.call_method(quest, "Quest", "IsRunning") or game.call_method(quest, "Quest", "IsCompleted"):
        pytest.fail("PerkInvestor is running or completed on this save: STOP and ask the owner")
    got: queue.Queue = queue.Queue()
    subs = [game.on(name, {"quest": [quest]}, got.put) for name in ("quest_start", "quest_stage", "quest_stop")]
    try:
        assert game.call_method(quest, "Quest", "Start") is True            # Start first: SetStage would also start it
        assert game.call_method(quest, "Quest", "SetStage", 10) is True
        game.call_method(quest, "Quest", "Stop")
        seen = [got.get(timeout=10.0) for _ in range(3)]
        assert [e.name for e in seen] == ["quest_start", "quest_stage", "quest_stop"], seen
        assert all(e.data["quest"].form_id == quest.form_id for e in seen)
        assert seen[1].data["stage"] == 10
    finally:
        for sub in subs:
            sub.close()


def test_mod_event_fields_and_case_insensitive_name_filter(game, session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "mod_event", {"name": ["SKYTHON_LIVE"]}, req_id=1)
        game.call_method(PLAYER, "Form", "SendModEvent", "skython_live", "s", 4.5)
        f = read_event(s, 10.0)
        assert f and f["sub"] == sub, f
        d = f["data"]
        # Game strings come back in the casing the engine's string pool already holds (README): "s" came back "S".
        assert (d["name"].lower(), d["str_arg"].lower(), d["num_arg"]) == ("skython_live", "s", 4.5)
        assert d["sender"]["form_id"] == 0x14
        game.call_method(PLAYER, "Form", "SendModEvent", "someone_else", "", 0.0)  # filtered out
        assert read_event(s, 3.0) is None
    finally:
        s.close()


def test_unsubscribe_stops_delivery(game, session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "mod_event", {"name": ["skython_live"]}, req_id=1)
        r = rpc(s, "events.unsubscribe", {"sub": sub}, req_id=2)
        assert r["ok"] is True and r["result"] is None
        game.call_method(PLAYER, "Form", "SendModEvent", "skython_live", "after", 1.0)
        assert read_event(s, 3.0) is None
        assert rpc(s, "events.unsubscribe", {"sub": sub}, req_id=3)["error"]["code"] == "not_found"
    finally:
        s.close()


def test_channel_subscription_is_accepted(session):
    s = raw_connect(session)
    try:
        sub = subscribe(s, "channel", {"channel": ["skython_live"]}, req_id=1)
        assert rpc(s, "events.unsubscribe", {"sub": sub}, req_id=2)["ok"] is True
    finally:
        s.close()


def test_hub_totals_in_diagnostics(session):
    s = raw_connect(session)
    try:
        d = rpc(s, "game.state", req_id=1)["result"]["diagnostics"]
        for key in ("event_consumers", "event_subscriptions", "events_dropped_total", "events_suppressed_total"):
            assert isinstance(d[key], int) and d[key] >= 0, (key, d)
        assert d["event_consumers"] >= 1  # this connection's consumer
    finally:
        s.close()


def test_in_process_mod_event():
    r = command("mod_event", event="skython_smoke")
    assert len(r["received"]) == 1, r
    got = r["received"][0]
    # name and str_arg come back through the engine's string pool, which can change case (R56).
    assert (got["name"].lower(), got["str_arg"].lower()) == ("skython_smoke", "in-process"), got
    assert (got["num_arg"], got["sender"], got["seq"]) == (2.5, 0x14, 1), got
