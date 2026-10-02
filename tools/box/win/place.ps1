# place.ps1 -Mode exists|list|place|move -DestB64 <path> [-SrcB64 <staged file or .tar | path to move>] [-Kind file|tar]
#   exists: prints EXISTS or ABSENT.   list: prints the names of the entries in a folder (nothing if absent).
#   place:  file -> Copy-Item to Dest (parent created), prints "PLACED <sha256>";
#           tar  -> extracts into a fresh folder next to the .tar, then robocopy /E merges it into Dest (
#                   files with the same name are replaced, nothing else in Dest is touched), prints "PLACED <n> files".
#   move:   Move-Item Src to Dest (no -Force), both inside Data\SKSE\Plugins; refuses when Src is absent or Dest
#           exists; prints "MOVED". Used for Task 26's startup variants (moved, never deleted, moved back after).
# Only box_deploy.sh calls this, for paths under the game's Data\SKSE\Plugins and for the single files in $Singles:
# Data\Scripts\SkyrimPython.pex (Task 7) and, under owner decision 7 (Gate 0, Task 45), Data\SkythonTest.esp and
# exactly six Data\Scripts\*.pex. Each single file takes -Kind file only.
param([string]$Mode, [string]$DestB64, [string]$SrcB64 = '', [string]$Kind = 'file')
$ErrorActionPreference = 'Stop'
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
$dest = From-B64 $DestB64
# Allowed destinations: the Data\SKSE\Plugins tree, and the single files below outside it: the spike's compiled
# Papyrus script (Task 7; box_deploy.sh --pex) and the Gate 0 list (owner decision 7; box_deploy.sh --esp, --test-pex).
$PexDest = 'C:\ChronicleDev\Stock Game\Data\Scripts\SkyrimPython.pex'
$EspDest = 'C:\ChronicleDev\Stock Game\Data\SkythonTest.esp'
$TestPexDests = @(
  'C:\ChronicleDev\Stock Game\Data\Scripts\Skython.pex',
  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestQuestBase.pex',
  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestQuest.pex',
  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestAlias.pex',
  'C:\ChronicleDev\Stock Game\Data\Scripts\SkythonTestLocationAlias.pex',
  'C:\ChronicleDev\Stock Game\Data\Scripts\QF_SkythonTestQuest_01000800.pex')
$Singles = @($PexDest, $EspDest) + $TestPexDests
$root = 'C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\'
if (-not $dest.StartsWith($root) -and -not ($Singles -ccontains $dest)) { "REFUSED $dest is outside Data\SKSE\Plugins and is not one of the single files"; exit 2 }
# Every mode: no '..' and the normalised path must still be inside (a prefix test alone lets Plugins\..\.. escape).
$fullDest = [IO.Path]::GetFullPath($dest)
if ($dest -match '\.\.' -or -not ($fullDest.StartsWith($root) -or ($Singles -ccontains $fullDest))) { "REFUSED $dest is not inside Data\SKSE\Plugins"; exit 2 }
switch ($Mode) {
  'exists' { if (Test-Path -LiteralPath $dest) { 'EXISTS' } else { 'ABSENT' }; exit 0 }
  'list'   { if (Test-Path -LiteralPath $dest) { Get-ChildItem -LiteralPath $dest -Force | ForEach-Object { $_.Name } }; exit 0 }
  'move' {
    $src = From-B64 $SrcB64
    foreach ($p in @($src, $dest)) {
      if (-not ([IO.Path]::GetFullPath($p)).StartsWith($root) -or $p -match '\.\.') { "REFUSED $p is not inside Data\SKSE\Plugins"; exit 2 }
    }
    if (-not (Test-Path -LiteralPath $src)) { "ERR source absent $src"; exit 1 }
    if (Test-Path -LiteralPath $dest) { "ERR destination exists $dest"; exit 1 }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Move-Item -LiteralPath $src -Destination $dest
    'MOVED'; exit 0
  }
  'place' {
    if (($Singles -ccontains $dest) -and $Kind -ne 'file') { "REFUSED $dest takes a single file"; exit 2 }
    $src = From-B64 $SrcB64
    if ($Kind -eq 'file') {
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
      Copy-Item -LiteralPath $src -Destination $dest -Force
      "PLACED " + (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToLower(); exit 0
    }
    $x = $src + '.d'
    New-Item -ItemType Directory -Force -Path $x | Out-Null
    & tar.exe -xf $src -C $x
    if ($LASTEXITCODE -ne 0) { "ERR tar exit $LASTEXITCODE"; exit 1 }
    # robocopy /E merges the tree into Dest (no /PURGE, /MIR or /MOV: it never deletes); codes 0-7 are success.
    & robocopy.exe $x $dest /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { "ERR robocopy exit $LASTEXITCODE"; exit 1 }
    "PLACED " + (Get-ChildItem -LiteralPath $x -Recurse -File).Count + " files"; exit 0
  }
  default { "ERR unknown mode $Mode"; exit 1 }
}
