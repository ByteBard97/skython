"""Find the dev bridge: explicit port+token, a session file, or environment variables. The bridge (Task 23)
writes the session file with write_session_file, or write_inactive_session_file when it is not serving."""
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import _protocol_generated as _g
from .errors import NotRunning

_PORT_MAX = _g.CONFIG_KEYS["port"]["max"]


@dataclass(frozen=True)
class Session:
    host: str
    port: int
    token: str
    pid: int | None = None
    plugin_version: str | None = None


def _port(value, source: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise NotRunning(f"{source} is not a port number: {value!r}") from None
    if isinstance(value, bool) or not 1 <= port <= _PORT_MAX:
        raise NotRunning(f"{source} is not a port number (1-{_PORT_MAX}): {value!r}")
    return port


def _read(path: Path, host: str) -> Session:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotRunning(f"no session file at {path}; is the game running with the dev bridge enabled?") from None
    except (OSError, ValueError) as e:
        raise NotRunning(f"session file {path} is unreadable: {e}") from None
    if isinstance(data, dict) and data.get("active") is False:
        raise NotRunning(f"session file {path} says the bridge is not running: {data.get('reason', 'no reason given')}")
    try:
        port = _port(data["port"], f"the port in session file {path}")
        return Session(host, port, str(data["token"]), data.get("pid"), data.get("plugin_version"))
    except (KeyError, TypeError, AttributeError) as e:
        raise NotRunning(f"session file {path} is unreadable: missing {e}") from None


def find_session(session=None, *, host: str = _g.HOST, port: int | None = None, token: str | None = None,
                 env=os.environ) -> Session:
    if (port is None) != (token is None):
        raise NotRunning(f"port= and token= must be given together (got only {'port' if token is None else 'token'})")
    if port is not None:
        return Session(host, _port(port, "port="), token)
    if session is not None:
        return _read(Path(session), host)
    if env.get(_g.ENV_SESSION):
        return _read(Path(env[_g.ENV_SESSION]), host)
    env_port, env_token = env.get(_g.ENV_PORT), env.get(_g.ENV_TOKEN)
    if env_port and env_token:
        return Session(host, _port(env_port, _g.ENV_PORT), env_token)
    if env_port or env_token:
        have, missing = (_g.ENV_PORT, _g.ENV_TOKEN) if env_port else (_g.ENV_TOKEN, _g.ENV_PORT)
        raise NotRunning(f"{have} is set but {missing} is not; the pair must be given together "
                         f"(or set {_g.ENV_SESSION} instead)")
    raise NotRunning(f"no session given: pass session=/port=+token=, or set {_g.ENV_SESSION} "
                     f"(or {_g.ENV_PORT} and {_g.ENV_TOKEN})")


def _write_json(path, data: dict) -> None:
    """Write `data` to `path` atomically: a reader sees the old file or the new one, never half of one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)  # plain str/int/None values: only an OSError (disk full) can stop it
    os.replace(tmp, path)


def write_session_file(path, session: Session) -> None:
    """The active session file: exactly the generated SESSION_FILE_FIELDS."""
    data = {"port": session.port, "token": session.token, "pid": session.pid,
            "plugin_version": session.plugin_version, "protocol": list(_g.PROTOCOL_VERSION)}
    assert set(data) == set(_g.SESSION_FILE_FIELDS), "SESSION_FILE_FIELDS changed: update write_session_file"
    _write_json(path, data)


def write_inactive_session_file(path, reason: str, *, pid: int | None = None, plugin_version: str | None = None) -> None:
    """The bridge is not serving: no port, no token, and why, so a client never follows a stale port."""
    _write_json(path, {"active": False, "reason": reason, "pid": pid, "plugin_version": plugin_version})
