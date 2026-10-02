# launch.ps1: run by the scheduled task SkyrimPythonGame (interactive, as the box's configured user, on demand only).
# 0. the install: launch.game (written by box_launch.sh before every launch) names its key (ae1170, ae17104); the
#    game folder comes from box.config and the required SkyrimSE.exe version from config_lib.ps1. A missing
#    launch.game, an unknown key or an unconfigured install is refused: there is no default install;
# 1. refuse if the game or loader runs, or if that install's SkyrimSE.exe is not the version required for it;
# 2. Steam: start it with -silent if it is not running, then wait (bounded, 120 s) for a NEW
#    "[Logged On ...] RecvMsgClientLogOnResponse() : processing complete" line in connection_log.txt
#    (if Steam already runs, its latest state line is "Logged On" and that logon is newer than the last
#    LoggedOff / "Logged In Elsewhere" line, no wait; if Steam exits while we wait, start it -silent once);
# 3. apply launch.env (box_launch.sh --env; only SKYTHON_TEST_* lines) to this process's environment, which
#    skse64_loader.exe and the game inherit, then empty the file so it is used by one launch only;
# 4. start skse64_loader.exe from the game folder; if no SkyrimSE process 20 s later (never started, or exited),
#    retry once (attempt 2 is the only retry). The box's Steam and the owner's Linux Steam share one account: when
#    Linux Steam logs in, the box's Steam is logged off ("Logged In Elsewhere") and shuts down, and a launch at that
#    moment can exit silently (S8, ruling A7). If a LoggedOff line was written during attempt 1, log a warning,
#    re-ensure Steam as in step 2 (bounded, 90 s) and then retry. Steam is only ever started -silent, never stopped.
# Every step goes to launch.log; the outcome goes to launch.result as "<launch stamp> ok|fail ...".
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'config_lib.ps1')
$box  = $BoxDir
$conn = 'C:\Steam\logs\connection_log.txt'
$log  = Join-Path $box 'launch.log'
$stampFile = Join-Path $box 'launch.stamp'
$stamp = if (Test-Path $stampFile) { (Get-Content $stampFile -Raw).Trim() } else { 'no-stamp' }
function Log([string]$m) { "$(Get-Date -Format o) [$stamp] $m" | Out-File -FilePath $log -Append -Encoding UTF8 }
function Result([string]$m) { Log "RESULT $m"; Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $box 'launch.result') -Value "$stamp $m" }
$logonPat = '^\[[^\]]+\] \[Logged On,[^\]]*\].*RecvMsgClientLogOnResponse\(\) : processing complete'
$statePat = '^\[[^\]]+\] \[(Logged On|Logged Off|Connecting|Connected)[,\]]'
# The exact logoff line format is inferred, not verified: the 9/13 investigation saw LoggedOff('Logged In Elsewhere').
# Select-String matches case-insensitively, so this catches either spelling anywhere in a line.
$logoffPat = 'LoggedOff|Logged In Elsewhere'
function LastMatch([string]$pat) {
  if (-not (Test-Path $conn)) { return '' }
  $m = Select-String -Path $conn -Pattern $pat | Select-Object -Last 1
  if ($m) { return $m.Line } else { return '' }
}
function LastLineNo([string]$pat) {
  if (-not (Test-Path $conn)) { return 0 }
  $m = Select-String -Path $conn -Pattern $pat | Select-Object -Last 1
  if ($m) { return $m.LineNumber } else { return 0 }
}
# EnsureSteam: '' once Steam runs and is logged on, else the failure reason. Only ever starts Steam -silent.
function EnsureSteam([int]$waitSec) {
  $before = LastMatch $logonPat
  $steam = Get-Process -Name steam -ErrorAction SilentlyContinue
  $started = $false
  if ($steam) {
    $state = LastMatch $statePat
    $onN = LastLineNo $logonPat; $offN = LastLineNo $logoffPat
    Log "steam already running (pid $($steam[0].Id)); latest state line: $state (logon line $onN, logoff line $offN)"
    if ($state -match '\[Logged On,' -and $onN -gt $offN) { return '' }
  } else {
    Log 'starting steam -silent'
    Start-Process 'C:\Steam\steam.exe' -ArgumentList '-silent'; $started = $true
  }
  $end = (Get-Date).AddSeconds($waitSec); $got = ''
  while ((Get-Date) -lt $end) {
    $now = LastMatch $logonPat
    if ($now -and $now -ne $before) { $got = $now; break }
    if (-not $started -and -not (Get-Process -Name steam -ErrorAction SilentlyContinue)) {
      Log 'steam exited while waiting for logon; starting steam -silent'
      Start-Process 'C:\Steam\steam.exe' -ArgumentList '-silent'; $started = $true
    }
    Start-Sleep -Seconds 2
  }
  if (-not $got) { return "no new Steam ""Logged On"" line within $waitSec s" }
  Log "steam logged on: $got"
  Start-Sleep -Seconds 3
  return ''
}

Log 'start'
$gameFile = Join-Path $box 'launch.game'
$gameKey = if (Test-Path $gameFile) { ([string](Get-Content $gameFile -Raw)).Trim() } else { '' }
$game = Get-GameDir $gameKey
if (-not $game) { Result "fail install '$gameKey' from launch.game is unknown or not in box.config (run tools/box/box_setup.sh)"; exit 3 }
$want = $GameVersions[$gameKey]
Log "install $gameKey at $game (requires SkyrimSE.exe $want)"
$running = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if ($running) { Result ('fail already running: ' + (($running | % { "$($_.Name) $($_.Id)" }) -join ', ')); exit 2 }
$ver = (Get-Item (Join-Path $game 'SkyrimSE.exe')).VersionInfo.FileVersion
Log "SkyrimSE.exe version $ver"
if ($ver -ne $want) { Result "fail SkyrimSE.exe in $game is $ver, not $want as $gameKey requires"; exit 3 }

$why = EnsureSteam 120
if ($why) { Result "fail $why"; exit 4 }

$envFile = Join-Path $box 'launch.env'
if (Test-Path $envFile) {
  foreach ($line in (Get-Content -Encoding UTF8 $envFile)) {
    if ($line -match '^(SKYTHON_TEST_[A-Z0-9_]+)=(.*)$') { Set-Item -Path ('Env:' + $Matches[1]) -Value $Matches[2]; Log "env $($Matches[1])=$($Matches[2])" }
    elseif ($line.Trim()) { Log "ignored launch.env line: $line" }
  }
  Set-Content -Encoding ASCII -NoNewline -Path $envFile -Value ''
}

$pidOk = $null; $reason = ''
foreach ($attempt in 1, 2) {
  $offBefore = LastMatch $logoffPat
  Log "starting skse64_loader.exe (attempt $attempt)"
  Start-Process (Join-Path $game 'skse64_loader.exe') -WorkingDirectory $game
  $end = (Get-Date).AddSeconds(20); $seen = $false
  while ((Get-Date) -lt $end) {
    Start-Sleep -Seconds 2
    if (Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue) { $seen = $true }
  }
  $g = Get-Process -Name SkyrimSE -ErrorAction SilentlyContinue
  if ($g) { $pidOk = $g[0].Id; Log "SkyrimSE running, pid $pidOk"; break }
  if ($seen) { Log "SkyrimSE started then exited within 20 s of attempt $attempt" } else { Log "no SkyrimSE process 20 s after attempt $attempt" }
  $off = LastMatch $logoffPat
  if ($off -and $off -ne $offBefore) {
    $reason = 'steam logged off during launch: Logged In Elsewhere'
    Log "WARNING $off"
    if ($attempt -eq 2) { break }
    Log "WARNING $reason; retrying once"
    $why = EnsureSteam 90
    if ($why) { Result "fail $reason; then $why"; exit 4 }
  } elseif ($attempt -eq 1) { Log 'retrying once' }
}
if ($pidOk) {
  $off = LastMatch $logoffPat
  if ($off -and $off -ne $offBefore) { Log "WARNING steam was logged off elsewhere after the game started: $off" }
  Result "ok pid=$pidOk"; exit 0
}
if ($reason) { Result "fail no SkyrimSE process after two loader starts ($reason)"; exit 5 }
Result 'fail no SkyrimSE process after two loader starts'; exit 5
