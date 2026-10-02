<#
Installs a staged Skython build (plugin\build.ps1 makes it) into a Skyrim Special Edition game folder.

  powershell -ExecutionPolicy Bypass -File tools\install.ps1 <GameDir> [-Staging <dir>] [-CheckOnly]

<GameDir> is the folder that holds SkyrimSE.exe. -Staging defaults to <repo>\out\stage (the folder that holds Data\).
-CheckOnly runs the checks and lists what would be copied, and copies nothing.

Before copying anything it checks, and stops with an explanation if one fails:
  - SkyrimSE.exe is there and its FileVersion is a supported runtime;
  - SKSE: skse64_loader.exe and skse64_<major>_<minor>_<build>.dll for that version;
  - Address Library for SKSE Plugins: Data\SKSE\Plugins\versionlib-<version>.bin for that version.
It never deletes anything. It overwrites a file only if Data\SKSE\Plugins\Skython\install-manifest.txt (written by
an earlier run) lists it; any other file already at a target path stops the install before a single file is copied.
Files that an earlier install put there and this stage no longer has are listed, not removed.
#>
param(
  [Parameter(Mandatory = $true, Position = 0)] [string]$GameDir,
  [string]$Staging = "",
  [switch]$CheckOnly
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $Staging) { $Staging = Join-Path $root "out\stage" }

# Runtimes the loader accepts (plugin/loader/main.cpp, kVerified). Keep the two lists in step.
$Supported = @(
  "1.6.1170.0",
  "1.7.104.0"   # pending verification: allowed by the loader, not yet confirmed by a live run
)
$SkseUrl = "https://www.nexusmods.com/skyrimspecialedition/mods/30379?tab=files"
$AddressLibUrl = "https://www.nexusmods.com/skyrimspecialedition/mods/32444?tab=files"
$ManifestRel = "SKSE\Plugins\Skython\install-manifest.txt"

function Stop-Install([string[]]$lines) {
  foreach ($l in $lines) { Write-Host "install: $l" }
  exit 1
}

# --- the game folder and its runtime version
if (-not (Test-Path -LiteralPath $GameDir -PathType Container)) { Stop-Install "no such folder: $GameDir" }
$GameDir = (Resolve-Path -LiteralPath $GameDir).ProviderPath.TrimEnd('\')
$exe = Join-Path $GameDir "SkyrimSE.exe"
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
  Stop-Install "$exe not found. Pass the folder that holds SkyrimSE.exe (the game folder, not Data)."
}
$v = (Get-Item -LiteralPath $exe).VersionInfo.FileVersionRaw
if (-not $v) { Stop-Install "could not read the FileVersion of $exe" }
$ver = "$($v.Major).$($v.Minor).$($v.Build).$($v.Revision)"
Write-Host "game: $GameDir (SkyrimSE.exe $ver)"
if ($Supported -notcontains $ver) {
  Stop-Install @("SkyrimSE.exe is version $ver; Skython supports $($Supported -join ' and ').",
                 "The plugin would load but keep Python off on any other version.")
}

# --- SKSE and Address Library for this version
$missing = @()
$loader = Join-Path $GameDir "skse64_loader.exe"
$skseDll = Join-Path $GameDir ("skse64_{0}_{1}_{2}.dll" -f $v.Major, $v.Minor, $v.Build)
$addrLib = Join-Path $GameDir ("Data\SKSE\Plugins\versionlib-{0}-{1}-{2}-{3}.bin" -f $v.Major, $v.Minor, $v.Build, $v.Revision)
$needSkse = $false
if (-not (Test-Path -LiteralPath $loader -PathType Leaf)) { $missing += "SKSE: $loader is missing"; $needSkse = $true }
if (-not (Test-Path -LiteralPath $skseDll -PathType Leaf)) {
  $other = @(Get-ChildItem -LiteralPath $GameDir -Filter "skse64_*.dll" -File | ForEach-Object Name)
  $hint = if ($other.Count) { " (found $($other -join ', '): an SKSE build for another game version)" } else { "" }
  $missing += "SKSE: $skseDll is missing$hint"; $needSkse = $true
}
if ($needSkse) {
  $missing += "  Get the SKSE build for runtime $ver from $SkseUrl"
  $missing += "  and copy skse64_loader.exe and the skse64_*.dll into $GameDir. Start the game with skse64_loader.exe."
}
if (-not (Test-Path -LiteralPath $addrLib -PathType Leaf)) {
  $missing += "Address Library: $addrLib is missing"
  $missing += "  Get 'All in one (all game versions)' from $AddressLibUrl"
  $missing += "  and put its versionlib-*.bin files in $(Join-Path $GameDir 'Data\SKSE\Plugins') (or install it with your mod manager)."
}
if ($missing.Count) { Stop-Install (@("not installed: requirements missing") + $missing) }
Write-Host "found: $(Split-Path -Leaf $loader), $(Split-Path -Leaf $skseDll), $(Split-Path -Leaf $addrLib)"

# --- the staged files
$stageData = Join-Path $Staging "Data"
foreach ($need in "SKSE\Plugins\Skython.dll", "SKSE\Plugins\Skython\SkythonCore.dll", "SKSE\Plugins\Skython\python\python314.dll") {
  if (-not (Test-Path -LiteralPath (Join-Path $stageData $need) -PathType Leaf)) {
    Stop-Install "$(Join-Path $stageData $need) is missing: build and stage first (plugin\build.ps1), or pass -Staging <folder that holds Data>"
  }
}
$stageBase = (Resolve-Path -LiteralPath $stageData).ProviderPath.TrimEnd('\')
$files = @(Get-ChildItem -LiteralPath $stageBase -Recurse -File -Force | ForEach-Object { $_.FullName.Substring($stageBase.Length + 1) } | Sort-Object)
if ($files -contains $ManifestRel) { Stop-Install "the stage holds $ManifestRel; that file is written by this script, not staged" }

# --- ownership: a target that exists must be listed in the manifest of an earlier install
$data = Join-Path $GameDir "Data"
$manifest = Join-Path $data $ManifestRel
$owned = @()
if (Test-Path -LiteralPath $manifest -PathType Leaf) {
  $owned = @(Get-Content -LiteralPath $manifest | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object { $_.Trim() })
}
$foreign = @(); $new = 0; $replace = 0
foreach ($rel in $files) {
  $t = Join-Path $data $rel
  if (Test-Path -LiteralPath $t) {
    if ($owned -contains $rel) { $replace++ } else { $foreign += $t }
  } else { $new++ }
}
if ($foreign.Count) {
  Stop-Install (@("not installed: these files already exist and were not installed by this script (not in $manifest):") +
                ($foreign | ForEach-Object { "  $_" }) +
                @("Move them out of the way (or uninstall the mod that owns them) and run again. Nothing was copied."))
}
$stale = @($owned | Where-Object { $files -notcontains $_ -and (Test-Path -LiteralPath (Join-Path $data $_)) })
Write-Host "$($files.Count) files from $stageBase`: $new new, $replace replacing an earlier Skython install"
if ($CheckOnly) {
  $files | ForEach-Object { Write-Host "  Data\$_" }
  Write-Host "check only: nothing copied"
  exit 0
}

# --- copy, then record. The manifest keeps every entry it had, so a later run still owns older files.
foreach ($rel in $files) {
  $t = Join-Path $data $rel
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $t) | Out-Null
  Copy-Item -LiteralPath (Join-Path $stageBase $rel) -Destination $t -Force
}
$all = @($owned + $files | Sort-Object -Unique)
$header = "# Files tools\install.ps1 put under this Data folder (paths relative to Data). Last install: $(Get-Date -Format s)"
Set-Content -LiteralPath $manifest -Value (@($header) + $all) -Encoding UTF8
Write-Host "installed $($files.Count) files into $data"
Write-Host "manifest: $manifest"
if ($stale.Count) {
  Write-Host "left in place from an earlier install (not in this build; remove them yourself if you no longer want them):"
  $stale | ForEach-Object { Write-Host "  Data\$_" }
}
Write-Host "Start the game with skse64_loader.exe. Logs: Documents\My Games\Skyrim Special Edition\SKSE\Skython.log and SKSE\Skython\python.log"
