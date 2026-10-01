import copy
import pickle

import pytest

from skyrim import errors

SAMPLES = [
    errors.ConnectionLost("gone"),
    errors.NotRunning("no session"),
    errors.CallTimeout("slow"),
    errors.ProtocolViolation("bad frame"),
    errors.EncodeError("too big"),
    errors.HandshakeError("bad_request", "no"),
    errors.Unauthorized("unauthorized", "bad token"),
    errors.ProtocolMismatch("protocol_mismatch", "server speaks 2.0"),
    errors.RemoteError("weird_new_code", "x", {"a": 1}),
    errors.NotReady("not_ready", "loading", {"reason": "loading"}),
    errors.TypeMismatch("type_mismatch", "out of range"),
    errors.InternalError("internal", "boom"),
]


def _clones(exc):
    return [pickle.loads(pickle.dumps(exc)), copy.copy(exc), copy.deepcopy(exc)]


@pytest.mark.parametrize("exc", SAMPLES, ids=lambda e: type(e).__name__)
def test_exceptions_survive_pickle_and_copy(exc):
    for clone in _clones(exc):
        assert type(clone) is type(exc)
        assert str(clone) == str(exc)
        assert clone.__dict__ == exc.__dict__


def test_every_remote_subclass_round_trips():
    for cls in errors._REMOTE.values():
        exc = cls(cls.code, "m", {"k": [1]})
        clone = pickle.loads(pickle.dumps(exc))
        assert (type(clone), clone.code, clone.message, clone.detail) == (cls, cls.code, "m", {"k": [1]})


def test_malformed_error_objects_become_typed_errors():
    for bad in ("boom", None, 7, ["x"]):
        exc = errors.remote_error_from_wire(bad)
        assert isinstance(exc, errors.InternalError)
        assert "malformed" in exc.message
    exc = errors.remote_error_from_wire({"code": ["x"], "message": 5, "detail": "d"})
    assert isinstance(exc, errors.InternalError)
    assert exc.message == "5" and exc.detail == {"raw": "d"}
    assert type(errors.remote_error_from_wire({"code": "no_such_code", "message": "m"})) is errors.RemoteError


def test_malformed_handshake_error_code():
    assert type(errors.handshake_error_from_wire({"code": ["x"]})) is errors.HandshakeError
