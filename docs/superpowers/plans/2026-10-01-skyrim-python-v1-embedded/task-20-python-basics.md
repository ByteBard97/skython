# Task 20: Python package basics: framing, errors, values, form keys

Adapted from: branch `plan-out-of-process`, task 13 (`task-13-python-package-basics-framing-errors-values-form.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2, 4.4 and 4.7). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 11 (the fixtures) and Task 12 (the generated `python/src/skyrim/_protocol_generated.py`; run `python3 tools/gen_protocol.py --check` first, it must print `gen_protocol: 3 generated files are up to date`). This is pure Linux work; it does not need spike S8 or the game. Task 21 builds on it.

**What changed from the out-of-process plan.** Almost nothing: the package is the same pure-Python code, now used in two modes (in the game over `_skyrim`, and from another process over the dev bridge), so it needs Python 3.12 or later (skyrim-dm's floor; the game runs 3.14) and is tested on 3.12 and 3.14. Three additions: `_framing.dumps(obj) -> str` gives the JSON text of a message without the length prefix, because the in-process transport (Task 21) hands text to `_skyrim` and must fail exactly like `encode`; `values.MAX_ARRAY_ITEMS` (the generated 128) makes an over-long array a `TypeMismatch` before anything is sent, matching the 129-item fixture; and the generated-table tests pin `MAX_ARRAY_ITEMS`, `needs_game` and the new ini keys.

**Files:**
- Create: `python/pyproject.toml`, `python/src/skyrim/__init__.py`, `python/src/skyrim/_framing.py`, `python/src/skyrim/errors.py`, `python/src/skyrim/values.py`, `python/src/skyrim/refs.py`
- Create: `python/tests/support.py`, `python/tests/test_framing.py`, `python/tests/test_errors.py`, `python/tests/test_values_refs.py`, `python/tests/test_fixtures.py`, `python/tests/test_generated.py`
- Consumes (generated, committed in Task 12): `python/src/skyrim/_protocol_generated.py`. The package imports its limits, error-code registry data, value types and form-id rules from there and defines none of them. That module is plain constants and tuples, so it imports nothing and every other module can import it.

**Interfaces:**
- Consumes: `skyrim._protocol_generated` (Task 12: `PROTOCOL_VERSION`, the limits including `MAX_FRAME_BYTES` and `MAX_ARRAY_ITEMS`, `CODE_*`, `ERROR_CODES`, `REMOTE_EXCEPTIONS`, `HANDSHAKE_EXCEPTIONS`, `HANDSHAKE_CODES`, `ALIAS_TYPES`, `DEFAULT_ALIAS_TYPE`, `ELEM_TYPES`, `FORM_ID_*`, `PLUGIN_EXTENSIONS`, `LIGHT_EXTENSION`, `OPS` with `needs_game`, `BATCHABLE_OPS`, `CONFIG_KEYS`, `HOST`, `ENV_*`) and the golden fixtures from Task 11 (`protocol/v1/fixtures/`: `values.json`, `form_keys.json`, `responses.json`, and for the agreement tests `requests.json` and `hello.json`). The tests look entries up by content (the `not_ready` error response, the `handshake_error` message, the arrays longer than `MAX_ARRAY_ITEMS`), so Task 11 may add or reorder entries.
- Produces (names as in the index Contracts):
  - `skyrim._framing`: `MAX_FRAME: int` (the generated `MAX_FRAME_BYTES`); `dumps(obj: dict) -> str` (the JSON text of one message, compact, non-ASCII kept; raises `EncodeError` for a body over `MAX_FRAME` bytes, text that is not valid Unicode, or anything JSON cannot hold, including NaN); `encode(obj: dict) -> bytes` (the 4-byte little-endian length plus the `dumps` text as UTF-8; same failures); `read_frame(sock, deadline: float | None = None) -> dict`. `deadline` is a `time.monotonic()` value that bounds the WHOLE frame read (header and body, however many `recv` calls), and `TimeoutError` is raised when it passes. With no deadline the call waits as long as it takes; a socket timeout is then only a wake-up tick and is retried without losing the bytes already read. Raises `ConnectionLost` on EOF, `ProtocolViolation` on an oversized header, non-UTF-8, bad JSON or a non-object payload.
  - `skyrim.errors`: `SkyrimPythonError`; `ConnectionLost`, `NotRunning`, `CallTimeout`, `ProtocolViolation`; `EncodeError` (also a `ValueError`: the request cannot be sent, nothing was sent); `HandshakeError(code, message)` with `Unauthorized`, `ProtocolMismatch`; `RemoteError(code, message, detail)` with one subclass per code: `NotReady`, `Busy`, `BadRequest`, `UnknownFunction`, `TypeMismatch`, `StaleReference`, `NotFound`, `Timeout`, `VmError`, `Unsupported`, `InternalError`. **The classes are written out in `errors.py` (so `errors.NotReady`, pickling and type checkers work), but the code-to-class mapping is the generated `REMOTE_EXCEPTIONS` / `HANDSHAKE_EXCEPTIONS` data**: at import, `errors.py` looks each named class up, sets its `code` from the table and builds the registries `_REMOTE` and `_HANDSHAKE`; a code in `protocol.json` whose class is missing is an `ImportError` naming it, and `test_generated.py` fails if a class exists that the table does not list. `remote_error_from_wire(err) -> RemoteError` (accepts any JSON value: a malformed error object gives an `InternalError` whose message says so); `handshake_error_from_wire(msg: dict) -> HandshakeError`. Every exception can be pickled and copied (`__reduce__`).
  - `skyrim.values`: `INT_MIN`, `INT_MAX`, `FLOAT_MAX`, `MAX_ARRAY_ITEMS`, `ALIAS_TYPES`, `ELEM_TYPES` are the generated constants re-exported; frozen dataclasses `Fingerprint(base, type)`, `Form(form_id=None, form_key=None, type=None, epoch=None, fingerprint=None)`, `Alias(quest: Form, alias_id: int, alias_type="ReferenceAlias", epoch=None)`, `PArray(elem: str, items: tuple)`; `to_wire(v) -> Any`; `from_wire(j) -> Any` (arrays decode to plain `list`; a malformed form, alias or array from the server raises `ProtocolViolation`, including a `Bool`, `Int`, `Float` or `String` array with an item of another JSON type: a `bool` is not an `Int`, and a `Float` item may be an int or a float). `Form` and `Alias` validate on construction (`ValueError`): `form_id` in `0..0xFFFFFFFF`; when both `form_id` and `form_key` are given they must agree on the local id (24 bits, 12 for a `0xFE` light form) and a `0xFF` runtime id has no key; `alias_id` in `0..INT_MAX`; `alias_type` is `ReferenceAlias` or `LocationAlias`. Float rule: a float with magnitude above `3.4028235e38`, or non-finite, is `TypeMismatch`. Int rule: a scalar Python `int` is always a Papyrus Int and is range-checked (`TypeMismatch`), even where the Papyrus parameter is a Float, until the M3 signature table can tell the two apart; pass `3.0`, not `3`. Inside a list that infers as Float (or a `PArray("Float", ...)`), ints are converted with `float()` and are not range-checked. Array rule: a list or `PArray` with more than `MAX_ARRAY_ITEMS` (128) items is `TypeMismatch`, raised by `to_wire` before anything is sent.
  - `skyrim.refs`: `parse_form_key(text) -> tuple[str, int]` (raises `ValueError`; whole-string match, so a trailing newline, an empty or space-padded plugin name, control characters and non-ASCII look-alikes are all rejected, and an `.esl` local id is at most `0xFFF`, as in `PROTOCOL.md`), `format_form_key(plugin, local_id) -> str`, `is_runtime_form_id(int) -> bool`, `is_light_form_id(int) -> bool`
  - `python/tests/support.py` (plain module; pytest's default import mode puts `tests/` on `sys.path`): `FIXTURES: pathlib.Path`, `load_fixture(name) -> dict`. Task 21 adds `wait_until`; Task 22 appends the socket test helpers.

- [ ] **Step 1: Project file and test helpers**

The package targets Python 3.12 or later and has no runtime dependencies. `uv run` builds it with hatchling and writes `python/uv.lock`, which is committed.

`python/pyproject.toml`:
```toml
[project]
name = "skyrim-python"
version = "0.1.0"
description = "Call Papyrus in a running Skyrim from Python: in the game through the embedded interpreter, or from another process through the dev bridge"
requires-python = ">=3.12"
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
```

`python/tests/test_errors.py`:
```python
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
```

`python/tests/test_values_refs.py`:
```python
import math

import pytest

from skyrim import errors
from skyrim.refs import format_form_key, is_light_form_id, is_runtime_form_id, parse_form_key
from skyrim.values import INT_MAX, INT_MIN, MAX_ARRAY_ITEMS, Alias, Form, PArray, from_wire, to_wire


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


def test_float_array_items_skip_the_int_range_check():
    big = 3_000_000_000
    assert to_wire([big, 1.5]) == {"array": "Float", "items": [3e9, 1.5]}
    assert all(type(i) is float for i in to_wire([big, 1.5])["items"])
    assert to_wire(PArray("Float", (1, big))) == {"array": "Float", "items": [1.0, 3e9]}
    assert to_wire(PArray("Float", ())) == {"array": "Float", "items": []}


def test_int_arrays_and_scalars_are_still_range_checked():
    # the scalar ambiguity: a bare int is an Int until the M3 signature table says otherwise
    with pytest.raises(errors.TypeMismatch):
        to_wire([3_000_000_000, 4])
    with pytest.raises(errors.TypeMismatch):
        to_wire(PArray("Int", (3_000_000_000,)))
    with pytest.raises(errors.TypeMismatch):
        to_wire(3_000_000_000)
    assert to_wire(float(3_000_000_000)) == 3e9


def test_float_array_rejects_what_cannot_be_a_float():
    with pytest.raises(errors.TypeMismatch):
        to_wire([10**400, 1.5])
    with pytest.raises(errors.TypeMismatch):
        to_wire([math.nan, 1.5])
    with pytest.raises(TypeError):
        to_wire(PArray("Float", ("x",)))


def test_float_range_is_32_bit():
    assert to_wire(3.4028235e38) == 3.4028235e38 and to_wire(-3.4028235e38) == -3.4028235e38
    for bad in (3.5e38, -3.5e38, 1e39):
        with pytest.raises(errors.TypeMismatch):
            to_wire(bad)
        with pytest.raises(errors.TypeMismatch):
            to_wire([1.5, bad])


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


def test_malformed_server_values_are_protocol_violations():
    for bad in ({"kind": "form"}, {"kind": "alias", "alias_id": 0}, {"kind": "alias", "quest": {"kind": "form"}, "alias_id": 0},
                {"array": "Int", "items": 5}, {"kind": "form", "form_id": 1, "fingerprint": {"base": "x"}},
                {"array": "Float", "items": ["1.5", True]}, {"array": "Int", "items": [1, True]},
                {"array": "String", "items": ["a", 1]}, {"array": "Bool", "items": [0]}):
        with pytest.raises(errors.ProtocolViolation):
            from_wire(bad)


def test_form_id_and_form_key_must_agree():
    Form(form_id=20, form_key="Skyrim.esm|0x000014")
    Form(form_id=0xFE000ABC, form_key="Light Mod.esl|0x000ABC")  # light forms compare 12 bits
    for form_id, key in ((20, "Skyrim.esm|0x000015"), (0xFE000ABC, "Light Mod.esl|0x000123"),
                         (0xFF000002, "Skyrim.esm|0x000002"), (-1, None), (2**32, None), (True, None)):
        with pytest.raises(ValueError):
            Form(form_id=form_id, form_key=key)


def test_alias_is_validated():
    q = Form(form_id=1)
    for alias_id, alias_type in ((-1, "ReferenceAlias"), (2**31, "ReferenceAlias"), ("0", "ReferenceAlias"), (0, "MagicAlias")):
        with pytest.raises(ValueError):
            Alias(q, alias_id, alias_type)
    assert Alias(q, 0, "LocationAlias").alias_type == "LocationAlias"


def test_alias_round_trip():
    a = Alias(Form(form_id=0x800, type="Quest", epoch=1), 3, epoch=1)
    assert from_wire(to_wire(a)) == a


def test_form_keys():
    assert parse_form_key("Skyrim.esm|0x14") == ("Skyrim.esm", 0x14)
    assert format_form_key("Skyrim.esm", 0x14) == "Skyrim.esm|0x000014"
    with pytest.raises(ValueError):
        parse_form_key("Skyrim.esm|0x1000000")
    assert parse_form_key("skyrim.ESM|0X14") == ("skyrim.ESM", 0x14)
    assert parse_form_key("Light Mod.esl|0xFFF") == ("Light Mod.esl", 0xFFF)
    assert is_runtime_form_id(0xFF000002)
    assert not is_runtime_form_id(0x14)
    assert is_light_form_id(0xFE001800)


@pytest.mark.parametrize("text", [
    "Skyrim.esm|0x14\n",      # "$" would accept a trailing newline; fullmatch does not
    "Skyrim.esm|0x14 ",
    "\nSkyrim.esm|0x14",
    " Skyrim.esm|0x14",
    ".esm|0x1",               # empty plugin stem
    " .esp|0x1",
    "Sky\nrim.esm|0x14",
    "x.e\u017fm|0x14",        # U+017F must not stand in for "s" under IGNORECASE
    "Skyrim.esm|0x\u0661",    # non-ASCII digit
    "Skyrim.esm|0x123456789",
    "Skyrim .esm|0x14",         # space before the extension
    "Light Mod.esl|0x1000",     # light plugins have 12-bit local ids
    "",
    None,
    20,
])
def test_form_key_strictness(text):
    with pytest.raises(ValueError):
        parse_form_key(text)


def test_arrays_hold_at_most_max_array_items():
    assert MAX_ARRAY_ITEMS == 128
    assert len(to_wire(list(range(MAX_ARRAY_ITEMS)))["items"]) == MAX_ARRAY_ITEMS
    assert len(to_wire(PArray("Float", (1.5,) * MAX_ARRAY_ITEMS))["items"]) == MAX_ARRAY_ITEMS
    for too_long in (list(range(MAX_ARRAY_ITEMS + 1)), ["a"] * (MAX_ARRAY_ITEMS + 1),
                     PArray("Float", (1.5,) * (MAX_ARRAY_ITEMS + 1)), PArray("Int", (1,) * (MAX_ARRAY_ITEMS + 1))):
        with pytest.raises(errors.TypeMismatch, match=str(MAX_ARRAY_ITEMS)):
            to_wire(too_long)
```

`python/tests/test_fixtures.py`:
```python
import pytest

from support import load_fixture
from skyrim import errors
from skyrim.refs import format_form_key, parse_form_key
from skyrim.values import MAX_ARRAY_ITEMS, PArray, from_wire, to_wire

VALUES = load_fixture("values.json")
KEYS = load_fixture("form_keys.json")
RESPONSES = load_fixture("responses.json")


def same(a, b):
    """Strict equality: 1 is not 1.0 and True is not 1 (Python's == says they are equal)."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def test_same_helper_is_strict():
    assert not same(1, 1.0) and not same(True, 1) and not same([1], [1.0])
    assert same({"array": "Float", "items": [1.0, 2.5]}, {"array": "Float", "items": [1.0, 2.5]})
    assert not same({"array": "Float", "items": [1.0, 2.5]}, {"array": "Float", "items": [1, 2.5]})


@pytest.mark.parametrize("case", VALUES["valid"], ids=lambda c: str(c["wire"])[:40])
def test_valid_values_round_trip(case):
    # an entry's "canonical" member is the exact JSON the protocol emits for it (1 in a Float array is 1.0)
    assert same(to_wire(from_wire(case["wire"])), case.get("canonical", case["wire"]))


def _is_number_case(wire):
    return (isinstance(wire, (int, float)) and not isinstance(wire, bool)) or (isinstance(wire, dict) and wire.get("array") == "Float")


@pytest.mark.parametrize("case", [c for c in VALUES["invalid"] if c["error"] == "type_mismatch" and _is_number_case(c["wire"])],
                         ids=lambda c: str(c["wire"])[:40])
def test_out_of_range_numbers_rejected_client_side(case):
    with pytest.raises(errors.TypeMismatch):
        to_wire(from_wire(case["wire"]))


def _is_long_array_case(wire):
    return isinstance(wire, dict) and isinstance(wire.get("items"), list) and len(wire["items"]) > MAX_ARRAY_ITEMS


@pytest.mark.parametrize("case", [c for c in VALUES["invalid"] if _is_long_array_case(c["wire"])],
                         ids=lambda c: f"{c['wire']['array']}[{len(c['wire']['items'])}]")
def test_arrays_over_the_limit_rejected_client_side(case):
    assert case["error"] == "type_mismatch"
    with pytest.raises(errors.TypeMismatch):
        to_wire(PArray(case["wire"]["array"], tuple(from_wire(case["wire"]))))


def test_the_array_limit_fixtures_exist():
    lengths = {len(c["wire"]["items"]) for c in VALUES["valid"] if isinstance(c["wire"], dict) and "items" in c["wire"]}
    assert MAX_ARRAY_ITEMS in lengths
    assert any(_is_long_array_case(c["wire"]) for c in VALUES["invalid"])


def _is_reference_case(wire):
    return isinstance(wire, dict) and (wire.get("kind") in ("form", "alias") or "array" in wire)


@pytest.mark.parametrize("case", [c for c in VALUES["invalid"] if c["error"] == "bad_request" and _is_reference_case(c["wire"])],
                         ids=lambda c: str(c["wire"])[:50])
def test_malformed_references_and_arrays_from_the_server_are_violations(case):
    with pytest.raises(errors.ProtocolViolation):
        from_wire(case["wire"])


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
    err = next(r["error"] for r in RESPONSES["valid"] if r.get("ok") is False and r["error"]["code"] == "not_ready")
    exc = errors.remote_error_from_wire(err)
    assert isinstance(exc, errors.NotReady)
    assert exc.detail == err["detail"]
    hs = errors.handshake_error_from_wire(next(r for r in RESPONSES["valid"] if r["type"] == "handshake_error"))
    assert isinstance(hs, errors.Unauthorized)
```

The generated-table tests tie the tables to the package and to the fixtures. They compare against literals and fixture files, never a table against itself.

`python/tests/test_generated.py`:
```python
"""The generated protocol tables (protocol/protocol.json) against the hand-written package and the golden
fixtures. A mutation of protocol.json changes at least one result here."""
import inspect

import pytest

from support import load_fixture
from skyrim import _framing, _protocol_generated as g, errors, refs, values

REQUESTS = load_fixture("requests.json")
HELLO = load_fixture("hello.json")
VALUES = load_fixture("values.json")
KEYS = load_fixture("form_keys.json")
RESPONSES = load_fixture("responses.json")


def test_v1_wire_contract_is_pinned():
    """A tripwire: change these literals only together with a protocol version bump."""
    assert g.PROTOCOL_VERSION == (1, 0)
    assert (g.MAX_FRAME_BYTES, g.PRE_AUTH_FRAME_BYTES) == (16 * 1024 * 1024, 64 * 1024)
    assert (g.MAX_NAME_BYTES, g.MAX_BATCH_CALLS, g.MIN_TIMEOUT_MS, g.MAX_TIMEOUT_MS) == (256, 256, 1, 3_600_000)
    assert (g.MAX_ERROR_MESSAGE_BYTES, g.MAX_CLIENT_NAME_BYTES, g.TOKEN_LENGTH) == (1024, 64, 32)
    assert (g.INT_MIN, g.INT_MAX, g.FLOAT_MAX_TEXT) == (-2**31, 2**31 - 1, 3.4028235e38)
    assert (g.MAX_CONNECTIONS, g.HELLO_TIMEOUT_MS, g.MAX_JSON_DEPTH) == (32, 5000, 64)
    assert g.MAX_ARRAY_ITEMS == 128
    assert (g.FORM_ID_MAX_LOCAL_ID, g.FORM_ID_MAX_LIGHT_LOCAL_ID) == (0xFFFFFF, 0xFFF)
    assert (g.FORM_ID_RUNTIME_PREFIX, g.FORM_ID_LIGHT_PREFIX) == (0xFF, 0xFE)
    assert g.ERROR_CODES == ("not_ready", "busy", "bad_request", "unknown_function", "type_mismatch", "stale_reference",
                             "not_found", "timeout", "vm_error", "unsupported", "internal", "unauthorized", "protocol_mismatch")
    assert tuple(g.OPS) == ("game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
                            "papyrus.get_property", "papyrus.set_property", "papyrus.batch")
    assert g.BATCHABLE_OPS == ("papyrus.call_static", "papyrus.call_method", "papyrus.get_property", "papyrus.set_property")
    assert [name for name, spec in g.OPS.items() if not spec["needs_game"]] == ["game.state"]
    assert tuple(g.CONFIG_KEYS) == ("python_enabled", "scripts_enabled", "bridge_enabled", "port", "fixed_token", "session_file",
                                    "default_timeout_ms", "max_inflight_per_connection", "max_inflight_total", "enable_console")
    assert g.CONFIG_KEYS["port"]["default"] == 45977 and g.CONFIG_KEYS["bridge_enabled"]["default"] is False
    assert g.HOST == "127.0.0.1"
    assert (g.ENV_SESSION, g.ENV_PORT, g.ENV_TOKEN) == ("SKYRIM_PYTHON_SESSION", "SKYRIM_PYTHON_PORT", "SKYRIM_PYTHON_TOKEN")


def test_the_exception_registry_is_built_from_the_table():
    for code, name in g.REMOTE_EXCEPTIONS.items():
        cls = getattr(errors, name)
        assert errors._REMOTE[code] is cls and cls.code == code and issubclass(cls, errors.RemoteError)
    for code, name in g.HANDSHAKE_EXCEPTIONS.items():
        cls = getattr(errors, name)
        assert errors._HANDSHAKE[code] is cls and cls.code == code and issubclass(cls, errors.HandshakeError)
    assert set(g.REMOTE_EXCEPTIONS) | set(g.HANDSHAKE_EXCEPTIONS) <= set(g.ERROR_CODES)
    remote = {n for n, c in vars(errors).items() if inspect.isclass(c) and issubclass(c, errors.RemoteError) and c is not errors.RemoteError}
    handshake = {n for n, c in vars(errors).items() if inspect.isclass(c) and issubclass(c, errors.HandshakeError) and c is not errors.HandshakeError}
    assert remote == set(g.REMOTE_EXCEPTIONS.values()), "errors.py has a RemoteError class the table does not list (or the reverse)"
    assert handshake == set(g.HANDSHAKE_EXCEPTIONS.values())
    assert errors.RemoteError.code == g.CODE_INTERNAL


def test_an_unknown_code_from_a_newer_server_is_the_generic_class():
    assert type(errors.remote_error_from_wire({"code": "from_the_future", "message": "m"})) is errors.RemoteError
    assert type(errors.handshake_error_from_wire({"code": "from_the_future"})) is errors.HandshakeError


def test_fixture_error_codes_are_in_the_table():
    seen = {c["error"] for name in (HELLO, REQUESTS, VALUES) for c in name["invalid"]}
    seen |= {c["result"] for c in HELLO["check"] if c["result"]}
    seen |= {r["error"]["code"] for r in RESPONSES["valid"] if r.get("ok") is False}
    seen |= {r["code"] for r in RESPONSES["valid"] if r["type"] == "handshake_error"}
    assert len(seen) >= 4 and seen <= set(g.ERROR_CODES), seen - set(g.ERROR_CODES)
    assert {c["result"] for c in HELLO["check"] if c["result"]} <= set(g.HANDSHAKE_CODES)


def test_every_op_has_a_fixture_and_fixtures_use_only_the_tables_argument_names():
    seen = {name: set() for name in g.OPS}
    for req in REQUESTS["valid"]:
        spec = g.OPS[req["op"]]
        names = {a[0] for a in spec["args"]}
        assert set(req["args"]) <= names, (req["op"], set(req["args"]) - names)
        seen[req["op"]] |= set(req["args"])
        for step in req["args"].get("calls", []):
            assert step["op"] in g.BATCHABLE_OPS and set(step["args"]) <= {a[0] for a in g.OPS[step["op"]]["args"]}
    for name, spec in g.OPS.items():
        required = {a[0] for a in spec["args"] if a[2]}
        assert seen[name] >= required, f"no valid fixture sets every required argument of {name}"
        assert seen[name] or not spec["args"], f"no valid fixture exercises {name}"
        assert set(spec["exactly_one_of"]) <= {a[0] for a in spec["args"]}
    for r in RESPONSES["valid"]:
        assert r["type"] in g.MESSAGE_TYPES
        for cap in r.get("capabilities", []):
            assert cap in g.OPS


def test_the_fixtures_sit_exactly_on_the_limits_in_the_table():
    bad_timeouts = {c["message"]["args"]["timeout_ms"] for c in REQUESTS["invalid"]
                    if isinstance(c["message"].get("args"), dict) and isinstance(c["message"]["args"].get("timeout_ms"), int)}
    assert g.MIN_TIMEOUT_MS - 1 in bad_timeouts and g.MAX_TIMEOUT_MS + 1 in bad_timeouts
    wires = [c["wire"] for c in VALUES["valid"]]
    bad = [c["wire"] for c in VALUES["invalid"]]
    assert g.INT_MAX in wires and g.INT_MIN in wires and g.INT_MAX + 1 in bad and g.INT_MIN - 1 in bad
    assert g.FLOAT_MAX_TEXT in wires and 3.5e38 in bad and g.FLOAT_MAX_TEXT < 3.5e38
    assert g.INT_MAX in [w["alias_id"] for w in wires if isinstance(w, dict) and w.get("kind") == "alias"]
    assert all(len(h["token"]) == g.TOKEN_LENGTH and h["protocol"][0] == g.PROTOCOL_MAJOR for h in HELLO["valid"])
    ok = {c["text"] for c in KEYS["valid"]}
    assert f"Skyrim.esm|0x{g.FORM_ID_MAX_LOCAL_ID:X}" in ok and f"Skyrim.esm|0x{g.FORM_ID_MAX_LOCAL_ID + 1:X}" in KEYS["invalid"]
    assert f"Light Mod.esl|0x{g.FORM_ID_MAX_LIGHT_LOCAL_ID:X}" in ok
    assert f"Light Mod.esl|0x{g.FORM_ID_MAX_LIGHT_LOCAL_ID + 1:X}" in KEYS["invalid"]


def test_package_constants_come_from_the_table():
    assert _framing.MAX_FRAME == g.MAX_FRAME_BYTES
    assert (values.INT_MIN, values.INT_MAX, values.FLOAT_MAX) == (g.INT_MIN, g.INT_MAX, g.FLOAT_MAX_TEXT)
    assert values.ALIAS_TYPES == g.ALIAS_TYPES and values.ELEM_TYPES == g.ELEM_TYPES
    assert values.MAX_ARRAY_ITEMS == g.MAX_ARRAY_ITEMS
    assert values.Alias(values.Form(form_id=1), 0).alias_type == g.DEFAULT_ALIAS_TYPE


@pytest.mark.parametrize("ext", g.PLUGIN_EXTENSIONS)
def test_every_plugin_extension_parses_with_its_own_local_id_ceiling(ext):
    ceiling = g.FORM_ID_MAX_LIGHT_LOCAL_ID if ext == g.LIGHT_EXTENSION else g.FORM_ID_MAX_LOCAL_ID
    assert refs.parse_form_key(f"a{ext}|0x{ceiling:X}") == (f"a{ext}", ceiling)
    assert refs.parse_form_key(f"a{ext.upper()}|0x1")[1] == 1
    with pytest.raises(ValueError):
        refs.parse_form_key(f"a{ext}|0x{ceiling + 1:X}")
    with pytest.raises(ValueError):
        refs.parse_form_key("a.txt|0x1")
    assert refs.format_form_key(f"a{ext}", 1) == f"a{ext}|0x" + "1".zfill(g.FORM_ID_CANONICAL_HEX_DIGITS)
    assert refs.is_runtime_form_id(g.FORM_ID_RUNTIME_PREFIX << 24) and refs.is_light_form_id(g.FORM_ID_LIGHT_PREFIX << 24)
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd python && uv run --python 3.14 pytest -q`
Expected: FAIL. `uv run` creates the virtualenv and installs pytest, then pytest stops with `5 errors during collection` (`test_errors.py`, `test_fixtures.py`, `test_framing.py`, `test_generated.py`, `test_values_refs.py`), each `ImportError: cannot import name ... from 'skyrim' (unknown location)`: the package directory already holds Task 12's `_protocol_generated.py` but no `__init__.py` yet, so `skyrim` is an empty namespace package.

- [ ] **Step 4: Implement**

`python/src/skyrim/errors.py`:
```python
"""Exceptions. Server error codes map one-to-one onto RemoteError subclasses.

Every exception here survives pickle and copy (multiprocessing, concurrent.futures and pytest-xdist
all rely on that), so the classes with a custom __init__ define __reduce__.

The classes are written out so that `errors.NotReady`, pickling and type checkers work. Which wire code maps to
which class is NOT decided here: it is the generated table in _protocol_generated (protocol/protocol.json).
At the bottom of this module each class is looked up by the name the table gives and takes its `code` from it.
"""
from . import _protocol_generated as _g


class SkyrimPythonError(Exception):
    """Base class for everything this package raises on purpose."""


class ConnectionLost(SkyrimPythonError):
    """The connection to the game closed (game quit, crashed, stopped reading, or the client closed it)."""


class NotRunning(SkyrimPythonError):
    """No reachable SkyrimPython server: no session file, connection refused, or no welcome in time."""


class CallTimeout(SkyrimPythonError):
    """The client stopped waiting for a response. The call may still run in the game."""


class ProtocolViolation(SkyrimPythonError):
    """The peer sent something that is not protocol v1."""


class EncodeError(SkyrimPythonError, ValueError):
    """A request cannot be put on the wire: over the frame limit, or text that is not valid Unicode.
    Raised before anything is sent, so the connection is unaffected."""


class HandshakeError(SkyrimPythonError):
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


class RemoteError(SkyrimPythonError):
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
            raise ImportError(f"skyrim.errors has no {base.__name__} subclass {class_name!r}, "
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


__all__ = ["SkyrimPythonError", "ConnectionLost", "NotRunning", "CallTimeout", "ProtocolViolation", "EncodeError",
           "HandshakeError", "Unauthorized", "ProtocolMismatch", "RemoteError", *[c.__name__ for c in _REMOTE.values()]]
```

`python/src/skyrim/_framing.py`:
```python
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
    """Read exactly n bytes. With a deadline (time.monotonic() value) the WHOLE read is bounded and
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


def read_frame(sock: socket.socket, deadline: float | None = None) -> dict:
    """Read one frame. Raises ConnectionLost on EOF, ProtocolViolation on a bad frame, and TimeoutError
    if `deadline` (a time.monotonic() value) passes first."""
    (n,) = struct.unpack("<I", _recv_exact(sock, 4, deadline))
    if n > MAX_FRAME:
        raise ProtocolViolation(f"frame of {n} bytes exceeds {MAX_FRAME}")
    try:
        obj = json.loads(_recv_exact(sock, n, deadline).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolViolation(f"frame is not UTF-8 JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ProtocolViolation("frame is not a JSON object")
    return obj
```

`python/src/skyrim/refs.py`:
```python
"""Form keys ("Plugin.esp|0x0012AB") and form-id ranges. Grammar: protocol/PROTOCOL.md "Values"; the extensions,
digit counts and ceilings are the generated constants (protocol.json "form_ids")."""
import re

from . import _protocol_generated as _g

_EXTENSIONS = "|".join(re.escape(e.lstrip(".")) for e in _g.PLUGIN_EXTENSIONS)
# fullmatch, not match: "$" would accept a trailing newline. ASCII keeps IGNORECASE from letting
# "ſ" stand in for "s" or the Kelvin sign for "k".
_KEY = re.compile(rf"(?P<plugin>[^|\x00-\x1f]+\.(?:{_EXTENSIONS}))\|0[xX](?P<id>[0-9A-Fa-f]{{1,{_g.FORM_ID_MAX_KEY_HEX_DIGITS}}})",
                  re.ASCII | re.IGNORECASE)


def parse_form_key(text: str) -> tuple[str, int]:
    m = _KEY.fullmatch(text) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"not a form key (expected Plugin.esp|0x0012AB): {text!r}")
    plugin = m.group("plugin")
    stem, ext = plugin.rsplit(".", 1)
    if not stem or stem != stem.strip():
        raise ValueError(f"form key has an empty or space-padded plugin name: {text!r}")
    local_id = int(m.group("id"), 16)
    limit = _g.FORM_ID_MAX_LIGHT_LOCAL_ID if f".{ext.lower()}" == _g.LIGHT_EXTENSION else _g.FORM_ID_MAX_LOCAL_ID
    if local_id > limit:
        raise ValueError(f"local form id {local_id:#x} is over {limit.bit_length()} bits for {ext.lower()}: {text!r}")
    return plugin, local_id


def format_form_key(plugin: str, local_id: int) -> str:
    return f"{plugin}|0x{local_id:0{_g.FORM_ID_CANONICAL_HEX_DIGITS}X}"


def is_runtime_form_id(form_id: int) -> bool:
    return (form_id >> 24) == _g.FORM_ID_RUNTIME_PREFIX


def is_light_form_id(form_id: int) -> bool:
    return (form_id >> 24) == _g.FORM_ID_LIGHT_PREFIX
```

`python/src/skyrim/values.py`:
```python
"""Papyrus values on the wire. See protocol/PROTOCOL.md "Values"."""
import math
from dataclasses import dataclass, field
from typing import Any

from . import _protocol_generated as _g
from . import errors
from .refs import parse_form_key

INT_MIN = _g.INT_MIN
INT_MAX = _g.INT_MAX
FLOAT_MAX = _g.FLOAT_MAX_TEXT  # Papyrus Float is 32-bit; the protocol rejects a larger magnitude (type_mismatch)
ALIAS_TYPES = _g.ALIAS_TYPES
ELEM_TYPES = _g.ELEM_TYPES
MAX_ARRAY_ITEMS = _g.MAX_ARRAY_ITEMS  # the Papyrus VM's array limit; a longer array is type_mismatch
# What an item of a primitive array from the server must be (bool is not an Int; a Float may arrive as an int).
_WIRE_ITEM = {"Bool": lambda v: type(v) is bool, "Int": lambda v: type(v) is int,
              "Float": lambda v: type(v) in (int, float), "String": lambda v: type(v) is str}


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
        if self.form_id is not None and (type(self.form_id) is not int or not 0 <= self.form_id <= 0xFFFFFFFF):
            raise ValueError(f"form_id must be an integer in 0..0xFFFFFFFF, not {self.form_id!r}")
        if self.form_key is not None:
            _, local_id = parse_form_key(self.form_key)
            if self.form_id is not None:  # the two names of one form must agree (PROTOCOL.md "Values")
                top = self.form_id >> 24
                if top == _g.FORM_ID_RUNTIME_PREFIX:
                    raise ValueError(f"a runtime-created form (0x{top:02X} id) has no form_key")
                mask = _g.FORM_ID_MAX_LIGHT_LOCAL_ID if top == _g.FORM_ID_LIGHT_PREFIX else _g.FORM_ID_MAX_LOCAL_ID
                if self.form_id & mask != local_id:
                    raise ValueError(f"form_id {self.form_id:#x} and form_key {self.form_key!r} name different forms")


@dataclass(frozen=True)
class Alias:
    quest: Form
    alias_id: int
    alias_type: str = _g.DEFAULT_ALIAS_TYPE
    epoch: int | None = field(default=None, compare=False)

    def __post_init__(self):
        if type(self.alias_id) is not int or not 0 <= self.alias_id <= INT_MAX:
            raise ValueError(f"alias_id must be an integer in 0..{INT_MAX}, not {self.alias_id!r}")
        if self.alias_type not in ALIAS_TYPES:
            raise ValueError(f"alias_type must be one of {ALIAS_TYPES}, not {self.alias_type!r}")


@dataclass(frozen=True)
class PArray:
    """An explicitly typed Papyrus array, for when a Python list is empty or ambiguous."""
    elem: str
    items: tuple = ()


def _int(v: int) -> int:
    if not INT_MIN <= v <= INT_MAX:
        raise errors.TypeMismatch("type_mismatch", f"{v} is out of Papyrus Int range")
    return v


def _float(v) -> float:
    """A Float item: ints are converted with float(), so 3_000_000_000 is fine inside a Float array."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TypeError(f"a Float array item must be a number, not {type(v).__name__}: {v!r}")
    try:
        f = float(v)
    except OverflowError:
        raise errors.TypeMismatch("type_mismatch", "integer is too large for a Papyrus Float") from None
    if not math.isfinite(f) or abs(f) > FLOAT_MAX:
        raise errors.TypeMismatch("type_mismatch", f"{v} is out of Papyrus Float range (finite, at most {FLOAT_MAX:g})")
    return f


def _items(elem: str, items) -> list:
    if len(items) > MAX_ARRAY_ITEMS:
        raise errors.TypeMismatch("type_mismatch", f"a Papyrus array holds at most {MAX_ARRAY_ITEMS} items, not {len(items)}")
    return [_float(i) for i in items] if elem == "Float" else [to_wire(i) for i in items]


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
    """Python value -> wire value.

    A scalar Python int is always sent as a Papyrus Int and range-checked to 32 bits, even when the
    Papyrus parameter is a Float: pass 3.0, not 3, for a Float parameter (and float(n) for an n over
    2**31 - 1). The client cannot tell the difference until the M3 signature table exists. Inside a list
    that is (or is declared by PArray as) Float, ints are converted with float() and are not range-checked.
    """
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        return _int(v)
    if isinstance(v, float):
        return _float(v)
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
        return {"array": v.elem, "items": _items(v.elem, v.items)}
    if isinstance(v, (list, tuple)):
        elem = _infer_elem(list(v))
        return {"array": elem, "items": _items(elem, v)}
    raise TypeError(f"cannot send {type(v).__name__} to Papyrus: {v!r}")


def _opt(j: dict, key: str, kind: type):
    v = j.get(key)
    if v is not None and (type(v) is not kind or (kind is int and v < 0)):
        raise ValueError(f"{key} must be {'a non-negative integer' if kind is int else 'a string'}, not {v!r}")
    return v


def _form_from(j: dict) -> Form:
    fp = j.get("fingerprint")
    if fp is not None:
        if not (isinstance(fp, dict) and isinstance(fp.get("base"), str) and isinstance(fp.get("type"), str)):
            raise ValueError(f"fingerprint must be {{base, type}} strings, not {fp!r}")
        fp = Fingerprint(fp["base"], fp["type"])
    return Form(form_id=_opt(j, "form_id", int), form_key=_opt(j, "form_key", str), type=_opt(j, "type", str),
                epoch=_opt(j, "epoch", int), fingerprint=fp)


def from_wire(j: Any) -> Any:
    """Wire value -> Python value. A malformed reference or array from the server is a ProtocolViolation."""
    if not isinstance(j, dict):
        return j
    try:
        if "array" in j:
            if j["array"] not in ELEM_TYPES or not isinstance(j.get("items", []), list):
                raise ValueError(f"bad array {j['array']!r} / items")
            check = _WIRE_ITEM.get(j["array"])
            if check is not None and not all(check(i) for i in j.get("items", [])):
                raise ValueError(f"an item of a {j['array']} array has the wrong type")
            items = [from_wire(i) for i in j.get("items", [])]
            if j["array"] == "Float":
                items = [float(i) for i in items]
            return items
        kind = j.get("kind")
        if kind == "form":
            return _form_from(j)
        if kind == "alias":
            return Alias(_form_from(j["quest"]), _opt(j, "alias_id", int), j.get("alias_type", _g.DEFAULT_ALIAS_TYPE),
                         _opt(j, "epoch", int))
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise errors.ProtocolViolation(f"malformed {j.get('kind') or 'array'} value from the server: {e}") from None
    return j
```

The package's `__init__.py` (Task 21 adds the `Game` API; Task 22 adds `connect`):

`python/src/skyrim/__init__.py`:
```python
"""Call Papyrus in a running Skyrim from Python."""
from .errors import *  # noqa: F401,F403
from .values import Alias, Fingerprint, Form, PArray  # noqa: F401

__version__ = "0.1.0"
```

- [ ] **Step 5: Run to verify they pass, on both Python versions**

Run: `cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q`
Expected: `143 passed` twice, each in about 1.5 s (`uv` replaces the virtualenv when the version changes; any 3.12.x and 3.14.x will do). The count includes the parametrized fixture cases as Task 11's fixtures stand today and grows by one per entry added to `values.json` or `form_keys.json`.

Note on the fixture round trip: `{"array":"Float","items":[1,2.5]}` decodes to `[1.0, 2.5]` and re-encodes as Float (all items are numbers, one is a float). Python says `1.0 == 1`, so the test compares with `same()`, which is strict about `int` versus `float` and `bool` versus `int` everywhere except the items of a Float array (where the protocol accepts integers). An all-integer Float array such as `[1.0, 2.0]` decodes to floats and so re-encodes as Float too. Ambiguity that remains until M3: a bare `[1, 2]` always infers as an Int array. The 129-item fixture is re-encoded through `PArray` with the fixture's own element type, so the test checks the array limit and nothing else.

- [ ] **Step 6: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`. `errors.py`, `_framing.py`, `refs.py` and `values.py` must not spell out an error code, a limit, an extension or a form-id ceiling: they take them from `skyrim._protocol_generated`.

- [ ] **Step 7: Commit**

```bash
git add python/pyproject.toml python/uv.lock python/src python/tests
git commit -m "feat(python): framing, typed errors, value codec, form keys, checked against the generated protocol tables"
```
