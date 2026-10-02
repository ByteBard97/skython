"""M2 events on SkythonTest.esp: a stage fragment's Skython.Send reaches a remote and an in-process subscriber,
a mod event raised through Form.SendModEvent with an ESP sender, overflow under a slow callback (drops reported in
`dropped`, whether made in the hub or in the client's RemoteEventStream), unsubscribe mid-stream, and the ESP NPC
across a save and reload (its death in alias 0: killer = the player, quest_stage 100 and stage_100 from the death
rule). Needs skythonbase (fresh_save), livetest/scripts deployed (livetest_events) and a fresh launch (the
in-process overflow test expects the script's slow subscription to start at seq 1). The last test leaves one new
save from new_save_name("t53") (recorded in livetest/results/new_saves.txt for the removal list). Run -s."""
import json
import threading
import time

import pytest

import _testesp as t
from livetest_events import FILE, MOD_EVENT, SLOW_CHANNEL, SLOW_QUEUE
from smoke import py_dir
from support import BOX, box_pull, diagnostics, fresh, new_save_name, wait_save_file

pytestmark = pytest.mark.usefixtures("fresh_save")
DRAIN_LIMIT_MS = 4.5   # twice the measured max_task_drain_ms (2.2454 ms for a 904-task burst, recorded live)


class Recorder:
    """A callback that keeps every Event and lets the test wait for a condition on them."""

    def __init__(self):
        self.events, self._cv = [], threading.Condition()

    def __call__(self, event):
        with self._cv:
            self.events.append(event)
            self._cv.notify_all()

    def wait(self, pred, timeout: float, why: str):
        deadline = time.monotonic() + timeout
        with self._cv:
            while not (got := pred(list(self.events))):
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"{why} within {timeout} s; events: {self.events!r}")
                self._cv.wait(left)
            return got


def payloads(events) -> list:
    return [e.data["payload"].lower() for e in events]


def inproc() -> dict | None:
    if BOX:
        box_pull(f"Skython/{FILE}")
    p = py_dir() / FILE
    try:
        return json.loads(p.read_text()) if fresh(p) else None
    except (OSError, ValueError):
        return None


def wait_inproc(pred, timeout: float, why: str, key: str = "events"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        d = inproc()
        if d and (got := pred(d[key])):
            return got
        time.sleep(1.0)
    raise AssertionError(f"in-process livetest_events: {why} within {timeout} s; last file: {inproc()!r}")


def inproc_last_seq(name: str) -> int:
    """The highest seq the in-process script has recorded for `name` so far (0 when none or no file yet). Events
    newer than a step have a higher seq on the same subscription; seq is used instead of the file's "t" stamps because
    those come from the test machine's clock, not this machine's."""
    d = inproc()
    return max((e["seq"] for e in (d or {}).get("events", []) if e["name"] == name), default=0)


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m2-esp")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    t.ensure_running(mgame, t.quest(mgame))  # first: alias scripts may bind only once the quest runs (discover uses them)
    return t.discover(mgame)


def test_inprocess_script_is_subscribed():
    deadline = time.monotonic() + 60
    while (d := inproc()) is None or d["status"] == "starting":
        assert time.monotonic() < deadline, "livetest_events never wrote its file: is livetest/scripts deployed?"
        time.sleep(1.0)
    assert d["status"] == "subscribed" and not d["errors"], d


def test_stage_fragment_send_reaches_remote_and_inprocess(game, esp):
    rec = Recorder()
    sub = game.on_channel(t.CHANNEL, rec)
    try:
        seq0 = inproc_last_seq("channel")      # only in-process events recorded after this point count
        game.call_method(esp.quest, "Quest", "SetStage", 10)
        want = t.stage_payload(10)
        ev = rec.wait(lambda evs: next((e for e in evs if e.data["payload"].lower() == want), None), 20,
                      f"no {want} on {t.CHANNEL}")
        assert ev.name == "channel" and ev.data["channel"].lower() == t.CHANNEL and ev.data["truncated"] is False
        assert ev.epoch == game.state()["epoch"] and ev.dropped == 0
        mine = wait_inproc(lambda evs: [e for e in evs if e["name"] == "channel" and e["seq"] > seq0
                                        and e["data"]["payload"].lower() == want], 30, f"no new {want}")
        assert mine[-1]["epoch"] == ev.epoch
    finally:
        sub.close()


def test_mod_event_via_form_sendmodevent_with_an_esp_sender(game, esp):
    # The SKSE native Form.SendModEvent called through the bridge on the ESP quest (not a Papyrus script of ours).
    rec = Recorder()
    sub = game.on_mod_event(MOD_EVENT, rec)
    try:
        seq0 = inproc_last_seq("mod_event")
        game.call_method(esp.quest, "Form", "SendModEvent", MOD_EVENT, "esp", 2.5)
        ev = rec.wait(lambda evs: evs[0] if evs else None, 20, f"no mod_event {MOD_EVENT}")
        d = ev.data
        assert d["name"].lower() == MOD_EVENT and d["str_arg"].lower() == "esp" and d["num_arg"] == 2.5, d
        assert d["sender"].form_id == esp.quest.form_id and t.same_key(d["sender"], t.QUEST_KEY), d
        wait_inproc(lambda evs: [e for e in evs if e["name"] == "mod_event" and e["seq"] > seq0], 30,
                    f"no new mod_event {MOD_EVENT}")
    finally:
        sub.close()


def check_drop_accounting(seq_dropped: list, total: int) -> None:
    """Gaps in seq are exactly the drops; drop-oldest keeps the newest, so the last delivered seq is the last pushed."""
    prev = 0
    for seq, dropped in seq_dropped:
        assert seq - prev - 1 == dropped, (prev, seq, dropped)
        prev = seq
    assert prev == total, (prev, total)
    drops = sum(d for _, d in seq_dropped)
    assert drops > 0 and len(seq_dropped) + drops == total, (seq_dropped, total)


def test_slow_callback_overflows_while_the_game_and_other_subscriptions_carry_on(session, game, esp):
    """Remote: the subscription's max_queue 4 bounds both the hub queue and this client's RemoteEventStream queue,
    so drops may be made in the hub (if the bridge's pump falls behind) or in the client; either way they are reported
    in `dropped` on the next delivered event, and seq gaps equal the dropped sums."""
    slow_events = []

    def slow(event):
        slow_events.append(event)
        time.sleep(2.0)

    fast = Recorder()
    s_slow = game.on("channel", {"channel": [t.CHANNEL]}, slow, max_queue=4)
    s_fast = game.on_channel(t.CHANNEL, fast)
    burst = [f"burst_{i}" for i in range(20)]
    stages = [t.stage_payload(s) for s in (20, 100, 200)]
    drain_before = diagnostics(session)["max_task_drain_ms"]
    latencies = []

    def probe():
        for _ in range(10):
            t0 = time.perf_counter()
            game.call("Utility", "RandomInt", 1, 6)
            latencies.append(time.perf_counter() - t0)

    prober = threading.Thread(target=probe, name="latency-probe", daemon=True)
    prober.start()
    try:
        for stage in (20, 100, 200):
            game.call_method(esp.quest, "Quest", "SetStage", stage)
        b = game.batch()
        for p in burst:
            b.call("Skython", "Send", t.CHANNEL, p)
        b.run(timeout=30)
        prober.join(60)
        total = len(burst) + len(stages)
        fast.wait(lambda evs: len(evs) >= total, 120, f"the fast subscription did not get all {total}")
        quiet_since, seen = time.monotonic(), 0
        while time.monotonic() - quiet_since < 5:              # the slow queue has drained when nothing new for 5 s
            if len(slow_events) != seen:
                seen, quiet_since = len(slow_events), time.monotonic()
            time.sleep(0.5)
    finally:
        s_slow.close()
        s_fast.close()
    got = payloads(fast.events)
    print(f"\nfast: {got}\nslow: {[(e.seq, e.dropped, e.data['payload']) for e in slow_events]}")
    assert sorted(got) == sorted(burst + stages) and all(e.dropped == 0 for e in fast.events), got
    assert [p for p in got if p.startswith("burst_")] == burst          # the burst keeps its order
    check_drop_accounting([(e.seq, e.dropped) for e in slow_events], total)
    assert len(latencies) == 10 and max(latencies) < 1.0, latencies
    assert diagnostics(session)["max_task_drain_ms"] <= max(drain_before, DRAIN_LIMIT_MS)


def test_slow_callback_overflows_in_process(session, game, esp):
    """In-process: livetest_events' slow SLOW_CHANNEL subscription (max_queue 4, 2 s per event) on its own Game;
    the hub drops oldest while the callback sleeps. Only this test sends on SLOW_CHANNEL and the script subscribes at
    launch, so its seq starts at 1: this assumes a fresh launch (a second run in the same launch sees seq continue
    and fails here; relaunch instead of relaxing the check)."""
    burst = [f"slow_{i}" for i in range(20)]
    drain_before = diagnostics(session)["max_task_drain_ms"]
    b = game.batch()
    for p in burst:
        b.call("Skython", "Send", SLOW_CHANNEL, p)
    b.run(timeout=30)
    t0 = time.perf_counter()
    assert game.call("Utility", "RandomInt", 1, 6) in range(1, 7)
    assert time.perf_counter() - t0 < 1.0                      # the game is not waiting on the sleeping callback
    last = wait_inproc(lambda evs: evs[-1] if evs and evs[-1]["seq"] == len(burst) else None, 120,
                       f"the slow in-process subscription never delivered seq {len(burst)}", key="slow")
    slow = inproc()["slow"]
    print(f"\nin-process slow: {[(e['seq'], e['dropped'], e['data']['payload']) for e in slow]}")
    assert last["data"]["payload"].lower() == burst[-1]
    assert all(e["data"]["payload"].lower() in burst for e in slow)
    check_drop_accounting([(e["seq"], e["dropped"]) for e in slow], len(burst))
    assert SLOW_QUEUE == 4
    assert diagnostics(session)["max_task_drain_ms"] <= max(drain_before, DRAIN_LIMIT_MS)


def test_unsubscribe_mid_stream(game, esp):
    rec, control = Recorder(), Recorder()
    sub = game.on_channel(t.CHANNEL, rec)
    ctl = game.on_channel(t.CHANNEL, control)
    try:
        for i in range(3):
            game.call("Skython", "Send", t.CHANNEL, f"u_{i}")
        rec.wait(lambda evs: len(evs) >= 3, 20, "the first three did not arrive")
        sub.close()
        assert sub.closed
        for i in range(3, 6):
            game.call("Skython", "Send", t.CHANNEL, f"u_{i}")
        control.wait(lambda evs: len(evs) >= 6, 20, "the control subscription did not get all six")
        time.sleep(2)
        assert payloads(rec.events) == ["u_0", "u_1", "u_2"]
        assert payloads(control.events) == [f"u_{i}" for i in range(6)]
    finally:
        ctl.close()


def test_esp_npc_survives_save_and_reload_and_its_death_has_the_new_epoch(game, esp):
    save = new_save_name("t53")              # checks box_saves.sh count is 0; records it for the removal list
    deaths, loaded, stages, quest_stages = Recorder(), Recorder(), Recorder(), Recorder()
    s_death = game.on_death(esp.npc, deaths)
    s_loaded = game.on_game_loaded(loaded)
    s_stage = game.on_channel(t.CHANNEL, stages)
    s_qstage = game.on_quest_stage(esp.quest, quest_stages, stage=100)   # the ESP quest's own quest_stage event
    try:
        epoch0 = game.state()["epoch"]
        assert game.save(save) == save           # the engine started the save
        wait_save_file(save)                     # the .ess can appear a moment after game.save answers
        epoch1 = game.load(save, wait=True)["epoch"]
        assert epoch1 > epoch0
        ev = loaded.wait(lambda evs: next((e for e in evs if e.epoch == epoch1), None), 30, "no game_loaded")
        assert ev.data["new_game"] is False
        assert game.call_method(esp.npc, "Actor", "IsDead") is False       # the plugin-defined NPC is still valid
        assert game.call_method(esp.npc, "ObjectReference", "GetBaseObject") is not None
        target = esp.ref_aliases[t.TARGET_ALIAS]                            # the death rule watches alias 0
        game.call_method(target, "ReferenceAlias", "ForceRefTo", esp.npc)
        game.call_method(esp.npc, "Actor", "Kill", t.PLAYER)
        death = deaths.wait(lambda evs: next((e for e in evs if e.name == "death"), None), 20,
                            "no death event after Kill (the ESP build fixes the NPC as not essential: STOP)")
        assert death.data["victim"].form_id == esp.npc.form_id and death.epoch == epoch1, death
        assert death.data["killer"] is not None and death.data["killer"].form_id == t.PLAYER.form_id, death
        want = t.stage_payload(100)
        hit = stages.wait(lambda evs: next((e for e in evs if e.data["payload"].lower() == want), None), 20,
                          f"no {want} after the alias 0 actor died (the death rule: STOP)")
        assert hit.epoch == epoch1, hit
        qs = quest_stages.wait(lambda evs: evs[0] if evs else None, 20, "no quest_stage 100 on the ESP quest")
        assert qs.data["quest"].form_id == esp.quest.form_id and qs.data["stage"] == 100 and qs.epoch == epoch1, qs
        wait_inproc(lambda evs: [e for e in evs if e["name"] == "game_loaded" and e["epoch"] == epoch1], 30,
                    f"no game_loaded with epoch {epoch1}")
    finally:
        s_death.close()
        s_loaded.close()
        s_stage.close()
        s_qstage.close()
    print(f"\nsave {save}: epoch {epoch0} -> {epoch1}; death {death!r}")
