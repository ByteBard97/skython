# stop.ps1: force-stop the game and its loader, by process name only; bounded check that they are gone.
$p = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if (-not $p) { "NOTHING_RUNNING"; exit 0 }
$p | ForEach-Object { "STOPPING $($_.Name) $($_.Id)" }
Stop-Process -Name SkyrimSE,skse64_loader -Force -ErrorAction SilentlyContinue
$end = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $end) {
  if (-not (Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue)) { "GONE"; exit 0 }
  Start-Sleep -Seconds 1
}
"STILL_RUNNING after 20 s: the process ignores termination (seen 2026-10-01: hung at exit, one thread in a kernel wait,"
"still holding 45978). STOP and ask the owner; a box reboot is their call."; exit 1
