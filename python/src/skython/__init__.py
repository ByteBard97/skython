"""Call Papyrus in a running Skyrim from Python."""
from .discovery import Session, find_session  # noqa: F401
from .errors import *  # noqa: F401,F403
from .game import Batch, Game, Transport  # noqa: F401
from .inprocess import InProcessTransport, connect_in_process  # noqa: F401
from .remote import RemoteTransport, connect_remote  # noqa: F401
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"

_REMOTE_ONLY = ("host", "port", "token", "client_name", "connect_timeout", "send_timeout")


def connect(session=None, **kw) -> Game:
    """The running game, from anywhere.

    Inside the game (the built-in `_skython` module reports BACKEND == "game") this is the in-process
    transport. Anywhere else, or when the caller names a bridge (session, host, port, token, ...), it is the
    dev bridge through connect_remote(); keyword arguments are connect_remote()'s. `call_timeout` and
    `retry_not_ready` apply in both modes."""
    if session is None and not any(k in kw for k in _REMOTE_ONLY):
        try:
            import _skython
        except ImportError:
            _skython = None
        if _skython is not None and getattr(_skython, "BACKEND", None) == "game":
            return connect_in_process(module=_skython, **kw)
    return connect_remote(session, **kw)
