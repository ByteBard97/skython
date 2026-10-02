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


def test_a_connection_closed_while_its_consumer_opens_does_not_leak_it(start, fake, monkeypatch):
    # BridgeServer.close() can close a connection between open_consumer() and the welcome; close() then saw consumer 0,
    # so the handshake must close the consumer it just opened itself.
    b = start()
    base = fake.consumers()
    real_open = fake.open_consumer

    def open_then_close_bridge():
        c = real_open()
        b.close()  # closes every connection, this one included, while its consumer is not yet recorded
        return c

    monkeypatch.setattr(fake, "open_consumer", open_then_close_bridge)
    s = socket.socket()
    s.settimeout(5)
    s.connect(("127.0.0.1", b.port))
    s.sendall(_framing.encode({"type": "hello", "protocol": [1, 1], "token": b.token, "client": "raw"}))
    wait_until(lambda: fake.consumers() == base and b.connections() == 0, what="the consumer opened during close to be closed")
    s.close()


# -- Task 37 review minors (fixed in Task 38's first commit) ---------------------------------------------------
def test_track_with_max_queue_0_keeps_one_frame_like_the_hub():
    s = RemoteEventStream()
    for seq in range(1, 4):
        s.put(frame(5, seq))
    s.track(5, 0)  # the hub's bound is at least 1: the newest frame stays, the others count as drops
    s.put(frame(5, 4))  # a full queue of one: seq 3 goes
    assert [(f["seq"], f.get("dropped", 0)) for f in s.next(0.1)] == [(4, 3)]


def test_next_forgets_the_drained_queue_of_an_untracked_sub():
    s = RemoteEventStream()
    s.track(5, 16)
    s.put(frame(5, 1))
    s.put(frame(9, 1))  # sub 9 not tracked (yet): a provisional queue
    s.put({"type": "event", "event": "channel", "sub": "bad", "seq": 1})  # malformed: key None
    assert len(s.next(0.1)) == 3
    assert set(s._queues) == {5}  # test-only peek: provisional queues do not pile up once drained


def test_an_event_frame_over_the_frame_limit_is_counted_not_answered(start, fake, caplog, monkeypatch):
    # Unreachable with the real limits (the hub's per-consumer byte budget drops such an event first); lowered here.
    caplog.set_level(logging.WARNING, logger="skython.bridge")
    monkeypatch.setattr("skython.bridge._g.MAX_FRAME_BYTES", 4096)
    b = start()
    s = raw(b)
    resp, _ = rpc(s, 1, g.OP_EVENTS_SUBSCRIBE, {"event": "channel", "filter": {"channel": ["c"]}})
    assert resp["ok"] is True
    push(fake, "channel", chan("c", "x" * 8000))
    push(fake, "channel", chan("c", "y" * 8000))
    push(fake, "channel", chan("c", "small"))
    wait_until(lambda: b.oversize_events == 2, what="both oversize event frames to be counted")
    resp, events = rpc(s, 2, "papyrus.call_static", {"script": "Fake", "function": "Echo", "args": [3]})
    assert resp["ok"] is True
    assert [e["data"]["payload"] for e in events] == ["small"]  # no id-0 internal response in between
    assert caplog.text.count("over the frame limit") == 1  # logged once, counted every time
    s.close()


def test_an_open_consumer_failure_other_than_no_hub_names_its_error(start, fake, caplog, monkeypatch):
    caplog.set_level(logging.WARNING, logger="skython.bridge")

    def broken():
        raise ValueError("consumer table full")

    monkeypatch.setattr(fake, "open_consumer", broken)
    b = start()
    with remote(b) as game:
        assert game.call("Fake", "Echo", 1) == 1
    assert "bridge: could not open an event consumer (ValueError: consumer table full)" in caplog.text
    assert "no event hub" not in caplog.text
