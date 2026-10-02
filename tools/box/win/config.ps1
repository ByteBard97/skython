# config.ps1: read-only. Prints box.config as this box reads it: the user, and for each install the game folder,
# the SkyrimSE.exe version found there and the version launch.ps1 requires for it. Changes nothing.
#   USER <name>
#   GAME <key> OK|MISMATCH|NO_EXE|NO_LOADER|NOT_CONFIGURED exe=<version or -> want=<version>
#        loader=present|absent dir=<folder>
# Exit 1 when an install is not OK (a wrong or missing SkyrimSE.exe, or no skse64_loader.exe next to it).
. (Join-Path $PSScriptRoot 'config_lib.ps1')
$c = Read-BoxConfig
if (-not $c) { "NO_CONFIG $BoxConfig (run tools/box/box_setup.sh)"; exit 1 }
"USER $($c.User)"
$bad = 0
foreach ($k in ($GameVersions.Keys | Sort-Object)) {
  $d = Get-GameDir $k; $want = $GameVersions[$k]
  if (-not $d) { "GAME $k NOT_CONFIGURED exe=- want=$want loader=- dir=-"; $bad++; continue }
  $loader = if (Test-Path -LiteralPath (Join-Path $d 'skse64_loader.exe')) { 'present' } else { 'absent' }
  $exe = Join-Path $d 'SkyrimSE.exe'
  if (-not (Test-Path -LiteralPath $exe)) { "GAME $k NO_EXE exe=- want=$want loader=$loader dir=$d"; $bad++; continue }
  $v = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
  $state = if ($v -ne $want) { 'MISMATCH' } elseif ($loader -ne 'present') { 'NO_LOADER' } else { 'OK' }
  if ($state -ne 'OK') { $bad++ }
  "GAME $k $state exe=$v want=$want loader=$loader dir=$d"
}
if ($bad) { exit 1 }
