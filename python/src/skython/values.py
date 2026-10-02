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
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as e:
        raise errors.ProtocolViolation(f"malformed {j.get('kind') or 'array'} value from the server: {e}") from None
    return j
