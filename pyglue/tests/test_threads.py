"""The GIL is released while _skyrim waits, and many Python threads can call at once (Review Focus 6)."""
import threading
import time

import pytest

import _skyrim
from conftest import request, response

# The three functions that wait for the game. The bridge's completion thread sits in wait_any for the life of every
# connection, so each one must release the GIL, not only call.
WAITERS = {
    "call": lambda text: _skyrim.call(text, 5.0),
    "wait": lambda text: _skyrim.wait(_skyrim.submit(text), 5.0),
    "wait_any": lambda text: _skyrim.wait_any([_skyrim.submit(text)], 5.0)[0][1],
}


@pytest.mark.parametrize("waiter", list(WAITERS))
def test_other_python_threads_run_while_a_call_waits(waiter):
    done = threading.Event()
    result = {}

    def caller():
        result["r"] = response(WAITERS[waiter](request(1, "Sleep", 500)))
        done.set()

    t = threading.Thread(target=caller)
    t.start()
    time.sleep(0.05)  # let the caller get into the wait
    count = 0
    start = time.monotonic()
    while not done.is_set():
        count += 1
    elapsed = time.monotonic() - start
    t.join()
    assert result["r"]["result"] == 500
    assert elapsed > 0.3
    # If the waiter held the GIL for the 500 ms, this loop could not have run during it. A free loop does millions of
    # iterations in that time; the bound is far below that to stay robust on a slow or busy machine.
    assert count > 10_000, count


def test_cancel_while_another_thread_waits_on_the_same_tickets():
    # The bridge's pattern: a completion thread polls wait_any while a disconnect cancels the same tickets.
    before = _skyrim.in_flight()
    tickets = [_skyrim.submit(request(100 + i, "Sleep", 100)) for i in range(50)]
    cancelled = threading.Event()
    problems = []

    def poller():
        try:
            while True:
                got = _skyrim.wait_any(tickets, 0.05)
                if cancelled.is_set() and not got:
                    return
        except Exception as e:  # noqa: BLE001 - report everything from the thread
            problems.append(repr(e))

    t = threading.Thread(target=poller)
    t.start()
    for ticket in tickets:
        _skyrim.cancel(ticket)
    cancelled.set()
    t.join(1.0)
    assert not t.is_alive()
    assert problems == []
    assert _skyrim.in_flight() == before


def test_sixteen_threads_two_hundred_calls_each():
    errors = []

    def worker(n):
        try:
            for k in range(200):
                value = n * 1000 + k
                r = response(_skyrim.call(request(value, "Echo", value), 10.0))
                if r["id"] != value or r["result"] != value:
                    errors.append((n, k, r))
        except Exception as e:  # noqa: BLE001 - report everything from the thread
            errors.append((n, repr(e)))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads)
    assert errors == []
    assert _skyrim.in_flight() == 0
