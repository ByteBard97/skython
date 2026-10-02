param([string]$VcpkgRoot = "C:\vcpkg", [string]$Config = "Release")
$ErrorActionPreference = "Stop"
$toolchain = Join-Path $VcpkgRoot "scripts\buildsystems\vcpkg.cmake"
$src = $PSScriptRoot; $build = Join-Path $src "build"
# Test hooks only when plugin/build-options.local (gitignored) says SPY_TEST_HOOKS=ON; always passed explicitly,
# so an earlier ON in the CMake cache cannot leak into a normal build.
$hooks = "OFF"
$opts = Join-Path $src "build-options.local"
if ((Test-Path $opts) -and ((Get-Content $opts -Raw) -match 'SPY_TEST_HOOKS=ON')) { $hooks = "ON" }
cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64 "-DCMAKE_TOOLCHAIN_FILE=$toolchain" -DVCPKG_TARGET_TRIPLET=x64-windows-static-md "-DSPY_TEST_HOOKS=$hooks"
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build $build --config $Config --parallel
if ($LASTEXITCODE -ne 0) { throw "build failed" }
Write-Output "SPY_TEST_HOOKS=$hooks"
