# Task 18: Properties, batch, and the in-flight caps

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Modify: `plugin/src/Ops.cpp`
- Create: `livetest/test_m1_batch_props.py`

**Interfaces:**
- Consumes: Task 17 (`ops::Run`, `pv::ResolveTarget`, `pv::ToVariable`, `pv::FromVariable`, `sig::FindProperty`), Task 12 (`parse_property_args`, `parse_batch_args`, `BatchArgs`).
- Produces: `papyrus.get_property`, `papyrus.set_property`, `papyrus.batch` served by `ops::Run`. Batch semantics (spec 4.3): steps run in order, one `AddTask` per step, not a transaction; the batch-level `timeout_ms` covers all steps (per-step `timeout_ms` is ignored); the response is `{"results": [...]}` with one entry per step run, shorter than the step list if `stop_on_error` stopped it; a timed-out or disconnected batch runs no further steps.

- [ ] **Step 1: Write the live tests**

`livetest/test_m1_batch_props.py`:
```python
import pytest

from support import raw_connect
from skyrim import _framing, errors
from skyrim.values import Form

PLAYER = Form(form_id=0x14)


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def test_batch_runs_in_order_and_reports_each_step(game):
    b = (game.batch()
         .call("Utility", "RandomInt", 1, 6)
         .call("Game", "GetPlayer")
         .call("NoSuchScriptAnywhere", "Foo")
         .call_method(PLAYER, "Actor", "GetActorValue", "Health"))
    results = b.run(stop_on_error=False)
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
    results = b.run(timeout=30)
    assert len(results) == 100 and all(1 <= r <= 6 for r in results)


def test_property_errors(game):
    with pytest.raises(errors.NotFound, match="no script named"):
        game.get_property(PLAYER, "NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.NotFound, match="no property"):
        game.get_property(PLAYER, "Actor", "NoSuchProperty")


def test_per_connection_cap_returns_busy(session):
    s = raw_connect(session)
    n = 300
    for i in range(1, n + 1):
        s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                   "args": {"script": "Utility", "function": "Wait", "args": [1.0]}}))
    codes = []
    s.settimeout(30)
    for _ in range(n):
        r = _framing.read_frame(s)
        codes.append("ok" if r["ok"] else r["error"]["code"])
    s.close()
    assert codes.count("busy") == n - 256
    assert codes.count("ok") == 256
```
v1 property ops target auto properties. Property reads and writes on a real script property are tested in M1.5 (including what the engine does with a full property that has getter/setter functions; `vm_error` is an acceptable, documented outcome), against the test ESP's quest script (no vanilla script offers a property that is both always bound and safe to write). Here only the error paths run live; the happy path is exercised by M1.5's plan before v1 ships.

- [ ] **Step 2: Implement properties and batch in `Ops.cpp`**

Add inside the anonymous namespace of `plugin/src/Ops.cpp`, after `CallMethod`:
```cpp
// v1 targets auto properties (a stored value). A full property's getter/setter is Papyrus code the
// engine cannot run synchronously here; M1.5's test ESP probes what the engine does with those.
json GetProperty(const PropertyArgs& p)
{
    sig::FindProperty(p.script, p.property);  // clear not_found before touching the object
    auto obj = pv::ResolveTarget(p.object, p.script).object;
    Variable out;
    if (!VM()->GetPropertyValue(obj, p.property.c_str(), out))
        throw ProtocolError(ErrorCode::vm_error, "could not read " + p.script + "." + p.property);
    return pv::FromVariable(out);
}

void SetProperty(const PropertyArgs& p)
{
    const auto type = sig::FindProperty(p.script, p.property);
    auto obj = pv::ResolveTarget(p.object, p.script).object;
    Variable in;
    pv::ToVariable(*p.value, type, p.property + " (property of " + p.script + ")", in);
    if (!VM()->SetPropertyValue(obj, p.property.c_str(), in))
        throw ProtocolError(ErrorCode::vm_error, "could not write " + p.script + "." + p.property);
}

struct BatchState
{
    BatchArgs args;
    std::size_t next = 0;
    json results = json::array();
    Done done;
    Cancelled cancelled;
};

void BatchStep(const std::shared_ptr<BatchState>& st);

void QueueBatchStep(const std::shared_ptr<BatchState>& st)
{
    SKSE::GetTaskInterface()->AddTask([st] { BatchStep(st); });  // one task per step (spec 4.5)
}

void BatchStep(const std::shared_ptr<BatchState>& st)
{
    if (st->cancelled()) return;
    if (st->next == st->args.calls.size()) return st->done(OpResult::Ok({{"results", st->results}}));
    const auto step = st->args.calls[st->next++];
    Run(step.op, step.args, [st](OpResult r) {
        st->results.push_back(r.ok ? json{{"ok", true}, {"result", r.result}}
                                   : json{{"ok", false}, {"error", {{"code", to_string(r.code)}, {"message", r.message}, {"detail", r.detail}}}});
        if (!r.ok && st->args.stop_on_error) return st->done(OpResult::Ok({{"results", st->results}}));
        QueueBatchStep(st);
    }, st->cancelled);
}
```
Then in `Run`, replace the line
```cpp
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
    (void)cancelled;  // used by papyrus.batch (Task 18)
```
with
```cpp
        if (op == "papyrus.get_property") return done(OpResult::Ok(GetProperty(parse_property_args(args, false))));
        if (op == "papyrus.set_property") {
            SetProperty(parse_property_args(args, true));
            return done(OpResult::Ok(nullptr));
        }
        if (op == "papyrus.batch") {
            auto st = std::make_shared<BatchState>();
            st->args = parse_batch_args(args);
            st->done = std::move(done);
            st->cancelled = std::move(cancelled);
            return BatchStep(st);
        }
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    }
```
Note: a batch step's `done` runs on the main thread (Task 17's `Callback` converts results there), so `BatchState` is only touched from the main thread. The `catch` in `Run` cannot fire after `done` was moved into the batch: `parse_batch_args` is the only throwing call before the move, and `BatchStep` routes each step's errors through that step's own `Run`.

- [ ] **Step 3: Build, deploy, run**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh   # then load a save
source tools/env.sh
uv run --project python pytest livetest/test_m1_batch_props.py livetest/test_m1_calls.py -q
```
Expected: all pass. The cap test takes about 1–2 s (256 one-second waits run concurrently in Papyrus).

- [ ] **Step 4: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): property access, ordered batches, in-flight caps"
```
