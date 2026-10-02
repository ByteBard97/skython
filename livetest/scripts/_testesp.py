"""Shared constants and helpers for SkythonTest.esp, the test plugin. Development only: never ship it.

Imported by the live tests on Linux (livetest/conftest.py puts livetest/scripts on sys.path) and by the in-process
test scripts inside the game (deployed next to them by `tools/box/box_deploy.sh --scripts livetest/scripts`; the
leading underscore keeps skython._host from loading this module as a script).

Every value below is fixed by the ESP's build (testesp/builder/TestEsp.cs and testesp/papyrus/*.psc). discover()
reads the same facts back from the running game and fails when they differ: it is a cross-check of the ESP build,
never a source of values.
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
# The scenario function and the fragments' channel, exactly as spikes/RESULTS.md records them. The ESP-based
# live tests use t.SCENARIO_FUNCTION and t.CHANNEL, never literals.
SCENARIO_FUNCTION = "SetupScenario"
CHANNEL = "skython_test"
STAGES = (10, 20, 100, 200)
TARGET_ALIAS, ITEM_ALIAS, EXTRA_ALIAS, PLACE_ALIAS = 0, 1, 2, 3
REF_ALIASES = (TARGET_ALIAS, ITEM_ALIAS, EXTRA_ALIAS)
ALIAS_NAMES = {0: "SkythonTarget", 1: "SkythonItem", 2: "SkythonExtra", 3: "SkythonPlace"}
OBJECTIVE = 10
INITIAL = {  # the ESP's initial property values; skythonbase holds them, fresh_save reloads it per module
    QUEST_SCRIPT: {"Counter": 7, "Ratio": 0.5, "Flag": True, "Label": "initial label", "Target": NPC_KEY},
    BASE_SCRIPT: {"BaseCounter": 3, "BaseLabel": "base label"},
    ALIAS_SCRIPT: {"Tag": 5},
    LOCATION_ALIAS_SCRIPT: {"Tag": 6},
}
PLAYER = Form(form_id=0x14)
IRON_SWORD = "Skyrim.esm|0x012EB7"
WHITERUN_LOCATION = "Skyrim.esm|0x018A56"  # a vanilla Location (a second value for alias 3); type-checked on use
FORM_TYPE_ACTOR = 62  # FormType::ActorCharacter (0x3E, ACHR) at the CommonLibSSE-NG pin: the Cell.GetNthRef filter


def stage_payload(stage: int) -> str:
    """What the stage fragment of `stage` sends on CHANNEL."""
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
        raise AssertionError(f"{key} is a {f.type}, not a {kind}: is {PLUGIN} the current build of the test plugin?")
    return f


def quest(game) -> Form:
    return _typed(game, QUEST_KEY, "Quest")


def npc(game) -> Form:
    return _typed(game, NPC_KEY, "Actor")


def location(game) -> Form:
    return _typed(game, LOCATION_KEY, "Location")


def require_test_cell(game) -> Form:
    """The player's cell, which must be SkythonTestCell: skythonbase stands there."""
    cell = game.call_method(PLAYER, "ObjectReference", "GetParentCell")
    if not same_key(cell, CELL_KEY):
        raise AssertionError(f"the player is not in SkythonTestCell (cell {cell!r}): load skythonbase, not s4test")
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
        raise AssertionError(f"{QUEST_KEY}: aliases {types}, the ESP build fixes {want} (a build defect: STOP)")
    names = {}
    for i, a in sorted(found.items()):
        try:
            names[i] = game.call_method(a, "Alias", "GetName")
        except errors.TypeMismatch as e:
            raise AssertionError(f"alias {i} has no script object bound (a build defect of the test plugin: "
                                 f"STOP): {e}") from e
    if names != ALIAS_NAMES:
        raise AssertionError(f"{QUEST_KEY}: alias names {names}, the ESP build fixes {ALIAS_NAMES} (STOP)")
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
        raise AssertionError(f"Quest.Start({QUEST_KEY}) returned False (the ESP makes every alias optional: STOP)")
    game.wait_for(lambda: game.call_method(q, "Quest", "IsRunning"), timeout=10)
