"""Exceptions. Server error codes map one-to-one onto RemoteError subclasses.

Every exception here survives pickle and copy (multiprocessing, concurrent.futures and pytest-xdist
all rely on that), so the classes with a custom __init__ define __reduce__.

The classes are written out so that `errors.NotReady`, pickling and type checkers work. Which wire code maps to
which class is not decided here: it is the generated table in _protocol_generated (protocol/protocol.json).
At the bottom of this module each class is looked up by the name the table gives and takes its `code` from it.
"""
from . import _protocol_generated as _g


class SkythonError(Exception):
    """Base class for everything this package raises on purpose."""


class ConnectionLost(SkythonError):
    """The connection to the game closed (game quit, crashed, stopped reading, or the client closed it)."""


class NotRunning(SkythonError):
    """No reachable Skython server: no session file, connection refused, or no welcome in time."""


class CallTimeout(SkythonError):
    """The client stopped waiting for a response. The call may still run in the game."""


class ProtocolViolation(SkythonError):
    """The peer sent something that is not protocol v1."""


class EncodeError(SkythonError, ValueError):
    """A request cannot be put on the wire: over the frame limit, or text that is not valid Unicode.
    Raised before anything is sent, so the connection is unaffected."""


class WaitTimeout(SkythonError):
    """Game.wait_for gave up: the predicate returned nothing truthy within its timeout."""


class HandshakeError(SkythonError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def __reduce__(self):
        return (self.__class__, (self.code, self.message))


class Unauthorized(HandshakeError):
    """Wrong token. Usually a stale session file from an earlier game run."""


class ProtocolMismatch(HandshakeError):
    """Client and plugin speak different protocol major versions."""


class RemoteError(SkythonError):
    code = _g.CODE_INTERNAL  # the class default; each subclass below gets its code from the generated table

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}

    def __reduce__(self):
        return (self.__class__, (self.code, self.message, self.detail))


class NotReady(RemoteError): """The game cannot run the request yet; detail["reason"] says why."""
class Busy(RemoteError): """An in-flight cap was reached; retry later."""
class BadRequest(RemoteError): """The request is malformed."""
class UnknownFunction(RemoteError): """The script or function does not exist in the running game."""
class TypeMismatch(RemoteError): """A value has the wrong Papyrus type or is out of range."""
class StaleReference(RemoteError): """A runtime-created reference no longer names the object it was handed out for."""
class NotFound(RemoteError): """A form, alias, quest or property does not exist."""
class Timeout(RemoteError): """The call did not finish within its timeout_ms; it may still run in the game."""
class VmError(RemoteError): """The Papyrus VM refused or failed the call."""
class Unsupported(RemoteError): """Valid protocol, but this plugin build cannot do it."""
class InternalError(RemoteError): """An unexpected failure inside the plugin."""


def _registry(names: dict, base: type) -> dict:
    """wire code -> class, from the generated table. A named class that does not exist is an ImportError."""
    out = {}
    for wire_code, class_name in names.items():
        cls = globals().get(class_name)
        if not (isinstance(cls, type) and issubclass(cls, base)):
            raise ImportError(f"skython.errors has no {base.__name__} subclass {class_name!r}, "
                              f"which protocol.json names for the code {wire_code!r}")
        cls.code = wire_code
        out[wire_code] = cls
    return out


_REMOTE = _registry(_g.REMOTE_EXCEPTIONS, RemoteError)
_HANDSHAKE = _registry(_g.HANDSHAKE_EXCEPTIONS, HandshakeError)


def _text(value, default: str) -> str:
    if value is None:
        return default
    return value if isinstance(value, str) else repr(value)


def remote_error_from_wire(err) -> RemoteError:
    """Build the exception for a response's "error" member. Tolerates a malformed one (not an object,
    non-string code or message, non-object detail): the caller gets an InternalError that says so."""
    if not isinstance(err, dict):
        return InternalError(_g.CODE_INTERNAL, f"malformed error object from the server: {err!r}")
    code = err.get("code")
    if not isinstance(code, str):
        code = _g.CODE_INTERNAL
    detail = err.get("detail")
    if detail is not None and not isinstance(detail, dict):
        detail = {"raw": detail}
    return _REMOTE.get(code, RemoteError)(code, _text(err.get("message"), ""), detail)


def handshake_error_from_wire(msg: dict) -> HandshakeError:
    code = msg.get("code")
    if not isinstance(code, str):
        code = _g.CODE_BAD_REQUEST
    return _HANDSHAKE.get(code, HandshakeError)(code, _text(msg.get("message"), ""))


__all__ = ["SkythonError", "ConnectionLost", "NotRunning", "CallTimeout", "ProtocolViolation", "EncodeError",
           "WaitTimeout", "HandshakeError", "Unauthorized", "ProtocolMismatch", "RemoteError", *[c.__name__ for c in _REMOTE.values()]]
