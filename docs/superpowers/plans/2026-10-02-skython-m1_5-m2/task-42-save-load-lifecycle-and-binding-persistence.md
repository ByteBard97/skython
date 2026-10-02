# Task 42: Save/load lifecycle tests and PackHandle/GetAlias save persistence

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves, Aliases R45), **Review Focus** (items 3 and 5 are pinned live here), **Contracts** (loading rule, event frame `epoch`/`suppressed`) and Part E's brief for Task 42 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** the Windows build box (one small plugin change: a dev-only log line) and the game on the box, through `tools/remote_build.sh` and `tools/box/*` ([v1 Task 1c](../2026-10-01-skyrim-python-v1-embedded/task-01c-part-c-on-the-box.md)).
>
> **STOP and ask the owner (do not improvise) if:** a new save name already exists; a save's `.ess` does not appear within 30 s; the game crashes or hangs on any save or load; the plain alias `SPY_PLAIN_QUEST`/`SPY_PLAIN_ALIAS` is already bound on a fresh `s4test` load (its `Alias.GetOwningQuest` does not answer `type_mismatch`); using an old-epoch reference crashes the game (that is the exact failure Review Focus 5 forbids); the game does not reach the main menu within 10 minutes; the box does not answer `ssh` within 10 s.
>
> **Saves:** this task makes exactly three new throwaway saves per run, `skython_t42a_<ts>`, `skython_t42b_<ts>` and `skython_t42bind_<ts>` (`new_save_name`, count 0 first, polled to 1 after), and loads only those and `s4test`. Never `save` over anything. All three go on the owner's removal list. End games with `tools/box/box_stop.sh`.
>
> **Record only for the carry-forward:** the PackHandle/GetAlias measurement changes no behaviour. R45 stands whatever it shows: aliases are never bound on demand, alias arguments still bind only an object already bound to the alias.

**Files:**
- Modify: `plugin/src/PapyrusValues.cpp` (`values: <target> bound before call: <bool>`, dev only)
- Create (if Task 38 did not): `plugin/config/variants/trace-timing.ini`
- Create: `livetest/test_m2_lifecycle.py`
- Create: `livetest/results/binding_persistence.json` (written by the test)
- Modify: `livetest/results/new_saves.txt` (appended by `new_save_name`), `spikes/RESULTS.md` (append)

**Interfaces:**
- Consumes: Task 33 `Config::trace_timing` (`settings::Get().trace_timing`); Task 36 `Game.on`, `Game.on_game_loaded`, `Game.save`, `Game.load`, `Event.suppressed`, `Event.epoch`; Task 39 `game.state` diagnostics `events_suppressed_total`; Task 40 events `menu_open`, `cell_attach`, `activate`, `game_loaded`, and `livetest/support.py` `PLAYER`, `translate_player`; Task 41 `livetest/support.py` `new_save_name(tag)`, `wait_save_file(name)`, `load_save(game, name)`, `box_save_count`; v1 `pv::ResolveTarget` (`PapyrusValues.cpp`), `values.Form`/`Alias`, `errors.StaleReference`/`NotFound`/`TypeMismatch`, `livetest/s4test.env` (`SPY_S4_REF`, `SPY_PLAIN_QUEST`, `SPY_PLAIN_ALIAS`), `support.log_text`, `support.spy_env`.
- Produces: the live proof of spec 8's save/load lifecycle on throwaway saves (Review Focus 3 and 5); the dev log line `values: <target> bound before call: <true|false>` (once per target per epoch, only with `trace_timing = true`; Task 44 and later probes may read it); `plugin/config/variants/trace-timing.ini` (the dev ini with `trace_timing = true`; Task 44 uses it); `livetest/results/binding_persistence.json`; RESULTS entries for the owner question from Task 29.

- [ ] **Step 1: The dev-only binding log in `ResolveTarget`**

In `plugin/src/PapyrusValues.cpp`, add `#include "Settings.h"` and `#include <set>` and `#include <utility>`, and in the anonymous namespace:
```cpp
// Task 42 (owner question from Task 29): did a form or alias already have a bound script object when a call reached it?
// Dev only (trace_timing); once per target per epoch, so a load shows the state again; at most 4096 targets per process.
void NoteBinding(std::uint64_t key, const std::string& desc, bool boundBefore)
{
    if (!settings::Get().trace_timing) return;
    static std::mutex* mx = new std::mutex;
    static auto* seen = new std::set<std::pair<std::uint64_t, std::uint32_t>>;
    {
        std::lock_guard lk(*mx);
        if (seen->size() >= 4096 || !seen->insert({ key, life::Epoch() }).second) return;
    }
    log::Info(std::format("values: {} bound before call: {}", desc, boundBefore ? "true" : "false"));
}
```
In `ResolveTarget`, replace
```cpp
    if (FindBound(handle, script, bound)) return {bound, desc};
```
with
```cpp
    const bool boundBefore = FindBound(handle, script, bound);
    NoteBinding(static_cast<std::uint64_t>(handle), desc, boundBefore);
    if (boundBefore) return {bound, desc};
```
(The handle is unique per form and per alias, so it is the key.) Nothing else changes: the alias branch still throws `type_mismatch` when nothing is bound (R45), the cold-form branch still binds with `PackHandle`.

- [ ] **Step 2: The trace-timing variant ini**

If `plugin/config/variants/trace-timing.ini` does not exist (Task 38 may have made it), create it from the dev ini and check it:
```bash
test -f plugin/config/variants/trace-timing.ini || sed 's/^trace_timing = false$/trace_timing = true/' plugin/config/Skython.dev.ini > plugin/config/variants/trace-timing.ini
grep -c '^trace_timing = true$' plugin/config/variants/trace-timing.ini     # 1
diff plugin/config/Skython.dev.ini plugin/config/variants/trace-timing.ini  # exactly the trace_timing line differs
```

- [ ] **Step 3: The live tests**

Create `livetest/test_m2_lifecycle.py`. The tests run in file order and share saves through module state; each starts by loading a known save, so a failure in one does not leave the next on an unknown save.
```python
"""Spec 8 save/load lifecycle on new throwaway saves (Task 42), Review Focus 3 and 5, and the PackHandle/GetAlias
save-persistence measurement (record only, R45 stands). Loads only s4test and the saves this module makes."""
import json
import queue
import time

import pytest

from skython import errors
from skython.values import Alias, Form
from support import (PLAYER, REPO, load_save, log_text, new_save_name, spy_env, translate_player, wait_save_file)

IRON_SWORD = "Skyrim.esm|0x012EB7"
GLASS_BOW = "Skyrim.esm|0x0139A5"
STEEL_WAR_AXE = "Skyrim.esm|0x013983"   # a cold base form: nothing in Tasks 27-41 touches it
STATE: dict = {}


def place(game, base_key: str, persist: bool) -> Form:
    return game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, persist, False)


def save_new(game, tag: str) -> str:
    name = new_save_name(tag)
    assert game.save(name) == name
    wait_save_file(name)
    return name


def take(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def test_references_and_subscriptions_across_a_save_and_load(game):
    st0 = load_save(game, "s4test")
    (npc_hex,) = spy_env("SPY_S4_REF")
    npc = Form(form_id=int(npc_hex, 16))
    item = place(game, IRON_SWORD, persist=True)                          # abForcePersist = true
    loose = place(game, IRON_SWORD, persist=False)
    acts: queue.Queue = queue.Queue()
    with game.on("activate", {"target": [loose]}, acts.put):
        game.call_method(loose, "ObjectReference", "Activate", PLAYER, False)  # picks it up: the ref goes away
        old_event = take(acts, 10.0)
    assert old_event is not None and old_event.epoch == st0["epoch"]
    name_a = save_new(game, "t42a")
    STATE.update(a=name_a, item=item)

    loading, loaded, attach = queue.Queue(), queue.Queue(), queue.Queue()
    subs = [game.on("menu_open", {"menu": ["Loading Menu"]}, loading.put), game.on_game_loaded(loaded.put),
            game.on("cell_attach", None, attach.put, max_queue=4096)]
    try:
        suppressed_before = game.state()["diagnostics"]["events_suppressed_total"]
        st1 = load_save(game, name_a)
        assert st1["epoch"] == st0["epoch"] + 1
        # Review Focus 5: subscriptions survive the load and later frames carry the new epoch.
        gl = take(loaded, 30.0)
        assert gl is not None and gl.epoch == st1["epoch"] and gl.data["new_game"] is False
        lm = take(loading, 5.0)
        assert lm is not None and lm.data["menu"] == "Loading Menu" and lm.suppressed == 0  # deliver_while_loading
        # Review Focus 3: cell_attach raised during the load was not queued; it is counted and reported on the next
        # delivered cell_attach. Glide 1.5 cells (no Loading Menu) if nothing attached by itself.
        rose = game.state()["diagnostics"]["events_suppressed_total"] - suppressed_before
        first = take(attach, 5.0)
        if first is None:
            x, y, z = translate_player(game, 6144.0, 0.0, 600.0)
            first = take(attach, 10.0)
        print(f"load: epoch {st0['epoch']}->{st1['epoch']} suppressed_rise={rose} "
              f"first_cell_attach suppressed={first.suppressed if first else None} epoch={first.epoch if first else None}")
        assert rose > 0, "no event was suppressed during the load (cell_attach should have been)"
        assert first is not None and first.suppressed > 0 and first.epoch == st1["epoch"]
    finally:
        for s in subs:
            s.close()
    # The persistent runtime item (fingerprint matches) and the vanilla NPC still work.
    assert game.call_method(item, "ObjectReference", "GetBaseObject").form_key == IRON_SWORD
    assert isinstance(game.call_method(npc, "Form", "GetName"), str)
    # A reference from an old-epoch event: works, or raises StaleReference / NotFound; never a crash.
    try:
        base = game.call_method(old_event.data["target"], "ObjectReference", "GetBaseObject")
        outcome = f"works ({base.form_key})"
    except (errors.StaleReference, errors.NotFound) as e:
        outcome = type(e).__name__
    print(f"old-epoch event reference after the load: {outcome}")
    STATE["old_event_ref"] = outcome


def test_runtime_reference_from_another_save_is_stale(game):
    assert "a" in STATE, "needs the save from the previous test"
    load_save(game, "s4test")
    bow = place(game, GLASS_BOW, persist=True)
    name_b = save_new(game, "t42b")
    same_id = bow.form_id == STATE["item"].form_id
    print(f"runtime ids: iron sword in A {STATE['item'].form_id:#x}, glass bow in B {bow.form_id:#x}, same={same_id}")
    load_save(game, STATE["a"])
    with pytest.raises((errors.StaleReference, errors.NotFound)) as e_in_a:
        game.call_method(bow, "ObjectReference", "GetBaseObject")
    load_save(game, name_b)
    with pytest.raises((errors.StaleReference, errors.NotFound)) as e_in_b:
        game.call_method(STATE["item"], "ObjectReference", "GetBaseObject")
    if same_id:  # the id names a different object in the other save: exactly the stale case
        assert e_in_a.type is errors.StaleReference and e_in_b.type is errors.StaleReference
    print(f"bow from B used in A: {e_in_a.type.__name__}; sword from A used in B: {e_in_b.type.__name__}")


def _owning_quest(game, alias: Alias) -> str:
    try:
        q = game.call_method(alias, "Alias", "GetOwningQuest")
        return f"works ({q.form_id:#x})"
    except errors.TypeMismatch:
        return "type_mismatch"


def _bound_lines(desc_part: str) -> list[str]:
    return [l for l in log_text().splitlines() if "bound before call:" in l and desc_part in l]


def test_packhandle_and_getalias_binding_across_a_save(game):
    """Record only (owner question from Task 29; R45 stands either way)."""
    quest_hex, alias_s = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")
    load_save(game, "s4test")
    quest = game.resolve(form_id=int(quest_hex, 16))
    alias = game.alias(quest, int(alias_s))
    axe = game.form(STEEL_WAR_AXE)
    if _owning_quest(game, alias) != "type_mismatch":
        pytest.fail("the plain alias is already bound on a fresh s4test load: STOP and ask the owner")
    assert isinstance(game.call_method(quest, "Quest", "GetAlias", int(alias_s)), Alias)  # binds an `Alias` object (S7g)
    before_save = _owning_quest(game, alias)
    assert before_save.startswith("works"), before_save
    assert isinstance(game.call_method(axe, "Form", "GetName"), str)       # cold: PackHandle binds it
    name_c = save_new(game, "t42bind")
    load_save(game, name_c)
    after_load = _owning_quest(game, alias)                                # first call after the load
    game.call_method(axe, "Form", "GetName")
    axe_lines = _bound_lines("0x013983") or _bound_lines("013983")
    result = {"save": name_c, "alias": {"quest": quest_hex, "alias_id": int(alias_s), "before_save": before_save,
                                        "after_load": after_load},
              "cold_form": {"form": STEEL_WAR_AXE, "log": axe_lines},
              "old_epoch_event_reference": STATE.get("old_event_ref"), "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    out = REPO / "livetest" / "results" / "binding_persistence.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    # The cold form must show "false" before the save and one more line after the load (a new epoch).
    assert len(axe_lines) >= 2 and axe_lines[0].endswith("bound before call: false"), axe_lines
```
`SKYTHON_LIVE= uv run --project python pytest livetest/test_m2_lifecycle.py -q` → `3 skipped`.

The `bound before call` line names the target the way `pv::Describe` prints it (Task 28: the form key or the decimal id); check the first box run's log for the exact spelling of the axe and adjust only the two `_bound_lines(...)` arguments if neither matches.

- [ ] **Step 4: Build**

```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
```
Expected: both succeed, 0 warnings.

- [ ] **Step 5: Run in the game (trace-timing ini)**

```bash
source tools/box/box_env.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/trace-timing.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600 || echo "STOP: Python did not report in 10 minutes; ask the owner"
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600 || echo "STOP: no main menu in 10 minutes; ask the owner"
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "events: game_loaded epoch 1" 300
source livetest/s4test.env
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_lifecycle.py -q -s -rs
tail -n 3 livetest/results/new_saves.txt
for n in $(tail -n 3 livetest/results/new_saves.txt); do echo "$n $(tools/box/box_saves.sh count "$n")"; done   # each 1
tools/box/box_files.sh pull Skython.log >/dev/null
grep "bound before call\|saveload: \|events: game_loaded" "$SKSE_LOG_DIR/Skython.log"
grep '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini for the next task
```
Expected: 3 passed; three new names, each count 1; the printed lines (keep them all): the load line (`suppressed_rise>0`, first `cell_attach` with `suppressed>0` and the new epoch), the old-epoch reference outcome, the runtime-id line and the two stale outcomes, and the binding JSON; `no unexpected errors` (the `type_mismatch` of the alias probe is an ordinary error reply, not a log error). A crash anywhere: STOP.

- [ ] **Step 6: Record**

Append to `spikes/RESULTS.md`: `**Task 42, save/load lifecycle and binding persistence (box, Windows-native, <date/time>, Skyrim 1.6.1170.0, s4test plus three new saves; <n> launches; SKSE plugins: <list>):**` with:
- Review Focus 5: subscriptions survived; `game_loaded` and later frames carried epoch <n>; persistent `PlaceAtMe` item valid after the load; vanilla NPC valid; the old-epoch event reference's outcome; the runtime-id line and both stale outcomes (`StaleReference` when the ids matched).
- Review Focus 3: `events_suppressed_total` rise, the first delivered `cell_attach`'s `suppressed` and epoch, the Loading Menu `menu_open` delivered unsuppressed.
- **Owner question (Task 29), PackHandle/GetAlias save persistence:** the alias line verbatim (`before_save`, `after_load`: `works` means the `Alias` object that `Quest.GetAlias` bound was in the save and came back; `type_mismatch` means it did not) and every `values: ... bound before call:` line for the axe (the line after the load says whether the PackHandle binding came back). Close with "No behaviour change; R45 stands."
- **Owner removal list (Task 42):** the three `skython_t42*` names, none removed without itemised sign-off.

- [ ] **Step 7: Commit**

```bash
git add plugin/src/PapyrusValues.cpp plugin/config/variants/trace-timing.ini livetest/test_m2_lifecycle.py \
  livetest/results/binding_persistence.json livetest/results/new_saves.txt spikes/RESULTS.md
git commit -m "test(live): save/load lifecycle on throwaway saves and the alias binding persistence measurement"
```
