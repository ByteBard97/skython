$ErrorActionPreference = "Stop"
$src = $PSScriptRoot; $build = Join-Path $src "build"
cmake -S $src -B $build -G "Visual Studio 17 2022" -A x64
if ($LASTEXITCODE -ne 0) { throw "configure failed" }
cmake --build $build --config Release --parallel
if ($LASTEXITCODE -ne 0) { throw "build failed" }
