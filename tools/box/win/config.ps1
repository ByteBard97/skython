# config.ps1: read-only. Prints box.config as this box reads it: the user, and for each install the game folder,
# the SkyrimSE.exe version found there and the version launch.ps1 requires for it. Changes nothing.
#   USER <name>
#   GAME <key> OK|MISMATCH|NO_EXE|NOT_CONFIGURED exe=<version or -> want=<version> dir=<folder>
. (Join-Path $PSScriptRoot 'config_lib.ps1')
$c = Read-BoxConfig
if (-not $c) { "NO_CONFIG $BoxConfig (run tools/box/box_setup.sh)"; exit 1 }
"USER $($c.User)"
foreach ($k in ($GameVersions.Keys | Sort-Object)) {
  $d = Get-GameDir $k; $want = $GameVersions[$k]
  if (-not $d) { "GAME $k NOT_CONFIGURED exe=- want=$want dir=-"; continue }
  $exe = Join-Path $d 'SkyrimSE.exe'
  if (-not (Test-Path -LiteralPath $exe)) { "GAME $k NO_EXE exe=- want=$want dir=$d"; continue }
  $v = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
  $state = if ($v -eq $want) { 'OK' } else { 'MISMATCH' }
  "GAME $k $state exe=$v want=$want dir=$d"
}
