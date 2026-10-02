# wait_log.ps1 -NameB64 <log path relative to the SKSE log folder> -PatB64 <.NET regex> [-Seconds 600]
# Bounded wait for a line written after the last launch stamp (see snapshot_lib.ps1). Exit 0 and
# "MATCH <line>" on a match; exit 1 with the tail of this launch's text on timeout; exit 3 with no stamp;
# exit 2 and "DIALOG <windows.txt line>" when SKSE's modal "SKSE Plugin Loader" MessageBox (e.g. "A DLL plugin
# has failed to load correctly") blocks the game's main thread, so no log line will ever come (2026-10-01: a game
# sat behind it for an hour). The dialog probe: over ssh MainWindowTitle is empty (no desktop), so every 15 s,
# while SkyrimSE runs, it starts our task SkyrimPythonShot (shot.ps1, interactive) and waits up to 10 s for a new
# windows.txt with a SkyrimSE line containing "SKSE Plugin Loader". A missing task or no new windows.txt just
# means "no verdict": the wait goes on. A matching log line always wins over the dialog.
param([string]$NameB64, [string]$PatB64, [int]$Seconds = 600)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$name = (From-B64 $NameB64) -replace '/', '\'
$pat = From-B64 $PatB64
$snap = Get-Snapshot
if (-not $snap) { "NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)"; exit 3 }
$end = (Get-Date).AddSeconds($Seconds)
$box = 'C:\dev\skyrim-python-box'
# Probe-Dialog: the SkyrimSE "SKSE Plugin Loader" line from a windows.txt written after now, or $null.
function Probe-Dialog {
  try {
    if (-not (Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue)) { return $null }
    $t0 = (Get-Date).ToUniversalTime()
    Start-ScheduledTask -TaskName SkyrimPythonShot -ErrorAction Stop
    $until = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $until) {
      Start-Sleep -Seconds 1
      $w = Get-Item (Join-Path $box 'windows.txt') -ErrorAction SilentlyContinue
      if ($w -and $w.LastWriteTimeUtc -gt $t0) {
        $hit = Get-Content $w.FullName -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*SkyrimSE\s.*SKSE Plugin Loader' } | Select-Object -First 1
        if ($hit) { return $hit.Trim() } else { return $null }
      }
    }
  } catch { }
  return $null
}
$nextProbe = (Get-Date).AddSeconds(15)
$text = $null
while ($true) {
  try { $text = Get-NewText $name $snap } catch { $text = $null }
  if ($text) {
    foreach ($line in ($text -split "`r?`n")) { if ($line -match $pat) { "MATCH $line"; exit 0 } }
  }
  if ((Get-Date) -ge $nextProbe) {
    $dlg = Probe-Dialog
    $nextProbe = (Get-Date).AddSeconds(15)
    if ($dlg) {
      try { $text = Get-NewText $name $snap } catch { $text = $null }
      if ($text) { foreach ($line in ($text -split "`r?`n")) { if ($line -match $pat) { "MATCH $line"; exit 0 } } }
      "DIALOG $dlg"
      "SKSE modal dialog blocks the game; screenshot already at C:\dev\skyrim-python-box\shot.png; fetch with tools/box/box_shot.sh or scp"
      exit 2
    }
  }
  if ((Get-Date) -ge $end) {
    "TIMEOUT no match for /$pat/ in $name after ${Seconds}s"
    if ($text) { "--- last 30 lines written since the launch stamp"; ($text -split "`r?`n") | Select-Object -Last 30 }
    else { "(no text written to $name since the launch stamp)" }
    exit 1
  }
  Start-Sleep -Seconds 2
}
