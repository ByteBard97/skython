"""The skython package against the REAL _skython (C++ Dispatcher + FakeBackend, Task 19), not fake_skython.

Skipped unless SKYTHON_REAL_MODULE_DIR names the folder holding the built _skython*.so
(pyglue/build after Task 19). It pins fake_skython's behaviour to the real Dispatcher's.
"""
import importlib
import json
import os
import socket
import sys
import threading
import time

import pytest

from support import wait_until
from skython import _framing, bridge, errors
from skython.inprocess import connect_in_process
from skython.remote import connect_remote

MODULE_DIR = os.environ.get("SKYTHON_REAL_MODULE_DIR")
pytestmark = pytest.mark.skipif(not MODULE_DIR, reason="SKYTHON_REAL_MODULE_DIR not set")
TOKEN = "ab" * 16


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """The real module, imported for this file only: SKYTHON_FAKE_DIR and sys.modules["_skython"] are restored
    afterwards, so later tests see the fake again."""
    before = sys.modules.pop("_skython", None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SKYTHON_FAKE_DIR", str(tmp_path_factory.mktemp("real")))
        sys.path.insert(0, MODULE_DIR)
        try:
            mod = importlib.import_module("_skython")
        finally:
            sys.path.remove(MODULE_DIR)
        try:
            assert mod.BACKEND == "fake"
            yield mod
            mod._fake_set_not_ready(None)
        finally:
            sys.modules.pop("_skython", None)
            if before is not None:
                sys.modules["_skython"] = before


@pytest.fixture
def game(real):
    with connect_in_process(module=real) as g:
        yield g


def test_round_trips(game):
    assert game.call("Fake", "Echo", 7) == 7
    assert game.call("Fake", "Echo", "hé") == "hé"
    assert game.call("Fake", "Echo", [1, 2, 3]) == [1, 2, 3]
    assert game.state()["backend"] == "fake"


def test_errors_map_to_the_same_classes(game):
    with pytest.raises(errors.NotFound):
        game.call("Fake", "Fail", "not_found", "nope")
    with pytest.raises(errors.InternalError):
        game.call("Fake", "Throw")
    with pytest.raises(errors.UnknownFunction):
        game.call("Fake", "Nope")
    with pytest.raises(errors.TypeMismatch):
        game.call("Fake", "Echo", 2**31)


def test_timeout_carries_game_state(game):
    with pytest.raises(errors.Timeout) as e:
        game.call("Fake", "Never", timeout=0.3)
    assert {"paused", "menus"} <= set(e.value.detail)


def test_not_ready_then_ready(game, real):
    real._fake_set_not_ready("loading")
    try:
        with pytest.raises(errors.NotReady) as e:
            game.call("Fake", "Echo", 1)
        assert e.value.detail["reason"] == "loading"
        assert game.state()["backend"] == "fake"     # game.state still answers
    finally:
        real._fake_set_not_ready(None)
    assert game.call("Fake", "Echo", 1) == 1


def test_a_load_fails_calls_in_flight(game, real):
    out = {}

    def slow():
        try:
            out["v"] = game.call("Fake", "Sleep", 1500, timeout=5)
        except Exception as exc:  # noqa: BLE001
            out["e"] = exc

    t = threading.Thread(target=slow)
    t.start()
    wait_until(lambda: real.in_flight() == 1, what="the call to be in flight")
    real._fake_bump_loads()
    t.join(5)
    assert isinstance(out.get("e"), errors.NotReady)
    assert out["e"].detail["dispatched"] is True


def test_the_gil_is_released_while_waiting(game, real):
    # The call runs on a worker. This thread can only see it in flight, and the spinner can only advance, while
    # the worker is inside _skython.call if call released the GIL; before and after the call they always could.
    count, stop = [0], [False]

    def spin():
        while not stop[0]:
            count[0] += 1

    result = {}

    def work():
        try:
            result["value"] = game.call("Fake", "Sleep", 1000)
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    caller = threading.Thread(target=work)
    spinner = threading.Thread(target=spin)
    spinner.start()
    caller.start()
    try:
        wait_until(lambda: real.in_flight() == 1, what="the call to be in flight")
        before = count[0]
        time.sleep(0.1)
        advanced = count[0] - before
        assert real.in_flight() == 1  # still inside the call
        assert advanced > 0
    finally:
        caller.join(5)
        stop[0] = True
        spinner.join()
    assert "error" not in result, result.get("error")
    assert not caller.is_alive() and "value" in result


def test_bridge_over_the_real_module(real):
    srv = bridge.serve(real, port=0, token=TOKEN,
                       welcome_info={"plugin_version": "0.0.0-fake", "game_version": "0.0.0", "runtime": "fake", "epoch": 0})
    try:
        with connect_remote(port=srv.port, token=TOKEN) as g:
            assert g.call("Fake", "Echo", [1.5, 2.5]) == [1.5, 2.5]
            with pytest.raises(errors.Timeout):
                g.call("Fake", "Never", timeout=0.3)
        # A client that disconnects with calls in flight leaves nothing behind.
        s = socket.create_connection(("127.0.0.1", srv.port))
        s.sendall(_framing.encode({"type": "hello", "protocol": [1, 0], "token": TOKEN, "client": "t"}))
        assert _framing.read_frame(s, time.monotonic() + 3)["type"] == "welcome"
        for i in range(1, 6):
            s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                       "args": {"script": "Fake", "function": "Never", "args": [], "timeout_ms": 60000}}))
        deadline = time.monotonic() + 3
        while real.in_flight() < 5 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert real.in_flight() >= 5
        s.close()
        deadline = time.monotonic() + 3
        while real.in_flight() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert real.in_flight() == 0
    finally:
        srv.close()


def test_bridge_connections_are_isolated_over_the_real_module(real):
    # Task 37 (Review A#12): two connections over the real C++ hub. One cannot unsubscribe the other's subscription,
    # and each sees only its own events.
    srv = bridge.serve(real, port=0, token=TOKEN,
                       welcome_info={"plugin_version": "0.0.0-fake", "game_version": "0.0.0", "runtime": "fake", "epoch": 0})
    try:
        with connect_remote(port=srv.port, token=TOKEN) as a, connect_remote(port=srv.port, token=TOKEN) as b:
            a_seen, b_seen = [], []
            sa = a.on_channel("real_a", a_seen.append)
            b.on_channel("real_b", b_seen.append)
            with pytest.raises(errors.NotFound):
                b._request("events.unsubscribe", {"sub": sa.sub})
            for name in ("real_a", "real_b"):
                real._fake_push_event("channel", json.dumps({"channel": name, "payload": name, "truncated": False}))
            wait_until(lambda: a_seen and b_seen, what="each connection's own event")
            assert [e.data["channel"] for e in a_seen] == ["real_a"]
            assert [e.data["channel"] for e in b_seen] == ["real_b"]
    finally:
        srv.close()
