import json

import pytest

from support import load_fixture
from skython import _protocol_generated as g
from skython import errors
from skython.refs import format_form_key, parse_form_key
from skython.values import MAX_ARRAY_ITEMS, Form, PArray, from_wire, to_wire

VALUES = load_fixture("values.json")
KEYS = load_fixture("form_keys.json")
RESPONSES = load_fixture("responses.json")
REQUESTS = load_fixture("requests.json")
EVENTS_FX = load_fixture("events.json")


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


def _golden(req_id):
    return next(r for r in RESPONSES["valid"] if r.get("id") == req_id)


def test_batch_timeout_carries_partial_results():
    exc = errors.remote_error_from_wire(_golden(41)["error"])
    assert isinstance(exc, errors.Timeout)
    assert exc.detail["completed"] == 1 and len(exc.detail["results"]) == 1


def test_late_cancel_timeout_is_marked():
    exc = errors.remote_error_from_wire(_golden(42)["error"])
    assert isinstance(exc, errors.Timeout) and exc.detail["late_cancel"] is True


@pytest.mark.parametrize("req_id", [43, 44])
def test_world_reset_not_ready_is_marked_dispatched(req_id):
    exc = errors.remote_error_from_wire(_golden(req_id)["error"])
    assert isinstance(exc, errors.NotReady) and exc.detail["dispatched"] is True


@pytest.mark.parametrize("fx", REQUESTS["valid_without_args"], ids=lambda r: r["op"])
def test_request_without_args_is_accepted(fake, fx):
    import json
    resp = json.loads(fake.call(json.dumps(fx), 5.0))
    assert resp["id"] == fx["id"]
    assert resp["ok"] is True or resp["error"]["code"] != "bad_request", resp


@pytest.mark.parametrize("frame", EVENTS_FX["frames"], ids=lambda f: f"{f['event']}-{f['seq']}")
def test_event_frames_have_exactly_the_tables_fields_and_decode(frame):
    spec = g.EVENTS[frame["event"]]
    assert frame["type"] == "event"
    assert set(frame["data"]) == {name for name, _t, _n in spec["fields"]}
    for name, typ, nullable in spec["fields"]:
        v = frame["data"][name]
        if v is None:
            assert nullable, name
        elif typ == "form":
            assert isinstance(from_wire(v), Form), name
        else:
            assert isinstance(v, {"int": int, "float": (int, float), "bool": bool, "string": str}[typ]), name
    assert set(frame) - {"type", "event", "sub", "seq", "epoch", "data"} <= {"dropped", "suppressed"}
    assert all(frame[k] > 0 for k in ("dropped", "suppressed") if k in frame)


@pytest.mark.parametrize("case", EVENTS_FX["subscribe"], ids=lambda c: str(c["request"]["id"]))
def test_subscribe_fixtures_name_only_filter_fields(case):
    args = case["request"]["args"]
    assert set(args.get("filter") or {}) <= set(g.EVENTS[args["event"]]["filter_fields"])
    assert case["result"]["max_queue"] == args.get("max_queue", g.DEFAULT_EVENT_QUEUE)


NEW_OPS = {"events.subscribe", "events.unsubscribe", "game.save", "game.load"}


@pytest.mark.parametrize("case", [c for c in REQUESTS["invalid"] if c["message"].get("op") in NEW_OPS], ids=lambda c: str(c["message"]["id"]))
def test_the_fake_refuses_bad_arguments_of_the_new_ops_like_core(fake, case):
    resp = json.loads(fake.call(json.dumps(case["message"]), 5.0))
    assert resp["ok"] is False and resp["error"]["code"] == case["error"], resp


@pytest.mark.parametrize("req", [r for r in REQUESTS["valid"] if r["op"] in NEW_OPS], ids=lambda r: r["op"])
def test_the_fake_accepts_valid_requests_of_the_new_ops(fake, req):
    resp = json.loads(fake.call(json.dumps(req), 5.0))
    assert resp["ok"] is True or resp["error"]["code"] != "bad_request", resp
