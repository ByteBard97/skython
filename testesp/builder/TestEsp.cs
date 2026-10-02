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
            // ANAM (NextAliasID) has no settable property in Mutagen 23.4.0: its writer computes it from the aliases.
            // With aliases 0-3 it writes ANAM = 4, the next free alias id, as vanilla quests do (checked in the bytes).
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
