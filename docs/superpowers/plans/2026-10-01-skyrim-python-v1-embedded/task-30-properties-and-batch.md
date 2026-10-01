# Task 30: Properties and batch

Adapted from: branch `plan-out-of-process`, task 18 (`task-18-properties-batch-and-the-in-flight-caps.md`). The property and batch code in `Ops.cpp` is unchanged apart from comments (it compiled on the build box). What moved: the per-connection in-flight cap is now the dev bridge's (Task 23, Python) and the global cap is the Dispatcher's (Task 18), so the cap test here exercises the bridge's cap through a low-cap ini; the batch timeout's partial results are reported by the Dispatcher from the `Progress` callback this task feeds.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.3, 4.5 rule 8, 4.9. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 29 is done (which needs S2, S4, S7, S8 and S9 PASS). S5 and S6 gate the M2 plan only.
>
> **Runs on:** the Windows build box plus the game; live tests run on Linux through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** a live test crashes the game; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170; `Object::GetProperty` does not exist and no equivalent is found.
>
> **Verify:** `RE::BSScript::Object::GetProperty(const BSFixedString&)` returns `Variable*`, null when absent (verified at the pin). Fallback if S7 shows it does not see the property: `VirtualMachine::GetPropertyValue` / `SetPropertyValue` take the `BSTSmartPointer<Object>&` itself, a `const char*` name and a `Variable&`.

**Files:**
- Modify: `plugin/src/Ops.cpp`
- Create: `plugin/config/variants/low-cap.ini` (dev ini with small in-flight caps, for the cap test only)
- Create: `livetest/test_m1_batch_props.py`

**Interfaces:**
- Consumes: Tasks 27-29 (`ops::Run` with its `Cancelled` and `Progress` parameters, `pv::ResolveTarget`, `pv::ToVariable`, `pv::FromVariable`, `sig::FindProperty`), core Task 16 (`PropertyArgs`, `parse_property_args`, `BatchArgs`, `BatchStep`, `parse_batch_args`, `to_string(ErrorCode)`), Task 18 (the Dispatcher reads the latest `Progress` payload `{"completed": n, "results": [...]}` into a batch's `timeout` detail), Task 23 (the bridge answers `busy` beyond `max_inflight_per_connection`), Task 26 (`livetest/support.py`: `raw_connect`, `encode`, `read_frame`, `send_request`).
- Produces: `papyrus.get_property`, `papyrus.set_property`, `papyrus.batch` served by `ops::Run`. Batch semantics (spec 4.3): steps run in order, one `AddTask` per step, not a transaction; the batch-level `timeout_ms` covers all steps (per-step `timeout_ms` is ignored); the response is `{"results": [...]}` with one entry per step run (`{"ok": true, "result": ...}` or `{"ok": false, "error": {code, message, detail}}`), shorter than the step list if `stop_on_error` stopped it; a timed-out, cancelled or load-interrupted batch runs no further steps (`cancelled()` is checked before each step). A batch that times out reports how far it got (`detail.completed`, `detail.results`). `papyrus.batch` and `game.state` are not allowed as steps (`bad_request`, nothing runs).

Known costs and limits (Task 32 puts them in the README):
- **Each batch step costs 2-3 frames** (one `AddTask` for the step, the VM, and for an object result one more task to convert it), so a batch runs at roughly 15-30 steps per second at 30-60 fps. Batches give ordering, not speed; Task 31 records the real figure.
- **Property access is not synchronised with Papyrus.** `get_property`/`set_property` read and write the script's stored value directly on the main thread while Papyrus threads may run the same script; a concurrent write of a String or object property can be observed half-done. `OnPropertyChanged` does **not** fire, and a full property's getter and setter code is not run. For anything that must be atomic or must notify, call a pre-authored Papyrus setter with `call_method` (spec 4.9).

- [ ] **Step 1: Write the live tests**

`plugin/config/variants/low-cap.ini` (the cap test needs a small per-connection cap so it never puts hundreds of latent calls into the VM; deploy it only for that run; sections are ignored by core's parser):
```ini
[SkyrimPython]
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
If Task 25's `SkyrimPython.dev.ini` sets other keys (a fixed token, for example), copy them here unchanged; only the two caps differ.

`livetest/test_m1_batch_props.py`:
```python
import pytest

from skyrim import errors
from skyrim.values import Form
from support import raw_connect, read_frame, send_request

PLAYER = Form(form_id=0x14)
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
    results = b.run(timeout=60)  # 2-3 frames per step: several seconds
    assert len(results) == 100 and all(1 <= r <= 6 for r in results)


def test_nested_batch_and_state_steps_are_rejected(session):
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
v1 property ops target auto properties. Reads and writes on a real script property are tested in M1.5 against the test ESP's quest script (no vanilla script offers a property that is both always bound and safe to write); here only the error paths run live.

- [ ] **Step 2: Implement properties and batch in `Ops.cpp`**

Add inside the anonymous namespace of `plugin/src/Ops.cpp`, after `CallMethod` (and `Resolve`), plus `#include <memory>` at the top:
```cpp
// v1 targets auto properties (a stored value). The stored Variable is read and written directly on the main
// thread: Papyrus threads are not stopped, no lock is taken, OnPropertyChanged does not fire, and a full
// property's getter/setter code does not run (see the limits above). M1.5's test ESP probes the full-property case.
// Object::GetProperty(BSFixedString) returning Variable* is the route S7 (Task 9) enumerated (signature verified at the pin).
// If it is absent, use VM()->GetPropertyValue(obj, name.c_str(), out) (verified signature: `bool GetPropertyValue(BSTSmartPointer<Object>&, const char*, Variable&)`; the smart pointer by non-const reference, a C string name).
Variable* PropertySlot(const pv::Target& target, const PropertyArgs& p)
{
    auto* slot = target.object->GetProperty(RE::BSFixedString(p.property));
    if (!slot) throw ProtocolError(ErrorCode::vm_error, "the object " + target.desc + " has no readable slot for " + p.script + "." + p.property);
    return slot;
}

json GetProperty(const PropertyArgs& p)
{
    sig::FindProperty(p.script, p.property);  // clear not_found before touching the object
    const auto target = pv::ResolveTarget(p.object, p.script);
    const Variable copy = *PropertySlot(target, p);  // copy first, convert the copy
    return pv::FromVariable(copy);
}

void SetProperty(const PropertyArgs& p)
{
    const auto type = sig::FindProperty(p.script, p.property);
    const auto target = pv::ResolveTarget(p.object, p.script);
    Variable in;
    pv::ToVariable(*p.value, type, p.property + " (property of " + p.script + ")", in);
    *PropertySlot(target, p) = in;
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
// A step's `done` can run on a VM thread (primitive results are converted there), but the next step is only
// queued from the previous step's `done`, so exactly one thread touches BatchState at a time.

void BatchStep(const std::shared_ptr<BatchState>& st);

void QueueBatchStep(const std::shared_ptr<BatchState>& st)
{
    SKSE::GetTaskInterface()->AddTask([st] { BatchStep(st); });  // one task per step (spec 4.5)
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
            st->args = parse_batch_args(args);
            // Nested batches and game.state are not steps. Core's parse_batch_args (Task 16) already refuses any op not
            // marked batchable in the generated op table; this check is the second line of defence and names the step.
            for (std::size_t i = 0; i < st->args.calls.size(); ++i)
                if (st->args.calls[i].op == "papyrus.batch" || st->args.calls[i].op == "game.state")
                    return done(OpResult::Err(ErrorCode::bad_request,
                        "step " + std::to_string(i) + ": " + st->args.calls[i].op + " cannot be used inside papyrus.batch"));
            st->done = std::move(done);
            st->cancelled = std::move(cancelled);
            st->progress = std::move(progress);
            return BatchStep(st);
        }
```
and delete the two `(void)cancelled; (void)progress;` lines in `Run`. Note: the `catch` in `Run` cannot fire after `done` was moved into the batch: `parse_batch_args` and the nested-op check are the only throwing code before the move, and `BatchStep` routes each step's errors through that step's own `Run` and its own try/catch.

- [ ] **Step 3: Build, deploy, run**

Loading a save is manual (press Continue); every wait is bounded. The cap test needs the low-cap ini, so the run is in two parts.
```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --package
tools/launch_game.sh
source tools/env.sh
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "Main Menu" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
# Manual: press Continue. Then:
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "interactive = true" 300
uv run --project python pytest livetest/test_m1_batch_props.py livetest/test_m1_calls.py -q -rs
# test_per_connection_cap_returns_busy reports a skip here. Quit the game, then:
tools/deploy.sh out/SkyrimPython.dll plugin/config/variants/low-cap.ini
tools/launch_game.sh   # Continue again, then wait for "interactive = true" as above
uv run --project python pytest livetest/test_m1_batch_props.py -q -k per_connection_cap
# Put the dev ini back afterwards:
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
```
Expected: the first run passes except the one skip (`test_per_connection_cap_returns_busy`); the second run passes it (at most 16 ok, the rest busy). A skip is not a pass for the cap: run the second part before committing. The 100-step batch takes about 3-7 s.

- [ ] **Step 4: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): property access and ordered batches"
```
