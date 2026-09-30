# Python-in-Skyrim: Research Questions 3 & 4

*Researched 2026-10-01. Labels: **V** = page opened and read; **U** = search snippet only; **R** = recall. Per evidence rule 5, Nexus Mods pages could not all be opened directly — Nexus content below is labelled accordingly and mostly comes from indexed page text, not a verified fetch.*

---

## Question 3. Distribution and install

**Verdict:** Nothing in Nexus Mods' current rules prohibits a mod that bundles DLLs or even executables, and nothing explicitly addresses a mod that launches a helper process — the only hard line is internet communication, which a loopback-only helper arguably avoids but should be pre-cleared with staff. The bigger practical risks are elsewhere: PyInstaller-style frozen executables are notorious antivirus false-positive magnets (the official embeddable zip is the community-recommended way to dodge this), MO2/Vortex handle runtime-written files gracefully (MO2's Overwrite folder), and — critically — **no public documented precedent exists for an interpreter-hosting or helper-process mod running under Proton/Wine**, so our own testing is the only evidence we'll have.

### 3.1 Nexus rules on bundled executables/DLLs and helper processes

The File Submission Guidelines (last revised ~Sep 2026) contain no clause about DLLs, embedded interpreters, or child processes. The two relevant rules are:

- *"Files that are intended to destroy or otherwise cause harm to a user's person or property, such as viruses, malware, adware, etc., are prohibited. Submitted files will be subject to virus scanning and may be moderated as a result."*
- *"Files (especially executables) that connect to the internet to download or send information and/or files are prohibited unless where it is crucial for the functioning of the mod/utility. Please note that 'auto update' functionality does not qualify as crucial… If your tool's/app's functionality depends on the ability to send and receive information/files, please contact staff or send an email to support@nexusmods.com laying out your reasoning and providing the source code."*

Evidence: https://help.nexusmods.com/article/28-file-submission-guidelines — **U** (indexed text, 2026-09-04), confidence **high**. Community pressure also exists for stricter DLL auditability (a June 2026 Nexus forum thread demands decompilable DLLs), suggesting the policy climate is tightening, not loosening — https://forums.nexusmods.com/topic/13539490 — **U**, confidence medium. Separately, Vortex reportedly auto-runs `.exe` files in "Nexus hand-picked" mods on install — confirmation that executables ship routinely (https://github.com/Nexus-Mods/Vortex/issues/21100 — **U**, 2026-02, confidence medium).

**What this changes:** The in-DLL embeddable CPython option (A) is clearly within the rules. The helper-process option (B) is not prohibited but is unusual enough that we should email support@nexusmods.com before release; keep the socket strictly loopback and document that in the mod description.

### 3.2 Antivirus false positives: frozen exes vs. embeddable zip

PyInstaller is a chronic false-positive source: GitHub issue #6754 shows a onefile exe flagged by 4+ engines even after rebuilding the bootloader (https://github.com/pyinstaller/pyinstaller/issues/6754 — **U**, 2022); a Stack Overflow thread records **11/61** VirusTotal detections and notes "the method used by PyInstaller to bundle up the files into one archive is what makes it seem suspicious" (https://stackoverflow.com/questions/43777106 — **U**, confidence high). Nuitka fares better (real machine code, no self-extraction) but is not clean — one report still shows 9/70 hits, and UPX re-triggers heuristics (https://dev.to/weisshufer/…19jf — **U**, 2025-12; https://stackoverflow.com/questions/43777106 — **U**, confidence medium). The most relevant answer: a Stack Overflow thread explicitly recommends *"use the embeddable package as downloaded from python.org"* as the false-positive-free distribution (https://stackoverflow.com/questions/69899867 — **U**, 2021-12, confidence medium-high). Direct evidence for the embeddable zip's clean record is thin, but it is signed by the Python Software Foundation and has no packer bootloader — the two mechanisms AVs flag.

**What this changes:** Confirms option A's packaging: ship the **official embeddable zip unmodified**; do not PyInstaller anything. If option B needs an exe stub, write a tiny C++ launcher rather than a frozen Python one.

### 3.3 Documented Proton/Wine precedent for interpreter/helper-process mods

**Dead end.** No public documentation found of a Skyrim mod hosting an interpreter or spawning a helper process being validated under Proton. What exists: Linux modding guides confirm *"all SKSE plugin mods (.dll) work as they should"* under Proton generally (https://github.com/spooknik/SkyrimSE-Linux-Modding — **U**, last touched 2019, confidence medium-low due to age), and detailed MO2-on-Linux guides exist (https://www.afkmods.com/index.php?/files/file/2624-… — **U**, 2024-01, confidence medium). Nothing about `CreateProcess` from inside an SKSE plugin under Wine. Our local ChronicleBridge evidence (GE-Proton works; DevBench's game-action load silently fails) remains the best data we have.

**What this changes:** Proton validation is a first-party research task (Part C live tests 4–5), not something to look up. Budget time for it.

### 3.4 MO2 and Vortex vs. runtime-written files and helper processes

MO2's VFS means *"all mod files remain in the staging folder at all times"* and files created at runtime land in the **Overwrite** folder: *"The Overwrite folder contains files generated at runtime or created by tools if they are not explicitly assigned to a mod. This commonly includes logs, configuration files, and tool output."* Recommended handling is moving them into a named mod; settings `ow_ignore_log` can suppress noise (https://modding.wiki/en/skyrim/users/infernalryan/mod-manager-fundamentals-mo2 — **U**, 2026-03, confidence high). This is long-standing community knowledge (SKSE logs in Overwrite, 2015: https://forums.nexusmods.com/topic/4915395 — **U**). Vortex hardlinks into the game dir instead, so runtime files physically persist there. Caveat: MO2's VFS only virtualises processes launched *through* MO2 — a child process spawned by the game itself is inside the VFS, but anything the helper writes outside the game dir is invisible to MO2. User scripts living under `Data\` (e.g. `Data\PyScripts`) would appear in Overwrite on first creation unless the mod ships the folder pre-made.

**What this changes:** Ship an empty `Data\SKSE\Plugins\<mod>\scripts\` (or similar) in the archive so user scripts land inside the managed mod, not Overwrite. Write logs to `Documents\My Games\Skyrim Special Edition\SKSE\` per SKSE convention.

### 3.5 Nexus rules on user-supplied scripts / arbitrary code execution

No rule specifically addresses scripting hosts or user-supplied code. The adjacent rules: the internet-communication clause above, and *"Files must be presented in good faith and in a functional state with respect to any caveats or requirements that you must detail and present to the user"* (same guidelines page — **U**, confidence high). Nexus scans uploads for viruses but user scripts are downloaded later, outside the scan. Community security guidance (the obfuscated-DLL thread) points toward auditability as the emerging norm: publish source, don't obfuscate.

**What this changes:** Mitigate by transparency — open source, plain-text scripts, no obfuscation, a description-page security note, and a default-off setting for loading third-party script packs.

---

## Question 4. Versions and ecosystem

**Verdict:** The 1.7.99/1.7.104 cycle (Aug 2026) confirmed every fear and every hope in our plan: class layouts *did* change (renderer state, PlayerCharacter offsets), old CommonLibSSE-NG DLLs all broke, yet CommonLibSSE-NG itself had 1.7.99 modelled within **two days**, Address Library within hours, and the ecosystem (PO3 Extender, SKSE 2.3.1, Engine Fixes) was largely compatible within about a month. A VM-and-events-only plugin is indeed the patch-survivable design — the community's own diagnosis of the breakage was "mods with a dll… need updating" while pure-Papyrus mods sailed through. Pin to 1.6.1170 as planned, but build multi-runtime so 1.7.x support is a recompile, not a rewrite.

### 4.1 What changed in 1.7.99 / 1.7.104; CommonLibSSE-NG support

1.7.99 released **2026-08-20** (console mod-storage increase, Creations changes; official changelog via UESP: https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch — **U**, confidence high). 1.7.104 followed **2026-08-27**, mostly console crash fixes (same source). On PC, layouts *did* change: the CommonLibSSE-NG changelog shows "model AE 1.7.99 layout changes (#298)" on **2026-08-22**, plus corrected `RUNTIME_DATA` offsets for 1.7.99 and "offset gaps in SkyrimVM/PlayerCharacter" (https://github.com/alandtse/CommonLibSSE-NG/blob/ng/CHANGELOG.md — **U**, confidence high). At launch, *"no SKSE DLLs created with CommonLib NG work anymore 🙂 All DLLs must be recompiled"* (https://forums.nexusmods.com/topic/13543597 — **U**, 2026-08-20, confidence high) — because Address Library itself moved to a new **format 5** that older CommonLib couldn't parse; one author documents a DLL failing with `failed to open address library file` and a hook offset landing one byte inside an instruction after code shifted (https://www.nexusmods.com/skyrimspecialedition/mods/189617 — **U**, 2026-08-25, confidence high). Current CommonLibSSE-NG (v10.1.0, 2026-09-30) fully supports 1.7.x.

**What this changes:** Address Library format changes can break *the version-independence mechanism itself* — so "Address Library makes us immune" is false; expect a recompile per major patch regardless.

### 4.2 SKSE for 1.7.x; PO3, PapyrusUtil, Engine Fixes builds

SKSE: **2.3.0** for 1.7.99 on release day, then **2.3.1** for 1.7.104 — the SKSE site lists "Current Anniversary Edition build 2.3.1 (game version 1.7.104)", with 2.2.6 retained for GOG 1.6.1179 and 2.0.20 for 1.5.97 (https://skse.silverlock.org/ — **U**, confidence high). Address Library v12 for 1.7.99 shipped **2026-08-20** (same day) and v13 for 1.7.104 on **2026-08-27** (https://www.nexusmods.com/skyrimspecialedition/mods/32444?tab=files — **U**, confidence high). PO3's Papyrus Extender 6.5.1 was last updated **2026-08-26** with 1.7.x support (https://www.nexusmods.com/skyrimspecialedition/mods/22854 — **U**, confidence high). SSE Engine Fixes lagged: Nexus build 7.0.20 (2026-08-25) initially failed on 1.7.104, with a working 1.7.104 build available via GitHub Actions by ~**2026-09-07** (https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=posts — **U**; https://github.com/aers/EngineFixesSkyrim64 — **U**, confidence medium-high). PapyrusUtil: no 1.7.x build found — its Nexus page shows no post-April-2026 release activity in what I could see; treat as **unknown** (https://www.nexusmods.com/skyrimspecialedition/mods/13048?tab=posts — **U**, confidence low). The community-maintained SKSE Plugin Status wiki (https://modding.wiki/en/skyrim/users/skse-plugins — **U**, updated 2026-09-20) tracks per-version compatibility and is the reference to watch.

**What this changes:** Every dependency we generate stubs from (PO3) is 1.7.x-ready; PapyrusUtil's silence doesn't block us but signals some maintainers move slowly — another reason not to hard-depend on others' native plugins.

### 4.3 Player share across 1.5.97 / 1.6.1170 / 1.6.1179 / 1.7.x

**No reliable public data found.** Bethesda and Nexus publish no per-version splits, and Steam gives only aggregate player counts. Qualitative signals only: pre-update, 1.6.1170 was "the current stable version… for a ready-to-go mod list" (https://tech.sportskeeda.com/gaming-news/how-downgrade-skyrim-patch-1-6-1170 — **U**, 2026-08-31, confidence medium); post-update, downgrade guides (Steam console `download_depot` back to 1.6.1170) spread widely and a "Best of Both Worlds Downgrade Patcher" exists (https://www.nexusmods.com/skyrimspecialedition/mods/169962 — **U**), implying a substantial stubborn 1.6.1170 population; the 1.5.97 holdout community persists but is ageing (https://forums.nexusmods.com/topic/13510557 — **U**, 2025-03). New/returning players on Steam default to 1.7.104.

**What this changes:** Smallest test that settles this: scrape download counts of the 1.6.1170-only vs all-version files of a popular SKSE mod (e.g. Address Library versionlib files, or Engine Fixes' per-version downloads) — a usable proxy for the active modding population split.

### 4.4 Do VM-only plugins survive patches better?

Yes, per community diagnosis: *"Mods with dll and USSEP (if you use it) [need updating]. The rest should still work fine… Some mods are already version independent, although they rely on SKSE."* (https://forums.nexusmods.com/topic/13545321 — **U**, 2026-09-16, confidence high). The 1.7.99 event is the natural experiment: pure-Papyrus mods needed nothing; struct-dependent native plugins broke until recompiled; and even the breakage of NG plugins was mostly *binary-level* (Address Library format), fixed by recompile rather than code changes. SKSE itself still hard-pins to the latest Steam version ("SKSE will support the latest version of Skyrim available on Steam, and only this version" — https://skse.silverlock.org/ — **U**), so *SKSE* is always the first thing that must catch up, and it did so same-day.

**What this changes:** Strong support for the generic-dispatcher design: a plugin touching only the VM, event sinks and SKSE APIs — with all engine specifics behind Address Library IDs — is in the "recompile and ship" class, not the "reverse-engineer again" class.

### 4.5 How authors ship across runtimes

The dominant pattern is **one DLL, multi-target**: CommonLibSSE-NG *"is able to create builds for any combination of these runtimes… a single DLL that works in any Skyrim runtime… no need to make a choice of multiple download options"* (https://github.com/alandtse/CommonLibSSE-NG — **U**, confidence high); the sample plugin does this by default (https://gitlab.com/colorglass/commonlibsse-sample-plugin — **U**). STEP forum consensus: *"Many (most?) SKSE mods these days have an 'NG' version or an installer that lets you choose the game version"* (https://stepmodifications.org/forum/topic/21542 — **U**, 2026-08-28, confidence medium-high). The FOMOD-with-per-version-DLLs pattern persists mainly for older/abandoned mods.

**What this changes:** Ship one flatrim (SE+AE) DLL. With CommonLibSSE-NG ≥6.5 the same binary should cover 1.6.1170 and 1.7.x.

---

## Open unknowns (and smallest settling test)

1. **Nexus staff stance on a helper-process mod** → email support@nexusmods.com with the design before release; smallest test: one email.
2. **Embeddable-zip AV record in practice** → upload our exact release archive to VirusTotal before publishing.
3. **Helper process under Proton** → Part C live test 5; no public precedent exists to shortcut this.
4. **Actual player version split** → scrape per-version download counts on Address Library / Engine Fixes Nexus files.
5. **PapyrusUtil 1.7.x status** → check its Nexus files tab directly (blocked by 403 in this research); matters only if we consume it.

## Dead ends

- No public documentation of any Skyrim mod hosting an interpreter or launching a helper process validated under Proton/Wine (searched multiple phrasings).
- No per-game-version player-share statistics exist publicly.
- Nexus Mods pages return 403 to automated fetches; several Nexus claims above rest on indexed page text (labelled **U**), not verified page reads.
- No Nexus rule text specifically about scripting hosts or user-supplied code — the absence itself is the finding.
