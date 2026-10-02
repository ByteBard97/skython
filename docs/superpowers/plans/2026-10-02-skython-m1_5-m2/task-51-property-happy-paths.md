# Task 51: Property happy paths, including the parent-variable offset

Carry-forward from v1 Task 30: the property route (ruling R57: reads through the VM's `GetVariableValue(obj, absIndex)`, writes to the backing variable; absolute index = the parent classes' variable counts + `autoVarIndex`) was proven only on scripts that extend native classes, whose parents have no script variables, so the parent offset was 0 either way (RESULTS, Task 30: "Not discriminated live: the parent-variable offset ... M1.5's test ESP pins it"). `SkythonTestQuest extends SkythonTestQuestBase`, and the parent has its own variables: a write that ignores the offset lands in the parent's slot, which this task detects from the Papyrus side.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Properties: R57/R58 stay), **Contracts** (Test ESP, Live tests) and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 49 (base save `skythonbase`) and Task 50 (`livetest/scripts/_testesp.py`, `livetest/scripts` on `sys.path`) are done.
>
> **Runs on:** Linux (the test file), then the Windows box + game through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** a write to `Counter` changes `GetBaseCounter()` or a write to `BaseCounter` changes `GetCounter()` (the absolute index is wrong: that is a plugin bug to report with the log lines, not a test to adjust); a String or Actor write is accepted (R58 broken); an initial value read differs from the index's **Test ESP** contract (a Task 48 build defect, or `skythonbase` was saved after a write: report, never adapt the expected value); a live test crashes the game; `plugins.txt` differs from its backup in any line other than ours. No new write types in this task: R57/R58 limits stay.

**Files:**
- Create: `livetest/test_m15_properties.py`
- Modify: `spikes/RESULTS.md` (append a Task 51 entry)

**Interfaces:**
- Consumes:
  - Task 48 (index Contracts, **Test ESP**): `SkythonTestQuestBase` (extends `Quest`; auto properties `BaseCounter` Int = `3`, `BaseLabel` String = `"base label"`); `SkythonTestQuest` (extends it; auto properties `Counter` Int = `7`, `Ratio` Float = `0.5`, `Flag` Bool = `True`, `Label` String = `"initial label"`, `Target` Actor = `SkythonTestNPC` `SkythonTest.esp|0x000804` (VMAD); full property `Computed`; `Int GetCounter()` returns `Counter`, `Int GetBaseCounter()` returns `BaseCounter`); `SkythonTestAlias` on aliases 0-2 (extends `ReferenceAlias`; `Tag` Int = `5`); `SkythonTestLocationAlias` on alias 3 (extends `LocationAlias`; `Tag` Int = `6`). `skythonbase` holds these initial values (Task 49 saves before any write) and `fresh_save` reloads it for this module, so the tests assert them exactly.
  - Task 50: `_testesp.discover(game) -> Esp` (`quest`, `ref_aliases`, `location_alias`, `npc`), `ensure_running(game, quest)`, constants `QUEST_SCRIPT`, `BASE_SCRIPT`, `ALIAS_SCRIPT`, `LOCATION_ALIAS_SCRIPT`, `NPC_KEY`, `INITIAL`, `TARGET_ALIAS`, `ITEM_ALIAS`, `same_key(form, key)`.
  - Task 49: `fresh_save`. v1 Task 30: `Game.get_property(obj, script, prop)`, `Game.set_property(obj, script, prop, value)`; errors `Unsupported` with the message `v1 writes only Int, Float and Bool`.
- Produces: `livetest/test_m15_properties.py`; the RESULTS entry that closes Task 30's "parent-variable offset not discriminated live".

- [ ] **Step 1: Write the live tests**

Create `livetest/test_m15_properties.py`:
```python
"""Property happy paths on SkythonTest.esp (Task 51): exact reads of every auto property type against the ESP's fixed
initial values, Int/Float/Bool writes seen by Papyrus, and the parent-variable offset (SkythonTestQuest extends
SkythonTestQuestBase, which has its own variables). Needs skythonbase (Task 49), loaded by fresh_save, which holds
the initial values. Every write is restored; skythonbase is never saved. Run with -s to see the values read."""
import pytest

import _testesp as t
from skython import errors
from skython.values import Form

pytestmark = pytest.mark.usefixtures("fresh_save")
CHILD = t.INITIAL[t.QUEST_SCRIPT]       # Counter 7, Ratio 0.5, Flag True, Label "initial label", Target NPC_KEY
BASE = t.INITIAL[t.BASE_SCRIPT]         # BaseCounter 3, BaseLabel "base label"


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


def test_reads_every_child_property_exactly(game, esp):
    for prop in ("Counter", "Ratio", "Flag", "Label"):
        v = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        print(f"\n{t.QUEST_SCRIPT}.{prop} -> {v!r}")
        assert type(v) is type(CHILD[prop]) and v == CHILD[prop], (prop, v)
    target = game.get_property(esp.quest, t.QUEST_SCRIPT, "Target")
    print(f"\n{t.QUEST_SCRIPT}.Target -> {target!r}")
    assert isinstance(target, Form) and target.type == "Actor" and t.same_key(target, CHILD["Target"]), target
    assert target.form_id == esp.npc.form_id


def test_reads_inherited_properties_exactly_through_child_and_parent_names(game, esp):
    for prop, want in BASE.items():
        via_child = game.get_property(esp.quest, t.QUEST_SCRIPT, prop)
        via_base = game.get_property(esp.quest, t.BASE_SCRIPT, prop)
        print(f"\n{prop}: via {t.QUEST_SCRIPT} {via_child!r}, via {t.BASE_SCRIPT} {via_base!r}")
        assert type(via_child) is type(want) and via_child == want and via_base == want, (prop, via_child, via_base)


def test_int_reads_agree_with_papyrus(game, esp):
    assert counters(game, esp.quest) == (CHILD["Counter"], BASE["BaseCounter"])  # (7, 3)


def test_child_int_write_lands_in_the_child_slot(game, esp):
    """The discriminating case: without the parent's variable count in the index, this write would land in one of
    SkythonTestQuestBase's slots, and GetBaseCounter (or BaseLabel) would show it."""
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", 12)
        assert counters(game, esp.quest) == (12, 3)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseLabel") == BASE["BaseLabel"]  # parent slot untouched
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Counter", CHILD["Counter"])
    assert counters(game, esp.quest) == (7, 3)


def test_inherited_int_write_lands_in_the_parent_slot(game, esp):
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", 10)
        assert counters(game, esp.quest) == (7, 10)
        assert game.get_property(esp.quest, t.BASE_SCRIPT, "BaseCounter") == 10
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "BaseCounter", BASE["BaseCounter"])
    assert counters(game, esp.quest) == (7, 3)


def test_float_and_bool_writes_read_back(game, esp):
    try:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", 0.25)    # exact in binary: no rounding noise
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", False)
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == 0.25
        assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is False
        assert counters(game, esp.quest) == (7, 3)                     # neighbouring Int slots untouched
    finally:
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Ratio", CHILD["Ratio"])
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Flag", CHILD["Flag"])
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Ratio") == 0.5
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Flag") is True


def test_string_and_object_writes_stay_unsupported(game, esp):
    """R58: v1 writes only Int, Float and Bool. Writing the value already held cannot be a type mismatch."""
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Label", CHILD["Label"])
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Target", esp.npc)
    with pytest.raises(errors.Unsupported, match="v1 writes only Int, Float and Bool"):
        game.set_property(esp.quest, t.BASE_SCRIPT, "BaseLabel", "x")
    assert game.get_property(esp.quest, t.QUEST_SCRIPT, "Label") == "initial label"


def test_full_property_stays_unsupported(game, esp):
    # Computed would read 14 (Counter * 2) through Papyrus, but v1 refuses full properties before anything runs.
    with pytest.raises(errors.Unsupported, match="full property"):
        game.get_property(esp.quest, t.QUEST_SCRIPT, "Computed")


def test_alias_script_int_property_read_and_write(game, esp):
    alias, other = esp.ref_aliases[t.TARGET_ALIAS], esp.ref_aliases[t.ITEM_ALIAS]
    assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == t.INITIAL[t.ALIAS_SCRIPT]["Tag"]  # 5
    assert game.get_property(other, t.ALIAS_SCRIPT, "Tag") == 5
    try:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", 16)
        assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == 16
        assert game.get_property(other, t.ALIAS_SCRIPT, "Tag") == 5     # each alias has its own script object
    finally:
        game.set_property(alias, t.ALIAS_SCRIPT, "Tag", 5)
    assert game.get_property(alias, t.ALIAS_SCRIPT, "Tag") == 5


def test_location_alias_script_int_property_read_and_write(game, esp):
    la = esp.location_alias
    assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == t.INITIAL[t.LOCATION_ALIAS_SCRIPT]["Tag"]  # 6
    try:
        game.set_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag", 17)
        assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == 17
    finally:
        game.set_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag", 6)
    assert game.get_property(la, t.LOCATION_ALIAS_SCRIPT, "Tag") == 6
```

Collect without a game:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m15_properties.py -q -rs 2>&1 | tail -3
```
Expected: `10 skipped` (reason `live test: set SKYTHON_LIVE=1 with the game running`).

- [ ] **Step 2: Run on the box**

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
Expected: `10 passed`, no skips; the `-s` output lists every property's value read (equal to the index's initial values); the `ops: property` lines (if the build still logs them) show the slot indexes used; `box_stop.sh` reports the game gone; `show` prints the backup with no `SkythonTest.esp` line. No save written.

If `test_child_int_write_lands_in_the_child_slot` fails with `GetBaseCounter` changed: STOP and report the two values and the `ops:` log lines (the index formula is wrong at this runtime).

- [ ] **Step 3: Record in RESULTS**

Append to "Plugin findings" in `spikes/RESULTS.md`, filled from Step 2 (no bracket left):
```markdown
- **Task 51, property happy paths and the parent-variable offset (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase, never saved; 1 launch, SKSE plugins: <list>):** `test_m15_properties.py` 10 passed. Every initial value read exactly as the ESP fixes it: `Counter` 7, `Ratio` 0.5, `Flag` True, `Label` "initial label", `Target` SkythonTestNPC, `BaseCounter` 3, `BaseLabel` "base label", `SkythonTestAlias.Tag` 5, `SkythonTestLocationAlias.Tag` 6. **Parent offset discriminated (closes Task 30's open point):** writing `SkythonTestQuest.Counter` changed `GetCounter()` only and writing the inherited `BaseCounter` changed `GetBaseCounter()` only, so the absolute index includes `SkythonTestQuestBase`'s variable count; inherited properties read the same through the child and parent script names. Float (0.25) and Bool writes read back; String and Actor writes and the full property `Computed` `unsupported` (R57/R58 unchanged); alias script `Tag` read/write is per alias, on reference and location aliases.
```
Check:
```bash
grep -n "Task 51, property happy paths" spikes/RESULTS.md && ! grep -n "Task 51, property happy paths.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 4: Commit**

```bash
git add livetest/test_m15_properties.py spikes/RESULTS.md
git commit -m "test(m1.5): property happy paths and the parent-variable offset on the test ESP"
```
