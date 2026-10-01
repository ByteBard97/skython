import socket
import struct
import threading
import time

import pytest

from skyrim import _framing
from skyrim.errors import ConnectionLost, EncodeError, ProtocolViolation


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    yield a, b
    a.close()
    b.close()


def test_round_trip_split_writes(pair):
    a, b = pair
    data = _framing.encode({"type": "hello", "x": 1})
    assert data[:4] == struct.pack("<I", len(data) - 4)
    a.sendall(data[:3])
    a.sendall(data[3:])
    assert _framing.read_frame(b) == {"type": "hello", "x": 1}


def test_eof_is_connection_lost(pair):
    a, b = pair
    a.close()
    with pytest.raises(ConnectionLost):
        _framing.read_frame(b)


def test_oversized_header_is_protocol_violation(pair):
    a, b = pair
    a.sendall(struct.pack("<I", _framing.MAX_FRAME + 1))
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)


def test_non_object_payload_is_protocol_violation(pair):
    a, b = pair
    body = b"[1,2]"
    a.sendall(struct.pack("<I", len(body)) + body)
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)


# -- encode failures are a typed client error, not a bare ValueError / UnicodeEncodeError ---------------
def test_oversize_message_is_encode_error():
    with pytest.raises(EncodeError, match="limit"):
        _framing.encode({"x": "a" * _framing.MAX_FRAME})


def test_lone_surrogate_is_encode_error():
    with pytest.raises(EncodeError, match="Unicode"):
        _framing.encode({"x": "\ud800"})


def test_unserialisable_and_non_finite_are_encode_errors():
    with pytest.raises(EncodeError):
        _framing.encode({"x": object()})
    with pytest.raises(EncodeError):
        _framing.encode({"x": float("nan")})


def test_encode_error_is_still_a_value_error():
    assert issubclass(EncodeError, ValueError)


# -- deadline: the whole read is bounded, not each recv ----------------------------------------------------
def test_deadline_bounds_a_slow_drip(pair):
    a, b = pair
    data = _framing.encode({"type": "welcome"})
    stop = threading.Event()

    def drip():
        for i in range(len(data)):
            if stop.wait(0.1):
                return
            try:
                a.sendall(data[i:i + 1])
            except OSError:
                return

    t = threading.Thread(target=drip, daemon=True)
    t.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            _framing.read_frame(b, deadline=time.monotonic() + 0.4)
        assert time.monotonic() - t0 < 1.5
    finally:
        stop.set()
        t.join(2)


def test_deadline_bounds_a_frame_that_never_finishes(pair):
    a, b = pair
    a.sendall(struct.pack("<I", 100) + b"0123456789")
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        _framing.read_frame(b, deadline=time.monotonic() + 0.3)
    assert time.monotonic() - t0 < 1.5


def test_without_a_deadline_socket_timeouts_are_only_wake_ups(pair):
    a, b = pair
    b.settimeout(0.05)  # shorter than the gaps below
    data = _framing.encode({"type": "event", "n": 1})

    def slow_send():
        for piece in (data[:2], data[2:6], data[6:]):
            time.sleep(0.2)
            a.sendall(piece)

    t = threading.Thread(target=slow_send)
    t.start()
    assert _framing.read_frame(b) == {"type": "event", "n": 1}
    t.join(2)


# -- dumps: the JSON text of one message, without the length prefix (the in-process transport sends text) --
def test_dumps_is_the_frame_body():
    msg = {"type": "request", "id": 1, "op": "game.state", "args": {}, "name": "Ærø"}
    text = _framing.dumps(msg)
    assert isinstance(text, str)
    assert _framing.encode(msg)[4:] == text.encode("utf-8")


@pytest.mark.parametrize("bad", [{"x": "\ud800"}, {"x": float("nan")}, {"x": object()}],
                         ids=["surrogate", "nan", "object"])
def test_dumps_fails_like_encode(bad):
    with pytest.raises(EncodeError):
        _framing.dumps(bad)


def test_dumps_applies_the_frame_limit():
    with pytest.raises(EncodeError, match="limit"):
        _framing.dumps({"x": "a" * _framing.MAX_FRAME})


# -- every malformed frame is a ProtocolViolation, never a bare ValueError / RecursionError ----------------
def _send_body(a, body: bytes):
    a.sendall(struct.pack("<I", len(body)) + body)


def _nested(levels: int) -> bytes:
    """An object whose total nesting depth (the object included) is `levels`."""
    return b'{"x":' + b"[" * (levels - 1) + b"]" * (levels - 1) + b"}"


@pytest.mark.parametrize("body", [
    b'{"x":' + b"[" * 100_000,
    b'{"x":' + b"1" * 5000 + b"}",
    b'{"x":NaN}',
    b'{"x":Infinity}',
    b'{"x":-Infinity}',
    _nested(_framing._g.MAX_JSON_DEPTH + 1),
], ids=["deep-open-brackets", "5000-digit-int", "nan", "infinity", "neg-infinity", "65-deep"])
def test_malformed_frames_are_protocol_violations(pair, body):
    a, b = pair
    _send_body(a, body)
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)


def test_depth_limit_is_inclusive(pair):
    a, b = pair
    _send_body(a, _nested(_framing._g.MAX_JSON_DEPTH))
    assert "x" in _framing.read_frame(b)


def test_read_frame_restores_the_socket_timeout(pair):
    a, b = pair
    b.settimeout(7.5)
    a.sendall(_framing.encode({"type": "event"}))
    _framing.read_frame(b, deadline=time.monotonic() + 5)
    assert b.gettimeout() == 7.5
    b.settimeout(None)
    a.sendall(_framing.encode({"type": "event"}))
    _framing.read_frame(b, deadline=time.monotonic() + 5)
    assert b.gettimeout() is None
    with pytest.raises(TimeoutError):
        _framing.read_frame(b, deadline=time.monotonic() + 0.1)
    assert b.gettimeout() is None
