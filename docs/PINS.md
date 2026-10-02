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
- `void PackHandle(Variable* a_dst, const void* a_src, VMTypeID a_typeID)` (PackUnpack.h:16; the literal `void PackHandle` grep misses it because the header column-aligns with spaces, `PackHandle` alone matches). A PackHandle value is typed as the form's own class; a native parameter declared as an ancestor class needs `SetObject(obj, declared.GetRawType())` (Task 28).
- `BGSSaveLoadManager::Load(const char* a_fileName)` and `Load(const char* a_fileName, bool a_checkForMods)` (BGSSaveLoadManager.h:123-124).
- `VMTYPEID`: `BGSBaseAlias` 139, `BGSRefAlias` 140, `BGSLocAlias` 141; `BGSBaseAlias::GetVMTypeID() const` exists (BGSBaseAlias.h:68).
- `TESObjectREFR* BGSRefAlias::GetReference() const` (BGSRefAlias.h:150).
- `SKSE::Init(const PreLoadInterface*, InitInfo = {})` and `Init(const LoadInterface*, InitInfo = {})` (API.h:40,42); `Init(const LoadInterface*, bool a_log)` is `[[deprecated]]` (API.h:76).
- `IVirtualMachine::GetObjectHandlePolicy()` non-virtual wrappers, const and non-const (IVirtualMachine.h:137-138).
- `template<class F> void RegisterFunction(std::string_view a_fnName, std::string_view a_className, F a_callback, bool a_callableFromTasklets = false)` (IVirtualMachine.h:147).
- `MakeFunctionArguments(Args&&...)` and the zero-argument specialisation (FunctionArguments.h:80, 86).
- `bool UI::GameIsPaused()` (UI.h:84).
- `TaskInterface::AddTask(TaskDelegate*)`, `AddTask(std::function<void()>)`, `AddUITask(UIDelegate_v1*)`, `AddUITask(std::function<void()>)` (Interfaces.h:393-396).

### S7 signatures checked in the pinned checkout (Task 9 Step 0, 2026-10-01)

All calls in Task 9's S7 code match the pinned headers; no call had to change.
- `FindBoundObject(VMHandle, const char* a_className, BSTSmartPointer<Object>& a_result) const` (IVirtualMachine.h:99, VirtualMachine.h:111): 3 parameters, as above.
- `PackHandle(Variable*, const void*, VMTypeID)` (PackUnpack.h:16). A PackHandle value is typed as the form's own class; a native parameter declared as an ancestor class needs `SetObject(obj, declared.GetRawType())` (Task 28).
- `DispatchMethodCall(BSTSmartPointer<Object>&, const BSFixedString& fn, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)` and `DispatchMethodCall(VMHandle, const BSFixedString& className, const BSFixedString& fn, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)` (IVirtualMachine.h:130, 132; virtual slots `DispatchMethodCall1/2` 27/28).
- `BGSBaseAlias::GetVMTypeID() const` (BGSBaseAlias.h:68); `VMTYPEID` BGSBaseAlias 139, BGSRefAlias 140, BGSLocAlias 141.
- `IFunction`: `GetReturnType() const -> TypeInfo` (04), `GetParamCount()` (05), `GetParam(std::uint32_t, BSFixedString&, TypeInfo&) const` (06), `GetIsNative()` (08), `GetIsStatic()` (09) (IFunction.h:51-56).
- `ObjectTypeInfo`: `GetParent()` (const and non-const, :127-128), `GetNumProperties()`, `GetPropertyIter()`, `GetGlobalFuncIter()`, `GetMemberFuncIter()` (:172-182).
- `BSScript::Array`: `operator[](size_type)` (:29-30), `size()` (:59), `type_info()` (:63-64, the element type).
- `VirtualMachine::attachedScriptsLock` (`mutable BSSpinLock`, :219) and `attachedScripts` (`BSTHashMap<VMHandle, BSTSmallSharedArray<AttachedScript>>`, :220); `AttachedScript : BSTPointerAndFlags<BSTSmartPointer<Object>, 1>` (AttachedScript.h:13, size 8).
- `CreateArray(const TypeInfo&, std::uint32_t, BSTSmartPointer<Array>&)` and `CreateArray(TypeInfo::RawType, const BSFixedString&, std::uint32_t, BSTSmartPointer<Array>&)` (IVirtualMachine.h:126-127); `GetScriptObjectType(const BSFixedString&, BSTSmartPointer<ObjectTypeInfo>&)` and the `VMTypeID` overload (:139-140).
- `Object::GetHandle() const` (:19), `Object::Resolve(VMTypeID) const -> void*` (:26), `Object::GetProperty(const BSFixedString&)` (const and non-const, :31-32).
- `TESForm::GetAllForms()` (static, returns the map pointer and its lock, TESForm.h:192).
- `RE::Main` (Main.h): `REX::W32::HWND wnd` (+0x18), `std::uint32_t threadID` (+0x28); REX has `GetCurrentThreadId` (REX/W32/KERNEL32.h:455) but no `GetWindowThreadProcessId` wrapper, so the tid probe uses `<windows.h>`.

Runtime findings from S7 on the box (2026-10-01; details in the S7 row of `spikes/RESULTS.md`):
- `TypeInfo::GetTypeInfo()` on an OBJECT-ARRAY type is wrong in this commit: it masks the raw value with `~kObjectArray` (~0xB), which also clears bit 3 of the class pointer. ObjectTypeInfo pointers were observed only 8-byte aligned (Form 0x...ea8, Form[] 0x...ea9 -> GetTypeInfo 0x...ea0). It read a garbage name in one launch and crashed the game in the next (AV in ucrtbase strlen); DevBench's papyrus `describe` crashed the same way. For an object array the class pointer is `raw & ~1`; call `GetTypeInfo()` only when `IsObject()`.
- `Array::type_info()` (the element type) reads 0 (None) on arrays returned by SKSE's Utility natives (CreateFormArray, ResizeFormArray); use the Variable's own type (`array_raw & ~1`) for the element class. An array built with `CreateArray(TypeInfo(array_raw & ~1), n)` is accepted as a Form[] argument; one built from a copied `type_info()` (0) is not.
- `ObjectTypeInfo::GetName()` returns `utility` (lowercase) for Utility; Actor/ObjectReference/Form/Quest keep their case. `GetScriptObjectType("actor")` finds Actor. Engine natives report parameter names `param1..N` from `IFunction::GetParam`; script-defined functions report their .psc parameter names (Task 27 in `spikes/RESULTS.md`: ArmorAddon.GetMaskForSlot).
- `GetScriptObjectType` on an unknown name: the first call returns false; the second returns true with an empty stub (0 member/global functions, 0 properties, no parent, linkedValid 2); `GetScriptObjectTypeNoLoad` stays false.
- `DispatchMethodCall(handle, className, ...)` returns false (no crash) when the form has no bound object (e.g. a quest with no attached script); `PackHandle` binds it (FindBoundObject true afterwards) and the `BSTSmartPointer<Object>&` overload dispatches. `FindBoundObject` accepts a parent class of the bound object (player: Actor, ObjectReference, Form true; Quest false).

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

## Plugin notes (Task 25)
- The core DLL's SKSEPluginInfo is not needed by CommonLib: SKSE::Init reads PluginVersionData only for name/author/version and falls back to the module file name. Kept as harmless.
- The core's RegisterListener uses the loader's plugin handle (GetPluginHandle() is captured during the loader's load call). SKSE may drop a second listener for the same handle and sender, so the loader registers nothing beyond SKSE::Init; every registration happens in the core.

## Papyrus (Task 46)
- Compiler: SimpleSkyrim `Stock Game/Papyrus Compiler/PapyrusCompiler.exe` sha256 f441afa877ce0b315c6b613db7fe700a7bbc66b71a72fc4d0d8bc94577ceb0ce, run headless by `tools/papyrus_compile.sh` (GE-Proton10-14 `wine64`, prefix `compatdata/4190904831`, DISPLAY and WAYLAND_DISPLAY unset; spike S5).
- Imports and flags: the game's `Data/Scripts.zip` sha256 45ff21e55be76f3bce86385770cba878ec8dacabb8df4cd3e86976fbdf3ebdc6, `Source/Scripts/*.psc` and `TESV_Papyrus_Flags.flg` extracted to `out/papyrus-import/vanilla` (gitignored; vanilla sources are never committed).
- `.pex` hashes: "raw" is the committed file; "stable" is `tools/papyrus_compile.sh --stable-hash` (compile time and source modification time zeroed). A rebuild is checked against the stable hash; it holds for the same source file name, machine and user (`geoff` on this machine).
- pin plugin/papyrus/compiled/Skython.pex stable 36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341
  (informational only, never compared: the committed file's raw sha256 03738c8307c2681327e0f1d1f32b63d10b83dd4fe16bd141aa2b6a93343da3fe)
