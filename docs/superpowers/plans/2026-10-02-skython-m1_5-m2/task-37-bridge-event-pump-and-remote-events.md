# Task 37: Bridge event pump and remote event delivery

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Review Focus**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2 and 4.6). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 36 (`skython.events`: `EventDispatcher`, the `EventStream` protocol with `track`/`untrack`, `Game.on` and helpers) and Task 36a (`fake_skython.py` with consumers, `next_events`, `_fake_push_event`, `consumers()`, `subscriptions()`). Linux only: the tests run a real `BridgeServer` over the fake `_skython` and drive it with the real remote transport and raw sockets, the whole dev path without the game.

**What this task builds.** Events through the dev bridge. On the server side each connection opens one `_skython` consumer once its hello is accepted, submits every request **with that consumer beside the text** (`backend.submit(text, conn.consumer)`; the text itself is forwarded unchanged, so a client can never name another consumer), runs a fourth thread `pump` that loops `backend.next_events(conn.consumer, 0.25)` and enqueues each event object as one frame through the existing writer (so `max_queued_bytes_per_connection` bounds events as well as responses), and closes the consumer when the connection closes (which drops its subscriptions and wakes the pump). On the client side `RemoteTransport`'s reader routes `"type": "event"` frames to a `RemoteEventStream` (behind Task 36's `EventStream` protocol, `consumer = None`) instead of the response futures. A server that opens no consumer (no event hub) still serves every other request: the bridge falls back to consumer 0 (Review A#1). Because the pump drains the hub continuously, the hub never drops for a slow **remote** callback; so the stream keeps a **client-side bounded queue per subscription** with the hub's semantics: each subscription's `max_queue` (given by the dispatcher's `track(sub, max_queue)`), drop-oldest, and the client's drops (plus any server `dropped`/`suppressed` carried by a dropped frame) added to the next delivered frame of that subscription, so `seq` gaps still equal `dropped` sums. A frame for a sub not yet tracked (an event frame can overtake its subscribe response: the pump and the completion thread write independently) is held under the provisional bound `MAX_EVENT_QUEUE` and trimmed when `track` arrives; `untrack(sub)` discards the sub's queue and every later frame for it. Memory is bounded by the sum of the subscriptions' `max_queue` (plus at most one `MAX_EVENTS_PER_NEXT` batch held by the dispatch thread) on the client, and by `max_queued_bytes_per_connection` on the server; any other message type after the handshake is a protocol violation that closes the connection. The same `EventDispatcher` serves a remote `Game`, so remote callbacks behave exactly like in-process ones.

**Files:**
- Modify: `python/src/skython/bridge.py` (consumer per connection, `pump` thread, `submit(text, consumer)`, `close_consumer` on close, docstrings), `python/src/skython/remote.py` (`RemoteEventStream` with per-subscription bounds, event routing, `events()`, unknown message types close the connection)
- Create: `python/tests/test_bridge_events.py`
- Modify: `python/tests/test_real_module.py` (one test: two bridge connections over the real hub are isolated, Review A#12)

**Interfaces:**
- Consumes: Task 36 (`skython.events.Event`, `EventDispatcher` (its `subscribe` calls `stream.track(sub, max_queue)` under its lock before registering; `Subscription.close` calls `stream.untrack(sub)`), the `EventStream` protocol `consumer` / `next(timeout)` / `close()` / `track(sub, max_queue)` / `untrack(sub)`; `Game.on`, `on_channel`, `on_death`; `Game.close()` closes subscriptions, the dispatch thread and the stream), `fake_skython.FakeSkython` (`submit(text, consumer=0)`, `open_consumer()`, `close_consumer(consumer)`, `next_events(consumer, timeout) -> str`, `_fake_push_event(event, data_json)`, test-only `consumers()`, `subscriptions(consumer=None)`), Task 35 (the real module has the same four methods, so `test_real_module.py`'s bridge test keeps passing), Task 33 generated `MAX_EVENTS_PER_NEXT`, `MAX_EVENT_QUEUE`, `DEFAULT_EVENT_QUEUE`, `OP_EVENTS_SUBSCRIBE`, `OP_EVENTS_UNSUBSCRIBE`; v1 `bridge.py` (`_Conn`, `_enqueue`, `_frame`, `_dumps`), `remote.py` (`RemoteTransport`), `tests/support.py` (`FakeServer`, `wait_until`, `TOKEN`, `ok`).
- Produces (index Contracts, `remote.py` / `bridge.py`):
  - `skython.remote.RemoteEventStream` (`consumer = None`; `put(msg)` from the reader: appends to the frame's sub queue, drop-oldest at that sub's `max_queue` (provisional bound `MAX_EVENT_QUEUE` until tracked), counting client drops; `track(sub, max_queue)`: sets the bound and trims to the newest `max_queue` frames, counting the trimmed as drops; `untrack(sub)`: discards the sub's queue and every later frame for it (sub ids are never reused; the set of ended subs keeps the newest `RemoteEventStream._GONE_MAX` = 4096, so it cannot grow without bound over a long session, Review A#12); `next(timeout) -> list[dict]`: blocks for the first frame up to `timeout`, then takes what else is queued, at most `MAX_EVENTS_PER_NEXT`, in arrival order across subs, each frame's `dropped` = the server's `dropped` + the sub's pending client drops (then reset); `[]` once closed; `close()` wakes a waiting `next`). `RemoteTransport.events() -> RemoteEventStream` (made in `__init__`, so a frame that arrives before anyone asked is kept). A connection failure or `close()` closes the stream. A message of any unknown `type` after the handshake is a `ProtocolViolation` that closes the connection.
  - `skython.bridge`: the `backend` duck type is now `submit(text, consumer) -> int`, `wait_any`, `cancel`, `open_consumer() -> int`, `close_consumer(consumer)`, `next_events(consumer, timeout) -> str`. Each accepted connection opens its consumer before the welcome is sent; if `open_consumer` raises (a module with no event hub: the game build before Task 39), the connection is served with consumer 0 and no pump, its `events.*` requests answer `unsupported`, and `bridge: no event hub (<error>); events are unsupported on this connection` is logged once per process (logger `skython.bridge`, WARNING; Review A#1). A connection with a consumer has four threads (`bridge-read-*`, `bridge-write-*`, `bridge-done-*`, `bridge-pump-*`), and closes its consumer exactly once when it closes. Task 38 adds `trace_timing` to this file.

- [ ] **Step 1: Write the failing tests**

`python/tests/test_bridge_events.py`:
```python
"""Events through the dev bridge, end to end on Linux: a real BridgeServer over the fake _skython, the real remote
transport and raw sockets. Review Focus 4 (a client disconnecting with events queued) and the "no lost first event"
rule over the bridge are pinned here."""
import json
import logging
import socket
import struct
import threading
import time

import pytest

from support import FakeServer, ok, wait_until
from skython import _framing
from skython import _protocol_generated as g
from skython import errors
from skython.bridge import serve
from skython.inprocess import connect_in_process
from skython.remote import RemoteEventStream, connect_remote
from skython.values import Form

INFO = {"plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1}


@pytest.fixture
def start(fake, tmp_path):
    started = []

    def _start(**kw):
        kw.setdefault("welcome_info", INFO)
        b = serve(fake, port=0, **kw)
        started.append(b)
        return b

    yield _start
    for b in started:
        b.close()


@pytest.fixture
def bridge(start):
    return start()


def remote(b, **kw):
    return connect_remote(port=b.port, token=b.token, **kw)


def raw(b, rcvbuf=None):
    s = socket.socket()
    if rcvbuf:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.settimeout(5)
    s.connect(("127.0.0.1", b.port))
    s.sendall(_framing.encode({"type": "hello", "protocol": [1, 1], "token": b.token, "client": "raw"}))
    assert _framing.read_frame(s, time.monotonic() + 5)["type"] == "welcome"
    return s


def rpc(s, req_id, op, args):
    """Send one request and return its response; event frames read meanwhile are returned too."""
    s.sendall(_framing.encode({"type": "request", "id": req_id, "op": op, "args": args}))
    events = []
    while True:
        msg = _framing.read_frame(s, time.monotonic() + 5)
        if msg["type"] == "event":
            events.append(msg)
        elif msg.get("id") == req_id:
            return msg, events


def push(fake, event, data):
    fake._fake_push_event(event, json.dumps(data))


def chan(name, payload=""):
    return {"channel": name, "payload": payload, "truncated": False}


def test_remote_events_have_the_same_fields_as_in_process(bridge, fake):
    remote_seen, local_seen, threads = [], [], set()

    def on_remote(e):
        threads.add(threading.current_thread().name)
        remote_seen.append(e)

    with remote(bridge) as rg, connect_in_process(module=fake) as lg:
        rs = rg.on_death(None, on_remote)
        ls = lg.on_death(None, local_seen.append)
        rc = rg.on_channel("same", on_remote)
        push(fake, "death", {"victim": {"kind": "form", "form_id": 0xFF000123, "type": "Actor"}, "killer": None})
        push(fake, "channel", chan("same", "hi"))
        wait_until(lambda: len(remote_seen) == 2 and local_seen, what="the remote and in-process events")
    r, l = remote_seen[0], local_seen[0]
    assert (r.name, r.seq, r.epoch, r.data, r.dropped, r.suppressed) == (l.name, l.seq, l.epoch, l.data, l.dropped, l.suppressed)
    assert r.sub == rs.sub and l.sub == ls.sub and r.sub != l.sub
    assert isinstance(r.data["victim"], Form) and r.data["victim"].form_id == 0xFF000123
    assert remote_seen[1].sub == rc.sub and remote_seen[1].data == chan("same", "hi")
    assert threads == {"skython-events"}


def test_two_connections_never_see_each_others_events(bridge, fake):
    a_seen = []
    with remote(bridge) as a:
        a.on_channel("only_a", a_seen.append)
        b = raw(bridge)
        resp, _ = rpc(b, 1, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": ["for_b"]}})
        b_sub = resp["result"]["sub"]
        for i in range(10):
            push(fake, "channel", chan("only_a", str(i)))
        push(fake, "channel", chan("for_b", "x"))
        first = _framing.read_frame(b, time.monotonic() + 5)  # B's first frame is its own event, never one of A's
        assert first["type"] == "event" and first["sub"] == b_sub and first["data"]["channel"] == "for_b"
        wait_until(lambda: len(a_seen) == 10, what="A's ten events")
        b.close()
    assert all(e.data["channel"] == "only_a" for e in a_seen)


def test_a_client_cannot_unsubscribe_another_connections_subscription(bridge, fake):
    a, b = raw(bridge), raw(bridge)
    resp, _ = rpc(a, 1, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": ["mine"]}})
    sub = resp["result"]["sub"]
    resp, _ = rpc(b, 1, g.OP_EVENTS_UNSUBSCRIBE, {"sub": sub})
    assert resp["ok"] is False and resp["error"]["code"] == "not_found"
    push(fake, "channel", chan("mine", "still subscribed"))
    ev = _framing.read_frame(a, time.monotonic() + 5)
    assert ev["type"] == "event" and ev["sub"] == sub
    resp, _ = rpc(a, 2, g.OP_EVENTS_UNSUBSCRIBE, {"sub": sub})  # its owner can
    assert resp["ok"] is True and resp["result"] is None
    a.close()
    b.close()


def test_subscribe_then_immediate_push_over_the_bridge_loses_no_first_event(bridge, fake):
    # As in Task 36: request() returns only once the dispatch thread has FETCHED the pushed event, i.e. while Game.on()
    # still holds the dispatcher's lock; without the lock the event would be dropped as unknown. Review A#4.
    with remote(bridge) as game:
        stream = game.transport.events()
        fetched = threading.Event()
        real_next = stream.next

        def next_(timeout):
            batch = real_next(timeout)
            if batch:
                fetched.set()
            return batch

        stream.next = next_  # before the dispatch thread exists, so every fetch goes through it
        warm = game.on_channel("warm-up", lambda e: None)  # starts the dispatch thread
        real_request = game.transport.request

        def request(op, args, wait_s):
            resp = real_request(op, args, wait_s)
            if op == g.OP_EVENTS_SUBSCRIBE and resp.get("ok"):  # the hub has it; Game.on() has not registered it yet
                fetched.clear()
                push(fake, "channel", chan(args["filter"]["channel"][0], "first"))
                assert fetched.wait(5), "the dispatch thread never fetched the first event"
            return resp

        game.transport.request = request
        for i in range(50):
            seen = []
            sub = game.on_channel(f"race{i}", seen.append)
            wait_until(lambda: seen, what=f"the first event of subscription {i}")
            assert seen[0].seq == 1 and seen[0].sub == sub.sub
            sub.close()
        warm.close()


def test_a_blocked_callback_on_one_connection_does_not_hold_up_another(bridge, fake):
    # Review Focus 2 remotely (Review A#13): each connection has its own consumer, pump and client queues.
    release, started = threading.Event(), threading.Event()
    other_seen = []

    def stuck(e):
        started.set()
        release.wait(10)

    with remote(bridge) as first, remote(bridge) as second:
        first.on_channel("both", stuck)
        second.on_channel("both", other_seen.append)
        push(fake, "channel", chan("both", "0"))
        assert started.wait(5), "the first connection's callback never started"
        for i in range(1, 51):
            push(fake, "channel", chan("both", str(i)))
        wait_until(lambda: len(other_seen) == 51, timeout=5, what="the second connection's events while the first is blocked")
        assert [e.seq for e in other_seen] == list(range(1, 52))
        release.set()


def test_a_backend_without_an_event_hub_still_serves_requests(start, fake, caplog, monkeypatch):
    # The game build deploys this bridge (Task 38) before Task 39 fills ModuleHost.events, so open_consumer raises
    # RuntimeError. Each connection is served with consumer 0 and no pump, events.* answer unsupported, and the
    # warning is logged once per process. Review A#1.
    caplog.set_level(logging.WARNING, logger="skython.bridge")
    monkeypatch.setattr("skython.bridge._no_hub_logged", False)
    fake.hub = None
    b = start()
    with remote(b) as g1, remote(b) as g2:
        assert g1.call("Fake", "Echo", 1) == 1 and g2.call("Fake", "Echo", 2) == 2
        with pytest.raises(errors.Unsupported):
            g1.on_channel("x", lambda e: None)
        pumps = {f"bridge-pump-{gm.transport._sock.getsockname()[1]}" for gm in (g1, g2)}
        assert not [t for t in threading.enumerate() if t.name in pumps]
    assert caplog.text.count("bridge: no event hub (") == 1
    assert "events are unsupported on this connection" in caplog.text


def test_an_event_frame_before_its_subscribe_response_is_delivered():
    # The pump and the completion thread are separate, so an event frame may overtake the subscribe response on the
    # wire. The reader hands it to the stream; the dispatch thread (already running for the first subscription) waits
    # for Game.on() to register the sub instead of dropping the event as unknown.
    subs = iter([40, 41])

    def handler(req, reply):
        if req["op"] == g.OP_EVENTS_SUBSCRIBE:
            sub = next(subs)
            if sub == 41:
                reply({"type": "event", "event": "channel", "sub": 41, "seq": 1, "epoch": 1, "data": chan("early", "x")})
                time.sleep(0.05)  # orders the two writes; the dispatch thread meets the event while on() holds the lock
            reply(ok(req["id"], {"sub": sub, "event": "channel", "max_queue": g.DEFAULT_EVENT_QUEUE}))
        else:
            reply(ok(req["id"], None))

    # A protocol 1.1 welcome: Game.on() refuses a server whose capabilities lack events.subscribe (Task 36).
    with FakeServer(handler, welcome={"protocol": [1, 1], "capabilities": list(g.OPS)}) as srv:
        with connect_remote(port=srv.port, token=srv.token) as game:
            game.on_channel("warm", lambda e: None)  # starts the dispatch thread
            seen = []
            game.on_channel("early", seen.append)
            wait_until(lambda: seen, what="the event that overtook its subscribe response")
            assert seen[0].sub == 41 and seen[0].data["payload"] == "x"


def test_an_unknown_message_type_closes_the_connection():
    def handler(req, reply):
        reply({"type": "surprise", "id": req["id"]})

    with FakeServer(handler) as srv:
        with connect_remote(port=srv.port, token=srv.token) as game:
            with pytest.raises(errors.ConnectionLost, match="surprise"):
                game.call("Fake", "Echo", 1)
            assert game.closed


def test_disconnect_with_1000_events_queued_frees_its_consumer(start, fake):
    # Review Focus 4: the connection's consumer and every subscription it owned are closed; others keep working.
    b = start()
    other_seen = []
    with remote(b) as other:
        other.on_channel("other", other_seen.append)
        base = (fake.consumers(), fake.subscriptions())
        s = raw(b, rcvbuf=4096)
        resp, _ = rpc(s, 1, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": ["flood"]}, "max_queue": 2000})
        assert resp["ok"] is True
        assert (fake.consumers(), fake.subscriptions()) == (base[0] + 1, base[1] + 1)
        for i in range(1000):  # the client reads none of them
            push(fake, "channel", chan("flood", "x" * 1000))
        s.close()
        wait_until(lambda: (fake.consumers(), fake.subscriptions()) == base and b.connections() == 1, timeout=10,
                   what="the closed connection's consumer and subscription to be gone")
        push(fake, "channel", chan("other", "after"))
        wait_until(lambda: other_seen, what="the other connection's event")
        assert other.call("Fake", "Echo", 7) == 7
    wait_until(lambda: fake.consumers() == base[0] - 1, what="the remote Game's own consumer to be closed")


def test_a_client_that_never_reads_events_is_closed_by_the_queue_cap(start, fake, caplog):
    caplog.set_level(logging.INFO, logger="skython.bridge")
    b = start(max_queued_bytes=256 * 1024)
    base = fake.consumers()
    s = raw(b, rcvbuf=4096)
    resp, _ = rpc(s, 1, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": ["big"]},
                                                "max_queue": g.MAX_EVENT_QUEUE})
    assert resp["ok"] is True
    for i in range(400):  # 12.8 MB of event frames: far beyond the socket buffers and the 256 KiB cap
        push(fake, "channel", chan("big", "y" * 32 * 1024))
    wait_until(lambda: b.connections() == 0, timeout=15, what="the client that stopped reading to be closed")
    assert "stopped reading" in caplog.text
    wait_until(lambda: fake.consumers() == base, what="its consumer to be closed")
    s.close()
    with remote(b) as game:
        assert game.call("Fake", "Echo", 1) == 1


def test_request_text_reaches_submit_unchanged_with_the_consumer_beside_it(start, fake, monkeypatch):
    seen = []
    real_submit = fake.submit

    def spy(text, consumer=0):
        seen.append((text, consumer))
        return real_submit(text, consumer)

    monkeypatch.setattr(fake, "submit", spy)
    b = start()
    s = raw(b)
    bodies = ['{"type":"request","id":7,"op":"papyrus.call_static","args":{"script":"Fake","function":"Echo","args":["é\\u00e9"]}}',
              '{"type":"request","id":8,"op":"events.subscribe","args":{"event":"channel","filter":{"channel":["c"]}}}']
    for body in bodies:
        data = body.encode("utf-8")
        s.sendall(struct.pack("<I", len(data)) + data)
        assert _framing.read_frame(s, time.monotonic() + 5)["ok"] is True
    assert [t for t, _ in seen] == bodies  # byte-identical: the bridge never re-encodes a request
    consumers = {c for _, c in seen}
    assert len(consumers) == 1 and next(iter(consumers)) > 0  # one consumer per connection, beside the text
    s.close()


# -- the client-side bound (Review Focus 1-2 remotely) -------------------------------------------------------
def frame(sub, seq, **extra):
    return {"type": "event", "event": "channel", "sub": sub, "seq": seq, "epoch": 1, "data": chan("u", str(seq)), **extra}


def test_the_remote_stream_bounds_each_subscription_and_carries_the_drops():
    s = RemoteEventStream()
    for seq in range(1, 11):  # sub 5 is not tracked yet (its frames overtook the subscribe response): all are kept
        s.put(frame(5, seq))
    s.track(5, 4)  # trimmed to the newest 4; the 6 trimmed count as drops
    s.track(6, 2)
    s.put(frame(6, 1))
    s.put(frame(6, 5, dropped=3))  # the hub already dropped seq 2-4
    s.put(frame(6, 6))  # the client queue of sub 6 is full: seq 1 goes
    got = s.next(0.1)
    assert [(f["sub"], f["seq"], f.get("dropped", 0)) for f in got] == [
        (5, 7, 6), (5, 8, 0), (5, 9, 0), (5, 10, 0), (6, 5, 4), (6, 6, 0)]  # arrival order; seq gaps equal dropped
    s.track(7, 1000)
    for seq in range(1, 301):
        s.put(frame(7, seq))
    assert len(s.next(0.1)) == g.MAX_EVENTS_PER_NEXT and len(s.next(0.1)) == 300 - g.MAX_EVENTS_PER_NEXT
    assert s.next(0.05) == []


def test_untrack_discards_a_subscriptions_frames_and_close_wakes_next():
    s = RemoteEventStream()
    s.track(5, 16)
    s.put(frame(5, 1, suppressed=2))
    s.put(frame(5, 2))
    s.untrack(5)
    s.put(frame(5, 3))  # a late frame of a closed subscription
    assert s.next(0.05) == []
    for sub in range(1000, 1000 + RemoteEventStream._GONE_MAX + 1000):  # a long session ends many subscriptions
        s.untrack(sub)
    assert len(s._gone) == RemoteEventStream._GONE_MAX  # test-only peek: the set of ended subs is capped
    got = []
    t = threading.Thread(target=lambda: got.append(s.next(5.0)))
    t.start()
    time.sleep(0.1)  # let next() block; close() must wake it at once
    t0 = time.monotonic()
    s.close()
    t.join(2)
    assert not t.is_alive() and got == [[]] and time.monotonic() - t0 < 1.0
    s.put(frame(6, 1))
    assert s.next(0.05) == []  # closed: nothing more


def test_a_slow_remote_callback_drops_only_its_own_events_with_a_bounded_client_queue(bridge, fake):
    # The pump drains the hub at once, so the bound that matters remotely is the client's queue per subscription.
    n = 10_000
    release, started = threading.Event(), threading.Event()
    slow_seen, fast_seen = [], []
    longest, last_seq = [0], [0]

    def slow(e):
        slow_seen.append(e)
        if e.seq == 1:
            started.set()
            release.wait(30)

    with remote(bridge) as game:
        stream = game.transport.events()
        real_put = stream.put

        def put(msg):  # the reader thread calls this for every event frame
            real_put(msg)
            if msg.get("data", {}).get("channel") == "slow":
                last_seq[0] = max(last_seq[0], msg["seq"])
                longest[0] = max(longest[0], len(stream._queues.get(msg["sub"], ())))  # test-only peek at the bound

        stream.put = put
        game.on("channel", {"channel": ["slow"]}, slow, max_queue=16)
        game.on_channel("fast", fast_seen.append)
        push(fake, "channel", chan("slow", "0"))
        assert started.wait(5), "the slow callback never started"
        for i in range(1, n):
            push(fake, "channel", chan("slow", str(i)))
            if i % 500 == 0:
                push(fake, "channel", chan("fast", str(i // 500)))
        wait_until(lambda: last_seq[0] == n, timeout=30, what="the last slow frame to reach the client")
        assert 0 < longest[0] <= 16, longest[0]
        release.set()
        wait_until(lambda: slow_seen[-1].seq == n and len(fast_seen) == 19, timeout=10, what="the held-back events")
    assert len(slow_seen) == 17  # the first (taken before the flood) and the 16 the client queue held
    assert len(slow_seen) + sum(e.dropped for e in slow_seen) == n
    assert all(b.seq - a.seq - 1 == b.dropped for a, b in zip(slow_seen, slow_seen[1:]))
    assert [e.seq for e in fast_seen] == list(range(1, 20)) and all(e.dropped == 0 for e in fast_seen)


def test_game_close_over_the_bridge_releases_eight_subscriptions(bridge, fake):
    base = (fake.consumers(), fake.subscriptions())
    game = remote(bridge)
    subs = [game.on_channel(f"c{i}", lambda e: None) for i in range(8)]
    assert fake.subscriptions() == base[1] + 8
    game.close()
    assert all(s.closed for s in subs)
    wait_until(lambda: (fake.consumers(), fake.subscriptions()) == base, what="the connection's consumer to be closed")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_bridge_events.py 2>&1 | tail -3`
Expected: FAIL. The remote `on_*` tests raise `Unsupported: RemoteTransport has no event stream`; the raw-socket subscribe tests get `bad_request` "no event consumer" (the bridge still submits without a consumer); `test_an_unknown_message_type_closes_the_connection` times out (the v1 reader skips unknown types); the three client-bound tests fail with `ImportError: cannot import name 'RemoteEventStream'` (collection error for the whole file until Step 3).

- [ ] **Step 3: `RemoteEventStream` and event routing in `remote.py`**

In `python/src/skython/remote.py`, add `import collections` to the imports (`itertools`, `threading` and `time` are there since v1). Before `class RemoteTransport`:
```python
class RemoteEventStream:
    """The event frames of one bridge connection, behind skython.events.EventStream. The reader thread put()s each
    frame; the Game's dispatch thread takes them with next(). consumer is None: the bridge owns the consumer (one per
    connection).

    The bridge's pump drains the hub continuously, so the hub never drops for a slow remote callback; this stream
    keeps the hub's semantics on the client instead: one queue per subscription, bounded by its max_queue (track()),
    drop-oldest, and the drops (plus the dropped/suppressed counts a dropped frame carried) added to the next
    delivered frame of that subscription, so seq gaps still equal dropped sums. A frame of a sub not tracked yet (it
    overtook its subscribe response) is held under the provisional bound MAX_EVENT_QUEUE until track() trims it;
    untrack() discards the sub's frames and every later one (sub ids are never reused). next() returns frames in
    arrival order across subscriptions, at most MAX_EVENTS_PER_NEXT."""

    consumer = None
    _GONE_MAX = 4096  # ended subs remembered (their late frames are discarded); older ones are forgotten

    def __init__(self):
        self._cond = threading.Condition()
        self._queues: dict = {}  # sub -> deque of (arrival number, frame)
        self._bounds: dict[int, int] = {}  # tracked subs -> max_queue
        self._pending: dict = {}  # sub -> [dropped, suppressed] not yet reported
        self._gone: set[int] = set()  # untracked subs: their later frames are discarded
        self._gone_order: collections.deque = collections.deque()  # the same subs, oldest first (caps _gone)
        self._arrivals = itertools.count(1)
        self._queued = 0
        self.closed = False

    def _drop_oldest(self, sub) -> None:  # with _cond held
        _, old = self._queues[sub].popleft()
        self._queued -= 1
        p = self._pending.setdefault(sub, [0, 0])
        p[0] += 1 + old.get("dropped", 0)  # the frame itself, and the hub's drops it was carrying
        p[1] += old.get("suppressed", 0)

    def put(self, msg: dict) -> None:
        sub = msg.get("sub")
        key = sub if type(sub) is int else None  # a malformed frame still reaches the dispatcher, which logs it
        with self._cond:
            if self.closed or key in self._gone:
                return
            q = self._queues.setdefault(key, collections.deque())
            if len(q) >= self._bounds.get(key, _g.MAX_EVENT_QUEUE):
                self._drop_oldest(key)
            q.append((next(self._arrivals), msg))
            self._queued += 1
            self._cond.notify_all()

    def track(self, sub: int, max_queue: int) -> None:
        with self._cond:
            if sub in self._gone:
                return
            self._bounds[sub] = max_queue
            q = self._queues.get(sub)
            while q and len(q) > max_queue:
                self._drop_oldest(sub)

    def untrack(self, sub: int) -> None:
        with self._cond:
            if sub not in self._gone:
                self._gone.add(sub)
                self._gone_order.append(sub)
                while len(self._gone_order) > self._GONE_MAX:  # a late frame comes within moments, never this late
                    self._gone.discard(self._gone_order.popleft())
            self._bounds.pop(sub, None)
            self._pending.pop(sub, None)
            self._queued -= len(self._queues.pop(sub, ()))

    def next(self, timeout: float) -> list[dict]:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while not self.closed and self._queued == 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)
            if self.closed:
                return []
            out = []
            while self._queued and len(out) < _g.MAX_EVENTS_PER_NEXT:
                sub = min((k for k, q in self._queues.items() if q), key=lambda k: self._queues[k][0][0])
                msg = self._queues[sub].popleft()[1]
                self._queued -= 1
                dropped, suppressed = self._pending.pop(sub, (0, 0))
                if dropped or suppressed:
                    msg = dict(msg)
                    if dropped:
                        msg["dropped"] = msg.get("dropped", 0) + dropped
                    if suppressed:
                        msg["suppressed"] = msg.get("suppressed", 0) + suppressed
                out.append(msg)
            return out

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._queues.clear()
            self._pending.clear()
            self._queued = 0
            self._cond.notify_all()  # wakes a waiting next()
```
In `RemoteTransport.__init__`, before the reader thread is created:
```python
        self._events = RemoteEventStream()  # before the reader starts: no frame can arrive without a place to go
```
Replace the body of `_read_loop`'s `while True:` up to the `req_id = msg.get("id")` line with:
```python
            while True:
                msg = _framing.read_frame(self._sock)
                kind = msg.get("type")
                if kind == "event":
                    self._events.put(msg)
                    continue
                if kind != "response":
                    raise errors.ProtocolViolation(f"the server sent a {kind!r} message after the handshake")
                req_id = msg.get("id")
```
In `_fail_all`, after `self._close_socket()` add `self._events.close()`. In `close()`, after `self._close_socket()` add `self._events.close()`. Add the method after `request`:
```python
    def events(self) -> RemoteEventStream:
        """This connection's event stream (the bridge holds the consumer)."""
        return self._events
```
And update the module docstring's last sentence to: `... so the connection is then closed, never reused. Event frames go to the connection's RemoteEventStream (Task 37); any other message type after the handshake closes the connection.`

- [ ] **Step 4: The consumer and the pump in `bridge.py`**

In `python/src/skython/bridge.py`:

Module docstring: replace the paragraph from "Three threads per connection:" to "...the bridge never times a call out itself." with:
```
Four threads per connection: a reader (handshake, then requests), a completion thread (wait_any on the
connection's tickets, 5 ms at a time, so a request submitted meanwhile is picked up quickly even when the
others are slow), a writer (the only thread that sends after the welcome) and an event pump (next_events on the
connection's consumer, 0.25 s at a time; each event object becomes one frame through the writer, so the
max_queued_bytes cap covers events too). The connection's consumer is opened once its hello is accepted, travels
beside every request text it submits (the text is forwarded unchanged, so a client cannot name another consumer),
and is closed with the connection, which drops its subscriptions. The Dispatcher's reaper enforces
each request's timeout_ms: the bridge never times a call out itself.
```
After `_IDLE_S = 0.05 ...` add:
```python
_EVENT_POLL_S = 0.25  # how long the pump waits in next_events per round (it releases the GIL meanwhile)
_no_hub_logged = False  # "no event hub" is logged once per process, not once per connection


def _log_no_hub_once(e: Exception) -> None:
    global _no_hub_logged
    if not _no_hub_logged:
        _no_hub_logged = True
        log.warning("bridge: no event hub (%s); events are unsupported on this connection", e)
```
In `_Conn.__init__`, add `self.consumer = 0  # the _skython event consumer; 0 until the hello is accepted`.

In `_read_loop`, after the `bridge-done` thread start line:
```python
            if self.consumer:  # no consumer (no event hub): no events to pump, and next_events(0) would spin
                threading.Thread(target=self._pump_loop, name=f"bridge-pump-{self.peer[1]}", daemon=True).start()
```
In `_handshake`, right after the `if code is not None: ... return False` block (before the client name is read):
```python
        try:
            self.consumer = self.server.backend.open_consumer()  # before the welcome: every request has a consumer
        except Exception as e:  # noqa: BLE001 - no event hub (the game build before Task 39): serve without events
            _log_no_hub_once(e)
            self.consumer = 0  # requests still go with consumer 0; events.* answer unsupported (the hub is null)
```
In `_on_request`, replace `ticket = self.server.backend.submit(text)` with:
```python
        ticket = self.server.backend.submit(text, self.consumer)  # the consumer travels beside the text, never in it
```
After `_complete_loop`, add:
```python
    # -- events ---------------------------------------------------------------------------------------
    def _pump_loop(self) -> None:
        backend = self.server.backend
        try:
            while not self.closed:
                text = backend.next_events(self.consumer, _EVENT_POLL_S)  # close_consumer wakes it
                if self.closed:
                    return
                for event in json.loads(text):
                    self._enqueue(_dumps(event))  # one frame per event, under the same max_queued_bytes cap
        except Exception:  # noqa: BLE001
            log.exception("bridge: the event pump failed")
            self.close("internal error")
```
In `close()`, after the `for ticket in tickets:` loop:
```python
        if self.consumer:
            try:
                self.server.backend.close_consumer(self.consumer)  # drops its subscriptions, wakes the pump
            except Exception:  # noqa: BLE001
                log.exception("bridge: closing the event consumer failed")
```
In `serve()`'s docstring replace `(anything with submit, wait_any and cancel: the _skython module)` with `(anything with submit(text, consumer), wait_any, cancel, open_consumer, close_consumer and next_events: the _skython module)`.

(`close()` sets `closed` under `cond` before it closes the consumer, so the pump, woken by `close_consumer`, always sees `closed` and exits; `close()` runs its body once, so the consumer is closed once.)

- [ ] **Step 5: Run the new tests, then the whole suite on both Pythons**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q tests/test_bridge_events.py 2>&1 | tail -1`
Expected: `15 passed` in about 6 s (the 50-iteration race test under 1 s, the 10^4-push slow-callback test about 2 s).

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected twice: Task 36's total plus 15 passed, with Task 36's skips plus the one new `test_real_module.py` case; `test_bridge.py`, `test_remote.py`, `test_host.py` unchanged and passing (the v1 tests use the fake, which has the consumer API since Task 36a).

Append to `python/tests/test_real_module.py` (it already imports `bridge`, `connect_remote`, `errors`, `json`, `wait_until`, and defines `TOKEN` and the `real` fixture):
```python
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
```

Against the real module (its bridge tests now also open a consumer per connection):
Run: `cd python && SKYTHON_REAL_MODULE_DIR="$(cd ../pyglue/build && pwd)" uv run --managed-python --python 3.14 pytest -q tests/test_real_module.py tests/test_events.py 2>&1 | tail -1`
Expected: all passed, no skips.

- [ ] **Step 6: Races, repeatedly and on one CPU**

Run: `cd python && for i in 1 2 3 4 5; do uv run --managed-python --python 3.14 pytest -q tests/test_bridge_events.py tests/test_bridge.py 2>&1 | tail -1; done; taskset -c 0 uv run --managed-python --python 3.12 pytest -q tests/test_bridge_events.py tests/test_bridge.py 2>&1 | tail -1`
Expected: the same `N passed` six times (Task 23's bridge tests plus these 15). Every wait has a deadline; no test sleeps for luck (the 50 ms in the overtaking test only orders two server writes).

Run: `python3 tools/gen_protocol.py --check`
Expected: the generated files are up to date (`bridge.py` and `remote.py` spell out no limit or code; they come from `_protocol_generated`).

- [ ] **Step 7: Commit**

```bash
git add python/src/skython/bridge.py python/src/skython/remote.py python/tests/test_bridge_events.py python/tests/test_real_module.py
git commit -m "feat(python): bridge event pump with a consumer per connection, remote event delivery"
```
