# stamp.ps1: before a launch. Refuses (exit 2) when the game or its loader runs. Otherwise writes
# launch.stamp (box clock, ISO 8601) and launch-snapshot.json: length and SHA-256 of every file under the SKSE
# log folder, so wait_log.ps1 and quit_check.ps1 can tell which log lines and crash logs are from this launch.
$ErrorActionPreference = 'Stop'
$box = 'C:\dev\skyrim-python-box'
$logDir = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition\SKSE'
$running = Get-Process -Name SkyrimSE,skse64_loader -ErrorAction SilentlyContinue
if ($running) { "REFUSED game running: " + (($running | % { "$($_.Name) $($_.Id)" }) -join ', '); exit 2 }
$snap = @{}
if (Test-Path $logDir) {
  Get-ChildItem $logDir -Recurse -File | Where-Object { $_.Length -lt 50MB } | ForEach-Object {
    $rel = $_.FullName.Substring($logDir.Length + 1)
    $snap[$rel] = @{ length = $_.Length; sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
  }
}
$stamp = (Get-Date).ToString('o')
($snap | ConvertTo-Json -Depth 3 -Compress) | Set-Content -Encoding UTF8 (Join-Path $box 'launch-snapshot.json')
Set-Content -Encoding ASCII -NoNewline -Path (Join-Path $box 'launch.stamp') -Value $stamp
"STAMP $stamp files=$($snap.Count)"
