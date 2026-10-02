"""The native Skython.Send raises `channel` events. Needs Skython.pex on the test machine (tools/box/box_deploy.sh
--test-pex) and a save loaded (interactive). Every channel and payload carries a fresh per-test tag:
Papyrus strings pass through the engine's case-insensitive string pool, so a string some other script interned
first could come back in that script's casing; a fresh tag cannot collide."""
import queue
import uuid

import pytest

from support import log_text

PAYLOAD_LIMIT = 65536  # max_channel_payload_bytes (protocol.json)


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def collect(game, channel):
    q = queue.Queue()
    return game.on_channel(channel, q.put), q


def next_event(q, timeout=10.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"no channel event within {timeout} s")


def test_native_registered_at_load():
    assert "native: Skython.Send registered" in log_text()


def test_send_reaches_a_channel_subscriber(game):
    tag = uuid.uuid4().hex
    ch = f"skython_live_{tag}"
    sub, q = collect(game, ch)
    try:
        assert game.call("Skython", "Send", ch, f"hello_{tag}") is None
        ev = next_event(q)
        assert ev.name == "channel"
        assert ev.data == {"channel": ch, "payload": f"hello_{tag}", "truncated": False}
        assert ev.seq == 1 and ev.dropped == 0 and ev.suppressed == 0
    finally:
        sub.close()


def test_long_payload_arrives_cut_at_the_limit(game):
    tag = uuid.uuid4().hex
    ch = f"skython_long_{tag}"
    payload = tag + "x" * (70000 - len(tag))
    sub, q = collect(game, ch)
    try:
        game.call("Skython", "Send", ch, payload)
        ev = next_event(q)
        assert ev.data["truncated"] is True
        assert len(ev.data["payload"].encode()) == PAYLOAD_LIMIT
        assert ev.data["payload"] == payload[:PAYLOAD_LIMIT]
    finally:
        sub.close()


def test_channel_filter_ignores_case(game):
    tag = uuid.uuid4().hex
    sub, q = collect(game, f"SKYTHON_CASE_{tag.upper()}")
    try:
        game.call("Skython", "Send", f"skython_case_{tag}", f"x_{tag}")
        ev = next_event(q)
        assert ev.data["channel"].lower() == f"skython_case_{tag}"
        assert ev.data["payload"] == f"x_{tag}"
    finally:
        sub.close()


def test_other_and_invalid_channels_never_arrive(game):
    tag = uuid.uuid4().hex
    ch = f"skython_only_{tag}"
    sub, q = collect(game, ch)
    try:
        game.call("Skython", "Send", f"skython_other_{tag}", "not for this subscriber")
        game.call("Skython", "Send", "", "empty channel")
        game.call("Skython", "Send", "c" * 257, "channel longer than max_channel_bytes")
        game.call("Skython", "Send", ch, f"yes_{tag}")
        ev = next_event(q)
        assert ev.data["payload"] == f"yes_{tag}"  # each call returned before the next, so order is call order
        with pytest.raises(queue.Empty):
            q.get(timeout=1.0)
    finally:
        sub.close()
    assert "native: Skython.Send dropped a call whose channel is empty or longer than 256 bytes" in log_text()
