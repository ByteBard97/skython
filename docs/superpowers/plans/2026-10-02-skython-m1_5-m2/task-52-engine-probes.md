# Task 52: The three engine probes and `docs/probes/`

The spec's M1.5 milestone (section 9) names three engine probes, all run through the generic call path: (1) three `<Alias=...>` tags in one objective line all resolve to the filled references' names; (2) powerofthree's `SetObjectiveText` with alias tags still resolves; (3) objective text and alias fills survive a save and reload. They answer questions any mod that builds quests at runtime asks, so the write-ups in `docs/probes/` are generic: no consumer is named.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Saves; general-purpose naming; Do it from the CLI), **Contracts** and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 49 (`skythonbase`), Task 50 (`_testesp.py`) and Task 41 (`Game.save`/`Game.load`) are done.
>
> **Runs on:** Linux (test file, write-ups), then the Windows box + game.
>
> **STOP and ask the owner (do not improvise) if:** a probe's verdict is INCONCLUSIVE after Step 4 (neither the screenshots nor the `sqo` text show the rendered objective; do not open the Journal from Papyrus: the menu pauses the VM, and nothing driven through Papyrus could close it again); the save name already exists (`box_saves.sh count` not 0); `PO3_SKSEFunctions.SetObjectiveText` is `unknown_function` (powerofthree's Papyrus Extender not loaded this launch); a screenshot shows anything but the game (another window, a desktop with personal content): do not commit it; a live test crashes the game.

**Method (the same for every probe, written into each write-up):**
- **Fills (all three probes):** alias 0 `SkythonTarget` ← `SkythonTestNPC` (`ForceRefTo`), alias 1 `SkythonItem` ← an iron sword placed at the player (`ForceRefTo`), alias 3 `SkythonPlace` ← `SkythonTestLocation` (`ForceLocationTo`); objective 10. Alias 2 is not tagged and stays empty.
- **Rendered text, method A (screenshot):** `Quest.SetObjectiveDisplayed(idx, False, False)` then `SetObjectiveDisplayed(idx, True, True)` (force) makes the HUD show the objective banner for a few seconds; `tools/box/box_shot.sh <png>` is started at once. Up to 3 tries per probe; the worker opens each PNG and records whether the banner is visible and what it says.
- **Rendered text, method B (console):** `tools/box/box_console.sh "sqo SkythonTestQuest"` (ShowQuestObjectives); the reply is recorded verbatim. Whether `sqo` prints resolved names or raw tags is itself recorded, not assumed.
- **State:** `Quest.IsObjectiveDisplayed(idx)`, aliases 0 and 1's `ReferenceAlias.GetReference()` with each filled reference's `ObjectReference.GetDisplayName()` (SKSE), and alias 3's `LocationAlias.GetLocation()` with the location's `Form.GetName()` (SKSE), through the bridge.
- **Verdict per probe (method A decides, method B only supports):** the rendered HUD banner (method A) is what a player sees, so it decides. **PASS** when a screenshot shows every tag replaced by the filled alias's name (the reference's display name for aliases 0 and 1, the location's name for alias 3). **FAIL** when a screenshot shows a raw `<Alias=...>` tag or an empty name where a filled alias should appear. When no screenshot captured the banner: method B (`sqo`) can give **PASS** only if its reply shows the resolved names; if `sqo` shows raw tags (or nothing) and method A captured nothing, the verdict is **INCONCLUSIVE**, never FAIL (`sqo` may print the stored text without resolving tags; that is recorded as a fact about `sqo`, not about the objective). A method-B result that disagrees with a captured method-A result is recorded; method A's verdict stands.

**Files:**
- Create: `livetest/test_m15_probes.py`
- Create: `docs/probes/README.md`, `docs/probes/01-objective-alias-tags.md`, `docs/probes/02-setobjectivetext-alias-tags.md`, `docs/probes/03-objective-save-reload.md`
- Create: `docs/probes/img/` (the screenshots each verdict rests on, copied from `out/probes/`)
- Modify: `spikes/RESULTS.md` (Task 52 entry; the new save on the removal list)

**Interfaces:**
- Consumes:
  - Task 48 (index Contracts, **Test ESP**): objective 10 with the text `Skython test: <Alias=SkythonTarget>, <Alias=SkythonItem>, <Alias=SkythonPlace>`, i.e. reference aliases 0 and 1 and location alias 3 (`SkythonTestLocationAlias`); `SkythonTestNPC` `SkythonTest.esp|0x000804`; `SkythonTestLocation` `|0x000801`.
  - Task 50: `_testesp.discover(game) -> Esp` (`quest`, `ref_aliases`, `location_alias`, `alias_names`, `npc`, `objective`), `ensure_running`, `location(game)`, `PLAYER`, `IRON_SWORD`, `TARGET_ALIAS`, `ITEM_ALIAS`, `PLACE_ALIAS`, `ALIAS_NAMES`, `OBJECTIVE`.
  - Task 41: `Game.save(name, *, timeout=60.0) -> str` (answers once the engine **started** the save; the `.ess` can appear a moment later), `Game.load(name, *, timeout=120.0, wait=True) -> dict` (returns the interactive state with the new `epoch`); `livetest/support.py` `new_save_name(tag) -> str` (checks `box_saves.sh count` is 0, records the name in `livetest/results/new_saves.txt`) and `wait_save_file(name, timeout=30.0)` (bounded poll until the `.ess` exists).
  - Task 49: `fresh_save`. Box tools: `box_console.sh`, `box_shot.sh [LOCAL_PNG]`, `box_saves.sh count NAME`.
  - Natives: `Quest.SetActive(bool)`, `Quest.SetObjectiveDisplayed(int, bool, bool)`, `Quest.IsObjectiveDisplayed(int)`, `ReferenceAlias.ForceRefTo(ObjectReference)`, `ReferenceAlias.GetReference()`, `LocationAlias.ForceLocationTo(Location)`, `LocationAlias.GetLocation()`, `ObjectReference.GetDisplayName()` (SKSE), `Form.GetName()` (SKSE), `ObjectReference.PlaceAtMe(Form, int, bool, bool)`, `PO3_SKSEFunctions.SetObjectiveText(Quest, string, int)`.
- Produces: `docs/probes/*.md` (method, raw results, verdict per probe); `out/probes/probes.json` and `out/probes/*.png` (raw, not committed; the PNGs a verdict rests on are copied to `docs/probes/img/`); one new throwaway save from `new_save_name("t52")` (a second one only if Step 4 re-runs probe 3), on the owner's removal list.

- [ ] **Step 1: Write the probe test**

Create `livetest/test_m15_probes.py`:
```python
"""Spec section 9's three engine probes on SkythonTest.esp (Task 52). The tests check the mechanics (fills, display
state, save/load) and collect the evidence for the rendered text: HUD screenshots (box_shot.sh) and the `sqo`
console reply (box_console.sh). The verdict on the rendered text is the worker's, from that evidence (task file,
Method). Writes out/probes/probes.json and out/probes/*.png. Each probe can run alone (-k probe2): the module fixture
`filled` makes the shared fills. Run with -s; probe 3 leaves one new save (new_save_name("t52"), recorded in
livetest/results/new_saves.txt for the owner's removal list)."""
import json
import subprocess
import time

import pytest

import _testesp as t
from support import REPO, new_save_name, wait_save_file

pytestmark = pytest.mark.usefixtures("fresh_save")
OUT = REPO / "out" / "probes"
SHOT_TRIES = 3
RAW: dict = {}


def _tool(script: str, *args: str) -> str:
    r = subprocess.run([str(REPO / "tools" / "box" / script), *args], capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"{script} {args} failed: {r.stdout}{r.stderr}"
    return r.stdout


def sqo() -> str:
    return _tool("box_console.sh", "sqo SkythonTestQuest")


@pytest.fixture(scope="module")
def mgame(session, fresh_save):
    import skython
    OUT.mkdir(parents=True, exist_ok=True)
    g = skython.connect(port=session.port, token=session.token, client_name="livetest-m15-probes")
    g.wait_ready(timeout=180)
    yield g
    g.close()
    (OUT / "probes.json").write_text(json.dumps(RAW, indent=2, default=str))


@pytest.fixture(scope="module")
def esp(mgame):
    t.ensure_running(mgame, t.quest(mgame))  # first: alias scripts may bind only once the quest runs (discover uses them)
    h = t.discover(mgame)
    mgame.call_method(h.quest, "Quest", "SetActive", True)
    return h


@pytest.fixture(scope="module")
def filled(mgame, esp):
    """The fills every probe uses, made once per module (so `-k probe2` or `-k probe3` alone starts from them):
    alias 0 <- SkythonTestNPC, alias 1 <- an iron sword placed at the player, alias 3 <- SkythonTestLocation, and
    objective 10 displayed. Alias 2 stays empty."""
    sword = mgame.call_method(t.PLAYER, "ObjectReference", "PlaceAtMe", mgame.form(t.IRON_SWORD), 1, True, False)
    place = t.location(mgame)
    mgame.call_method(esp.ref_aliases[t.TARGET_ALIAS], "ReferenceAlias", "ForceRefTo", esp.npc)
    mgame.call_method(esp.ref_aliases[t.ITEM_ALIAS], "ReferenceAlias", "ForceRefTo", sword)
    mgame.call_method(esp.location_alias, "LocationAlias", "ForceLocationTo", place)
    mgame.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, True, True)
    return {"sword": sword, "place": place}


def show_and_shoot(game, esp, tag: str) -> list:
    """Force the objective banner and screenshot it, up to SHOT_TRIES times. Returns the PNG paths."""
    paths = []
    for i in range(SHOT_TRIES):
        game.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, False, False)
        game.call_method(esp.quest, "Quest", "SetObjectiveDisplayed", esp.objective, True, True)
        png = OUT / f"{tag}_try{i + 1}.png"
        print(_tool("box_shot.sh", str(png)))
        paths.append(str(png))
        time.sleep(2)
    return paths


def fills(game, esp) -> list:
    """The three aliases the objective's tags name, in tag order: 0 SkythonTarget, 1 SkythonItem, 3 SkythonPlace."""
    out = []
    for i in (t.TARGET_ALIAS, t.ITEM_ALIAS):
        a = esp.ref_aliases[i]
        ref = game.call_method(a, "ReferenceAlias", "GetReference")
        name = game.call_method(ref, "ObjectReference", "GetDisplayName") if ref is not None else None
        out.append({"alias_id": i, "alias_name": t.ALIAS_NAMES[i],
                    "ref": None if ref is None else {"form_id": ref.form_id, "form_key": ref.form_key},
                    "display_name": name})
    loc = game.call_method(esp.location_alias, "LocationAlias", "GetLocation")
    out.append({"alias_id": t.PLACE_ALIAS, "alias_name": t.ALIAS_NAMES[t.PLACE_ALIAS],
                "ref": None if loc is None else {"form_id": loc.form_id, "form_key": loc.form_key},
                "display_name": game.call_method(loc, "Form", "GetName") if loc is not None else None})
    return out


def test_probe1_three_alias_tags_in_one_objective(game, esp, filled):
    sword, place = filled["sword"], filled["place"]
    got = fills(game, esp)
    assert [f["ref"]["form_id"] if f["ref"] else None for f in got] == [esp.npc.form_id, sword.form_id,
                                                                        place.form_id], got
    assert all(f["display_name"] for f in got), got        # every tag has a non-empty name to show
    shots = show_and_shoot(game, esp, "probe1")
    assert game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True
    RAW["probe1"] = {"objective": esp.objective, "fills": got, "sqo": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe1"], indent=2))


def test_probe2_po3_set_objective_text_with_alias_tags(game, esp, filled):
    names = [t.ALIAS_NAMES[i] for i in (t.TARGET_ALIAS, t.ITEM_ALIAS, t.PLACE_ALIAS)]
    text = "Probe two: " + " / ".join(f"<Alias={n}>" for n in names)
    game.call("PO3_SKSEFunctions", "SetObjectiveText", esp.quest, text, esp.objective)
    shots = show_and_shoot(game, esp, "probe2")
    assert game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective) is True
    RAW["probe2"] = {"text_set": text, "fills": fills(game, esp), "sqo": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe2"], indent=2))


def test_probe3_objective_and_fills_survive_save_and_reload(game, esp, filled):
    save = new_save_name("t52")              # checks box_saves.sh count is 0; records it for the removal list
    RAW.setdefault("saves", []).append(save)
    before = fills(game, esp)
    epoch = game.state()["epoch"]
    assert game.save(save) == save           # the engine started the save
    wait_save_file(save)                     # the .ess can appear a moment after game.save answers
    st = game.load(save, wait=True)
    assert st["epoch"] > epoch, (epoch, st)
    after = fills(game, esp)
    displayed = game.call_method(esp.quest, "Quest", "IsObjectiveDisplayed", esp.objective)
    text_after_load = sqo()                                    # before anything re-displays the objective
    shots = show_and_shoot(game, esp, "probe3")
    RAW["probe3"] = {"epoch_before": epoch, "epoch_after": st["epoch"], "fills_before": before,
                     "fills_after": after, "displayed_after_load": displayed, "sqo_after_load": text_after_load,
                     "sqo_after_redisplay": sqo(), "screenshots": shots}
    print(json.dumps(RAW["probe3"], indent=2))
    assert [f["ref"] for f in after] == [f["ref"] for f in before], (before, after)
    assert displayed is True
```

Collect without a game:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m15_probes.py -q -rs 2>&1 | tail -3
```
Expected: `3 skipped`.

- [ ] **Step 2: Run on the box**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600   # fresh_save's game.load is refused while "starting"
export SKYTHON_LIVE=1 SKYTHON_BOX=1
tools/box/box_console.sh --list > out/probes-devbench-tools.json    # recorded in the write-up (which tools exist)
uv run --project python pytest livetest/test_m15_probes.py -q -rs -s
tools/box/box_files.sh pull skse64.log Skython.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_plugins.sh show
ls -la out/probes/
```
Expected: `3 passed`; `out/probes/probes.json` and nine PNGs (`probe1_try1..3`, `probe2_...`, `probe3_...`); the plugin list shows `po3_PapyrusExtender` loaded correctly; `show` prints the backup with no `SkythonTest.esp` line. Record the save name printed in `probes.json` (`"saves"`; also the last line of `livetest/results/new_saves.txt`).

- [ ] **Step 3: Read the evidence and decide each verdict**

Open every PNG (the Read tool shows images) and `out/probes/probes.json`. For each probe write down: which try shows the objective banner (or none), the banner's exact text, the `sqo` reply's objective line, and the expected names (`display_name` of aliases 0, 1 and 3, in the tag order). Apply the Method's verdict rule. Probe 3 has two questions, recorded separately: (a) alias fills and the displayed flag after the reload (the test asserts both), (b) the objective text after the reload: the ESP text or the `SetObjectiveText` text from probe 2, and whether its tags resolve.

- [ ] **Step 4: If a verdict is INCONCLUSIVE, try once more, then STOP**

Re-run only that probe in a fresh launch (the module fixture `filled` remakes the fills, so each probe runs alone; probes 1 and 2 need no save; a probe-3 rerun makes a second save under a new `new_save_name("t52")` name, which also goes on the removal list):
```bash
cp out/probes/probes.json out/probes/probes-run1.json        # the rerun rewrites probes.json with that probe only
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m15_probes.py -q -rs -s -k probe1     # or probe2 / probe3
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_plugins.sh show
```
Still INCONCLUSIVE: STOP and ask the owner (report what each method showed; options are the owner looking at the in-game Journal once, or a DevBench tool from `out/probes-devbench-tools.json` that can close a menu).

- [ ] **Step 5: Write the probe docs**

Copy the screenshots each verdict rests on (one per probe, plus probe 3's before/after if both are used) to `docs/probes/img/` with the names `probe1.png`, `probe2.png`, `probe3.png`. Check each shows only the game first.

Create `docs/probes/README.md`:
```markdown
# Engine probes

Three questions about quest objectives and reference aliases that matter to any mod that fills a quest at runtime, answered on Skyrim AE 1.6.1170 through Skython's generic call path (no special API). The test plugin `testesp/SkythonTest.esp` provides the quest, its aliases and its objective; the test is `livetest/test_m15_probes.py`.

| Probe | Question | Verdict |
|---|---|---|
| [1](01-objective-alias-tags.md) | Do three `<Alias=...>` tags in one objective line all show the filled references' names? | [[verdict 1]] |
| [2](02-setobjectivetext-alias-tags.md) | Does objective text set at runtime with powerofthree's `SetObjectiveText` still resolve alias tags? | [[verdict 2]] |
| [3](03-objective-save-reload.md) | Do objective text and alias fills survive a save and a reload? | [[verdict 3]] |

Method (all three): the objective banner is forced onto the HUD with `Quest.SetObjectiveDisplayed(idx, True, True)` and screenshotted; the console command `sqo` (ShowQuestObjectives) is recorded verbatim; fills and display state are read back with `ReferenceAlias.GetReference`, `ObjectReference.GetDisplayName` and `Quest.IsObjectiveDisplayed`. Run on [[date]], save `skythonbase` (the test plugin's cell), plugins: [[SKSE plugin list]].
```
Create `docs/probes/01-objective-alias-tags.md` (and `02-...`, `03-...` the same way, each with its own probe's question, setup and data):
```markdown
# Probe 1: three alias tags in one objective line

**Question.** An objective's text can name reference aliases with `<Alias=Name>` tags. With three tags in one line and all three aliases filled, does the game show all three names?

**Setup.** Quest `SkythonTest.esp|0x000800`, objective 10 with the text `Skython test: <Alias=SkythonTarget>, <Alias=SkythonItem>, <Alias=SkythonPlace>`. Aliases filled: `SkythonTarget` ← `SkythonTestNPC` and `SkythonItem` ← an iron sword with `ReferenceAlias.ForceRefTo`, `SkythonPlace` ← `SkythonTestLocation` with `LocationAlias.ForceLocationTo` ([[display names as read]]). Then `SetObjectiveDisplayed(10, True, True)`.

**Raw results.**
- HUD banner (`img/probe1.png`, try [[n]]): [[exact banner text, or "not captured"]]
- `sqo SkythonTestQuest`: [[verbatim reply line]]
- `IsObjectiveDisplayed`: [[True/False]]

**Verdict: [[PASS/FAIL]].** [[One sentence: what showed it, and anything a mod author should do differently.]]
```
For probe 2 the setup line is the text passed to `SetObjectiveText`; for probe 3 the raw results are before/after pairs (fills, displayed flag, `sqo` before and after re-display, the epoch change, the save name) and the verdict states (a) and (b) from Step 3 separately.

Check that no marker is left and no consumer is named:
```bash
grep -rn '\[\[' docs/probes/ || echo "probes filled"
grep -rni 'skyrim-dm\|skyrim_dm' docs/probes/ || echo "generic"
```
Expected: `probes filled`, then `generic`.

- [ ] **Step 6: Record in RESULTS**

Append to "Plugin findings" in `spikes/RESULTS.md` (filled, no bracket left), and add the save to the removal list section (Task 41 created it; add the line under it):
```markdown
- **Task 52, the three engine probes (box, Windows-native, <date and time>, Skyrim 1.6.1170.0, skythonbase then <save>; 1 launch, SKSE plugins: <list>):** `test_m15_probes.py` 3 passed. Probe 1 (three alias tags in one objective) <verdict>: <one line of evidence>. Probe 2 (PO3 `SetObjectiveText` with alias tags) <verdict>: <evidence>. Probe 3 (save and reload) fills and display <kept/lost>, text after reload <ESP text / runtime text>, tags <resolved/raw>. `sqo` prints <resolved names / raw tags>. Write-ups: `docs/probes/`. New save(s): `<save>` (and `<second save>` if Step 4 re-ran probe 3) (removal list).
```
Removal list line(s): `- <save>.ess (Task 52, probe 3)` (and `- <second save>.ess (Task 52, probe 3 rerun)` if made).

Check:
```bash
grep -n "Task 52, the three engine probes" spikes/RESULTS.md && ! grep -n "Task 52, the three engine probes.*<" spikes/RESULTS.md && echo "entry filled"
```
Expected: the line number, then `entry filled`.

- [ ] **Step 7: Commit**

```bash
git add livetest/test_m15_probes.py docs/probes/ spikes/RESULTS.md
git commit -m "docs(probes): objective alias tags, SetObjectiveText and save/reload probes on the test ESP"
```
