"""The in-process transport: the Game API inside the game, over the built-in _skyrim module.

Requests and responses are the same JSON text as on the dev bridge (protocol/PROTOCOL.md). _skyrim.call
releases the GIL while it waits, so any number of Python threads may call at once; the game's own threads
never run Python."""
import itertools
import json
import threading

from . import _framing
from . import _protocol_generated as _g
from . import errors
from .game import Game


def _object(text, what: str) -> dict:
    try:
        obj = json.loads(text)
    except (TypeError, ValueError) as e:
        raise errors.ProtocolViolation(f"{what} is not JSON: {e}") from None
    if not isinstance(obj, dict):
        raise errors.ProtocolViolation(f"{what} is not a JSON object: {obj!r}")
    return obj


class InProcessTransport:
    """A Transport over `module` (the _skyrim module; imported when not given)."""

    def __init__(self, module=None):
        if module is None:
            import _skyrim as module  # built into SkyrimPythonCore.dll; a fake in tests
        self._m = module
        proto = tuple(module.PROTOCOL)
        if len(proto) != 2 or proto[0] != _g.PROTOCOL_MAJOR:
            raise errors.ProtocolMismatch(_g.CODE_PROTOCOL_MISMATCH, f"_skyrim speaks protocol {'.'.join(map(str, proto))}, "
                                          f"this package speaks {_g.PROTOCOL_MAJOR}.{_g.PROTOCOL_MINOR}")
        cfg = _object(module.config(), "_skyrim.config()")
        state = _object(module.state(), "_skyrim.state()")
        self.welcome = {"type": "welcome", "protocol": list(proto), "plugin_version": cfg.get("plugin_version"),
                        "game_version": cfg.get("game_version"), "runtime": cfg.get("runtime"),
                        "epoch": state.get("epoch", 0), "capabilities": list(_g.OPS), "backend": module.BACKEND}
        self.closed = False
        self._ids = itertools.count(1)
        self._ids_lock = threading.Lock()

    def request(self, op: str, args: dict, wait_s: float) -> dict:
        if self.closed:
            raise errors.ConnectionLost("the in-process transport is closed")
        with self._ids_lock:
            req_id = next(self._ids)
        text = _framing.dumps({"type": "request", "id": req_id, "op": op, "args": args})  # EncodeError: nothing sent
        resp = _object(self._m.call(text, wait_s), f"the response to {op}")
        if type(resp.get("id")) is not int or resp["id"] != req_id:
            raise errors.ProtocolViolation(f"the response to {op} has id {resp.get('id')!r}, not {req_id}")
        return resp

    def close(self) -> None:
        self.closed = True


def connect_in_process(*, module=None, call_timeout: float = _g.CALL_TIMEOUT_S,
                       retry_not_ready: bool | float = False) -> Game:
    """A Game over _skyrim. skyrim.connect() calls this inside the game (Task 22)."""
    return Game(InProcessTransport(module), call_timeout=call_timeout, retry_not_ready=retry_not_ready)
