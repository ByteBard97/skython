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
