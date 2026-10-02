# Task 50: Own-script live tests: bytecode member call, alias argument, LocationAlias ops, full property refused

Carry-forward from the v1 plan: alias **arguments** had no live test (Task 29: no vanilla function takes an alias parameter; the `BindAlias` path, ruling R45), and LocationAlias operations were never run (spike S4: `LocationAlias NOT_RUN (50 empty), verified in M1.5`). The test ESP's own scripts make both testable.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Contracts** (Papyrus, Live tests) and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 49 is done: the base save `skythonbase` exists on the box (in `SkythonTestCell`), `SkythonTest.esp` and the five decision-7 `.pex` files are deployed, and `fresh_save` prefers `skythonbase`.
>
> **Runs on:** Linux (the test files), then the Windows box + game through the dev bridge.
>
> **STOP and ask the owner (do not improvise) if:** `discover()` cannot find the quest, at least 3 reference aliases, exactly 1 location alias, exactly one `SkythonTest.esp` actor in `SkythonTestCell`, or an objective; `Quest.Start` on `SkythonTestQuest` returns False; any LocationAlias call answers `type_mismatch` "no ... script object bound" (the location alias carries no script: R45 makes it undrivable; Task 48 must attach one, which is a Task 48 change, not this task's); `LocationAlias.Clear` leaves the location filled (the alias is not optional); `testesp/papyrus/SkythonTestQuest.psc` shows a `BumpCounter` or `DescribeAlias` body other than the one in **Interfaces** below; `Skyrim.esm|0x018A56` does not resolve to a `Location`; a live test crashes the game; `plugins.txt` differs from its backup in any line other than ours.

**Files:**
- Create: `livetest/scripts/_testesp.py` (shared ESP helpers; the leading `_` keeps `skython._host` from loading it as a script, and `box_deploy.sh --scripts livetest/scripts` still deploys it so the in-process scripts of Tasks 53, 55 and 56 can import it)
- Modify: `livetest/conftest.py` (append: `livetest/scripts` on `sys.path`)
- Create: `livetest/test_m15_own_script.py`
- Modify: `spikes/RESULTS.md` (append a Task 50 entry)

**Interfaces:**
- Consumes:
  - Task 48 (index Contracts, Papyrus): quest `SkythonTest.esp|0x000800` with script `SkythonTestQuest` (extends `SkythonTestQuestBase`); `Int BumpCounter(Int n)` **adds `n` to `Counter` and returns the new `Counter`**; `Int GetCounter()` returns `Counter`; `String DescribeAlias(ReferenceAlias akAlias)` **returns a string that contains `akAlias.GetName()`**; full property `Computed` (Int); `SkythonTestAlias` on ≥ 3 reference aliases; 1 location alias **carrying a script** (the native `LocationAlias` or a script extending it); all aliases optional; `SkythonTestCell` with one persistent NPC from `SkythonTest.esp`. The two bold behaviours and the location alias's script are not spelled out in the index; check them in `testesp/papyrus/SkythonTestQuest.psc` and Task 48's builder before Step 4 (STOP list).
  - Task 49: `skythonbase`, `fresh_save` (module fixture in `livetest/conftest.py`, loads `skythonbase`).
  - Task 36: `Game.wait_for(predicate, timeout, *, interval=0.25)`.
  - v1: `Game.call_method`, `get_property`, `set_property`, `form`, `alias`; `skython.errors.TypeMismatch`, `Unsupported`; `support.spy_env`; `livetest/s4test.env` (`SPY_PLAIN_QUEST=0x0010FDD5 SPY_PLAIN_ALIAS=0`: a plain alias in `Skyrim.esm`, save-independent data).
  - Natives (vanilla + SKSE + PO3 sources, checked in the SimpleSkyrim copy and `Scripts.zip`): `Quest.GetNumAliases()`, `Quest.GetNthAlias(int)`, `Quest.Start()`, `Quest.IsRunning()`, `Alias.GetName()`, `ObjectReference.GetParentCell()`, `Cell.GetNumRefs(int formTypeFilter)`, `Cell.GetNthRef(int n, int formTypeFilter)`, `LocationAlias.ForceLocationTo(Location)`, `LocationAlias.GetLocation()`, `LocationAlias.Clear()`, `PO3_SKSEFunctions.GetAllQuestObjectives(Quest) -> int[]`.
- Produces (Tasks 51, 52, 53, 55, 56 import these; names are fixed here):
  - `livetest/scripts/_testesp.py`: constants `PLUGIN`, `QUEST_KEY`, `QUEST_SCRIPT`, `BASE_SCRIPT`, `ALIAS_SCRIPT`, `SCENARIO_FUNCTION`, `CHANNEL`, `STAGES`, `PLAYER`, `IRON_SWORD`, `WHITERUN_LOCATION`, `FORM_TYPE_ACTOR`; `stage_payload(stage: int) -> str`; `@dataclass(frozen=True) class Esp(quest: Form, ref_aliases: tuple[Alias, ...], location_alias: Alias, alias_names: dict[int, str], npc: Form, objectives: tuple[int, ...])` with property `objective -> int`; `quest(game) -> Form`; `require_test_cell(game) -> Form`; `discover(game) -> Esp`; `location(game) -> Form`; `ensure_running(game, quest: Form) -> None`.
  - `livetest/conftest.py`: `livetest/scripts` is on `sys.path` for every live test (`import _testesp`).
  - `livetest/test_m15_own_script.py` and the RESULTS entry closing S4's LocationAlias `NOT_RUN` and Task 29's alias-argument gap.

- [ ] **Step 1: Shared ESP helpers**

Create `livetest/scripts/_testesp.py`:
```python
"""Shared helpers for SkythonTest.esp, the test plugin (plan Task 48). Development only: never ship it.

Imported by the live tests on Linux (livetest/conftest.py puts livetest/scripts on sys.path) and by the in-process
test scripts inside the game (deployed next to them by `tools/box/box_deploy.sh --scripts livetest/scripts`; the
leading underscore keeps skython._host from loading this module as a script).

The plan fixes only the quest's form key, the script and function names, the stage numbers and the fragment payloads
(plan index, Contracts). Alias ids and names, the NPC and the objective index are discovered from the running game,
so a rebuilt ESP with other form ids still works.
"""
from dataclasses import dataclass

from skython import errors
from skython.values import Alias, Form

PLUGIN = "SkythonTest.esp"
QUEST_KEY = "SkythonTest.esp|0x000800"
QUEST_SCRIPT = "SkythonTestQuest"
BASE_SCRIPT = "SkythonTestQuestBase"
ALIAS_SCRIPT = "SkythonTestAlias"
# Owner decision 8 (plan index, Gates): the scenario function and the fragments' channel. Owner names replace these.
SCENARIO_FUNCTION = "SetupScenario"
CHANNEL = "skython_test"
STAGES = (10, 20, 100, 200)
PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
WHITERUN_LOCATION = "Skyrim.esm|0x018A56"  # a vanilla Location; location() checks the type, so a wrong id fails loudly
FORM_TYPE_ACTOR = 62  # FormType::ActorCharacter (0x3E, ACHR) at the CommonLibSSE-NG pin: the Cell.GetNthRef filter


def stage_payload(stage: int) -> str:
    """What the stage fragment of `stage` sends on CHANNEL (plan index, Contracts: Papyrus)."""
    return f"stage_{stage}"


@dataclass(frozen=True)
class Esp:
    quest: Form
    ref_aliases: tuple      # Alias(..., "ReferenceAlias"), in GetNthAlias order
    location_alias: Alias
    alias_names: dict       # alias_id -> Alias.GetName()
    npc: Form               # the persistent NPC in SkythonTestCell
    objectives: tuple       # objective indexes, in PO3_SKSEFunctions.GetAllQuestObjectives order

    @property
    def objective(self) -> int:
        return self.objectives[0]


def quest(game) -> Form:
    q = game.form(QUEST_KEY)
    if q.type != "Quest":
        raise AssertionError(f"{QUEST_KEY} is a {q.type}, not a Quest: is {PLUGIN} the Task 48 build?")
    return q


def require_test_cell(game) -> Form:
    """The player's cell, which must come from the ESP: skythonbase stands in SkythonTestCell (Task 49)."""
    cell = game.call_method(PLAYER, "ObjectReference", "GetParentCell")
    if cell is None or not (cell.form_key or "").startswith(PLUGIN + "|"):
        raise AssertionError(f"the player is not in SkythonTestCell (cell {cell!r}): load skythonbase (Task 49), "
                             "not s4test")
    return cell


def _find_npc(game, cell: Form) -> Form:
    n = game.call_method(cell, "Cell", "GetNumRefs", FORM_TYPE_ACTOR)
    refs = [game.call_method(cell, "Cell", "GetNthRef", i, FORM_TYPE_ACTOR) for i in range(n)]
    ours = [r for r in refs if r is not None and r.form_id != PLAYER.form_id
            and (r.form_key or "").startswith(PLUGIN + "|")]
    if len(ours) != 1:
        raise AssertionError(f"expected exactly one {PLUGIN} actor in SkythonTestCell, found {ours!r} "
                             f"(all actors: {refs!r})")
    return ours[0]


def discover(game) -> Esp:
    q = quest(game)
    cell = require_test_cell(game)
    n = game.call_method(q, "Quest", "GetNumAliases")
    found = [game.call_method(q, "Quest", "GetNthAlias", i) for i in range(n)]
    refs = tuple(a for a in found if isinstance(a, Alias) and a.alias_type == "ReferenceAlias")
    locs = [a for a in found if isinstance(a, Alias) and a.alias_type == "LocationAlias"]
    if len(refs) < 3 or len(locs) != 1:
        raise AssertionError(f"{QUEST_KEY}: expected >= 3 reference aliases and 1 location alias, found {found!r}")
    names = {}
    for a in (*refs, locs[0]):
        try:
            names[a.alias_id] = game.call_method(a, "Alias", "GetName")
        except errors.TypeMismatch as e:
            raise AssertionError(f"alias {a.alias_id} has no script object bound (R45): {e}") from e
    objectives = tuple(game.call("PO3_SKSEFunctions", "GetAllQuestObjectives", q) or ())
    if not objectives:
        raise AssertionError(f"PO3_SKSEFunctions.GetAllQuestObjectives({QUEST_KEY}) returned no objective")
    return Esp(q, refs, locs[0], names, _find_npc(game, cell), objectives)


def location(game) -> Form:
    loc = game.form(WHITERUN_LOCATION)
    if loc.type != "Location":
        raise AssertionError(f"{WHITERUN_LOCATION} is a {loc.type}, not a Location: pick another vanilla Location")
    return loc


def ensure_running(game, q: Form) -> None:
    """Start the quest if it is not running (aliases fill and objectives display only on a running quest)."""
    if game.call_method(q, "Quest", "IsRunning"):
        return
    if game.call_method(q, "Quest", "Start", timeout=30) is not True:
        raise AssertionError(f"Quest.Start({QUEST_KEY}) returned False: are all its aliases optional? (STOP)")
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

- [ ] **Step 2: Write the live tests**

Create `livetest/test_m15_own_script.py`:
```python
"""M1.5 own-script tests on SkythonTest.esp (Task 50). Needs skythonbase (Task 49): the module fixture fresh_save
loads it, and _testesp.require_test_cell fails at once when another save is loaded.
SPY_PLAIN_QUEST/SPY_PLAIN_ALIAS (livetest/s4test.env) name a plain alias in Skyrim.esm: the same in every save.
Run with -s to see the discovered aliases and values."""
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
    h = t.discover(mgame)
    t.ensure_running(mgame, h.quest)
    print(f"\n{t.QUEST_KEY}: ref aliases {[a.alias_id for a in h.ref_aliases]}, location alias "
          f"{h.location_alias.alias_id}, names {h.alias_names}, npc {h.npc.form_key}, objectives {h.objectives}")
    return h


def test_esp_is_loaded_and_the_player_is_in_its_cell(game, esp):
    assert game.call("Game", "IsPluginInstalled", t.PLUGIN) is True
    assert t.require_test_cell(game).form_key.startswith(t.PLUGIN + "|")
    assert game.call_method(esp.quest, "Quest", "IsRunning") is True


def test_bytecode_member_function_on_our_own_script(game, esp):
    # BumpCounter is Papyrus bytecode declared on SkythonTestQuest itself (not a native, not inherited).
    before = game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter")
    assert type(before) is int, before
    try:
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", 3) == before + 3
        assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == before + 3
    finally:
        game.call_method(esp.quest, t.QUEST_SCRIPT, "BumpCounter", -3)
    assert game.call_method(esp.quest, t.QUEST_SCRIPT, "GetCounter") == before


def test_alias_argument_arrives_as_that_alias(game, esp):
    # The BindAlias path (R45): the argument binds the object already bound to the alias, re-typed to ReferenceAlias.
    # Each result must name the alias that was passed, so an argument that arrived as None or as another alias fails.
    seen = {}
    for a in esp.ref_aliases:
        text = game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", a)
        print(f"\nDescribeAlias(alias {a.alias_id} '{esp.alias_names[a.alias_id]}') -> {text!r}")
        assert isinstance(text, str) and esp.alias_names[a.alias_id].lower() in text.lower(), (a, text)
        seen[a.alias_id] = text
    assert len(set(seen.values())) == len(seen), seen  # different aliases, different answers


def test_unscripted_alias_argument_is_type_mismatch(game, esp):
    quest, alias = spy_env("SPY_PLAIN_QUEST", "SPY_PLAIN_ALIAS")  # Skyrim.esm data: plain in every save
    plain = game.alias(game.resolve(form_id=int(quest, 16)), int(alias))
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", plain)


def test_location_alias_for_a_reference_alias_parameter_is_type_mismatch(game, esp):
    with pytest.raises(errors.TypeMismatch):
        game.call_method(esp.quest, t.QUEST_SCRIPT, "DescribeAlias", esp.location_alias)


def test_location_alias_force_get_clear_round_trip(game, esp):
    la = esp.location_alias
    place = t.location(game)
    before = game.call_method(la, "LocationAlias", "GetLocation")
    print(f"\nlocation alias {la.alias_id} before: {before!r}; forcing {place.form_key}")
    game.call_method(la, "LocationAlias", "ForceLocationTo", place)
    got = game.call_method(la, "LocationAlias", "GetLocation")
    assert got is not None and got.form_id == place.form_id and got.type == "Location", got
    game.call_method(la, "LocationAlias", "Clear")
    assert game.call_method(la, "LocationAlias", "GetLocation") is None  # Clear works on an optional alias only


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
Expected: `7 skipped` with the reason `live test: set SKYTHON_LIVE=1 with the game running`.

- [ ] **Step 3: Check the Task 48 behaviours this task relies on**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -n -i -A4 "Function BumpCounter\|Function DescribeAlias\|Function GetCounter" testesp/papyrus/SkythonTestQuest.psc
grep -n -i "Property Computed" -A6 testesp/papyrus/SkythonTestQuest.psc
grep -rn -i "LocationAlias\|Optional" testesp/builder/*.cs | head -20
```
Expected: `BumpCounter` adds its argument to `Counter` and returns `Counter`; `DescribeAlias` builds its result from `akAlias.GetName()`; `Computed` has an explicit `Function Get()`/`Set` (not `Auto`); the builder attaches a script to the location alias and marks the aliases optional. Anything else: STOP (do not loosen the tests to fit).

- [ ] **Step 4: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_deploy.sh --scripts livetest/scripts          # adds _testesp.py; the other scripts stay
tools/box/box_plugins.sh add                                # *SkythonTest.esp in plugins.txt (Task 45; restored by box_stop.sh)
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m15_own_script.py -q -rs -s
```
Expected: `7 passed`, no skips. The `-s` output shows the discovered alias ids, names, the NPC's form key and the objective list, the `DescribeAlias` results, and the location alias before/after.

Then the evidence and the stop:
```bash
tools/box/box_files.sh pull skse64.log Skython.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -E "error|ERROR|critical" out/box-skse/Skython.log | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
tools/box/box_plugins.sh show                               # plugins.txt equals its backup again
```
Expected: the plugin list (for the RESULTS entry), `no unexpected errors`, `box_stop.sh` reports the game gone, and `show` prints the backup's content with no `SkythonTest.esp` line. No save was written (this task never saves).

- [ ] **Step 5: Record in RESULTS**

Append to the "Plugin findings" list in `spikes/RESULTS.md`, filled from the Step 4 output (no bracket left):
```markdown
- **Task 50, own-script tests on SkythonTest.esp (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase, never saved; 1 launch, SKSE plugins: <list from skse64.log>):** `test_m15_own_script.py` 7 passed. Discovered: ref aliases <ids and names>, location alias <id and name>, NPC <form key>, objectives <list>. **Alias arguments work (closes Task 29's gap):** `DescribeAlias(alias <id>) -> <text>` for each scripted alias (the argument arrives as that alias, R45 BindAlias path); the plain `Skyrim.esm` alias 0 of 0x10FDD5 and the location alias are `type_mismatch` (<message>). **LocationAlias ops (closes S4's NOT_RUN):** `ForceLocationTo(<WhiterunLocation key>)`, `GetLocation -> <form>`, `Clear` then `GetLocation -> None`. Own bytecode member `BumpCounter(3)` -> <value> (`GetCounter` agreed). Full property `Computed`: get and set `unsupported` before anything runs.
```
Also update the S4 row's `LocationAlias NOT_RUN (50 empty), verified in M1.5` to `LocationAlias NOT_RUN in S4; PASS in M1.5 (Task 50)`.

Check:
```bash
grep -n "Task 50, own-script" spikes/RESULTS.md && ! grep -n "Task 50, own-script.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 6: Commit**

```bash
git add livetest/scripts/_testesp.py livetest/conftest.py livetest/test_m15_own_script.py spikes/RESULTS.md
git commit -m "test(m1.5): own-script member call, alias arguments and LocationAlias ops on the test ESP"
```
