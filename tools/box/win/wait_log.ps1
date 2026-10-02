# wait_log.ps1 -NameB64 <log path relative to the SKSE log folder> -PatB64 <.NET regex> [-Seconds 600]
# Bounded wait for a line written after the last launch stamp (see snapshot_lib.ps1). Exit 0 and
# "MATCH <line>" on a match; exit 1 with the tail of this launch's text on timeout; exit 3 with no stamp;
# exit 2 and "DIALOG <title>" when SkyrimSE's main window is SKSE's modal "SKSE Plugin Loader" MessageBox (e.g.
# "A DLL plugin has failed to load correctly"): it blocks the main thread, so no log line will ever come
# (2026-10-01: a game sat behind it for an hour). box_shot.sh shows the dialog.
param([string]$NameB64, [string]$PatB64, [int]$Seconds = 600)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$name = (From-B64 $NameB64) -replace '/', '\'
$pat = From-B64 $PatB64
$snap = Get-Snapshot
if (-not $snap) { "NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)"; exit 3 }
$end = (Get-Date).AddSeconds($Seconds)
$text = $null
while ($true) {
  try { $text = Get-NewText $name $snap } catch { $text = $null }
  if ($text) {
    foreach ($line in ($text -split "`r?`n")) { if ($line -match $pat) { "MATCH $line"; exit 0 } }
  }
  $dlg = Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match '^SKSE Plugin Loader' } | Select-Object -First 1
  if ($dlg) { "DIALOG $($dlg.MainWindowTitle)"; "SKSE modal dialog blocks the game; run tools/box/box_shot.sh to see it"; exit 2 }
  if ((Get-Date) -ge $end) {
    "TIMEOUT no match for /$pat/ in $name after ${Seconds}s"
    if ($text) { "--- last 30 lines written since the launch stamp"; ($text -split "`r?`n") | Select-Object -Last 30 }
    else { "(no text written to $name since the launch stamp)" }
    exit 1
  }
  Start-Sleep -Seconds 2
}
