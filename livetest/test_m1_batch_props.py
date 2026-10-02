"""Needs a save loaded (interactive): s4test on the test machine (spikes/RESULTS.md). The property read uses the auto property
spike S7c enumerated:
SPY_S7C_QUEST=0x001093E4 SPY_S7C_SCRIPT=QF_TGTQ01Handler_001093E4 SPY_S7C_PROPERTY=Alias_TGHandlerSilverMoldAlias
SPY_S7C_KIND=Alias (optional SPY_S7C_GETTER: a parameterless function returning the same property; unset for this one).
The Int auto property read uses SPY_OWN_QUEST/SPY_OWN_SCRIPT (as in test_m1_calls.py) and
SPY_OWN_INT_PROPERTY=currentAttackDelta (CWCampaignScript). The write-visibility test uses PetFramework_ParentQuest
(Update.esm 0x01002F1C), fixed in the file, and restores what it writes.
test_per_connection_cap_returns_busy is opt-in: deploy plugin/config/variants/low-cap.ini and set SPY_LOW_CAP=1.
The other SPY_* values come from `source livetest/s4test.env`; under SKYTHON_LIVE=1 a missing one fails the test.
Run with -s to see the batch timings (ms per step).
"""
import os
import time

import pytest

from skython import errors
from skython.values import Alias, Form
from support import raw_connect, read_frame, send_request, spy_env

PLAYER = Form(form_id=0x14)
KINDS = {"Int": int, "Float": float, "Bool": bool, "String": str, "Form": Form, "Alias": Alias, "Array": list}
WAIT_1S = {"script": "Utility", "function": "Wait", "args": [1.0]}
RANDOM = {"script": "Utility", "function": "RandomInt", "args": [1, 6]}


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def _timed_run(batch, n, **kw):
    t0 = time.perf_counter()
    results = batch.run(**kw)
    ms = (time.perf_counter() - t0) * 1000
    print(f"\nbatch of {n} steps: {ms:.0f} ms total, {ms / n:.1f} ms per step")
    return results


def test_batch_runs_in_order_and_reports_each_step(game):
    results = (game.batch()
               .call("Utility", "RandomInt", 1, 6)
               .call("Game", "GetPlayer")
               .call("NoSuchScriptAnywhere", "Foo")
               .call_method(PLAYER, "Actor", "GetActorValue", "Health")).run(stop_on_error=False)
    assert len(results) == 4
    assert 1 <= results[0] <= 6
    assert results[1].form_id == 0x14
    assert isinstance(results[2], errors.UnknownFunction)
    assert results[3] > 0


def test_batch_stops_on_first_error(game):
    results = (game.batch()
               .call("Utility", "RandomInt", 1, 6)
               .call("NoSuchScriptAnywhere", "Foo")
               .call("Utility", "RandomInt", 1, 6)).run(stop_on_error=True)
    assert len(results) == 2 and isinstance(results[1], errors.UnknownFunction)


def test_batch_of_100_steps(game):
    b = game.batch()
    for _ in range(100):
        b.call("Utility", "RandomInt", 1, 6)
    results = _timed_run(b, 100, timeout=60)  # S9: one drain (~46 ms on the test machine) per primitive call alone: several seconds
    assert len(results) == 100 and all(1 <= r <= 6 for r in results)


def test_batch_without_timeout_gets_time_for_every_step(game):
    # No timeout given: Batch.run sends 5 s per step (capped), not one 5 s default for the whole batch. At one drain
    # (~46 ms on the test machine, S9) or more per step, 150 steps need well over 5 s.
    b = game.batch()
    for _ in range(150):
        b.call("Utility", "RandomInt", 1, 6)
    results = _timed_run(b, 150)
    assert len(results) == 150 and all(1 <= r <= 6 for r in results)


def test_nested_batch_and_state_steps_are_rejected(session):
    # Core answers this while parsing the request (parse_batch_args); the plugin never sees such a batch.
    s = raw_connect(session)
    s.settimeout(30)
    for i, bad_op in enumerate(("papyrus.batch", "game.state"), start=1):
        send_request(s, "papyrus.batch", {"calls": [{"op": "papyrus.call_static", "args": RANDOM},
                                                     {"op": bad_op, "args": {}}]}, req_id=i)
        r = read_frame(s)
        assert r["id"] == i and r["ok"] is False and r["error"]["code"] == "bad_request", r
    s.close()


def test_batch_timeout_reports_progress(session):
    s = raw_connect(session)
    calls = [{"op": "papyrus.call_static", "args": RANDOM},
             {"op": "papyrus.call_static", "args": {"script": "Utility", "function": "Wait", "args": [3.0]}},
             {"op": "papyrus.call_static", "args": RANDOM}]
    send_request(s, "papyrus.batch", {"calls": calls, "timeout_ms": 800})
    s.settimeout(30)
    r = read_frame(s)
    assert r["ok"] is False and r["error"]["code"] == "timeout"
    d = r["error"]["detail"]
    assert d["completed"] == 1 and len(d["results"]) == 1 and d["results"][0]["ok"] is True
    s.close()


def test_property_errors(game):
    with pytest.raises(errors.NotFound, match="no script named"):
        game.get_property(PLAYER, "NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.NotFound, match="no property"):
        game.get_property(PLAYER, "Actor", "NoSuchProperty")


def _s7c_property():
    """The quest and the auto property spike S7c enumerated (spikes/RESULTS.md, the S7c row)."""
    quest, script, prop, kind = spy_env("SPY_S7C_QUEST", "SPY_S7C_SCRIPT", "SPY_S7C_PROPERTY", "SPY_S7C_KIND")
    return int(quest, 16), script, prop, kind


def test_get_property_reads_an_auto_property(game):
    """Read-only happy path on S7c's auto property. On s4test it holds alias 0 of its own quest, so
    the read must decode as that Alias: None is not accepted (an earlier build read None here and the test passed)."""
    quest_id, script, prop, kind = _s7c_property()
    quest = game.resolve(form_id=quest_id)
    first = game.get_property(quest, script, prop)
    print(f"\n{script}.{prop} -> {first!r}")
    assert type(first) is KINDS[kind], (kind, first)
    if kind == "Alias":
        assert isinstance(first, Alias) and first.quest.form_id == quest_id and first.alias_id == 0, first
    assert game.get_property(quest, script, prop) == first  # two reads agree (nothing writes it meanwhile)
    # Only when the script declares a parameterless function that returns this property (vanilla auto properties
    # rarely have one): the Papyrus side must see the same value.
    getter = os.environ.get("SPY_S7C_GETTER")
    if getter:
        assert game.call_method(quest, script, getter) == first


def test_set_property_refuses_a_reference_counted_property(game):
    """Only Int, Float and Bool properties are written (no lock-free write of a reference-counted value). S7c's property is alias-typed (a reference-counted value):
    writing even the value it already holds is refused `unsupported` before anything is written, and it reads the
    same afterwards."""
    quest_id, script, prop, kind = _s7c_property()
    quest = game.resolve(form_id=quest_id)
    before = game.get_property(quest, script, prop)
    assert type(before) is KINDS[kind], (kind, before)
    # The same value it holds: the refusal cannot be a type_mismatch, only the Int/Float/Bool write rule.
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(quest, script, prop, before)
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(quest, script, prop, None)  # a None write would clear it: refused the same way
    assert game.get_property(quest, script, prop) == before


def test_get_property_reads_an_int_auto_property(game):
    """An Int auto property cannot hold None, so this read is not vacuous the way an unfilled object property is.
    SPY_OWN_QUEST/SPY_OWN_SCRIPT as in test_m1_calls.py; SPY_OWN_INT_PROPERTY: an Int auto property declared on that
    script (CWCampaignScript.currentAttackDelta, `int Property currentAttackDelta auto Conditional Hidden`)."""
    quest, script, prop = spy_env("SPY_OWN_QUEST", "SPY_OWN_SCRIPT", "SPY_OWN_INT_PROPERTY")
    quest_id = int(quest, 16)
    quest = game.resolve(form_id=quest_id)
    first = game.get_property(quest, script, prop)
    print(f"\n{script}.{prop} -> {first!r}")
    assert type(first) is int, first
    assert game.get_property(quest, script, prop) == first


# PetFramework_ParentQuest (Update.esm, runtime form id 0x01002F1C), always bound (checked live). Its script declares
# `Int Property CurrentPetCount = 0 Auto Hidden` and the bytecode getter `Int Function GetCurrentPetCount()` with body
# `Return CurrentPetCount`, so Papyrus reads the same auto variable our write lands in.
PET_QUEST = 0x01002F1C
PET_SCRIPT = "PetFramework_ParentQuestScript"
PET_PROPERTY = "CurrentPetCount"
PET_GETTER = "GetCurrentPetCount"


def test_set_property_is_seen_by_papyrus(game):
    """Writes the auto property, then reads it back through the script's own bytecode getter. Always restores the
    original value (s4test is never saved anyway). This is the check the slot index is right: Papyrus finds the
    variable through its own name-to-index map."""
    quest = game.resolve(form_id=PET_QUEST)
    original = game.get_property(quest, PET_SCRIPT, PET_PROPERTY)
    assert type(original) is int, original
    assert game.call_method(quest, PET_SCRIPT, PET_GETTER) == original  # both routes agree before the write
    try:
        game.set_property(quest, PET_SCRIPT, PET_PROPERTY, original + 3)
        seen = game.call_method(quest, PET_SCRIPT, PET_GETTER)
        print(f"\n{PET_SCRIPT}.{PET_PROPERTY}: original {original}, set {original + 3}, Papyrus getter -> {seen}")
        assert seen == original + 3
        assert game.get_property(quest, PET_SCRIPT, PET_PROPERTY) == original + 3
    finally:
        game.set_property(quest, PET_SCRIPT, PET_PROPERTY, original)
    assert game.get_property(quest, PET_SCRIPT, PET_PROPERTY) == original
    assert game.call_method(quest, PET_SCRIPT, PET_GETTER) == original


def test_full_property_is_unsupported(game):
    # ObjectReference.X is a full property (`float Property X` with a get() function); refused before anything runs.
    with pytest.raises(errors.Unsupported, match="full property"):
        game.get_property(PLAYER, "ObjectReference", "X")
    with pytest.raises(errors.Unsupported, match="full property"):  # found on the parent of the named script
        game.get_property(PLAYER, "Actor", "X")


@pytest.mark.skipif(os.environ.get("SPY_LOW_CAP") != "1",
                    reason="opt-in: deploy plugin/config/variants/low-cap.ini and run with SPY_LOW_CAP=1")
def test_per_connection_cap_returns_busy(session):
    """Needs plugin/config/variants/low-cap.ini (bridge cap 16) and SPY_LOW_CAP=1. Counts the bridge's own busy
    replies: with the low cap deployed there must be some."""
    s = raw_connect(session)
    n = 40  # more than the low cap, few enough that the VM is never flooded
    for i in range(1, n + 1):
        send_request(s, "papyrus.call_static", WAIT_1S, req_id=i)
    s.settimeout(30)
    replies = [read_frame(s) for _ in range(n)]
    s.close()
    busy = [r for r in replies if not r["ok"] and r["error"]["code"] == "busy"]
    ok = [r for r in replies if r["ok"]]
    print(f"\nper-connection cap: {len(ok)} ok, {len(busy)} busy of {n}")
    assert len(busy) > 0, "SPY_LOW_CAP=1 but no busy reply: is plugin/config/variants/low-cap.ini deployed?"
    assert len(ok) + len(busy) == n and len(ok) <= 16
