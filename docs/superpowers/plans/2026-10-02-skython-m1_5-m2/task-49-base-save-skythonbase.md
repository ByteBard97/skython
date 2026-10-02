# Task 49: Base save `skythonbase` and the save fixture

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (especially "Saves"), **Fixed paths**, **Contracts** (Live tests: `load_save`, `fresh_save`; **Test ESP**: form keys, the quest not running at load, the initial property values), **Owner decisions** (decision 7 is a STOP gate) and the Part F brief for Task 49 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** the Windows box with the game (Steps 4-8), after Linux-side edits (Steps 1-3).
>
> **Depends on:** Task 41 (`Game.save`, `livetest/support.py` `load_save`, `livetest/conftest.py` `fresh_save`, which already loads `skythonbase` when it exists on the box, else `s4test`), Task 48 (SkythonTest.esp PASS, deployed), Gate 0 (owner decision 7, recorded as accepted; README **Owner decisions**).
>
> **STOP and ask the owner (do not improvise) if:** `tools/box/box_saves.sh count skythonbase` prints anything but `0` before the save (the name is taken: never save over it, never pick another name without the owner); `coc SkythonTestCell` from the main menu does not reach the cell within 5 minutes; `game.save` fails or the count is not `1` within the bounded 60 s poll afterwards (`game.save` answers when the engine **started** the save; the `.ess` can appear a moment later, so the count is polled, never read once); the game crashes.

**What the base save is.** One save, made once, from the main menu: `coc SkythonTestCell` starts the game directly in the test cell (no character creation, no intro: the player is the default character), then `game.save("skythonbase")`. Nothing else is touched before the save: no `set_property`, no `call_method` that writes (only reads such as `GetCounter`, `IsRunning`, `IsDead`), the test quest has never run, the NPC is alive, the quest scripts hold the Contracts' initial property values (`Counter` 7, `Ratio` 0.5, `Flag` True, `Label` `"initial label"`, `Target` = `SkythonTestNPC`, `BaseCounter` 3, `BaseLabel` `"base label"`), so every later test that starts from `skythonbase` starts from the same state. Old saves made without the ESP (`s4test`, `spikebase`) stay loadable (the ESP is only ever added). **`skythonbase` depends on SkythonTest.esp:** every box session that loads it must run `tools/box/box_plugins.sh add` before `box_launch.sh` (README Global Constraints, "`plugins.txt` around launches"; `box_quit.sh`/`box_stop.sh` run `box_plugins.sh restore-if-added` afterwards); loading it without the plugin raises the game's missing-content prompt.

**Files:**
- Create: `livetest/test_m15_base_save.py`
- Modify: `tools/box/win/list.ps1` (the teardown list shows `skythonbase`), `tools/box/test_box_tools.sh` (check 6g), `spikes/RESULTS.md`
- On the box only: the save `skythonbase` (`.ess` + `.skse`), on the owner's removal list.

**Interfaces:**
- Consumes: `Game.save(name)`, `Game.wait_ready`, `game.resolve`, `game.call_method` (v1, Task 41); `game.get_property` (v1 Task 30); `livetest/support.py` `load_session`, `raw_connect`, `subscribe(sock, event, filter=None, req_id=1, max_queue=None) -> int`, `read_event(sock, timeout) -> dict | None`, `load_save(game, name, timeout=120.0) -> dict`, `log_text`; the `fresh_save` fixture (Task 41); log lines `lifecycle: Main Menu open`, `lifecycle: interactive = true`, `saveload: saved <name>`, `saveload: load started <name>` (Task 41), `events: game_loaded epoch <n>` (Task 40); `tools/box/box_saves.sh count`, `box_console.sh`, `box_plugins.sh` (Task 45).
- Produces: the save `skythonbase` on the box (player in `SkythonTestCell` `SkythonTest.esp|0x000802`, quest `|0x000800` not running at stage 0 with every quest-script property at its Contracts initial value, `SkythonTestNPC` `|0x000804` alive in the cell); the rule "add the plugins.txt line before loading it"; `livetest/test_m15_base_save.py` as the check that `fresh_save` lands there (Tasks 50-56 start from it).

- [ ] **Step 1: The live test**

Create `livetest/test_m15_base_save.py`:
```python
"""Task 49: the base save `skythonbase` (made once from the main menu with `coc SkythonTestCell`) and the fresh_save
fixture that prefers it. Needs SkythonTest.esp and its scripts on the box with the plugins.txt line added
(tools/box/box_plugins.sh add): skythonbase depends on the plugin. Loads saves; never saves."""
from support import load_save, log_text

CELL = "skythontest.esp|0x000802"
QUEST = "SkythonTest.esp|0x000800"
NPC = "SkythonTest.esp|0x000804"
PLAYER_ID = 0x14
# README Contracts, Test ESP: the initial values skythonbase must hold (nothing writes a property before the save).
INITIAL = {"SkythonTestQuest": {"Counter": 7, "Ratio": 0.5, "Flag": True, "Label": "initial label"},
           "SkythonTestQuestBase": {"BaseCounter": 3, "BaseLabel": "base label"}}


def cell_of(game, ref) -> str:
    c = game.call_method(ref, "ObjectReference", "GetParentCell")
    return (c.form_key or "").lower() if c is not None else ""


def test_fresh_save_prefers_skythonbase(fresh_save, game):
    game.wait_ready(timeout=180)
    assert cell_of(game, game.resolve(form_id=PLAYER_ID)) == CELL
    assert "saveload: load started skythonbase" in log_text()


def test_skythonbase_is_pristine(game):
    load_save(game, "skythonbase")
    game.wait_ready(timeout=180)
    assert cell_of(game, game.resolve(form_id=PLAYER_ID)) == CELL
    npc = game.resolve(form_key=NPC)
    assert cell_of(game, npc) == CELL
    assert game.call_method(npc, "Actor", "IsDead") is False
    quest = game.resolve(form_key=QUEST)
    assert game.call_method(quest, "Quest", "IsRunning") is False
    assert game.call_method(quest, "Quest", "GetStage") == 0
    for script, props in INITIAL.items():
        for prop, value in props.items():
            assert game.get_property(quest, script, prop) == value, (script, prop)
    target = game.get_property(quest, "SkythonTestQuest", "Target")
    assert (target.form_key or "").lower() == NPC.lower()
    assert game.call_method(quest, "SkythonTestQuest", "GetCounter") == 7
    assert game.call_method(quest, "SkythonTestQuest", "GetBaseCounter") == 3
```
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
uv run --project python pytest livetest/test_m15_base_save.py -q
grep -n 'def fresh_save\|skythonbase' livetest/conftest.py; grep -n 'def load_save' livetest/support.py
```
Expected: `2 skipped`; the `fresh_save` fixture with its `skythonbase` preference and `def load_save` exist (Task 41). If `fresh_save` does not prefer `skythonbase`, STOP and report it as Task 41's gap (do not change the fixture here).

- [ ] **Step 2: The teardown list shows the base save**

In `tools/box/win/list.ps1`, add `skythonbase` to the throwaway-save header and regex (the line after `"== throwaway saves (`). Starting from the v1 text it becomes:
```powershell
"== throwaway saves (spikebase, spytest, s4test, skypy_r38_savecall, skythonbase) =="
Get-ChildItem (Join-Path $my 'Saves') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(spikebase|spytest|s4test|skypy_r38_savecall|skythonbase)' } | ForEach-Object { Show $_.FullName }
```
(If Task 41 already added `skython_`, keep it and add `skythonbase` beside it.) In `tools/box/test_box_tools.sh`, after check 6g's `list.ps1 lists skypy_r38_savecall` line, add:
```bash
grep -qE "Where-Object \{ \\\$_\.Name -match '\^\([^']*\|skythonbase[|)]" "$B/win/list.ps1" 2>/dev/null && ok "list.ps1 lists skythonbase" || bad "list.ps1 does not list skythonbase"
```
```bash
bash tools/box/test_box_tools.sh | grep -E 'skythonbase|^FAIL|BOX TOOLS'
```
Expected: `ok   list.ps1 lists skythonbase`, no `FAIL`, `BOX TOOLS OFFLINE PASS`. (Before the `list.ps1` edit the same command shows `FAIL list.ps1 does not list skythonbase`.)

- [ ] **Step 3: Commit**

```bash
git add livetest/test_m15_base_save.py tools/box/win/list.ps1 tools/box/test_box_tools.sh
git commit -m "test(live): base save skythonbase check; teardown list shows it"
```

- [ ] **Step 4: Preconditions on the box**

The game is not running. Install the updated `list.ps1`, check the name is free, add the plugin line:
```bash
tools/box/box_setup.sh | tail -3
tools/box/box_saves.sh count skythonbase
tools/box/box_plugins.sh backup && tools/box/box_plugins.sh add
```
Expected: `INSTALLED`; `0` (anything else: STOP); `BACKUP_SAME`, `ADDED`. (`box_saves.sh count` matches `skythonbase*.ess`, so `0` also means no `skythonbase2` or similar exists.)

- [ ] **Step 5: New game in the test cell, observing the lifecycle events**

This step's first live run is also the first test of loading the `skythonbase` save: later tasks load it routinely, so any problem on the load path shows up here first.

```bash
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
uv run --project python python - <<'PY'
import subprocess, sys, time
sys.path.insert(0, "livetest")
import skython
from support import REPO, load_session, raw_connect, read_event, subscribe

s = load_session()
raw = raw_connect(s)
for i, name in enumerate(("new_game", "game_loaded")):
    subscribe(raw, name, req_id=i + 1)
r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc SkythonTestCell"], capture_output=True, text=True, timeout=120)
print("console:", r.returncode, r.stdout.strip()[:200])
seen, deadline = [], time.monotonic() + 180
while time.monotonic() < deadline:
    ev = read_event(raw, timeout=5)
    if ev:
        seen.append((ev["event"], ev.get("epoch"), ev.get("data")))
        if ev["event"] == "game_loaded":
            break
print("events:", seen)
g = skython.connect(port=s.port, token=s.token, client_name="t49")
st = g.wait_ready(timeout=300)
player = g.resolve(form_id=0x14)
cell = g.call_method(player, "ObjectReference", "GetParentCell")
quest = g.resolve(form_key="SkythonTest.esp|0x000800")
npc = g.resolve(form_key="SkythonTest.esp|0x000804")
print("cell:", cell.form_key, "epoch:", st.get("epoch"))
print("quest running:", g.call_method(quest, "Quest", "IsRunning"), "stage:", g.call_method(quest, "Quest", "GetStage"))
print("npc dead:", g.call_method(npc, "Actor", "IsDead"), "npc cell:", g.call_method(npc, "ObjectReference", "GetParentCell").form_key)
print("counters:", g.call_method(quest, "SkythonTestQuest", "GetCounter"), g.call_method(quest, "SkythonTestQuest", "GetBaseCounter"))
g.close()
PY
tools/box/box_shot.sh out/t49-before-save.png
```
Expected: `console: 0 ...`; `events: [('new_game', <n>, {}), ('game_loaded', <n>, {'new_game': True})]` (a main-menu `coc` starts a new game: Task 40 raises `new_game` on `kNewGame`, then `game_loaded` with `new_game: true` at the interactive signal; this is the live check of both). If either is missing, or `game_loaded` carries `new_game: false`, record it in Step 8 as a **Task 40 defect for the owner** (with the `events:` line and the `Skython.log` lifecycle lines) and still make the save (the base save does not depend on the events); `cell: SkythonTest.esp|0x000802`; `quest running: False stage: 0`; `npc dead: False npc cell: SkythonTest.esp|0x000802`; `counters: 7 3` (the initial values: read-only calls, nothing written); the screenshot shows the player in the lit test room with the NPC. A different cell, a running quest, a dead NPC or other counters: do not save; STOP. If the heredoc dies in `wait_ready` with `NotReady` (the lifecycle never reported interactive after a main-menu `coc`), that is a Task 40/41 lifecycle finding: record it with the `events:` line and `Skython.log`, do not save, do not try another route; STOP and report.

- [ ] **Step 6: Save once, then confirm**

```bash
tools/box/box_saves.sh count skythonbase
uv run --project python python - <<'PY'
import sys
sys.path.insert(0, "livetest")
import skython
from support import load_session
s = load_session()
with skython.connect(port=s.port, token=s.token, client_name="t49-save") as g:
    print("saved:", g.save("skythonbase"))
PY
tools/box/box_wait_log.sh Skython.log "saveload: saved skythonbase" 120
# game.save answers once the engine started the save; the .ess can appear a moment later: poll, bounded (60 s).
n=0; for i in $(seq 1 30); do n="$(tools/box/box_saves.sh count skythonbase)"; [ "$n" = 1 ] && break; sleep 2; done; echo "count after save: $n"
tools/box/box_quit.sh || tools/box/box_stop.sh
tools/box/box_session.sh down
```
Expected: `0` (re-checked just before saving); `saved: skythonbase` (the engine started the save); `MATCH ... saveload: saved skythonbase`; `count after save: 1` (anything else after the 60 s poll: STOP); the quit tool ends with `RESTORED <sha>`. `game.save` refuses an existing name (`bad_request`, `save_exists`), so a second run of this step cannot overwrite it.

- [ ] **Step 7: The fixture lands on `skythonbase`**

```bash
tools/box/box_plugins.sh add
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_base_save.py -v -rs
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_esp_smoke.py -q -rs -k "not coc"
tools/box/box_quit.sh || tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_teardown_list.sh | grep -A3 'throwaway saves'
```
Expected: `test_m15_base_save.py` 2 passed (loads from the main menu: `game.load` is allowed there); Task 48's smoke file passes on top of `skythonbase` (every test but the deselected `coc` one passes); the quit tool restores `plugins.txt`; the teardown list shows `skythonbase.ess` (and its `.skse`). Neither test saved anything (`box_saves.sh count skythonbase` still `1`).

- [ ] **Step 8: Record and commit**

Append to `spikes/RESULTS.md`:
```markdown
- **Task 49, base save skythonbase (box, Windows-native, <date time>, Skyrim 1.6.1170.0; 2 launches; SKSE plugins loaded: <list>):** `box_saves.sh count skythonbase` 0 before, 1 after. From the main menu, `coc SkythonTestCell` (DevBench console) -> player in SkythonTest.esp|0x000802, quest |0x000800 not running at stage 0, SkythonTestNPC |0x000804 alive in the cell; events seen during the coc: <new_game, then game_loaded epoch n new_game true | what was seen instead: Task 40 defect recorded for the owner>. `game.save("skythonbase")` -> `saveload: saved skythonbase`. Launch 2: `test_m15_base_save.py` 2 passed (`fresh_save` loads skythonbase: `saveload: load started skythonbase`), `test_m15_esp_smoke.py` passed on it (`coc` test deselected); initial property values as in the README Contracts. **skythonbase depends on SkythonTest.esp: add the plugins.txt line (box_plugins.sh add) before every launch that loads it.** **Owner removal list:** save `skythonbase` (.ess and .skse) in `C:\Users\geoff\Documents\My Games\Skyrim Special Edition\Saves`.
```
```bash
git add spikes/RESULTS.md
git commit -m "docs(results): Task 49 base save skythonbase"
```
