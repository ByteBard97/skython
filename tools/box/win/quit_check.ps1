# quit_check.ps1 [-Seconds 120]: after console qqq. Waits (bounded) for SkyrimSE to end, then lists crash logs
# written since the launch stamp. Exit 0 only for a clean exit: process gone and no new crash-*.log.
param([int]$Seconds = 120)
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$snap = Get-Snapshot
$end = (Get-Date).AddSeconds($Seconds)
$gone = $false
while ((Get-Date) -lt $end) {
  if (-not (Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue)) { $gone = $true; break }
  Start-Sleep -Seconds 2
}
$crash = Get-NewCrashLogs $snap
if ($gone) { "EXITED" } else { "STILL_RUNNING after ${Seconds}s" }
if ($crash.Count -gt 0) { "NEW_CRASH_LOGS " + ($crash -join ', ') } else { "NO_NEW_CRASH_LOG" }
if ($gone -and $crash.Count -eq 0) { "CLEAN_EXIT"; exit 0 }
"NOT_CLEAN"; exit 1
