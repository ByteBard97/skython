# stop.ps1: force-stop the game and its loader, by process name only, in two stages with a bounded check after each:
#   1. Stop-Process -Name SkyrimSE,skse64_loader -Force                     -> "GONE via Stop-Process"
#   2. if either is still alive: CIM Win32_Process.Terminate, matched by the exact image names
#      SkyrimSE.exe and skse64_loader.exe                                   -> "GONE via CIM Terminate"
#   otherwise "STILL_RUNNING" (exit 1): the game has to be ended by hand on the box.
# Stage 2 exists because on 2026-10-01 a game hung at exit (one thread in a kernel wait) survived Stop-Process and
# taskkill /F, and Win32_Process.Terminate ended it.
function Alive { Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue }
function WaitGone([int]$Seconds) {
  $end = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $end) { if (-not (Alive)) { return $true }; Start-Sleep -Seconds 1 }
  return (-not (Alive))
}
$p = Alive
if (-not $p) { "NOTHING_RUNNING"; exit 0 }
$p | ForEach-Object { "STOPPING $($_.Name) $($_.Id)" }
Stop-Process -Name SkyrimSE,skse64_loader -Force -ErrorAction SilentlyContinue
if (WaitGone 20) { "GONE via Stop-Process"; exit 0 }
(Alive) | ForEach-Object { "still alive after Stop-Process: $($_.Name) $($_.Id); trying CIM Terminate" }
Get-CimInstance Win32_Process -Filter "Name='SkyrimSE.exe'" | Invoke-CimMethod -MethodName Terminate | ForEach-Object { "CIM Terminate SkyrimSE.exe returned $($_.ReturnValue)" }
Get-CimInstance Win32_Process -Filter "Name='skse64_loader.exe'" | Invoke-CimMethod -MethodName Terminate | ForEach-Object { "CIM Terminate skse64_loader.exe returned $($_.ReturnValue)" }
if (WaitGone 20) { "GONE via CIM Terminate"; exit 0 }
"STILL_RUNNING after Stop-Process and CIM Terminate: " + (((Alive) | ForEach-Object { "$($_.Name) $($_.Id)" }) -join ', ')
"End the game by hand on the box (Task Manager); nothing else was tried."; exit 1
