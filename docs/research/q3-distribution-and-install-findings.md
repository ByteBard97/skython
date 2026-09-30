# Python-in-Skyrim: Distribution and Install (Question 3)

**Verdict:** Nothing in Nexus Mods' written rules bans a mod that bundles DLLs, an embedded interpreter, or a helper process. The real risks are practical. Loose EXEs get held for manual review. Frozen Python EXEs are often flagged by antivirus. Child processes spawned under MO2 get pulled into its virtual filesystem (USVFS), and one documented case crashed. Proton adds loopback and path quirks. Taken together, the evidence favours option A: embed CPython inside the plugin DLL using the official embeddable zip, ship no frozen EXE, open no network sockets, and keep user scripts in a fixed, documented folder. Use option B, the child process, only as a fallback, and only after a Proton + MO2 test passes.

## TL;DR
- **Nexus rules (V):** no rule forbids bundled executables, DLLs, interpreters or helper processes. The rules ban malware and internet access that isn't "crucial". New uploaders' EXEs get held for manual review. Auto-update and code download are the real red lines.
- **Antivirus and hosting (V/U):** frozen Python EXEs (PyInstaller, and Nuitka less often) have a long record of false positives, including Mantella's own Nexus files. I found no reports about the embeddable zip. Embedding CPython in the DLL avoids a new EXE entirely.
- **Proton and MO2 (V/U):** a Mantella-style helper plus loopback HTTP works under Proton after fixes. MO2's USVFS hooks child processes, and one documented case of a spawned `cmd.exe` crashing under MO2 argues against option B.

## Q1. Nexus rules on executables, DLLs, interpreters, helper processes
**Answer:** None of this is prohibited. The limits are malware, internet access that isn't crucial, and scanner-hostile packaging.

**Evidence:**
- File Submission Guidelines, https://help.nexusmods.com/article/28-file-submission-guidelines (V, last updated Sept 29, 2026):
  - "Files that are intended to destroy or otherwise cause harm to a user's person or property, such as viruses, malware, adware, etc., are prohibited."\[1\]
  - "Files (especially executables) that connect to the internet to download or send information and/or files are prohibited unless where it is crucial… 'auto update' functionality does not qualify as crucial." Authors whose tools need network access are told to email staff "providing the source code for your tool/app."\[1\]
- Quarantine article, https://help.nexusmods.com/article/117-why-has-my-mod-been-quarantined (V, Sept 22, 2026): "any kind of self-extracting file… will be marked for review". Nested archives are rejected, and password-protected archives break the guidelines.\[2\]
- Community Manager Pickysaurus, https://forums.nexusmods.com/topic/13512258-nexus-says-my-mod-has-some-suspicious-files/ (V, Apr 7, 2025): for uploaders who are "unknown" to the site, "we require certain files to be manually approved. This is usually things like EXE files that aren't normally part of mods."\[3\]
- Virus Scanning article, https://help.nexusmods.com/article/128-anti-virus-false-positives (V, Dec 31, 2024): a red cross "doesn't automatically mean the file is malicious".\[4\] An older Nexus news post, "Virus scan reports now visible on file pages" (https://www.nexusmods.com/site/news/12378, per enricher), states: "If more than 4 anti-virus programs used by VirusTotal flag the file as suspicious the file will be sent to a quarantine that will require one of the moderation team to verify the file is either safe, or not safe, before it can be downloaded."
- Precedents: Mantella ships `Mantella.exe` under `SKSE\Plugins\MantellaSoftware\` (V, Mantella docs),\[5\] and SkyLink ships a .NET server under `Data\SKSE\Plugins\SkyLinkAI_Server\` (U).\[6\]

**Confidence:** high for what the rules say. Medium on whether the rules are complete, because the Terms of Service page wasn't opened.
**What this changes for us:** we can ship a DLL plus the embeddable zip. Don't add auto-update or pip-from-internet. Upload as .7z/.zip with no nested archives.\[2\]

## Q2. Antivirus false positives: frozen EXE vs embeddable zip
**Answer:** Frozen EXEs are the documented problem. I found no false-positive reports about the embeddable zip, which is an absence of reports, not proof that it's clean.

**Evidence:**
- Mantella issue #283, https://github.com/art-from-the-machine/Mantella/issues/283 (V): "False positive detection by Anti-virus increased since v0.10 … Avast and AVG". A Nexus user reported "win64:malware-gen".\[7\] Mantella's troubleshooting page (V) says repeatedly to "add Mantella.exe to your antivirus's whitelist".\[5\] There is partial, secondary support that Mantella.exe is a PyInstaller build: DeepWiki's summary of Mantella's setup code (https://deepwiki.com/art-from-the-machine/Mantella/2-installation-and-setup, U) says it "Removes old temporary files from previous runs, including _MEI folders from PyInstaller."
- PyInstaller #8164 (V, per enricher): on v6.3.0, "15-18 AV vendors (mostly low-tier, but also including McAfee and Bitdefender, sometimes Google) flag my executable as malware, namely Gen:Variant.Cerbu", and another commenter added "In Microsoft Defender as well". "Downgrading to v5.13.2 simply resolved the issue." Maintainer rokm suggested compiling the bootloader yourself, but another commenter replied "Building your own bootloader no longer seems to work." PyInstaller #6754 (U) covers `--onefile` false positives, and a locally built bootloader "gave MORE false postives".\[8\]
- Nuitka #1804 (U): `--onefile` build flagged as "Trojan:Win32/Sabsik.TE.A!ml by Windows Defender".\[9\] Nuitka #853 (U): Defender flags builds made with `--windows-disable-console`.\[10\]
- A secondary blog, pythonguis.com (U), claims Nuitka "tends to trigger fewer AV false positives".\[11\] That is a vendor-style claim.
- python.org docs, https://docs.python.org/3/using/windows.html (U): the embeddable package is "a minimal Python environment" for "embedding into larger applications".\[12\] The docs say nothing about antivirus. python.org's Downloads page (https://www.python.org/downloads/, per enricher) states that "The Windows installers and all binaries produced as part of each Python release are signed using an Authenticode signing certificate issued to the Python Software Foundation," which should help with SmartScreen and Defender reputation. A python.org Discourse post, however, says the certificate for 3.12.8 and 3.13.1 "has been revoked", so pin 3.12.10+ or 3.13.7+.

**Confidence:** high that frozen EXEs are a real risk. Low on how the embeddable zip compares.
**What this changes for us:** avoid PyInstaller and Nuitka entirely. Under option A no new EXE is shipped. Only our DLL and PSF's `pythonXY.dll` would be present, plus `python.exe` if we choose to ship it.

## Q3. Working Proton/Wine setups and known breakages
**Answer:** Yes, partly. A helper process with loopback HTTP has worked end to end under Proton. Known breakages are localhost/IPv6 resolution, the browser UI, and older random USVFS failures.

**Evidence:**
- mantella-linux PR #1, https://github.com/i-machine-things/mantella-linux/pull/1 (V, Sep 9, 2026; fork, one developer): with a native-Linux Python backend and the game under Proton, the "Full conversation … confirmed working end-to-end in Skyrim". The fix needed was dual-stack binding "so the game's SKSE_HTTP plugin can reach the backend via both localhost and 127.0.0.1 (this system resolves localhost to ::1 first)". It also documents "Proton/Flatpak sandboxing" issues.\[13\]
- Mantella #521, https://github.com/art-from-the-machine/Mantella/issues/521 (V, Feb 16, 2025): under Proton the UI "is frozen and will not actually load". The reply reads "this part works fine for me on arch linux with proton 10".\[14\] Upstream PR #617, "Add linux support", was opened Dec 5, 2025 (U).\[15\]
- Mantella #367 (U): Linux users hit `Z:\home\...` versus `/home/...` path validation errors.\[16\]
- USVFS under Wine (ksp_stuff #28, V via subagent, about 2018): "Programs now launch properly (thanks to CreateProcessInternalW having been implemented in Wine 3.19)". Writes were rerouted to `overwrite`.\[17\] MO2 #372 (U) reports it "will fail to load the VFS+EXE (tools/game) randomly" under Wine 5.\[18\] The rockerbacon installer (U) says MO2 2.4.4 "can successfuly load mods when running inside Proton 6.3".\[19\]
- Named pipes (WineHQ forum, U): local named pipes work "inside the same WINEPREFIX". Network pipes don't,\[20\] and heavy pipe traffic loads `wineserver`.\[21\]
- SkyLink lists "Windows 10 or 11" (U).\[6\] I found no Proton report for it.

**Confidence:** medium.
**What this changes for us:** if we use IPC, bind both 127.0.0.1 and ::1, or connect to 127.0.0.1 directly, and never rely on "localhost". An in-prefix Windows python.exe avoids the Z:\ path problem. Option A avoids IPC altogether.

## Q4. MO2 / Vortex handling of helper processes and runtime-written files
**Answer:** MO2's USVFS hooks child processes, unless the executable is blacklisted. New files written into virtualised paths land in Overwrite. Vortex hardlinks mods from staging and flags edits to files it deployed.

**Evidence:**
- The official MO2 page (U, snippet) says the VFS works "by hooking the target program (and any of it's children processes)".\[22\] MO2 #507 (V, subagent, about 2018) shows `chrome.exe` pulled into usvfs, which led to an executable blacklist.\[23\]
- TFWWorkbenchMO2Patcher, https://github.com/dataterminals/TFWWorkbenchMO2Patcher (V): "Under MO2, os.execute spawns a cmd.exe child that access-violates immediately (0xC0000005) — the likely cause being USVFS hooking CreateProcess and injecting into the child."\[24\] (The author's inference; different game, UE4SS.)
- Overwrite: "The overwrite folder is where MO2 puts ALL output files that do not already have an active instance loaded at the time of creation" (Nexus forum, U).\[25\] Edits to existing files change them where they actually live, in their mod folder (MO2 2.1.2 changelog via blog, U).\[26\]
- Vortex docs, https://github.com/Nexus-Mods/Vortex/blob/master/docs/mod-management/EXTERNAL-CHANGES.md (V): "Vortex deploys mods by creating links (hardlinks/symlinks) from a staging folder". Edits to deployed files raise a dialog, and for a deleted file the default is "Save change (delete file)" (destructive).\[27\] (R/inference: new files not in the manifest are left alone.)
- How mods tell users where files live: Mantella's docs (V) say "Mantella.exe can be found in your mods folder\Mantella\SKSE\Plugins\MantellaSoftware\".\[5\] Logs go to "Documents/My Games/Mantella/logging.log",\[28\] and a `custom_user_folder.ini` redirects save data. The docs also warn "Ensure that you are not running Mantella.exe via a Vortex / Mod Organizer 2 shortcut".\[5\] SKSE logs go to `Documents\My Games\Skyrim Special Edition\SKSE`, but on 1.6.1170 some plugins write to `My Games\Skyrim.INI\SKSE` (Nexus mod 144932, MO2 #2006, U).\[29\]\[30\]

**Confidence:** medium-high.
**What this changes for us:** write logs, caches and user scripts to `Documents\My Games\Skyrim Special Edition\SKSE\<Mod>\`, which is outside Data and avoids both Overwrite clutter and Vortex conflicts. Never rewrite shipped files. Option B's child would be hooked by USVFS, so it could crash or see a virtual Data folder.

## Q5. Nexus rules on user-supplied code; security practice; enforcement
**Answer:** Nexus has no rule on scripting hosts or user-supplied code. What applies is the malware ban, the internet rule, and the requirement that "files must be presented in good faith … with respect to any caveats".\[1\] Enforcement targets malware and download mechanisms.

**Evidence:**
- Guidelines (V, cited above): no clause covers scripting or user code. The internet rule would cover any script-download or package-install feature.
- Enforcement: malware mods on the Cyberpunk Nexus that hid unused .bin files were banned and removed, and users complained that takedown lagged behind the virus scan (https://www.nexusmods.com/cyberpunk2077/images/114269, U, Oct 2024).\[31\] A Steam Workshop case, not Nexus: a Cities: Skylines Harmony fork whose auto-updater could "remotely deploy any code" was removed (Notebookcheck, U, 2022).\[32\]
- Security practice: in mantella-linux (V), binding to `::` "listens on every network interface … exposing the API (and the LLM key it proxies) to the whole LAN". The fix binds 127.0.0.1 and ::1 only.\[13\] MLVScan on Nexus (U) flags "process execution, shell commands" in mod DLLs, which shows the community's detection norms.\[33\] DevBench being loopback-only with no auth is already settled.

**Confidence:** medium. No Nexus action against a scripting host was found.
**What this changes for us:** disclose on the mod page that the mod runs local Python scripts with full user rights. Ship no script downloader. If option B is used, bind loopback only and use a per-session random token.

## Recommendations
1. Choose **option A**: CPython embedded in the DLL, from the PSF embeddable zip, with the licence file bundled.
2. Put user scripts in `My Games\Skyrim Special Edition\SKSE\PySkyrim\scripts`. Document the path, and note the 1.6.1170 `Skyrim.INI` quirk.\[29\]
3. Before release, upload a private build to VirusTotal and check Defender on Windows.

## Open unknowns (smallest test)
- Does the embeddable zip pass VirusTotal and Nexus scanning cleanly? Upload the zip plus the DLL to VirusTotal.
- Does embedded CPython initialise under GE-Proton + MO2, with imports resolved through USVFS? Build a hello-world plugin that runs `import json` and writes a log file.
- Does an option-B `python.exe` child survive USVFS under Proton? Spawn it from the plugin, with and without an MO2 blacklist entry.
- Does the Nexus ToS add anything? Read https://help.nexusmods.com Terms of Service.

## Dead ends
- The Nexus ToS page was not opened.
- SkyrimNet, Pantella, xVASynth and Wabbajack: no Proton or antivirus reports found in the search budget.
- SkyLink's Nexus comments were not read.
- The official MO2 Nexus page only showed a login shell (subagent).
- I found no report of antivirus flags on the Python embeddable zip.

## Sources

1. [File Submission Guidelines - Nexus Mods Site Help](https://help.nexusmods.com/article/28-file-submission-guidelines)
2. [Why has my mod been quarantined? - Nexus Mods Site Help](https://help.nexusmods.com/article/117-why-has-my-mod-been-quarantined)
3. [Nexus says my mod has Some suspicious files](https://forums.nexusmods.com/topic/13512258-nexus-says-my-mod-has-some-suspicious-files/)
4. [Virus Scanning at Nexus Mods - Nexus Mods Site Help](https://help.nexusmods.com/article/128-anti-virus-false-positives)
5. [Issues Q&A - Mantella](https://art-from-the-machine.github.io/Mantella/pages/issues_qna.html)
6. [GitHub - jarvann/SkryimMCM: Skyrim MCM for direct access to runtime engine via AI like Claude Desktop · GitHub](https://github.com/jarvann/SkryimMCM)
7. [False positive detection by Anti-virus increased since v0.10](https://github.com/art-from-the-machine/Mantella/issues/283)
8. [My --onefile exe is getting anti-Virus False positive flags and is disabled by windows. · Issue #6754 · pyinstaller/pyinstaller](https://github.com/pyinstaller/pyinstaller/issues/6754)
9. [Question: Compiled file detected as Trojan:Win32/Sabsik.TE.A!ml by Windows Defender · Issue #1804 · Nuitka/Nuitka](https://github.com/Nuitka/Nuitka/issues/1804)
10. [virus detected when using --windows-disable-console · Issue #853 · Nuitka/Nuitka](https://github.com/Nuitka/Nuitka/issues/853)
11. [How to Fix Antivirus False Positives with PyInstaller Executables](https://www.pythonguis.com/faq/problems-with-antivirus-software-and-pyinstaller/)
12. [4\. Using Python on Windows — Python 3.14.7 documentation](https://docs.python.org/3/using/windows.html)
13. [Port Mantella to run natively on Linux (Steam Proton + xVASynth) by i-machine-things · Pull Request #1 · i-machine-things/mantella-linux](https://github.com/i-machine-things/mantella-linux/pull/1)
14. [Browser GUI does not load if running through Proton/Wine](https://github.com/art-from-the-machine/Mantella/issues/521)
15. [Pull requests · art-from-the-machine/Mantella](https://github.com/art-from-the-machine/Mantella/pulls)
16. [Bad config.ini generation · Issue #367 · art-from-the-machine/Mantella](https://github.com/art-from-the-machine/Mantella/issues/367)
17. [Exploring USVFS compatibility](https://github.com/qsniyg/ksp_stuff/issues/28)
18. [Linux-Wine USVFS compatibility thread · Issue #372 · ModOrganizer2/modorganizer](https://github.com/Modorganizer2/modorganizer/issues/372)
19. [Updating to Proton 6.3 · Issue #275 · Furglitch/modorganizer2-linux-installer](https://github.com/rockerbacon/modorganizer2-linux-installer/issues/275)
20. [Windows Pipes - WineHQ Forums](https://forum.winehq.org/viewtopic.php?f=2&t=22150)
21. [IPC / Pipes without wineserver - WineHQ Forums](https://forum.winehq.org/viewtopic.php?t=30811)
22. [Mod Organizer 2 at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/6194?tab=description)
23. [MO2 unlocking - usvfs waited for chrome.exe before completing](https://github.com/ModOrganizer2/modorganizer/issues/507)
24. [GitHub - dataterminals/TFWWorkbenchMO2Patcher: Patch a stock TFWWorkbench install to work under Mod Organizer 2 for The Forever Winter. Ships the transform, not the mod.](https://github.com/dataterminals/TFWWorkbenchMO2Patcher)
25. [Mod Organizer 2: Overwrite? - Skyrim SE - Nexus Mods Forums](https://forums.nexusmods.com/topic/7822593-mod-organizer-2-overwrite/)
26. [Mod Organizer Overwrite Files - imcrimson](https://imcrimson.weebly.com/blog/mod-organizer-overwrite-files)
27. [Vortex/docs/mod-management/EXTERNAL-CHANGES.md at master · Nexus-Mods/Vortex](https://github.com/Nexus-Mods/Vortex/blob/master/docs/mod-management/EXTERNAL-CHANGES.md)
28. [Mantella, CHIM and SkyrimNet (2026): The Skyrim AI NPC Mods Compared — Setup, Cost and What Each Remembers](https://arcanumrpgs.com/blog/skyrim-ai-npc-mods/)
29. [SKSE Log Redirector for SkyrimSE.exe 1.6.117x - Mod Organizer Plugin at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/144932)
30. [Question - Source of Folder: \\Documents\\My Games\\Skyrim.INI\\SKSE · Issue #2006 · ModOrganizer2/modorganizer](https://github.com/ModOrganizer2/modorganizer/issues/2006)
31. [Public Awareness post Both of these mods are MALWARE at Cyberpunk 2077 Nexus - Mods and community](https://www.nexusmods.com/cyberpunk2077/images/114269)
32. [Notebookcheck Logo](https://www.notebookcheck.net/Potential-malware-ridden-Bitcoin-mining-Cities-Skylines-mods-removed-by-Steam.601257.0.html)
33. [MLVScan at Modding Tools - Nexus Mods](https://www.nexusmods.com/site/mods/1689)
