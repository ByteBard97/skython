# Python-in-Skyrim mod: research questions

Date: 2026-09-30. Scope: a **standalone Skyrim mod that embeds Python and lets Python scripts read and write game state through a native-feeling API**, like KiCad's scripting. Nothing else. skyrim-dm, quest delivery, TTS, lip sync, local language models and NPC writing are out of scope for this file.

Use: give an agent Part A, then one question from Part B ("Read Part A, then begin research on Question 2").

---

# Part A: Background and rules

## The idea

A Skyrim SE/AE mod, built as an SKSE C++ plugin, that hosts a Python interpreter so modders can write Python that calls into the game. The leading design idea:

- A C++ plugin (CommonLibSSE-NG) exposes one **generic dispatcher** that calls any Papyrus native function by script and function name through the Papyrus VM.
- **Typed Python stubs are generated from the Papyrus `.psc` headers** (vanilla, SKSE, PO3), as SkyrimPlatform does for TypeScript.
- A **small hand-written layer** covers what Papyrus cannot: event subscriptions (death, hit, equip, cell attach, menu open, save/load), bulk reads, and a few curated engine classes.
- Game objects are referenced by form handle, not raw pointer. All game access is queued onto the game thread, and Python gets futures or async.
- Two ways to host Python are being compared: **(A)** embed CPython inside the plugin DLL (official embeddable distribution, about 10 MB), or **(B)** ship the embeddable Python as a hidden child process the plugin launches, talking over a local socket (the KiCad IPC pattern).
- Wrapping all CommonLibSSE-NG C++ classes, or calling raw engine functions by address from the ~32,000 named functions in a Ghidra database, is considered too heavy and unsafe.

## Constraints

- **Skyrim only.** Target AE 1.6.1170 (SE 1.5.97 optional). No VR, no other games.
- Developed and played on **Linux under Proton** (GE-Proton, Mod Organizer 2); C++ is compiled on a Windows machine. Anything must also be checked for Proton/Wine.
- The long-term goal is that a **non-technical player installs one mod** and does not install Python separately.

## Already settled locally (do not re-research)

- Chronicle's ChronicleBridge plugin exists: CommonLibSSE-NG, an HTTP protocol to a Python service, a live-test harness over DevBench, verified in-game writes (relationship ranks, AI package flags, barter prices, a spawned item that survives a cell reload, a death-event sink). Nothing for quests, objectives, dialogue or inventory events.
- **DevBench** (alandtse): GPL-3.0 with a linking exception; REST and MCP on loopback only with no auth; a `papyrus` tool calling any function synchronously with typed values; intended as a dev bench, not something to ship to players.
- PO3 Papyrus Extender is MIT and widely used; `SetObjectiveText` is a raw field write.
- Game version pin: 1.6.1170 with SKSE 2.2.6 and Address Library 11 or later; class-layout changes force recompiles; 1.7.99 (2026-08-20) broke native plugins.
- skyrim-re-toolkit's verified class list (37 of 39 hotspot classes exact, 11 cross-checked live) can serve as an allowlist for any hand-written engine layer.
- Known Proton facts: GE-Proton works; missing vcrun2022 gives error `0000007E`; DevBench's `game action=load` silently fails under Proton (cause not found).

## Evidence rules (mandatory)

1. Every factual claim needs a URL and, when important, one short quoted sentence. No URL means "unverified" or leave it out.
2. Label every claim: **V** = you opened the page and read the relevant part; **U** = search result or snippet only; **R** = recall, no source. Never upgrade U or R to V. Do not invent URLs, versions, dates, function names or quotes. A missing answer is acceptable; a fabricated one is not.
3. For each project, mod or tool record: what it does, licence, **last activity (absolute date)**, maintainer, supported game versions, open or closed source.
4. Separate what authors say, what users report, and what you infer. Prefer primary sources (repositories, changelogs, issue trackers, official policy pages).
5. Report dead ends. **Nexus Mods pages often return HTTP 403 to automated fetches**; if you cannot open a page, say so and never report its contents as verified.

## Output (every question)

A one-paragraph verdict, then for each sub-question the answer, the evidence (URL, label, date, short quote), a confidence (high, medium, low), and a one-line "what this changes for us". End with open unknowns (and the smallest test that would settle each) and dead ends. Under about 1,500 words.

---

# Part B: Questions

## Question 1. Does a Python (or generic) scripting host for Skyrim already exist?

Decide adopt, adapt or build.

1. **SkyrimPlatform / SkyMP:** current licence, latest release date, supported runtimes (1.6.1170, 1.7.x); are its TypeScript definitions generated from `.psc` files or hand-written; how it handles threads, lifetimes and save/load; does it expose an interface an external process could use?
2. **SkyLink AI** (repository `jarvann/SkryimMCM` or similar): licence, last commit, supported versions, tool list, does it expose a generic Papyrus call?
3. **Papyrus Stack Stalker:** what it actually is, and whether it is external-facing or only a logger.
4. **JContainers** (its Lua runtime and Lua-Papyrus integration), **Skyrim Scripting / mrowrpurr** projects, and any other plugin that embeds an interpreter (Lua, JavaScript, Python, .NET) or exposes a remote-call interface.
5. Any existing **Python** binding, JSON-RPC bridge or pybind11/nanobind project for Skyrim or another Bethesda game (Fallout 4, Starfield tooling is useful as a pattern).
6. If one exists: which single project would you extend, what would we still write ourselves, and what licence consequences follow?

## Question 2. Technical feasibility: embedding Python and generating bindings

1. **Precedents for an embedded interpreter inside an SKSE plugin** (JContainers Lua, SkyrimPlatform's JavaScript engine, any CPython example): how they host it, where they initialise it (not inside `DllMain`; after which SKSE message), and what problems they reported (crashes, loader lock, threading, leaks).
2. **Two embedded runtimes in one process:** what happens if another mod also loads a Python DLL? Is there a safe way to avoid conflicts (isolated config, renamed DLL, separate process)?
3. **Hosting choice:** reports of in-process versus out-of-process interpreters in game mods. How do mods handle a scripting crash, hot reload and unload?
4. **`.psc` parsing and stub generation:** is there a maintained parser or generator we can reuse (Caprica, Champollion, pyro, SkyrimPlatform's own definition generator, others)? How do SKSE and PO3 distribute their `.psc` headers, and under what licences?
5. **Calling Papyrus from C++ by name:** the documented pattern for dispatching a global or member function through the VM, how results and latent calls are handled, and known pitfalls (stack dumps, latency, VM overload). (A local CommonLibSSE-NG checkout also exists; see Part C.)
6. **Game-thread safety:** the accepted pattern for running work on the game thread from another thread (the SKSE task interface), plus known hazards such as dangling references after cell change or load. How do comparable projects represent a reference (form ID, handle)?
7. **Event sinks** in CommonLibSSE-NG that would matter for a scripting host (death, hit, equip, container changed, cell attach, menu open, save and load, quest stage) and any caveat reported with each.

## Question 3. Distribution and install

1. What do **Nexus Mods' current rules** say about mods that bundle executables, DLLs or an embedded interpreter, or that launch a helper process? Quote the text.
2. Do **frozen Python executables (PyInstaller, Nuitka) or the official embeddable Python zip** trigger antivirus false positives more, according to mod authors or Python-packaging reports?
3. Is there a **documented working setup under Proton or Wine** for a mod that hosts an interpreter or launches a helper process? Known breakages?
4. How do Mod Organizer 2 and Vortex treat helper processes and files written at runtime (caches, logs, user scripts)? How do mods tell users where their scripts live?
5. Does Nexus require disclosure or have rules about user-supplied scripts or code execution inside a mod (a scripting host is arbitrary code execution)? Include any security guidance mod authors follow.

## Question 4. Versions and ecosystem

1. What changed in **Steam builds 1.7.99, 1.7.104** and later? Did class layouts change? Does current **CommonLibSSE-NG** support them?
2. Which **SKSE** version matches 1.7.x, and did PO3 Papyrus Extender, PapyrusUtil and SSE Engine Fixes publish 1.7.x builds (with dates)?
3. What is the **share of players** on 1.5.97, 1.6.1170, 1.6.1179 (GOG) and 1.7.x?
4. Would a plugin that **only uses the Papyrus VM and event sinks** survive patches better than one that hooks engine internals? What do plugin authors report?
5. How do plugin authors ship across runtimes: one CommonLibSSE-NG DLL, or separate builds?

---

# Part C: Do locally or in a live game

**Local:**
- Read the CommonLibSSE-NG checkout at `skyrim-re-toolkit/type-importer/vendor/CommonLibSSE-NG` for the VM dispatch functions and event sinks.
- Decide where the Python mod's first build lives: Chronicle's ChronicleBridge plugin, the toolkit's RuntimeHarness plugin, or a new plugin (they are three separate SKSE plugins today).
- Resolve the contradiction about where Skyrim runs: one doc says Linux, another records an owner ruling (2026-08-28) that Skyrim must not run on the Linux box.

**Live-game tests:**
1. A minimal plugin starts an embedded CPython after `kDataLoaded`, runs a script that calls one Papyrus function through the generic dispatcher, and returns the result.
2. The same, from a worker thread with the game-thread queue; check for stalls.
3. Load and save a game with the interpreter running; check for crashes.
4. Start the same plugin under Proton.
5. Run the out-of-process variant (hidden child process plus socket) under Proton and compare.
6. A death or hit event reaches Python.

**Suggested priority:** Question 1 first (it can change the plan most), then Question 2. Question 3 before committing to packaging. Question 4 before choosing the game-version pin.
