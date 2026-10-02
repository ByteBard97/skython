# config_lib.ps1 (dot-sourced): the box's own settings, written by box_setup.sh from tools/box/box.local.env to
# C:\dev\skyrim-python-box\box.config, one tab-separated record per line:
#   user<TAB><Windows account the scheduled tasks run as>
#   game<TAB><install key><TAB><game folder, the one holding SkyrimSE.exe>
# The install keys and the SkyrimSE.exe version each one must have are fixed here, not in box.config, so the file
# can name folders but cannot widen what launch.ps1 accepts (the same table is in tools/box/box_env.sh).
$BoxDir = 'C:\dev\skyrim-python-box'
$BoxConfig = Join-Path $BoxDir 'box.config'
$GameVersions = @{ 'ae1170' = '1.6.1170.0'; 'ae17104' = '1.7.104.0' }
# Read-BoxConfig: @{ User = <name or ''>; Games = @{ key = folder } }, or $null when box.config is missing.
function Read-BoxConfig {
  if (-not (Test-Path -LiteralPath $BoxConfig)) { return $null }
  $c = @{ User = ''; Games = @{} }
  foreach ($line in (Get-Content -LiteralPath $BoxConfig -Encoding UTF8)) {
    $f = $line -split "`t"
    if ($f.Count -eq 2 -and $f[0] -ceq 'user') { $c.User = $f[1] }
    elseif ($f.Count -eq 3 -and $f[0] -ceq 'game') { $c.Games[$f[1]] = $f[2] }
  }
  return $c
}
# Get-GameDir <key>: the folder box.config gives a known install key, or $null for an unknown key, a key box.config
# does not list, or a folder that is not a plain absolute path (drive, no '..', no trailing backslash).
function Get-GameDir([string]$key) {
  if ($key -cnotmatch '^[a-z0-9]+$' -or -not $GameVersions.ContainsKey($key)) { return $null }
  $c = Read-BoxConfig
  if (-not $c -or -not $c.Games.ContainsKey($key)) { return $null }
  $d = $c.Games[$key]
  if ($d -notmatch '^[A-Za-z]:\\[^\\]' -or $d -match '\\$' -or $d -match '\.\.' -or $d -match "['`"]") { return $null }
  return $d
}
