"""The _skyrim module API (index Contracts), against the fake backend."""
import json
import math
import os
import subprocess
import sys
import time

import pytest

import _skyrim
from conftest import BUILD, FAKE_DIR, code, request, response


def test_surface():
    assert _skyrim.BACKEND == "fake"
    assert _skyrim.PROTOCOL == (1, 0)
    for name in ("submit", "wait", "wait_any", "cancel", "call", "state", "config", "log", "in_flight",
                 "_fake_set_not_ready", "_fake_bump_loads", "_fake_set_paused"):
        assert callable(getattr(_skyrim, name)), name


def test_call_round_trip():
    r = response(_skyrim.call(request(1, "Echo", "hello"), 5.0))
    assert r == {"type": "response", "id": 1, "ok": True, "result": "hello"}
    form = {"kind": "form", "form_id": 20}
    assert response(_skyrim.call(request(2, "Echo", form), 5.0))["result"] == form


def test_wait_returns_none_until_done_then_the_result_once():
    t = _skyrim.submit(request(3, "Sleep", 300))
    assert isinstance(t, int)
    assert _skyrim.wait(t, 0.05) is None
    assert response(_skyrim.wait(t, 5.0))["result"] == 300
    assert _skyrim.wait(t, 0.01) is None  # consumed


def test_wait_any_returns_only_the_finished_ones():
    slow = _skyrim.submit(request(4, "Sleep", 2000))
    fast = _skyrim.submit(request(5, "Echo", 5))
    got = _skyrim.wait_any([slow, fast], 5.0)
    assert [t for t, _ in got] == [fast]
    assert response(got[0][1])["id"] == 5
    assert _skyrim.wait_any([slow], 0.01) == []
    _skyrim.cancel(slow)
    assert _skyrim.wait_any([], 0.01) == []


def test_cancel_frees_the_slot_and_the_ticket():
    before = _skyrim.in_flight()
    t = _skyrim.submit(request(6, "Never"))
    assert _skyrim.in_flight() == before + 1
    _skyrim.cancel(t)
    assert _skyrim.in_flight() == before
    assert _skyrim.wait(t, 0.05) is None
    _skyrim.cancel(t)  # twice is harmless


def test_call_whose_wait_expires_is_a_timeout_with_late_cancel():
    before = _skyrim.in_flight()
    start = time.monotonic()
    r = response(_skyrim.call(request(7, "Never", timeout_ms=60000), 0.2))
    assert 0.15 < time.monotonic() - start < 2.0
    assert r["id"] == 7
    assert code(r) == "timeout"
    assert "no response within 0.2 s" in r["error"]["message"]
    assert r["error"]["detail"]["late_cancel"] is True
    assert {"paused", "menus"} <= set(r["error"]["detail"])
    assert _skyrim.in_flight() == before


def test_the_dispatcher_times_out_a_call_by_its_timeout_ms():
    r = response(_skyrim.call(request(8, "Never", timeout_ms=100), 5.0))
    assert code(r) == "timeout"
    assert "late_cancel" not in r["error"]["detail"]
    assert {"paused", "menus"} <= set(r["error"]["detail"])


def test_not_ready_answers_at_once_but_game_state_still_works():
    _skyrim._fake_set_not_ready("loading")
    start = time.monotonic()
    r = response(_skyrim.call(request(9, "Echo", 1), 5.0))
    assert time.monotonic() - start < 1.0
    assert code(r) == "not_ready"
    assert r["error"]["detail"]["reason"] == "loading"
    s = response(_skyrim.call(json.dumps({"type": "request", "id": 10, "op": "game.state", "args": {}}), 5.0))
    assert s["ok"] and s["result"]["backend"] == "fake" and s["result"]["loading"] is True
    _skyrim._fake_set_not_ready(None)
    assert code(response(_skyrim.call(request(11, "Echo", 1), 5.0))) == "ok"


def test_paused_only_refuses_calls_that_require_unpaused():
    _skyrim._fake_set_paused(True)
    r = response(_skyrim.call(request(12, "Echo", 1, require_unpaused=True), 5.0))
    assert code(r) == "not_ready" and r["error"]["detail"]["reason"] == "paused"
    assert code(response(_skyrim.call(request(13, "Echo", 1), 5.0))) == "ok"
    assert json.loads(_skyrim.state())["paused"] is True


def test_a_load_starting_ends_calls_in_flight():
    t = _skyrim.submit(request(14, "Never"))
    _skyrim._fake_bump_loads()
    r = response(_skyrim.wait(t, 2.0))
    assert code(r) == "not_ready" and r["error"]["detail"]["reason"] == "loading"
    assert r["error"]["detail"]["dispatched"] is True  # it had reached the backend: not safe to resubmit


def test_backend_exception_is_internal_and_errors_keep_their_codes():
    assert code(response(_skyrim.call(request(15, "Throw"), 5.0))) == "internal"
    r = response(_skyrim.call(request(16, "Fail", "vm_error", "no"), 5.0))
    assert code(r) == "vm_error" and r["error"]["message"] == "no"


def test_bad_request_text_is_bad_request_not_an_exception():
    r = response(_skyrim.call("this is not json", 5.0))
    assert code(r) == "bad_request" and r["id"] == 0
    r = response(_skyrim.call(json.dumps({"type": "request", "id": 17, "op": "nope", "args": {}}), 5.0))
    assert code(r) == "bad_request" and r["id"] == 17


@pytest.mark.parametrize("bad", [-1.0, math.nan])
def test_bad_timeouts_are_value_errors(bad):
    with pytest.raises(ValueError):
        _skyrim.wait(1, bad)
    with pytest.raises(ValueError):
        _skyrim.call(request(18, "Echo", 1), bad)


def test_wrong_argument_types_are_type_errors():
    with pytest.raises(TypeError):
        _skyrim.submit(42)
    with pytest.raises(TypeError):
        _skyrim.wait(-1, 1.0)


def test_state_is_the_base_state():
    s = json.loads(_skyrim.state())
    assert set(s) == {"data_ready", "game_loaded", "loading", "interactive", "paused", "menus", "epoch"}


def test_config_has_the_ini_fields_and_the_fake_paths(monkeypatch):
    monkeypatch.setenv("SKYRIM_FAKE_INI", "bridge_enabled = true\nport = 0\n")
    c = json.loads(_skyrim.config())
    assert c["bridge_enabled"] is True and c["port"] == 0
    assert c["python_enabled"] is True and c["default_timeout_ms"] == 5000
    assert (c["plugin_version"], c["game_version"], c["runtime"]) == ("0.0.0-fake", "0.0.0", "fake")
    for key in ("python_dir", "scripts_dir", "site_packages_dir", "log_dir", "session_file"):
        assert c[key].startswith(str(FAKE_DIR)), key
    assert c["session_file"] == os.path.join(c["log_dir"], "session.json")


def test_log_appends_to_the_fake_plugin_log():
    _skyrim.log("info", "hello from the test")
    log_dir = json.loads(_skyrim.config())["log_dir"]
    with open(os.path.join(log_dir, "plugin.log"), encoding="utf-8") as f:
        assert "info hello from the test" in f.read()


def test_busy_cap_from_the_fake_ini():
    # The fake build reads max_inflight_total once, when the module is imported, so this runs in a fresh interpreter.
    code_text = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(BUILD)!r})\n"
        "import _skyrim\n"
        "req = lambda i: json.dumps({'type': 'request', 'id': i, 'op': 'papyrus.call_static',"
        " 'args': {'script': 'Fake', 'function': 'Never', 'args': []}})\n"
        "a, b = _skyrim.submit(req(1)), _skyrim.submit(req(2))\n"
        "print(json.loads(_skyrim.call(req(3), 5.0))['error']['code'])\n"
    )
    env = dict(os.environ, SKYRIM_FAKE_INI="max_inflight_total = 2\n")
    out = subprocess.run([sys.executable, "-c", code_text], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "busy"
