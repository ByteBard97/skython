# list.ps1 -PathsB64 <base64 of newline-separated paths>: LISTS (removes nothing) what the harness put on the box:
# the given deployed paths, our folder, our scheduled tasks, spike/plugin logs and the spike throwaway saves.
param([string]$PathsB64 = '')
$my = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition'
function Show($p) { if (Test-Path -LiteralPath $p) { $i = Get-Item -LiteralPath $p -Force; "{0,-10} {1:s} {2}" -f $(if ($i.PSIsContainer) { 'dir' } else { $i.Length }), $i.LastWriteTime, $i.FullName } }
"== deployed into the game by box_deploy.sh (out/box-deployed.txt) =="
if ($PathsB64) { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PathsB64)) -split "`n" | Where-Object { $_ } | Sort-Object -Unique | ForEach-Object { $s = Show $_; if ($s) { $s } else { "absent     $_" } } }
else { '(none recorded)' }
"== our folder C:\dev\skyrim-python-box =="
Get-ChildItem 'C:\dev\skyrim-python-box' -Force -ErrorAction SilentlyContinue | ForEach-Object { Show $_.FullName }
"== our scheduled tasks =="
foreach ($n in 'SkyrimPythonGame','SkyrimPythonShot') {
  $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
  if ($t) { "$n -> " + ($t.Actions | % { "$($_.Execute) $($_.Arguments)" }) } else { "$n (not registered)" }
}
"== logs written by the spikes and the plugin =="
Get-ChildItem (Join-Path $my 'SKSE') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Skython*' -or $_.Name -like 'SkyrimPython*' } | ForEach-Object { Show $_.FullName }
"== throwaway saves (spikebase, spytest, s4test, skypy_r38_savecall) =="
Get-ChildItem (Join-Path $my 'Saves') -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^(spikebase|spytest|s4test|skypy_r38_savecall)' } | ForEach-Object { Show $_.FullName }
''
'Nothing was removed. Review this list and remove by hand only the items you are sure of, one at a time.'
