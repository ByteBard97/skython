# Task 45: Gate 0 — owner decision 7, box tools for the ESP, the `.pex` list and `plugins.txt`

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (especially "Box files, extension gated on owner decision 7"), **Fixed paths**, **Gates** and the Part F brief for Task 45 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (Steps 1-9, offline plus one read-only ssh check), then the Windows box (Steps 10-13, only after Gate 0 is open). The game is **not** launched in this task.
>
> **Gate 0:** Steps 1-9 do not need it (README Gates: "the offline parts of 45-48 do not" wait). Step 10 is the gate (README **Owner decisions** table, decision 7: a STOP gate, never assumed): until `OWNER-DECISIONS-2026-10-02.md` decision 7 is recorded as accepted, no step writes `Data\SkythonTest.esp`, any new `Data\Scripts\*.pex`, `plugins.txt`, or even installs the new box scripts. If it is not accepted, commit Steps 1-9 and STOP.
>
> **STOP and ask the owner (do not improvise) if:** decision 7 is not accepted, or accepted with a list other than the one below; the verified `plugins.txt` path (Step 2) is not `C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt`, or more than one `plugins.txt` could be the one the loader reads; `box_plugins.sh backup` prints `BACKUP_DIFFERS` (the owner changed the load order after the backup was taken: the owner may approve replacing the backup with the current file; only with that sign-off run `source tools/box/box_env.sh; bssh "Copy-Item -LiteralPath 'C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt' -Destination 'C:\dev\skyrim-python-box\plugins.txt.bak' -Force"` once, then `box_plugins.sh backup` must print `BACKUP_SAME`; nothing is deleted); `plugins.txt` is ever neither the backup nor the backup plus our line (exit 3); the restored file is not byte-identical to the backup; the build box does not answer ssh within 10 s.

**Why.** M1.5 needs a test plugin and its scripts in the game. Until now our tools could write only under `Data\SKSE\Plugins` plus `Data\Scripts\SkyrimPython.pex`. Owner decision 7 allows exactly: `Data\SkythonTest.esp`; six named `Data\Scripts\*.pex` (`Skython.pex`, `SkythonTestQuestBase.pex`, `SkythonTestQuest.pex`, `SkythonTestAlias.pex`, `SkythonTestLocationAlias.pex`, `QF_SkythonTestQuest_01000800.pex`); one added line `*SkythonTest.esp` in the box's `plugins.txt`, with a backup taken before the first change and restored at the end of every box session that added it. This task teaches `box_deploy.sh`/`place.ps1` that list and nothing more, and adds `box_plugins.sh` + `win/plugins.ps1` for `plugins.txt`. Offline tests pin every refusal; one live check (no game) proves backup → add → restore is byte-exact on the box.

**Files:**
- Modify: `tools/box/box_deploy.sh` (`--esp`, `--test-pex`), `tools/box/win/place.ps1` (single-file allow-list), `tools/box/box_quit.sh`, `tools/box/box_stop.sh` (restore after the game ends), `tools/box/test_box_tools.sh` (block 6j and two updated pinned strings)
- Create: `tools/box/box_plugins.sh`, `tools/box/win/plugins.ps1`
- Modify: `spikes/RESULTS.md` (Task 45 entry)
- On the box only (after Gate 0): `C:\dev\skyrim-python-box\plugins.txt.bak` (the backup, kept; on the owner's removal list), the re-installed `C:\dev\skyrim-python-box\*.ps1`.

**Interfaces:**
- Consumes: the v1 box harness (`box_env.sh`'s `bps`, `b64`, `box_die`, `BOX_OFFLINE`, `BOX_SSH_STUB`; `put_file`/`check_target` in `box_deploy.sh`; `box_setup.sh`).
- Produces (Tasks 46-56 use exactly these):
  - `tools/box/box_deploy.sh --esp path/to/SkythonTest.esp` → `C:\ChronicleDev\Stock Game\Data\SkythonTest.esp` (basename must be `SkythonTest.esp`).
  - `tools/box/box_deploy.sh --test-pex FILE.pex...` → `...\Data\Scripts\<basename>` for basenames in exactly `Skython.pex`, `SkythonTestQuestBase.pex`, `SkythonTestQuest.pex`, `SkythonTestAlias.pex`, `SkythonTestLocationAlias.pex`, `QF_SkythonTestQuest_01000800.pex` (case-sensitive; a name twice is refused). Both go through `put_file`: placed only if absent or already in `out/box-deployed.txt`, so a later task's rebuild overwrites our own earlier copy (Task 48 over Task 47's spike files) and never anyone else's.
  - `tools/box/box_plugins.sh backup | show | add | restore | restore-if-added` and the offline `compose BAK_FILE` / `classify CUR_FILE BAK_FILE` (`BACKUP` | `ADDED` | `OTHER`). `add` and `restore` refuse while the game runs; anything but `BACKUP`/`ADDED` is exit 3 (STOP). State in `$SKYTHON_STATE/box-plugins.state` (`added ...` / `restored ...`).
  - `box_quit.sh` (after `EXITED`) and `box_stop.sh` (after the game is gone) run `box_plugins.sh restore-if-added`; a failed restore makes them exit 3.
  - **Box session rule for later tasks (README Global Constraints, "`plugins.txt` around launches"):** `tools/box/box_plugins.sh add` before every `tools/box/box_launch.sh` that loads `skythonbase` or otherwise needs the ESP; the quit/stop tools put the backup back. A save made with the ESP (`skythonbase`, Task 49) must only be loaded with the line added.

- [ ] **Step 1: Read the owner's answer to decision 7 (record it; do not act on it yet)**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
sed -n '/^\*\*7\. Gate 0/,/^\*\*8\./p' OWNER-DECISIONS-2026-10-02.md
grep -n -i -E 'decision 7|gate 0|^ *7[ .:-]' .superpowers/sdd/README/progress.md 2>/dev/null | tail -5
```
Expected: the decision text, and either an owner reply that accepts decision 7 (e.g. "7 accept"; it may also arrive in the controller's brief for this task) or nothing. Write down which. Gate 0 is open only for an explicit acceptance of exactly the list in the Why paragraph. Steps 2-9 proceed either way.

- [ ] **Step 2: Verify where the box's `plugins.txt` lives (read-only)**

`plugins.ps1` hard-codes the path, so it is read off the box first, not guessed. `launch.ps1` starts `skse64_loader.exe` directly from the Stock Game folder (no mod manager, no virtual file system), so the game reads the `plugins.txt` in the launching user's `%LOCALAPPDATA%`; the scheduled task `SkyrimPythonGame` and ssh both run as `geoff`.
```bash
source tools/box/box_env.sh
grep -n 'skse64_loader.exe' tools/box/win/launch.ps1 | head -3
bssh 'Write-Output "USER=$env:USERNAME LOCALAPPDATA=$env:LOCALAPPDATA"' | tr -d '\r'
bssh '(Get-ScheduledTask -TaskName SkyrimPythonGame).Principal | Format-List UserId,LogonType' | tr -d '\r'
bssh "\$p = Join-Path \$env:LOCALAPPDATA 'Skyrim Special Edition\plugins.txt'; \$p; Test-Path -LiteralPath \$p; (Get-Item -LiteralPath \$p).LastWriteTime.ToString('o'); (Get-Content -LiteralPath \$p).Count; Get-Content -LiteralPath \$p -TotalCount 8" | tr -d '\r'
bssh_t 180 "Get-ChildItem -Path 'C:\ChronicleDev' -Recurse -Depth 5 -Filter plugins.txt -ErrorAction SilentlyContinue | ForEach-Object { \$_.FullName + '  ' + \$_.LastWriteTime.ToString('o') }" | tr -d '\r'
```
Expected: `launch.ps1` starts `skse64_loader.exe` with `-WorkingDirectory` the game folder; `USER=geoff LOCALAPPDATA=C:\Users\geoff\AppData\Local`; the task's `UserId` is `geoff`; the path `C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt` exists (`True`), with lines like `*SomePlugin.esp` (AE format: `*` = enabled), and no `SkythonTest.esp` line. Any `plugins.txt` under `C:\ChronicleDev` (for example a mod-manager profile) is listed for the record: it is not read by a direct `skse64_loader.exe` launch. If the first path is missing, or `launch.ps1` goes through a mod manager, STOP and ask. Copy the output into the Step 13 RESULTS entry.

- [ ] **Step 3: Write the offline tests (block 6j) and see them fail**

Apply this patch to `tools/box/test_box_tools.sh` (header line, the `SH`/`PS` lists, two pinned `place.ps1` strings that the new allow-list changes, and block 6j before check 7):
```bash
git apply <<'PATCH'
--- a/tools/box/test_box_tools.sh
+++ b/tools/box/test_box_tools.sh
@@ -9,5 +9,6 @@
 # 6. Part C (Tasks 25-32): the two-DLL deploy targets, recorded moves, launch env, the bridge session tunnel
 #    (loopback only, token only in out/), the box file mirror, save-name checks, the teardown list's saves, and the
-#    snapshot's rule for a log rewritten with the same bytes after the stamp.
+#    snapshot's rule for a log rewritten with the same bytes after the stamp;
+#    Gate 0 (Task 45, owner decision 7): --esp, --test-pex, the single-file allow-list, plugins.txt add/restore.
 # Every check runs offline: BOX_OFFLINE=1 makes box_env.sh's wrappers refuse (exit 97), and ssh/scp on PATH are
 # traps that record any call and fail. A check that exercises a real script uses its print-only mode on top of that.
@@ -36,6 +37,7 @@
 
 SH=(box_env.sh box_deploy.sh box_launch.sh box_stop.sh box_wait_log.sh box_tunnel.sh box_console.sh
-    box_quit.sh box_setup.sh box_teardown_list.sh box_session.sh box_files.sh box_saves.sh box_shot.sh)
-PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1 files.ps1 shot.ps1)
+    box_quit.sh box_setup.sh box_teardown_list.sh box_session.sh box_files.sh box_saves.sh box_shot.sh box_plugins.sh)
+PS=(launch.ps1 setup_task.ps1 stamp.ps1 wait_log.ps1 console.ps1 stop.ps1 quit_check.ps1 place.ps1 list.ps1 files.ps1 shot.ps1
+    plugins.ps1)
 
 for f in "${SH[@]}"; do
@@ -86,7 +88,7 @@
 if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --pex "$tmpd/Other.pex" >/dev/null 2>&1; then bad "--pex accepted Other.pex"; else ok "--pex refuses a file not named SkyrimPython.pex"; fi
 if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --pex "$tmpd/missing.pex" >/dev/null 2>&1; then bad "--pex accepted a missing file"; else ok "--pex refuses a missing file"; fi
-# place.ps1 allows exactly one destination outside Data\SKSE\Plugins: that .pex
+# place.ps1 allows outside Data\SKSE\Plugins only the single files in $Singles: that .pex plus the Gate 0 list (6j)
 grep -qF "\$PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'" "$B/win/place.ps1" 2>/dev/null \
-  && grep -qF -- '-and $dest -ne $PexDest' "$B/win/place.ps1" && ok "place.ps1 allow-list: Plugins tree + the one .pex" || bad "place.ps1 allow-list"
+  && grep -qF -- '-and -not ($Singles -ccontains $dest)' "$B/win/place.ps1" && ok "place.ps1 allow-list: Plugins tree + the single files" || bad "place.ps1 allow-list"
 # box_deploy records through put_file, which refuses a target that exists and is not recorded
 grep -qE 'put_file "\$PEX" "\$PEX_DEST"' "$B/box_deploy.sh" 2>/dev/null && ok "--pex goes through put_file (absent or ours; recorded)" || bad "--pex does not use put_file"
@@ -240,5 +242,5 @@
 pre="$(sed -n '/^switch (\$Mode)/q;p' "$pl" 2>/dev/null)"
 echo "$pre" | grep -qF '$fullDest = [IO.Path]::GetFullPath($dest)' \
-  && echo "$pre" | grep -qF "if (\$dest -match '\.\.' -or -not (\$fullDest.StartsWith(\$root) -or \$fullDest -eq \$PexDest)) { \"REFUSED" \
+  && echo "$pre" | grep -qF "if (\$dest -match '\.\.' -or -not (\$fullDest.StartsWith(\$root) -or (\$Singles -ccontains \$fullDest))) { \"REFUSED" \
   && ok "place.ps1 checks GetFullPath(Dest) and '..' before the mode switch (all modes)" || bad "place.ps1 does not normalise Dest for every mode"
 
@@ -283,4 +285,113 @@
   && bad "the old 'reads as UNCHANGED' caveat is still documented" || ok "no stale 'reads as UNCHANGED' caveat"
 
+# 6j. Gate 0 (Task 45, owner decision 7): exactly Data\SkythonTest.esp, exactly six Data\Scripts\*.pex, and
+#     plugins.txt changed only by adding *SkythonTest.esp and putting the backup back (no ssh in print/local modes).
+D='C:\ChronicleDev\Stock Game\Data'
+gd="$(mktemp -d)"; mkdir -p "$gd/sub"
+for n in SkythonTest.esp Other.esp Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex \
+         SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex Other.pex skython.pex SkyrimPython.pex Skython.psc; do : > "$gd/$n"; done
+: > "$gd/sub/Skython.pex"
+t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --esp "$gd/SkythonTest.esp" 2>&1)"
+[ "$t" = "$D\SkythonTest.esp" ] && ok "--esp target is Data\\SkythonTest.esp" || bad "--esp target: '$t'"
+for a in "$gd/Other.esp" "$gd/missing.esp" "$gd/SkythonTest.esp $gd/SkythonTest.esp"; do
+  # shellcheck disable=SC2086
+  if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --esp $a >/dev/null 2>&1; then bad "--esp accepted: $a"; else ok "--esp refuses: ${a//$gd\//}"; fi
+done
+six=(Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex)
+t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "${six[@]/#/$gd/}" 2>&1)"
+want="$(for n in "${six[@]}"; do printf '%s\n' "$D\Scripts\\$n"; done)"
+[ "$t" = "$want" ] && ok "--test-pex: the six names resolve to Data\\Scripts\\<name>" || { bad "--test-pex targets:"; echo "$t"; }
+t="$(BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "$gd/sub/../sub/Skython.pex" 2>&1)"
+[ "$t" = "$D\Scripts\Skython.pex" ] && ok "--test-pex: the local path never reaches the destination (basename only)" || bad "--test-pex local path: '$t'"
+for a in Other.pex skython.pex SkyrimPython.pex Skython.psc missing.pex "Skython.pex Skython.pex" "Skython.pex Other.pex"; do
+  args=(); for n in $a; do args+=("$gd/$n"); done
+  if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex "${args[@]}" >/dev/null 2>&1; then bad "--test-pex accepted: $a"; else ok "--test-pex refuses: $a"; fi
+done
+if BOX_DEPLOY_PRINT_TARGET=1 "$B/box_deploy.sh" --test-pex >/dev/null 2>&1; then bad "--test-pex accepted no file"; else ok "--test-pex refuses no file"; fi
+grep -qE 'TEST_PEX_NAMES=\(Skython\.pex SkythonTestQuestBase\.pex SkythonTestQuest\.pex SkythonTestAlias\.pex SkythonTestLocationAlias\.pex QF_SkythonTestQuest_01000800\.pex\)$' "$B/box_deploy.sh" \
+  && ok "box_deploy.sh TEST_PEX_NAMES is exactly the six names" || bad "box_deploy.sh TEST_PEX_NAMES"
+grep -qE 'put_file "\$ESP" "\$ESP_DEST"' "$B/box_deploy.sh" && grep -qE 'put_file "\$\{TPEX\[\$i\]\}" "\$\{TPEX_DEST\[\$i\]\}"' "$B/box_deploy.sh" \
+  && ok "--esp and --test-pex go through put_file (absent or ours; recorded)" || bad "--esp/--test-pex do not use put_file"
+pl="$B/win/place.ps1"
+grep -qF "\$EspDest = 'C:\ChronicleDev\Stock Game\Data\SkythonTest.esp'" "$pl" && ok "place.ps1 lists Data\\SkythonTest.esp" || bad "place.ps1 EspDest"
+all6=1; for n in "${six[@]}"; do grep -qF "'C:\ChronicleDev\Stock Game\Data\Scripts\\$n'" "$pl" || all6=0; done
+[ "$(grep -cE "^  'C:\\\\ChronicleDev\\\\Stock Game\\\\Data\\\\Scripts\\\\[A-Za-z0-9_]+\.pex'[,)]?$" "$pl")" = 6 ] && [ "$all6" = 1 ] \
+  && ok "place.ps1 TestPexDests: exactly the six Data\\Scripts names" || bad "place.ps1 TestPexDests is not exactly the six names"
+grep -qF '$Singles = @($PexDest, $EspDest) + $TestPexDests' "$pl" && grep -qF "if ((\$Singles -ccontains \$dest) -and \$Kind -ne 'file')" "$pl" \
+  && ok "place.ps1: single files are compared exactly (-ccontains) and take one file each" || bad "place.ps1 single-file rule"
+
+# plugins.txt: compose / classify (local) and the box script's guards
+P_="$B/box_plugins.sh"; pd="$(mktemp -d)"; export SKYTHON_STATE="$pd/state"
+printf 'a.esp\r\n*B.esp\r\n' > "$pd/bak1"; printf 'a.esp\r\n*B.esp\r\n*SkythonTest.esp\r\n' > "$pd/want1"
+printf 'a.esp\r\n*B.esp' > "$pd/bak2"; printf 'a.esp\r\n*B.esp\r\n*SkythonTest.esp\r\n' > "$pd/want2"
+: > "$pd/bak3"; printf '*SkythonTest.esp\r\n' > "$pd/want3"
+for i in 1 2 3; do
+  "$P_" compose "$pd/bak$i" > "$pd/got$i" 2>&1 && cmp -s "$pd/got$i" "$pd/want$i" && ok "compose case $i: backup + [CRLF] + *SkythonTest.esp CRLF" || bad "compose case $i: $(od -c "$pd/got$i" | head -3)"
+done
+printf 'a.esp\r\n*skythontest.esp\r\n' > "$pd/listed"; printf 'SkythonTest.esp\r\n' > "$pd/listed2"
+for f in listed listed2; do if "$P_" compose "$pd/$f" >/dev/null 2>&1; then bad "compose accepted a backup that already lists it ($f)"; else ok "compose refuses a backup that already lists SkythonTest.esp ($f)"; fi; done
+printf '*B.esp\r\na.esp\r\n*SkythonTest.esp\r\n' > "$pd/reordered"; cp "$pd/want1" "$pd/extra"; printf 'c.esp\r\n' >> "$pd/extra"
+for c in "bak1 BACKUP" "want1 ADDED" "reordered OTHER" "extra OTHER" "bak2 OTHER"; do
+  set -- $c; t="$("$P_" classify "$pd/$1" "$pd/bak1" 2>&1)"
+  [ "$t" = "$2" ] && ok "classify $1 -> $2" || bad "classify $1: '$t' (expected $2)"
+done
+pp="$B/win/plugins.ps1"
+grep -qF "\$PluginsTxt = 'C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt'" "$pp" && grep -qF "\$Backup = 'C:\dev\skyrim-python-box\plugins.txt.bak'" "$pp" \
+  && ok "plugins.ps1: the verified plugins.txt path and the backup in C:\\dev\\skyrim-python-box" || bad "plugins.ps1 paths"
+w="$(grep -vE '^\s*#' "$pp" | grep -E 'WriteAllBytes|Copy-Item|Set-Content|Out-File|Move-Item|Add-Content|New-Item')"
+[ "$(echo "$w" | grep -c .)" = 3 ] && echo "$w" | grep -qF '[IO.File]::WriteAllBytes($PluginsTxt, $n)' \
+  && echo "$w" | grep -qF 'Copy-Item -LiteralPath $PluginsTxt -Destination $Backup' && echo "$w" | grep -qF 'Copy-Item -LiteralPath $Backup -Destination $PluginsTxt -Force' \
+  && ok "plugins.ps1 writes only: the backup (once), plugins.txt (add), plugins.txt from the backup (restore)" || { bad "plugins.ps1 write operations:"; echo "$w"; }
+[ "$(grep -cF "if (GameRunning) { Refuse 'the game is running' }" "$pp")" = 2 ] && ok "plugins.ps1 add and restore refuse while the game runs" || bad "plugins.ps1 game-running guard"
+grep -qF 'if ((Sha $c) -ne (Sha (Compose $b))) { Refuse' "$pp" && grep -qF 'if ((Sha $c) -ne (Sha $b)) { Refuse' "$pp" && grep -qF 'if (Listed $b) { Refuse' "$pp" \
+  && ok "plugins.ps1 re-checks on the box: add only from the backup, restore only from backup + line" || bad "plugins.ps1 box-side checks"
+
+# box_quit.sh / box_stop.sh / box_plugins.sh against a stubbed box (BOX_SSH_STUB, offline only)
+pst="$pd/pstub.sh"; PSTUBLOG="$pd/pcalls.log"; export PSTUBLOG
+cat > "$pst" <<'PSTUBEOF'
+#!/usr/bin/env bash
+echo "$*" >> "$PSTUBLOG"
+case "$*" in
+  *"console.ps1"*)                 echo '{"ok": true}' ;;
+  *"quit_check.ps1"*)              printf 'EXITED\nNO_NEW_CRASH_LOG\nCLEAN_EXIT\n' ;;
+  *"stop.ps1"*)                    echo NOTHING_RUNNING ;;
+  *"plugins.ps1 -Mode read"*)      cat "$PSTUB_READ" ;;
+  *"plugins.ps1 -Mode restore"*)   echo "RESTORED $PSTUB_BAKSHA" ;;
+  *"plugins.ps1 -Mode add"*)       echo "ADDED $PSTUB_NEWSHA" ;;
+  *) echo "pstub: unexpected $*" >&2; exit 99 ;;
+esac
+PSTUBEOF
+chmod +x "$pst"
+readf() { printf 'CUR %s %s\nBAK %s %s\nGAME %s\n' "$(sha256sum "$1" | cut -d' ' -f1)" "$(base64 -w0 < "$1")" \
+          "$(sha256sum "$2" | cut -d' ' -f1)" "$(base64 -w0 < "$2")" "$3" > "$pd/read.txt"; }
+sbox() { : > "$PSTUBLOG"; PSTUB_READ="$pd/read.txt" PSTUB_BAKSHA="$(sha256sum "$pd/bak1" | cut -d' ' -f1)" \
+         PSTUB_NEWSHA="$(sha256sum "$pd/want1" | cut -d' ' -f1)" BOX_SSH_STUB="$pst" "$@" 2>&1; }
+mkdir -p "$SKYTHON_STATE"; echo "restored 2026-10-02T00:00:00" > "$SKYTHON_STATE/box-plugins.state"
+t="$(sbox "$B/box_quit.sh")"; rc=$?
+[ "$rc" = 0 ] && case "$t" in *"nothing to restore"*) true ;; *) false ;; esac && ! grep -q plugins.ps1 "$PSTUBLOG" \
+  && ok "box_quit.sh with no add this session: no plugins.ps1 call" || bad "box_quit.sh without add: rc=$rc '$t'"
+echo "added 2026-10-02T00:00:00" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/want1" "$pd/bak1" IDLE
+t="$(sbox "$B/box_quit.sh")"; rc=$?
+[ "$rc" = 0 ] && grep -qE "plugins.ps1 -Mode restore -ExpectCur $(sha256sum "$pd/want1" | cut -d' ' -f1)" "$PSTUBLOG" \
+  && [ "$(cut -d' ' -f1 "$SKYTHON_STATE/box-plugins.state")" = restored ] && ok "box_quit.sh after EXITED restores the backup (state -> restored)" || bad "box_quit.sh restore: rc=$rc '$t'"
+echo "added x" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/reordered" "$pd/bak1" IDLE
+t="$(sbox "$B/box_quit.sh")"; rc=$?
+[ "$rc" = 3 ] && ! grep -q 'plugins.ps1 -Mode restore' "$PSTUBLOG" && case "$t" in *STOP*) true ;; *) false ;; esac \
+  && ok "box_quit.sh: plugins.txt neither backup nor backup + line -> STOP (exit 3), nothing written" || bad "box_quit.sh other: rc=$rc '$t'"
+echo "added x" > "$SKYTHON_STATE/box-plugins.state"; readf "$pd/want1" "$pd/bak1" IDLE
+t="$(sbox "$B/box_stop.sh")"; rc=$?
+[ "$rc" = 0 ] && grep -q 'plugins.ps1 -Mode restore' "$PSTUBLOG" && ok "box_stop.sh restores after the game is gone" || bad "box_stop.sh restore: rc=$rc '$t'"
+readf "$pd/bak1" "$pd/bak1" IDLE
+t="$(sbox "$P_" add)"; rc=$?
+[ "$rc" = 0 ] && grep -qE "plugins.ps1 -Mode add -ExpectCur $(sha256sum "$pd/bak1" | cut -d' ' -f1) -ExpectNew $(sha256sum "$pd/want1" | cut -d' ' -f1)" "$PSTUBLOG" \
+  && [ "$(cut -d' ' -f1 "$SKYTHON_STATE/box-plugins.state")" = added ] && ok "add sends the backup's and the composed file's SHA-256 (state -> added)" || bad "add: rc=$rc '$t'"
+readf "$pd/bak1" "$pd/bak1" RUNNING
+t="$(sbox "$P_" add)"; rc=$?
+[ "$rc" != 0 ] && ! grep -q 'plugins.ps1 -Mode add' "$PSTUBLOG" && ok "add refuses while the game runs" || bad "add while running: rc=$rc '$t'"
+readf "$pd/extra" "$pd/bak1" IDLE
+t="$(sbox "$P_" add)"; rc=$?
+[ "$rc" = 3 ] && ! grep -q 'plugins.ps1 -Mode add' "$PSTUBLOG" && ok "add: plugins.txt differs from the backup -> STOP, nothing written" || bad "add other: rc=$rc '$t'"
+unset SKYTHON_STATE
+
 # 7. nothing reached ssh or scp during the whole run
 if [ -e "$TRAPLOG" ]; then bad "ssh/scp was called during the offline test:"; cat "$TRAPLOG"; else ok "no ssh/scp call reached PATH"; fi
PATCH
bash tools/box/test_box_tools.sh | grep -E '^FAIL|BOX TOOLS'
```

If `git apply` reports a conflict because an earlier task (41-44) touched these lines, make the same edits by hand: the patch shows exactly what changes.
Expected: `BOX TOOLS OFFLINE FAIL` with about 30 `FAIL` lines, among them `missing tools/box/box_plugins.sh`, `missing tools/box/win/plugins.ps1`, `place.ps1 allow-list`, `place.ps1 does not normalise Dest for every mode`, `--esp target: 'BOX_OFFLINE: refusing ssh ...'`, `box_deploy.sh TEST_PEX_NAMES`, `place.ps1 EspDest`, `compose case 1..3`, `plugins.ps1 paths`, `box_quit.sh restore: ...`. No `ssh/scp was called` line (the offline guard holds).

- [ ] **Step 4: `box_deploy.sh --esp` and `--test-pex`**

```bash
git apply <<'PATCH'
--- a/tools/box/box_deploy.sh
+++ b/tools/box/box_deploy.sh
@@ -14,4 +14,10 @@
 #                                                               own script, which keeps its name; the only
 #                                                               target outside Plugins; placed only if absent or ours)
+#   tools/box/box_deploy.sh --esp path/to/SkythonTest.esp    -> exactly Data\SkythonTest.esp (owner decision 7, Gate 0;
+#                                                               Task 45). Placed only if absent or ours.
+#   tools/box/box_deploy.sh --test-pex FILE.pex...           -> Data\Scripts\<name> for names on the Gate 0 list only:
+#                                                               Skython.pex, SkythonTestQuestBase.pex, SkythonTestQuest.pex,
+#                                                               SkythonTestAlias.pex, SkythonTestLocationAlias.pex,
+#                                                               QF_SkythonTestQuest_01000800.pex
 #   tools/box/box_deploy.sh --move REL NEWREL                -> moves Plugins\REL to Plugins\NEWREL (Task 26's startup
 #                                                               variants; Task 25 moves the spike DLL aside). REL must be
@@ -19,5 +24,6 @@
 #                                                               Move-Item, never a delete. Each move is appended to
 #                                                               out/box-moves.txt and NEWREL is recorded.
-#   BOX_DEPLOY_PRINT_TARGET=1 with --pex, a DLL or --move prints the resolved target(s) and exits before any ssh
+#   BOX_DEPLOY_PRINT_TARGET=1 with --pex, --esp, --test-pex, a DLL or --move prints the resolved target(s) and exits
+#   before any ssh
 #   (offline test).
 # Every target is recorded in out/box-deployed.txt. A target that exists on the box and is not recorded there
@@ -86,4 +92,7 @@
   return 1
 }
+# Owner decision 7 (Gate 0): the only Papyrus scripts --test-pex places. These six names are the whole list.
+TEST_PEX_NAMES=(Skython.pex SkythonTestQuestBase.pex SkythonTestQuest.pex SkythonTestAlias.pex SkythonTestLocationAlias.pex QF_SkythonTestQuest_01000800.pex)
+test_pex_ok() { local n; for n in "${TEST_PEX_NAMES[@]}"; do [ "$1" = "$n" ] && return 0; done; return 1; }
 plugins_rel() {  # plugins_rel <REL>: the Windows path under Plugins, or die
   local r="${1//\//\\}"
@@ -97,4 +106,21 @@
   PEX_DEST="$BOX_DATA\\Scripts\\SkyrimPython.pex"
   if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$PEX_DEST"; exit 0; fi
+elif [ "$1" = --esp ]; then
+  [ $# -eq 2 ] || box_die "usage: box_deploy.sh --esp path/to/SkythonTest.esp"
+  ESP="$2"; [ -f "$ESP" ] || box_die "no such file: $ESP"
+  [ "$(basename "$ESP")" = SkythonTest.esp ] || box_die "--esp places only SkythonTest.esp (got $(basename "$ESP"))"
+  ESP_DEST="$BOX_DATA\\SkythonTest.esp"
+  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then echo "$ESP_DEST"; exit 0; fi
+elif [ "$1" = --test-pex ]; then
+  [ $# -ge 2 ] || box_die "usage: box_deploy.sh --test-pex FILE.pex..."
+  TPEX=(); TPEX_DEST=()
+  for f in "${@:2}"; do
+    [ -f "$f" ] || box_die "no such file: $f"
+    b="$(basename "$f")"
+    test_pex_ok "$b" || box_die "--test-pex places only ${TEST_PEX_NAMES[*]} (got $b)"
+    for d in "${TPEX_DEST[@]}"; do [ "$d" != "$BOX_DATA\\Scripts\\$b" ] || box_die "--test-pex: $b given twice"; done
+    TPEX+=("$f"); TPEX_DEST+=("$BOX_DATA\\Scripts\\$b")
+  done
+  if [ "${BOX_DEPLOY_PRINT_TARGET:-0}" = 1 ]; then printf '%s\n' "${TPEX_DEST[@]}"; exit 0; fi
 elif [ "$1" = --move ]; then
   [ $# -eq 3 ] || box_die "usage: box_deploy.sh --move REL NEWREL (both relative to Plugins)"
@@ -153,4 +179,10 @@
     put_file "$PEX" "$PEX_DEST"
     ;;
+  --esp)
+    put_file "$ESP" "$ESP_DEST"
+    ;;
+  --test-pex)
+    for i in "${!TPEX[@]}"; do put_file "${TPEX[$i]}" "${TPEX_DEST[$i]}"; done
+    ;;
   --move)
     r="$(bps 60 place.ps1 -Mode move -SrcB64 "$(b64 "$MV_SRC")" -DestB64 "$(b64 "$MV_DEST")" | tr -d '\r')"
PATCH
```
The list lives in one array (`TEST_PEX_NAMES`); `--esp`/`--test-pex` resolve and print their targets before any ssh (print mode), and place through `put_file` after the game-running check like `--pex`.

If `git apply` reports a conflict because an earlier task (41-44) touched these lines, make the same edits by hand: the patch shows exactly what changes.

- [ ] **Step 5: `place.ps1`: the single-file allow-list**

```bash
git apply <<'PATCH'
--- a/tools/box/win/place.ps1
+++ b/tools/box/win/place.ps1
@@ -6,17 +6,28 @@
 #   move:   Move-Item Src to Dest (no -Force), both inside Data\SKSE\Plugins; refuses when Src is absent or Dest
 #           exists; prints "MOVED". Used for Task 26's startup variants (moved, never deleted, moved back after).
-# Only box_deploy.sh calls this, for paths under the game's Data\SKSE\Plugins and for Data\Scripts\SkyrimPython.pex.
+# Only box_deploy.sh calls this, for paths under the game's Data\SKSE\Plugins and for the single files in $Singles:
+# Data\Scripts\SkyrimPython.pex (Task 7) and, under owner decision 7 (Gate 0, Task 45), Data\SkythonTest.esp and
+# exactly six Data\Scripts\*.pex. Each single file takes -Kind file only.
 param([string]$Mode, [string]$DestB64, [string]$SrcB64 = '', [string]$Kind = 'file')
 $ErrorActionPreference = 'Stop'
 function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
 $dest = From-B64 $DestB64
-# Allowed destinations: the Data\SKSE\Plugins tree, and exactly one file outside it, the spike's compiled Papyrus
-# script Data\Scripts\SkyrimPython.pex (Task 7; box_deploy.sh --pex).
+# Allowed destinations: the Data\SKSE\Plugins tree, and the single files below outside it: the spike's compiled
+# Papyrus script (Task 7; box_deploy.sh --pex) and the Gate 0 list (owner decision 7; box_deploy.sh --esp, --test-pex).
 $PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'
+$EspDest = 'C:\ChronicleDev\Stock Game\Data\SkythonTest.esp'
+$TestPexDests = @(
+  'C:\ChronicleDev\Stock Game\Data\Scripts\Skython.pex',
+  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestQuestBase.pex',
+  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestQuest.pex',
+  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestAlias.pex',
+  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestLocationAlias.pex',
+  'C:\ChronicleDev\Stock Game\Data\Scripts\QF_SkythonTestQuest_01000800.pex')
+$Singles = @($PexDest, $EspDest) + $TestPexDests
 $root = 'C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\'
-if (-not $dest.StartsWith($root) -and $dest -ne $PexDest) { "REFUSED $dest is outside Data\SKSE\Plugins and is not $PexDest"; exit 2 }
+if (-not $dest.StartsWith($root) -and -not ($Singles -ccontains $dest)) { "REFUSED $dest is outside Data\SKSE\Plugins and is not one of the single files"; exit 2 }
 # Every mode: no '..' and the normalised path must still be inside (a prefix test alone lets Plugins\..\.. escape).
 $fullDest = [IO.Path]::GetFullPath($dest)
-if ($dest -match '\.\.' -or -not ($fullDest.StartsWith($root) -or $fullDest -eq $PexDest)) { "REFUSED $dest is not inside Data\SKSE\Plugins"; exit 2 }
+if ($dest -match '\.\.' -or -not ($fullDest.StartsWith($root) -or ($Singles -ccontains $fullDest))) { "REFUSED $dest is not inside Data\SKSE\Plugins"; exit 2 }
 switch ($Mode) {
   'exists' { if (Test-Path -LiteralPath $dest) { 'EXISTS' } else { 'ABSENT' }; exit 0 }
@@ -34,5 +44,5 @@
   }
   'place' {
-    if ($dest -eq $PexDest -and $Kind -ne 'file') { "REFUSED $PexDest takes a single file"; exit 2 }
+    if (($Singles -ccontains $dest) -and $Kind -ne 'file') { "REFUSED $dest takes a single file"; exit 2 }
     $src = From-B64 $SrcB64
     if ($Kind -eq 'file') {
PATCH
```
`-ccontains` compares exactly (case-sensitive), so only these spellings pass; the `GetFullPath` + `..` check still runs for every mode before the switch.

If `git apply` reports a conflict because an earlier task (41-44) touched these lines, make the same edits by hand: the patch shows exactly what changes.

- [ ] **Step 6: `plugins.ps1` (box) and `box_plugins.sh` (Linux)**

Create `tools/box/win/plugins.ps1`:
```powershell
# plugins.ps1 -Mode read|backup|add|restore [-ExpectCur <sha256>] [-ExpectNew <sha256>]   (owner decision 7, Task 45)
# The box's plugins.txt (path verified in Task 45) changes only by one added line "*SkythonTest.esp" and by putting the
# backup back. read: "CUR <sha|NONE> <b64|->", "BAK <sha|NONE> <b64|->", "GAME IDLE|RUNNING". backup: copies plugins.txt
# to plugins.txt.bak once ("BACKED_UP"/"BACKUP_SAME <sha>"; a differing backup is never overwritten: "BACKUP_DIFFERS",
# exit 3). add: game idle, plugins.txt == backup (sha -ExpectCur), backup + [CRLF] + "*SkythonTest.esp" CRLF hashes to
# -ExpectNew -> "ADDED <sha>". restore: game idle, plugins.txt (sha -ExpectCur) == backup + that line -> copies the
# backup back, "RESTORED <sha>". Anything else: "REFUSED <why>", exit 3. Nothing is ever deleted.
param([string]$Mode, [string]$ExpectCur = '', [string]$ExpectNew = '')
$ErrorActionPreference = 'Stop'
$PluginsTxt = 'C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt'
$Backup = 'C:\dev\skyrim-python-box\plugins.txt.bak'
$Line = [Text.Encoding]::ASCII.GetBytes("*SkythonTest.esp`r`n")
function Sha([byte[]]$b) { ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($b))).Replace('-', '').ToLower() }
function Bytes([string]$p) { if (Test-Path -LiteralPath $p) { return ,[IO.File]::ReadAllBytes($p) } else { return $null } }
function Refuse([string]$why) { "REFUSED $why"; exit 3 }
function GameRunning { return [bool](Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue) }
function Listed([byte[]]$b) {
  foreach ($l in ([Text.Encoding]::UTF8.GetString($b) -split "`r?`n")) { if ($l.Trim().TrimStart('*').ToLower() -eq 'skythontest.esp') { return $true } }
  return $false
}
function Compose([byte[]]$bak) {
  $sep = [byte[]]@()
  if ($bak.Length -gt 0 -and $bak[$bak.Length - 1] -ne 10) { $sep = [byte[]](13, 10) }
  return ,([byte[]]($bak + $sep + $Line))
}
switch ($Mode) {
  'read' {
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c) { 'CUR NONE -' } else { "CUR $(Sha $c) $([Convert]::ToBase64String($c))" }
    if ($null -eq $b) { 'BAK NONE -' } else { "BAK $(Sha $b) $([Convert]::ToBase64String($b))" }
    if (GameRunning) { 'GAME RUNNING' } else { 'GAME IDLE' }
    exit 0
  }
  'backup' {
    $c = Bytes $PluginsTxt
    if ($null -eq $c) { Refuse "no $PluginsTxt" }
    $b = Bytes $Backup
    if ($null -eq $b) {
      Copy-Item -LiteralPath $PluginsTxt -Destination $Backup
      $b = Bytes $Backup
      if ((Sha $b) -ne (Sha $c)) { Refuse 'the backup copy does not match plugins.txt' }
      "BACKED_UP $(Sha $b)"; exit 0
    }
    if ((Sha $b) -eq (Sha $c)) { "BACKUP_SAME $(Sha $b)"; exit 0 }
    "BACKUP_DIFFERS bak=$(Sha $b) cur=$(Sha $c)"; exit 3
  }
  'add' {
    if (GameRunning) { Refuse 'the game is running' }
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c -or $null -eq $b) { Refuse 'plugins.txt or its backup is missing (run backup first)' }
    if ((Sha $c) -ne $ExpectCur) { Refuse "plugins.txt has sha $(Sha $c), Linux expected $ExpectCur" }
    if ((Sha $c) -ne (Sha $b)) { Refuse 'plugins.txt is not identical to the backup' }
    if (Listed $b) { Refuse 'SkythonTest.esp is already listed in the backup' }
    $n = Compose $b
    if ((Sha $n) -ne $ExpectNew) { Refuse "backup + line has sha $(Sha $n), Linux expected $ExpectNew" }
    [IO.File]::WriteAllBytes($PluginsTxt, $n)
    $w = Bytes $PluginsTxt
    if ((Sha $w) -ne $ExpectNew) { Refuse "after the write plugins.txt has sha $(Sha $w)" }
    "ADDED $(Sha $w)"; exit 0
  }
  'restore' {
    if (GameRunning) { Refuse 'the game is running' }
    $c = Bytes $PluginsTxt; $b = Bytes $Backup
    if ($null -eq $c -or $null -eq $b) { Refuse 'plugins.txt or its backup is missing' }
    if ((Sha $c) -ne $ExpectCur) { Refuse "plugins.txt has sha $(Sha $c), Linux expected $ExpectCur" }
    if ((Sha $c) -ne (Sha (Compose $b))) { Refuse 'plugins.txt is not exactly the backup plus *SkythonTest.esp' }
    Copy-Item -LiteralPath $Backup -Destination $PluginsTxt -Force
    $w = Bytes $PluginsTxt
    if ((Sha $w) -ne (Sha $b)) { Refuse "after the copy plugins.txt has sha $(Sha $w)" }
    "RESTORED $(Sha $w)"; exit 0
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
```
Create `tools/box/box_plugins.sh` (then `chmod +x tools/box/box_plugins.sh`):
```bash
#!/usr/bin/env bash
# The box's plugins.txt (owner decision 7, Task 45): back it up once, add the one line *SkythonTest.esp, show it, put the
# backup back. Only win/plugins.ps1 writes the file; this script sends SHA-256 values, never bytes, and the box re-checks.
# Usage: box_plugins.sh backup | show | add | restore | restore-if-added
#        box_plugins.sh compose BAK_FILE | classify CUR_FILE BAK_FILE     (local, offline; classify: BACKUP|ADDED|OTHER)
# add/restore refuse while the game runs and record added/restored in $SKYTHON_STATE/box-plugins.state; box_quit.sh and
# box_stop.sh run restore-if-added once the game has ended. A plugins.txt that is neither the backup nor the backup plus
# our line is a STOP (exit 3, diff printed, nothing written).
set -euo pipefail
source "$(dirname "$0")/box_env.sh"
STATE_FILE="$SKYTHON_STATE/box-plugins.state"
WORK="$SKYTHON_STATE/box-plugins"   # this run's copies (overwritten each run)

compose() {  # compose <bak file>: backup + [CRLF] + "*SkythonTest.esp" CRLF on stdout; refuses a backup that lists it
  python3 - "$1" <<'PY'
import sys
b = open(sys.argv[1], "rb").read()
if b"skythontest.esp" in [l.strip().lstrip(b"*").lower() for l in b.splitlines()]:
    sys.exit("box_plugins: SkythonTest.esp is already listed in the backup; ask the owner")
sep = b"\r\n" if b and not b.endswith(b"\n") else b""
sys.stdout.buffer.write(b + sep + b"*SkythonTest.esp\r\n")
PY
}
classify() {  # classify <cur> <bak>: BACKUP (identical), ADDED (backup + our line, byte for byte) or OTHER
  local t="$WORK/composed"
  mkdir -p "$WORK"
  if cmp -s "$1" "$2"; then echo BACKUP
  elif compose "$2" > "$t" 2>/dev/null && cmp -s "$1" "$t"; then echo ADDED
  else echo OTHER; fi
}
sha() { sha256sum "$1" | cut -d' ' -f1; }
field() { echo "$1" | awk -v k="$2" -v n="$3" '$1 == k { print $n }'; }
read_box() {  # sets CUR_SHA, BAK_SHA, GAME and writes $WORK/cur, $WORK/bak
  local out
  mkdir -p "$WORK"
  out="$(bps 60 plugins.ps1 -Mode read | tr -d '\r')" || box_die "plugins.ps1 read failed: $out"
  CUR_SHA="$(field "$out" CUR 2)"; BAK_SHA="$(field "$out" BAK 2)"; GAME="$(field "$out" GAME 2)"
  [ -n "$CUR_SHA" ] && [ -n "$BAK_SHA" ] && [ -n "$GAME" ] || box_die "unexpected plugins.ps1 read output: $out"
  [ "$CUR_SHA" != NONE ] || box_die "no plugins.txt at the verified path on the box; STOP and ask the owner"
  field "$out" CUR 3 | base64 -d > "$WORK/cur"
  [ "$(sha "$WORK/cur")" = "$CUR_SHA" ] || box_die "plugins.txt arrived damaged (SHA-256 mismatch)"
  if [ "$BAK_SHA" != NONE ]; then
    field "$out" BAK 3 | base64 -d > "$WORK/bak"
    [ "$(sha "$WORK/bak")" = "$BAK_SHA" ] || box_die "the backup arrived damaged (SHA-256 mismatch)"
  fi
}
need_idle_and_backup() {
  [ "$GAME" = IDLE ] || box_die "the game is running on the box; plugins.txt is changed only while it is not"
  [ "$BAK_SHA" != NONE ] || box_die "no backup yet: run tools/box/box_plugins.sh backup first"
}
stop_other() {
  echo "STOP: plugins.txt on the box is neither the backup nor the backup plus *SkythonTest.esp. Diff (backup -> current):" >&2
  diff <(tr -d '\r' < "$WORK/bak") <(tr -d '\r' < "$WORK/cur") >&2 || true
  echo "Nothing was written. Ask the owner." >&2
  exit 3
}
mark() { mkdir -p "$SKYTHON_STATE"; echo "$1 $(date -Is)" > "$STATE_FILE"; }

case "${1:-}" in
  compose)  [ $# -eq 2 ] || box_die "usage: box_plugins.sh compose BAK_FILE"; compose "$2" ;;
  classify) [ $# -eq 3 ] || box_die "usage: box_plugins.sh classify CUR_FILE BAK_FILE"; classify "$2" "$3" ;;
  backup)
    r="$(bps 60 plugins.ps1 -Mode backup | tr -d '\r')" || true
    echo "$r"
    case "$r" in
      BACKED_UP*|BACKUP_SAME*) ;;
      *) echo "STOP: no usable backup (above); nothing was overwritten. Ask the owner." >&2; exit 3 ;;
    esac ;;
  show)
    read_box
    echo "plugins.txt sha256 $CUR_SHA; backup $BAK_SHA; game $GAME"
    [ "$BAK_SHA" = NONE ] || echo "state: $(classify "$WORK/cur" "$WORK/bak")"
    tr -d '\r' < "$WORK/cur" ;;
  add)
    read_box; need_idle_and_backup
    case "$(classify "$WORK/cur" "$WORK/bak")" in
      ADDED)  mark added; echo "ALREADY: *SkythonTest.esp is already the one added line" ;;
      BACKUP) compose "$WORK/bak" > "$WORK/new"; new="$(sha "$WORK/new")"
              r="$(bps 60 plugins.ps1 -Mode add -ExpectCur "$CUR_SHA" -ExpectNew "$new" | tr -d '\r')" || true
              [ "$r" = "ADDED $new" ] || box_die "add: '$r' (expected ADDED $new)"
              mark added; echo "$r" ;;
      *) stop_other ;;
    esac ;;
  restore|restore-if-added)
    if [ "$1" = restore-if-added ] && [ "$(cut -d' ' -f1 "$STATE_FILE" 2>/dev/null || true)" != added ]; then
      echo "plugins.txt: nothing to restore (no add recorded this session)"; exit 0
    fi
    read_box; need_idle_and_backup
    case "$(classify "$WORK/cur" "$WORK/bak")" in
      BACKUP) mark restored; echo "ALREADY_RESTORED: plugins.txt equals the backup" ;;
      ADDED)  r="$(bps 60 plugins.ps1 -Mode restore -ExpectCur "$CUR_SHA" | tr -d '\r')" || true
              [ "$r" = "RESTORED $BAK_SHA" ] || box_die "restore: '$r' (expected RESTORED $BAK_SHA)"
              mark restored; echo "$r" ;;
      *) stop_other ;;
    esac ;;
  *) box_die "usage: box_plugins.sh backup | show | add | restore | restore-if-added | compose BAK | classify CUR BAK" ;;
esac
```
Both sides compose: Linux sends only SHA-256 values (no long base64 on a Windows command line) and the box re-derives the file from its own backup, refusing unless the hashes agree.

- [ ] **Step 7: `box_quit.sh` and `box_stop.sh` restore after the game ends**

Replace `tools/box/box_quit.sh` with:
```bash
#!/usr/bin/env bash
# Clean exit on the box: console qqq through DevBench, then a bounded wait (QUIT_WAIT=120 s) for SkyrimSE to end
# and a check that no crash-*.log appeared in the box's SKSE log folder since the launch stamp.
# Exit 0 only for CLEAN_EXIT. DevBench may drop the HTTP reply to qqq because the game is exiting: not an error.
# Gate 0 (Task 45): once the game has EXITED, box_plugins.sh restore-if-added puts plugins.txt's backup back if this
# session added *SkythonTest.esp; a restore that cannot be done safely is a STOP (exit 3).
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
"$(dirname "$0")/box_console.sh" qqq || echo "(no reply to qqq; expected while the game exits)"
out="$(bps $(( ${QUIT_WAIT:-120} + 60 )) quit_check.ps1 -Seconds "${QUIT_WAIT:-120}")"; rc=$?
out="$(printf '%s\n' "$out" | tr -d '\r')"; echo "$out"
if printf '%s\n' "$out" | grep -qx EXITED; then
  "$(dirname "$0")/box_plugins.sh" restore-if-added || rc=3
else
  echo "plugins.txt: not restored yet (the game has not exited); tools/box/box_stop.sh restores after it ends"
fi
exit "$rc"
```
Replace `tools/box/box_stop.sh` with:
```bash
#!/usr/bin/env bash
# Force-stop the game on the box: Stop-Process -Name SkyrimSE,skse64_loader (by name only), bounded 20 s check.
# Use box_quit.sh (console qqq) for a clean exit; this is for a hung game.
# Gate 0 (Task 45): once nothing runs, box_plugins.sh restore-if-added puts plugins.txt's backup back if this session
# added *SkythonTest.esp; a restore that cannot be done safely is a STOP (exit 3).
set -uo pipefail
source "$(dirname "$0")/box_env.sh"
out="$(bps 60 stop.ps1)"; rc=$?
out="$(printf '%s\n' "$out" | tr -d '\r')"; echo "$out"
if [ "$rc" = 0 ]; then
  "$(dirname "$0")/box_plugins.sh" restore-if-added || rc=3
else
  echo "plugins.txt: not restored (the game is still running; STOP and ask the owner)"
fi
exit "$rc"
```
(`set -e` is dropped in `box_stop.sh` on purpose: the stop result decides whether to restore.)

- [ ] **Step 8: Run the offline suite**

```bash
bash tools/box/test_box_tools.sh | grep -E '^FAIL|BOX TOOLS|6j|--esp|--test-pex|plugins|compose|classify|restore'
```
Expected: no `FAIL`; the new lines include `ok   --esp target is Data\SkythonTest.esp`, `ok   --test-pex: the six names resolve to Data\Scripts\<name>`, `ok   --test-pex refuses: skython.pex`, `ok   place.ps1 TestPexDests: exactly the six Data\Scripts names`, `ok   compose case 2: backup + [CRLF] + *SkythonTest.esp CRLF`, `ok   classify reordered -> OTHER`, `ok   box_quit.sh after EXITED restores the backup (state -> restored)`, `ok   add: plugins.txt differs from the backup -> STOP, nothing written`; last lines `ok   no ssh/scp call reached PATH` and `BOX TOOLS OFFLINE PASS`. Also check the tests bite: change `SkythonTestAlias.pex` to `SkythonTestAliasX.pex` in `place.ps1`'s list, rerun, see `FAIL place.ps1 TestPexDests is not exactly the six names`, then undo the change (`git diff tools/box/win/place.ps1` shows only Step 5's patch again).

- [ ] **Step 9: Commit**

```bash
git add tools/box/box_deploy.sh tools/box/win/place.ps1 tools/box/win/plugins.ps1 tools/box/box_plugins.sh \
        tools/box/box_quit.sh tools/box/box_stop.sh tools/box/test_box_tools.sh
git commit -m "feat(box): Gate 0 deploy list for the test ESP and plugins.txt backup/add/restore"
```

- [ ] **Step 10: Gate 0 check — STOP here unless decision 7 is accepted**

Re-read Step 1's answer. Not accepted (or accepted with another list) → report "Task 45 offline part committed; waiting for owner decision 7" and STOP. Accepted → go on. Nothing below runs before that.

- [ ] **Step 11: Install the new box scripts**

The game must not be running (`box_setup.sh` copies scripts only into `C:\dev\skyrim-python-box`; the box's PowerShell parser checks each first).
```bash
tools/box/box_setup.sh
```
Expected: `parsed plugins.ps1`, `parsed place.ps1` (and the other scripts), `INSTALLED`, then `EXISTS SkyrimPythonGame` and `EXISTS SkyrimPythonShot`.

- [ ] **Step 12: Live check: backup, add, show, restore, show (no game)**

```bash
source tools/box/box_env.sh
tools/box/box_plugins.sh backup
tools/box/box_plugins.sh show | head -3
tools/box/box_plugins.sh add
tools/box/box_plugins.sh show > out/t45-added.txt; head -2 out/t45-added.txt; tail -1 out/t45-added.txt
tools/box/box_plugins.sh restore
tools/box/box_plugins.sh show | head -2
tools/box/box_plugins.sh restore-if-added
bps 60 plugins.ps1 -Mode read | tr -d '\r' | awk '{print $1, $2}'
```
Expected, in order: `BACKED_UP <sha A>` (first time ever; `BACKUP_SAME <sha A>` on a re-run); `plugins.txt sha256 <A>; backup <A>; game IDLE` and `state: BACKUP`; `ADDED <sha B>`; `state: ADDED` and the last line `*SkythonTest.esp`; `RESTORED <sha A>`; `plugins.txt sha256 <A>; backup <A>; game IDLE`, `state: BACKUP`; `plugins.txt: nothing to restore (no add recorded this session)`; the raw read shows `CUR <A>`, `BAK <A>`, `GAME IDLE` (byte-identical to the backup). Any other answer is a STOP (do not retry `add`/`restore` by hand).

- [ ] **Step 13: Record and commit**

Append to `spikes/RESULTS.md`:
```markdown
- **Task 45, Gate 0 box tools (box, Windows-native, <date time>; no game launched):** owner decision 7 accepted on <date> ("<the owner's words>"). plugins.txt path verified read-only: `C:\Users\geoff\AppData\Local\Skyrim Special Edition\plugins.txt` (LOCALAPPDATA of `geoff`, the user of ssh and of the task SkyrimPythonGame; launch.ps1 starts skse64_loader.exe directly), <n> lines, last written <time>; other plugins.txt under C:\ChronicleDev: <list or none>. Backup `C:\dev\skyrim-python-box\plugins.txt.bak` sha256 <A>. Live check: backup, add (`ADDED <B>`, last line `*SkythonTest.esp`), restore (`RESTORED <A>`), byte-identical to the backup. Offline: `test_box_tools.sh` PASS (block 6j). **Owner removal list:** `C:\dev\skyrim-python-box\plugins.txt.bak` (keep until the M1.5 work is done; it is the only copy of the pre-Skython plugins.txt).
```
```bash
git add spikes/RESULTS.md
git commit -m "docs(results): Task 45 Gate 0 tools verified on the box"
```
