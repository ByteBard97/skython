# Q2 Technical feasibility: embedding Python and generating bindings in an SKSE plugin

**Verdict.** The design is feasible, but no public example of CPython running inside an SKSE plugin has shipped. The one attempt found, wabbajack-tools/SkyrimPy, is an unfinished experiment. The closest working model is SkyrimPlatform: it runs an in-process script engine, supports AE 1.6.1170, generates typed stubs, and supports hot reload. Its own README also lists save breakage and conflicts with other mods as costs.\[1\] Calling Papyrus by name through `IVirtualMachine::Dispatch*Call` is documented and simple. Queueing onto the game thread through the SKSE task interface is the standard pattern.\[2\]\[3\] The biggest unmitigated risk is another plugin in the same process loading its own `python3.dll`.\[4\] My recommendation: build the out-of-process host (B) first, since it matches your working ChronicleBridge HTTP design, and treat in-process embedding (A) as a later optimisation that needs a DLL-isolation spike.

Labels: **V** = I opened the page and read the relevant part. **U** = search snippet only. **R** = from memory, no source. Dates are shown where the page gave one; most GitHub pages I opened showed no commit date.

## Key Findings

### 2.1 Precedents for embedded interpreters
**Answer:**
- **SkyrimPlatform:** open source, in process, ChakraCore engine, supports 1.6.1170 and 1.6.640.\[1\]
- **JContainers:** embeds LuaJIT.
- **SkyrimPy:** the only CPython attempt, work in progress with 4 commits.

I found no primary source that says which SKSE message each project initialises on. The general guidance is: use forms only after `kDataLoaded`, and start threads and talk to other plugins only after `kPostLoad`.\[5\]

**Evidence:**
- SkyrimPy — https://github.com/wabbajack-tools/SkyrimPy — V, no date shown. "This is a SKSE plugin that wraps CommonLibSSE and presents it to Python." MIT, 4 commits, 3 stars, no releases.\[6\] It takes the "wrap all of CommonLib" approach you ruled out.
- SkyrimPlatform README — https://github.com/skyrim-multiplayer/skymp/blob/main/skyrim-platform/README.md — V. It says:
  - Supported versions: "Steam latest (currently Skyrim SE/AE 1.6.1170)".\[1\]
  - Engine: "Right now ChakraCore powers our mod."\[1\]
  - Drawbacks: "SkyrimPlatform conflicts with many mods" and "Updating/deleting SkyrimPlatform on a current save might break your save."\[1\]
  - Without SSE Engine Fixes, "Chromium process will hang in your task manager after game exit."\[1\]
  - Licence is GPL-3.0 according to the Nexus page (U, https://www.nexusmods.com/skyrimspecialedition/mods/54909); I could not open that page.\[7\]
- JContainers:
  - https://github.com/ryobg/JContainers — U. It lists "Embedded, lightweight scripting with Lua".\[8\]
  - Releases page — https://github.com/ryobg/jcontainers/releases — U. It lists builds for SKSE 2.3.1 / 1.7.104 and 1.6.1170, and the release note "Improved LuaJIT memory support" (details not read).\[9\]
  - JContainersNG — https://github.com/outtamercy/JContainersNG — U. It is a clean-room rewrite: "JLua — intentionally excluded to avoid LuaJIT dependency".\[10\]
- Init timing — https://github.com/ArcEarth/DynamicAnimationCasting — U: "safe to begin using multithreaded operations… after kPostLoad… safe to start interacting with forms after kDataLoaded."\[5\] Microsoft's "Dynamic-Link Library Best Practices" (V, https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-best-practices) explains why this work stays out of DllMain: "DllMain is called while the loader-lock is held… You should never perform the following tasks from within DllMain: Call LoadLibrary or LoadLibraryEx (either directly or indirectly). This can cause a deadlock or a crash." The same page also forbids calling CreateThread there, so Py_Initialize and thread startup belong in kPostLoad.

**Confidence:** medium.
**What this changes for us:** we would be first with in-process CPython, so budget for a spike rather than assuming a known-good recipe.

### 2.2 Two Python runtimes in one process
**Answer:** A second embedded Python in the same process is a real, known way to fail. Isolated config does not fix DLL-name collisions; it only protects against environment variables and user site-packages. A separate process avoids the problem completely.

**Evidence:**
- Tony Roberts on python-dev, 24 Feb 2022 — https://groups.google.com/g/dev-python/c/53xUHM__Hi0 — V. "If a process embeds two different versions of Python 3… The python3.dll that it gets linked to will be the first one that was loaded… 'bad things' happen." His workaround patches the undocumented Windows loader table. For the same Python version, the problem gets worse because the `.pyd` files have the same names.\[4\]
- CPython docs — https://docs.python.org/3/c-api/init_config.html — U. Isolated config "ignores global configuration variables, environment variables… and user site directory… Ensure PyConfig.home is specified."\[11\]
- Author/user report, ScriptHookVPy (GTA V) — https://cs.gta5-mods.com/tools/scripthookvpy-python-scripting-for-gta-v — U: "the standard python311.dll has dependencies that conflict with GTA V." Users still reported crashes after it switched to the embeddable build.\[12\]
- CPython issue 24679 — https://bugs.python.org/issue24679 — U. Embedders must supply the C runtime themselves.\[13\] This matches your vcrun2022/0000007E finding under Proton.

I did not find any Fallout 4 or Cyberpunk reports.

**Confidence:** medium.
**What this changes for us:** under (A), link to the version-specific `python3XX.dll` rather than the stable-ABI `python3.dll`, keep the interpreter in a private folder we load by full path, and set `PyConfig.home`. That is my inference and still needs a test. Under (B), the whole problem goes away.

### 2.3 In-process vs out-of-process; crash, hot reload, unload
**Answer:** SkyrimPlatform is in process and hot-reloads scripts; it does not unload the engine. I found no Skyrim mod that runs its scripting language out of process. Your ChronicleBridge is effectively that pattern. CPython cannot be cleanly re-initialised inside one process. The Python/C API docs, "Initialization, Finalization, and Threads" (U, https://docs.python.org/3.11/c-api/init.html), warn: "Some memory allocated by extension modules may not be freed. Some extensions may not work properly if their initialization routine is called more than once; this can happen if an application calls Py_Initialize() and Py_FinalizeEx() more than once." So, in process, "reload" has to mean reloading modules, never restarting the interpreter.

**Evidence:**
- SkyrimPlatform README (V): "Hotreload. SP watches plugins by default."\[1\]
- CPython issue 17408 — https://bugs.python.org/issue17408 — U: "second python execution fails when embedding."\[14\]

**Confidence:** medium-low.
**What this changes for us:** a Python crash under (B) kills only the child process, which the plugin can restart. Under (A) it takes down Skyrim.

### 2.4 .psc parsing and stub generation
**Answer:** None of the projects checked is a maintained, reusable `.psc`-to-stub generator you could lift directly:

| Project | What it is | Licence | Source | Evidence |
|---|---|---|---|---|
| Caprica (Orvid) | Papyrus compiler (MSVC-only) with a full parser | MIT | open | https://github.com/Orvid/Caprica — U\[15\] |
| OpenPapyrus (fireundubh) | ANTLR4 grammars for Papyrus | not checked | open | https://github.com/fireundubh — U\[16\] |
| Pyro (fireundubh) | build tool; calls an external compiler, has no parser | conflicting and unresolved: the GitHub sidebar says "MPL-2.0 license", while release notes say "The Pyro source code is licensed under the MIT License" (bsarch v0.7 is MPL 2.0), and joelday's papyrus-lang licence bundle lists Pyro as "The MIT License (MIT) Copyright (c) 2019 fireundubh" | open | https://github.com/fireundubh/pyro, /releases — U |
| Papyrus-2-Typescript | converts .psc to .ts for SkyrimPlatform | unknown | unknown | Nexus 56916 — U (page not opened)\[17\] |
| @skyrim-platform/po3-papyrus-extender | TypeScript stubs for PO3 | MIT | open | https://github.com/skyrim-platform/po3-papyrus-extender — U: "originally generated using Papyrus-2-Typescript"\[18\]\[19\] |

SkyrimPlatform ships a generated `skyrimPlatform.ts`. Its docs say SP "follows Semantic Versioning for JavaScript, but not for TypeScript" (V, https://github.com/skyrim-multiplayer/skymp/blob/main/docs/docs_skyrim_platform.md). \[20\] Its input is a `Definitions.txt` / `FunctionsDump.txt` dump, not .psc files. That comes from deepwiki and GitBook secondary sources (U).\[21\]

On headers: PO3 ships `.psc` files in its repo under `papyrus src/` (U).\[22\] The PO3 licence shows up as both GPL-3.0 and MIT across snippets, against your local "MIT"; check the current LICENSE file.\[23\]\[24\] SKSE ships its `.psc` files in `Data\Scripts\Source`, which is the wrong folder for SE's default `Source\Scripts` layout (U, https://forums.nexusmods.com/topic/13526655-having-issues-using-skse-functions-and-events/). \[25\] I could not verify SKSE's licence or redistribution terms (dead end).

**Confidence:** medium.
**What this changes for us:** write our own small parser, since headers only need `Scriptname`/`extends`/`Function … native [global]`. Use Caprica or OpenPapyrus as a grammar reference. Generate the stubs on the build machine; do not redistribute the SKSE sources.

### 2.5 Calling Papyrus by name
**Answer:** The documented pattern is as follows:
1. Get the VM singleton.
2. Get a handle through `GetObjectHandlePolicy()->GetHandleForObject(type, form)`.
3. Call `FindBoundObject(handle, scriptName, object)`.
4. Call `DispatchMethodCall1(object, fn, MakeFunctionArguments(...), callback)`.\[26\]

For global functions, use `DispatchStaticCall(className, fnName, args, callback)`.\[27\]

**Evidence:**
- https://ck.uesp.net/wiki/SKSE_Plugin_Development/Calling_Papyrus_Functions — V; it cites Reddit as its source.\[26\]
- The signatures are in https://ng.commonlib.dev/classRE_1_1BSScript_1_1IVirtualMachine.html — U.\[27\]

The result comes back asynchronously through `IStackCallbackFunctor` (U, the class exists).\[28\] Latent functions such as `Utility.Wait` complete later. SkyrimPlatform exposes them as Promises (V: "Promises and async/await support… for async Papyrus functions like Utility.wait").\[1\] I found no primary source on stack dumps or VM overload.

**Confidence:** high for the API, low for the pitfalls.
**What this changes for us:** map every call to a Python future that resolves in the callback functor. Latent calls then need no special handling.

### 2.6 Game-thread safety and reference representation
**Answer:** Use `SKSE::GetTaskInterface()->AddTask` (game thread) or `AddUITask` (UI thread) (U: https://ng.commonlib.dev/functions_a.html; Ryan's gist https://gist.github.com/Ryan-rsm-McKenzie/436ef9d4d053b0d5cf9cbd87268e8978, U: "This task is running on the main thread!").\[2\]\[3\]\[29\] SkyrimPlatform identifies objects by form ID and only allows game calls "inside the update event handler" (V, via the GitBook mirror https://pospelovlm.gitbook.io/skyrim-multiplayer-docs/docs_skyrim_platform). Its guarantee is weak: `getFormEx` finds the form "if the form was not destroyed by the game."\[30\]

Raw pointers held across a queue round trip break in practice (V, https://github.com/SteveTownsend/SmartHarvestSE/issues/614): "keeps raw TESObjectREFR* … across the EnqueueStealIfUndetected round trip, and if that task is skipped on a CELL change…".\[31\] Its Nexus changelog says it moved to "Ref Handles instead of raw TESObjectREFR pointers for async op safety" (U).\[32\] A Fallout 4 analogue, Addictol issue #61, points the same way (U).\[33\]

**Confidence:** medium-high.
**What this changes for us:** pass a form ID across the Python boundary, and turn it into an `ObjectRefHandle` or a fresh lookup inside each task. Treat a failed lookup as "gone", never as an error that crashes the game.

### 2.7 Event sinks
**Answer:** All the events you need are exposed as `BSTEventSink`s on `ScriptEventSourceHolder`/`SkyrimVM`:
- `TESDeathEvent`, `TESHitEvent`, `TESEquipEvent`, `TESContainerChangedEvent`, `TESCellAttachDetachEvent`\[34\]
- `TESQuestStageEvent`, `TESQuestInitEvent`, `TESLoadGameEvent`\[34\]\[35\]

Evidence is U: https://ng.commonlib.dev/classRE_1_1ScriptEventSourceHolder.html and https://ng.commonlib.dev/_skyrim_v_m_8h_source.html. \[34\]\[35\] Menu opening comes from `MenuOpenCloseEvent`, which is registered on `RE::UI`, not on `ScriptEventSourceHolder`. The CommonLibSSE-NG UI.h source (U, https://ng.commonlib.dev/_u_i_8h_source.html) declares "template <class T> void AddEventSink(BSTEventSink<T>* a_sink)" on RE::UI, and mrowrpurr's "Various Skyrim Events.cpp" gist (U) shows the call "RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(eventSink);". Saving and loading come from SKSE messages (U, SkyrimScripting/SKSE_Template_SKSE_Events).\[36\]

Caveats found:
- `TESDeathEvent` has `dead`, `actorDying` and `actorKiller` fields (U),\[37\] and SkyrimPlatform splits death into `deathStart`/`deathEnd` (V, partly read).\[38\]\[39\]\[40\] That supports, but does not prove, the "fires twice" claim.
- SkyrimPlatform skips events nobody subscribes to, "to provide more execution performance" (U, sp-2.6.md).\[41\]
- SkyrimPlatform's `cellFullyLoaded` "may fire multiple times" (V).\[39\]

I found no primary source on how often `TESHitEvent` fires or on quirks of the quest-stage, container or equip events.

**Confidence:** medium for availability, low for caveats.
**What this changes for us:** register sinks lazily, only when Python subscribes, and filter on the C++ side before crossing into Python.

## Open unknowns and smallest tests
1. **Two Pythons in one process.** Load a stub SKSE DLL that calls `LoadLibrary` on a different `python3XX.dll`, then run `import _ctypes` in both interpreters under GE-Proton.
2. **Init point.** Initialise the interpreter from `kPostLoad` on a worker thread and log the thread ID of the first `AddTask` callback.
3. **Death event count.** Add a sink that logs `dead` per event, then kill one NPC.
4. **Hit volume.** Count `TESHitEvent`s per second in a 5-NPC fight.
5. **Licences.** Read the current LICENSE files for PO3, SKSE and Pyro.

## Dead ends
- Nexus pages (SkyrimPlatform, JContainers, Papyrus-2-Typescript, Smart Harvest) were not opened; everything from them is U.
- The TESDeathEvent header source page and the SkyrimPlatform EventHandler source could not be reached.
- The search budget ran out before I could look into SKSE licensing and `ObjectRefHandle` documentation.
- I found no Fallout 4 or Cyberpunk Python-embedding reports.

## Sources

1. [skymp/skyrim-platform/README.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/skyrim-platform/README.md)
2. [Native SKSE64 Task Interface Implementation · GitHub](https://gist.github.com/Ryan-rsm-McKenzie/436ef9d4d053b0d5cf9cbd87268e8978)
3. [SKSE API](https://deepwiki.com/powerof3/CommonLibSSE/4.1-skse-api)
4. [\[Python-Dev\] Embedding multiple Python runtimes in the same process on Windows](https://groups.google.com/g/dev-python/c/53xUHM__Hi0)
5. [GitHub - ArcEarth/DynamicAnimationCasting: Skyrim SE SKSE plugin that serves as a spell casting framework to cast spells on Animation Events · GitHub](https://github.com/ArcEarth/DynamicAnimationCasting)
6. [GitHub - wabbajack-tools/SkyrimPy: WIP testing for integrating Python 3 into Skyrim...write SKSE plugins in Python is the idea](https://github.com/wabbajack-tools/SkyrimPy)
7. [Skyrim Platform - A TypeScript SDK for Skyrim at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/54909)
8. [GitHub - ryobg/JContainers: JSON-based data structures for Papyrus - the TESV Skyrim SE scripting language · GitHub](https://github.com/ryobg/JContainers)
9. [Releases · ryobg/JContainers](https://github.com/ryobg/jcontainers/releases)
10. [GitHub - outtamercy/JContainersNG: JContainersNG · GitHub](https://github.com/outtamercy/JContainersNG)
11. [Python Initialization Configuration — Python 3.14.7 documentation](https://docs.python.org/3/c-api/init_config.html)
12. [ScriptHookVPy - Python Scripting for GTA V 1.01](https://cs.gta5-mods.com/tools/scripthookvpy-python-scripting-for-gta-v)
13. [Issue 24679: Windows embeddable Python zip file crashes (cannot find a dll) - Python tracker](https://bugs.python.org/issue24679)
14. [Issue 17408: second python execution fails when embedding - Python tracker](https://bugs.python.org/issue17408)
15. [GitHub - Orvid/Caprica: A compiler for the Papyrus scripting language used by the Creation Engine. · GitHub](https://github.com/Orvid/Caprica)
16. [fireundubh (fireundubh) · GitHub](https://github.com/fireundubh)
17. [Papyrus-2-Typescript at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/56916?tab=posts)
18. [GitHub - skyrim-platform/po3-papyrus-extender: (Unofficial) TypeScript library for the powerofthree Papyrus Extender Skyrim modding utility · GitHub](https://github.com/skyrim-platform/po3-papyrus-extender)
19. [keywords:skyrim - npm search](https://www.npmjs.com/search?q=keywords%3Askyrim)
20. [skymp/docs/docs\_skyrim\_platform.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/docs/docs_skyrim_platform.md)
21. [SkyrimPlatform Foundation](https://deepwiki.com/skyrim-multiplayer/skymp/3.1-skyrim-platform-integration)
22. [PapyrusExtenderSSE/papyrus src/PO3\_SKSEFunctions.psc at 0b897777c9b1dc77ca67f4a71ca2e683eacb8adb · powerof3/PapyrusExtenderSSE](https://github.com/powerof3/PapyrusExtenderSSE/blob/0b897777c9b1dc77ca67f4a71ca2e683eacb8adb/papyrus%20src/PO3_SKSEFunctions.psc)
23. [GitHub - powerof3/PapyrusExtenderSSE · GitHub](https://github.com/powerof3/PapyrusExtenderSSE)
24. [GitHub - powerof3/PapyrusExtenderSSE](https://writemythesisv.co/host-https-github.com/powerof3/PapyrusExtenderSSE)
25. [Having issues using SKSE functions and events - Creation Kit and Modders - Nexus Mods Forums](https://forums.nexusmods.com/topic/13526655-having-issues-using-skse-functions-and-events/)
26. [SKSE Plugin Development/Calling Papyrus Functions - the CreationKit Wiki](https://ck.uesp.net/wiki/SKSE_Plugin_Development/Calling_Papyrus_Functions)
27. [CommonLibSSE NG: RE::BSScript::IVirtualMachine Class Reference](https://ng.commonlib.dev/classRE_1_1BSScript_1_1IVirtualMachine.html)
28. [CommonLibSSE NG: Class Index](https://ng.commonlib.dev/classes.html)
29. [CommonLibSSE NG: Class Members](https://ng.commonlib.dev/functions_a.html)
30. <https://pospelovlm.gitbook.io/skyrim-multiplayer-docs/docs_skyrim_platform>
31. [Auto-Theft logic uses raw REFR pointers](https://github.com/SteveTownsend/SmartHarvestSE/issues/614)
32. [Smart Harvest NG AutoLoot at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/37091)
33. [\[Vanilla Bug Fix Request\] ShaderReferenceEffect Crash · Issue #61 · Dear-Modding-FO4/Addictol](https://github.com/Dear-Modding-FO4/Addictol/issues/61)
34. [CommonLibSSE NG: CommonLibSSE-NG/include/RE/S/SkyrimVM.h Source File](https://ng.commonlib.dev/_skyrim_v_m_8h_source.html)
35. [CommonLibSSE NG: RE::ScriptEventSourceHolder Class Reference](https://ng.commonlib.dev/classRE_1_1ScriptEventSourceHolder.html)
36. [GitHub - SkyrimScripting/SKSE\_Template\_SKSE\_Events: SKSE plugin detecting system events · GitHub](https://github.com/SkyrimScripting/SKSE_Template_SKSE_Events)
37. [CommonLibSSE (powerof3): Class Members - Variables](https://ryan.commonlib.dev/functions_vars_a.html)
38. [CommonLibSSE NG: Class Members - Variables](https://ng.commonlib.dev/functions_vars_a.html)
39. [skymp/docs/skyrim\_platform/new\_events.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/docs/skyrim_platform/new_events.md)
40. [CommonLibSSE NG: Class Members](https://ng.commonlib.dev/functions_d.html)
41. [skymp/docs/release/sp-2.6.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/docs/release/sp-2.6.md)
