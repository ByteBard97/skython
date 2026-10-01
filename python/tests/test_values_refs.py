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
                {"array": "String", "items": ["a", 1]}, {"array": "Bool", "items": [0]},
                {"array": "Float", "items": [10**400]}):
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
