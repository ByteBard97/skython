# Task 57: Docs, RESULTS, spec name mapping, local tag `m2`

Closes the plan: the README describes events, `game.save`/`game.load`, script settings and console echo, the test ESP for developers and the limits that stand (R45, R57/R58, object-array arguments); `PROTOCOL.md` gets a final consistency check; `spikes/RESULTS.md` gets an M2 summary and the consolidated removal list; the spec's text uses the shipped event names (and, only if the owner accepted decision 8, the generic acceptance framing); `docs/PINS.md` lists the new pinned artefacts; one final full live run; then the local annotated tag `m2`, never pushed.

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Owner decisions** (decision 8 gates Step 4's framing edits; decision 5: this task chooses no licence option), **Contracts** (events table and the name mapping under it, Test ESP) and **Gates** first. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start only after Task 56 is done (and Tasks 33-55 before it), each with its RESULTS entry.
>
> **Runs on:** Linux (docs). Step 6 runs the game on the Windows box.
>
> **STOP and ask the owner (do not improvise) if:** any test fails in the final run, or skips for a reason other than the expected opt-ins listed in Step 6 (a `SKYTHON_LIVE` or missing `SPY_*` skip is a failure); `tools/gen_protocol.py --check` reports drift; a tracked build output is missing from git, or `SkythonTest.esp` differs from its recorded SHA-256, or a `.pex` from its recorded stable hash (Step 5); the tag `m2` already exists; owner decision 8 has no recorded answer when Step 4 is reached (a STOP gate, index **Owner decisions**: the recommendation in `OWNER-DECISIONS-2026-10-02.md` is not an answer); a README or spec edit would choose a licence option for the DLLs (owner decision 5).

**Files:**
- Modify: `README.md`, `docs/specs/2026-09-30-skyrim-python-design.md`, `spikes/RESULTS.md`, `docs/PINS.md`, `livetest/results/new_saves.txt` and `livetest/results/binding_persistence.json` (rewritten by the final run, Step 6)
- Modify only if Task 44 adopted attach on demand: `protocol/PROTOCOL.md` (one sentence in the events section, Step 2)
- Check only (Task 33 owns them; report, do not edit): `protocol/protocol.json`, the generated files, and the rest of `protocol/PROTOCOL.md`

**Interfaces:**
- Consumes: every RESULTS entry of Tasks 33-56 (Task 44's sink-detach outcome, Task 52's probe verdicts, Task 56's soak summary, every save and box file recorded); `livetest/results/soak.json`; the owner's recorded answer to decision 8 (the owner's reply, `.superpowers/sdd/README/progress.md`, or the controller's brief); the index's name mapping (spec 5 `game.loaded` → `game_loaded`; spec 4.6 "dying and death" → `dying`/`death`; one name per direction for equip/unequip, cell attach/detach, quest start/stop, menu open/close; "player location change" → `player_location`; "mod events" → `mod_event`; `Skython.Send` delivers `channel`).
- Produces: the M2 README; spec draft 5; the RESULTS M2 summary and removal list; PINS entries; the local annotated tag `m2`.

- [ ] **Step 1: README**

Edit `README.md` section by section (keep any paragraph Tasks 46 or 48 added, merged into the matching new section without duplicating it).

(a) Replace the Status paragraph (`Status: M1 (embedded dispatcher). ... packaging for players in M4.`) with:
```markdown
Status: M2 (events, save and load). Verified on Skyrim AE 1.6.1170, Windows-native. Proton (the game on Linux) has not been tested yet. The plugin does not start Python on any other runtime. Typed bindings arrive in M3, packaging for players in M4.
```
(b) In the Install layout block, after the `Skython.ini` line add:
```
Data/Scripts/Skython.pex               Papyrus script Skython  plugin/papyrus/compiled/Skython.pex (Skython.Send)
```
(c) After the "Third-party packages go in `site-packages/` ..." paragraph and before `## Examples`, insert:
````markdown
## Events

A script subscribes with a callback. Callbacks run on one Skython thread per script, in the order the game raised the events, and may make game calls:
```python
from skython.values import Form

PLAYER = Form(form_id=0x14)

def setup(game):
    game.on_death(None, lambda e: print(e.data["victim"], "killed by", e.data["killer"]))   # None: every actor
    game.on("hit", {"target": [PLAYER]}, lambda e: print("hit by", e.data["aggressor"]))
    game.on_channel("my_mod", lambda e: print(e.data["payload"]))     # Skython.Send("my_mod", ...) in Papyrus
    game.on_game_loaded(lambda e: print("a save finished loading"))
```
Each `on...` call returns a `Subscription`; `close()` it to stop (once `close()` returns, its callback is not called again). Subscribing works before a save is loaded, subscriptions stay across loads, and closing a script's `Game` closes them. `event.data` holds form references (`Form`), numbers, booleans and strings, never engine pointers; `event.epoch` says which loaded game raised the event.

| Event | Data | Notes |
|---|---|---|
| `death`, `dying` | `victim`, `killer` (or `None`) | the engine's death event, split by its `dead` flag |
| `hit` | `target`, `aggressor`, `source`, `projectile`, `power_attack`, `sneak_attack`, `bash_attack`, `blocked` | needs a filter on `target` or `aggressor` |
| `equip`, `unequip` | `actor`, `item` (base object), `reference` | |
| `container_changed` | `old_container`, `new_container`, `item`, `count`, `reference` | needs a filter on `old_container`, `new_container` or `item` |
| `cell_attach`, `cell_detach` | `reference` | `cell_attach` can repeat for one reference |
| `quest_stage` | `quest`, `stage` | `game.on_quest_stage(quest, fn, stage=...)` |
| `quest_start`, `quest_stop` | `quest` | |
| `activate` | `target`, `activator` | |
| `player_location` | `old_location`, `new_location` | the player only |
| `menu_open`, `menu_close` | `menu` | |
| `mod_event` | `name`, `str_arg`, `num_arg`, `sender` | Papyrus `SendModEvent`; `game.on_mod_event(name, fn)` |
| `channel` | `channel`, `payload`, `truncated` | Papyrus `Skython.Send(channel, payload)`; `game.on_channel(channel, fn)` |
| `game_loaded` | `new_game` | a save finished loading, or a new game started, and the game is interactive |
| `game_saved` | `name` | |
| `new_game` | (nothing) | |

- **Filters** are equality sets: `{"victim": [a, b]}` matches either; several fields must all match. Forms match by form id, strings ignoring case.
- **A slow callback loses the oldest events, never the game's time.** Each subscription has its own queue (`max_queue=`, default 1024, at most 65536). When it is full the oldest waiting event is dropped and the next delivered event says how many (`event.dropped`; the gaps in `event.seq` are the same drops). The game never waits for Python.
- **While a save loads** (or a new game starts), events are not queued, except `menu_open`/`menu_close`, `mod_event`, `channel` and the three game events; the subscription's next delivered event says how many were skipped (`event.suppressed`). `game_loaded` marks the end of the load. The loading screens of doors, fast travel and `coc` suppress nothing: `player_location`, `cell_attach` and `cell_detach` arrive as usual. Compare `event.epoch` with the `game_loaded` event's epoch to tell which loaded game an event belongs to; do not rely on its order relative to `game_loaded`.
- **From Papyrus to Python:** `Skython.Send(string channel, string payload)` is a native global function of the Papyrus script `Skython` (`Data/Scripts/Skython.pex`; compile your scripts with `plugin/papyrus/Skython.psc` on the import path). A payload over 64 KiB is cut and `truncated` is then true. A channel name is 1 to 256 bytes: an empty or longer one drops the call (one warning in `Skython.log`), it is never cut. Channel names match ignoring case. See [`examples/papyrus_to_python.py`](examples/papyrus_to_python.py).
- [[Task 44 outcome: write exactly one of these two sentences, chosen by Task 44's RESULTS verdict. Probe FAIL (skip-only sinks): "Event hooks stay registered for the whole session and do nothing while an event has no subscriber." Probe PASS (attach on demand): "An event's engine hook is removed while nobody subscribes to it and restored on the next subscription. Events the game raises between the moment `on(...)` returns and the moment the hook is restored (normally within one frame) are not seen and are not counted in `dropped` or `suppressed`; subscribe a moment before the action you want to observe."]]

## Save and load

```python
game.save("my_mod_checkpoint")   # returns once the game has accepted the save; the file can appear a moment later
state = game.load("my_mod_checkpoint")      # returns once the loaded game is interactive
```
- Save names are 1 to 64 letters, digits and underscores. A name that already exists, or whose save is still being written, is refused (`BadRequest`, `detail["reason"] == "save_exists"`); Skython never overwrites a save.
- `game.load` right after `game.save` can raise `NotReady` (`detail["reason"] == "saving"`) or `NotFound` while the file is still being written; retry briefly.
- `game.load` also works from the main menu. It returns the new `game.state()` (its `epoch` is one higher); `wait=False` returns as soon as the load has started. Calls in flight when a load starts end with `NotReady`.
- After a load, forms defined by plugins stay valid. A reference created at runtime (a `0xFF...` form id, for example from `PlaceAtMe`) stays valid only while it still names the same object; otherwise using it raises `StaleReference` or `NotFound`. Subscriptions carry on, and `game_loaded` fires.
- Skython writes nothing into saves: state that must survive belongs in the game (quest stages, aliases, script properties, globals) or in your own files.

## Script settings and errors

A script can have a settings file, `scripts/<name>.toml` (for a package script also `scripts/<name>/settings.toml`; the first that exists is used). It is read with Python's `tomllib` before `setup(game)` runs and is available, read-only, as `game.settings`:
```python
def setup(game):
    channel = game.settings.get("twitch", {}).get("channel", "")
```
A settings file that does not parse marks its script as failed (`python.log` says why) and skips its `setup`; other scripts load normally. When a script fails, one line also appears in the in-game console (`Skython: script <name> failed: ...; see python.log`), at most one per second; `console_echo_errors = false` in `Skython.ini` turns that off.
````
(d) Replace the Examples paragraph with:
```markdown
See [`examples/`](examples/): Twitch chat spawning named mudcrabs, talking to NPCs through a language model, a live stats overlay for OBS, real-world weather sync, a kill tracker, your own Papyrus script reporting to Python with `Skython.Send`, and a quest-stage notifier, each in one Python file.
```
(e) At the end of the bridge paragraph that ends `... (this project's own tests reach the Windows test machine from Linux that way).` add: ` The bridge protocol is 1.1 (events and save/load); a 1.0 client still connects. Events reach a bridge client exactly as they reach a script.`
(f) In "Limits to know about": replace the **Aliases.** bullet with these two bullets
```markdown
- **Aliases.** Only aliases that carry a script (`ReferenceAlias`, `LocationAlias`, or a script extending them) work as call targets, arguments and results; a plain alias is `TypeMismatch` (Skython never attaches a script on demand, because that would leak into saves). An alias argument passes the script object already bound to that alias. An alias whose id is 0xFFFF would decode as its quest (form handles carry 0xFFFF in the high 32 bits; one quest sampled).
- **Bindings are saved.** Calling a method on a form or alias binds it in the Papyrus VM, as any Papyrus script would; that binding is saved (measured, Task 42). Scripts that touch many thousands of distinct forms grow the save accordingly.
```
(The new **Bindings are saved.** bullet may quote Task 56's `binding_growth` numbers in one clause; no bracket left.)
in the **Call cost.** bullet replace `the bridge costs a second drain, cause not yet measured` with Task 38's one-line finding from its RESULTS entry (for example `the bridge costs a second drain because <Task 38's measured cause>`; no bracket left); append to the **Properties.** bullet: ` Inherited properties work (tested on a script whose parent script has its own properties).`; and replace the bullet `- Not in M1: events, and loading or saving the game from Python (\`game.load\` / \`game.save\`). Both come in M2.` with
```markdown
- **Events** are delivered at most once and can be dropped when a callback falls behind or a save is loading (see Events); there is no history, so subscribe before the moment you care about. A callback that raises is logged, and the next event still arrives.
```
(g) In "Build and test", after the live-tests bullet add:
```markdown
- Test plugin (development only, never shipped): `testesp/build.sh` rebuilds `testesp/SkythonTest.esp` with a small Mutagen program in `testesp/builder/` (Mutagen is GPL-3.0-only, so the builder is a separate development tool that is never distributed) and compiles the Papyrus sources with `tools/papyrus_compile.sh` (the Papyrus Compiler under Wine, headless); `docs/PINS.md` holds the ESP's SHA-256 and each `.pex`'s stable hash (a `.pex` embeds compile times, user and machine, so its hash is taken with the times zeroed and matches only on the machine and account that built it). On the test machine: `tools/box/box_deploy.sh --esp testesp/SkythonTest.esp` and `--test-pex` for its scripts, and `tools/box/box_plugins.sh add` before each launch that needs the plugin (`plugins.txt` is backed up first and restored by `box_quit.sh`/`box_stop.sh`).
- Live tests run in two invocations, each after `source out/box-live.env && source livetest/s4test.env` and with `SKYTHON_LIVE=1 SKYTHON_BOX=1`: first the M1 suite and the M2 modules that run on any loaded save, on `s4test` (with `plugin/config/variants/trace-timing.ini` deployed, which two of them need): `uv run --project python pytest livetest/test_m1_*.py livetest/test_m2_events_batch1.py livetest/test_m2_events_batch2.py livetest/test_m2_lifecycle.py livetest/test_m2_console_settings.py livetest/test_m2_sink_detach.py -q -rs --deselect livetest/test_m1_mainmenu.py --deselect livetest/test_m1_throughput.py`; then the modules whose `fresh_save` fixture loads the save `skythonbase` (in the test plugin's cell; `tools/box/box_plugins.sh add` before the launch): `uv run --project python pytest livetest/test_m2_saveload.py livetest/test_m15_esp_smoke.py livetest/test_m15_send.py livetest/test_m15_base_save.py livetest/test_m15_own_script.py livetest/test_m15_properties.py livetest/test_m2_events_esp.py livetest/test_m2_examples.py livetest/test_acceptance_scenario.py livetest/test_m2_soak.py --deselect livetest/test_m15_esp_smoke.py::test_coc_into_the_test_cell -q -rs`. Throughput, drain timing, the engine probes and the ESP spike test run on purpose, not routinely. The 30-minute soak is opt-in (`SPY_SOAK=1`); its last result is `livetest/results/soak.json`. The engine probes are written up in `docs/probes/`.
```
Fill the one `[[...]]` from Task 44's RESULTS entry, then check:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -n '\[\[' README.md || echo "README filled"
grep -rni 'skyrim-dm\|skyrim_dm' README.md examples/ docs/ --exclude-dir=specs --exclude-dir=superpowers --exclude-dir=research || echo "generic"
grep -n "Not in M1" README.md || echo "M1 caveat gone"
```
Expected: `README filled`, `generic`, `M1 caveat gone`.

- [ ] **Step 2: PROTOCOL.md final check (report only)**

```bash
uv run --project python python tools/gen_protocol.py --check
grep -n '"protocol"\|\[1, *1\]' protocol/PROTOCOL.md protocol/v1/fixtures/hello.json | head
for e in death dying quest_stage quest_start quest_stop mod_event channel hit equip unequip container_changed \
         cell_attach cell_detach activate player_location menu_open menu_close game_loaded game_saved new_game; do
  grep -q "^| \`$e\` " protocol/PROTOCOL.generated.md || echo "MISSING $e"
done
grep -n "dropped\|suppressed\|consumer" protocol/PROTOCOL.md | head
```
Expected: no drift; `[1, 1]` in PROTOCOL.md and the hello fixture; no `MISSING` line (the generated events table has all 20 events of the index); the events section explains `dropped`, `suppressed` and the consumer beside the request. A mismatch: STOP and report (Task 33 owns these files).

Only if Task 44's RESULTS entry says the probe PASSed and attach on demand was adopted, append this sentence to the end of PROTOCOL.md's events section (a protocol fact, not a protocol change; `protocol.json` is untouched, so `gen_protocol.py --check` still reports no drift):
```markdown
An event whose engine hook was detached (no subscriber) is re-attached by an SKSE task after `events.subscribe` answers; events the game raises between that answer and the attach are not seen and are not counted in `dropped` or `suppressed`.
```
On FAIL (skip-only sinks) PROTOCOL.md is not edited.

- [ ] **Step 3: RESULTS: M2 summary and removal list**

```bash
tools/box/box_teardown_list.sh > out/teardown-m2.txt; cat out/teardown-m2.txt
tools/box/box_saves.sh count skython_
grep -n "removal list\|Removal list" spikes/RESULTS.md
```
Append to `spikes/RESULTS.md` (filled from the task entries, no bracket left):
```markdown
## M2 summary (plan 2026-10-02-skython-m1_5-m2, Tasks 33-57)

- Events: <n> v1 events live (Tasks 39-40), filters, drop-oldest queues and the loading rule verified (Tasks 34-37, 40, 42, 53); flood and soak: <Task 40 hit flood one line>; <Task 56 one line>.
- Save and load: `game.save`/`game.load` through `AddUITask` (Task 41); lifecycle and binding persistence (Task 42): <one line each>.
- Sink detach (Task 44): <PASS/FAIL, one line>. Console echo and script settings (Task 43): <one line>. Two-drain experiment (Task 38, record only): <one line>.
- Test ESP (Tasks 45-49): built on Linux with Mutagen <version> and the headless Papyrus Compiler; `skythonbase` made. Own-script and alias arguments, LocationAlias (Task 50), property parent offset (Task 51): PASS. Probes (Task 52): 1 <verdict>, 2 <verdict>, 3 <verdict> (`docs/probes/`).
- Acceptance (Task 55): <PASS through the bridge and in-process; owner decision 8: answer>. Examples (Task 54) live.
- Final full run (Task 57 Step 6): launch check <counts>; invocation 1 (M1 suite and Part E modules on `s4test`, `trace-timing.ini`) <counts>; invocation 2 (`fresh_save` modules on `skythonbase`) <counts>.

## Owner removal list (M1.5/M2 plan)

Nothing here is removed without the owner's itemised yes.
- Saves on the box: <every skython_* save named in Tasks 38-57, one per line>; `skythonbase.ess` (Task 49; keep while the live tests use it).
- Box files: <every path from out/teardown-m2.txt this plan added: Data\SkythonTest.esp, the six .pex files (Skython.pex, SkythonTestQuestBase.pex, SkythonTestQuest.pex, SkythonTestAlias.pex, SkythonTestLocationAlias.pex, QF_SkythonTestQuest_01000800.pex), scripts\_testesp.py, scripts\livetest_events\, scripts\livetest_scenario\, scripts\livetest_settings\, the three example scripts, plugins.txt.bak>.
```
The number of saves listed (excluding `skythonbase`) must equal `box_saves.sh count skython_` minus any `skython_*` save that existed before this plan (none expected); if not, find the missing name in the task entries before going on.

- [ ] **Step 4: Spec text (name mapping; decision 8 framing only if accepted)**

In `docs/specs/2026-09-30-skyrim-python-design.md`, make these replacements (each old text appears once):

1. `Date: 2026-10-01. Status: draft 4. Supersedes draft 3 (2026-09-30), whose` → `Date: 2026-10-02. Status: draft 5 (draft 4 plus the M2 event names and API). Draft 4 superseded draft 3 (2026-09-30), whose` (the rest of the line, about the out-of-process design and its branch, stays).
2. Before `## Changes from draft 3` insert:
```markdown
## Changes from draft 4

1. Event names are fixed (4.6): one snake_case name per event and direction, `game_loaded` instead of `game.loaded` (5); `Skython.Send` delivers a `channel` event.
2. `_skython.next_events` takes the consumer it drains (one per player script's `Game` and one per bridge connection); event frames carry `dropped` and `suppressed` counts (4.6).
3. The v1 call surface lists the event helpers, `wait_for`, `save`, `load` and `settings` as shipped (4.7).
4. The test ESP is built with Mutagen (open question 4 settled).
```
(Only if decision 8 was accepted, add two more items: `5. The v1 acceptance test is a generic scenario on the test ESP (4.6, 8, 9); open question 5 is settled.` and `6. The acceptance scenario no longer calls \`SetObjectiveText\`: the objective text with its alias tags comes from the test ESP's objective record (probe 1); PO3 \`SetObjectiveText\` with alias tags is checked separately by probe 2 (Task 52).`)
3. In 4.6, `A Python **event dispatch thread** blocks on \`_skython.next_events(timeout)\` with the GIL released and runs callbacks in order; a callback may make blocking game calls. Over the bridge, the same events are pushed as \`{event, sub, seq, epoch, data}\` frames.` → `A Python **event dispatch thread** per \`Game\` blocks on \`_skython.next_events(consumer, timeout)\` with the GIL released and runs callbacks in order; a callback may make blocking game calls. Over the bridge, the same events are pushed as \`{type: "event", event, sub, seq, epoch, data}\` frames, with \`dropped\` and \`suppressed\` counts when they are not zero.`
4. In 4.6, `- **v1 events:** dying and death (from \`TESDeathEvent\`'s \`dead\` flag, exposed separately), hit, equip/unequip, container changed, cell attach/detach, quest stage, quest start/stop, player location change, activate, menu open/close, game loaded (the interactive signal), game saved, new game, and **mod events**.` → `- **v1 events:** \`dying\` and \`death\` (from \`TESDeathEvent\`'s \`dead\` flag, exposed separately), \`hit\`, \`equip\`/\`unequip\`, \`container_changed\`, \`cell_attach\`/\`cell_detach\`, \`quest_stage\`, \`quest_start\`/\`quest_stop\`, \`player_location\`, \`activate\`, \`menu_open\`/\`menu_close\`, \`game_loaded\` (the interactive signal), \`game_saved\`, \`new_game\`, \`mod_event\` and \`channel\`. Events raised while a save loads or a new game starts are suppressed except menus, \`mod_event\`, \`channel\` and the three game events; door, fast-travel and \`coc\` loading screens suppress nothing (\`protocol/PROTOCOL.md\`).`
5. In 4.6, `delivers \`{channel, payload}\` to subscribers of \`channel\`.` → `delivers a \`channel\` event \`{channel, payload, truncated}\` to subscribers of that channel.`
6. In 4.7, `event helpers (\`game.on_death(actor, fn)\`, \`game.on_mod_event(name, fn)\`, \`game.on_channel(channel, fn)\`)` → `\`game.on(event, filter, fn)\` and helpers (\`on_death\`, \`on_dying\`, \`on_mod_event\`, \`on_channel\`, \`on_quest_stage\`, \`on_game_loaded\`), \`game.save(name)\`, \`game.load(name)\`, \`game.settings\``
7. In 5, `` `game.loaded` fires at the interactive signal`` → `` the `game_loaded` event fires at the interactive signal``.
7a. In 4.2, `pushed events \`{event, sub, seq, epoch, data}\`` → `pushed events \`{type: "event", event, sub, seq, epoch, data}\` (plus \`dropped\` / \`suppressed\` when not zero)`.
7b. In 4.3, `` `events.subscribe {event, filter}` → subscription id`` → `` `events.subscribe {event, filter, max_queue}` → `{sub, event, max_queue}` ``.
7c. Open question 4 (`4. ESP authoring tool for \`SkythonTest.esp\` (M1.5): Mutagen, the Creation Kit, or xEdit.`) → `4. Settled: Mutagen (Task 47's spike PASSed); the builder is a GPL-3.0-only development tool in \`testesp/builder/\`, never distributed.`

Items 8-11 change spec sections 4.6, 8 and 9 and open question 5. First read the owner's recorded answer to decision 8: no recorded answer → STOP (Owner decisions); accepted → apply them with the names it settled (below as proposed); rejected → leave all four places as they are and apply only items 1-7:
8. In 4.6, `This is how skyrim-dm's pre-authored dialogue fragments, quest stage fragments and trigger boxes report \`npc_told\`, \`npc_confronted\` and "location entered".` → `This is how a mod's dialogue fragments, quest stage fragments and trigger boxes report to Python.`
9. In 8, the bullet starting `- **skyrim-dm acceptance test**, against the test ESP:` → `- **Generic acceptance scenario**, against the test ESP: one \`call_method\` of \`SkythonTestQuest.SetupScenario(npc, item, place, label)\` starts the quest, fills three aliases and displays the objective (its text, with three alias tags, is the ESP's objective record, probe 1; PO3 \`SetObjectiveText\` with alias tags is probe 2); an item is placed; killing the target (alias 0) raises a death event, the test ESP's death rule sets stage 100 and its stage fragment calls \`Skython.Send("skython_test", "stage_100")\`; the test reads the stage, objective state and alias fills back. Run once through the bridge and once in-process as a player script (\`livetest/test_acceptance_scenario.py\`). Consumers keep their own integration tests in their own repositories.`
10. In 9, `**v1 = M0, M1, M1.5, M2, and the skyrim-dm acceptance test.**` → `**v1 = M0, M1, M1.5, M2, and the generic acceptance scenario.**`; in the M1.5 bullet `a quest script with \`SetupContract(...)\`` → `a quest script with \`SetupScenario(...)\`` and `Results to \`docs/probes/\` for skyrim-dm. Which repo owns skyrim-dm's production shell quest is decided with skyrim-dm's integration work before this milestone starts.` → `Results in \`docs/probes/\`.`; the bullet `- **skyrim-dm acceptance test** (section 8).` → `- **Generic acceptance scenario** (section 8).`
11. Open question 5 → `5. Settled (owner decision 8, 2026-10-02): a consumer's production quest and its Papyrus script live in that consumer's own repository; Skython's test ESP is test data only.`

Check:
```bash
grep -n "game\.loaded\|next_events(timeout)\|{event, filter}\|Mutagen, the Creation Kit, or xEdit" docs/specs/2026-09-30-skyrim-python-design.md || echo "names mapped"
grep -n "draft 5" docs/specs/2026-09-30-skyrim-python-design.md | head -2
```
Expected: `names mapped`, then the date line and the Changes heading.

- [ ] **Step 5: PINS and tracked build outputs**

```bash
git ls-files testesp/SkythonTest.esp plugin/papyrus/compiled testesp/compiled
testesp/build.sh --check | tail -1
tools/papyrus_compile.sh --stable-hash plugin/papyrus/compiled/Skython.pex
grep -n "SkythonTest.esp\|Skython.pex\|SkythonTest.*pex\|QF_SkythonTestQuest\|Mutagen\|8.0.424" docs/PINS.md
~/.dotnet/dotnet --version
grep -n "Mutagen.Bethesda.Skyrim" testesp/builder/*.csproj
```
Expected: git lists the ESP and all six `.pex` (`plugin/papyrus/compiled/Skython.pex` and the five test scripts; Task 46's `.gitignore` negations make them tracked; any missing: STOP); `TEST ESP BUILD MATCHES PINS` (the ESP's raw SHA-256 and each test `.pex`'s stable hash equal their `- pin` lines); `Skython.pex`'s stable hash equals `36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341` (index Contracts) and its `docs/PINS.md` line; `8.0.424` and `23.4.0`. `.pex` files are never compared by raw SHA-256 (they embed compile times, user and machine). Add whatever line is missing to `docs/PINS.md` under a heading `## Test ESP and Papyrus (M1.5/M2 plan)`:
```markdown
- `testesp/SkythonTest.esp` SHA-256 `<hash>` (Task 48 build); `plugin/papyrus/compiled/Skython.pex` stable hash `<hash>` (Task 46); `testesp/compiled/<name>.pex` stable hash `<hash>` for each of the five test scripts (stable hash: `tools/papyrus_compile.sh --stable-hash`, times zeroed, reproducible on the same machine and account only).
- Built with .NET SDK 8.0.424 (`~/.dotnet/dotnet`) and `Mutagen.Bethesda.Skyrim` 23.4.0 (GPL-3.0-only; restored offline from `~/.nuget/packages`); Papyrus Compiler from the SimpleSkyrim copy under GE-Proton10-14's `wine64`, headless, with `TESV_Papyrus_Flags.flg` (`tools/papyrus_compile.sh`).
```
```bash
grep -n "<hash>" docs/PINS.md || echo "PINS filled"
```
Expected: `PINS filled`.

- [ ] **Step 6: Final full run**

Linux suites first:
```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
ctest --test-dir core/build --output-on-failure
uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q
(cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q)
tools/box/test_box_tools.sh
testesp/build.sh --check
git status --short
```
Expected: all pass; `testesp/build.sh` reports the hashes match `docs/PINS.md`; `git status` shows only this task's edits.

Then the game, as **two suite invocations** on two saves: invocation 1 runs the M1 suite and the Part E modules that do not use `fresh_save` (they run on whatever save is loaded) on `s4test`; invocation 2 runs the M1.5/M2 modules whose `fresh_save` fixture loads `skythonbase` (Tasks 41 and 48-56). Only Tasks 41 and 49-56 use `fresh_save`; `test_m2_lifecycle.py` loads `s4test` and its own throwaway saves itself. The main-menu test runs once right after launch, before any load, as the launch check (it cannot share an invocation with tests that need a loaded game). Tasks 42 and 44 restored `Skython.dev.ini` when they finished, but `test_m2_lifecycle.py::test_packhandle_and_getalias_binding_across_a_save` (asserts `bound before call:` log lines) and `test_m2_sink_detach.py::test_probe_register_unregister_cycles` (`_skython._probe_sink_cycles` exists only with `trace_timing`) need `trace_timing`, so the run deploys `trace-timing.ini` before the launch (it changes nothing else any test checks) and puts the dev ini back after `box_stop.sh`. The deployed build is the one Task 56 ran; redeploy the package or scripts only if `git log` shows plugin or package changes since:
```bash
tools/box/box_deploy.sh --package && tools/box/box_deploy.sh --scripts livetest/scripts
tools/box/box_deploy.sh out/Skython.dll plugin/config/variants/trace-timing.ini   # Tasks 42 and 44 need trace_timing; no effect on other tests
tools/box/box_plugins.sh add                      # before the launch: invocation 2 loads skythonbase (Global Constraints)
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log 'python host: running' 600
tools/box/box_wait_log.sh Skython.log 'lifecycle: Main Menu open' 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
export SKYTHON_LIVE=1 SKYTHON_BOX=1
uv run --project python pytest livetest/test_m1_mainmenu.py -q -rs              # launch check: at the main menu, before any load
uv run --project python python -c "import skython; g = skython.connect(); print(g.load('s4test', wait=True)['epoch']); g.close()"
# Invocation 1: the M1 suite and the Part E modules that run on any loaded save, on s4test
uv run --project python pytest -q -rs livetest/test_m1_*.py \
  livetest/test_m2_events_batch1.py livetest/test_m2_events_batch2.py livetest/test_m2_lifecycle.py \
  livetest/test_m2_console_settings.py livetest/test_m2_sink_detach.py \
  --deselect livetest/test_m1_mainmenu.py --deselect livetest/test_m1_throughput.py
# Invocation 2: the modules whose fresh_save fixture loads skythonbase (the plugins.txt line was added before launch)
uv run --project python pytest -q -rs \
  livetest/test_m2_saveload.py livetest/test_m15_esp_smoke.py livetest/test_m15_send.py livetest/test_m15_base_save.py \
  livetest/test_m15_own_script.py livetest/test_m15_properties.py livetest/test_m2_events_esp.py \
  livetest/test_m2_examples.py livetest/test_acceptance_scenario.py livetest/test_m2_soak.py \
  --deselect livetest/test_m15_esp_smoke.py::test_coc_into_the_test_cell
tools/box/box_files.sh pull skse64.log Skython.log Skython/python.log
grep -E "loaded correctly|no version data|disabled" out/box-skse/skse64.log
grep -c "timing: id=" out/box-skse/Skython.log                       # > 0: trace-timing.ini was in force
grep -E "(\bERROR\b|\berror:|\bcritical\b)" out/box-skse/Skython.log | grep -v console_echo_errors | grep -v livetest_broken || echo "no unexpected errors"
tools/box/box_stop.sh
tools/box/box_session.sh down
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini   # back to the dev ini
tools/box/box_plugins.sh show
git status --short
```
Not run here, on purpose: `test_m1_throughput.py` and `test_m2_drain_timing.py` (they rewrite the recorded results files the README and RESULTS quote), `test_m15_probes.py` (its verdicts are recorded; a rerun adds a save and screenshots for nothing), `test_m15_esp_spike.py` (the spike ESP's `spike_stage_10` fragment was replaced by Task 48's build, so it cannot pass against the deployed ESP), and `test_m15_esp_smoke.py::test_coc_into_the_test_cell` (a `coc` in the middle of the invocation moves the player out of the cell later modules' `fresh_save` reload restores anyway; it passed in Task 48 and adds nothing here). `test_m2_soak.py` runs only its diagnostics test; the soak itself skips (opt-in).

Expected: everything passes; `grep -c "timing: id="` prints a number above 0 (the plugin log has `timing:` lines, so `trace-timing.ini` was deployed for invocation 1). The error grep prints `no unexpected errors`, or only lines a test caused on purpose and whose text that test asserts (a refusal or failure the test provokes); the deliberately broken script's lines (`livetest_broken`, Task 43) and the `console_echo_errors` config line are already filtered out. Name each remaining match and the test that caused it in the RESULTS line; any match no test explains is a STOP. The skip report (`-rs`) lists only: the low-cap test, the two `SPY_MANUAL` tests, the opt-in save test (`SPY_SAVE_TEST`), the eight startup variants, the soak (`SPY_SOAK`), and any other opt-in a Part E task declared with its own `SPY_*` switch (check each reason names a setup, not a missing value). Tests that save make new `skython_*` saves: add their names to the removal list (Step 3). `show` prints the backup. `git status --short` shows only this task's edits plus exactly two files the run rewrote: `livetest/results/new_saves.txt` (the new save names, appended by `new_save_name`) and `livetest/results/binding_persistence.json` (rewritten by `test_m2_lifecycle.py`); any other tracked file changed by the run is a STOP.

Fill the "Final full run" line of the M2 summary with the pass/skip counts of the launch check and the two suite invocations (invocation 1 on `s4test`, invocation 2 on `skythonbase`).

- [ ] **Step 7: Commit and tag (local only; never push)**

```bash
sed -n '/## M2 summary/,$p' spikes/RESULTS.md | grep -n "<[a-zA-Z]" || echo "RESULTS filled"
git add README.md docs/specs/2026-09-30-skyrim-python-design.md spikes/RESULTS.md docs/PINS.md \
  livetest/results/new_saves.txt livetest/results/binding_persistence.json
git status --short protocol/PROTOCOL.md | grep -q . && git add protocol/PROTOCOL.md   # only if Step 2 added the attach-on-demand sentence
git commit -m "docs: M2 README, spec event names, results summary and pins"
git tag -l m2 | grep -q . && echo "m2 EXISTS: STOP" || git tag -a m2 -m "M2: events, save/load, test ESP and probes, generic acceptance scenario"
git tag -n1 m2
```
Expected: `RESULTS filled`, the commit, and `m2  M2: events, save/load, ...`. Do not push the branch or the tag. Report to the owner: the tag, the consolidated removal list from Step 3 (itemised, for their yes/no), the probe verdicts, the soak summary, and whether decision 8's spec edits were applied.
