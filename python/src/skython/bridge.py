"""The dev bridge: the same requests as in-process, from another process, over loopback TCP.

It runs inside the game (skython._host starts it when bridge_enabled is set) and forwards each request text
unchanged to `backend` (the _skython module; a fake in tests); the response text goes back unchanged in a frame.
Rules (protocol/PROTOCOL.md, "Dev bridge transport" and "Handshake"):
- 127.0.0.1 only; a non-loopback peer is closed at accept; at most `max_connections` connections.
- The first frame must be a valid hello within the hello timeout, and at most PRE_AUTH_FRAME_BYTES long.
  A malformed hello is `bad_request`, then the token is checked in constant time (`unauthorized`), then
  the protocol major (`protocol_mismatch`); the server answers handshake_error and closes.
- After the welcome, a frame over MAX_FRAME_BYTES, not UTF-8, not a JSON object, or nested deeper than
  MAX_JSON_DEPTH closes that connection only. A request whose id is already in flight on the connection is
  `bad_request`; more than `max_inflight_per_connection` in flight is `busy`; both answered here, in the
  same response shape the plugin uses.
- Replies waiting to be sent above `max_queued_bytes` mean the client stopped reading: the connection closes.
- When a connection ends, every request it still has in flight is cancelled.
Four threads per connection: a reader (handshake, then requests), a completion thread (wait_any on the
connection's tickets, 5 ms at a time, so a request submitted meanwhile is picked up quickly even when the
others are slow), a writer (the only thread that sends after the welcome) and an event pump (next_events on the
connection's consumer, 0.25 s at a time; each event object becomes one frame through the writer, so the
max_queued_bytes cap covers events too). The connection's consumer is opened once its hello is accepted, travels
beside every request text it submits (the text is forwarded unchanged, so a client cannot name another consumer),
and is closed with the connection, which drops its subscriptions. The Dispatcher's reaper enforces
each request's timeout_ms: the bridge never times a call out itself. With trace_timing (dev only, Task 38) the writer logs
"timing: id=<id> bridge_read=<us> bridge_write=<us>" per request on the given clock (in the game _skython.now_us, the
plugin's steady clock): read when the request frame was complete, write when its response frame was sent. An error from accept() (a client that
reset before it was accepted, no descriptors left) is logged and the bridge keeps accepting."""
import collections
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import struct
import threading
import time

from . import _protocol_generated as _g
from .discovery import Session, write_inactive_session_file, write_session_file

log = logging.getLogger("skython.bridge")

_U64_MAX = 2**64 - 1
_POLL_S = 0.005  # how long the completion thread waits on its tickets per round (it releases the GIL meanwhile)
_IDLE_S = 0.05  # how long it sleeps with no tickets (a new ticket wakes it at once)
_EVENT_POLL_S = 0.25  # how long the pump waits in next_events per round (it releases the GIL meanwhile)
_no_hub_logged = False  # "no event hub" is logged once per process, not once per connection
_ACCEPT_RETRY_S = 0.05  # after a failed accept()
_DRAIN_S = 0.5  # after a handshake_error, how long to read and discard so the error is not lost to a reset
_TOKEN = re.compile(r"[0-9a-f]{%d}" % _g.TOKEN_LENGTH)
# Strings (skipped whole) and brackets. Possessive, so the scan is linear: an unterminated string is consumed to the
# end in one pass instead of being retried from every later quote (a pre-auth frame could otherwise hold the GIL
# for seconds).
_STRUCTURE = re.compile(r'"(?:[^"\\]++|\\.)*+"?|[\[\]{}]', re.S)


def _log_no_consumer(e: Exception) -> None:
    """open_consumer failed. The expected case (a build with no event hub, before Task 39) is logged once per process
    with its documented text; any other failure is unexpected and logged every time, naming the actual error."""
    global _no_hub_logged
    if "no event hub" not in str(e):
        log.warning("bridge: could not open an event consumer (%s: %s); events are unsupported on this connection",
                    type(e).__name__, e)
    elif not _no_hub_logged:
        _no_hub_logged = True
        log.warning("bridge: no event hub (%s); events are unsupported on this connection", e)


class _Violation(Exception):
    """The peer broke the framing rules: close the connection without a reply."""


class _Ended(Exception):
    """The peer went away (EOF, reset) or never said hello."""


def nesting_within(text: str, max_depth: int) -> bool:
    """True if no array/object in `text` is nested deeper than `max_depth`; brackets inside strings do
    not count. A raw-text scan, so a hostile frame is refused before json.loads can recurse on it."""
    depth = 0
    for m in _STRUCTURE.finditer(text):
        c = m.group()
        if c in "[{":
            depth += 1
            if depth > max_depth:
                return False
        elif c in "]}":
            depth -= 1
    return True


def _recv_exact(sock: socket.socket, n: int, deadline: float | None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline passed")
            sock.settimeout(remaining)
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise _Ended("the client closed the connection")
        buf += chunk
    return bytes(buf)


def _read_text(sock: socket.socket, limit: int, deadline: float | None = None) -> str:
    (n,) = struct.unpack("<I", _recv_exact(sock, 4, deadline))
    if n > limit:
        raise _Violation(f"a frame of {n} bytes is over the limit of {limit}")
    try:
        return _recv_exact(sock, n, deadline).decode("utf-8")
    except UnicodeDecodeError:
        raise _Violation("a frame is not UTF-8") from None


def _parse_object(text: str) -> dict:
    if not nesting_within(text, _g.MAX_JSON_DEPTH):
        raise _Violation(f"a frame is nested deeper than {_g.MAX_JSON_DEPTH} levels")
    try:
        obj = json.loads(text)
    except ValueError:
        raise _Violation("a frame is not JSON") from None
    if not isinstance(obj, dict):
        raise _Violation("a frame is not a JSON object")
    return obj


def _frame(text: str) -> bytes:
    body = text.encode("utf-8")
    return struct.pack("<I", len(body)) + body


def _dumps(obj: dict) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def _error_text(req_id: int, code: str, message: str) -> str:
    return _dumps({"type": "response", "id": req_id, "ok": False,
                   "error": {"code": code, "message": message, "detail": {}}})


def _request_id(obj: dict) -> int | None:
    i = obj.get("id")
    return i if type(i) is int and 0 <= i <= _U64_MAX else None


def check_hello(hello: dict, token: str) -> tuple[str | None, str]:
    """(error code or None, message), in the protocol's order: shape, then token, then protocol major."""
    if hello.get("type") != "hello":
        return _g.CODE_BAD_REQUEST, "the first message must be a hello"
    proto = hello.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2
            and all(type(n) is int and 0 <= n <= _g.INT_MAX for n in proto)):
        return _g.CODE_BAD_REQUEST, "protocol must be [major, minor], two integers"
    their = hello.get("token")
    if not (isinstance(their, str) and _TOKEN.fullmatch(their)):
        return _g.CODE_BAD_REQUEST, f"token must be {_g.TOKEN_LENGTH} lowercase hex characters"
    if "client" in hello and not isinstance(hello["client"], str):
        return _g.CODE_BAD_REQUEST, "client must be a string"
    if not hmac.compare_digest(their.encode("ascii"), token.encode("ascii")):
        return _g.CODE_UNAUTHORIZED, "wrong token (is the session file from an earlier game run?)"
    if proto[0] != _g.PROTOCOL_MAJOR:
        return _g.CODE_PROTOCOL_MISMATCH, (f"the client speaks protocol {proto[0]}.{proto[1]}, "
                                           f"the game {_g.PROTOCOL_MAJOR}.{_g.PROTOCOL_MINOR}")
    return None, ""


class _Conn:
    def __init__(self, server: "BridgeServer", sock: socket.socket, peer):
        self.server = server
        self.sock = sock
        self.peer = peer
        self.client = "?"
        self.cond = threading.Condition()
        self.tickets: dict[int, int | None] = {}  # ticket -> request id (None when the request had no usable id)
        self.ids: set[int] = set()
        self.out: collections.deque[tuple[bytes, int | None, int | None]] = collections.deque()  # frame, id, read_us
        self.read_us: dict[int, int] = {}  # ticket -> bridge_read stamp (trace_timing only)
        self.out_bytes = 0
        self.closed = False
        self.consumer = 0  # the _skython event consumer; 0 until the hello is accepted

    def start(self) -> None:
        threading.Thread(target=self._read_loop, name=f"bridge-read-{self.peer[1]}", daemon=True).start()

    # -- reader ---------------------------------------------------------------------------------------
    def _read_loop(self) -> None:
        reason = "the client closed the connection"
        try:
            if not self._handshake():
                return
            self.sock.settimeout(None)
            threading.Thread(target=self._write_loop, name=f"bridge-write-{self.peer[1]}", daemon=True).start()
            threading.Thread(target=self._complete_loop, name=f"bridge-done-{self.peer[1]}", daemon=True).start()
            if self.consumer:  # no consumer (no event hub): no events to pump, and next_events(0) would spin
                threading.Thread(target=self._pump_loop, name=f"bridge-pump-{self.peer[1]}", daemon=True).start()
            while True:
                text = _read_text(self.sock, _g.MAX_FRAME_BYTES)
                read_us = self.server.clock() if self.server.clock is not None else None
                self._on_request(text, _parse_object(text), read_us)
        except (_Ended, _Violation) as e:
            reason = str(e)
        except TimeoutError:
            reason = f"no hello within {self.server.hello_timeout:g} s"
        except OSError as e:
            reason = str(e) or type(e).__name__
        except Exception:  # noqa: BLE001 - a bug here must close one connection, never the bridge
            log.exception("bridge: unexpected error on a connection from %s", self.client)
            reason = "internal error"
        finally:
            self.close(reason)

    def _handshake(self) -> bool:
        deadline = time.monotonic() + self.server.hello_timeout
        hello = _parse_object(_read_text(self.sock, _g.PRE_AUTH_FRAME_BYTES, deadline))
        code, message = check_hello(hello, self.server.token)
        if code is not None:
            self._refuse(code, message)
            return False
        try:
            consumer = self.server.backend.open_consumer()  # before the welcome: every request has a consumer
        except Exception as e:  # noqa: BLE001 - no event hub (the game build before Task 39): serve without events
            _log_no_consumer(e)
            consumer = 0  # requests still go with consumer 0; events.* answer unsupported (the hub is null)
        with self.cond:  # set under cond, so exactly one of this thread and close() closes the consumer
            if not self.closed:
                self.consumer, consumer = consumer, 0
        if consumer:  # the connection was closed meanwhile (BridgeServer.close): do not leak the consumer
            self.server.backend.close_consumer(consumer)
            raise _Ended("the connection was closed during the handshake")
        if isinstance(hello.get("client"), str):
            self.client = hello["client"].encode("utf-8")[:_g.MAX_CLIENT_NAME_BYTES].decode("utf-8", "ignore")
        info = self.server.welcome_info() if callable(self.server.welcome_info) else self.server.welcome_info
        welcome = {**info, "type": "welcome", "protocol": list(_g.PROTOCOL_VERSION), "capabilities": list(_g.OPS)}
        self.sock.sendall(_frame(_dumps(welcome)))
        log.info("bridge: %s connected", self.client)
        return True

    def _refuse(self, code: str, message: str) -> None:
        """Answer handshake_error, stop sending, and drain briefly so the error is read, not reset away."""
        try:
            self.sock.sendall(_frame(_dumps({"type": "handshake_error", "code": code, "message": message})))
            self.sock.shutdown(socket.SHUT_WR)
            deadline = time.monotonic() + _DRAIN_S
            while time.monotonic() < deadline:
                self.sock.settimeout(max(0.01, deadline - time.monotonic()))
                if not self.sock.recv(65536):
                    break
        except OSError:
            pass
        log.info("bridge: refused a client: %s", code)

    def _on_request(self, text: str, obj: dict, read_us: int | None = None) -> None:
        rid = _request_id(obj)
        refusal = None
        with self.cond:
            if self.closed:
                return
            if rid is not None and rid in self.ids:
                refusal = _error_text(rid, _g.CODE_BAD_REQUEST, f"request id {rid} is already in flight on this connection")
            elif len(self.tickets) >= self.server.max_inflight_per_connection:
                refusal = _error_text(rid or 0, _g.CODE_BUSY,
                                      f"{self.server.max_inflight_per_connection} requests are already in flight on this connection")
            elif rid is not None:
                self.ids.add(rid)
        if refusal is not None:
            self._enqueue(refusal)
            return
        ticket = self.server.backend.submit(text, self.consumer)  # the consumer travels beside the text, never in it
        with self.cond:
            if not self.closed:
                self.tickets[ticket] = rid
                if read_us is not None and rid is not None:
                    self.read_us[ticket] = read_us
                self.cond.notify_all()
                return
        self.server.backend.cancel(ticket)  # the connection closed while the request was being submitted

    # -- completion -----------------------------------------------------------------------------------
    def _complete_loop(self) -> None:
        backend = self.server.backend
        try:
            while True:
                with self.cond:
                    while not self.closed and not self.tickets:
                        self.cond.wait(_IDLE_S)
                    if self.closed:
                        return
                    tickets = list(self.tickets)
                for ticket, text in backend.wait_any(tickets, _POLL_S):
                    with self.cond:
                        if ticket not in self.tickets:
                            continue
                        rid = self.tickets.pop(ticket)
                        self.ids.discard(rid)
                        read_us = self.read_us.pop(ticket, None)
                    self._enqueue(text, rid, read_us)
        except Exception:  # noqa: BLE001
            log.exception("bridge: waiting for results failed")
            self.close("internal error")

    # -- events ---------------------------------------------------------------------------------------
    def _pump_loop(self) -> None:
        backend = self.server.backend
        try:
            while not self.closed:
                text = backend.next_events(self.consumer, _EVENT_POLL_S)  # close_consumer wakes it
                if self.closed:
                    return
                for event in json.loads(text):
                    self._enqueue(_dumps(event), event=True)  # one frame per event, same max_queued_bytes cap
        except Exception:  # noqa: BLE001
            log.exception("bridge: the event pump failed")
            self.close("internal error")

    # -- writer ---------------------------------------------------------------------------------------
    def _enqueue(self, text: str, rid: int | None = None, read_us: int | None = None, *, event: bool = False) -> None:
        data = _frame(text)
        if len(data) - 4 > _g.MAX_FRAME_BYTES:
            if event:
                # Unreachable with the real limits: the hub's per-consumer byte budget (max_queued_event_bytes_per_
                # consumer, each event counted as its data plus a 96-byte envelope) drops an event that large before
                # it is queued. Should one come anyway it is counted and dropped: an id-0 response would answer
                # nothing the client sent.
                self.server._oversize_event()
                return
            try:
                rid = _request_id(json.loads(text)) or 0
            except ValueError:
                rid = 0
            data = _frame(_error_text(rid, _g.CODE_INTERNAL, "the response is over the frame limit"))
        with self.cond:
            if self.closed:
                return
            if self.out_bytes + len(data) <= self.server.max_queued_bytes:
                self.out.append((data, rid, read_us))
                self.out_bytes += len(data)
                self.cond.notify_all()
                return
        self.close(f"the client stopped reading: more than {self.server.max_queued_bytes} bytes of replies are waiting")

    def _write_loop(self) -> None:
        while True:
            with self.cond:
                while not self.closed and not self.out:
                    self.cond.wait()
                if self.closed:
                    return
                data, rid, read_us = self.out.popleft()
            try:
                self.sock.sendall(data)
            except OSError as e:
                self.close(str(e) or type(e).__name__)
                return
            with self.cond:
                self.out_bytes -= len(data)
            if read_us is not None:
                log.info("timing: id=%d bridge_read=%d bridge_write=%d", rid, read_us, self.server.clock())

    # -- end ------------------------------------------------------------------------------------------
    def close(self, reason: str) -> None:
        with self.cond:
            if self.closed:
                return
            self.closed = True
            tickets = list(self.tickets)
            self.tickets.clear()
            self.ids.clear()
            self.read_us.clear()
            self.out.clear()
            self.cond.notify_all()
        for ticket in tickets:
            try:
                self.server.backend.cancel(ticket)
            except Exception:  # noqa: BLE001
                log.exception("bridge: cancel failed")
        if self.consumer:
            try:
                self.server.backend.close_consumer(self.consumer)  # drops its subscriptions, wakes the pump
            except Exception:  # noqa: BLE001
                log.exception("bridge: closing the event consumer failed")
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()
        self.server._forget(self)
        log.info("bridge: %s disconnected (%s); %d call(s) cancelled", self.client, reason, len(tickets))


class BridgeServer:
    """A listening dev bridge. Use serve() to start one."""

    def __init__(self, backend, *, host: str, port: int, token: str | None, session_file, welcome_info,
                 max_connections: int, max_inflight_per_connection: int, hello_timeout: float,
                 max_queued_bytes: int, trace_timing: bool = False, clock=None):
        if not ipaddress.ip_address(host).is_loopback:
            raise ValueError(f"the dev bridge binds loopback only, not {host!r}")
        if token is None:
            token = secrets.token_hex(_g.TOKEN_LENGTH // 2)
        if not (isinstance(token, str) and _TOKEN.fullmatch(token)):
            raise ValueError(f"token must be {_g.TOKEN_LENGTH} lowercase hex characters")
        self.backend = backend
        self.token = token
        self.session_file = session_file
        self.welcome_info = welcome_info
        self.max_connections = max_connections
        self.max_inflight_per_connection = max_inflight_per_connection
        self.hello_timeout = hello_timeout
        self.max_queued_bytes = max_queued_bytes
        # The bridge's timing clock, or None when trace_timing is off (then no stamp is taken at all).
        self.clock = (clock or (lambda: time.monotonic_ns() // 1000)) if trace_timing else None
        self._conns: set[_Conn] = set()
        self._lock = threading.Lock()
        self._closing = False
        self.oversize_events = 0  # event frames over MAX_FRAME_BYTES, dropped (unreachable; see _Conn._enqueue)
        self._lsock = socket.socket()
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: nobody else may bind our port
                self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._lsock.bind((host, port))
            self._lsock.listen()
        except OSError as e:
            self._lsock.close()
            self._mark_inactive(f"the bridge could not listen on {host}:{port}: {e}")
            raise
        try:
            self.host = host
            self.port = self._lsock.getsockname()[1]
            if session_file:
                info = welcome_info() if callable(welcome_info) else welcome_info
                write_session_file(session_file, Session(host, self.port, token, os.getpid(), info.get("plugin_version")))
            self._accepter = threading.Thread(target=self._accept_loop, name="bridge-accept", daemon=True)
            self._accepter.start()
        except BaseException as e:  # listening worked but the rest of the start did not: do not keep the port
            self._closing = True
            self._lsock.close()
            self._mark_inactive(f"the bridge could not start after listening: {e!r}")
            raise
        log.info("bridge: listening on %s:%d", host, self.port)

    def _mark_inactive(self, reason: str) -> None:
        """Record why there is no bridge. A failure to write the record is logged, never raised: it must not hide the
        error that is being reported."""
        if not self.session_file:
            return
        try:
            write_inactive_session_file(self.session_file, reason, pid=os.getpid())
        except Exception:  # noqa: BLE001
            log.exception("bridge: could not write the inactive session file")

    def _accept_loop(self) -> None:
        while True:
            try:
                sock, peer = self._lsock.accept()
            except OSError as e:
                if self._closing:
                    return
                log.warning("bridge: accept failed: %s; still accepting", e)
                time.sleep(_ACCEPT_RETRY_S)
                continue
            if self._closing:
                sock.close()
                return
            conn = None
            try:
                if not ipaddress.ip_address(peer[0]).is_loopback:
                    log.warning("bridge: refused a non-loopback peer %s", peer[0])
                    sock.close()
                    continue
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    full = len(self._conns) >= self.max_connections
                    if not full:
                        conn = _Conn(self, sock, peer)
                        self._conns.add(conn)
                if full:
                    log.warning("bridge: %d connections already open; refused one more", self.max_connections)
                    sock.close()
                    continue
                conn.start()
            except Exception:  # noqa: BLE001 - one bad accept must not stop the bridge
                log.exception("bridge: accept failed")
                if conn is not None:
                    self._forget(conn)  # it was registered: give its slot back
                sock.close()

    def _oversize_event(self) -> None:
        with self._lock:
            self.oversize_events += 1
            first = self.oversize_events == 1
        if first:
            log.warning("bridge: an event frame over the frame limit (%d bytes) was dropped; later ones are only counted "
                        "(BridgeServer.oversize_events)", _g.MAX_FRAME_BYTES)

    def _forget(self, conn: _Conn) -> None:
        with self._lock:
            self._conns.discard(conn)

    def connections(self) -> int:
        with self._lock:
            return len(self._conns)

    def close(self) -> None:
        """Stop accepting, close every connection (their calls are cancelled), mark the session file inactive."""
        if self._closing:
            return
        self._closing = True
        try:  # closing a listener does not wake a blocked accept(); a throwaway connection does
            socket.create_connection((self.host, self.port), timeout=1).close()
        except OSError:
            pass
        self._accepter.join(2)
        self._lsock.close()
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            c.close("the bridge was closed")
        if self.session_file:
            write_inactive_session_file(self.session_file, "the bridge was closed", pid=os.getpid())
        log.info("bridge: closed")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve(backend, *, host: str = _g.HOST, port: int = _g.CONFIG_KEYS["port"]["default"], token: str | None = None,
          session_file=None, welcome_info, max_connections: int = _g.MAX_CONNECTIONS,
          max_inflight_per_connection: int = _g.CONFIG_KEYS["max_inflight_per_connection"]["default"],
          hello_timeout: float = _g.HELLO_TIMEOUT_MS / 1000,
          max_queued_bytes: int = _g.MAX_QUEUED_BYTES_PER_CONNECTION, trace_timing: bool = False,
          clock=None) -> BridgeServer:
    """Start a dev bridge over `backend` (anything with submit(text, consumer), wait_any, cancel, open_consumer,
    close_consumer and next_events: the _skython module).

    `welcome_info` is a dict, or a function returning one, with plugin_version, game_version, runtime and
    epoch (a function gives every client the current epoch). Port 0 picks a free port (see .port). The
    session file, when given, gets the port and token; if the bridge cannot start, it gets an inactive
    record with the reason instead, and the error is raised. trace_timing logs one timing line per request (Task 38;
    clock: a function returning microseconds, default time.monotonic_ns() // 1000)."""
    return BridgeServer(backend, host=host, port=port, token=token, session_file=session_file,
                        welcome_info=welcome_info, max_connections=max_connections,
                        max_inflight_per_connection=max_inflight_per_connection,
                        hello_timeout=hello_timeout, max_queued_bytes=max_queued_bytes,
                        trace_timing=trace_timing, clock=clock)
