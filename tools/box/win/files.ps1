# files.ps1 -Mode new -NamesB64 <base64 of newline-separated names> | -Mode session | -Mode put -NameB64 <n> -DataB64 <c>
#   new: for each name (relative to the SKSE log folder), one line "<name>`t<state>`t<base64>": state NEW (the text
#        written since the last launch stamp, see snapshot_lib.ps1; base64 of its UTF-8), UNCHANGED (nothing written
#        since the stamp), ABSENT, or REFUSED (a path with '..', a drive or a leading slash, or session.json).
#        Exit 3 with NO_STAMP when no launch was stamped. A file rewritten since the stamp with exactly the bytes it
#        had then (same length, same hash) reads as NEW, all of it, when its LastWriteTimeUtc moved past the
#        snapshot's (snapshot_lib.ps1); only an untouched file reads as UNCHANGED.
#   session: the same for Skython\session.json alone (box_session.sh; it holds the bridge token, so only that
#        script reads it, and only into out/box-session.json).
#   put: writes exactly one file, Skython\livetest_control.json (livetest/smoke.py's command channel; Task 31),
#        through a temp file and an atomic replace. Any other name is refused. Nothing is ever deleted.
param([string]$Mode, [string]$NamesB64 = '', [string]$NameB64 = '', [string]$DataB64 = '')
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'snapshot_lib.ps1')
$PutAllowed = 'Skython\livetest_control.json'
function Bad-Name([string]$n) { return ($n -match '\.\.' -or $n -match ':' -or $n -match '^[\\/]' -or $n -match 'session\.json$') }
switch ($Mode) {
  'new' {
    $snap = Get-Snapshot
    if (-not $snap) { 'NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)'; exit 3 }
    foreach ($n in ((From-B64 $NamesB64) -split "`n" | Where-Object { $_ })) {
      $rel = $n -replace '/', '\'
      if (Bad-Name $rel) { "$n`tREFUSED`t"; continue }
      $t = $null
      try { $t = Get-NewText $rel $snap } catch { $t = $null }
      if ($null -eq $t) { "$n`tABSENT`t" }
      elseif ($t -eq '') { "$n`tUNCHANGED`t" }
      else { "$n`tNEW`t" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t)) }
    }
    exit 0
  }
  'session' {
    $snap = Get-Snapshot
    if (-not $snap) { 'NO_STAMP: run box_launch.sh first (launch-snapshot.json missing)'; exit 3 }
    $t = $null
    try { $t = Get-NewText 'Skython\session.json' $snap } catch { $t = $null }
    if ($null -eq $t) { 'ABSENT' } elseif ($t -eq '') { 'UNCHANGED' } else { 'NEW ' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($t)) }
    exit 0
  }
  'put' {
    $rel = (From-B64 $NameB64) -replace '/', '\'
    if ($rel -ne $PutAllowed) { "REFUSED $rel (only $PutAllowed may be written)"; exit 2 }
    $dest = Join-Path $LogDir $rel
    $tmp = $dest + '.linux-tmp'
    [IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($DataB64))
    # The in-game script may be reading the file at that moment: a few short retries.
    for ($i = 1; $i -le 10; $i++) {
      try {
        if (Test-Path -LiteralPath $dest) { [IO.File]::Replace($tmp, $dest, $null) } else { [IO.File]::Move($tmp, $dest) }
        "PUT $rel"; exit 0
      } catch { if ($i -eq 10) { "ERR $($_.Exception.Message)"; exit 1 }; Start-Sleep -Milliseconds 100 }
    }
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
