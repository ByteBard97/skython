# Task 48: `SkythonTest.esp` — quest, scripted aliases, LocationAlias, stages, objective, scripts, cell and NPC

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Owner decisions**, **Contracts** (Papyrus, and the **Test ESP** section: every record, alias, stage, objective, initial property value and function body this task builds), the **Gates** paragraphs "ESP/VMAD spike" and "Owner decision 8", and the Part F brief for Task 48 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (Steps 1-8: scripts, builder, `build.sh`, pins), then the Windows box with the game (Steps 9-11).
>
> **Gates:** Task 47 recorded **PASS** in `spikes/RESULTS.md` (else STOP: this task is re-planned). **Owner decision 8** (a STOP gate in the index's Owner decisions table) decides the names `SetupScenario` and `skython_test`: if it is still open when Step 3 would write `SkythonTestQuest.psc`, STOP and ask; if it was rejected, use the owner's names and framing (and tell the controller that Task 55 must be re-planned). Gate 0 (decision 7) for the box steps.
>
> **STOP and ask the owner (do not improvise) if:** a gate above is not met; `verify-esm` prints a `MISMATCH`; two builds differ, or `build.sh` reports a pin mismatch without a source change that explains it; the plugin does not load or `coc SkythonTestCell` does not reach the cell; the game crashes.

**Design.** The builder from Task 47 gains `full` mode (`TestEsp.cs`) and `verify-esm`; the spike mode stays (its output is unchanged, `00a11e2e...`). All FormIDs are fixed, so rebuilds are byte-identical (measured while writing this plan on a draft without the location-alias script and the initial property values: the same SHA-256 twice; Step 6 measures and pins this build's values). Records (all `SkythonTest.esp|0x0008xx`): quest `SkythonTestQuest` 0x800 (AllowRepeatedStages); location `SkythonTestLocation` 0x801; interior cell `SkythonTestCell` 0x802 (DefaultLightingTemplate, location 0x801, a 4x-scaled `ImpFloorMid01` floor, a `COCMarkerHeading`); NPC base `SkythonTestNPCBase` 0x803 (templated on the neutral vanilla `WEFarmerMale`, so the game uses that NPC's face, stats, inventory and AI); persistent actor `SkythonTestNPC` 0x804; persistent `XMarker` `SkythonTestMarker` 0x805. Referencing Skyrim.esm makes it the one master (`MAST` is written from the FormLinks). Quest aliases 0 `SkythonTarget`, 1 `SkythonItem`, 2 `SkythonExtra` (reference aliases with `SkythonTestAlias`), 3 `SkythonPlace` (location alias with the script `SkythonTestLocationAlias`: R45 drives only aliases that carry a script, and Task 50 calls the native `LocationAlias` functions on it through that script); stages 10/20/100/200 each with one log entry and a fragment that sends `stage_<n>` on `skython_test`; objective 10 with three `<Alias=...>` tags (probe 1, Task 52). `SkythonTestAlias.OnDeath` sets stage 100, so killing the actor in alias 0 raises both `death` and a `channel` `stage_100` (Tasks 53, 55; aliases 1 and 2 carry the same script, tests use alias 0). Auto properties start at fixed values (Contracts: Int/Float/Bool/String by Papyrus initializers in the `.psc`; `SkythonTestQuest.Target` = `SkythonTestNPC` through a VMAD object property, since Papyrus has no Form initializer), so Task 51 asserts them exactly. The NPC inherits `WEFarmerMale`'s base data: not essential, not protected (Tasks 53 and 55 kill it; the smoke test checks `IsEssential` is false). All scripts use vanilla functions only (the compile imports vanilla sources, not SKSE's).

**Files:**
- Create: `testesp/papyrus/SkythonTestQuestBase.psc`, `SkythonTestQuest.psc`, `SkythonTestAlias.psc`, `SkythonTestLocationAlias.psc`, `QF_SkythonTestQuest_01000800.psc`; `testesp/compiled/*.pex` (committed; `.gitignore` negation from Task 46)
- Create: `testesp/builder/TestEsp.cs`; Modify: `testesp/builder/Program.cs` (modes `full`, `verify-esm`), `testesp/builder/Dump.cs` (prints quest script properties; the spike's dump is unchanged because its scripts have none)
- Create: `testesp/expected-dump.txt`, `testesp/build.sh`, `testesp/SkythonTest.esp` (committed test data)
- Create: `livetest/test_m15_esp_smoke.py`
- Modify: `docs/PINS.md` (pin lines), `spikes/RESULTS.md`

**Interfaces:**
- Consumes: Task 47's builder (`Names`, `SpikeEsp`, `Dump`, the restore/build commands), `tools/papyrus_compile.sh` (with `--stable-hash`) and `plugin/papyrus/compiled/Skython.pex` (Task 46), the box tools (Task 45: `box_deploy.sh --esp/--test-pex` with the six-name list, `box_plugins.sh backup/add`, the quit/stop tools' `restore-if-added`).
- Produces (exactly the index Contracts' **Test ESP** section; Tasks 49-56 rely on these values):
  - Form keys: quest `SkythonTest.esp|0x000800`, location `|0x000801`, cell `|0x000802` (editor id `SkythonTestCell`, the `coc` target), NPC base `|0x000803`, actor `SkythonTestNPC` `|0x000804`, marker `SkythonTestMarker` `|0x000805`.
  - Alias ids (all optional): 0 `SkythonTarget`, 1 `SkythonItem`, 2 `SkythonExtra` (ReferenceAlias + `SkythonTestAlias`), 3 `SkythonPlace` (LocationAlias + `SkythonTestLocationAlias`). Objective index 10. Stages 10, 20, 100, 200 → `Skython.Send("skython_test", "stage_<n>")`. The actor in alias 0 dies → stage 100 (`SkythonTestAlias.OnDeath`, also on aliases 1-2). The NPC is not essential.
  - `SkythonTestQuest`: auto properties `Counter` Int (`7`), `Ratio` Float (`0.5`), `Flag` Bool (`True`), `Label` String (`"initial label"`), `Target` Actor (`SkythonTestNPC`, VMAD); full property `Computed` Int (= `Counter * 2`); `SetupScenario(Actor, ObjectReference, Location, String)` (starts the quest if needed, fills aliases 0, 1 and 3, sets `Target`/`Label`, stage 10, displays objective 10); `Int GetCounter()`, `Int GetBaseCounter()`, `String DescribeAlias(ReferenceAlias)` (`"none"`, `"foreign"`, `"empty"` or `"filled"`), `Int BumpCounter(Int n)` (adds `n` to `Counter`, returns the new `Counter`). `SkythonTestQuestBase`: `BaseCounter` Int (`3`), `BaseLabel` String (`"base label"`). `SkythonTestAlias`: `Tag` Int (`5`), `OnDeath` → stage 100. `SkythonTestLocationAlias` (extends `LocationAlias`): `Tag` Int (`6`).
  - `testesp/build.sh [--check|--print-pins]`: exit 0 only when the ESP's sha256 and each `.pex`'s stable hash equal the `- pin <path> sha256|stable <hex>` lines in `docs/PINS.md` and the read-back equals `testesp/expected-dump.txt`.

- [ ] **Step 1: Gates**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -n 'Task 47, ESP/VMAD spike: PASS' spikes/RESULTS.md || echo "STOP: no Task 47 PASS"
sed -n '/^\*\*8\. Acceptance test framing/,/^## /p' OWNER-DECISIONS-2026-10-02.md
```
Expected: the Task 47 PASS line. Then find the owner's recorded answer to decision 8 (the owner's reply, the controller's brief, or `.superpowers/sdd/README/progress.md`; the recommendation in `OWNER-DECISIONS-2026-10-02.md` is not an answer). Accepted → the names below stand. Open → STOP before Step 3. Rejected → STOP and get the owner's names.

- [ ] **Step 2: The expected read-back (offline test) and see it fail**

Create `testesp/expected-dump.txt`:
```text
plugin SkythonTest.esp masters=Skyrim.esm
location SkythonTestLocation 000801:SkythonTest.esp
npc SkythonTestNPCBase 000803:SkythonTest.esp race=013746:Skyrim.esm template=1034E4:Skyrim.esm
cell SkythonTestCell 000802:SkythonTest.esp block=0/5 location=000801:SkythonTest.esp
  persistent SkythonTestNPC 000804:SkythonTest.esp base=000803:SkythonTest.esp
  persistent SkythonTestMarker 000805:SkythonTest.esp base=00003B:Skyrim.esm
  temporary - 000806:SkythonTest.esp base=000032:Skyrim.esm
  temporary - 000807:SkythonTest.esp base=03CB89:Skyrim.esm
quest SkythonTestQuest 000800:SkythonTest.esp flags=AllowRepeatedStages form_version=0
  alias 0 SkythonTarget Reference flags=Optional, AllowDead, AllowDisabled, AllowReserved, AllowCleared
  alias 1 SkythonItem Reference flags=Optional, AllowDead, AllowDisabled, AllowReserved, AllowCleared
  alias 2 SkythonExtra Reference flags=Optional, AllowDead, AllowDisabled, AllowReserved, AllowCleared
  alias 3 SkythonPlace Location flags=Optional, AllowCleared
  stage 10 log_entries=1
  stage 20 log_entries=1
  stage 100 log_entries=1
  stage 200 log_entries=1
  objective 10 "Skython test: <Alias=SkythonTarget>, <Alias=SkythonItem>, <Alias=SkythonPlace>"
  vmad version=5 objformat=2 unknown=2 file=QF_SkythonTestQuest_01000800 scripts=SkythonTestQuest,QF_SkythonTestQuest_01000800
  script-property script=SkythonTestQuest name=Target flags=Edited object=000804:SkythonTest.esp alias=-1
  fragment stage=10 index=0 unknown2=1 script=QF_SkythonTestQuest_01000800 function=Fragment_0
  fragment stage=20 index=0 unknown2=1 script=QF_SkythonTestQuest_01000800 function=Fragment_1
  fragment stage=100 index=0 unknown2=1 script=QF_SkythonTestQuest_01000800 function=Fragment_2
  fragment stage=200 index=0 unknown2=1 script=QF_SkythonTestQuest_01000800 function=Fragment_3
  alias-vmad alias=0 quest=000800:SkythonTest.esp scripts=SkythonTestAlias
  alias-vmad alias=1 quest=000800:SkythonTest.esp scripts=SkythonTestAlias
  alias-vmad alias=2 quest=000800:SkythonTest.esp scripts=SkythonTestAlias
  alias-vmad alias=3 quest=000800:SkythonTest.esp scripts=SkythonTestLocationAlias
```
```bash
DOTNET_CLI_TELEMETRY_OPTOUT=1 timeout 300 ~/.dotnet/dotnet build testesp/builder -c Release --no-restore -v q 2>&1 | grep -E 'error|warn'
timeout 60 ~/.dotnet/dotnet testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll full out/t48; echo rc=$?
```
Expected: the build succeeds (Task 47's sources) and `full` prints the usage line with `rc=2`.

Add to `testesp/builder/Dump.cs`, inside `if (q.VirtualMachineAdapter is IQuestAdapterGetter vm)`, right after the `vmad` line (the spike's quest scripts have no properties, so `testesp/spike/expected-dump.txt` is unchanged):
```csharp
                    foreach (var sc in vm.Scripts)
                        foreach (var p in sc.Properties)
                            Console.WriteLine(p is IScriptObjectPropertyGetter op
                                ? $"  script-property script={sc.Name} name={p.Name} flags={p.Flags} object={op.Object.FormKey} alias={op.Alias}"
                                : $"  script-property script={sc.Name} name={p.Name} flags={p.Flags} kind={p.GetType().Name}");
```

- [ ] **Step 3: The Papyrus sources**

Create `testesp/papyrus/SkythonTestQuestBase.psc`:
```papyrus
Scriptname SkythonTestQuestBase extends Quest
{Skython test data: the parent script whose variables come before SkythonTestQuest's (property offset tests).}

; Initial values are fixed (index Contracts, Test ESP) so the property tests (Task 51) assert them exactly.
Int Property BaseCounter = 3 Auto
String Property BaseLabel = "base label" Auto
```
Create `testesp/papyrus/SkythonTestQuest.psc`:
```papyrus
Scriptname SkythonTestQuest extends SkythonTestQuestBase
{Skython test data, attached to SkythonTestQuest (SkythonTest.esp|0x000800). Alias ids: 0 SkythonTarget,
1 SkythonItem, 2 SkythonExtra (reference aliases with SkythonTestAlias), 3 SkythonPlace (location alias with
SkythonTestLocationAlias).}

; Initial values are fixed (index Contracts, Test ESP). Target has no Papyrus initializer: the quest's VMAD sets it to
; SkythonTestNPC (SkythonTest.esp|0x000804), see TestEsp.cs.
Int Property Counter = 7 Auto
Float Property Ratio = 0.5 Auto
Bool Property Flag = True Auto
String Property Label = "initial label" Auto
Actor Property Target Auto

; A full (non-auto) property: Skython refuses get/set_property on it before anything runs (R57/R58).
Int Property Computed
	Int Function Get()
		Return Counter * 2
	EndFunction
EndProperty

; The generic scenario (owner decision 8): start the quest, fill the aliases, show the objective.
Function SetupScenario(Actor akTarget, ObjectReference akItem, Location akPlace, String asLabel)
	If !IsRunning()
		Start()
	EndIf
	Target = akTarget
	Label = asLabel
	(GetAlias(0) as ReferenceAlias).ForceRefTo(akTarget)
	(GetAlias(1) as ReferenceAlias).ForceRefTo(akItem)
	(GetAlias(3) as LocationAlias).ForceLocationTo(akPlace)
	SetStage(10)
	SetObjectiveDisplayed(10, True)
EndFunction

Int Function GetCounter()
	Return Counter
EndFunction

Int Function GetBaseCounter()
	Return BaseCounter
EndFunction

; Vanilla calls only (no SKSE script sources needed to compile): "none", "foreign", "empty" or "filled".
String Function DescribeAlias(ReferenceAlias akAlias)
	If akAlias == None
		Return "none"
	EndIf
	If akAlias.GetOwningQuest() != Self
		Return "foreign"
	EndIf
	If akAlias.GetReference() == None
		Return "empty"
	EndIf
	Return "filled"
EndFunction

Int Function BumpCounter(Int n)
	Counter += n
	Return Counter
EndFunction
```
Create `testesp/papyrus/SkythonTestAlias.psc`:
```papyrus
Scriptname SkythonTestAlias extends ReferenceAlias
{Skython test data, on the reference aliases 0-2 of SkythonTestQuest.}

Int Property Tag = 5 Auto

; Killing the actor in an alias moves the quest to stage 100, whose fragment calls Skython.Send.
Event OnDeath(Actor akKiller)
	GetOwningQuest().SetStage(100)
EndEvent
```
Create `testesp/papyrus/SkythonTestLocationAlias.psc`:
```papyrus
Scriptname SkythonTestLocationAlias extends LocationAlias
{Skython test data, on the location alias 3 (SkythonPlace) of SkythonTestQuest. Skython drives only aliases that
carry a script (R45), so this script is what lets Task 50 call ForceLocationTo / GetLocation / Clear on alias 3.}

Int Property Tag = 6 Auto
```
Create `testesp/papyrus/QF_SkythonTestQuest_01000800.psc`:
```papyrus
;BEGIN FRAGMENT CODE - Do not edit anything between this and the end comment
;NEXT FRAGMENT INDEX 4
Scriptname QF_SkythonTestQuest_01000800 Extends Quest Hidden
{Skython test data: the stage fragments of SkythonTestQuest. The VMAD written by testesp/builder binds
stage 10 -> Fragment_0, 20 -> Fragment_1, 100 -> Fragment_2, 200 -> Fragment_3.}

;BEGIN FRAGMENT Fragment_0
Function Fragment_0()
;BEGIN CODE
Skython.Send("skython_test", "stage_10")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_1
Function Fragment_1()
;BEGIN CODE
Skython.Send("skython_test", "stage_20")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_2
Function Fragment_2()
;BEGIN CODE
Skython.Send("skython_test", "stage_100")
;END CODE
EndFunction
;END FRAGMENT

;BEGIN FRAGMENT Fragment_3
Function Fragment_3()
;BEGIN CODE
Skython.Send("skython_test", "stage_200")
;END CODE
EndFunction
;END FRAGMENT

;END FRAGMENT CODE - Do not edit anything between this and the begin comment
```

- [ ] **Step 4: `TestEsp.cs`, the new `Program.cs`, and the Skyrim.esm check**

Create `testesp/builder/TestEsp.cs`:
```csharp
using Mutagen.Bethesda;
using Mutagen.Bethesda.Skyrim;
using Noggog;

namespace SkythonTestEsp
{
    // Task 48: the test plugin. FormIDs are fixed here (never Mutagen's counter) so rebuilds are byte-identical and
    // tests can name every record by form key. The Skyrim.esm FormKeys below were looked up by EditorID in the
    // installed Skyrim.esm; `verify-esm` re-checks them (Task 48 Step 4).
    public static class TestEsp
    {
        static readonly ModKey Own = ModKey.FromNameAndExtension(Names.PluginFile);
        static readonly ModKey Esm = ModKey.FromNameAndExtension("Skyrim.esm");
        const SkyrimRelease R = SkyrimRelease.SkyrimSE;
        static FormKey O(uint id) => new FormKey(Own, id);
        static FormKey E(uint id) => new FormKey(Esm, id);

        // Skyrim.esm (EditorID -> FormKey, verified)
        static readonly FormKey NordRace = E(0x013746);
        static readonly FormKey WEFarmerMale = E(0x1034E4);         // neutral NPC_ used as the template
        static readonly FormKey EncClassBanditMelee = E(0x01CE17);  // the class WEFarmerMale itself carries (required field)
        static readonly FormKey DefaultLightingTemplate = E(0x0300E2);
        static readonly FormKey XMarker = E(0x00003B);
        static readonly FormKey COCMarkerHeading = E(0x000032);
        static readonly FormKey ImpFloorMid01 = E(0x03CB89);       // 256 x 256 floor piece; scaled x4 below

        static readonly (string EditorId, FormKey Key)[] EsmRefs =
        {
            ("NordRace", NordRace), ("WEFarmerMale", WEFarmerMale), ("EncClassBanditMelee", EncClassBanditMelee),
            ("DefaultLightingTemplate", DefaultLightingTemplate), ("XMarker", XMarker),
            ("COCMarkerHeading", COCMarkerHeading), ("ImpFloorMid01", ImpFloorMid01),
        };

        // `verify-esm <Skyrim.esm>`: prints "<EditorID> <FormKey> ok|MISMATCH <found>" for each reference above.
        public static int VerifyEsm(string esmPath)
        {
            using var esm = SkyrimMod.CreateFromBinaryOverlay(esmPath, R);
            var byKey = new System.Collections.Generic.Dictionary<FormKey, string>();
            foreach (var r in esm.EnumerateMajorRecords()) byKey[r.FormKey] = r.EditorID;
            var bad = 0;
            foreach (var (id, key) in EsmRefs)
            {
                byKey.TryGetValue(key, out var found);
                var ok = found == id;
                if (!ok) bad++;
                System.Console.WriteLine($"{id} {key} {(ok ? "ok" : "MISMATCH " + (found ?? "absent"))}");
            }
            return bad == 0 ? 0 : 1;
        }

        // Ours (SkythonTest.esp|0x0008xx)
        public const uint LocationId = 0x000801, CellId = 0x000802, NpcBaseId = 0x000803, NpcRefId = 0x000804,
                          MarkerId = 0x000805, CocMarkerId = 0x000806, FloorId = 0x000807, NextId = 0x000808;

        public static void Write(string path)
        {
            var mod = new SkyrimMod(Own, R);
            mod.Locations.Add(new Location(O(LocationId), R) { EditorID = "SkythonTestLocation", Name = "Skython Test Location" });
            mod.Npcs.Add(NpcBase());
            mod.Cells.Records.Add(CellBlockWith(TestCell()));
            mod.Quests.Add(TestQuest());
            mod.ModHeader.Author = "Skython test data";
            mod.ModHeader.Stats.NextFormID = NextId;
            mod.WriteToBinary(path);   // MAST Skyrim.esm is written from the FormLinks
        }

        static Npc NpcBase()
        {
            var npc = new Npc(O(NpcBaseId), R) { EditorID = "SkythonTestNPCBase", Name = "Skython Test NPC" };
            npc.Race = NordRace;
            npc.Class = EncClassBanditMelee;
            npc.Template = WEFarmerMale;
            npc.Configuration.Flags = NpcConfiguration.Flag.UseTemplate;
            npc.Configuration.TemplateFlags = NpcConfiguration.TemplateFlag.Traits | NpcConfiguration.TemplateFlag.Stats
                | NpcConfiguration.TemplateFlag.Factions | NpcConfiguration.TemplateFlag.SpellList
                | NpcConfiguration.TemplateFlag.AIData | NpcConfiguration.TemplateFlag.AIPackages
                | NpcConfiguration.TemplateFlag.BaseData | NpcConfiguration.TemplateFlag.Inventory
                | NpcConfiguration.TemplateFlag.DefPackList | NpcConfiguration.TemplateFlag.AttackData
                | NpcConfiguration.TemplateFlag.Keywords;
            return npc;
        }

        static Placement At(float x, float y, float z) =>
            new Placement { Position = new P3Float(x, y, z), Rotation = new P3Float(0, 0, 0) };

        static Cell TestCell()
        {
            var cell = new Cell(O(CellId), R) { EditorID = "SkythonTestCell", Name = "Skython Test Cell", Flags = Cell.Flag.IsInteriorCell };
            cell.LightingTemplate = DefaultLightingTemplate;
            cell.Location = O(LocationId);
            var npc = new PlacedNpc(O(NpcRefId), R) { EditorID = "SkythonTestNPC", MajorFlags = PlacedNpc.MajorFlag.Persistent, Placement = At(0, 128, 70) };
            npc.Base = O(NpcBaseId);
            cell.Persistent.Add(npc);
            var marker = new PlacedObject(O(MarkerId), R) { EditorID = "SkythonTestMarker", Placement = At(128, 0, 70) };
            marker.Base = XMarker;
            marker.MajorRecordFlagsRaw |= 0x400;   // persistent reference
            cell.Persistent.Add(marker);
            var coc = new PlacedObject(O(CocMarkerId), R) { Placement = At(0, -128, 70) };
            coc.Base = COCMarkerHeading;
            cell.Temporary.Add(coc);
            var floor = new PlacedObject(O(FloorId), R) { Scale = 4f, Placement = At(0, 0, 0) };
            floor.Base = ImpFloorMid01;
            cell.Temporary.Add(floor);
            return cell;
        }

        static CellBlock CellBlockWith(Cell cell)
        {
            // Creation Kit grouping for interior cells: block = last decimal digit of the FormID, sub-block = the one before.
            var sub = new CellSubBlock { BlockNumber = (int)(CellId / 10 % 10), GroupType = GroupTypeEnum.InteriorCellSubBlock };
            sub.Cells.Add(cell);
            var block = new CellBlock { BlockNumber = (int)(CellId % 10), GroupType = GroupTypeEnum.InteriorCellBlock };
            block.SubBlocks.Add(sub);
            return block;
        }

        // Alias ids (Interfaces): 0 SkythonTarget, 1 SkythonItem, 2 SkythonExtra (reference aliases, SkythonTestAlias),
        // 3 SkythonPlace (location alias, SkythonTestLocationAlias). Stages 10/20/100/200, fragments Fragment_0..Fragment_3.
        // Objective 10.
        const string LocationAliasScript = "SkythonTestLocationAlias";
        static readonly (ushort Stage, string Fragment)[] Stages = { (10, "Fragment_0"), (20, "Fragment_1"), (100, "Fragment_2"), (200, "Fragment_3") };

        static Quest TestQuest()
        {
            var quest = new Quest(O(Names.QuestId), R)
            {
                EditorID = Names.QuestEditorId, Name = "Skython Test Quest", Flags = Quest.Flag.AllowRepeatedStages,
                QuestFormVersion = 0,
            };
            var refFlags = QuestAlias.Flag.Optional | QuestAlias.Flag.AllowDisabled | QuestAlias.Flag.AllowReserved
                           | QuestAlias.Flag.AllowDead | QuestAlias.Flag.AllowCleared;
            string[] refNames = { "SkythonTarget", "SkythonItem", "SkythonExtra" };
            for (uint i = 0; i < refNames.Length; i++)
                quest.Aliases.Add(new QuestAlias { ID = i, Type = QuestAlias.TypeEnum.Reference, Name = refNames[i], Flags = refFlags });
            quest.Aliases.Add(new QuestAlias { ID = 3, Type = QuestAlias.TypeEnum.Location, Name = "SkythonPlace", Flags = QuestAlias.Flag.Optional | QuestAlias.Flag.AllowCleared });

            foreach (var (stage, _) in Stages)
            {
                var s = new QuestStage { Index = stage };
                s.LogEntries.Add(new QuestLogEntry { Flags = 0 });   // QSDT; fragment StageIndex 0
                quest.Stages.Add(s);
            }
            quest.Objectives.Add(new QuestObjective
            {
                Index = 10,
                DisplayText = "Skython test: <Alias=SkythonTarget>, <Alias=SkythonItem>, <Alias=SkythonPlace>",
            });

            var vm = new QuestAdapter { Version = 5, ObjectFormat = 2, Unknown = 2, FileName = Names.FragmentScript };
            // Target's initial value (Papyrus has no Form initializer): SkythonTestNPC. Alias -1 = a plain object.
            var questScript = new ScriptEntry { Name = "SkythonTestQuest" };
            questScript.Properties.Add(new ScriptObjectProperty
            {
                Name = "Target", Flags = ScriptProperty.Flag.Edited, Object = O(NpcRefId), Alias = -1,
            });
            vm.Scripts.Add(questScript);
            vm.Scripts.Add(new ScriptEntry { Name = Names.FragmentScript });
            foreach (var (stage, fragment) in Stages)
                vm.Fragments.Add(new QuestScriptFragment { Stage = stage, StageIndex = 0, Unknown2 = 1, ScriptName = Names.FragmentScript, FragmentName = fragment });
            for (short i = 0; i < refNames.Length; i++)
            {
                var a = new QuestFragmentAlias { Version = 5, ObjectFormat = 2, Property = new ScriptObjectProperty { Name = "", Object = quest.FormKey, Alias = i } };
                a.Scripts.Add(new ScriptEntry { Name = Names.AliasScript });
                vm.Aliases.Add(a);
            }
            // Location alias 3 carries its own script (R45: only scripted aliases can be driven).
            var loc = new QuestFragmentAlias { Version = 5, ObjectFormat = 2, Property = new ScriptObjectProperty { Name = "", Object = quest.FormKey, Alias = 3 } };
            loc.Scripts.Add(new ScriptEntry { Name = LocationAliasScript });
            vm.Aliases.Add(loc);
            quest.VirtualMachineAdapter = vm;
            return quest;
        }
    }
}
```
Replace `testesp/builder/Program.cs` with:
```csharp
using System;
using System.IO;

namespace SkythonTestEsp
{
    // Usage: SkythonTestEspBuilder spike <out_dir>   -> <out_dir>/SkythonTest.esp (Task 47: the VMAD spike)
    //        SkythonTestEspBuilder full <out_dir>    -> <out_dir>/SkythonTest.esp (Task 48: the test plugin)
    //        SkythonTestEspBuilder dump <file.esp>    -> a text summary of the plugin (offline tests)
    //        SkythonTestEspBuilder verify-esm <Skyrim.esm>  -> checks the Skyrim.esm FormKeys TestEsp uses
    // Mutagen refuses to write a mod under a file name other than its ModKey, so the output argument is a folder.
    public static class Program
    {
        public static int Main(string[] args)
        {
            if (args.Length == 2 && args[0] == "dump") { Dump.Print(args[1]); return 0; }
            if (args.Length == 2 && args[0] == "verify-esm") return TestEsp.VerifyEsm(args[1]);
            if (args.Length != 2 || (args[0] != "spike" && args[0] != "full"))
            {
                Console.Error.WriteLine("usage: SkythonTestEspBuilder spike|full <out_dir> | dump <file.esp> | verify-esm <Skyrim.esm>");
                return 2;
            }
            Directory.CreateDirectory(args[1]);
            var path = Path.Combine(args[1], Names.PluginFile);
            if (args[0] == "spike") SpikeEsp.Write(path); else TestEsp.Write(path);
            Console.WriteLine($"wrote {path}");
            return 0;
        }
    }
}
```
```bash
DOTNET_CLI_TELEMETRY_OPTOUT=1 timeout 300 ~/.dotnet/dotnet build testesp/builder -c Release --no-restore -v q 2>&1 | grep -E 'error|warn'
timeout 120 ~/.dotnet/dotnet testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll verify-esm "/mnt/games/SteamLibrary/steamapps/common/Skyrim Special Edition/Data/Skyrim.esm"; echo rc=$?
```
Expected: no errors or warnings; seven lines `NordRace 013746:Skyrim.esm ok`, `WEFarmerMale 1034E4:Skyrim.esm ok`, `EncClassBanditMelee 01CE17:Skyrim.esm ok`, `DefaultLightingTemplate 0300E2:Skyrim.esm ok`, `XMarker 00003B:Skyrim.esm ok`, `COCMarkerHeading 000032:Skyrim.esm ok`, `ImpFloorMid01 03CB89:Skyrim.esm ok`; `rc=0`. (The box's Skyrim.esm is the same 1.6.1170 release; Step 10 proves the references resolve there.)

- [ ] **Step 5: `testesp/build.sh`**

Create `testesp/build.sh` (then `chmod +x testesp/build.sh`):
```bash
#!/usr/bin/env bash
# Rebuild the test plugin and its Papyrus scripts, then compare them with the pins in docs/PINS.md (Task 48).
# Usage: testesp/build.sh               build into testesp/ (SkythonTest.esp, compiled/*.pex) and compare
#        testesp/build.sh --check       build into out/testesp-check/ and compare; the committed files are untouched
#        testesp/build.sh --print-pins  build into out/testesp-check/ and print the pin lines for docs/PINS.md
# Pins are lines "- pin <path> sha256 <hex>" (the ESP: Mutagen's output is byte-identical across builds) and
# "- pin <path> stable <hex>" (a .pex: tools/papyrus_compile.sh --stable-hash). Exit 0 only when every pin matches and
# Mutagen's read-back equals testesp/expected-dump.txt. A mismatch without a source change that explains it is a STOP.
# Offline only: dotnet restores from ~/.nuget/packages and never from the network.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOTNET="${DOTNET:-$HOME/.dotnet/dotnet}"
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
SCRIPTS=(SkythonTestQuestBase SkythonTestQuest SkythonTestAlias SkythonTestLocationAlias QF_SkythonTestQuest_01000800)
mode="${1:-build}"
case "$mode" in
  build) esp_dir="$ROOT/testesp"; pex_dir="$ROOT/testesp/compiled" ;;
  --check|--print-pins) esp_dir="$ROOT/out/testesp-check"; pex_dir="$ROOT/out/testesp-check/compiled" ;;
  *) echo "usage: testesp/build.sh [--check|--print-pins]" >&2; exit 2 ;;
esac
cd "$ROOT"
timeout 300 "$DOTNET" restore testesp/builder --source "$HOME/.nuget/packages" >/dev/null
timeout 300 "$DOTNET" build testesp/builder -c Release --no-restore -v q -nologo | grep -E 'error|warn' && exit 1 || true
B="testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll"
timeout 120 "$DOTNET" "$B" full "$esp_dir"
srcs=(); for s in "${SCRIPTS[@]}"; do srcs+=("testesp/papyrus/$s.psc"); done
tools/papyrus_compile.sh "${srcs[@]}" -o "$pex_dir" | grep -E '^compiled'
keys=(testesp/SkythonTest.esp)
declare -A got=(["testesp/SkythonTest.esp"]="sha256 $(sha256sum "$esp_dir/SkythonTest.esp" | cut -d' ' -f1)")
for s in "${SCRIPTS[@]}"; do
  keys+=("testesp/compiled/$s.pex")
  got["testesp/compiled/$s.pex"]="stable $(tools/papyrus_compile.sh --stable-hash "$pex_dir/$s.pex" | cut -d' ' -f1)"
done
if [ "$mode" = --print-pins ]; then
  for k in "${keys[@]}"; do echo "- pin $k ${got[$k]}"; done
  exit 0
fi
fail=0
timeout 120 "$DOTNET" "$B" dump "$esp_dir/SkythonTest.esp" | diff - testesp/expected-dump.txt > /dev/null \
  && echo "ok   dump equals testesp/expected-dump.txt" || { echo "FAIL dump differs from testesp/expected-dump.txt"; fail=1; }
for k in "${keys[@]}"; do
  want="$(awk -v k="$k" '$1 == "-" && $2 == "pin" && $3 == k { print $4 " " $5 }' docs/PINS.md)"
  if [ -z "$want" ]; then echo "FAIL no pin for $k in docs/PINS.md (got ${got[$k]})"; fail=1
  elif [ "$want" = "${got[$k]}" ]; then echo "ok   $k ${got[$k]}"
  else echo "FAIL $k: built ${got[$k]}, pinned $want"; fail=1; fi
done
[ "$fail" = 0 ] && echo "TEST ESP BUILD MATCHES PINS" || echo "TEST ESP BUILD DIFFERS"
exit "$fail"
```

- [ ] **Step 6: Build, pin, check**

```bash
testesp/build.sh --print-pins | grep '^- pin' > out/t48-pins-1.txt
testesp/build.sh --print-pins | grep '^- pin' > out/t48-pins-2.txt
diff out/t48-pins-1.txt out/t48-pins-2.txt && echo PINS_STABLE
cat out/t48-pins-1.txt
```
Expected: `PINS_STABLE`, then exactly six lines in this order: `- pin testesp/SkythonTest.esp sha256 <hex>`, then `- pin testesp/compiled/<name>.pex stable <hex>` for `SkythonTestQuestBase`, `SkythonTestQuest`, `SkythonTestAlias`, `SkythonTestLocationAlias`, `QF_SkythonTestQuest_01000800`. One value is known in advance because its source is the one this plan's draft measured unchanged: `- pin testesp/compiled/QF_SkythonTestQuest_01000800.pex stable a7df67748249d5e59e1c163922aab79db4d7406321edf2a4a91959039bbf0de5` (a different value means the compiler, flags file, machine or account differs from the draft's: STOP). The other five are this build's first measurement (the draft's values predate the location-alias script and the initial property values, so they are not comparable); the two runs agreeing is the reproducibility check.
Append those six lines to `docs/PINS.md` under the "Test ESP (Tasks 47-48)" section from Task 47, after one line `- Full plugin and scripts (Task 48), checked by testesp/build.sh:`. Then:
```bash
testesp/build.sh | grep -E '^(ok|FAIL)|TEST ESP'
testesp/build.sh --check | tail -1
git status --short testesp/
```
Expected: `ok   dump equals testesp/expected-dump.txt`, six `ok   <path> ...` lines, `TEST ESP BUILD MATCHES PINS` (both runs); `git status` shows the new `testesp/SkythonTest.esp`, `testesp/compiled/*.pex`, sources, `build.sh`, `expected-dump.txt`, and no `bin/`/`obj/`. Rebuild once more and confirm the spike is unchanged: `timeout 60 ~/.dotnet/dotnet testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll spike out/t48-spike && sha256sum out/t48-spike/SkythonTest.esp` → `00a11e2e3481e87d02f38d8ff23d0b8a42ce9df6fbd6bde0aca66c288479cbce`.

- [ ] **Step 7: Live smoke test file**

Create `livetest/test_m15_esp_smoke.py`:
```python
"""Task 48: the full SkythonTest.esp loads and holds what Tasks 49-55 expect. Needs testesp/SkythonTest.esp and
testesp/compiled/*.pex on the box (owner decision 7), the plugins.txt line (tools/box/box_plugins.sh add) and s4test
loaded. The last test moves the player into SkythonTestCell with `coc`. Nothing is saved."""
import queue
import subprocess
import time

import pytest

from skython import errors
from support import REPO

ESP = "SkythonTest.esp"
QUEST, LOCATION, CELL = f"{ESP}|0x000800", f"{ESP}|0x000801", f"{ESP}|0x000802"
NPC, MARKER = f"{ESP}|0x000804", f"{ESP}|0x000805"
PLAYER_ID = 0x14


def key(form) -> str:
    return (form.form_key or "").lower() if form is not None else ""


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


@pytest.fixture
def quest(game):
    return game.resolve(form_key=QUEST)


def test_plugin_and_records_resolve(game):
    assert game.call("Game", "IsPluginInstalled", ESP) is True
    for k, kind in [(QUEST, "Quest"), (LOCATION, "Location"), (CELL, "Cell"), (NPC, "Actor"), (MARKER, "ObjectReference")]:
        f = game.resolve(form_key=k)
        assert key(f) == k.lower() and f.type == kind, (k, f)
    assert key(game.resolve(editor_id="SkythonTestQuest")) == QUEST.lower()


def test_quest_scripts_are_attached(game, quest):
    # The fixed initial values (index Contracts, Test ESP) prove both scripts are attached and initialised.
    assert game.call_method(quest, "SkythonTestQuest", "GetCounter") == 7
    assert game.call_method(quest, "SkythonTestQuest", "GetBaseCounter") == 3


def test_stage_fragments_send_on_skython_test(game, quest):
    q = queue.Queue()
    sub = game.on_channel("skython_test", q.put)
    try:
        for stage in (10, 20, 200):
            assert game.call_method(quest, "Quest", "SetStage", stage) is True
            assert q.get(timeout=15).data["payload"] == f"stage_{stage}"
    finally:
        sub.close()


def test_aliases_carry_their_scripts(game, quest):
    if not game.call_method(quest, "Quest", "IsRunning"):
        assert game.call_method(quest, "Quest", "Start") is True
    player = game.resolve(form_id=PLAYER_ID)
    for alias_id in (0, 1, 2):
        alias = game.alias(quest, alias_id)
        game.call_method(alias, "ReferenceAlias", "ForceRefTo", player)
        try:
            assert game.get_property(alias, "SkythonTestAlias", "Tag") == 5
        finally:
            game.call_method(alias, "ReferenceAlias", "Clear")
    place = game.alias(quest, 3)
    game.call_method(place, "LocationAlias", "ForceLocationTo", game.resolve(form_key=LOCATION))
    try:
        assert game.get_property(place, "SkythonTestLocationAlias", "Tag") == 6
    finally:
        game.call_method(place, "LocationAlias", "Clear")


def parent_cell(game, ref) -> str:
    try:
        return key(game.call_method(ref, "ObjectReference", "GetParentCell"))
    except errors.NotReady:
        return ""  # the cell is loading


def test_coc_into_the_test_cell(game):
    r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "coc SkythonTestCell"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    player = game.resolve(form_id=PLAYER_ID)
    deadline = time.monotonic() + 180
    while parent_cell(game, player) != CELL.lower():
        assert time.monotonic() < deadline, "the player never arrived in SkythonTestCell"
        time.sleep(1)
    game.wait_ready(timeout=120)
    npc = game.resolve(form_key=NPC)
    assert parent_cell(game, npc) == CELL.lower()
    assert game.call_method(npc, "ObjectReference", "Is3DLoaded") is True
    assert game.call_method(npc, "Actor", "IsDead") is False
    assert game.call_method(npc, "Actor", "IsEssential") is False  # Tasks 53 and 55 kill it
```
```bash
uv run --project python pytest livetest/test_m15_esp_smoke.py -q   # skipped without SKYTHON_LIVE
```
Expected: `5 skipped`.

- [ ] **Step 8: Commit**

```bash
git add testesp/papyrus testesp/compiled testesp/builder/TestEsp.cs testesp/builder/Program.cs testesp/builder/Dump.cs \
        testesp/expected-dump.txt \
        testesp/build.sh testesp/SkythonTest.esp docs/PINS.md livetest/test_m15_esp_smoke.py
git commit -m "feat(testesp): SkythonTest.esp with quest, aliases, stages, cell, NPC and its scripts"
```

- [ ] **Step 9: Deploy (Gate 0)**

The full build replaces Task 47's spike files on the box (same recorded paths, so `put_file` overwrites only our copies).
```bash
tools/box/box_deploy.sh --esp testesp/SkythonTest.esp
tools/box/box_deploy.sh --test-pex plugin/papyrus/compiled/Skython.pex testesp/compiled/SkythonTestQuestBase.pex \
  testesp/compiled/SkythonTestQuest.pex testesp/compiled/SkythonTestAlias.pex testesp/compiled/SkythonTestLocationAlias.pex \
  testesp/compiled/QF_SkythonTestQuest_01000800.pex
tools/box/box_plugins.sh backup && tools/box/box_plugins.sh add
```
Expected: one `deployed ... -> ...\Data\SkythonTest.esp`, six `deployed ... -> ...\Data\Scripts\...` lines (the whole decision-7 list), `BACKUP_SAME`, `ADDED`.

- [ ] **Step 10: Live smoke on `s4test`**

```bash
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_esp_smoke.py -v -rs
tools/box/box_shot.sh out/t48-cell.png
tools/box/box_files.sh pull Skython.log >/dev/null; grep -E '\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken
tools/box/box_quit.sh || tools/box/box_stop.sh
tools/box/box_session.sh down
```
Expected: 5 passed (the last one leaves the player in `SkythonTestCell` next to the farmer-looking `SkythonTestNPC`; `out/t48-cell.png` shows a lit floor, not a black void); no error lines; the quit tool ends with `RESTORED <sha>`. Loading `s4test` with the ESP added raises no missing-content prompt (the ESP is added, not removed). If the screenshot shows the player falling or a black room, record it: the floor or lighting choice in `TestEsp.cs` is wrong; fix, rebuild, re-pin (a source change explains the new hash) and rerun.

- [ ] **Step 11: Record and commit**

Append to `spikes/RESULTS.md`:
```markdown
- **Task 48, SkythonTest.esp (box, Windows-native, <date time>, Skyrim 1.6.1170.0, s4test, never saved; 1 launch; SKSE plugins loaded: <list>):** owner decision 8 <accepted on <date> | names per the owner: ...>. Built by testesp/build.sh: SkythonTest.esp sha256 <the pinned value from Step 6> (master Skyrim.esm), five test-ESP .pex at the pinned stable hashes. `test_m15_esp_smoke.py` 5 passed: plugin installed; quest/location/cell/actor/marker resolve by form key with types Quest/Location/Cell/Actor/ObjectReference; `SkythonTestQuest.GetCounter`/`GetBaseCounter` -> 7/3 (initial values); stages 10/20/200 -> `stage_10`/`stage_20`/`stage_200` on skython_test; aliases 0-2 carry SkythonTestAlias (Tag 5), alias 3 carries SkythonTestLocationAlias (Tag 6); `coc SkythonTestCell` -> player and SkythonTestNPC in the cell, NPC 3D loaded, alive, not essential (screenshot out/t48-cell.png: <what it shows>). **Owner removal list:** Data\SkythonTest.esp and the six Data\Scripts .pex of the decision-7 list (SkythonTestLocationAlias.pex is new with this build; the others hold the Task 48 build over Task 47's copies).
```
```bash
git add spikes/RESULTS.md
git commit -m "docs(results): Task 48 test plugin verified on the box"
```
