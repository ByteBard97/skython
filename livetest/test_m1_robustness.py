import os
import pathlib
import subprocess
import threading
import time

import pytest

from skython import errors
from skython.values import Fingerprint, Form
from smoke import wait_smoke
from support import diagnostics, raw_connect, read_frame, rpc, send_request

PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
MANUAL = os.environ.get("SPY_MANUAL") == "1"
manual = pytest.mark.skipif(not MANUAL, reason="manual test: run with SPY_MANUAL=1 and `pytest -s`, with someone at the keyboard")
SAVE_TEST = os.environ.get("SPY_SAVE_TEST") == "1"   # opt-in: every run leaves one new save on the box
save_test = pytest.mark.skipif(not SAVE_TEST, reason="save test (opt-in): run with SPY_SAVE_TEST=1 (game on the Windows box, DevBench answering; leaves one new save)")
BOX_CONSOLE = pathlib.Path(__file__).resolve().parent.parent / "tools" / "box" / "box_console.sh"
BOX_SAVES = pathlib.Path(__file__).resolve().parent.parent / "tools" / "box" / "box_saves.sh"
# A new save of our own per run, never an existing one (Task 1b deploy safety); list.ps1 lists the prefix.
SAVE_DURING_CALL = f"skypy_r38_savecall_{time.strftime('%Y%m%d%H%M%S')}"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_in_process_smoke_script_passed():
    data = wait_smoke()  # written in this run (support.fresh)
    failed = [c for c in data["checks"] if not c["ok"]]
    assert data["status"] == "pass" and not failed, failed
    # The shipping mode really ran: the game's _skython and the in-process transport, not a bridge-backed Game.
    assert data["backend"] == "game" and data["transport"] == "InProcessTransport", data
    # The host loaded every script in this run: the broken one failed (Task 26's livetest_broken), the smoke one ran.
    scripts = data["scripts"]
    assert scripts["livetest_broken"].startswith("failed"), scripts
    assert scripts["livetest_smoke"] == "ok", scripts


def test_runtime_reference_fingerprint(game):
    sword = game.form(IRON_SWORD)
    # PlaceAtMe(akFormToPlace, aiCount, abForcePersist, abInitiallyDisabled[, abDeleteWhenAble]): persistent,
    # not disabled, every argument passed. Try five parameters, fall back to four (check ObjectReference.psc).
    args = (sword, 1, True, False, True)
    try:
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", *args)
    except errors.TypeMismatch as e:
        if "takes 4" not in str(e):
            raise
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", *args[:4])
    try:
        assert ref.form_id >> 24 == 0xFF and ref.form_key is None
        assert ref.fingerprint == Fingerprint(IRON_SWORD, "ObjectReference")
        assert game.call_method(ref, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
        forged = Form(form_id=ref.form_id, fingerprint=Fingerprint("Skyrim.esm|0x00000F", "ObjectReference"))
        with pytest.raises(errors.StaleReference):
            game.call_method(forged, "ObjectReference", "GetBaseObject")
    finally:
        game.call_method(ref, "ObjectReference", "Delete")


def test_disconnect_with_calls_in_flight_drops_the_results(session, game):
    n = 32  # enough to prove the point, few enough that the VM is never flooded
    before = diagnostics(session)
    s = raw_connect(session)
    for i in range(1, n + 1):
        send_request(s, "papyrus.call_static",
                     {"script": "Utility", "function": "Wait", "args": [2.0], "timeout_ms": 30000}, req_id=i)
    time.sleep(0.3)  # let the bridge submit them, so they really are in flight when we close
    assert diagnostics(session)["in_flight"] >= before["in_flight"] + n
    s.close()  # the bridge cancels the connection's tickets (Task 23)
    time.sleep(0.5)  # well before the 2 s waits finish in the game
    # Dispatcher::Cancel releases each entry at once: the closed connection holds nothing any more.
    assert diagnostics(session)["in_flight"] == before["in_flight"]
    time.sleep(4)  # the 32 waits finish in the game
    # Every late result was dropped and counted, none delivered.
    assert diagnostics(session)["late_results_dropped"] - before["late_results_dropped"] == n
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_many_short_connections(session):
    for i in range(50):
        s = raw_connect(session)
        assert rpc(s, "game.state", req_id=i + 1)["result"]["interactive"] is True
        s.close()


@manual
def test_paused_game_reports_state_and_honours_require_unpaused(session):
    """Spec 4.1: a menu that pauses the game. A human opens and closes it."""
    s = raw_connect(session)
    input("\nIn the game, open the INVENTORY or JOURNAL menu (it must pause the game), then press Enter here... ")
    st = rpc(s, "game.state")["result"]
    assert st["paused"] is True and st["menus"], st
    r = rpc(s, "papyrus.call_static", {"script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": True}, req_id=2)
    assert r["error"]["code"] == "not_ready" and r["error"]["detail"]["reason"] == "paused"
    r = rpc(s, "papyrus.call_static", {"script": "Utility", "function": "Wait", "args": [0.5], "timeout_ms": 1500}, req_id=3)
    assert r["error"]["code"] == "timeout" and r["error"]["detail"]["paused"] is True and r["error"]["detail"]["menus"]
    input("Close the menu, then press Enter here... ")
    assert rpc(s, "game.state", req_id=4)["result"]["paused"] is False
    assert rpc(s, "papyrus.call_static", {"script": "Utility", "function": "RandomInt", "args": [1, 6]}, req_id=5)["ok"]
    s.close()


def box_console(command: str) -> str:
    # DevBench reroutes `save <name>` and `load <name>` to its game tool; typed raw they would deadlock (Task 1b).
    r = subprocess.run([str(BOX_CONSOLE), command], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"box_console.sh {command!r} failed: {r.stdout}{r.stderr}"
    return r.stdout


def steady_late_count(session, settle: float = 1.0, tries: int = 10) -> int:
    """late_results_dropped once two readings `settle` seconds apart agree (Task 27's steady_late_count, via support)."""
    prev = diagnostics(session)["late_results_dropped"]
    for _ in range(tries):
        time.sleep(settle)
        now = diagnostics(session)["late_results_dropped"]
        if now == prev:
            return now
        prev = now
    raise AssertionError("late_results_dropped kept changing after the load")


def box_save_count(name: str) -> int:
    r = subprocess.run([str(BOX_SAVES), "count", name], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"box_saves.sh count {name} failed: {r.stdout}{r.stderr}"
    return int(r.stdout.strip())


@save_test
def test_save_during_a_latent_call_then_load(session, game):
    """R38: a save taken while our Utility.Wait is suspended in the VM must not record our callback. Task 27's
    functor returns false from CanSave(); the base class already does at the pin, so this pins the behaviour, not
    the override. Loading that save ends the call NotReady (the load cancels it) or Timeout, never a crash; no late
    result arrives after the load; the game keeps running."""
    assert box_save_count(SAVE_DURING_CALL) == 0, (f"a save named {SAVE_DURING_CALL}* already exists on the box: never "
                                                   "save over it; ask the owner")
    outcome = {}

    def call():
        try:
            outcome["result"] = game.call("Utility", "Wait", 5.0, timeout=30)
        except Exception as e:  # recorded, asserted below
            outcome["error"] = e

    t = threading.Thread(target=call, name="latent-call", daemon=True)
    t.start()
    time.sleep(0.5)
    assert diagnostics(session)["in_flight"] >= 1, "the 5 s wait is not in flight"
    box_console(f"save {SAVE_DURING_CALL}")
    box_console(f"load {SAVE_DURING_CALL}")
    t.join(60)
    assert not t.is_alive(), "the call neither returned nor raised within 60 s"
    assert "result" not in outcome, ("the 5 s wait finished before the load started (two DevBench round trips took too "
                                     "long): a setup problem, not a product result; do not reinterpret it.")
    print(f"\nsave {SAVE_DURING_CALL}: the in-flight Utility.Wait(5.0) ended with {outcome!r}")
    assert isinstance(outcome["error"], (errors.NotReady, errors.Timeout)), outcome
    game.wait_ready(timeout=180)  # the loaded game is up
    # The baseline is taken after the load, once it is steady: the cancelled call's own late result (if the VM still
    # delivers one) is not what this test is about.
    baseline = steady_late_count(session)
    time.sleep(6)  # longer than the 5 s wait: a callback restored from the save would have fired by now
    assert diagnostics(session)["late_results_dropped"] == baseline, \
        "a late result arrived after the load: the save recorded our callback"
    assert box_save_count(SAVE_DURING_CALL) == 1, f"{SAVE_DURING_CALL}.ess was not written: no save was tested"
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


@manual
def test_inflight_call_is_failed_when_a_save_starts_loading(session):
    s = raw_connect(session)
    send_request(s, "papyrus.call_static", {"script": "Utility", "function": "Wait", "args": [60.0], "timeout_ms": 120000})
    print("\nA 60 s wait is in flight. In the game, LOAD A SAVE now (within 60 s)... ", flush=True)
    s.settimeout(90)
    r = read_frame(s)
    assert r["ok"] is False and r["error"]["code"] == "not_ready" and r["error"]["detail"]["reason"] == "loading", r
    s.close()
