"""4-byte little-endian length prefix + UTF-8 JSON object. Same rules as protocol/PROTOCOL.md.

The in-process transport sends the same JSON text without the prefix (dumps); the bridge and the remote
transport frame it (encode, read_frame)."""
import json
import socket
import struct
import time

from . import _protocol_generated as _g
from .errors import ConnectionLost, EncodeError, ProtocolViolation

MAX_FRAME = _g.MAX_FRAME_BYTES  # a frame header over this closes the connection (protocol.json: max_frame_bytes)


def _body(obj: dict) -> tuple[str, bytes]:
    try:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        body = text.encode("utf-8")
    except UnicodeEncodeError as e:  # a lone surrogate in a string
        raise EncodeError(f"message contains text that is not valid Unicode: {e}") from None
    except (TypeError, ValueError, RecursionError) as e:
        raise EncodeError(f"message cannot be serialised as JSON: {e}") from None
    if len(body) > MAX_FRAME:
        raise EncodeError(f"message is {len(body)} bytes; the limit is {MAX_FRAME}")
    return text, body


def dumps(obj: dict) -> str:
    """The JSON text of one message (a frame body without its length prefix). Same checks as encode()."""
    return _body(obj)[0]


def encode(obj: dict) -> bytes:
    """Serialise one message. Raises EncodeError (a ValueError) for anything that cannot be sent."""
    body = _body(obj)[1]
    return struct.pack("<I", len(body)) + body


def _recv_exact(sock: socket.socket, n: int, deadline: float | None) -> bytes:
    """Read exactly n bytes. With a deadline (time.monotonic() value) the whole read, not each recv, is bounded and
    TimeoutError is raised when it passes; without one, wait as long as it takes (a socket timeout is then
    only a wake-up tick and is retried, keeping the bytes read so far)."""
    buf = bytearray()
    while len(buf) < n:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline passed while reading a frame")
            sock.settimeout(remaining)
        try:
            chunk = sock.recv(min(n - len(buf), 1 << 20))
        except TimeoutError:
            if deadline is None:
                continue
            raise
        if not chunk:
            raise ConnectionLost("connection closed by peer")
        buf += chunk
    return bytes(buf)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def _too_deep(obj) -> bool:
    """True if arrays/objects nest deeper than MAX_JSON_DEPTH (the top-level object is depth 1). Iterative."""
    stack = [(obj, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        if depth > _g.MAX_JSON_DEPTH:
            return True
        stack.extend((c, depth + 1) for c in children if isinstance(c, (dict, list)))
    return False


def _read_frame(sock: socket.socket, deadline: float | None) -> dict:
    (n,) = struct.unpack("<I", _recv_exact(sock, 4, deadline))
    if n > MAX_FRAME:
        raise ProtocolViolation(f"frame of {n} bytes exceeds {MAX_FRAME}")
    body = _recv_exact(sock, n, deadline)
    try:
        obj = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as e:  # JSONDecodeError is a ValueError
        raise ProtocolViolation(f"frame is not UTF-8 JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ProtocolViolation("frame is not a JSON object")
    if _too_deep(obj):
        raise ProtocolViolation(f"frame nests deeper than {_g.MAX_JSON_DEPTH} levels")
    return obj


def read_frame(sock: socket.socket, deadline: float | None = None) -> dict:
    """Read one frame. Raises ConnectionLost on EOF, ProtocolViolation on a bad frame, and TimeoutError
    if `deadline` (a time.monotonic() value) passes first. The socket's timeout is restored on return."""
    previous = sock.gettimeout()
    try:
        return _read_frame(sock, deadline)
    finally:
        if deadline is not None:
            sock.settimeout(previous)
