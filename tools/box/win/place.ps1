# place.ps1 -Mode exists|list|place -DestB64 <path> [-SrcB64 <staged file or .tar>] [-Kind file|tar]
#   exists: prints EXISTS or ABSENT.   list: prints the names of the entries in a folder (nothing if absent).
#   place:  file -> Copy-Item to Dest (parent created), prints "PLACED <sha256>";
#           tar  -> extracts into a fresh folder next to the .tar, then robocopy /E merges it into Dest (
#                   files with the same name are replaced, nothing else in Dest is touched), prints "PLACED <n> files".
# Only box_deploy.sh calls this, and only for paths under the game's Data\SKSE\Plugins.
param([string]$Mode, [string]$DestB64, [string]$SrcB64 = '', [string]$Kind = 'file')
$ErrorActionPreference = 'Stop'
function From-B64([string]$s) { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($s)) }
$dest = From-B64 $DestB64
if (-not $dest.StartsWith('C:\ChronicleDev\Stock Game\Data\SKSE\Plugins\')) { "REFUSED $dest is outside Data\SKSE\Plugins"; exit 2 }
switch ($Mode) {
  'exists' { if (Test-Path -LiteralPath $dest) { 'EXISTS' } else { 'ABSENT' }; exit 0 }
  'list'   { if (Test-Path -LiteralPath $dest) { Get-ChildItem -LiteralPath $dest -Force | ForEach-Object { $_.Name } }; exit 0 }
  'place' {
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
