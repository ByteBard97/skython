# Task 30: Properties and batch

Adapted from: branch `plan-out-of-process`, task 18 (`task-18-properties-batch-and-the-in-flight-caps.md`). The batch code in `Ops.cpp` is the branch's apart from comments and a `catch (...)`; the property code now uses the engine's `GetPropertyValue`/`SetPropertyValue` and refuses full properties (plan review F). What moved: the per-connection in-flight cap is now the dev bridge's (Task 23, Python) and the global cap is the Dispatcher's (Task 18), so the cap test here exercises the bridge's cap through a low-cap ini; the batch timeout's partial results are reported by the Dispatcher from the `Progress` callback this task feeds.

> Part of the [Skython v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.3, 4.5 rule 8, 4.9. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 29 is done (which needs S2, S4, S7, S8 and S9 PASS). S5 and S6 gate the M2 plan only.
>
> **Runs on:** the Windows build box plus the game, which runs on the box ([Task 1c](task-01c-part-c-on-the-box.md)); live tests run on Linux through the dev bridge and the session tunnel.
>
> **STOP and ask the owner (do not improvise) if:** a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; `get_property` on the auto property S7c enumerated is refused `unsupported` (the engine then sets `getFunction` on auto properties too, and the full-property check below is wrong: record it and STOP).
>
> **Measured finding (Task 30, AE 1.6.1170, ruling R57; supersedes the property code in Step 2):** `IVirtualMachine::GetPropertyValue` returned true with a None value for every auto property (`GetPropertyValue=true vm_raw_type=0`, Int properties included), so it is **not used**, nor `SetPropertyValue`. The shipped code finds the auto variable's absolute index (`sig::FindPropertySlot`: the parents' variable counts plus `autoVarIndex`), **reads** it through `VirtualMachine::GetVariableValue(obj, index, out)` (vfunc `GetVariableValue1`; falls back to the direct slot, logged once, if that answers false or None while the slot holds a value) and **writes** `Object::variables[index]` directly after the type check. Fix round 1 logged `GetVariableValue=true ... engine_equals_direct=true`, and a write to `PetFramework_ParentQuestScript.CurrentPetCount` was seen by the script's own getter. Full properties are unsupported. The Step 2 code block below is the original brief, kept for the record.
>
> **Verify (original brief):** `IVirtualMachine::GetPropertyValue` / `SetPropertyValue(BSTSmartPointer<Object>&, const char*, Variable&) -> bool` (the smart pointer by non-const reference, a C string name; verified at the pin, `API-VERIFICATION.md`); `PropertyTypeInfo::{type, getFunction, setFunction}`. `Object::GetProperty` is **not** used: at the pin it indexes `variables[]` with the declaring class's own index and ignores the parent classes' variables, while `Object::GetVariable` adds them, so one of the two is wrong for a property declared on a script that extends another script with variables.

**Files:**
- Modify: `plugin/src/Ops.cpp`
- Create: `plugin/config/variants/low-cap.ini` (dev ini with small in-flight caps, for the cap test only)
- Create: `livetest/test_m1_batch_props.py`

**Interfaces:**
- Consumes: Tasks 27-29 (`ops::Run` with its `Cancelled` and `Progress` parameters, `pv::ResolveTarget`, `pv::ToVariable`, `pv::FromVariable`, `sig::FindProperty`), core Task 16 (`PropertyArgs`, `parse_property_args`, `BatchArgs`, `BatchStep`, `parse_batch_args`, `to_string(ErrorCode)`), Task 18 (the Dispatcher reads the latest `Progress` payload `{"completed": n, "results": [...]}` into a batch's `timeout` detail), Task 23 (the bridge answers `busy` beyond `max_inflight_per_connection`), Task 26 (`livetest/support.py`: `raw_connect`, `encode`, `read_frame`, `send_request`).
- Produces: `papyrus.get_property`, `papyrus.set_property` (auto properties only; a full property is `unsupported`), `papyrus.batch` served by `ops::Run`. Batch semantics (spec 4.3): steps run in order, one `AddTask` per step, not a transaction; the batch-level `timeout_ms` covers all steps (per-step `timeout_ms` is ignored; `Batch.run` without a timeout sends the sum of the step timeouts, 5 s for a step without one, capped at `MAX_TIMEOUT_MS`: Task 21); the response is `{"results": [...]}` with one entry per step run (`{"ok": true, "result": ...}` or `{"ok": false, "error": {code, message, detail}}`), shorter than the step list if `stop_on_error` stopped it; a timed-out, cancelled or load-interrupted batch runs no further steps (`cancelled()` is checked before each step). A batch that times out reports how far it got (`detail.completed`, `detail.results`). `papyrus.batch` and `game.state` are not allowed as steps (`bad_request`, nothing runs; core's `parse_batch_args` refuses any op the generated table does not mark batchable while the request is parsed, so the plugin never sees such a batch).

Known costs and limits (Task 32 puts them in the README):
- **Each batch step costs at least one task drain** (one `AddTask` for the step, the VM, and for an object result at most one more task (none when the VM calls back synchronously inside our task, R52)). S9: one drain (~46 ms on the box) per primitive call alone, two with a spinning Python thread; Task 31 records the batch figure. Batches give ordering, not speed. **Measured (Task 30, all launches): 46.4-46.8 ms per RandomInt step at n=100 and n=150, one drain per step (S9: ~46 ms), so a step queued during a drain runs in the next drain, not the same one.**
- **Property ops serve auto properties only.** A full property (one with Papyrus get/set code) is refused `unsupported`; call a Papyrus function for it. Inside an SKSE task, reads go through the engine's `GetVariableValue` by the auto variable's absolute index and writes assign the variable directly (measured: `GetPropertyValue` is broken at this runtime, see above); neither is **synchronised with Papyrus threads** that may run the same script, and `OnPropertyChanged` does **not** fire. For anything that must be atomic or must notify, call a pre-authored Papyrus setter with `call_method` (spec 4.9).
- **A batch without a timeout** gets the sum of its steps' timeouts (5 s per step), capped at `MAX_TIMEOUT_MS`; give `run(timeout=...)` for a tighter bound.

- [ ] **Step 1: Write the live tests**

`plugin/config/variants/low-cap.ini` (the cap test needs a small per-connection cap so it never puts hundreds of latent calls into the VM; deploy it only for that run; sections are ignored by core's parser):
```ini
[Skython]
python_enabled = true
scripts_enabled = true
bridge_enabled = true
port = 45977
fixed_token =
session_file =
default_timeout_ms = 5000
max_inflight_per_connection = 16
max_inflight_total = 64
enable_console = false
```
If Task 25's `Skython.dev.ini` sets other keys (a fixed token, for example), copy them here unchanged; only the two caps differ.

`livetest/test_m1_batch_props.py`:
```python
import os

import pytest

from skython import errors
from skython.values import Alias, Form
from support import raw_connect, read_frame, send_request

PLAYER = Form(form_id=0x14)
KINDS = {"Int": int, "Float": float, "Bool": bool, "String": str, "Form": Form, "Alias": Alias, "Array": list}
WAIT_1S = {"script": "Utility", "function": "Wait", "args": [1.0]}
RANDOM = {"script": "Utility", "function": "RandomInt", "args": [1, 6]}


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


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
    results = b.run(timeout=60)  # S9: one drain (~46 ms on the box) per primitive call alone: several seconds
    assert len(results) == 100 and all(1 <= r <= 6 for r in results)


def test_batch_without_timeout_gets_time_for_every_step(game):
    # No timeout given: Batch.run sends 5 s per step (capped), not one 5 s default for the whole batch. At one drain
    # (~46 ms on the box, S9) or more per step, 150 steps need well over 5 s.
    b = game.batch()
    for _ in range(150):
        b.call("Utility", "RandomInt", 1, 6)
    results = b.run()
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
    try:
        return (int(os.environ["SPY_S7C_QUEST"], 16), os.environ["SPY_S7C_SCRIPT"], os.environ["SPY_S7C_PROPERTY"],
                os.environ["SPY_S7C_KIND"])
    except KeyError as e:
        pytest.skip(f"set SPY_S7C_QUEST, SPY_S7C_SCRIPT, SPY_S7C_PROPERTY and SPY_S7C_KIND from spikes/RESULTS.md "
                    f"({e.args[0]} is missing)")


def test_get_property_reads_an_auto_property(game):
    """Read-only happy path; writes wait for M1.5's test ESP (no vanilla property is safe to write)."""
    quest_id, script, prop, kind = _s7c_property()
    quest = game.resolve(form_id=quest_id)
    first = game.get_property(quest, script, prop)
    unfilled = first is None and kind in ("Form", "Alias")  # an object property may hold None
    assert unfilled or type(first) is KINDS[kind], (kind, first)
    assert game.get_property(quest, script, prop) == first  # two reads agree (nothing writes it meanwhile)
    # Only when the script declares a parameterless function that returns this property (vanilla auto properties
    # rarely have one): the Papyrus side must see the same value.
    getter = os.environ.get("SPY_S7C_GETTER")
    if getter:
        assert game.call_method(quest, script, getter) == first


def test_per_connection_cap_returns_busy(session):
    """Needs plugin/config/variants/low-cap.ini (bridge cap 16). Counts the bridge's own busy replies."""
    s = raw_connect(session)
    n = 40  # more than the low cap, few enough that the VM is never flooded
    for i in range(1, n + 1):
        send_request(s, "papyrus.call_static", WAIT_1S, req_id=i)
    s.settimeout(30)
    replies = [read_frame(s) for _ in range(n)]
    s.close()
    busy = [r for r in replies if not r["ok"] and r["error"]["code"] == "busy"]
    ok = [r for r in replies if r["ok"]]
    if not busy:
        pytest.skip("no busy reply: deploy plugin/config/variants/low-cap.ini for this test (the dev ini's cap is 256)")
    assert len(ok) + len(busy) == n and len(ok) <= 16
```
v1 property ops serve auto properties. A read runs live here, on the auto property S7c enumerated (`SPY_S7C_KIND` is one of `Int`, `Float`, `Bool`, `String`, `Form`, `Alias`, `Array`, from the type S7c printed). S7c's property is `Alias_TGHandlerSilverMoldAlias` on quest `0x001093E4`, script `QF_TGTQ01Handler_001093E4`, typed `ReferenceAlias` (`obj=true cls=ReferenceAlias`), so `SPY_S7C_KIND=Alias`: `get_property` returns an alias, decoded by Task 29's alias JSON (`ObjectToJson` falls through the form check to `AliasObjectToJson`). If Task 29's alias tests did not pass, this test cannot either. The QF_ script declares no getter (S7c), so leave `SPY_S7C_GETTER` unset. Writes, and the refusal of a full property, are tested in M1.5 against the test ESP's quest script (no vanilla script offers a property that is both always bound and safe to write). (Superseded in fix round 1: `test_set_property_is_seen_by_papyrus` writes `PetFramework_ParentQuestScript.CurrentPetCount` on Update.esm 0x01002F1C, always bound, checks the script's own getter `GetCurrentPetCount()` sees the write, and restores it in a `finally`; `test_full_property_is_unsupported` covers the refusal. The S7c test asserts the Alias it holds on s4test, alias 0 of 0x1093E4, instead of accepting None.)

- [ ] **Step 2: Implement properties and batch in `Ops.cpp`**

Add inside the anonymous namespace of `plugin/src/Ops.cpp`, after `CallMethod` (and `Resolve`), plus `#include <memory>` at the top:
```cpp
// v1 serves auto properties only, through the engine's own GetPropertyValue/SetPropertyValue on the bound script
// object: they find the property's storage the engine's way, including for a property declared on a parent script
// (Object::GetProperty at the pin ignores the parents' variable offset). A full property (Papyrus get/set code) is
// refused: its code would not run here. Nothing is synchronised with Papyrus threads, and OnPropertyChanged does not
// fire (see the limits above).
RE::BSScript::TypeInfo AutoPropertyType(const PropertyArgs& p)
{
    const auto info = sig::FindProperty(p.script, p.property);  // clear not_found before touching the object
    if (info.getFunction || info.setFunction)
        throw ProtocolError(ErrorCode::unsupported, p.script + "." + p.property + " is a full property (it has Papyrus "
            "get/set code); v1 reads and writes auto properties only: call a Papyrus function on the script instead");
    return info.type;
}

json GetProperty(const PropertyArgs& p)
{
    AutoPropertyType(p);
    auto target = pv::ResolveTarget(p.object, p.script);
    Variable out;
    if (!VM()->GetPropertyValue(target.object, p.property.c_str(), out))
        throw ProtocolError(ErrorCode::vm_error, "the VM could not read " + p.script + "." + p.property + " on " + target.desc);
    return pv::FromVariable(out);
}

void SetProperty(const PropertyArgs& p)
{
    const auto type = AutoPropertyType(p);
    auto target = pv::ResolveTarget(p.object, p.script);
    Variable in;
    pv::ToVariable(*p.value, type, p.property + " (property of " + p.script + ")", in);
    if (!VM()->SetPropertyValue(target.object, p.property.c_str(), in))
        throw ProtocolError(ErrorCode::vm_error, "the VM could not write " + p.script + "." + p.property + " on " + target.desc);
}

struct BatchState
{
    BatchArgs args;
    std::size_t next = 0;
    json results = json::array();
    Done done;
    Cancelled cancelled;
    Progress progress;
};
// A step's `done` can run on a VM thread (primitive results are converted there) or inside an SKSE task. BatchState is
// safe by ordering, not by thread: the next step is only queued from the previous step's `done`, so at most one step
// is in flight and each access happens-before the next (the AddTask queue hand-off). No single "main thread" touches
// it: SKSE tasks run on varying job threads (R49).
// Measured (Task 30): 46.4-46.8 ms per RandomInt step, one drain per step (S9), so a step queued during a drain runs
// in the next drain, not the same one.

void BatchStep(const std::shared_ptr<BatchState>& st);

void QueueBatchStep(const std::shared_ptr<BatchState>& st)
{
    SKSE::GetTaskInterface()->AddTask([st] { ops::InTask in; BatchStep(st); });  // one task per step (spec 4.5)
}

void BatchStep(const std::shared_ptr<BatchState>& st)
{
    try {
        if (st->cancelled()) return;
        if (st->next == st->args.calls.size()) return st->done(OpResult::Ok({{"results", st->results}}));
        const auto step = st->args.calls[st->next++];
        Run(step.op, step.args, [st](OpResult r) {
            st->results.push_back(r.ok ? json{{"ok", true}, {"result", r.result}}
                                       : json{{"ok", false}, {"error", {{"code", to_string(r.code)}, {"message", r.message}, {"detail", r.detail}}}});
            // Tell the Dispatcher (Task 18) how far we got, so a timeout can report it (the steps' side effects already happened).
            if (st->progress) st->progress(json{{"completed", st->results.size()}, {"results", st->results}});
            if (!r.ok && st->args.stop_on_error) return st->done(OpResult::Ok({{"results", st->results}}));
            QueueBatchStep(st);
        }, st->cancelled, Progress{});
    } catch (const std::exception& e) {
        log::Error(std::string("ops: batch step threw: ") + e.what());
        st->done(OpResult::Err(ErrorCode::internal, "internal error in a batch step"));
    } catch (...) {  // a main-thread task never lets an exception escape (Global Constraints)
        log::Error("ops: batch step threw an unknown exception");
        st->done(OpResult::Err(ErrorCode::internal, "internal error in a batch step"));
    }
}
```
Then in `Run`, add before the `unsupported` fall-through:
```cpp
        if (op == "papyrus.get_property") return done(OpResult::Ok(GetProperty(parse_property_args(args, false))));
        if (op == "papyrus.set_property") {
            SetProperty(parse_property_args(args, true));
            return done(OpResult::Ok(nullptr));
        }
        if (op == "papyrus.batch") {
            auto st = std::make_shared<BatchState>();
            st->args = parse_batch_args(args);  // refuses non-batchable steps (nested batches, game.state): bad_request
            st->done = std::move(done);
            st->cancelled = std::move(cancelled);
            st->progress = std::move(progress);
            return BatchStep(st);
        }
```
and delete the two `(void)cancelled; (void)progress;` lines in `Run`. Note: the `catch` in `Run` cannot fire after `done` was moved into the batch: `parse_batch_args` is the only throwing code before the move, and `BatchStep` routes each step's errors through that step's own `Run` and its own try/catch. (No plugin-side check for nested batches or `game.state` steps: core refuses them while parsing the request, Task 16, and `test_nested_batch_and_state_steps_are_rejected` pins that answer.)

- [ ] **Step 3: Build, deploy, run**

The game runs on the Windows box ([Task 1c](task-01c-part-c-on-the-box.md)); the save is loaded through DevBench; every wait is bounded and counts only this launch's log text. The cap test needs the low-cap ini, so the run is in two parts. The property happy path needs the quest S7c used, so load the named save S7 ran on (`s4test`, spikes/RESULTS.md), not the most recent one.
```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load s4test"     # the S7 save; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
export SPY_S7C_QUEST=0x001093E4 SPY_S7C_SCRIPT=QF_TGTQ01Handler_001093E4 SPY_S7C_PROPERTY=Alias_TGHandlerSilverMoldAlias SPY_S7C_KIND=Alias   # the S7c row of spikes/RESULTS.md
export SPY_S4_QUEST=0x0010FDD5 SPY_S4_ALIAS=7 SPY_S4_REF=0x0001A677                   # as in Task 29
export SPY_PLAIN_QUEST=0x0010FDD5 SPY_PLAIN_ALIAS=0 SPY_OWN_QUEST=0x0001C4A1 SPY_OWN_SCRIPT=CWCampaignScript SPY_OWN_FUNCTION=getAttackDeltaMissionBonus   # SPY_OWN_* from Task 29's record in spikes/RESULTS.md
export SPY_OWN_INT_PROPERTY=currentAttackDelta   # Task 30: an Int auto property of CWCampaignScript (the non-vacuous read test skips without it)
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_batch_props.py livetest/test_m1_calls.py -q -rs
# test_per_connection_cap_returns_busy reports a skip here. Quit the game, then:
tools/box/box_quit.sh     # NOT_CLEAN with NO_NEW_CRASH_LOG: tools/box/box_stop.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/low-cap.ini
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env     # new launch, new token
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test"     # the S7 save; then Task 1b block B6
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 600
SKYTHON_LIVE=1 uv run --project python pytest livetest/test_m1_batch_props.py -q -rs -k per_connection_cap
tools/box/box_quit.sh
# Put the dev ini back afterwards:
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
```
Expected: the first run passes except the one skip (`test_per_connection_cap_returns_busy`); the second run passes it (at most 16 ok, the rest busy). A skip is not a pass: run the second part before committing, and a skip of `test_get_property_reads_an_auto_property` (missing `SPY_S7C_*`) or of the alias tests means they did not run. Any skip whose reason is `live test: set SKYTHON_LIVE=1` is a failure. S9: one drain (~46 ms on the box) per primitive call alone, two with a spinning Python thread, so the 100-step batch takes at least about 5 s and the 150-step one with no timeout at least about 7 s; Task 31 records the batch figure. If `test_get_property_reads_an_auto_property` gets `Unsupported` ("is a full property"), STOP (see the STOP list).

- [ ] **Step 4: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): property access and ordered batches"
```
