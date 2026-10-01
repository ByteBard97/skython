# list.ps1 -PathsB64 <base64 of newline-separated paths>: LISTS (removes nothing) what the harness put on the box:
# the given deployed paths, our folder, our scheduled task, spike/plugin logs and the spike throwaway saves.
param([string]$PathsB64 = '')
$my = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition'
function Show($p) { if (Test-Path -LiteralPath $p) { $i = Get-Item -LiteralPath $p -Force; "{0,-10} {1:s} {2}" -f $(if ($i.PSIsContainer) { 'dir' } else { $i.Length }), $i.LastWriteTime, $i.FullName } }
"== deployed into the game by box_deploy.sh (out/box-deployed.txt) =="
if ($PathsB64) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PathsB64)) -split "`n" | Where-Object { $_ } | Sort-Object -Unique | ForEach-Object { $s = Show $_; if ($s) { $s } else { "absent     $_" } } }
else { '(none recorded)' }
"== our folder C:\dev\skyrim-python-box =="
Get-ChildItem 'C:\dev\skyrim-python-box' -Force -ErrorAction SilentlyContinue | ForEach-Object { Show $_.FullName }
"== our scheduled task =="
$t = Get-ScheduledTask -TaskName SkyrimPythonGame -ErrorAction SilentlyContinue
if ($t) { "SkyrimPythonGame -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) } else { '(not registered)' }
"== logs written by the spikes and the plugin =="
Get-ChildItem (Join-Path $my 'SKSE') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'SkyrimPython*' } | ForEach-Object { Show $_.FullName }
"== throwaway saves (spikebase, spytest, s4test) =="
Get-ChildItem (Join-Path $my 'Saves') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(spikebase|spytest|s4test)' } | ForEach-Object { Show $_.FullName }
''
'Nothing was removed. Show this list to the owner; remove only what the owner signs off on, item by item.'
