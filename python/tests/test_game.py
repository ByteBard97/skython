"""The Game API (both modes share it), exercised over the in-process transport and the fake `_skyrim`.
The requests the Game builds are checked against the golden fixtures; transport-specific behaviour
(sockets, handshake) is Task 22's."""
import contextlib
import inspect
import pickle
import threading
import time

import pytest

from fake_skyrim import FakeRemoteError
from support import load_fixture, wait_until
from skyrim import _protocol_generated as g
from skyrim import errors
from skyrim.game import Game
from skyrim.inprocess import connect_in_process
from skyrim.values import INT_MAX, INT_MIN, Form, from_wire

REQUESTS = load_fixture("requests.json")


def echo(op, args, ctx):
    return {"op": op, "args": args}


def results(value):
    return lambda op, args, ctx: value


# -- calls -----------------------------------------------------------------------------------------------
def test_call_sends_a_request_and_returns_the_result(game, fake):
    fake.handler = echo
    r = game.call("Utility", "RandomInt", 1, 6)
    assert r == {"op": "papyrus.call_static", "args": {
        "script": "Utility", "function": "RandomInt", "args": [1, 6], "require_unpaused": False}}
    assert fake.requests[0]["type"] == "request" and type(fake.requests[0]["id"]) is int


def test_call_method_sends_reference_and_timeout(game, fake):
    fake.handler = echo
    game.call_method(Form(form_id=20), "Actor", "GetActorValue", "Health", timeout=2.0)
    args = fake.requests[0]["args"]
    assert args["object"] == {"kind": "form", "form_id": 20}
    assert args["timeout_ms"] == 2000


def test_int_overflow_raises_before_sending(game, fake):
    with pytest.raises(errors.TypeMismatch):
        game.call("Utility", "RandomInt", 1, 2**31)
    assert fake.requests == []


def test_float_array_with_big_ints_is_sent(game, fake):
    fake.handler = echo
    game.call("X", "Y", [3_000_000_000, 1.5])
    assert fake.requests[0]["args"]["args"] == [{"array": "Float", "items": [3e9, 1.5]}]


def test_remote_errors_are_typed(game, fake):
    def handler(op, args, ctx):
        raise FakeRemoteError("not_ready", "loading", {"reason": "loading"})

    fake.handler = handler
    with pytest.raises(errors.NotReady) as ei:
        game.call("Game", "GetPlayer")
    assert ei.value.detail["reason"] == "loading"
    clone = pickle.loads(pickle.dumps(ei.value))
    assert isinstance(clone, errors.NotReady) and clone.detail == {"reason": "loading"}


def test_the_fakes_own_errors_reach_the_caller_typed(game, fake):
    fake._fake_set_not_ready("no_game")
    with pytest.raises(errors.NotReady) as ei:
        game.call("Fake", "Echo", 1)
    assert ei.value.detail["reason"] == "no_game"
    assert game.state()["interactive"] is False  # game.state does not need a game
    fake._fake_set_not_ready(None)
    with pytest.raises(errors.InternalError, match="Fake.Throw"):
        game.call("Fake", "Throw")
    with pytest.raises(errors.Unsupported):
        game.call("Nope", "Nope")
    t0 = time.monotonic()
    with pytest.raises(errors.Timeout):
        game.call("Fake", "Never", timeout=0.2)
    assert time.monotonic() - t0 < 2


def test_retry_not_ready_until_ok(fake):
    count = {"n": 0}

    def handler(op, args, ctx):
        count["n"] += 1
        if count["n"] < 3:
            raise FakeRemoteError("not_ready", "loading")
        return 7

    fake.handler = handler
    with connect_in_process(retry_not_ready=5.0) as game:
        assert game.call("Utility", "RandomInt", 1, 6) == 7
    assert count["n"] == 3


def test_retry_not_ready_never_resubmits_a_call_a_load_ended_after_dispatch(fake):
    # The call may already have had its effect (an AddItem ran) when the load cancelled it: retrying could repeat it.
    out = {}

    def caller():
        with connect_in_process(retry_not_ready=5.0) as game:
            try:
                game.call("Fake", "Never", timeout=3)
            except Exception as e:  # noqa: BLE001 - checked below
                out["e"] = e

    t = threading.Thread(target=caller)
    t.start()
    wait_until(lambda: fake.handler_calls == 1, what="the call to reach the backend")
    fake._fake_bump_loads()
    t.join(10)
    assert isinstance(out.get("e"), errors.NotReady) and out["e"].detail["dispatched"] is True
    assert len(fake.requests) == 1  # not resubmitted


def test_answers_that_finish_out_of_order_reach_the_right_callers(game, fake):
    def handler(op, args, ctx):
        n = args["args"][0]
        ctx.wait(0.3 if n == 1 else 0.0)  # the first call finishes last
        return n

    fake.handler = handler
    got = {}
    threads = [threading.Thread(target=lambda i=i: got.__setitem__(i, game.call("X", "Y", i))) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert got == {1: 1, 2: 2}


def test_oversize_request_is_a_typed_error_and_nothing_is_sent(game, fake):
    with pytest.raises(errors.EncodeError, match="limit"):
        game.call("X", "Y", "a" * (16 * 1024 * 1024))
    fake.handler = echo
    assert game.call("X", "Y", 1)["args"]["args"] == [1]
    assert len(fake.requests) == 1


def test_unencodable_string_is_a_typed_error_and_nothing_is_sent(game, fake):
    with pytest.raises(errors.EncodeError, match="Unicode"):
        game.call("X", "Y", "bad \ud800 text")
    assert fake.requests == []


def test_malformed_results_are_violations_not_crashes(game, fake):
    for bad in ({"results": "nope"}, {"results": [1]}, {"no": "results"}):
        fake.handler = results(bad)
        with pytest.raises(errors.ProtocolViolation):
            game.batch().call("X", "Y").run()
    fake.handler = results([1, 2])
    with pytest.raises(errors.ProtocolViolation):
        game.state()
    fake.handler = results({"results": []})
    assert game.batch().call("X", "Y").run() == []


def test_a_response_without_a_boolean_ok_is_a_violation(fake):
    fake.call = lambda text, timeout: '{"type": "response", "id": 1, "result": 1}'
    with connect_in_process() as game:
        with pytest.raises(errors.ProtocolViolation, match="ok"):
            game.call("X", "Y")


def test_close_and_closed(fake):
    game = connect_in_process()
    assert game.closed is False
    with game:
        pass
    assert game.closed is True
    with pytest.raises(errors.ConnectionLost):
        game.call("Fake", "Echo", 1)


# -- timeout arithmetic ----------------------------------------------------------------------------------
def test_tiny_timeout_is_one_millisecond_not_zero(game, fake):
    fake.handler = echo
    for t in (0.0004, 1.001, 0.3):
        with contextlib.suppress(errors.Timeout):  # 1 ms is a real deadline for the fake; only what is sent matters
            game.call("X", "Y", timeout=t)
    assert [r["args"]["timeout_ms"] for r in fake.requests] == [1, 1001, 300]


@pytest.mark.parametrize("bad", [0, -1, 0.0, float("nan"), float("inf"), "5", True, 5_000_000, 3600.001])
def test_invalid_timeouts_raise_before_sending(game, fake, bad):
    with pytest.raises(ValueError):
        game.call("X", "Y", timeout=bad)
    assert fake.requests == []


def test_the_timeout_range_is_the_protocols_one_hour(game, fake):
    fake.handler = echo
    game.call("X", "Y", timeout=3600)
    with contextlib.suppress(errors.Timeout):
        game.call("X", "Y", timeout=0.001)
    with pytest.raises(ValueError, match="3600000"):
        game.call("X", "Y", timeout=3600.001)
    with pytest.raises(ValueError):  # an explicit batch timeout over the hour; step timeouts that add up to more are capped
        game.batch().call("A", "b").run(timeout=3600.001)
    assert [r["args"]["timeout_ms"] for r in fake.requests] == [3_600_000, 1]


@pytest.mark.parametrize("bad", [0, -1, float("nan"), None, "5"])
def test_bad_call_timeout_is_a_value_error(fake, bad):
    with pytest.raises(ValueError):
        connect_in_process(call_timeout=bad)


def test_defaults_are_the_tables_and_the_v1_values():
    p = inspect.signature(Game).parameters
    assert p["call_timeout"].default == g.CALL_TIMEOUT_S == 30.0
    assert p["retry_not_ready"].default is False
    assert inspect.signature(Game.wait_ready).parameters["timeout"].default == g.READY_TIMEOUT_S == 120.0
    assert (g.RETRY_NOT_READY_S, g.GRACE_S) == (30.0, 2.0)
    assert inspect.signature(connect_in_process).parameters["call_timeout"].default == g.CALL_TIMEOUT_S


def test_batch_wait_covers_the_default_step_timeouts(fake):
    # call_timeout is 0.3 s but one batch step may legitimately take the plugin's 5 s default
    def handler(op, args, ctx):
        ctx.wait(0.6)
        return {"results": [{"ok": True, "result": 3}]}

    fake.handler = handler
    with connect_in_process(call_timeout=0.3) as game:
        assert game.batch().call("Utility", "RandomInt", 1, 6).run() == [3]
        with pytest.raises(errors.Timeout) as ei:  # single calls still use call_timeout
            game.call("Utility", "RandomInt", 1, 6)
        assert ei.value.detail["late_cancel"] is True


def test_batch_timeout_is_the_sum_of_the_step_timeouts(game, fake):
    fake.handler = results({"results": []})
    game.batch().call("A", "b", timeout=2).call("C", "d").call_method(Form(form_id=1), "E", "f", timeout=0.5).run()
    game.batch().call("A", "b", timeout=2).run(timeout=9)
    game.batch().call("A", "b").call("C", "d").run()
    game.batch().call("A", "b", timeout=3000).call("C", "d", timeout=3000).run()
    with pytest.raises(ValueError):
        game.batch().call("A", "b", timeout=0)
    sent = [r["args"] for r in fake.requests]
    assert sent[0]["timeout_ms"] == 2000 + 5000 + 500
    assert all("timeout_ms" not in c["args"] for c in sent[0]["calls"])
    assert sent[1]["timeout_ms"] == 9000
    assert sent[2]["timeout_ms"] == 2 * 5000  # no timeouts at all: still the sum, so the plugin waits as long as we do
    assert sent[3]["timeout_ms"] == g.MAX_TIMEOUT_MS  # the sum is capped at the protocol maximum


# -- results ---------------------------------------------------------------------------------------------
def test_batch_results_mix_values_and_errors(game, fake):
    b = game.batch().call("Fake", "Echo", 3).call("Nope", "Nope")
    out = b.run(stop_on_error=False)
    assert out[0] == 3 and isinstance(out[1], errors.Unsupported)
    sent = fake.requests[0]["args"]
    assert sent["stop_on_error"] is False
    assert [c["op"] for c in sent["calls"]] == ["papyrus.call_static", "papyrus.call_static"]


def test_form_decodes_result(game, fake):
    fake.handler = results({"kind": "form", "form_id": 0x12EB7, "form_key": "Skyrim.esm|0x012EB7", "type": "Weapon", "epoch": 1})
    f = game.form("Skyrim.esm|0x012EB7")
    assert f.form_id == 0x12EB7 and f.type == "Weapon"
    assert fake.requests[0]["args"] == {"form_key": "Skyrim.esm|0x012EB7"}
    with pytest.raises(TypeError):
        game.resolve(form_key="Skyrim.esm|0x1", form_id=1)


def test_state_decodes_the_player(game, fake):
    fake.handler = results({"interactive": True, "epoch": 1, "player": {"kind": "form", "form_id": 20, "type": "Actor"}})
    assert game.state()["player"] == Form(form_id=20)


def test_wait_ready_polls_state(game, fake):
    states = iter([False, False, True])
    fake.handler = lambda op, args, ctx: {"interactive": next(states), "player": None, "epoch": 1}
    assert game.wait_ready(timeout=5)["interactive"] is True


def test_wait_ready_gives_up_with_not_ready_not_call_timeout(game, fake):
    fake._fake_set_not_ready("no_game")
    with pytest.raises(errors.NotReady) as ei:  # CallTimeout means the bridge did not answer; this is the game
        game.wait_ready(timeout=0.3)
    assert ei.value.detail["reason"] == "wait_ready timed out"
    assert ei.value.detail["interactive"] is False


def test_game_epoch_is_the_welcome_value_only(game, fake):
    fake.handler = results({"interactive": True, "epoch": 5})
    assert game.epoch == 0
    assert game.state()["epoch"] == 5  # the game has loaded a save since the Game was made
    assert game.epoch == 0  # documented: Game.epoch never follows


def test_alias_takes_the_quests_epoch(game):
    q = Form(form_id=0x800, epoch=3)
    a = game.alias(q, 2, "LocationAlias")
    assert (a.quest, a.alias_id, a.alias_type, a.epoch) == (q, 2, "LocationAlias", 3)


# -- the golden request fixtures ------------------------------------------------------------------------
OPTIONAL_KEYS = {"timeout_ms", "require_unpaused"}  # the Game may add these to an args object


def covers(actual, expected, path="args"):
    """`actual` (what the Game sent) equals `expected` (the fixture), allowing the optional keys."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        for k in expected:
            assert k in actual, f"{path}: missing {k!r}"
            covers(actual[k], expected[k], f"{path}.{k}")
        assert set(actual) - set(expected) <= OPTIONAL_KEYS, f"{path}: unexpected keys {set(actual) - set(expected)}"
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected)):
            covers(a, e, f"{path}[{i}]")
    else:
        assert type(actual) is type(expected) and actual == expected, f"{path}: {actual!r} != {expected!r}"


def _values(a):
    return [from_wire(x) for x in a.get("args", [])]


def _secs(a):
    return a["timeout_ms"] / 1000 if "timeout_ms" in a else None


def _call_static(game, a):
    return game.call(a["script"], a["function"], *_values(a), timeout=_secs(a), require_unpaused=a.get("require_unpaused", False))


def _call_method(game, a):
    return game.call_method(from_wire(a["object"]), a["script"], a["function"], *_values(a), timeout=_secs(a),
                            require_unpaused=a.get("require_unpaused", False))


STEPS = {
    "papyrus.call_static": lambda b, a: b.call(a["script"], a["function"], *_values(a), require_unpaused=a.get("require_unpaused", False)),
    "papyrus.call_method": lambda b, a: b.call_method(from_wire(a["object"]), a["script"], a["function"], *_values(a),
                                                      require_unpaused=a.get("require_unpaused", False)),
    "papyrus.get_property": lambda b, a: b.get_property(from_wire(a["object"]), a["script"], a["property"]),
    "papyrus.set_property": lambda b, a: b.set_property(from_wire(a["object"]), a["script"], a["property"], from_wire(a["value"])),
}


def _batch(game, a):
    b = game.batch()
    for step in a["calls"]:
        STEPS[step["op"]](b, step["args"])
    return b.run(stop_on_error=a["stop_on_error"], timeout=_secs(a))


CALLERS = {
    "game.state": lambda game, a: game.state(),
    "form.resolve": lambda game, a: game.resolve(**a),
    "papyrus.call_static": _call_static,
    "papyrus.call_method": _call_method,
    "papyrus.get_property": lambda game, a: game.get_property(from_wire(a["object"]), a["script"], a["property"]),
    "papyrus.set_property": lambda game, a: game.set_property(from_wire(a["object"]), a["script"], a["property"], from_wire(a["value"])),
    "papyrus.batch": _batch,
}


def replay_handler(op, args, ctx):
    return {"results": []} if op == "papyrus.batch" else {}


@pytest.mark.parametrize("fx", REQUESTS["valid"], ids=lambda r: f"{r['id']}-{r['op']}")
def test_valid_request_fixtures_replay(game, fake, fx):
    fake.handler = replay_handler
    CALLERS[fx["op"]](game, fx["args"])
    sent = fake.requests[0]
    assert set(sent) == set(fx) == {"type", "id", "op", "args"}
    assert sent["type"] == "request" and sent["op"] == fx["op"]
    assert type(sent["id"]) is int and sent["id"] >= 0
    covers(sent["args"], fx["args"])
    assert set(sent["args"]) <= {a[0] for a in g.OPS[sent["op"]]["args"]}, "the Game sent an argument the table does not know"


def test_the_game_speaks_exactly_the_ops_in_the_table():
    assert set(CALLERS) == set(g.OPS)
    assert set(STEPS) == set(g.BATCHABLE_OPS)
    assert {fx["op"] for fx in REQUESTS["valid"]} == set(g.OPS)


def has_out_of_range_int(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return not INT_MIN <= v <= INT_MAX
    if isinstance(v, dict):
        return any(has_out_of_range_int(x) for x in v.values())
    if isinstance(v, list):
        return any(has_out_of_range_int(x) for x in v)
    return False


# Only the type_mismatch cases the Game can spot without a signature table (an Int out of 32-bit range);
# other type_mismatch fixtures are the server's to reject.
TYPE_MISMATCH = [c["message"] for c in REQUESTS["invalid"]
                 if c["error"] == "type_mismatch" and c["message"].get("op") in CALLERS and has_out_of_range_int(c["message"]["args"])]


@pytest.mark.parametrize("msg", TYPE_MISMATCH, ids=lambda m: f"{m['id']}-{m['op']}")
def test_type_mismatch_fixtures_are_refused_before_sending(game, fake, msg):
    fake.handler = replay_handler
    with pytest.raises(errors.TypeMismatch):
        CALLERS[msg["op"]](game, msg["args"])
    assert fake.requests == []


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), "5", None, [], -0.5])
def test_bad_retry_not_ready_is_a_value_error(fake, bad):
    with pytest.raises(ValueError):
        connect_in_process(retry_not_ready=bad)


@pytest.mark.parametrize("ok", [False, 0, 0.0, True, 2.5])
def test_good_retry_not_ready_is_accepted(fake, ok):
    connect_in_process(retry_not_ready=ok).close()
