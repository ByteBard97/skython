# Python-in-Skyrim scripting host: adopt, adapt or build? (Q1, as of 2026-09-30)

**Verdict:** Build it. No maintained, player-ready Python host for Skyrim SE/AE exists. The only Python attempt I found, wabbajack-tools/SkyrimPy, is a stalled prototype with four commits. The closest usable reference is SkyLink AI (jarvann/SkryimMCM, MIT). It is an SKSE plugin plus an external process, joined by a named pipe, with a Papyrus VM call bridge, a game-thread task queue and 17 event types.\[1\] That is almost exactly design option (B), only with C#/MCP where we want Python. Take its patterns, and possibly its code, since MIT allows it. SkyrimPlatform is the mature generic host, but it is JavaScript, GPL-3.0 and pinned to 1.6.x.\[2\]\[3\] Use it as a design reference only, and do not copy its code unless we accept GPL. Nothing here supersedes ChronicleBridge; the gap is the Python-facing layer, the stub generator and the event coverage.

## TL;DR
- Nothing to adopt. SkyrimPy is a 4-commit WIP; JContainers' Lua is an experimental in-Papyrus evaluator; SkyrimPlatform is JS-only and GPL-3.0.
- Adapt patterns from SkyLink AI (MIT): pipe IPC, PapyrusBridge, TaskQueue, EventSystem, game-safety checks. Write the Python host, the stub generator and the save/load semantics ourselves.
- Licence path: MIT/0BSD inputs keep us free to choose a licence. Any SkyrimPlatform code copied in makes us GPL-3.0.

## Sub-question 1: SkyrimPlatform / SkyMP
**Answer:**
- **Licence:** GPL-3.0.\[3\]\[4\]
- **Release:** the current Nexus version is 2.9.0, uploaded 14 May 2024 (U, from search-result text of the Nexus Files tab for mod 54909; the page I fetched did not render dates). It was listed as "Skyrim Platform 2.9.0 (Anniversary Edition) Date uploaded 14 May 2024, 12:32PM … THIS IS FOR ANNIVERSARY EDITION (1.6.640/1.6.1170)!", with a separate Special Edition (1.5.97) file uploaded two minutes later. Its release notes add 1.6.1170 support.\[5\]
- **Supported runtimes:** 1.6.1170, 1.6.640 and 1.5.97 (deprecated).\[2\] I found no mention of 1.7.x anywhere.
- **Type generation:** not settled. The codegen folder has a Definitions.txt that looks hand-written for SP-specific APIs. The docs describe a runtime "DumpFunctions.txt" of native functions.\[6\] My inference is that the types come from a runtime dump plus a hand-written header, not from .psc parsing. Unverified.
- **Threads:** Papyrus calls are only allowed inside hooks and SP events.\[7\]
- **Save/load:** self-described as weak.\[2\]
- **External-process interface:** none found. There is an outbound HTTP client API only.\[5\]

**Evidence:**
- https://github.com/skyrim-multiplayer/skymp/blob/main/skyrim-platform/README.md (V, read 2026-09-30):
  - "Steam latest (currently Skyrim SE/AE 1.6.1170)"\[2\]
  - "Inadequate savegame support: managing the game state can be tricky."\[2\]
  - "Updating/deleting SkyrimPlatform on a current save might break your save."\[2\]
  - "Right now [ChakraCore] powers our mod", with a plan to "Migrate to NodeJS"\[2\]
- https://github.com/skyrim-multiplayer/skymp/blob/main/docs/release/sp-2.9.md (V, undated): "Added support for Skyrim 1.6.1170.0"; "HTTP Client API now supports callbacks".\[5\]
- https://github.com/skyrim-multiplayer/skymp/blob/main/docs/skyrim_platform/papyrus.md (U): "Papyrus types and methods are only available inside hooks or new events… An exception will be thrown."\[7\]
- https://www.nexusmods.com/skyrimspecialedition/mods/54909 (U, via subagent from a meta tag): version 2.9.0. The Nexus page states the GPL-3.0 licence.\[4\]
- https://github.com/skyrim-multiplayer/skymp/releases (V): the only release listed is "Pre-release of Skyrim Platform 2.6", dated "06 Apr" with no year shown.\[8\]
- https://deepwiki.com/skyrim-multiplayer/skymp/3.1-skyrim-platform-integration (V, AI-generated summary): says an "embedded Node.js environment".\[9\] This conflicts with the README's ChakraCore statement; the conflict is unresolved.
- https://pospelovlm.gitbook.io/skyrim-multiplayer-docs/docs_skyrim_platform (U): "output information about game functions to… DumpFunctions.txt".\[6\]

**Confidence:** high on licence, runtimes and caveats; low on how the types are generated.

**What this changes for us:** copy the ideas: a promise/async model, papyrus calls restricted to a context, and a hand-written layer for events.\[2\] Do not copy the code (GPL), and do not depend on it (1.6-only, JavaScript).

## Sub-question 2: SkyLink AI (jarvann/SkryimMCM)
**Answer:**
- **Architecture:** Claude Desktop ↔ .NET 10 MCP server over stdio ↔ SKSE plugin over the named pipe `\\.\pipe\SkyrimMCP`.\[1\]\[10\]
- **Tools:** 74.\[10\]
- **Papyrus:** a "Function catalog (3,389 functions from 166 scripts), script function lookup, VM call bridge".\[11\] The Nexus page says it can "call any mod's Papyrus functions at runtime".\[1\]\[10\] I have not seen the call signature, so whether it allows truly arbitrary script+function calls is unconfirmed.
- **Licence:** MIT on GitHub.\[10\] The Nexus page's own permissions contradict this: "You must get permission from me before you are allowed to modify my files".\[1\]
- **Game versions:** README says "v1.6.1130+";\[10\] Nexus says "1.5.97 / 1.6.x"; nothing for 1.7.x.\[1\]
- **Activity:** 134 commits.\[10\] Nexus v1.0 was uploaded on 27 March 2026.\[1\] I could not get the last commit date.
- **Maintainer:** Jarvann Darr.\[1\]\[12\]
- **Source:** open.\[1\]

**Evidence:**
- https://github.com/jarvann/SkryimMCM (V): its file list includes "PapyrusBridge.* # Papyrus VM bridge", "EventSystem.*", "TaskQueue.* # Game thread dispatch", and states the licence as "MIT".\[10\]
- https://www.nexusmods.com/skyrimspecialedition/mods/175682 (V; it opened): "Papyrus VM bridge — call any mod's Papyrus functions at runtime"; "Event system (17 event types…)"; a safety checker that "detects risky states (loading, kill moves, saves)".\[1\]

**Confidence:** high on architecture and licence; medium on how generic the Papyrus call is.

**What this changes for us:** this is the closest existing proof of option (B). Read PapyrusBridge.cpp and TaskQueue before writing our own dispatcher. The pipe runs inside the Wine prefix, so our Python child must run under the same Proton prefix (inferred, R).

## Sub-question 3: Papyrus Stack Stalker NG
**Answer:** A logger and in-game monitor for developers. It is not external-facing. It shows live Papyrus stacks and has a profiler, a VM time graph, "Dump" of stack traces,\[13\] and "Force Return" for stacks stuck in WaitLatent.\[13\] It does not describe any network or IPC interface.
- **Author:** 33void.\[13\]
- **Version:** 1.6, last updated 08 June 2025.\[13\]
- **Permissions:** "Contact me regarding permissions".\[13\]
- **Source:** no source link seen.
- **Game versions:** not stated.

**Evidence:** https://www.nexusmods.com/skyrimspecialedition/mods/151940 (V): "a real-time window into the inner workings of Skyrim's Papyrus Virtual Machine".\[13\]

**Confidence:** high.

**What this changes for us:** use it as a dev companion to watch our dispatcher's Papyrus load. It is irrelevant as a host.

## Sub-question 4: JContainers, mrowrpurr and other interpreter hosts
- **JContainers (ryobg fork, MIT, V):**
  - It embeds Lua, which Papyrus calls through `JValue.evalLuaFlt/Int/Str/Form/Obj`.\[14\]
  - Its own docs say "Lua feature status is highly experimental" (https://github.com/SilverIce/JContainers/blob/develop/JC.md, U).\[14\]
  - Lua works on JC containers. I found no indication that Lua can call arbitrary Papyrus (inferred, R).
  - Last activity: the ryobg/JContainers GitHub Releases page (U, from search-result text; the fetched page rendered only navigation) lists "JContainers v4.3.2 Latest", released "29 Aug" with no year shown. Its changes read "SKSE 2.3.1 / SAE 1.7.104", with compatibility rows "AE/64 | 2.3.1 | 1.7.104" and "GOG | 2.2.6 | 1.6.1179". So a 1.7.x AE runtime exists and JContainers already supports it.
  - Source: https://github.com/ryobg/JContainers (V): "Embedded, lightweight scripting with Lua".\[15\]
  - The JContainersNG rewrite drops Lua: "JLua — intentionally excluded to avoid LuaJIT dependency" (https://github.com/outtamercy/JContainersNG, U).\[16\]
- **mrowrpurr / Skyrim Scripting:**
  - SkyScript is a YAML/JSON script interpreter described as a "Work in Progress… C++ learning project" (https://github.com/mrowrpurr/SkyScript, U).\[17\]
  - The SkyrimScripting organisation is templates and packages. Packages is 0BSD and was updated Mar 3, 2026 (https://github.com/SkyrimScripting, U).\[18\]
  - I found no Python or remote-call host there.
- **.NET Script Framework (meh321):**
  - A C# plugin host.\[19\]
  - Reported "not compatible with Anniversary Edition 1.6.x runtime" (https://stepmodifications.org/forum/topic/14805-net-script-framework-by-meh321/, U).\[20\]
  - Dead for our target.\[21\]
- **SkyrimNet:**
  - A native DLL with an MCP server "on port 8889 that exposes 44+ tools"\[22\] and a "public C++ DLL API" (https://github.com/MinLL/SkyrimNet-GamePlugin, U).\[22\]
  - Licence and source availability are unverified.
  - It is an AI mod, so it is out of scope as a host.

**Confidence:** medium.

**What this changes for us:** no embedded-interpreter precedent survives on AE. Lua-in-JContainers shows that shipping an interpreter DLL to players is accepted.

## Sub-question 5: Python / JSON-RPC / pybind11 bindings (Skyrim and other Bethesda games)
- **wabbajack-tools/SkyrimPy (V):**
  - Its self-description: "a SKSE plugin that wraps CommonLibSSE and presents it to Python".\[23\]
  - It is labelled "WIP testing", with 4 commits, 3 stars, no releases and an MIT licence.\[23\]
  - Last commit date not visible.
  - It follows the "wrap all of CommonLib" approach we already rejected.
- **peetronicus/f4se-console-bridge (U):**
  - A "local-file command bridge for Fallout 4. Send ordinary console commands from Python".\[24\]
  - It targets FO4 1.11.240 with F4SE 0.7.9.\[24\]
  - It is a pattern for file-based IPC that works without sockets, but it is limited to console commands.
- I found no pybind11, nanobind or JSON-RPC project for Skyrim, and no generic scripting host for Starfield.

**Confidence:** medium. A negative result is only as good as the search.

**What this changes for us:** a Python host is genuinely new work, and nobody has done the .psc→Python stub generator.

## Sub-question 6: What to extend, what to write, licence consequences
**Decision:** build, and treat SkyLink AI as the single project to mine or fork.
- **Reuse from SkyLink (MIT):**
  - the pipe server and command registry
  - the Papyrus VM bridge and its function catalog
  - the TaskQueue for game-thread dispatch
  - the event sinks
  - the game-safety checker
- **Write ourselves:**
  - the Python host: CPython child process (B) first, embedded (A) later
  - a futures/async API
  - form-handle references
  - the .psc→typed-stub generator for vanilla, SKSE and PO3
  - the events SkyLink lacks for our needs: cell attach, menu open, save/load
  - bulk reads
  - save/load lifecycle rules
  - a replacement for the .NET/MCP layer
  - Proton validation
- **Licences:**
  - MIT code needs its notice kept. The SkyLink Nexus page's "no modification without permission" wording contradicts its MIT repo, so ask the author before forking.\[1\]
  - Using SkyrimPlatform code forces GPL-3.0.\[3\]
  - CPython's PSF License v2 allows redistributing the embeddable zip (U, via enricher: docs.python.org/3/license.html, 3.14.7). §2 grants the right to "reproduce … prepare derivative works, distribute, and otherwise use Python alone or in any derivative version, provided, however, that PSF's License Agreement and PSF's notice of copyright … are retained". The page also says "All Python licenses, unlike the GPL, let you distribute a modified version without making your changes open source." Under §3, we would owe "a brief summary of the changes made to Python" only if we ship a modified build.

**Confidence:** medium.

**What this changes for us:** skip the market survey and start the prototype. The first job is porting SkyLink's bridge pattern onto ChronicleBridge.

## Open unknowns and the smallest test for each
1. **Is SkyLink's Papyrus call fully generic?** Read PapyrusBridge.cpp and USAGE.md in the repo.
2. **How is skyrimPlatform.ts generated?** Open the skymp `skyrim-platform/src/platform_se/codegen` tree through a search result.
3. **SkyrimPlatform 1.7.x status.** The 2.9.0 upload date is 14 May 2024 (U, from Nexus Files tab search-result text), and those files target only 1.6.640/1.6.1170 and 1.5.97. Log into Nexus mod 54909 and read the Files tab to confirm nothing newer exists.
4. **Does a Windows-side Python child reach SkyLink's named pipe under GE-Proton?** Launch embeddable Python inside the same prefix and open `\\.\pipe\SkyrimMCP`.
5. **SkyLink last-commit date and the year of JContainers v4.3.2.** Check the GitHub commits pages. JContainers v4.3.2 is dated "29 Aug" with no year (U).

## Dead ends
- The GitHub commits page for SkryimMCM and the skymp codegen tree were refused by the fetch tool (not permitted), so I did not read them.
- The GitHub release list for skymp showed no year on its one pre-release.
- Nexus pages opened this time, but "Last updated" for SkyrimPlatform did not render.
- I found no Starfield/SFSE scripting host.

## Sources

1. [SkyLink AI](https://www.nexusmods.com/skyrimspecialedition/mods/175682)
2. [skymp/skyrim-platform/README.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/skyrim-platform/README.md)
3. [GitHub - skyrim-platform/skyrim-platform: TypeScript type definitions for Skyrim Platform (released with permission) · GitHub](https://github.com/skyrim-platform/skyrim-platform)
4. [Skyrim Platform - A TypeScript SDK for Skyrim at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/54909)
5. [skymp/docs/release/sp-2.9.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/docs/release/sp-2.9.md)
6. [Skyrim Platform - Skyrim Multiplayer Docs - GitBook](https://pospelovlm.gitbook.io/skyrim-multiplayer-docs/docs_skyrim_platform)
7. [skymp/docs/skyrim\_platform/papyrus.md at main · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/blob/main/docs/skyrim_platform/papyrus.md)
8. [Releases · skyrim-multiplayer/skymp](https://github.com/skyrim-multiplayer/skymp/releases)
9. [SkyrimPlatform Foundation | skyrim-multiplayer/skymp | DeepWiki](https://deepwiki.com/skyrim-multiplayer/skymp/3.1-skyrim-platform-integration)
10. [GitHub - jarvann/SkryimMCM: Skyrim MCM for direct access to runtime engine via AI like Claude Desktop · GitHub](https://github.com/jarvann/SkryimMCM)
11. [GitHub - WraithFallen/SkryimMCM: Skyrim MCM for direct access to runtime engine via AI like Claude Desktop · GitHub](https://github.com/WraithFallen/SkryimMCM)
12. [jarvann (Jarvann Darr) · GitHub](https://github.com/jarvann)
13. [Papyrus Stack Stalker NG at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/151940)
14. [JContainers/JC.md at develop · SilverIce/JContainers](https://github.com/SilverIce/JContainers/blob/develop/JC.md)
15. [GitHub - ryobg/JContainers: JSON-based data structures for Papyrus - the TESV Skyrim SE scripting language](https://github.com/ryobg/JContainers)
16. [GitHub - outtamercy/JContainersNG: JContainersNG · GitHub](https://github.com/outtamercy/JContainersNG)
17. [GitHub - mrowrpurr/SkyScript: Nothing to see here ;) · GitHub](https://github.com/mrowrpurr/SkyScript)
18. [Skyrim Scripting · GitHub](https://github.com/SkyrimScripting)
19. [.NET Script Framework at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/21294)
20. [.NET Script Framework (by meh321) - Skyrim SE Mods - Step Mods](https://stepmodifications.org/forum/topic/14805-net-script-framework-by-meh321/)
21. [A Fix for All Plugins Disabling after 1.6.1130 - Page 2 - Skyrim SE - Nexus Mods Forums](https://forums.nexusmods.com/topic/13458736-a-fix-for-all-plugins-disabling-after-161130/page/2/)
22. [GitHub - MinLL/SkyrimNet-GamePlugin: Public facing files for SkyrimNet · GitHub](https://github.com/MinLL/SkyrimNet-GamePlugin)
23. [GitHub - wabbajack-tools/SkyrimPy: WIP testing for integrating Python 3 into Skyrim...write SKSE plugins in Python is the idea](https://github.com/wabbajack-tools/SkyrimPy)
24. [GitHub - peetronicus/f4se-console-bridge: Standalone local-file console command bridge for Fallout 4 and F4SE · GitHub](https://github.com/peetronicus/f4se-console-bridge)
