# Building Skython on Windows

This builds the two plugin DLLs, fetches the embedded Python runtime, and stages a folder you can copy into a game. Release is the tested configuration.

## Requirements

Tested with the versions in brackets. Older patch releases of the same major version probably work, but I haven't tried them.

- Windows 10 or 11, x64.
- Visual Studio 2022 or Visual Studio Build Tools 2022 with the "Desktop development with C++" workload (MSVC v143 and a Windows 10/11 SDK). [Build Tools 17.14.34, MSVC toolset 14.44.35207 (cl 19.44.35228), Windows SDK 10.0.26100.0]
- CMake 3.21 or newer on PATH: the installer from cmake.org, or the "C++ CMake tools for Windows" component of Visual Studio. [3.31.6 from cmake.org]
- Git for Windows. [2.49.0]
- vcpkg as a git clone, with `VCPKG_ROOT` pointing at it. `plugin/vcpkg.json` pins a vcpkg baseline commit, so the clone needs its full history (no `--depth`). [vcpkg tool 2026-09-26]
- Internet access for the first build: vcpkg downloads and builds spdlog, fmt, DirectXTK and friends, and the build script downloads CPython 3.14.8 from python.org and nuget.org.

Ninja is not needed; the build uses the Visual Studio generator. The first build took about 10 minutes on my machine, most of it vcpkg building its packages.

## Get vcpkg

Skip this if you already have a vcpkg clone; just set `VCPKG_ROOT`.

```
git clone https://github.com/microsoft/vcpkg C:\dev\vcpkg
C:\dev\vcpkg\bootstrap-vcpkg.bat -disableMetrics
setx VCPKG_ROOT C:\dev\vcpkg
```

`setx` applies to new windows; in the current one also run `$env:VCPKG_ROOT = "C:\dev\vcpkg"`.

## Clone

```
git clone --recurse-submodules <repository url> skython
cd skython
```

CommonLibSSE-NG is a submodule (`plugin/extern/CommonLibSSE-NG`), and it has its own submodule. If you cloned without `--recurse-submodules`, run `git submodule update --init --recursive`.

## Build

Open "Developer PowerShell for VS 2022" (or any PowerShell where `cmake` and `git` are on PATH), go to the repository and run:

```
powershell -ExecutionPolicy Bypass -File plugin\build.ps1
```

`-ExecutionPolicy Bypass` is there because the script is not signed. The Developer PowerShell shortcut starts an x86 environment; that does not matter here, the preset builds x64.

What it does:

1. Downloads `python-3.14.8-embed-amd64.zip` (python.org) and `python.3.14.8.nupkg` (nuget.org) into `third_party\cpython\downloads`, checks both against the SHA-256 lines in `docs/PINS.md`, and unpacks them to `third_party\cpython\embed` and `third_party\cpython\nuget`. A mismatch stops the build. Later runs reuse the files.
2. Configures with the `vs2022` preset in `plugin/CMakePresets.json` (Visual Studio 17 2022, x64, vcpkg triplet `x64-windows-static-md`) and builds. The first build compiles the vcpkg packages and CommonLibSSE-NG and takes a while; later builds are quick. The script prints the warning count at the end; it should be 0.
3. Stages the install layout in `out\stage`. An existing `out\stage` is renamed to `out\stage-<date-time>`, not deleted.

Options: `-Config Debug`, `-VcpkgRoot <dir>` (instead of `VCPKG_ROOT`), `-Stage <dir>`, `-NoStage`.

The DLLs land in `plugin\build\Release\`, the build log in `plugin\build\build-Release.log`. Once `build.ps1` has fetched CPython you can also rebuild from `plugin` with `cmake --preset vs2022` and `cmake --build --preset release` (no staging).

The staged folder:

```
out\stage\Data\
  SKSE\Plugins\Skython.dll              loader, the DLL SKSE loads
  SKSE\Plugins\Skython.ini              settings (plugin\config\Skython.ini, dev bridge off)
  SKSE\Plugins\Skython\SkythonCore.dll  core, links CPython
  SKSE\Plugins\Skython\python\          CPython 3.14.8 embeddable runtime, unmodified
  SKSE\Plugins\Skython\lib\skython\     the skython Python package (python\src\skython)
  Scripts\Skython.pex                   Papyrus side of the API (Skython.Send)
  Source\Scripts\Skython.psc            its source, to compile your own scripts against
```

## Tests

The plugin itself is tested in a running game, which needs more setup than this page covers. The core library's unit tests build and run on Windows with no game:

```
cmake -S core -B core\build-win -G "Visual Studio 17 2022" -A x64
cmake --build core\build-win --config Release --parallel
ctest --test-dir core\build-win -C Release --output-on-failure
```

## Install

You need, in the game folder:

- SKSE for your game version: https://www.nexusmods.com/skyrimspecialedition/mods/30379?tab=files
- Address Library for SKSE Plugins: https://www.nexusmods.com/skyrimspecialedition/mods/32444?tab=files

Verified against 1.6.1170 (SkyrimSE.exe FileVersion 1.6.1170.0) and 1.7.104 (1.7.104.0), both with the full live test suite. On any other version the plugin loads but leaves Python off.

Then:

```
powershell -ExecutionPolicy Bypass -File tools\install.ps1 "C:\Games\Skyrim Special Edition"
```

The argument is the folder that holds `SkyrimSE.exe`. The script reads the exe's version, checks for `skse64_loader.exe`, the matching `skse64_<version>.dll` and `Data\SKSE\Plugins\versionlib-<version>.bin`, and tells you what is missing and where to get it. It then copies `out\stage\Data` into the game's `Data` folder and records what it copied in `Data\SKSE\Plugins\Skython\install-manifest.txt`. It never deletes, and it never overwrites a file that is not in that manifest. `-CheckOnly` runs the checks and copies nothing; `-Staging <dir>` installs from another stage folder.

With Mod Organizer 2 or Vortex, skip the script: make a new empty mod and copy the contents of `out\stage\Data` into it.

Start the game with `skse64_loader.exe`. Logs go to `Documents\My Games\Skyrim Special Edition\SKSE\Skython.log` (plugin) and `...\SKSE\Skython\python.log` (Python). Put your scripts in `Data\SKSE\Plugins\Skython\scripts\` (created on the first start).

## Troubleshooting

- `no vcpkg at ...`: set `VCPKG_ROOT` or pass `-VcpkgRoot`.
- `plugin\extern\CommonLibSSE-NG is empty`: `git submodule update --init --recursive`.
- `SHA-256 mismatch`: the download is corrupt or changed upstream. Move the file in `third_party\cpython\downloads` aside and run again.
- `... exists but was not unpacked from ...`: an old `third_party\cpython\embed` or `nuget` folder is in the way. Move it aside and run again.
- vcpkg errors about the baseline: your vcpkg clone is shallow or old. `git -C $env:VCPKG_ROOT fetch --unshallow` or `git pull`.
