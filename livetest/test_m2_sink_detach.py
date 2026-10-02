"""Task 44: the sink register/unregister probe, then either attach-on-demand (probe PASS) or skip-only (FAIL or
INCONCLUSIVE). Mode is read from the plugin log: 'events: sinks attach on demand' means Part 2 is in. s4test, never
saved."""
import queue
import re
import threading
import time

import pytest

from skython import errors
from smoke import command
from support import PLAYER, box_menu, log_text

IRON_SWORD = "Skyrim.esm|0x012EB7"
MENU = "InventoryMenu"


def pick_up(game, n: int, stop: threading.Event | None = None) -> int:
    """Place a sword and pick it up, n times (or until stop): one player TESActivateEvent each. Returns how many."""
    sword, done = game.form(IRON_SWORD), 0
    while done < n and not (stop and stop.is_set()):
        ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", sword, 1, False, False)
        game.call_method(ref, "ObjectReference", "Activate", PLAYER, False)
        done += 1
    return done


def toggle_menu_once(windows: list, delay: float) -> None:
    """Open and close the menu once through DevBench, `delay` seconds from now (menu_open/menu_close for the spare UI
    sink). Records (opened_at, closed_at) on time.monotonic(), from before the open call to after the close call, so
    the window is never shorter than the time the menu was really open (ruling R62)."""
    time.sleep(delay)
    t0 = time.monotonic()
    try:
        box_menu("open", MENU)
        time.sleep(0.3)
    finally:
        box_menu("close", MENU)
        windows.append((t0, time.monotonic()))


def remove_swords(game) -> None:
    sword = game.form(IRON_SWORD)
    n = game.call_method(PLAYER, "ObjectReference", "GetItemCount", sword)
    if n:
        game.call_method(PLAYER, "ObjectReference", "RemoveItem", sword, n, True, None)


def detach_mode() -> bool:
    return "events: sinks attach on demand" in log_text()


def drain(q: queue.Queue) -> int:
    n = 0
    while not q.empty():
        q.get_nowait()
        n += 1
    return n


BATCHES, PER_BATCH = 10, 100  # 1000 cycles in all
COUNTERS = ("cycles", "errors", "delivered", "delivered_any", "menu_delivered", "events_outside_tasks", "overlaps")


def test_probe_register_unregister_cycles(game):
    """1000 register/unregister cycles in 10 batches of 100 while the player activates swords. The menu is opened and
    closed once during every odd batch and never during an even one, so a step that timed out can be told apart:
    inside a menu window it is INCONCLUSIVE (the menu pauses the game, ruling R62), in a quiet batch it is FAIL. Each
    batch zeroes the probe's counters, so the totals are sums over the batches."""
    hub_seen: queue.Queue = queue.Queue()
    stop = threading.Event()
    raised = {"during": 0}
    rows, windows = [], []
    with game.on("activate", {"activator": [PLAYER]}, hub_seen.put, max_queue=65536):
        t = threading.Thread(target=lambda: raised.__setitem__("during", pick_up(game, 10**6, stop)), daemon=True)
        t.start()
        try:
            for b in range(BATCHES):
                mt = None
                if b % 2 == 1:
                    mt = threading.Thread(target=toggle_menu_once, args=(windows, 1.0), daemon=True)
                    mt.start()
                t0 = time.monotonic()
                r = command("probe_sink_cycles", n=PER_BATCH, timeout=600.0, first_poll_after=2.0)  # ends unregistered
                t1 = time.monotonic()
                if mt:
                    mt.join(60)
                rows.append({"batch": b, "menu": mt is not None, "t0": round(t0, 3), "t1": round(t1, 3), **r})
                print(f"probe batch {b} ({'menu' if mt else 'quiet'}, {t1 - t0:.1f} s): {r}")
        finally:
            stop.set()
            t.join(30)
            box_menu("close", MENU)  # never leave the menu open
        total = {k: sum(row[k] for row in rows) for k in COUNTERS}
        print(f"probe totals over {len(rows)} batches: {total}; activations raised during the cycles: "
              f"{raised['during']}; menu windows: {[(round(a, 3), round(z, 3)) for a, z in windows]}")
        print(f"events outside our tasks: {total['events_outside_tasks']} (0 = INCONCLUSIVE), overlaps: "
              f"{total['overlaps']}, menu_delivered: {total['menu_delivered']}, delivered_any: {total['delivered_any']}")
        bad = []
        for row in rows:
            if row["errors"] or row["registered"] is not False or row["cycles"] != PER_BATCH:
                in_menu = any(a <= row["t1"] and z >= row["t0"] for a, z in windows)
                bad.append(f"batch {row['batch']}: {'INCONCLUSIVE (menu window, R62)' if in_menu else 'FAIL'} {row}")
        time.sleep(2.0)                                       # late activations of the cycling phase land first
        drained = drain(hub_seen)
        try:
            armed = command("probe_sink_cycles", n=-1)        # one task: register the spare sink, zero its counters
            assert armed["registered"] is True and armed["errors"] == 0, armed
            k = pick_up(game, 20)                             # every one of these must reach both sinks once
            got = 0
            deadline = time.monotonic() + 15
            while got < k and time.monotonic() < deadline:
                try:
                    hub_seen.get(timeout=1.0)
                    got += 1
                except queue.Empty:
                    pass
            final = command("probe_sink_cycles", n=0)         # report, then unregister
        finally:
            remove_swords(game)
    print(f"after: drained {drained}, raised {k}, spare sink {final['delivered']} "
          f"(any actor: {final['delivered_any']}), hub {got}")
    assert not bad, bad
    assert total["cycles"] == BATCHES * PER_BATCH and total["errors"] == 0, total
    # INCONCLUSIVE (not a test failure): the verdict in Step 4 reads events_outside_tasks from the totals.
    assert final["errors"] == 0, final
    assert final["delivered"] == k, "every player activation after the arm must reach the spare sink exactly once"
    assert got == k, "the product's own activate sink must keep delivering"


def test_mode_specific_behaviour(game):
    q: queue.Queue = queue.Queue()
    if not detach_mode():
        # Skip-only (probe FAIL, INCONCLUSIVE or not adopted): sinks stay registered; nothing queued without subscribers.
        assert "events: sink detached" not in log_text()
        before = game.state()["diagnostics"]["events_dropped_total"]
        pick_up(game, 3)
        assert game.state()["diagnostics"]["events_dropped_total"] == before
        remove_swords(game)
        return
    # Attach on demand: subscribe -> attached, unsubscribe -> detached, subscribe again -> attached and delivering.
    for round_ in range(2):
        before = log_text()
        attached0 = before.count("events: sink attached activate")
        detached0 = before.count("events: sink detached activate")
        sub = game.on("activate", {"activator": [PLAYER]}, q.put)
        try:
            assert pick_up(game, 1) == 1
            ev = q.get(timeout=10.0)                        # our own call is queued after the attach task
            target = ev.data["target"]
        finally:
            sub.close()
        time.sleep(1.0)
        text = log_text()
        # Exactly one new attach and one new detach per round (counts from earlier tests are subtracted).
        assert text.count("events: sink attached activate") == attached0 + 1, round_
        assert text.count("events: sink detached activate") == detached0 + 1, round_
    # The reference an event named is gone (picked up): its next use raises, never crashes, never vanishes silently.
    with pytest.raises((errors.NotFound, errors.StaleReference)):
        game.call_method(target, "ObjectReference", "GetBaseObject")
    remove_swords(game)


def test_rapid_subscribe_close_ends_attached(game):
    """200 rapid subscribe/close cycles queue out-of-order reconcile tasks; one final subscription must leave the
    activate sink attached and delivering (attach-on-demand mode only)."""
    if not detach_mode():
        pytest.skip("skip-only mode: sinks never detach")
    start = len(re.findall(r"events: sink (?:attached|detached) activate", log_text()))
    for _ in range(200):
        game.on("activate", {"activator": [PLAYER]}, lambda ev: None).close()
    q: queue.Queue = queue.Queue()
    with game.on("activate", {"activator": [PLAYER]}, q.put):
        time.sleep(2.0)                                      # every queued reconcile task has run
        lines = re.findall(r"events: sink (attached|detached) activate", log_text())[start:]
        assert "detached" in lines, lines  # the cycles really detached at least once
        assert lines[-1] == "attached", lines[-5:]
        assert pick_up(game, 1) == 1
        q.get(timeout=10.0)
    remove_swords(game)
