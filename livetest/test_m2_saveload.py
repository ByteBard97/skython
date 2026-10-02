"""game.save / game.load in the game (Task 41). Starts on s4test; makes one new throwaway save per run."""
import queue
import threading
import time

import pytest

from skython import errors
from support import LOADABLE, box_save_count, load_save, log_text, new_save_name, raw_connect, rpc, wait_save_file


@pytest.fixture(scope="module")
def saved(session):
    """One new save for the whole module, made from s4test gameplay. game.save returns once the engine started the
    save; the .ess may not be on disk yet (Contracts). Right then, a second save of that name and a load of it are
    tried: the save is refused (save_exists, "already in progress" or, if the file is there, "already exists"), and
    the load either finds the file and loads it or answers not_ready with reason "saving". Both outcomes are
    recorded; the test below checks each is one of the allowed ones."""
    import skython
    with skython.connect(port=session.port, token=session.token, client_name="livetest-t41") as g:
        got: queue.Queue = queue.Queue()
        with g.on("game_saved", None, got.put):
            name = new_save_name("t41")
            assert g.save(name) == name
            try:
                g.save(name)
                again = None  # a second save of the same name must never succeed: the test fails on this
            except errors.BadRequest as e:
                again = e
            ev = got.get(timeout=30.0)
        assert LOADABLE.fullmatch(name), name  # tests load only their own saves (load_save's rule)
        try:
            g.load(name, timeout=120.0, wait=True)
            immediate_load = "loaded"
        except errors.NotReady as e:
            if (e.detail or {}).get("reason") != "saving":
                raise
            immediate_load = "saving"
        wait_save_file(name)
    print(f"t41: immediate second save -> {again and again.message!r}; immediate load -> {immediate_load}")
    return {"name": name, "event": ev, "again": again, "immediate_load": immediate_load}


def test_save_then_save_and_load_at_once(saved):
    again = saved["again"]
    assert again is not None, "a second game.save of the same name succeeded: it would overwrite the first"
    assert again.detail.get("reason") == "save_exists"
    assert again.message in (f"a save named {saved['name']} is already in progress",
                             f"a save named {saved['name']} already exists"), again.message
    assert saved["immediate_load"] in ("loaded", "saving")


def test_save_creates_exactly_one_new_file(saved):
    assert saved["event"].data["name"] == saved["name"]
    text = log_text()
    assert f"saveload: saved {saved['name']}" in text
    assert "lifecycle: kSaveGame data '" in text  # the raw payload, for RESULTS
    assert box_save_count(saved["name"]) == 1


def test_saving_the_same_name_again_is_refused(game, saved):
    with pytest.raises(errors.BadRequest) as e:
        game.save(saved["name"])
    assert e.value.detail.get("reason") == "save_exists"
    assert e.value.message == f"a save named {saved['name']} already exists"


def test_bad_save_names_are_refused(game):
    # Refused by the client (Task 36 may check SAVE_NAME_PATTERN locally: ValueError/EncodeError) or by the plugin
    # (bad_request); either way nothing is saved.
    for bad in ("", "a" * 65, "has space", "dots.ess", "../up"):
        with pytest.raises((errors.BadRequest, ValueError)):
            game.save(bad)


def test_load_missing_save_is_not_found(game):
    with pytest.raises(errors.NotFound) as e:
        game.load("no_such_save_skython", wait=False)
    assert e.value.message == "no save named no_such_save_skython"


def test_load_from_gameplay_raises_epoch_and_game_loaded(game, saved):
    before = game.state()["epoch"]
    got: queue.Queue = queue.Queue()
    with game.on_game_loaded(got.put):
        state = load_save(game, saved["name"])
        ev = got.get(timeout=30.0)
    assert state["epoch"] == before + 1 and state["interactive"] is True
    assert ev.name == "game_loaded" and ev.data["new_game"] is False and ev.epoch == before + 1
    assert f"saveload: load started {saved['name']}" in log_text()


def test_a_call_in_flight_during_the_load_ends_not_ready_dispatched(game, saved):
    outcome: dict = {}

    def slow():
        try:
            outcome["result"] = game.call("Utility", "Wait", 5.0, timeout=15.0)
        except errors.SkythonError as e:
            outcome["error"] = e

    t = threading.Thread(target=slow)
    t.start()
    time.sleep(0.5)  # the Wait is running in the VM
    load_save(game, saved["name"])
    t.join(20.0)
    err = outcome.get("error")
    assert isinstance(err, errors.NotReady) and err.detail.get("dispatched") is True, outcome


def test_a_save_cancelled_after_dispatch_keeps_its_name_reserved(game):
    """Fix round 1: a game.save whose request ends (here: a tiny timeout) after its Game.SaveGame was dispatched keeps
    its name reserved, so an immediate second save of that name is refused instead of dispatching a second
    Game.SaveGame that would overwrite the first. The ~50 ms window between dispatch and kSaveGame cannot be hit on
    purpose (the dispatcher's reaper ticks every 50 ms), so each attempt checks what must hold on every branch and
    the log says which branch ran; at most 3 attempts (3 new saves) try to hit the post-dispatch one."""
    branches = []
    timeout = 0.03
    for _attempt in range(3):
        name = new_save_name("t41c")
        try:
            game.save(name, timeout=timeout)
            first = "ok"
        except errors.Timeout:
            first = "timeout"
        second = None
        if first == "timeout":
            try:
                game.save(name)
                second = "saved"  # the first was cancelled before its dispatch: nothing was requested for it
            except errors.BadRequest as e:
                assert e.detail.get("reason") == "save_exists", e
                assert e.message in (f"a save named {name} is already in progress",
                                     f"a save named {name} already exists"), e.message
                second = "refused"
        wait_save_file(name)
        with pytest.raises(errors.BadRequest) as e:
            game.save(name)
        assert e.value.detail.get("reason") == "save_exists"
        assert e.value.message == f"a save named {name} already exists"
        assert box_save_count(name) == 1
        text = log_text()
        assert text.count(f"saveload: save dispatched {name}\n") == 1, "Game.SaveGame was dispatched more than once"
        reserved = f"saveload: save {name} cancelled after dispatch; name stays reserved" in text
        if second == "saved":
            assert not reserved
        branches.append((name, timeout, first, second, reserved))
        if second == "refused" and reserved:
            break  # the post-dispatch branch ran
        # Cancelled before the dispatch: wait longer next time; cancelled too late (or not at all): shorter.
        timeout = timeout * 2 if second == "saved" else max(0.001, timeout / 3)
    print(f"t41c: {branches}")
    if not any(second == "refused" and reserved for _, _, _, second, reserved in branches):
        pytest.skip(f"the post-dispatch cancel branch was not hit in {len(branches)} attempts: {branches}")


def test_load_from_the_main_menu(session, game, saved):
    try:
        game.call("Game", "QuitToMainMenu")
    except errors.NotReady:
        pass  # the quit is a world reset: the call itself may be ended by it
    deadline = time.monotonic() + 120.0
    while game.state().get("game_loaded"):
        assert time.monotonic() < deadline, "the game did not reach the main menu"
        time.sleep(0.5)
    before = game.state()["epoch"]
    assert LOADABLE.fullmatch(saved["name"]), saved["name"]
    s = raw_connect(session)
    try:
        r = rpc(s, "game.load", {"save": saved["name"]}, req_id=1, timeout=60.0)
        assert r["ok"] is True and r["result"] == {"save": saved["name"], "started": True}, r
    finally:
        s.close()
    deadline = time.monotonic() + 120.0
    while True:
        st = game.state()
        if st["interactive"] and st["epoch"] > before:
            break
        assert time.monotonic() < deadline, f"not interactive after the main-menu load: {st}"
        time.sleep(0.5)
