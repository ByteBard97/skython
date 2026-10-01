"""The remote transport: the Game API from another process, over the dev bridge (Task 23).

One socket and one reader thread per connection; any thread may call. Responses are matched to callers by
request id. A send that times out may have put half a frame on the wire, so the connection is then closed,
never reused."""
import itertools
import math
import socket
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

from . import _framing, errors
from . import _protocol_generated as _g
from .discovery import find_session
from .game import Game

PROTOCOL = list(_g.PROTOCOL_VERSION)


def _positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {value!r}")
    return float(value)


class RemoteTransport:
    """A Transport over one handshaken socket (see connect_remote)."""

    def __init__(self, sock: socket.socket, welcome: dict, *, send_timeout: float = _g.SEND_TIMEOUT_S):
        self.welcome = welcome
        self.closed = False
        #: Responses that could not be matched to a request (null, non-integer or unhashable id).
        self.unmatched_responses = 0
        self._sock = sock
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._send_timeout = send_timeout
        sock.settimeout(send_timeout)  # bounds sendall; the reader treats it as a wake-up tick only
        self._reader = threading.Thread(target=self._read_loop, name="skyrim-remote-reader", daemon=True)
        self._reader.start()

    def _read_loop(self):
        try:
            while True:
                msg = _framing.read_frame(self._sock)
                if msg.get("type") != "response":
                    continue  # "event" messages arrive in M2; M1 has no subscriptions, so none are sent
                req_id = msg.get("id")
                if type(req_id) is not int:  # null, a list (unhashable), a bool, a string: nobody to give it to
                    self.unmatched_responses += 1
                    continue
                with self._lock:
                    fut = self._pending.pop(req_id, None)
                if fut is not None:
                    fut.set_result(msg)
        except Exception as e:  # noqa: BLE001 - any read failure ends the connection
            self._fail_all(str(e) or type(e).__name__)

    def _close_socket(self):
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def _fail_all(self, reason: str):
        """The connection is unusable: mark it closed, close the socket, fail every waiting caller."""
        with self._lock:
            self.closed = True
            pending, self._pending = self._pending, {}
        self._close_socket()
        for fut in pending.values():
            fut.set_exception(errors.ConnectionLost(reason))

    def _send_frame(self, frame: bytes):
        with self._send_lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            try:
                self._sock.sendall(frame)
            except TimeoutError:
                # Part of the frame may be on the wire, so the stream is unusable: close, never retry.
                reason = f"the game did not read our request within {self._send_timeout:g}s; connection closed"
            except OSError as e:
                reason = str(e) or type(e).__name__
            else:
                return
            # Mark closed BEFORE releasing the send lock, so a queued sender sees it and writes nothing.
            with self._lock:
                self.closed = True
        self._fail_all(reason)  # outside the send lock: closes the socket, fails the waiting callers
        raise errors.ConnectionLost(reason)

    def request(self, op: str, args: dict, wait_s: float) -> dict:
        req_id = next(self._ids)
        # Encode BEFORE registering the future: an oversize or unencodable request raises EncodeError
        # and leaves nothing behind in _pending.
        frame = _framing.encode({"type": "request", "id": req_id, "op": op, "args": args})
        fut: Future = Future()
        with self._lock:
            if self.closed:
                raise errors.ConnectionLost("connection is closed")
            self._pending[req_id] = fut
        try:
            self._send_frame(frame)
            return fut.result(timeout=wait_s)
        except BaseException as e:
            with self._lock:
                self._pending.pop(req_id, None)
            if isinstance(e, FutureTimeout):
                raise errors.CallTimeout(
                    f"{op}: the bridge sent no response within {wait_s:.1f}s (the call may still run in the game)") from None
            raise

    def close(self) -> None:
        with self._lock:
            self.closed = True
        self._close_socket()
        self._reader.join(timeout=2)  # the reader then fails any caller still waiting with ConnectionLost


def _handshake(sock: socket.socket, token: str, client_name: str, deadline: float) -> dict:
    """Send hello, read welcome. The WHOLE exchange is bounded by `deadline`, not each recv."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("connect_timeout used up before the hello was sent")
    sock.settimeout(remaining)
    sock.sendall(_framing.encode({"type": "hello", "protocol": PROTOCOL, "token": token, "client": client_name}))
    msg = _framing.read_frame(sock, deadline)
    if msg.get("type") == "handshake_error":
        raise errors.handshake_error_from_wire(msg)
    if msg.get("type") != "welcome":
        raise errors.ProtocolViolation(f"expected welcome, got {msg.get('type')!r}")
    proto = msg.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2 and all(type(n) is int for n in proto)):
        raise errors.ProtocolViolation(f"welcome has no [major, minor] protocol: {proto!r}")
    if proto[0] != PROTOCOL[0]:
        raise errors.ProtocolMismatch(_g.CODE_PROTOCOL_MISMATCH, f"the game speaks protocol {proto[0]}.{proto[1]}, "
                                      f"this client speaks {PROTOCOL[0]}.{PROTOCOL[1]}")
    if "epoch" in msg and type(msg["epoch"]) is not int:
        raise errors.ProtocolViolation(f"welcome epoch is not an integer: {msg['epoch']!r}")
    return msg


def connect_remote(session=None, *, host: str = _g.HOST, port: int | None = None, token: str | None = None,
                   client_name: str = _g.CLIENT_NAME, connect_timeout: float = _g.CONNECT_TIMEOUT_S,
                   call_timeout: float = _g.CALL_TIMEOUT_S, retry_not_ready: bool | float = False,
                   send_timeout: float = _g.SEND_TIMEOUT_S) -> Game:
    """Connect to the dev bridge and handshake. Raises NotRunning, Unauthorized, ProtocolMismatch or
    ProtocolViolation; a failed handshake never leaves the socket open.

    `connect_timeout` bounds the whole connect-hello-welcome exchange. `send_timeout` bounds how long a
    request may sit unread by the game before the connection is closed (ConnectionLost)."""
    _positive(connect_timeout, "connect_timeout")
    _positive(send_timeout, "send_timeout")
    _positive(call_timeout, "call_timeout")
    s = find_session(session, host=host, port=port, token=token)
    deadline = time.monotonic() + connect_timeout
    try:
        sock = socket.create_connection((s.host, s.port), timeout=connect_timeout)
    except OSError as e:
        raise errors.NotRunning(f"cannot connect to {s.host}:{s.port}: {e}") from None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # split writes otherwise stall ~40 ms
        try:
            welcome = _handshake(sock, s.token, client_name, deadline)
        except TimeoutError:
            raise errors.NotRunning(
                f"{s.host}:{s.port} accepted but sent no welcome within {connect_timeout}s") from None
        except (OSError, errors.ConnectionLost) as e:
            raise errors.NotRunning(f"{s.host}:{s.port} closed during the handshake: {e}") from None
        transport = RemoteTransport(sock, welcome, send_timeout=send_timeout)
    except BaseException:
        sock.close()
        raise
    try:
        return Game(transport, call_timeout=call_timeout, retry_not_ready=retry_not_ready)
    except BaseException:
        transport.close()  # a bad Game argument must not leak the socket or the reader thread
        raise
