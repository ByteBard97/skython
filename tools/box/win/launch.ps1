# launch.ps1: run by the scheduled task SkyrimPythonGame (interactive, as geoff, on demand only).
# 1. refuse if the game or loader runs, or if the Stock Game exe is not 1.6.1170.0;
# 2. Steam: start it with -silent if it is not running, then wait (bounded, 120 s) for a NEW
#    "[Logged On ...] RecvMsgClientLogOnResponse() : processing complete" line in connection_log.txt
#    (if Steam already runs and its latest state line is "Logged On", no wait);
# 3. start skse64_loader.exe from the game folder; if no SkyrimSE process 15 s later, retry once.
# Every step goes to launch.log; the outcome goes to launch.result as "<launch stamp> ok|fail ...".
$ErrorActionPreference = 'Continue'
$box  = 'C:\dev\skyrim-python-box'
$game = 'C:\ChronicleDev\Stock Game'
$conn = 'C:\Steam\logs\connection_log.txt'
$log  = Join-Path $box 'launch.log'
$stampFile = Join-Path $box 'launch.stamp'
$stamp = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { 'no-stamp' }
function Log([string]$m) { "$(Get-Date -Format o) [$stamp] $m" | Out-File -FilePath $log -Append -Encoding UTF8 }
function Result([string]$m) { Log "RESULT $m"; Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $box 'launch.result') -Value "$stamp $m" }
$logonPat = '^\[[^\]]+\] \[Logged On,[^\]]*\].*RecvMsgClientLogOnResponse\(\) : processing complete'
$statePat = '^\[[^\]]+\] \[(Logged On|Logged Off|Connecting|Connected)[,\]]'
function LastMatch([string]$pat) {
  if (-not (Test-Path $conn)) { return '' }
  $m = Select-String -Path $conn -Pattern $pat | Select-Object -Last 1
  if ($m) { return $m.Line } else { return '' }
}

Log 'start'
$running = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if ($running) { Result ('fail already running: ' + (($running | % { "$($_.Name) $($_.Id)" }) -join ', ')); exit 2 }
$ver = (Get-Item (Join-Path $game 'SkyrimSE.exe')).VersionInfo.FileVersion
Log "SkyrimSE.exe version $ver"
if ($ver -ne '1.6.1170.0') { Result "fail SkyrimSE.exe is $ver, not 1.6.1170.0 (STOP and ask the owner)"; exit 3 }

$before = LastMatch $logonPat
$steam = Get-Process -Name steam -ErrorAction SilentlyContinue
$needWait = $true
if ($steam) {
  $state = LastMatch $statePat
  Log "steam already running (pid $($steam[0].Id)); latest state line: $state"
  if ($state -match '\[Logged On,' -and $state -notmatch 'LoggedOff|Logged In Elsewhere') { $needWait = $false }
} else {
  Log 'starting steam -silent'
  Start-Process 'C:\Steam\steam.exe' -ArgumentList '-silent'
}
if ($needWait) {
  $end = (Get-Date).AddSeconds(120); $got = ''
  while ((Get-Date) -lt $end) {
    $now = LastMatch $logonPat
    if ($now -and $now -ne $before) { $got = $now; break }
    Start-Sleep -Seconds 2
  }
  if (-not $got) { Result 'fail no new Steam "Logged On" line within 120 s'; exit 4 }
  Log "steam logged on: $got"
  Start-Sleep -Seconds 3
}

$elsewhereBefore = LastMatch 'Logged In Elsewhere'
$pidOk = $null
foreach ($attempt in 1, 2) {
  Log "starting skse64_loader.exe (attempt $attempt)"
  Start-Process (Join-Path $game 'skse64_loader.exe') -WorkingDirectory $game
  Start-Sleep -Seconds 15
  $g = Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue
  if ($g) { $pidOk = $g[0].Id; Log "SkyrimSE running, pid $pidOk"; break }
  Log "no SkyrimSE process 15 s after attempt $attempt"
}
$elsewhere = LastMatch 'Logged In Elsewhere'
if ($elsewhere -and $elsewhere -ne $elsewhereBefore) { Log "WARNING steam was logged off elsewhere: $elsewhere" }
if ($pidOk) { Result "ok pid=$pidOk"; exit 0 }
Result 'fail no SkyrimSE process after two loader starts'; exit 5
