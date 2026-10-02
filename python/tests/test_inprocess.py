"""The in-process transport, and the fake `_skython` it runs against in these tests (Tasks 23 and 24 rely on
the fake behaving like core's Dispatcher, so its rules are pinned here too)."""
import json
import threading
import time

import pytest

from fake_skython import FakeRemoteError, FakeSkython
from support import wait_until
from skython import _protocol_generated as g
from skython import errors
from skython.inprocess import InProcessTransport, connect_in_process


def req(op="papyrus.call_static", req_id=1, **args):
    return json.dumps({"type": "request", "id": req_id, "op": op, "args": args})


def fake_call(function, *a, req_id=1, **extra):
    return req("papyrus.call_static", req_id, script="Fake", function=function, args=list(a), **extra)


# -- the transport ---------------------------------------------------------------------------------------
def test_the_welcome_is_built_from_config_and_state(fake):
    fake.epoch = 4
    t = InProcessTransport()  # imports _skython, which the fixture installed
    assert t.welcome["protocol"] == [1, 1] and t.welcome["epoch"] == 4
    assert (t.welcome["plugin_version"], t.welcome["game_version"], t.welcome["runtime"]) == ("0.0.0-fake", "0.0.0", "fake")
    assert t.welcome["capabilities"] == list(g.OPS) and t.welcome["backend"] == "fake"
    assert t.closed is False


def test_another_protocol_major_is_refused(fake):
    fake.PROTOCOL = (2, 0)
    with pytest.raises(errors.ProtocolMismatch, match="2.0"):
        InProcessTransport()


def test_the_request_text_has_the_fixture_shape(fake):
    t = InProcessTransport()
    resp = t.request("game.state", {}, 5.0)
    assert resp["ok"] is True and resp["result"]["backend"] == "fake"
    sent = fake.requests[0]
    assert set(sent) == {"type", "id", "op", "args"} and sent["type"] == "request" and sent["id"] == resp["id"]
    assert t.request("game.state", {}, 5.0)["id"] == resp["id"] + 1


@pytest.mark.parametrize("bad", ["not json", "[1, 2]", '{"type": "response", "id": 999, "ok": true, "result": 1}'],
                         ids=["not-json", "not-object", "wrong-id"])
def test_a_malformed_response_is_a_protocol_violation(fake, bad):
    fake.call = lambda text, timeout: bad
    with pytest.raises(errors.ProtocolViolation):
        InProcessTransport().request("game.state", {}, 5.0)


def test_a_closed_transport_raises_connection_lost(fake):
    t = InProcessTransport()
    t.close()
    with pytest.raises(errors.ConnectionLost):
        t.request("game.state", {}, 5.0)
    assert fake.requests == []


def test_an_expired_wait_is_timeout_with_late_cancel_and_nothing_is_left_in_flight(fake):
    with connect_in_process(call_timeout=0.2) as game:
        t0 = time.monotonic()
        with pytest.raises(errors.Timeout) as ei:
            game.call("Fake", "Never")  # no timeout_ms: the fake's 5 s default would fire later
        assert time.monotonic() - t0 < 2
    assert ei.value.detail["late_cancel"] is True
    assert {"paused", "menus"} <= set(ei.value.detail)  # like the real module's late-cancel reply
    assert fake.in_flight() == 0


# -- the fake's Dispatcher rules -------------------------------------------------------------------------
def test_a_bad_request_is_answered_at_once(fake):
    for text, req_id in (("{nope", 0), ('{"type": "request", "id": 7, "op": "no.such.op", "args": {}}', 7),
                         ('{"id": 8, "op": "game.state", "args": {}}', 8)):
        resp = json.loads(fake.wait(fake.submit(text), 1.0))
        assert (resp["id"], resp["ok"], resp["error"]["code"]) == (req_id, False, "bad_request")


def test_not_ready_only_for_ops_that_need_a_game(fake):
    fake._fake_set_not_ready("loading")
    resp = json.loads(fake.call(fake_call("Echo", 1), 1.0))
    assert resp["error"]["code"] == "not_ready"
    assert resp["error"]["detail"] == {"reason": "loading", "paused": False, "menus": []}
    assert json.loads(fake.call(req("game.state"), 1.0))["result"]["interactive"] is False
    assert fake.handler_calls == 1  # only game.state reached the backend


def test_require_unpaused_while_paused_is_not_ready_paused(fake):
    fake._fake_set_paused(True)
    resp = json.loads(fake.call(fake_call("Echo", 1, require_unpaused=True), 1.0))
    assert resp["error"]["detail"]["reason"] == "paused"
    assert json.loads(fake.call(fake_call("Echo", 2), 1.0))["result"] == 2


def test_timeout_ms_is_the_deadline(fake):
    t0 = time.monotonic()
    resp = json.loads(fake.call(fake_call("Never", timeout_ms=100), 5.0))
    assert resp["error"]["code"] == "timeout" and time.monotonic() - t0 < 2
    assert {"paused", "menus"} <= set(resp["error"]["detail"])


def test_timeout_ms_and_require_unpaused_count_only_where_the_op_declares_them(fake):
    fake._fake_set_paused(True)
    fake.handler = lambda op, args, ctx: ctx.wait(0.3) or "done"
    text = req("papyrus.get_property", object={"kind": "form", "form_id": 20}, script="ObjectReference", property="X",
               timeout_ms=50, require_unpaused=True)
    resp = json.loads(fake.call(text, 5.0))
    assert resp["ok"] is True and resp["result"] == "done"  # neither refused as paused nor timed out after 50 ms


def test_a_batch_timeout_reports_its_progress(fake):
    calls = [{"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Echo", "args": [1]}},
             {"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Never", "args": []}}]
    resp = json.loads(fake.call(req("papyrus.batch", calls=calls, timeout_ms=200), 5.0))
    assert resp["error"]["code"] == "timeout"
    detail = resp["error"]["detail"]
    assert detail["completed"] == 1 and detail["results"] == [{"ok": True, "result": 1}]
    assert {"paused", "menus"} <= set(detail)


def test_busy_at_the_in_flight_cap(tmp_path):
    fake = FakeSkython(root=tmp_path, max_inflight_total=2)
    try:
        held = [fake.submit(fake_call("Never", req_id=i)) for i in (1, 2)]
        assert json.loads(fake.call(fake_call("Echo", 3, req_id=3), 1.0))["error"]["code"] == "busy"
        fake.cancel(held[0])
        assert json.loads(fake.call(fake_call("Echo", 4, req_id=4), 1.0))["result"] == 4
    finally:
        fake.close()


def test_a_load_fails_calls_in_flight_with_not_ready_loading(fake):
    ticket = fake.submit(fake_call("Never"))
    wait_until(lambda: fake.handler_calls == 1, what="the call to start")
    fake._fake_bump_loads()
    resp = json.loads(fake.wait(ticket, 1.0))
    assert resp["error"]["code"] == "not_ready" and resp["error"]["detail"]["reason"] == "loading"
    assert resp["error"]["detail"]["dispatched"] is True  # it had reached the backend
    assert {"paused", "menus"} <= set(resp["error"]["detail"])
    # A quit to the main menu also counts: the reason is the one of that moment.
    ticket = fake.submit(fake_call("Never", req_id=2))
    wait_until(lambda: fake.handler_calls == 2, what="the second call to start")
    fake._fake_set_not_ready("no_game")
    fake._fake_bump_loads()
    assert json.loads(fake.wait(ticket, 1.0))["error"]["detail"]["reason"] == "no_game"


def test_cancel_erases_the_ticket_and_the_late_result_is_counted(fake):
    ticket = fake.submit(fake_call("Sleep", 100))
    fake.cancel(ticket)
    t0 = time.monotonic()
    assert fake.wait(ticket, 2.0) is None and time.monotonic() - t0 < 0.5  # unknown tickets are not waited for
    wait_until(lambda: fake.late_results == 1, what="the late result")


def test_wait_any_returns_the_done_ones_as_soon_as_one_is_done(fake):
    slow = fake.submit(fake_call("Sleep", 300, req_id=1))
    quick = fake.submit(fake_call("Echo", "q", req_id=2))
    done = fake.wait_any([slow, quick], 5.0)
    assert [t for t, _ in done] == [quick] and json.loads(done[0][1])["result"] == "q"
    rest = fake.wait_any([slow], 5.0)
    assert [t for t, _ in rest] == [slow]
    assert fake.wait_any([slow, quick], 0.05) == []  # both consumed


def test_handler_errors_map_to_codes(fake):
    err = json.loads(fake.call(fake_call("Throw"), 1.0))["error"]
    assert err["code"] == "internal" and "Fake.Throw" in err["message"]
    assert json.loads(fake.call(fake_call("Nope"), 1.0))["error"]["code"] == "unknown_function"  # as FakeBackend

    def refuse(op, args, ctx):
        raise FakeRemoteError("vm_error", "refused", {"x": 1})

    fake.handler = refuse
    err = json.loads(fake.call(req("game.state"), 1.0))["error"]
    assert err == {"code": "vm_error", "message": "refused", "detail": {"x": 1}}


def test_config_state_and_log(fake, tmp_path):
    cfg = json.loads(fake.config())
    assert cfg["bridge_enabled"] is False and cfg["port"] == 45977 and cfg["log_dir"] == str(tmp_path / "logs")
    assert set(g.CONFIG_KEYS) <= set(cfg)
    assert json.loads(fake.state())["interactive"] is True
    fake.log("info", "hello")
    assert fake.logs == [("info", "hello")]


# -- many threads at once --------------------------------------------------------------------------------
def test_sixteen_threads_calling_at_once_get_their_own_answers(game, fake):
    results, failures = {}, []

    def worker(n):
        try:
            for i in range(25):
                assert game.call("Fake", "Echo", n * 1000 + i) == n * 1000 + i
            results[n] = True
        except Exception as e:  # noqa: BLE001 - reported below
            failures.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not failures and len(results) == 16
    assert fake.handler_calls == 16 * 25 and fake.in_flight() == 0


# -- argument checks and details that follow core's parser and Dispatcher --------------------------------
def test_a_batch_timeout_with_no_finished_step_still_has_completed_and_results(fake):
    calls = [{"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Never", "args": []}}]
    resp = json.loads(fake.call(req("papyrus.batch", calls=calls, timeout_ms=100), 5.0))
    detail = resp["error"]["detail"]
    assert resp["error"]["code"] == "timeout" and detail["completed"] == 0 and detail["results"] == []
    assert {"paused", "menus"} <= set(detail)
    resp = json.loads(fake.call(fake_call("Never", timeout_ms=100), 5.0))  # a single call has neither
    assert "completed" not in resp["error"]["detail"]


STEP = {"op": "papyrus.call_static", "args": {"script": "Fake", "function": "Echo", "args": [1]}}


@pytest.mark.parametrize("text", [
    fake_call("Echo", 1, timeout_ms=0),
    fake_call("Echo", 1, timeout_ms=g.MAX_TIMEOUT_MS + 1),
    fake_call("Echo", 1, timeout_ms=1.5),
    fake_call("Echo", 1, timeout_ms="5"),
    fake_call("Echo", 1, timeout_ms=True),
    fake_call("Echo", 1, require_unpaused="yes"),
    req("papyrus.call_static", function="Echo"),
    req("papyrus.call_static", script="Fake"),
    req("papyrus.batch", calls=[]),
    req("papyrus.batch", calls=[STEP] * (g.MAX_BATCH_CALLS + 1)),
    req("papyrus.batch", calls=[{"op": "game.state", "args": {}}]),
    req("papyrus.batch", calls=[STEP], timeout_ms=0),
    req("papyrus.batch", calls=[STEP], stop_on_error="no"),
], ids=["t0", "tmax+1", "tfloat", "tstr", "tbool", "unpaused-str", "no-script", "no-function", "empty-batch",
        "big-batch", "unbatchable-step", "batch-t0", "stop-str"])
def test_the_parsers_argument_checks_answer_bad_request_at_submit(fake, text):
    resp = json.loads(fake.wait(fake.submit(text), 1.0))
    assert resp["error"]["code"] == "bad_request"
    assert fake.handler_calls == 0 and fake.requests == []


def test_a_call_that_ended_before_its_thread_started_never_runs_the_handler(fake):
    fake.handler = lambda op, args, ctx: 1 / 0
    ticket = fake.submit(fake_call("Echo", 1))
    fake.cancel(ticket)  # may or may not beat the thread; either way no result is returned
    fake._fake_bump_loads()
    time.sleep(0.1)
    assert fake.wait(ticket, 0.05) is None


def test_dispatched_only_when_the_handler_had_started(fake):
    started = threading.Event()
    gate = threading.Event()

    def handler(op, args, ctx):
        started.set()
        gate.wait(5)

    real_run = fake._run
    fake._run = lambda *a: (gate.wait(5), real_run(*a))  # hold the thread back before it starts
    ticket = fake.submit(fake_call("Echo", 1))
    fake.handler = handler
    fake._fake_bump_loads()
    resp = json.loads(fake.wait(ticket, 1.0))
    gate.set()
    assert resp["error"]["code"] == "not_ready" and "dispatched" not in resp["error"]["detail"]
    time.sleep(0.1)
    assert fake.handler_calls == 0 and not started.is_set()


def test_base_state_follows_the_loads_like_fake_backend(fake):
    assert fake.base_state()["epoch"] == 0
    fake._fake_set_not_ready("loading")
    st = fake.base_state()
    assert st["loading"] is True and st["game_loaded"] is False and st["interactive"] is False
    fake._fake_set_not_ready(None)
    fake._fake_bump_loads()
    assert fake.base_state()["epoch"] == 1 and fake.base_state()["game_loaded"] is True
    fake.epoch = 7
    assert json.loads(fake.state())["epoch"] == 7
