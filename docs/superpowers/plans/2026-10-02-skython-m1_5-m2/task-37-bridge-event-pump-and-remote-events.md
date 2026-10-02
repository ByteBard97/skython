# Task 37: Bridge event pump and remote event delivery

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Review Focus**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2 and 4.6). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 36 (`skython.events`: `EventDispatcher`, the `EventStream` protocol, `Game.on` and helpers; `fake_skython.py` with consumers, `next_events`, `_fake_push_event`, `consumers()`, `subscriptions()`). Linux only: the tests run a real `BridgeServer` over the fake `_skython` and drive it with the real remote transport and raw sockets, the whole dev path without the game.

**What this task builds.** Events through the dev bridge. On the server side each connection opens one `_skython` consumer once its hello is accepted, submits every request **with that consumer beside the text** (`backend.submit(text, conn.consumer)`; the text itself is forwarded unchanged, so a client can never name another consumer), runs a fourth thread `pump` that loops `backend.next_events(conn.consumer, 0.25)` and enqueues each event object as one frame through the existing writer (so `max_queued_bytes_per_connection` bounds events as well as responses), and closes the consumer when the connection closes (which drops its subscriptions and wakes the pump). On the client side `RemoteTransport`'s reader routes `"type": "event"` frames to a `RemoteEventStream` (a `queue.Queue` behind Task 36's `EventStream` protocol, `consumer = None`) instead of the response futures; any other message type after the handshake is a protocol violation that closes the connection. The same `EventDispatcher` serves a remote `Game`, so remote callbacks behave exactly like in-process ones.

**Known limit (recorded, not fixed here; listed for the owner).** The pump drains the hub continuously, so a slow **remote** callback never makes the hub drop: its events pile up in the client's `RemoteEventStream` queue (unbounded, as the Contract says) and in the bridge's per-connection send queue, which closes the connection at `max_queued_bytes_per_connection`. Remote events therefore never carry `dropped` from a slow callback; in-process they do (Task 36). A per-subscription bound on the client side would fix it and needs a Contract change (`EventStream` would need each subscription's `max_queue`).

**Files:**
- Modify: `python/src/skython/bridge.py` (consumer per connection, `pump` thread, `submit(text, consumer)`, `close_consumer` on close, docstrings), `python/src/skython/remote.py` (`RemoteEventStream`, event routing, `events()`, unknown message types are violations)
- Create: `python/tests/test_bridge_events.py`

**Interfaces:**
- Consumes: Task 36 (`skython.events.Event`, `EventDispatcher`, the `EventStream` protocol `consumer` / `next(timeout)` / `close()`; `Game.on`, `on_channel`, `on_death`; `Game.close()` closes subscriptions, the dispatch thread and the stream), `fake_skython.FakeSkython` (`submit(text, consumer=0)`, `open_consumer()`, `close_consumer(consumer)`, `next_events(consumer, timeout) -> str`, `_fake_push_event(event, data_json)`, test-only `consumers()`, `subscriptions(consumer=None)`), Task 35 (the real module has the same four methods, so `test_real_module.py`'s bridge test keeps passing), Task 33 generated `MAX_EVENTS_PER_NEXT`, `OP_EVENTS_SUBSCRIBE`, `OP_EVENTS_UNSUBSCRIBE`; v1 `bridge.py` (`_Conn`, `_enqueue`, `_frame`, `_dumps`), `remote.py` (`RemoteTransport`), `tests/support.py` (`FakeServer`, `wait_until`, `TOKEN`, `ok`).
- Produces (index Contracts, `remote.py` / `bridge.py`):
  - `skython.remote.RemoteEventStream` (`consumer = None`; `put(msg)` from the reader; `next(timeout) -> list[dict]`: blocks for the first event up to `timeout`, then takes what else is queued, at most `MAX_EVENTS_PER_NEXT`; `[]` once closed; `close()` wakes a waiting `next`). `RemoteTransport.events() -> RemoteEventStream` (made in `__init__`, so a frame that arrives before anyone asked is kept). A connection failure or `close()` closes the stream.
  - `skython.bridge`: the `backend` duck type is now `submit(text, consumer) -> int`, `wait_any`, `cancel`, `open_consumer() -> int`, `close_consumer(consumer)`, `next_events(consumer, timeout) -> str`. Each accepted connection opens its consumer before the welcome is sent (a failure closes that connection only), has four threads (`bridge-read-*`, `bridge-write-*`, `bridge-done-*`, `bridge-pump-*`), and closes its consumer exactly once when it closes. Task 38 adds `trace_timing` to this file.

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
from skython.remote import connect_remote
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


def test_1000_subscribe_then_immediate_push_over_the_bridge_lose_no_first_event(bridge, fake):
    with remote(bridge) as game:
        real_request = game.transport.request

        def request(op, args, wait_s):
            resp = real_request(op, args, wait_s)
            if op == g.OP_EVENTS_SUBSCRIBE and resp.get("ok"):  # the hub has it; Game.on() has not registered it yet
                push(fake, "channel", chan(args["filter"]["channel"][0], "first"))
            return resp

        game.transport.request = request
        for i in range(1000):
            seen = []
            sub = game.on_channel(f"race{i}", seen.append)
            wait_until(lambda: seen, what=f"the first event of subscription {i}")
            assert seen[0].seq == 1 and seen[0].sub == sub.sub
            sub.close()


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

    with FakeServer(handler) as srv:
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
Expected: FAIL. The remote `on_*` tests raise `Unsupported: RemoteTransport has no event stream`; the raw-socket subscribe tests get `bad_request` "no event consumer" (the bridge still submits without a consumer); `test_an_unknown_message_type_closes_the_connection` times out (the v1 reader skips unknown types).

- [ ] **Step 3: `RemoteEventStream` and event routing in `remote.py`**

In `python/src/skython/remote.py`, add `import queue` to the imports. Before `class RemoteTransport`:
```python
class RemoteEventStream:
    """The event frames of one bridge connection, behind skython.events.EventStream. The reader thread put()s each
    frame; the Game's dispatch thread takes them with next(). consumer is None: the bridge owns the consumer (one per
    connection). Unbounded, so a slow remote callback grows this queue instead of making the hub drop (Task 37's
    known limit); the bridge's max_queued_bytes_per_connection still bounds what the server holds."""

    consumer = None

    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self.closed = False

    def put(self, msg: dict) -> None:
        if not self.closed:
            self._q.put(msg)

    def next(self, timeout: float) -> list[dict]:
        if self.closed:
            return []
        try:
            first = self._q.get(timeout=timeout)
        except queue.Empty:
            return []
        if first is None:  # close()'s wake-up
            return []
        out = [first]
        while len(out) < _g.MAX_EVENTS_PER_NEXT:
            try:
                msg = self._q.get_nowait()
            except queue.Empty:
                break
            if msg is None:
                break
            out.append(msg)
        return out

    def close(self) -> None:
        self.closed = True
        self._q.put(None)  # wakes a waiting next()
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
```
In `_Conn.__init__`, add `self.consumer = 0  # the _skython event consumer; 0 until the hello is accepted`.

In `_read_loop`, after the `bridge-done` thread start line:
```python
            threading.Thread(target=self._pump_loop, name=f"bridge-pump-{self.peer[1]}", daemon=True).start()
```
In `_handshake`, right after the `if code is not None: ... return False` block (before the client name is read):
```python
        self.consumer = self.server.backend.open_consumer()  # before the welcome: every request has a consumer
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
Expected: `10 passed` in about 5 s (the 1000-iteration test about 3 s).

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected twice: Task 36's total plus 10 passed, with Task 36's skips; `test_bridge.py`, `test_remote.py`, `test_host.py` unchanged and passing (the v1 tests use the fake, which has the consumer API since Task 36).

Against the real module (its bridge test now also opens a consumer per connection):
Run: `cd python && SKYTHON_REAL_MODULE_DIR="$(cd ../pyglue/build && pwd)" uv run --managed-python --python 3.14 pytest -q tests/test_real_module.py tests/test_events.py 2>&1 | tail -1`
Expected: all passed, no skips.

- [ ] **Step 6: Races, repeatedly and on one CPU**

Run: `cd python && for i in 1 2 3 4 5; do uv run --managed-python --python 3.14 pytest -q tests/test_bridge_events.py tests/test_bridge.py 2>&1 | tail -1; done; taskset -c 0 uv run --managed-python --python 3.12 pytest -q tests/test_bridge_events.py tests/test_bridge.py 2>&1 | tail -1`
Expected: the same `N passed` six times (Task 23's bridge tests plus these 10). Every wait has a deadline; no test sleeps for luck (the 50 ms in the overtaking test only orders two server writes).

Run: `python3 tools/gen_protocol.py --check`
Expected: the generated files are up to date (`bridge.py` and `remote.py` spell out no limit or code; they come from `_protocol_generated`).

- [ ] **Step 7: Commit**

```bash
git add python/src/skython/bridge.py python/src/skython/remote.py python/tests/test_bridge_events.py
git commit -m "feat(python): bridge event pump with a consumer per connection, remote event delivery"
```
