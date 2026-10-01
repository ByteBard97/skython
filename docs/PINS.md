# Pins

Recorded 2026-10-01 (Task 0). Later tasks cite this file instead of re-deriving.

## CommonLibSSE-NG

- Repository `https://github.com/alandtse/CommonLibSSE-NG`, branch `ng`, submodule `plugin/extern/CommonLibSSE-NG`.
- Commit `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (2026-09-30, `chore(release): 10.1.0 [skip ci]`), project version 10.1.0.
- CHANGELOG mentions AE 1.7.99 and Address Library format 5; it does not mention 1.7.104 (support for 1.7.104 is unverified).
- CMake options: `ENABLE_SKYRIM_SE`, `ENABLE_SKYRIM_AE`, `ENABLE_SKYRIM_VR`, `BUILD_TESTS` (as printed by Task 0 Step 3).
- vcpkg packages the build needs (box tree 2026-08-24): `spdlog`, `rapidcsv`, `directxtk`.
- Full signature notes: `docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md`.

### API names checked in the pinned checkout (Task 0 Step 5)

- `IVirtualMachine::FindBoundObject(VMHandle a_handle, const char* a_className, BSTSmartPointer<Object>& a_result) const` (IVirtualMachine.h:99, 3 parameters).
- `DispatchStaticCall(const BSFixedString&, const BSFixedString&, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)` (IVirtualMachine.h:109, VirtualMachine.h:121); `DispatchMethodCall1(BSTSmartPointer<Object>&, fn, args, result&)` and `DispatchMethodCall2(VMHandle, className, fn, args, result&)` (virtual slots 27/28), plus non-virtual `DispatchMethodCall(...)` wrappers with both argument shapes (IVirtualMachine.h:130, 132).
- `void PackHandle(Variable* a_dst, const void* a_src, VMTypeID a_typeID)` (PackUnpack.h:16; the literal `void PackHandle` grep misses it because the header column-aligns with spaces, `PackHandle` alone matches).
- `BGSSaveLoadManager::Load(const char* a_fileName)` and `Load(const char* a_fileName, bool a_checkForMods)` (BGSSaveLoadManager.h:123-124).
- `VMTYPEID`: `BGSBaseAlias` 139, `BGSRefAlias` 140, `BGSLocAlias` 141; `BGSBaseAlias::GetVMTypeID() const` exists (BGSBaseAlias.h:68).
- `TESObjectREFR* BGSRefAlias::GetReference() const` (BGSRefAlias.h:150).
- `SKSE::Init(const PreLoadInterface*, InitInfo = {})` and `Init(const LoadInterface*, InitInfo = {})` (API.h:40,42); `Init(const LoadInterface*, bool a_log)` is `[[deprecated]]` (API.h:76).
- `IVirtualMachine::GetObjectHandlePolicy()` non-virtual wrappers, const and non-const (IVirtualMachine.h:137-138).
- `template<class F> void RegisterFunction(std::string_view a_fnName, std::string_view a_className, F a_callback, bool a_callableFromTasklets = false)` (IVirtualMachine.h:147).
- `MakeFunctionArguments(Args&&...)` and the zero-argument specialisation (FunctionArguments.h:80, 86).
- `bool UI::GameIsPaused()` (UI.h:84).
- `TaskInterface::AddTask(TaskDelegate*)`, `AddTask(std::function<void()>)`, `AddUITask(UIDelegate_v1*)`, `AddUITask(std::function<void()>)` (Interfaces.h:393-396).

## CPython

- Version 3.14.8, standard (GIL) build, x64.
- Runtime: `python-3.14.8-embed-amd64.zip` from python.org, unmodified; ships `python314.dll`, `python314.zip`, `python314._pth`, `vcruntime140.dll`, `vcruntime140_1.dll`, `LICENSE.txt`. The `._pth` file is ignored once `PyConfig.module_search_paths_set = 1`.
- Build inputs: NuGet package `python` 3.14.8, `tools/include/` and `tools/libs/python314.lib`.
- Linux test interpreters (uv): `/home/geoff/.local/share/uv/python/cpython-3.14-linux-x86_64-gnu/bin/python3.14` (`Python 3.14.7`), `/home/geoff/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12` (`Python 3.12.13`). uv offered no 3.14.8 build on 2026-10-01; any 3.14.x has the same ABI.
- Caution: on the dev host a plain `uv python find 3.12` resolves to anaconda's `/home/geoff/anaconda3/bin/python3` (3.12.2), not the uv-managed build; use `uv python find --managed-python 3.12` to get the path above. `uv python find 3.14` resolves to the uv-managed 3.14.7 either way. The 3.14 include dir is `/home/geoff/.local/share/uv/python/cpython-3.14.7-linux-x86_64-gnu/include/python3.14`.

## pybind11

- 3.1.0, headers and LICENSE from the PyPI sdist `pybind11-3.1.0.tar.gz`, vendored at `third_party/pybind11/`.

## Downloaded artifacts (sha256sum format; tools/fetch_python.sh reads the python lines)

```
a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310  python-3.14.8-embed-amd64.zip
ce85f674d9a63029f709cbff7a3da1c6bc5bfcfaefdd9999f98fa0290470c454  python.3.14.8.nupkg
a1cc06b524ab3edca51f8ad3895f9c4fa20b8b19283173dff4ae781449dc9639  pybind11-3.1.0.tar.gz
9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6  json.hpp
44faa038e9c3f9728efbda143748d01124ea0a27f4bf78f35a15d8fab2e039fb  doctest.h
```
