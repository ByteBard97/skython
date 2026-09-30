# Question 4: Skyrim 1.7.x versions and plugin ecosystem (as of 2026-09-30)

**Verdict.** Build for 1.6.1170 and 1.7.104 from one alandtse CommonLibSSE-NG DLL, and treat 1.7.x as a real layout break, not just an address shuffle. On 1.7.99, most PlayerCharacter members moved by 8 bytes because of an extra base class.\[1\] The Address Library moved to a new "format 5" file.\[2\] The result was that every older CommonLib DLL failed until it was recompiled.\[3\] SKSE 2.3.1 (1.7.104), Address Library v13, PO3 Papyrus Extender 6.5.x and PapyrusUtil 4.8 all had 1.7.x builds within about ten days. SSE Engine Fixes put out a 1.7.99-only beta on 24 Aug 2026, but as of 25 Sep 2026 its Nexus page still had no 1.7.104 build. A sticky post there points users to a GitHub Actions build instead (Nexus posts tab, snippet). Only the alandtse fork of CommonLibSSE-NG is still actively fixing 1.7.x bindings. As late as 29–30 Sep 2026 it was still patching IDs that were missing on 1.7.x.\[4\] I found no hard data on what share of players runs each version. The signals conflict: big modlists and downgrade tools keep 1.6.1170 alive, while SKSE and the main frameworks have moved to 1.7.104. A plugin that sticks to the Papyrus VM and event sinks touches very few version-specific addresses and layouts. It still needs a rebuild whenever CommonLib's ABI or the Address Library format changes, as happened at 1.7.99.

Labels: V = I opened the page and read the relevant part; U = search snippet only; R = recall.

## 4.1 What changed in 1.7.99 / 1.7.104; layouts; CommonLib support

**Answer.** 1.7.99 came out on 20 Aug 2026 and 1.7.104 on 27 Aug 2026.\[5\] Bethesda's notes cover Creations, console storage and Creation Kit changes only; they say nothing about engine internals.\[6\]\[7\] Reverse-engineering (RE) work does show layout changes in 1.7.99. The 1.7.104 patch moved function addresses but, per one patch author, changed no data structures.\[8\] An unconfirmed "1.7.106" is mentioned once.\[9\] Among the CommonLib forks, alandtse's is the one carrying 1.7.x.

**Evidence**
- Bethesda, "Skyrim Update – August 20" (V, 2026-08-20; hotfix section dated Aug 27). The notes are about Creations, console storage and the CK. The one engine-relevant line is "Fixed a crash when the player transforms into a new race."\[7\] https://elderscrolls.bethesda.net/en-US/news/skyrim-update-august-20
- UESP (U): "Version 1.7.104 was released on August 27, 2026. It addressed issues on Xbox and PlayStation."\[5\] https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch. \[5\] My fetch returned an old cached copy, so this stays U.
- alandtse PR #343 (V, mentioned 10 Sep 2026): "most members shifted by 8 bytes due to an additional base class… `StartPowerAttackCoolDown` writes the same member at `+0x90C` in AE 1.6.1170 and `+0x914` in AE 1.7.99."\[1\] https://github.com/alandtse/CommonLibSSE-NG/pull/343
- alandtse PR #400 (V, merged 30 Sep 2026): IsValidName's ID "82331 exists in every 1.6.x address library and in no 1.7.x one, 524270 in both 1.7.x ones".\[4\] The fix picks the ID per version with `AE_CHECK`. The PR notes say "Not run on a game" (from the subagent's reading).\[4\] https://github.com/alandtse/CommonLibSSE-NG/pull/400
- alandtse PR #401 (U): two more bindings "absent from every 1.7.x address library". The same PR claims that across 913 bindings, "These three ids… are the only 1.7 gaps."\[10\] https://github.com/alandtse/CommonLibSSE-NG/pull/401
- DynamicArmorVariants 1.7.104 patch author (U): "1.7.104 moved every function in the game. It did not change any data structure."\[8\] https://www.nexusmods.com/skyrimspecialedition/mods/189578
- JContainers issue #138 (V) mentions "1.7.99, 1.7.104, 1.7.106 in just the last couple weeks".\[9\] Neither Bethesda nor UESP confirms a 1.7.106, so treat it as unverified. https://github.com/ryobg/JContainers/issues/138
- alandtse releases (V, via subagent): v7.0.0 (2026-08-30) has "version AE>=1.7.99 frame fields"; v7.5.3 (2026-09-12) has "correct PlayerCharacter 1.7.x accessors".\[11\] The first release with 1.7.99 support is most likely v6.7.1. The TrueHUD 1.7.99 Unofficial DLL Patch says it was "Recompiled against CommonLibSSE-NG (alandtse) v6.7.1, which adds Address Library format 5 support for 1.7.99" (U, https://www.nexusmods.com/skyrimspecialedition/mods/189792). Other third-party patches cite "CommonLibSSE-NG 6.7.0 — format 5 support plus the 1.7.99 layouts" (U, https://www.nexusmods.com/skyrimspecialedition/mods/191335). I could not open either release page, so there is no primary date or note.
- CharmedBaryon/CommonLibSSE-NG: I found no 1.7.x release (U). Its last visible release is v3.7.0, and a third party calls it "the stale CharmedBaryon fork" (U, via subagent).\[12\]\[13\] powerof3/CommonLibSSE `dev` has 1.7 IDs (commit bd4f90a, 10 Sep 2026, seen through PR #400, V). However, it replaced the 1.6 ID outright, which "would break AE 1.6".\[4\]
- SteamDB depot and build history: dead end (see below).

**Confidence:** high on the dates and the PlayerCharacter shift; medium on "1.7.104 changed no structures"; low on the exact alandtse version and date that first supported 1.7.99.

**What this changes for us:** pin alandtse CommonLibSSE-NG at v7.5.3 or later (ideally a release that includes #400 and #401). Do not use CharmedBaryon. Do not read PlayerCharacter fields directly.

## 4.2 SKSE, PO3, PapyrusUtil, Engine Fixes, Address Library for 1.7.x

**Answer.** SKSE 2.3.0 supports 1.7.99 and 2.3.1 supports 1.7.104. GOG stays on 2.2.6 (1.6.1179) and SE on 2.0.20 (1.5.97).\[14\]\[15\] The JContainers GitHub releases compatibility table lists the same mapping: "AE/64 | 2.3.0 | 1.7.99; GOG | 2.2.6 | 1.6.1179" and "AE/64 | 2.3.1 | 1.7.104" (U, https://github.com/ryobg/jcontainers/releases). PO3 Papyrus Extender and PapyrusUtil published 1.7.x builds between 25 and 30 Aug 2026. SSE Engine Fixes published only a 1.7.99 beta (24 Aug 2026); as of 25 Sep 2026 it had no 1.7.104 build on Nexus.

**Evidence**
- SKSE site (U; my fetch got HTTP 429): "Current Anniversary Edition build 2.3.1 (game version 1.7.104)… Current GOG Anniversary Edition build 2.2.6 (game version 1.6.1179)". The same page announces "2026-08-14: …Bethesda has announced an incoming update".\[14\] https://skse.silverlock.org/ \[14\]
- SKSE Nexus changelog (U). 2.3.0: "support for 1.7.99". 2.3.1: "support for 1.7.104", "fix PlayerCharacter functions", "better compatibility reporting for plugins that need to be rebuilt due to the address library change".\[16\] https://www.nexusmods.com/skyrimspecialedition/mods/30379. The posts tab gives the dates (U): "2026-08-20: Game version 1.7.99 was released, and SKSE64 2.3.0 was released to support it. 2026-08-27: Game version 1.7.104 was released, and SKSE64 2.3.1 was released to support it." https://www.nexusmods.com/skyrimspecialedition/mods/30379?tab=posts
- Address Library (V): version 13, "Last updated 27 August 2026, 3:30PM", by meh321.\[17\] https://www.nexusmods.com/skyrimspecialedition/mods/32444. The files tab (U) also lists an older build, "Address Library All in One (1.7.99.0) v12 Date uploaded 20 Aug 2026", posted the same day as the patch. It says v13 "is for ALL game versions up to 1.7.104.0" (https://www.nexusmods.com/skyrimspecialedition/mods/32444?tab=files). One user reports "1.7.x… needs Address Library v13 or newer" (U, https://nexusmods.com/skyrimspecialedition/mods/192919). \[18\]
- PO3 Papyrus Extender (U): 6.5.0 was uploaded 25 Aug 2026 and 6.5.2 on 05 Sep 2026: "Supports SE 1.5.97 Supports SE/AE 1.6.1170 or 1.6.1179 GOG Supports SE/AE 1.7.99+".\[19\]\[20\] https://www.nexusmods.com/skyrimspecialedition/mods/22854?tab=files. One user reports 6.5.2 refusing to load on 1.7.104 ("newer version of Skyrim than this version… supports") while 6.5.1 worked (U, posts tab).\[21\]
- PapyrusUtil (U): "Version 4.8 for SKSE 2.3.1, Skyrim 1.7.104", uploaded 30 Aug 2026; 4.6 is still offered for 1.6.1170. https://www.nexusmods.com/skyrimspecialedition/mods/13048?tab=files \[22\]
- SSE Engine Fixes (U): "Engine Fixes 7.0.21 beta for Skyrim AE 1.7.99… uploaded 24 Aug 2026… This beta is for 1.7.99 only." 7.0.20 (26 Feb 2026) covers 1.5.97 and 1.6.1170+.\[23\] https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files
- RaceMenu author, 23 Aug 2026 (U): the 1.7.99 build is "pending an update for SKSE which currently has some broken Papyrus functions". https://www.nexusmods.com/skyrimspecialedition/mods/19080?tab=posts \[24\]

**Confidence:** high on the version mapping; medium on the dates (Nexus snippets).

**What this changes for us:** we need two dependency sets: SKSE 2.2.6 + AL v11 on 1.6.1170, and SKSE 2.3.1 + AL v13 on 1.7.104. PO3 ships one FOMOD for both, which is a model we can copy.

## 4.3 Player share by runtime

**Answer.** Unknown. I found no Nexus per-file download counts (they show "--" to logged-out fetches), no polls and no telemetry. The qualitative signals conflict.

**Evidence**
- Arthmoor on AFK Mods (U, author opinion): "The grand majority of players updated their games and had no issues".\[25\] https://www.afkmods.com/index.php?%2Ftopic%2F10584-skyrim-1799-is-still-not-working-wait-or-downgrade%2F=
- Sportskeeda, 31 Aug 2026 (U, journalist's assumption): 1.6.1170 support is aimed at "what I assume is the majority of the modding player base".\[26\] https://tech.sportskeeda.com/gaming-news/how-downgrade-skyrim-patch-1-6-1170 \[26\]
- Downgrade tooling (U): wSkeever's "Steam 1.7.104 – 1.6.1170 – 1.5.97 Best of Both Worlds Downgrade Patcher" (388 endorsements) and a CK downgrader (uploaded 30 Aug 2026).\[27\]\[28\] https://www.nexusmods.com/skyrimspecialedition/mods/169962?tab=description
- Modlists (U, conflicting): a LoreRim readme snippet says "The specific version used is 1.7.99", but my fetch cut off before that section, so it stays U.\[29\]\[30\] ADT pins 1.6.1170. Wabbajack featured-list rules require installs "from the latest version from steam" through the Stock Game auto-downgrade.\[31\]\[32\] https://v4.lorerim.com/read-me, https://wiki.wabbajack.org/wabbajack_cdn_and_gallery_access/Criteria%20for%20'Featured%20Mod%20List'%20status.html

**Confidence:** low.

**What this changes for us:** until real data exists, supporting both 1.6.1170 and 1.7.104 is mandatory. 1.5.97 is optional.

## 4.4 Does Papyrus-VM-only survive patches better?

**Answer.** Partly. What breaks at a patch like 1.7.99 happens at three levels:
- **Hard-coded offsets:** break every time.
- **CommonLib ABI / Address Library format:** every older CommonLib DLL broke at 1.7.99 until recompiled, whatever the plugin actually did.\[33\]
- **Plugin-specific hooks and struct fields:** need per-plugin re-derivation.

A VM-plus-event-sink design avoids the third level almost entirely but not the second. It also inherits SKSE's own Papyrus bugs.

**Evidence**
- Forum user (U): "no SKSE DLLs created with CommonLib NG work anymore… All DLLs must be recompiled".\[3\] https://forums.nexusmods.com/topic/13543597-skyrim-se-updated-to-1799/
- DynamicStringDistributor patch (U): CommonLib 4.26.2 "only understands formats 1 and 2 and cannot read the file at all". The fix meant porting to 6.7.0 and re-deriving a hook offset.\[34\] https://www.nexusmods.com/skyrimspecialedition/mods/189617
- GoToBed patch (U): for 1.7.104 "Nothing had to be rebuilt". A byte patch had to be "re-derived, and gated at runtime so 1.6.x keeps the offset".\[2\] https://www.nexusmods.com/skyrimspecialedition/mods/191335
- JContainers #138 (V): the old build "hardcodes offsets" and needs re-patching every release. An Address Library rewrite was "verified working on 1.6.1170" and 1.7.99.\[9\]
- Inference (R/own reasoning): our dispatcher uses `BSScript::IVirtualMachine`, `ScriptEventSourceHolder` and form lookups. These are among the most-used CommonLib bindings, and #401's sweep found only three 1.7 gaps overall.

**Confidence:** medium.

**What this changes for us:** keep engine hooks at zero. Route everything through the VM and event sinks. Plan to rebuild on every CommonLib release that follows a game patch.

## 4.5 One NG DLL vs per-runtime builds

**Answer.** Both approaches are in use. CommonLibSSE-NG's multi-runtime design selects IDs and offsets at runtime (`AE_CHECK`, version shifts), so one DLL can cover 1.6.x and 1.7.x.\[35\] Several authors still ship separate 1.7.x builds (Infinity UI NG, Engine Fixes beta, PapyrusUtil).\[18\]

**Evidence**
- CharmedBaryon README (U): "a single DLL that works in any Skyrim runtime… both versions before 1.6.629 and those after are supported".\[36\] https://github.com/CharmedBaryon/CommonLibSSE-NG \[37\]
- PR #400/#343 (V): per-version IDs and +8 shifts are handled inside CommonLib. The limitation: coverage depends on each binding being fixed, and IDs taken from upstream po3 without a version check "would break AE 1.6".\[1\]\[4\]
- Template README (U): "Hook IDs and offsets must still be found manually for each version."\[38\] https://github.com/Monitor221hz/CommonLibSSE-NG-Template-Plugin
- Infinity UI NG (U): "each build only loads on the runtimes it was built for"; it ships a separate 1.7.x build.\[18\]
- Licence: alandtse fork is "GPL-3.0-or-later" with Modding/Linking Exceptions (U, DAV patch README, https://github.com/Juan-MZ/DynamicArmorVariants-1.7.99-Patch). \[39\]

**Confidence:** medium-high.

**What this changes for us:** ship one flatrim DLL (SE+AE) and test it on both 1.6.1170 and 1.7.104 before each release.

## Open unknowns → smallest test
1. **Does one alandtse DLL load and dispatch on both 1.6.1170 and 1.7.104?** Build ChronicleBridge against alandtse v7.5.3 or later. Under Proton, run one Papyrus call and one death-event sink on each runtime.
2. **The first alandtse release with 1.7.99 support, and its date:** read CHANGELOG.md on the `ng` branch.
3. **Does 1.7.106 exist?** Check the SteamDB depot history in a browser.
4. **Runtime share:** read the Nexus per-file "Unique DLs" for PapyrusUtil 4.6 vs 4.8 while logged in.
5. **Are SKSE 2.3.x Papyrus natives fixed (the RaceMenu report)?** Call the SKSE natives we generate stubs for on 1.7.104.

## Dead ends
- skse.silverlock.org returned HTTP 429.
- Nexus pages load but hide download counts from logged-out fetches; most Nexus data here is snippet-only (U).
- The UESP fetch returned a stale cache.
- LoreRim readme fetch was truncated.
- alandtse releases page 2 and the v6.x release notes never loaded.
- I did not open SteamDB history or any Reddit polls.
- I did not open the CharmedBaryon repo contents (fetch truncated).

## Sources

1. [fix(AE): correct PlayerCharacter 1.7.x accessors by alandtse · Pull Request #343 · alandtse/CommonLibSSE-NG](https://github.com/alandtse/CommonLibSSE-NG/pull/343)
2. [GoToBed-1.7.104-Patch at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/191335)
3. [Skyrim SE updated to 1.7.99? - Skyrim SE - Nexus Mods Forums](https://forums.nexusmods.com/topic/13543597-skyrim-se-updated-to-1799/)
4. [chore: sync powerof3/CommonLibSSE dev (2026-09-29) by alandtse · Pull Request #400 · alandtse/CommonLibSSE-NG](https://github.com/alandtse/CommonLibSSE-NG/pull/400)
5. [Skyrim:Special Edition Patch - UESP Wiki - The Unofficial Elder Scrolls Pages](https://en.uesp.net/wiki/Skyrim:Special_Edition_Patch)
6. [The Elder Scrolls V: Skyrim Special Edition - The Elder Scrolls V: Skyrim Anniversary Edition - Update 1.7.99 (Updated Aug 27) - Steam News](https://store.steampowered.com/news/app/489830/view/681885122980479679)
7. [Skyrim Update - August 20](https://elderscrolls.bethesda.net/en-US/news/skyrim-update-august-20)
8. [DynamicArmorVariants-1.7.104-Patch at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/189578)
9. [Ground-up rewrite on CommonLibSSE + Address Library (fixes 1.7.99+ breakage permanently) — proposal, feedback welcome](https://github.com/ryobg/JContainers/issues/138)
10. [fix(AE): add 1.7 ids for two bindings by alandtse · Pull Request #401 · alandtse/CommonLibSSE-NG](https://github.com/alandtse/CommonLibSSE-NG/pull/401)
11. [Releases · alandtse/CommonLibSSE-NG](https://github.com/alandtse/CommonLibSSE-NG/releases)
12. [Feature/1.7.104 commonlibsse ng migration by Soneka96 · Pull Request #74 · Soneka96/DovahLink](https://github.com/Soneka96/DovahLink/pull/74)
13. [Releases · CharmedBaryon/CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG/releases)
14. [Skyrim Script Extender (SKSE)](https://skse.silverlock.org/)
15. [Skyrim Script Extender (SKSE64) at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/30379?tab=logs)
16. [Skyrim Script Extender (SKSE64) at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/30379)
17. [Address Library for SKSE Plugins](https://www.nexusmods.com/skyrimspecialedition/mods/32444)
18. [Infinity UI NG at Skyrim Special Edition Nexus - Mods and Community](https://nexusmods.com/skyrimspecialedition/mods/192919)
19. [powerofthree's Papyrus Extender at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/22854?tab=posts)
20. [powerofthree's Papyrus Extender at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/22854?tab=files)
21. [powerofthree's Papyrus Extender at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/22854?tab=posts&topic_id=8876738)
22. [PapyrusUtil SE - Modders Scripting Utility Functions at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/13048?tab=files)
23. [SSE Engine Fixes (skse64 plugin) at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/17230?tab=files)
24. [RaceMenu at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/19080?tab=posts)
25. [Skyrim 1.7.99 is still not working, wait or downgrade? - General Discussion - AFK Mods](https://www.afkmods.com/index.php?%2Ftopic%2F10584-skyrim-1799-is-still-not-working-wait-or-downgrade%2F=)
26. [How to downgrade Skyrim to patch 1.6.1170](https://tech.sportskeeda.com/gaming-news/how-downgrade-skyrim-patch-1-6-1170)
27. [Steam 1.7.99 - 1.6.1170 - 1.5.97 Best of Both Worlds Downgrade Patcher at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/169962?tab=files&file_id=792768)
28. [Creation Kit Downgrade Patcher (1.7.99) at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/190110)
29. [ReadMe](https://v4.lorerim.com/read-me)
30. [LoreRim — Grokipedia](https://grokipedia.com/page/LoreRim)
31. [Criteria for 'Featured Mod List' status - Wabbajack Documentation](https://wiki.wabbajack.org/wabbajack_cdn_and_gallery_access/Criteria%20for%20'Featured%20Mod%20List'%20status.html)
32. [github.com](https://github.com/The-Animonculory/ADT)
33. [Address Library for SKSE Plugins at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/32444?tab=posts)
34. [DynamicStringDistributor-1.7.104-Patch at Skyrim Special Edition Nexus - Mods and Community](https://www.nexusmods.com/skyrimspecialedition/mods/189617)
35. [alandtse/CommonLibSSE-NG](https://deepwiki.com/alandtse/CommonLibSSE-NG)
36. [GitHub - CharmedBaryon/CommonLibSSE-NG: This is a reverse engineered library for Skyrim Special Edition and Skyrim VR. · GitHub](https://github.com/CharmedBaryon/CommonLibSSE-NG)
37. [CommonLibSSE NG: CommonLibSSE NG ("Next Generation")](https://ng.commonlib.dev/)
38. [GitHub - Monitor221hz/CommonLibSSE-NG-Template-Plugin · GitHub](https://github.com/Monitor221hz/CommonLibSSE-NG-Template-Plugin)
39. [GitHub - Juan-MZ/DynamicArmorVariants-1.7.99-Patch: Unofficial compatibility patch that gets Dynamic Armor Variants working again on Skyrim 1.7.99. Just the recompiled DLL - the original mod is required. All credit to Parapets. Made for the community, nothing asked in return. · GitHub](https://github.com/Juan-MZ/DynamicArmorVariants-1.7.99-Patch)
