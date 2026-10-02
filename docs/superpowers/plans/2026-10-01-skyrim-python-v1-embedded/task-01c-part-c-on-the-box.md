# Task 1c: Part C (Tasks 25-32) runs the game on the Windows box

> Part of the [skyrim-python v1 plan](README.md). Ruling R42. Not a task to execute on its own: it is the substitution table that Tasks 25-32 point at. The tools are Task 1b's (`tools/box/`), including the Part C additions listed in its Interfaces block.

**Why.** The game never runs on the owner's Linux desktop (it steals his keyboard and mouse focus). Task 1b moved the M0 spikes (Tasks 3-10) to the Windows build box. This file does the same for the plugin tasks. Everything is still driven from Linux. The DLLs, the Python package and the scripts go to the box with `tools/box/box_deploy.sh`. The game starts through the box's scheduled task, and logs are read on the box. The dev bridge reaches Linux through an ssh tunnel that binds `127.0.0.1` on both ends, and the live tests (`livetest/`) run on Linux unchanged. Their helpers gain a box mode, set by `out/box-live.env`.

> **Never on the Linux desktop:** no step of Tasks 25-32 runs `tools/deploy.sh`, `tools/launch_game.sh`, `tools/wait_for_log.sh` against the Linux game, xdotool, or Linux Steam. Every live pytest keeps `SKYRIM_LIVE=1`.

> **Before the first Part C step:** re-run `tools/box/box_setup.sh` once (it installs `files.ps1` and the updated `launch.ps1`, `place.ps1` and `list.ps1`; the task `SkyrimPythonGame` is left as it is), then `tools/box/test_box_tools.sh` (offline; `BOX TOOLS OFFLINE PASS`).

> **Deploy safety (Task 1b, unchanged):** new files only under `C:\dev\skyrim-python-box`, `...\Data\SKSE\Plugins` (our DLLs and ini) and `...\Plugins\SkyrimPython`. Never `Remove-Item`, `del` or `Unregister-ScheduledTask`, and never save over a save. Only the throwaway saves `spikebase`, the S4/S7 saves named in `spikes/RESULTS.md` and `skypy_r38_savecall_<timestamp>` (Task 31; a new name per run, checked unused first). Outside `Plugins`, Linux writes exactly one file on the box: `C:\Users\geoff\Documents\My Games\Skyrim Special Edition\SKSE\SkyrimPython\livetest_control.json` (`files.ps1 -Mode put`, Task 31's smoke commands), through its temp file `livetest_control.json.linux-tmp` in the same folder, which an atomic replace consumes. Both are ours; the teardown list shows that folder. `tools/box/box_teardown_list.sh` lists everything, and nothing is removed without the owner's item-by-item sign-off. Three 0-byte files from the R42 incident (`Plugins\SkyrimPython.dll`, `Plugins\SkyrimPython\SkyrimPythonCore.dll`, `Plugins\SkyrimPython.ini`) are recorded as ours; Task 25's first deploy overwrites them.

## Substitutions for Tasks 25-32

Each block assumes `source tools/box/box_env.sh` has run. Box paths are Windows paths: read them only through the box tools or `bssh`.

| Local step in Tasks 25-32 | On the box |
|---|---|
| `source tools/env.sh` | `source tools/box/box_env.sh`; after `tools/box/box_session.sh up` (or `env`): `source out/box-live.env` (`SKYRIM_PYTHON_SESSION`, `SKSE_LOG_DIR`, `SKYRIM_PYTHON_LOG` point into `out/`; `SKYRIM_PYTHON_BOX=1`). |
| Task 25 Step 8 and Task 26 Step 3: full replacements of `tools/deploy.sh` | Skipped. `tools/box/box_deploy.sh` already covers the two-DLL layout, `--python`, `--site-packages`, `--package` and `--scripts`. `tools/deploy.sh` stays as it is. |
| `tools/deploy.sh out/SkyrimPython.dll <ini>` | `tools/box/box_deploy.sh out/SkyrimPython.dll <ini>`: loader → `Plugins\SkyrimPython.dll`, `out/SkyrimPythonCore.dll` (must exist and not be older) → `Plugins\SkyrimPython\SkyrimPythonCore.dll`, ini → `Plugins\SkyrimPython.ini`. |
| `... --python` / `--package` / `--scripts DIR` appended to that call | One more call each (one mode per call): `tools/box/box_deploy.sh --python`, `tools/box/box_deploy.sh --package`, `tools/box/box_deploy.sh --scripts livetest/scripts`. |
| `tools/launch_game.sh` | `tools/box/box_launch.sh` (refuses while the game runs). |
| `VAR=1 tools/launch_game.sh` (Task 25 `SKYRIM_PYTHON_TEST_UNVERIFIED_RUNTIME`, Task 26 `SKYRIM_PYTHON_TEST_PRELOAD`) | `tools/box/box_launch.sh --env VAR=VALUE` (only `SKYRIM_PYTHON_TEST_*`; `launch.ps1` sets it for the loader and the game, logs `env VAR=...` in `launch.log` (block B10), and empties `launch.env`; the next launch writes it fresh, so nothing leaks). |
| `[SINCE=...] tools/wait_for_log.sh "$SKSE_LOG_DIR/<log>" "<re>" [s]`, `export SINCE` / `unset SINCE` | `tools/box/box_wait_log.sh <log> "<re>" [s]` with `<log>` = `SkyrimPython.log`, `SkyrimPythonLoader.log` or `SkyrimPython/python.log`. Only text since the last `box_launch.sh` counts; drop the `SINCE` lines. |
| `cat`/`grep` of `"$SKSE_LOG_DIR/<log>"` | `tools/box/box_files.sh pull <log> >/dev/null`, then the same `cat`/`grep` on `"$SKSE_LOG_DIR/<log>"` (with `out/box-live.env` sourced it is the mirror `out/box-skse/<log>`: this launch's text only). |
| The dev bridge on Linux's `127.0.0.1:45977`, `SKYRIM_PYTHON_SESSION` | After `python host: running`: `tools/box/box_session.sh up && source out/box-live.env`. Run it again after every launch (new token); the tunnel itself survives a restart. Bring it down at the end of the session: `tools/box/box_session.sh down`. |
| Live tests reading logs (`support.log_text`, `python_log_text`), `support.refused()`, `livetest/smoke.py`'s files | Unchanged tests. With `SKYRIM_PYTHON_BOX=1` the helpers pull each log from the box before reading it (`box_files.sh pull`). `refused()` asks the box (`box_session.sh probe`), because through the tunnel a port nobody listens on is accepted locally and then closed. `smoke.py` pulls `livetest_smoke.json`/`livetest_reply.json` and pushes `livetest_control.json` (`box_files.sh put-control`). |
| "Manual: press Continue" / "Load the save ..." | `tools/box/box_console.sh "load <save>"` (DevBench; `spikebase` for Continue, or the named S4/S7 save from `spikes/RESULTS.md`), then Task 1b block B6 (`"playerLoaded":true`, only `HUD Menu`). |
| Quit the game (from its main menu / between runs) | `tools/box/box_quit.sh`; on `NOT_CLEAN` with `NO_NEW_CRASH_LOG`, `tools/box/box_stop.sh` (the box's exit hang, Task 1b Known issue; not a plugin verdict). `STILL_RUNNING` after `box_stop.sh`: STOP and ask the owner. |
| `pgrep -x SkyrimSE.exe` loops | `box_quit.sh`'s bounded wait (`QUIT_WAIT`). |
| Task 25 Step 9: move `*Spike*.dll` to `Plugins-disabled` | `tools/box/box_deploy.sh --move SkyrimPythonSpike.dll SkyrimPython/spike-off/SkyrimPythonSpike.dll` (SKSE loads only DLLs directly in `Plugins`). For spike re-runs move it back with the two paths swapped. |
| Task 26 variants that `mv` a deployed file | `tools/box/box_deploy.sh --move <REL> <REL>.off` and back with the paths swapped (table below). Recorded in `out/box-moves.txt`; never a delete. |
| Task 26 collision: `SpyCollideTest/` outside `Plugins` | Copies under our own folder: `tools/box/box_deploy.sh --file third_party/cpython/embed/python314.dll collide-test/python314.dll` and the same for each `third_party/cpython/embed/vcruntime140*.dll`; launch with `--env 'SKYRIM_PYTHON_TEST_PRELOAD=C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\SkyrimPython\collide-test\python314.dll'`. |
| `tools/teardown_list.sh` | `tools/box/box_teardown_list.sh` (also lists `skypy_r38_savecall_*`, the `.off` and `spike-off` paths, `collide-test`). |
| Someone at the keyboard (Task 31 `SPY_MANUAL` tests) | Manual. Run them with `pytest -s` and act on each prompt from a second shell through DevBench: `tools/box/box_console.sh --tool menu '{"action":"open","name":"InventoryMenu"}'` (and `"close"`), `tools/box/box_console.sh "load spikebase"`. Record "driven through DevBench, no player input". |

### Startup variants (Task 26 Step 7) on the box

Between variants: `tools/box/box_quit.sh` (then `box_stop.sh` on `NOT_CLEAN`). Launch with `tools/box/box_launch.sh`, wait with `tools/box/box_wait_log.sh <log> "<phrase>" 600`, then `source out/box-live.env` (`tools/box/box_session.sh env` writes it if it is missing) and run `SKYRIM_LIVE=1 SPY_VARIANT=<name> uv run --project python pytest livetest/test_m1_startup_variants.py -q -rs`.

| Variant | Stage | Wait (log, phrase) | Restore |
|---|---|---|---|
| `no-python` | `tools/box/box_deploy.sh --move SkyrimPython/python SkyrimPython/python.off` | `SkyrimPythonLoader.log`, `Python disabled` | `--move SkyrimPython/python.off SkyrimPython/python` |
| `no-core` | `--move SkyrimPython/SkyrimPythonCore.dll SkyrimPython/SkyrimPythonCore.dll.off` | `SkyrimPythonLoader.log`, `Python disabled` | the same, swapped |
| `no-package` | `--move SkyrimPython/lib/skyrim SkyrimPython/lib/skyrim.off` | `SkyrimPython.log`, `python host: failed` | the same, swapped |
| `python-off` | `tools/box/box_deploy.sh out/SkyrimPython.dll plugin/config/variants/python-off.ini` | `SkyrimPython.log`, `python host: disabled` | redeploy the dev ini |
| `bridge-off` | `tools/box/box_deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.ini` | `SkyrimPython.log`, `python host: running` | redeploy the dev ini |
| `collision` | test-hook build (Task 26), the copies above, `box_launch.sh --env SKYRIM_PYTHON_TEST_PRELOAD=...` | `SkyrimPythonLoader.log`, `Python disabled` | hooks off, rebuild, redeploy the dev ini; `collide-test` stays until the owner approves removing it |
| `no-stdlib` | `--move SkyrimPython/python/python314.zip SkyrimPython/python/python314.zip.off` | `SkyrimPython.log`, `python host: failed` | the same, swapped |
| `relaunch` | see below | `SkyrimPython.log`, `python host: (running\|failed)` | nothing |

`relaunch` on the box: with the dev set deployed, the game at the main menu and the session up, start the held client as Task 26 says, then `QUIT_WAIT=50 tools/box/box_quit.sh` and at once `tools/box/box_launch.sh`, wait, `tools/box/box_session.sh up`, run the test. If `box_quit.sh` prints `NOT_CLEAN` (the box's exit hang, Task 1b), the 60 s window cannot be met without killing the game. Record `relaunch` as **not staged on the box (exit hang)** in `spikes/RESULTS.md` instead of a pass. Then `box_stop.sh` and go on. Do not loop.

## For the controller: add to the plan index (`README.md`)

1. Part C header: "Tasks 25-32 run the game on the Windows box through [Task 1c](task-01c-part-c-on-the-box.md) (ruling R42)"; Part A table row `| 1c | [Part C on the Windows box](task-01c-part-c-on-the-box.md) | (table only) | 1b |`.
2. Contracts → Tools: replace the `tools/deploy.sh` / `launch_game.sh` / `wait_for_log.sh` / `env.sh` lines for Part C with the box tools (Task 1b Interfaces), and `livetest/support.py`'s box mode (`SKYRIM_PYTHON_BOX=1`, `BOX`, `box_pull`, `refused` via `box_session.sh probe`) and `diagnostics(session)` (Task 31).
3. Fixed paths: `out/box-session.json` (session file with the tunnel's port, mode 600), `out/box-live.env`, `out/box-skse/` (log mirror), `out/box-moves.txt`; the deployed layout line names `box_deploy.sh`.
