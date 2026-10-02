# Task 51: Property happy paths, including the parent-variable offset

Carry-forward from v1 Task 30: the property route (ruling R57: reads through the VM's `GetVariableValue(obj, absIndex)`, writes to the backing variable; absolute index = the parent classes' variable counts + `autoVarIndex`) was proven only on scripts that extend native classes, whose parents have no script variables, so the parent offset was 0 either way (RESULTS, Task 30: "Not discriminated live: the parent-variable offset ... M1.5's test ESP pins it"). `SkythonTestQuest extends SkythonTestQuestBase`, and the parent has its own variables: a write that ignores the offset lands in the parent's slot, which this task detects from the Papyrus side.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Properties: R57/R58 stay), **Contracts** (Papyrus) and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 49 (base save `skythonbase`) and Task 50 (`livetest/scripts/_testesp.py`, `livetest/scripts` on `sys.path`) are done.
>
> **Runs on:** Linux (the test file), then the Windows box + game through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** a write to `Counter` changes `GetBaseCounter()` or a write to `BaseCounter` changes `GetCounter()` (the absolute index is wrong: that is a plugin bug to report with the log lines, not a test to adjust); a String or Actor write is accepted (R58 broken); `GetCounter`/`GetBaseCounter` are missing or not plain `Return` getters in `testesp/papyrus/*.psc`; `SkythonTestAlias.Tag` is not an Int auto property; a live test crashes the game. No new write types in this task: R57/R58 limits stay.

**Files:**
- Create: `livetest/test_m15_properties.py`
- Modify: `spikes/RESULTS.md` (append a Task 51 entry)

**Interfaces:**
- Consumes:
  - Task 48 (index Contracts, Papyrus): `SkythonTestQuestBase` (extends `Quest`; auto properties `BaseCounter` Int, `BaseLabel` String); `SkythonTestQuest` (extends it; auto properties `Counter` Int, `Ratio` Float, `Flag` Bool, `Label` String, `Target` Actor; `Int GetCounter()` returns `Counter`, `Int GetBaseCounter()` returns `BaseCounter`); `SkythonTestAlias` (extends `ReferenceAlias`; auto property `Tag` Int). Initial (VMAD) values are not fixed by the plan: the tests record them and never assume them.
  - Task 50: `_testesp.discover(game) -> Esp`, `ensure_running(game, quest)`, constants `QUEST_SCRIPT`, `BASE_SCRIPT`, `ALIAS_SCRIPT`.
  - Task 49: `fresh_save`. v1 Task 30: `Game.get_property(obj, script, prop)`, `Game.set_property(obj, script, prop, value)`; errors `Unsupported` with the message `v1 writes only Int, Float and Bool`.
- Produces: `livetest/test_m15_properties.py`; the RESULTS entry that closes Task 30's "parent-variable offset not discriminated live".

- [ ] **Step 1: Check the getters this test trusts**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -n -i -A3 "Function GetCounter\|Function GetBaseCounter" testesp/papyrus/SkythonTestQuest.psc
grep -n -i "Property" testesp/papyrus/SkythonTestQuestBase.psc testesp/papyrus/SkythonTestQuest.psc testesp/papyrus/SkythonTestAlias.psc
```
Expected: `GetCounter` is `Return Counter` and `GetBaseCounter` is `Return BaseCounter` (declared in `SkythonTestQuest`); `BaseCounter`/`BaseLabel` are `Auto` in the base; `Counter`, `Ratio`, `Flag`, `Label`, `Target` are `Auto` in the child; `Tag` is `Int Property Tag Auto` in the alias script. Otherwise STOP.

- [ ] **Step 2: Write the live tests**

Create `livetest/test_m15_properties.py`:
```python
"""Property happy paths on SkythonTest.esp (Task 51): reads of every auto property type, Int/Float/Bool writes seen
by Papyrus, and the parent-variable offset (SkythonTestQuest extends SkythonTestQuestBase, which has its own
variables). Needs skythonbase (Task 49), loaded by fresh_save. Every write is restored; skythonbase is never saved.
Run with -s to see the values read."""
import pytest

import _testesp as t
from skython import errors
from skython.values import Form

pytestmark = pytest.mark.usefixtures("fresh_save")
CHILD_TYPES = {"Counter": int, "Ratio": float, "Flag": bool, "Label": str}
BASE_TYPES = {"BaseCounter": int, "BaseLabel": str}


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-props")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    h = t.discover(mgame)
    t.ensure_running(mgame, h.quest)
    return h


def counters(game, quest):
    """(Counter, BaseCounter) as Papyrus bytecode sees them: the getters read the variables by Papyrus's own map."""
    return (game.call_method(quest, t.QUEST_SCRIPT, "GetCounter"),
            game.call_method(quest, t.QUEST_SCRIPT, "GetBaseCounter"))


def test_reads_every_child_property_with_its_type(game, esp):
    for prop, kind in CHILD_TYPES.items():
        v = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        print(f"\n{t.QUEST_SCRIPT}.{prop} -> {v!r}")
        assert type(v) is kind, (prop, v)
    target = game.get_property(esp.quest, t.QUEST_SCRIPT, "Target")
    print(f"\n{t.QUEST_SCRIPT}.Target -> {target!r}")
    assert target is None or (isinstance(target, Form) and target.type == "Actor"), target


def test_reads_inherited_properties_through_child_and_parent_names(game, esp):
    for prop, kind in BASE_TYPES.items():
        via_child = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        via_base = game.get_property(esp.quest, t.BASE_SCRIPT, prop)
        print(f"\n{prop}: via {t.QUEST_SCRIPT} {via_child!r}, via {t.BASE_SCRIPT} {via_base!r}")
        assert type(via_child) is kind and via_child == via_base, (prop, via_child, via_base)


def test_int_read_agrees_with_papyrus(game, esp):
    counter, base = counters(game, esp.quest)
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Counter") == counter
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter") == base


def test_child_int_write_lands_in_the_child_slot(game, esp):
    """The discriminating case: without the parent's variable count in the index, this write would land in one of
    SkythonTestQuestBase's slots, and GetBaseCounter (or a type check on BaseLabel) would show it."""
    counter, base = counters(game, esp.quest)
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", counter + 5)
        assert counters(game, esp.quest) == (counter + 5, base)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseLabel") == game.get_property(
            esp.quest, t.QUEST_SCRIPT, "BaseLabel")  # the parent's String slot is untouched and still a String
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", counter)
    assert counters(game, esp.quest) == (counter, base)


def test_inherited_int_write_lands_in_the_parent_slot(game, esp):
    counter, base = counters(game, esp.quest)
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", base + 7)
        assert counters(game, esp.quest) == (counter, base + 7)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseCounter") == base + 7
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", base)
    assert counters(game, esp.quest) == (counter, base)


def test_float_and_bool_writes_read_back(game, esp):
    ratio = game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio")
    flag = game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag")
    counter, base = counters(game, esp.quest)
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", 0.25)    # exact in binary: no rounding noise
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", not flag)
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == 0.25
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is (not flag)
        assert counters(game, esp.quest) == (counter, base)             # neighbouring Int slots untouched
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", ratio)
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", flag)
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == ratio
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is flag


def test_string_and_object_writes_stay_unsupported(game, esp):
    """R58: v1 writes only Int, Float and Bool. Writing the value already held cannot be a type mismatch."""
    label = game.get_property(esp.quest, t.QUEST_SCRIPT, "Label")
    target = game.get_property(esp.quest, t.QUEST_SCRIPT, "Target")
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Label", label)
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Target", target if target is not None else esp.npc)
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.BASE_SCRIPT, "BaseLabel", "x")
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Label") == label


def test_alias_script_int_property_read_and_write(game, esp):
    alias = esp.ref_aliases[0]
    tag = game.get_property(alias, t.ALIAS_SCRIPT, "Tag")
    print(f"\n{t.ALIAS_SCRIPT}.Tag on alias {alias.alias_id} -> {tag!r}")
    assert type(tag) is int, tag
    other = esp.ref_aliases[1]
    other_tag = game.get_property(other, t.ALIAS_SCRIPT, "Tag")
    try:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", tag + 11)
        assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == tag + 11
        assert game.get_property(other, t.ALIAS_SCRIPT, "Tag") == other_tag  # each alias has its own object
    finally:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", tag)
    assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == tag
```

Collect without a game:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m15_properties.py -q -rs 2>&1 | tail -3
```
Expected: `8 skipped` (reason `live test: set SKYTHON_LIVE=1 with the game running`).

- [ ] **Step 3: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m15_properties.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "ops: property|values:" out/box-skse/Skython.log | head -20
tools/box/box_stop.sh
tools/box/box_plugins.sh show
```
Expected: `8 passed`, no skips; the `-s` output lists every property's initial value (record them); the `ops: property` lines (if the build still logs them) show the slot indexes used; `box_stop.sh` reports the game gone; `show` prints the backup with no `SkythonTest.esp` line. No save written.

If `test_child_int_write_lands_in_the_child_slot` fails with `GetBaseCounter` changed: STOP and report the two values and the `ops:` log lines (the index formula is wrong at this runtime).

- [ ] **Step 4: Record in RESULTS**

Append to "Plugin findings" in `spikes/RESULTS.md`, filled from Step 3 (no bracket left):
```markdown
- **Task 51, property happy paths and the parent-variable offset (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase, never saved; 1 launch, SKSE plugins: <list>):** `test_m15_properties.py` 8 passed. Initial values: `Counter` <v>, `Ratio` <v>, `Flag` <v>, `Label` <v>, `Target` <v>, `BaseCounter` <v>, `BaseLabel` <v>, alias `Tag` <v>. **Parent offset discriminated (closes Task 30's open point):** writing `SkythonTestQuest.Counter` changed `GetCounter()` only and writing the inherited `BaseCounter` changed `GetBaseCounter()` only, so the absolute index includes `SkythonTestQuestBase`'s variable count; inherited properties read the same through the child and parent script names. Float (0.25) and Bool writes read back; String and Actor writes `unsupported` (R58 unchanged); alias script `Tag` read/write is per alias.
```
Check:
```bash
grep -n "Task 51, property happy paths" spikes/RESULTS.md && ! grep -n "Task 51, property happy paths.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 5: Commit**

```bash
git add livetest/test_m15_properties.py spikes/RESULTS.md
git commit -m "test(m1.5): property happy paths and the parent-variable offset on the test ESP"
```
