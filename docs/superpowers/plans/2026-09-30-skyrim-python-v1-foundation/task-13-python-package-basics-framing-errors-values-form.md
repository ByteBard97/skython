# Task 13: Python package basics: framing, errors, values, form keys

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `python/pyproject.toml`, `python/src/skyrim/__init__.py`, `python/src/skyrim/_framing.py`, `python/src/skyrim/errors.py`, `python/src/skyrim/values.py`, `python/src/skyrim/refs.py`
- Create: `python/tests/support.py`, `python/tests/test_framing.py`, `python/tests/test_values_refs.py`, `python/tests/test_fixtures.py`

**Interfaces:**
- Produces:
  - `skyrim._framing`: `MAX_FRAME: int`, `encode(obj: dict) -> bytes`, `read_frame(sock) -> dict` (raises `ConnectionLost` on EOF, `ProtocolViolation` on an oversized header or non-object JSON)
  - `skyrim.errors`: `SkyrimPythonError`; `ConnectionLost`, `NotRunning`, `CallTimeout`, `ProtocolViolation`; `HandshakeError(code, message)` with `Unauthorized`, `ProtocolMismatch`; `RemoteError(code, message, detail)` with one subclass per code: `NotReady`, `Busy`, `BadRequest`, `UnknownFunction`, `TypeMismatch`, `StaleReference`, `NotFound`, `Timeout`, `VmError`, `Unsupported`, `InternalError`; `remote_error_from_wire(err: dict) -> RemoteError`; `handshake_error_from_wire(msg: dict) -> HandshakeError`
  - `skyrim.values`: frozen dataclasses `Fingerprint(base, type)`, `Form(form_id=None, form_key=None, type=None, epoch=None, fingerprint=None)`, `Alias(quest: Form, alias_id: int, alias_type="ReferenceAlias", epoch=None)`, `PArray(elem: str, items: tuple)`; `INT_MIN`, `INT_MAX`; `to_wire(v) -> Any`; `from_wire(j) -> Any` (arrays decode to plain `list`)
  - `skyrim.refs`: `parse_form_key(text) -> tuple[str, int]` (raises `ValueError`), `format_form_key(plugin, local_id) -> str`, `is_runtime_form_id(int) -> bool`, `is_light_form_id(int) -> bool`
  - `python/tests/support.py` (plain module; pytest's default import mode puts `tests/` on `sys.path`): `FIXTURES: pathlib.Path`, `load_fixture(name) -> dict`

- [ ] **Step 1: Project file and test helpers**

`python/pyproject.toml`:
```toml
[project]
name = "skyrim-python"
version = "0.1.0"
description = "Call Papyrus in a running Skyrim from Python, over the SkyrimPython SKSE plugin"
requires-python = ">=3.11"
license = "MIT"
dependencies = []

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/skyrim"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`python/tests/support.py` (Task 14 appends the fake server to this file):

`python/tests/support.py`:
```python
import json
import pathlib

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())
```

- [ ] **Step 2: Write the failing tests**

`python/tests/test_framing.py`:
```python
import socket
import struct

import pytest

from skyrim import _framing
from skyrim.errors import ConnectionLost, ProtocolViolation


def pair():
    a, b = socket.socketpair()
    return a, b


def test_round_trip_split_writes():
    a, b = pair()
    data = _framing.encode({"type": "hello", "x": 1})
    assert data[:4] == struct.pack("<I", len(data) - 4)
    a.sendall(data[:3])
    a.sendall(data[3:])
    assert _framing.read_frame(b) == {"type": "hello", "x": 1}


def test_eof_is_connection_lost():
    a, b = pair()
    a.close()
    with pytest.raises(ConnectionLost):
        _framing.read_frame(b)


def test_oversized_header_is_protocol_violation():
    a, b = pair()
    a.sendall(struct.pack("<I", _framing.MAX_FRAME + 1))
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)


def test_non_object_payload_is_protocol_violation():
    a, b = pair()
    body = b"[1,2]"
    a.sendall(struct.pack("<I", len(body)) + body)
    with pytest.raises(ProtocolViolation):
        _framing.read_frame(b)
```

`python/tests/test_values_refs.py`:
```python
import math

import pytest

from skyrim import errors
from skyrim.refs import format_form_key, is_light_form_id, is_runtime_form_id, parse_form_key
from skyrim.values import INT_MAX, INT_MIN, Alias, Form, PArray, from_wire, to_wire


def test_int_range_checked_not_truncated():
    assert to_wire(INT_MAX) == INT_MAX
    assert to_wire(INT_MIN) == INT_MIN
    with pytest.raises(errors.TypeMismatch):
        to_wire(INT_MAX + 1)
    with pytest.raises(errors.TypeMismatch):
        to_wire(INT_MIN - 1)


def test_bool_is_not_int():
    assert to_wire(True) is True
    assert to_wire([True, False]) == {"array": "Bool", "items": [True, False]}


def test_list_inference():
    assert to_wire([1, 2]) == {"array": "Int", "items": [1, 2]}
    assert to_wire([1, 2.5]) == {"array": "Float", "items": [1, 2.5]}
    assert to_wire(["a"]) == {"array": "String", "items": ["a"]}
    assert to_wire([Form(form_id=20), None]) == {"array": "Form", "items": [{"kind": "form", "form_id": 20}, None]}
    with pytest.raises(TypeError):
        to_wire([])
    with pytest.raises(TypeError):
        to_wire([1, "a"])
    assert to_wire(PArray("Int", ())) == {"array": "Int", "items": []}


def test_unrepresentable_values():
    with pytest.raises(TypeError):
        to_wire({"a": 1})
    with pytest.raises(errors.TypeMismatch):
        to_wire(math.inf)


def test_form_needs_id_or_key():
    with pytest.raises(ValueError):
        Form()
    with pytest.raises(ValueError):
        Form(form_key="nonsense")


def test_alias_round_trip():
    a = Alias(Form(form_id=0x800, type="Quest", epoch=1), 3, epoch=1)
    assert from_wire(to_wire(a)) == a


def test_form_keys():
    assert parse_form_key("Skyrim.esm|0x14") == ("Skyrim.esm", 0x14)
    assert format_form_key("Skyrim.esm", 0x14) == "Skyrim.esm|0x000014"
    with pytest.raises(ValueError):
        parse_form_key("Skyrim.esm|0x1000000")
    assert is_runtime_form_id(0xFF000002)
    assert not is_runtime_form_id(0x14)
    assert is_light_form_id(0xFE001800)
```

`python/tests/test_fixtures.py`:
```python
import pytest

from support import load_fixture
from skyrim import errors
from skyrim.refs import format_form_key, parse_form_key
from skyrim.values import from_wire, to_wire

VALUES = load_fixture("values.json")
KEYS = load_fixture("form_keys.json")
RESPONSES = load_fixture("responses.json")


@pytest.mark.parametrize("case", VALUES["valid"], ids=lambda c: str(c["wire"])[:40])
def test_valid_values_round_trip(case):
    assert to_wire(from_wire(case["wire"])) == case["wire"]


@pytest.mark.parametrize("case", [c for c in VALUES["invalid"] if isinstance(c["wire"], int)])
def test_int_overflow_rejected_client_side(case):
    with pytest.raises(errors.TypeMismatch):
        to_wire(case["wire"])


@pytest.mark.parametrize("case", KEYS["valid"], ids=lambda c: c["text"])
def test_valid_form_keys(case):
    plugin, local_id = parse_form_key(case["text"])
    assert (plugin, local_id) == (case["plugin"], case["local_id"])
    assert format_form_key(plugin, local_id) == case["canonical"]


@pytest.mark.parametrize("text", KEYS["invalid"])
def test_invalid_form_keys(text):
    with pytest.raises(ValueError):
        parse_form_key(text)


def test_error_responses_map_to_typed_exceptions():
    err = RESPONSES["valid"][1]["error"]
    exc = errors.remote_error_from_wire(err)
    assert isinstance(exc, errors.NotReady)
    assert exc.detail == {"reason": "loading"}
    hs = errors.handshake_error_from_wire(RESPONSES["valid"][4])
    assert isinstance(hs, errors.Unauthorized)
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run pytest -q`
Expected: FAIL — uv cannot build `skyrim-python` yet (`src/skyrim` does not exist) or pytest reports `ModuleNotFoundError: No module named 'skyrim'`.

- [ ] **Step 4: Implement**

`python/src/skyrim/errors.py`:
```python
"""Exceptions. Server error codes map one-to-one onto RemoteError subclasses."""


class SkyrimPythonError(Exception):
    """Base class for everything this package raises on purpose."""


class ConnectionLost(SkyrimPythonError):
    """The connection to the game closed (game quit, crashed, or the client closed it)."""


class NotRunning(SkyrimPythonError):
    """No reachable SkyrimPython server: no session file, connection refused, or no welcome in time."""


class CallTimeout(SkyrimPythonError):
    """The client stopped waiting for a response. The call may still run in the game."""


class ProtocolViolation(SkyrimPythonError):
    """The peer sent something that is not protocol v1."""


class HandshakeError(SkyrimPythonError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Unauthorized(HandshakeError):
    """Wrong token. Usually a stale session file from an earlier game run."""


class ProtocolMismatch(HandshakeError):
    """Client and plugin speak different protocol major versions."""


class RemoteError(SkyrimPythonError):
    code = "internal"

    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = detail or {}


class NotReady(RemoteError): code = "not_ready"
class Busy(RemoteError): code = "busy"
class BadRequest(RemoteError): code = "bad_request"
class UnknownFunction(RemoteError): code = "unknown_function"
class TypeMismatch(RemoteError): code = "type_mismatch"
class StaleReference(RemoteError): code = "stale_reference"
class NotFound(RemoteError): code = "not_found"
class Timeout(RemoteError): code = "timeout"
class VmError(RemoteError): code = "vm_error"
class Unsupported(RemoteError): code = "unsupported"
class InternalError(RemoteError): code = "internal"


_REMOTE = {c.code: c for c in (NotReady, Busy, BadRequest, UnknownFunction, TypeMismatch, StaleReference,
                               NotFound, Timeout, VmError, Unsupported, InternalError)}
_HANDSHAKE = {"unauthorized": Unauthorized, "protocol_mismatch": ProtocolMismatch}


def remote_error_from_wire(err: dict) -> RemoteError:
    code = err.get("code", "internal")
    return _REMOTE.get(code, RemoteError)(code, err.get("message", ""), err.get("detail"))


def handshake_error_from_wire(msg: dict) -> HandshakeError:
    code = msg.get("code", "bad_request")
    return _HANDSHAKE.get(code, HandshakeError)(code, msg.get("message", ""))


__all__ = ["SkyrimPythonError", "ConnectionLost", "NotRunning", "CallTimeout", "ProtocolViolation",
           "HandshakeError", "Unauthorized", "ProtocolMismatch", "RemoteError", *[c.__name__ for c in _REMOTE.values()]]
```

`python/src/skyrim/_framing.py`:
```python
"""4-byte little-endian length prefix + UTF-8 JSON object. Same rules as protocol/PROTOCOL.md."""
import json
import socket
import struct

from .errors import ConnectionLost, ProtocolViolation

MAX_FRAME = 16 * 1024 * 1024


def encode(obj: dict) -> bytes:
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise ValueError(f"message is {len(body)} bytes; the limit is {MAX_FRAME}")
    return struct.pack("<I", len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ConnectionLost("connection closed by peer")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket) -> dict:
    (n,) = struct.unpack("<I", _recv_exact(sock, 4))
    if n > MAX_FRAME:
        raise ProtocolViolation(f"frame of {n} bytes exceeds {MAX_FRAME}")
    try:
        obj = json.loads(_recv_exact(sock, n).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolViolation(f"frame is not UTF-8 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolViolation("frame is not a JSON object")
    return obj
```

`python/src/skyrim/refs.py`:
```python
"""Form keys ("Plugin.esp|0x0012AB") and form-id ranges."""
import re

_KEY = re.compile(r"^(?P<plugin>[^|]*\.(?:esm|esp|esl))\|0[xX](?P<id>[0-9A-Fa-f]{1,8})$", re.IGNORECASE)


def parse_form_key(text: str) -> tuple[str, int]:
    m = _KEY.match(text)
    if not m:
        raise ValueError(f"not a form key (expected Plugin.esp|0x0012AB): {text!r}")
    local_id = int(m.group("id"), 16)
    if local_id > 0xFFFFFF:
        raise ValueError(f"local form id {local_id:#x} is over 24 bits: {text!r}")
    return m.group("plugin"), local_id


def format_form_key(plugin: str, local_id: int) -> str:
    return f"{plugin}|0x{local_id:06X}"


def is_runtime_form_id(form_id: int) -> bool:
    return (form_id >> 24) == 0xFF


def is_light_form_id(form_id: int) -> bool:
    return (form_id >> 24) == 0xFE
```

`python/src/skyrim/values.py`:
```python
"""Papyrus values on the wire. See protocol/PROTOCOL.md "Values"."""
import math
from dataclasses import dataclass, field
from typing import Any

from . import errors
from .refs import parse_form_key

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1
ELEM_TYPES = ("Bool", "Int", "Float", "String", "Form", "Alias")


@dataclass(frozen=True)
class Fingerprint:
    base: str
    type: str


@dataclass(frozen=True)
class Form:
    """A form as the game names it. form_id is load-order dependent; form_key is not."""
    form_id: int | None = None
    form_key: str | None = None
    type: str | None = field(default=None, compare=False)
    epoch: int | None = field(default=None, compare=False)
    fingerprint: Fingerprint | None = None

    def __post_init__(self):
        if self.form_id is None and self.form_key is None:
            raise ValueError("a Form needs form_id or form_key")
        if self.form_key is not None:
            parse_form_key(self.form_key)


@dataclass(frozen=True)
class Alias:
    quest: Form
    alias_id: int
    alias_type: str = "ReferenceAlias"
    epoch: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class PArray:
    """An explicitly typed Papyrus array, for when a Python list is empty or ambiguous."""
    elem: str
    items: tuple = ()


def _int(v: int) -> int:
    if not INT_MIN <= v <= INT_MAX:
        raise errors.TypeMismatch("type_mismatch", f"{v} is out of Papyrus Int range")
    return v


def _form(f: Form) -> dict:
    j: dict[str, Any] = {"kind": "form"}
    if f.form_id is not None:
        j["form_id"] = f.form_id
    if f.form_key is not None:
        j["form_key"] = f.form_key
    if f.type:
        j["type"] = f.type
    if f.epoch is not None:
        j["epoch"] = f.epoch
    if f.fingerprint is not None:
        j["fingerprint"] = {"base": f.fingerprint.base, "type": f.fingerprint.type}
    return j


def _infer_elem(items: list) -> str:
    present = [i for i in items if i is not None]
    if not present:
        raise TypeError("cannot infer the element type of an empty or all-None list; use PArray")
    if all(isinstance(i, bool) for i in items):
        return "Bool"
    if all(isinstance(i, int) and not isinstance(i, bool) for i in items):
        return "Int"
    if all(isinstance(i, (int, float)) and not isinstance(i, bool) for i in items):
        return "Float"
    if all(isinstance(i, str) for i in items):
        return "String"
    if all(isinstance(i, Form) or i is None for i in items):
        return "Form"
    if all(isinstance(i, Alias) or i is None for i in items):
        return "Alias"
    raise TypeError(f"list mixes element types: {items!r}")


def to_wire(v: Any) -> Any:
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return _int(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise errors.TypeMismatch("type_mismatch", "Papyrus Float must be finite")
        return v
    if isinstance(v, Form):
        return _form(v)
    if isinstance(v, Alias):
        j = {"kind": "alias", "quest": _form(v.quest), "alias_id": v.alias_id, "alias_type": v.alias_type}
        if v.epoch is not None:
            j["epoch"] = v.epoch
        return j
    if isinstance(v, PArray):
        if v.elem not in ELEM_TYPES:
            raise TypeError(f"unknown array element type {v.elem!r}")
        return {"array": v.elem, "items": [to_wire(i) for i in v.items]}
    if isinstance(v, (list, tuple)):
        return {"array": _infer_elem(list(v)), "items": [to_wire(i) for i in v]}
    raise TypeError(f"cannot send {type(v).__name__} to Papyrus: {v!r}")


def _form_from(j: dict) -> Form:
    fp = j.get("fingerprint")
    return Form(form_id=j.get("form_id"), form_key=j.get("form_key"), type=j.get("type"), epoch=j.get("epoch"),
                fingerprint=Fingerprint(fp["base"], fp["type"]) if fp else None)


def from_wire(j: Any) -> Any:
    if not isinstance(j, dict):
        return j
    if "array" in j:
        items = [from_wire(i) for i in j.get("items", [])]
        if j["array"] == "Float":
            items = [float(i) for i in items]
        return items
    kind = j.get("kind")
    if kind == "form":
        return _form_from(j)
    if kind == "alias":
        return Alias(_form_from(j["quest"]), j["alias_id"], j.get("alias_type", "ReferenceAlias"), j.get("epoch"))
    return j
```

`python/src/skyrim/__init__.py` (Task 14 adds `connect`, `Game` and `find_session`):
```python
"""Call Papyrus in a running Skyrim from Python."""
from .errors import *  # noqa: F401,F403
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd python && uv run pytest -q`
Expected: all pass.

Note on the fixture round trip: `{"array":"Float","items":[1,2.5]}` decodes to `[1.0, 2.5]` and re-encodes as Float (all items are numbers, one is a float); `1.0 == 1` in the comparison. An all-integer Float array such as `[1.0, 2.0]` decodes to floats and so re-encodes as Float too.

- [ ] **Step 6: Commit**

```bash
git add python && git commit -m "feat(python): framing, typed errors, value codec, form keys"
```
