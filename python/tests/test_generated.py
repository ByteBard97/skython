"""The generated protocol tables (protocol/protocol.json) against the hand-written package and the golden
fixtures. A mutation of protocol.json changes at least one result here."""
import inspect

import pytest

from support import load_fixture
from skython import _framing, _protocol_generated as g, errors, refs, values

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
    assert (g.ENV_SESSION, g.ENV_PORT, g.ENV_TOKEN) == ("SKYTHON_SESSION", "SKYTHON_PORT", "SKYTHON_TOKEN")


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
