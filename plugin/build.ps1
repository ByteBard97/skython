<#
Builds Skython.dll (loader) and SkythonCore.dll (core) and stages an install folder. See docs/BUILDING.md.

  powershell -ExecutionPolicy Bypass -File plugin\build.ps1 [-Config Release] [-VcpkgRoot <dir>] [-Stage <dir>] [-NoStage]

Steps:
  1. Fetch the pinned CPython 3.14.8 runtime and build inputs into third_party\cpython\ (python.org embed zip and
     the nuget.org "python" package), check their SHA-256 against docs\PINS.md, unpack them. Same layout and .sha256
     stamps as tools/fetch_python.sh, so either script accepts the other's tree.
  2. Configure with the "vs2022" preset (plugin\CMakePresets.json; vcpkg toolchain from VCPKG_ROOT) and build.
     The build log is plugin\build\build-<Config>.log; the warning count is printed at the end.
  3. Stage the install layout in <repo>\out\stage (or -Stage): Data\SKSE\Plugins\Skython.dll, Skython.ini,
     Skython\SkythonCore.dll, Skython\python\, Skython\lib\skython\, plus Data\Scripts\Skython.pex and
     Data\Source\Scripts\Skython.psc. tools\install.ps1 copies that folder into a game. An existing stage folder is
     moved aside to stage-<timestamp>, never deleted.

If third_party\cpython\nuget exists without a .sha256 stamp (tools/remote_build.sh copies just the headers and the
import library there), the build uses it as is, says so, and does not stage. This script deletes nothing.
Test hooks: only when plugin\build-options.local (gitignored) says SPY_TEST_HOOKS=ON. The value is always passed
explicitly, so an earlier ON in the CMake cache cannot leak into a normal build.
#>
param(
  [string]$VcpkgRoot = $(if ($env:VCPKG_ROOT) { $env:VCPKG_ROOT } else { "C:\vcpkg" }),
  [ValidateSet("Release", "Debug", "RelWithDebInfo")] [string]$Config = "Release",
  [string]$Stage = "",
  [switch]$NoStage
)
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$root = Split-Path -Parent $src
$build = Join-Path $src "build"
function Fail([string]$msg) { throw "build.ps1: $msg" }

# --- 0. prerequisites
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) { Fail "cmake is not on PATH (open a Developer PowerShell for VS 2022, or install CMake 3.21+)" }
$toolchain = Join-Path $VcpkgRoot "scripts\buildsystems\vcpkg.cmake"
if (-not (Test-Path -LiteralPath $toolchain)) { Fail "no vcpkg at '$VcpkgRoot' ($toolchain is missing); set VCPKG_ROOT or pass -VcpkgRoot" }
if (-not (Test-Path -LiteralPath (Join-Path $src "extern\CommonLibSSE-NG\CMakeLists.txt"))) {
  Fail "plugin\extern\CommonLibSSE-NG is empty: run 'git submodule update --init --recursive' in the repository"
}

# --- 1. CPython runtime and build inputs
$cpy = Join-Path $root "third_party\cpython"
$pins = Join-Path $root "docs\PINS.md"
$archives = @(
  @{ File = "python-3.14.8-embed-amd64.zip"; Dest = "embed"; Url = "https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip" },
  @{ File = "python.3.14.8.nupkg"; Dest = "nuget"; Url = "https://api.nuget.org/v3-flatcontainer/python/3.14.8/python.3.14.8.nupkg" }
)
function Get-Pin([string]$name) {
  foreach ($line in Get-Content -LiteralPath $pins) {
    if ($line -match '^([0-9a-f]{64})  (\S+)$' -and $Matches[2] -eq $name) { return $Matches[1] }
  }
  Fail "docs\PINS.md has no sha256 line for $name"
}
function Get-Sha256([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }

$verified = $true
$nuget = Join-Path $cpy "nuget"
if ((Test-Path -LiteralPath $nuget) -and -not (Test-Path -LiteralPath (Join-Path $nuget ".sha256"))) {
  # The tools/remote_build.sh case: headers and import library only, checked on the machine that sent them.
  $verified = $false
  Write-Output "note: $nuget has no .sha256 stamp (not unpacked by this script or fetch_python.sh); building against it unverified and not staging"
} else {
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $ProgressPreference = "SilentlyContinue"
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $downloads = Join-Path $cpy "downloads"
  New-Item -ItemType Directory -Force -Path $downloads | Out-Null
  foreach ($a in $archives) {
    $want = Get-Pin $a.File
    $arch = Join-Path $downloads $a.File
    if (-not (Test-Path -LiteralPath $arch)) {
      Write-Output "downloading $($a.Url)"
      $part = "$arch.part"
      Invoke-WebRequest -UseBasicParsing -Uri $a.Url -OutFile $part
      Move-Item -LiteralPath $part -Destination $arch -Force
    }
    $got = Get-Sha256 $arch
    if ($got -ne $want) {
      Fail ("SHA-256 mismatch for $arch`n  expected $want (docs\PINS.md)`n  got      $got`n" +
            "  nothing was unpacked; move the file aside and run again, and if it still differs, check the download before changing docs\PINS.md")
    }
    $out = Join-Path $cpy $a.Dest
    $stamp = Join-Path $out ".sha256"
    if ((Test-Path -LiteralPath $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $want)) {
      Write-Output "ok $($a.File) ($want)"
      continue
    }
    if (Test-Path -LiteralPath $out) { Fail "$out exists but was not unpacked from $($a.File); move it out of the way and run again" }
    [IO.Compression.ZipFile]::ExtractToDirectory($arch, $out)
    Set-Content -LiteralPath $stamp -Value $want -NoNewline -Encoding Ascii
    Write-Output "unpacked $($a.File) -> $out"
  }
  foreach ($need in "embed\python314.dll", "embed\python314.zip", "embed\LICENSE.txt", "nuget\tools\include\Python.h", "nuget\tools\libs\python314.lib") {
    if (-not (Test-Path -LiteralPath (Join-Path $cpy $need))) { Fail "$(Join-Path $cpy $need) is missing after unpacking" }
  }
  Write-Output "CPython 3.14.8 runtime and build inputs verified against docs\PINS.md"
}

# --- 2. configure and build
$hooks = "OFF"
$opts = Join-Path $src "build-options.local"
if ((Test-Path -LiteralPath $opts) -and ((Get-Content -LiteralPath $opts -Raw) -match 'SPY_TEST_HOOKS=ON')) { $hooks = "ON" }
$env:VCPKG_ROOT = $VcpkgRoot
Push-Location $src
try {
  cmake --preset vs2022 "-DSPY_TEST_HOOKS=$hooks"
  if ($LASTEXITCODE -ne 0) { Fail "configure failed" }
} finally { Pop-Location }
$log = Join-Path $build "build-$Config.log"
cmake --build $build --config $Config --parallel | Tee-Object -FilePath $log
if ($LASTEXITCODE -ne 0) { Fail "build failed (log: $log)" }
# MSBuild repeats each warning in its summary, so count distinct lines.
$warnings = @(Select-String -LiteralPath $log -Pattern '\bwarning [A-Z]+\d+' | ForEach-Object { $_.Line.Trim() } | Sort-Object -Unique)
Write-Output "SPY_TEST_HOOKS=$hooks"
Write-Output "warnings: $($warnings.Count) (log: $log)"
$outDir = Join-Path $build $Config
foreach ($dll in "Skython.dll", "SkythonCore.dll") {
  if (-not (Test-Path -LiteralPath (Join-Path $outDir $dll))) { Fail "$dll was not produced in $outDir" }
}

# --- 3. stage
if ($NoStage -or -not $verified) { return }
if (-not $Stage) { $Stage = Join-Path $root "out\stage" }
if (Test-Path -LiteralPath $Stage) {
  $aside = "$Stage-$(Get-Date -Format yyyyMMdd-HHmmss)"
  Move-Item -LiteralPath $Stage -Destination $aside
  Write-Output "moved the previous stage folder to $aside"
}
# Copy-Tree <from> <to>: every file under <from>, except .sha256 stamps, __pycache__ and *.pyc.
function Copy-Tree([string]$from, [string]$to) {
  $base = (Resolve-Path -LiteralPath $from).ProviderPath.TrimEnd('\')
  foreach ($f in Get-ChildItem -LiteralPath $base -Recurse -File -Force) {
    $rel = $f.FullName.Substring($base.Length + 1)
    if ($rel -eq ".sha256" -or $rel -like "*.pyc" -or $rel -match '(^|\\)__pycache__\\') { continue }
    $dest = Join-Path $to $rel
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dest) | Out-Null
    Copy-Item -LiteralPath $f.FullName -Destination $dest
  }
}
$plugins = Join-Path $Stage "Data\SKSE\Plugins"
$own = Join-Path $plugins "Skython"
foreach ($d in $plugins, $own, (Join-Path $own "lib"), (Join-Path $Stage "Data\Scripts"), (Join-Path $Stage "Data\Source\Scripts")) {
  New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Copy-Item -LiteralPath (Join-Path $outDir "Skython.dll") -Destination $plugins
Copy-Item -LiteralPath (Join-Path $src "config\Skython.ini") -Destination $plugins
Copy-Item -LiteralPath (Join-Path $outDir "SkythonCore.dll") -Destination $own
Copy-Tree (Join-Path $cpy "embed") (Join-Path $own "python")
Copy-Tree (Join-Path $root "python\src\skython") (Join-Path $own "lib\skython")
Copy-Item -LiteralPath (Join-Path $src "papyrus\compiled\Skython.pex") -Destination (Join-Path $Stage "Data\Scripts")
Copy-Item -LiteralPath (Join-Path $src "papyrus\Skython.psc") -Destination (Join-Path $Stage "Data\Source\Scripts")
$n = @(Get-ChildItem -LiteralPath (Join-Path $Stage "Data") -Recurse -File).Count
Write-Output "staged $n files in $Stage\Data (install with: tools\install.ps1 <game folder>)"
