# Task 50: Own-script live tests: bytecode member call, alias argument, LocationAlias ops, full property refused

Carry-forward from the v1 plan: alias **arguments** had no live test (Task 29: no vanilla function takes an alias parameter; the `BindAlias` path, ruling R45), and LocationAlias operations were never run (spike S4: `LocationAlias NOT_RUN (50 empty), verified in M1.5`). The test ESP's own scripts make both testable.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Contracts** (Papyrus, Live tests) and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 49 is done: the base save `skythonbase` exists on the box (in `SkythonTestCell`), `SkythonTest.esp` and the six decision-7 `.pex` files are deployed, and `fresh_save` prefers `skythonbase`. **Decision-8 names:** `SCENARIO_FUNCTION` and `CHANNEL` in `_testesp.py` are taken from Task 48's RESULTS entry (its line "decision-8 names used: scenario function `...`, channel `...`"), never from the defaults below when that line names others; no such line in `spikes/RESULTS.md` → STOP (Task 48 is not done).
>
> **Cold forms (Task 42):** if this task needs a form with no bound script object ("a cold form"), verify it on the save in use through the dev trace line `values: <form> bound before call: false` (`trace_timing`), never assume it from a form being obscure: on `s4test` the Steel War Axe and Iron Dagger were already bound, the Steel Mace (`0x013988`) and Iron Mace (`0x013982`) were cold; `skythonbase` may differ.
>
> **Runs on:** Linux (the test files), then the Windows box + game through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** `discover()` finds any value that differs from the index's **Test ESP** contract (alias ids/types/names, the NPC, objective 10): that is a Task 48 defect, never something this task adapts to; `Quest.Start` on `SkythonTestQuest` returns False; any LocationAlias call answers `type_mismatch` "no ... script object bound" (alias 3 must carry `SkythonTestLocationAlias`: a Task 48 defect); `LocationAlias.Clear` leaves the location filled; `Skyrim.esm|0x018A56` does not resolve to a `Location`; a live test crashes the game; `plugins.txt` differs from its backup in any line other than ours.

**Files:**
- Create: `livetest/scripts/_testesp.py` (shared ESP constants and helpers; the leading `_` keeps `skython._host` from loading it as a script, and `box_deploy.sh --scripts livetest/scripts` still deploys it so the in-process scripts of Tasks 53, 55 and 56 can import it)
- Modify: `livetest/conftest.py` (append: `livetest/scripts` on `sys.path`)
- Create: `livetest/test_m15_own_script.py`
- Modify: `spikes/RESULTS.md` (append a Task 50 entry)

**Interfaces:**
- Consumes:
  - Task 48 (index Contracts, **Test ESP**): quest `SkythonTest.esp|0x000800` with script `SkythonTestQuest` (extends `SkythonTestQuestBase`); aliases 0 `SkythonTarget`, 1 `SkythonItem`, 2 `SkythonExtra` (ReferenceAlias + `SkythonTestAlias`), 3 `SkythonPlace` (LocationAlias + `SkythonTestLocationAlias`), all optional; objective 10; actor `SkythonTestNPC` `|0x000804` in cell `SkythonTestCell` `|0x000802`; location `|0x000801`; marker `|0x000805`; initial values (`Counter` 7, `Ratio` 0.5, `Flag` True, `Label` `"initial label"`, `Target` `|0x000804`, `BaseCounter` 3, `BaseLabel` `"base label"`, `SkythonTestAlias.Tag` 5, `SkythonTestLocationAlias.Tag` 6); `Int BumpCounter(Int n)` (adds `n` to `Counter`, returns the new `Counter`); `Int GetCounter()`; `String DescribeAlias(ReferenceAlias akAlias)` → `"none"`, `"foreign"`, `"empty"` or `"filled"`; `String DescribeLocationAlias(LocationAlias akAlias)` → the same four answers (`"empty"` when `GetLocation()` is `None`); `Int AliasTag(SkythonTestAlias akAlias)` → the alias script's `Tag`; full property `Computed` (Int, getter only); the RESULTS line "decision-8 names used: scenario function `<name>`, channel `<name>`".
  - Task 49: `skythonbase`, `fresh_save` (module fixture in `livetest/conftest.py`, loads `skythonbase`).
  - Task 36: `Game.wait_for(predicate, timeout, *, interval=0.25)`.
  - v1: `Game.call_method`, `get_property`, `set_property`, `form`, `alias(quest, alias_id, alias_type="ReferenceAlias")`; `skython.errors.TypeMismatch`, `Unsupported`; `support.spy_env`; `livetest/s4test.env` (`SPY_PLAIN_QUEST=0x0010FDD5 SPY_PLAIN_ALIAS=0`: a plain alias in `Skyrim.esm`, save-independent data).
  - Natives (vanilla + SKSE + PO3 sources, checked in the SimpleSkyrim copy and `Scripts.zip`): `Quest.GetNumAliases()`, `Quest.GetNthAlias(int)`, `Quest.Start()`, `Quest.IsRunning()`, `Alias.GetName()`, `ObjectReference.GetParentCell()`, `Cell.GetNumRefs(int formTypeFilter)`, `Cell.GetNthRef(int n, int formTypeFilter)`, `ReferenceAlias.ForceRefTo(ObjectReference)`, `ReferenceAlias.Clear()`, `LocationAlias.ForceLocationTo(Location)`, `LocationAlias.GetLocation()`, `LocationAlias.Clear()`, `PO3_SKSEFunctions.GetAllQuestObjectives(Quest) -> int[]`.
- Produces (index Contracts, Live tests; Tasks 51-56 import these):
  - `livetest/scripts/_testesp.py`: constants `PLUGIN`, `QUEST_KEY`, `LOCATION_KEY`, `CELL_KEY`, `NPC_KEY`, `MARKER_KEY`, `QUEST_SCRIPT`, `BASE_SCRIPT`, `ALIAS_SCRIPT`, `LOCATION_ALIAS_SCRIPT`, `SCENARIO_FUNCTION`, `CHANNEL`, `STAGES`, `TARGET_ALIAS` (0), `ITEM_ALIAS` (1), `EXTRA_ALIAS` (2), `PLACE_ALIAS` (3), `REF_ALIASES` (`(0, 1, 2)`), `ALIAS_NAMES` (`{0: "SkythonTarget", 1: "SkythonItem", 2: "SkythonExtra", 3: "SkythonPlace"}`), `OBJECTIVE` (10), `INITIAL` (script → {property → initial value}), `PLAYER`, `IRON_SWORD`, `WHITERUN_LOCATION`, `FORM_TYPE_ACTOR`; `stage_payload(stage: int) -> str`; `same_key(form, key: str) -> bool` (form-key comparison, case-insensitive, `None`-safe); `@dataclass(frozen=True) class Esp(quest: Form, ref_aliases: tuple[Alias, ...], location_alias: Alias, alias_names: dict[int, str], npc: Form, objectives: tuple[int, ...])` with property `objective -> int` (always `OBJECTIVE`); `quest(game) -> Form`; `npc(game) -> Form`; `location(game) -> Form` (the ESP's `SkythonTestLocation`); `require_test_cell(game) -> Form`; `discover(game) -> Esp` (builds the `Esp` from the fixed values and **asserts** the game agrees: a cross-check, never a source of values); `ensure_running(game, quest: Form) -> None`.
  - `livetest/conftest.py`: `livetest/scripts` is on `sys.path` for every live test (`import _testesp`).
  - `livetest/test_m15_own_script.py` and the RESULTS entry closing S4's LocationAlias `NOT_RUN` and Task 29's alias-argument gap.

- [ ] **Step 1: Shared ESP constants and helpers**

Create `livetest/scripts/_testesp.py`:
```python
"""Shared constants and helpers for SkythonTest.esp, the test plugin (plan Task 48). Development only: never ship it.

Imported by the live tests on Linux (livetest/conftest.py puts livetest/scripts on sys.path) and by the in-process
test scripts inside the game (deployed next to them by `tools/box/box_deploy.sh --scripts livetest/scripts`; the
leading underscore keeps skython._host from loading this module as a script).

Every value below is fixed by the plan index (Contracts, "Test ESP"). discover() reads the same facts back from the
running game and fails when they differ: it is a cross-check of the ESP build, never a source of values.
"""
from dataclasses import dataclass

from skython import errors
from skython.values import Alias, Form

PLUGIN = "SkythonTest.esp"
QUEST_KEY = "SkythonTest.esp|0x000800"
LOCATION_KEY = "SkythonTest.esp|0x000801"
CELL_KEY = "SkythonTest.esp|0x000802"
NPC_KEY = "SkythonTest.esp|0x000804"
MARKER_KEY = "SkythonTest.esp|0x000805"
QUEST_SCRIPT = "SkythonTestQuest"
BASE_SCRIPT = "SkythonTestQuestBase"
ALIAS_SCRIPT = "SkythonTestAlias"
LOCATION_ALIAS_SCRIPT = "SkythonTestLocationAlias"
# Owner decision 8 (plan index, Owner decisions): the scenario function and the fragments' channel, exactly as
# Task 48's RESULTS entry records them ("decision-8 names used: ..."). These are the proposal's names; if that line
# names others, write those here instead (Tasks 53-56 use t.SCENARIO_FUNCTION and t.CHANNEL, never literals).
SCENARIO_FUNCTION = "SetupScenario"
CHANNEL = "skython_test"
STAGES = (10, 20, 100, 200)
TARGET_ALIAS, ITEM_ALIAS, EXTRA_ALIAS, PLACE_ALIAS = 0, 1, 2, 3
REF_ALIASES = (TARGET_ALIAS, ITEM_ALIAS, EXTRA_ALIAS)
ALIAS_NAMES = {0: "SkythonTarget", 1: "SkythonItem", 2: "SkythonExtra", 3: "SkythonPlace"}
OBJECTIVE = 10
INITIAL = {  # the ESP's initial property values; skythonbase (Task 49) holds them, fresh_save reloads it per module
    QUEST_SCRIPT: {"Counter": 7, "Ratio": 0.5, "Flag": True, "Label": "initial label", "Target": NPC_KEY},
    BASE_SCRIPT: {"BaseCounter": 3, "BaseLabel": "base label"},
    ALIAS_SCRIPT: {"Tag": 5},
    LOCATION_ALIAS_SCRIPT: {"Tag": 6},
}
PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
WHITERUN_LOCATION = "Skyrim.esm|0x018A56"  # a vanilla Location (a second value for alias 3); typed-checked on use
FORM_TYPE_ACTOR = 62  # FormType::ActorCharacter (0x3E, ACHR) at the CommonLibSSE-NG pin: the Cell.GetNthRef filter


def stage_payload(stage: int) -> str:
    """What the stage fragment of `stage` sends on CHANNEL (plan index, Contracts: Test ESP)."""
    return f"stage_{stage}"


def same_key(form, key: str) -> bool:
    return form is not None and (form.form_key or "").lower() == key.lower()


@dataclass(frozen=True)
class Esp:
    quest: Form
    ref_aliases: tuple      # Alias(quest, 0..2, "ReferenceAlias")
    location_alias: Alias   # Alias(quest, 3, "LocationAlias")
    alias_names: dict       # alias_id -> name (ALIAS_NAMES, confirmed by Alias.GetName)
    npc: Form               # SkythonTestNPC
    objectives: tuple       # objective indexes as PO3_SKSEFunctions.GetAllQuestObjectives reports them

    @property
    def objective(self) -> int:
        return OBJECTIVE


def _typed(game, key: str, kind: str) -> Form:
    f = game.form(key)
    if f.type != kind:
        raise AssertionError(f"{key} is a {f.type}, not a {kind}: is {PLUGIN} the Task 48 build?")
    return f


def quest(game) -> Form:
    return _typed(game, QUEST_KEY, "Quest")


def npc(game) -> Form:
    return _typed(game, NPC_KEY, "Actor")


def location(game) -> Form:
    return _typed(game, LOCATION_KEY, "Location")


def require_test_cell(game) -> Form:
    """The player's cell, which must be SkythonTestCell: skythonbase stands there (Task 49)."""
    cell = game.call_method(PLAYER, "ObjectReference", "GetParentCell")
    if not same_key(cell, CELL_KEY):
        raise AssertionError(f"the player is not in SkythonTestCell (cell {cell!r}): load skythonbase (Task 49), "
                             "not s4test")
    return cell


def _cell_actors(game, cell: Form) -> list:
    n = game.call_method(cell, "Cell", "GetNumRefs", FORM_TYPE_ACTOR)
    refs = [game.call_method(cell, "Cell", "GetNthRef", i, FORM_TYPE_ACTOR) for i in range(n)]
    return [r for r in refs if r is not None and (r.form_key or "").startswith(PLUGIN + "|")]


def discover(game) -> Esp:
    """The fixed ESP values as live objects, after checking the game agrees with every one of them."""
    q = quest(game)
    cell = require_test_cell(game)
    n = game.call_method(q, "Quest", "GetNumAliases")
    found = {a.alias_id: a for a in (game.call_method(q, "Quest", "GetNthAlias", i) for i in range(n))
             if isinstance(a, Alias)}
    types = {i: a.alias_type for i, a in sorted(found.items())}
    want = {0: "ReferenceAlias", 1: "ReferenceAlias", 2: "ReferenceAlias", 3: "LocationAlias"}
    if types != want:
        raise AssertionError(f"{QUEST_KEY}: aliases {types}, the plan fixes {want} (Task 48 defect: STOP)")
    names = {}
    for i, a in sorted(found.items()):
        try:
            names[i] = game.call_method(a, "Alias", "GetName")
        except errors.TypeMismatch as e:
            raise AssertionError(f"alias {i} has no script object bound (R45; Task 48 defect: STOP): {e}") from e
    if names != ALIAS_NAMES:
        raise AssertionError(f"{QUEST_KEY}: alias names {names}, the plan fixes {ALIAS_NAMES} (STOP)")
    objectives = tuple(game.call("PO3_SKSEFunctions", "GetAllQuestObjectives", q) or ())
    if OBJECTIVE not in objectives:
        raise AssertionError(f"objective {OBJECTIVE} missing: GetAllQuestObjectives -> {objectives!r} (STOP)")
    the_npc = npc(game)
    actors = _cell_actors(game, cell)
    if [a.form_id for a in actors] != [the_npc.form_id]:
        raise AssertionError(f"SkythonTestCell should hold exactly {NPC_KEY} from {PLUGIN}, found {actors!r} (STOP)")
    return Esp(q, tuple(found[i] for i in REF_ALIASES), found[PLACE_ALIAS], dict(ALIAS_NAMES), the_npc, objectives)


def ensure_running(game, q: Form) -> None:
    """Start the quest if it is not running (aliases fill and objectives display only on a running quest)."""
    if game.call_method(q, "Quest", "IsRunning"):
        return
    if game.call_method(q, "Quest", "Start", timeout=30) is not True:
        raise AssertionError(f"Quest.Start({QUEST_KEY}) returned False (the plan makes every alias optional: STOP)")
    game.wait_for(lambda: game.call_method(q, "Quest", "IsRunning"), timeout=10)
```

Append to `livetest/conftest.py` (after its imports):
```python
import pathlib
import sys

# livetest/scripts holds _testesp (shared ESP helpers, Task 50) and the in-process scripts' importable modules.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "scripts"))
```

Check it imports on Linux (no game needed):
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python python -c "import sys; sys.path.insert(0, 'livetest/scripts'); import _testesp as t; print(t.stage_payload(10), t.QUEST_KEY)"
```
Expected: `stage_10 SkythonTest.esp|0x000800`.

Check the two decision-8 names against Task 48's RESULTS entry:
```bash
grep -o 'decision-8 names used: scenario function `[^`]*`, channel `[^`]*`' spikes/RESULTS.md
uv run --project python python -c "import sys; sys.path.insert(0, 'livetest/scripts'); import _testesp as t; print(f'decision-8 names used: scenario function \`{t.SCENARIO_FUNCTION}\`, channel \`{t.CHANNEL}\`')"
```
Expected: the two printed lines are identical. No line from the `grep` → STOP (Task 48's entry is missing); different lines → edit `SCENARIO_FUNCTION`/`CHANNEL` in `_testesp.py` to the RESULTS names and rerun.

- [ ] **Step 2: Write the live tests**

Create `livetest/test_m15_own_script.py`:
```python
"""M1.5 own-script tests on SkythonTest.esp (Task 50). Needs skythonbase (Task 49): the module fixture fresh_save
loads it, and _testesp.discover fails at once when another save is loaded or the ESP differs from the plan.
SPY_PLAIN_QUEST/SPY_PLAIN_ALIAS (livetest/s4test.env) name a plain alias in Skyrim.esm: the same in every save.
Run with -s to see the values."""
import pytest

import _testesp as t
from skython import errors
from support import spy_env

pytestmark = pytest.mark.usefixtures("fresh_save")


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-own")
    g.wait_ready(timeout=180)
    yield g
    g.close()


@pytest.fixture(scope="module")
def esp(mgame):
    # Start the quest first: alias scripts may bind only once the quest runs, and discover() calls Alias.GetName
    # through them, so discovering on a stopped quest could raise a false "no script object bound" STOP.
    t.ensure_running(mgame, t.quest(mgame))
    h = t.discover(mgame)
    print(f"\n{t.QUEST_KEY}: aliases {h.alias_names}, npc {h.npc.form_key}, objectives {h.objectives}")
    return h


def test_esp_is_loaded_and_the_player_is_in_its_cell(game, esp):
    assert game.call("Game", "IsPluginInstalled", t.PLUGIN) is True
    assert t.same_key(t.require_test_cell(game), t.CELL_KEY)
    assert game.call_method(esp.quest, "Quest", "IsRunning") is True


def test_bytecode_member_function_on_our_own_script(game, esp):
    # BumpCounter is Papyrus bytecode declared on SkythonTestQuest itself (not a native, not inherited).
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == t.INITIAL[t.QUEST_SCRIPT]["Counter"]  # 7
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", 3) == 10
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == 10
    finally:
        game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", -3)
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == 7


def test_alias_argument_arrives_as_that_alias(game, esp):
    # The BindAlias path (R45): the argument binds the object already bound to the alias, re-typed to ReferenceAlias.
    # DescribeAlias answers "empty" for an unfilled alias of this quest and "filled" once it holds a reference; an
    # argument that arrived as None would answer "none", one bound to another quest "foreign".
    for a in esp.ref_aliases:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", a) == "empty", a
    target = esp.ref_aliases[t.TARGET_ALIAS]
    game.call_method(target, "ReferenceAlias", "ForceRefTo", esp.npc)
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", target) == "filled"
        others = [game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", a) for a in esp.ref_aliases[1:]]
        assert others == ["empty", "empty"], others          # the filled answer belongs to the alias passed
    finally:
        game.call_method(target, "ReferenceAlias", "Clear")
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", target) == "empty"


def test_none_alias_argument(game, esp):
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", None) == "none"


def test_unscripted_alias_argument_is_type_mismatch(game, esp):
    quest, alias = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")  # Skyrim.esm data: plain in every save
    plain = game.alias(game.resolve(form_id=int(quest, 16)), int(alias))
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", plain)


def test_location_alias_for_a_reference_alias_parameter_is_type_mismatch(game, esp):
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", esp.location_alias)


def test_location_alias_force_get_clear_round_trip(game, esp):
    la = esp.location_alias                                   # alias 3, scripted with SkythonTestLocationAlias
    assert game.call_method(la, "LocationAlias", "GetLocation") is None   # optional, empty after Start
    ours, vanilla = t.location(game), game.form(t.WHITERUN_LOCATION)
    assert vanilla.type == "Location", vanilla
    for place in (vanilla, ours):
        game.call_method(la, "LocationAlias", "ForceLocationTo", place)
        got = game.call_method(la, "LocationAlias", "GetLocation")
        assert got is not None and got.form_id == place.form_id and got.type == "Location", (place, got)
    game.call_method(la, "LocationAlias", "Clear")
    assert game.call_method(la, "LocationAlias", "GetLocation") is None


def test_location_alias_argument_arrives_as_that_alias(game, esp):
    # The BindAlias path with a LocationAlias parameter: "empty" while alias 3 holds no location, "filled" after
    # ForceLocationTo (an argument that arrived as None would answer "none", another quest's alias "foreign").
    la = esp.location_alias
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "empty"
    game.call_method(la, "LocationAlias", "ForceLocationTo", t.location(game))
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "filled"
    finally:
        game.call_method(la, "LocationAlias", "Clear")
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", la) == "empty"
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeLocationAlias", None) == "none"


def test_alias_argument_typed_as_our_alias_script(game, esp):
    # A parameter typed SkythonTestAlias (our own alias script, not the native ReferenceAlias): the bound object is
    # re-typed to that script, so the function reads the script's own property Tag (initial 5).
    target = esp.ref_aliases[t.TARGET_ALIAS]
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "AliasTag", target) == t.INITIAL[t.ALIAS_SCRIPT]["Tag"]  # 5


def test_full_property_is_unsupported_before_anything_runs(game, esp):
    with pytest.raises(errors.Unsupported, match="full property"):
        game.get_property(esp.quest, t.QUEST_SCRIPT, "Computed")
    with pytest.raises(errors.Unsupported, match="full property"):
        game.set_property(esp.quest, t.QUEST_SCRIPT, "Computed", 1)
```

Collect without a game (every test must be collected and skipped, none errored):
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m15_own_script.py -q -rs 2>&1 | tail -3
```
Expected: `10 skipped` with the reason `live test: set SKYTHON_LIVE=1 with the game running`.

- [ ] **Step 3: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_deploy.sh --scripts livetest/scripts          # adds _testesp.py; the other scripts stay
tools/box/box_plugins.sh add                                # *SkythonTest.esp in plugins.txt (Task 45; restored by box_stop.sh)
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600   # fresh_save's game.load is refused while "starting"
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m15_own_script.py -q -rs -s
```
Expected: `10 passed`, no skips. The `-s` output shows the alias names, the NPC's form key and the objective list (all equal to the index's Test ESP values, or `discover` would have failed).

Then the evidence and the stop:
```bash
tools/box/box_files.sh pull skse64.log Skython.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "error|ERROR|critical" out/box-skse/Skython.log | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_plugins.sh show                               # plugins.txt equals its backup again
```
Expected: the plugin list (for the RESULTS entry), `no unexpected errors`, `box_stop.sh` reports the game gone, and `show` prints the backup's content with no `SkythonTest.esp` line. No save was written (this task never saves).

- [ ] **Step 4: Record in RESULTS**

Append to the "Plugin findings" list in `spikes/RESULTS.md`, filled from the Step 3 output (no bracket left):
```markdown
- **Task 50, own-script tests on SkythonTest.esp (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase, never saved; 1 launch, SKSE plugins: <list from skse64.log>):** `test_m15_own_script.py` 10 passed; `discover` confirmed the plan's ESP values (aliases 0-3 with their names and scripts, `SkythonTestNPC`, objectives <list>). **Alias arguments work (closes Task 29's gap):** `DescribeAlias` answered `empty` for aliases 0-2, `filled` for alias 0 after `ForceRefTo(SkythonTestNPC)` (the others stayed `empty`), `none` for `None` (the argument arrives as that alias, R45 BindAlias path); the plain `Skyrim.esm` alias 0 of 0x10FDD5 and the location alias are `type_mismatch` (<message>); `DescribeLocationAlias` answered `empty`, `filled` after `ForceLocationTo(SkythonTestLocation)`, `empty` after `Clear`, `none` for `None`; `AliasTag(alias 0)` -> 5 (a parameter typed as our own alias script). **LocationAlias ops (closes S4's NOT_RUN):** on alias 3 (`SkythonTestLocationAlias`), `ForceLocationTo` WhiterunLocation then `SkythonTestLocation`, `GetLocation` returned each, `Clear` then `GetLocation -> None`. Own bytecode member `BumpCounter(3)` -> 10 from the initial 7 (`GetCounter` agreed). Full property `Computed`: get and set `unsupported` before anything runs.
```
Also update the S4 row's `LocationAlias NOT_RUN (50 empty), verified in M1.5` to `LocationAlias NOT_RUN in S4; PASS in M1.5 (Task 50)`.

Check:
```bash
grep -n "Task 50, own-script" spikes/RESULTS.md && ! grep -n "Task 50, own-script.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 5: Commit**

```bash
git add livetest/scripts/_testesp.py livetest/conftest.py livetest/test_m15_own_script.py spikes/RESULTS.md
git commit -m "test(m1.5): own-script member call, alias arguments and LocationAlias ops on the test ESP"
```
