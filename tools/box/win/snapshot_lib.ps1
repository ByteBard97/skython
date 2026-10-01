# snapshot_lib.ps1 (dot-sourced): "what is new since launch.stamp" for files under the SKSE log folder.
# A file not in launch-snapshot.json is new (all of it is from this launch). A file whose first L bytes still
# hash to the snapshot (L = its length then) was appended to: only bytes after L are new. Otherwise it was
# rewritten (spdlog truncate, skse64.log): all of it is new. No file times are used (NTFS updates them lazily
# for files held open by the game).
$Box = 'C:\dev\skyrim-python-box'
$LogDir = Join-Path $env:USERPROFILE 'Documents\My Games\Skyrim Special Edition\SKSE'
function Get-Snapshot {
  $p = Join-Path $Box 'launch-snapshot.json'
  if (-not (Test-Path $p)) { return $null }
  return (Get-Content $p -Raw | ConvertFrom-Json)
}
function Read-Shared([string]$path) {
  $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
  try { $ms = New-Object IO.MemoryStream; $fs.CopyTo($ms); return ,$ms.ToArray() } finally { $fs.Close() }
}
function Get-NewText([string]$rel, $snap) {
  $path = Join-Path $LogDir $rel
  if (-not (Test-Path $path)) { return $null }
  $bytes = Read-Shared $path
  $offset = 0
  $entry = $null
  if ($snap) { $entry = $snap.PSObject.Properties[$rel] }
  if ($entry) {
    $L = [int64]$entry.Value.length
    if ($bytes.Length -ge $L) {
      $sha = [Security.Cryptography.SHA256]::Create()
      $h = ($sha.ComputeHash($bytes, 0, [int]$L) | ForEach-Object { $_.ToString('X2') }) -join ''
      if ($h -eq $entry.Value.sha256) { $offset = $L }
    }
  }
  return [Text.Encoding]::UTF8.GetString($bytes, [int]$offset, [int]($bytes.Length - $offset))
}
function Get-NewCrashLogs($snap) {
  if (-not (Test-Path $LogDir)) { return @() }
  return @(Get-ChildItem $LogDir -File -Filter 'crash-*.log' | Where-Object {
    -not ($snap -and $snap.PSObject.Properties[$_.Name]) } | ForEach-Object { $_.Name })
}
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
