using Mutagen.Bethesda;
using Mutagen.Bethesda.Skyrim;

namespace SkythonTestEsp
{
    // Task 47: one quest, one optional reference alias carrying SkythonTestAlias, one stage (10) whose QF_ fragment
    // calls Skython.Send. No masters (nothing from Skyrim.esm is referenced).
    public static class SpikeEsp
    {
        public static void Write(string path)
        {
            var modKey = ModKey.FromNameAndExtension(Names.PluginFile);
            var mod = new SkyrimMod(modKey, SkyrimRelease.SkyrimSE);
            var quest = new Quest(new FormKey(modKey, Names.QuestId), SkyrimRelease.SkyrimSE)
            {
                EditorID = Names.QuestEditorId,
                Name = "Skython Test Quest",
                Flags = Quest.Flag.AllowRepeatedStages,   // the spike sets stage 10 twice (console, then call_method)
                QuestFormVersion = 0,                     // as most Skyrim.esm quests (Mutagen's default is 255)
            };
            quest.Aliases.Add(new QuestAlias
            {
                ID = 0, Type = QuestAlias.TypeEnum.Reference, Name = "SkythonTestAlias0",
                Flags = QuestAlias.Flag.Optional | QuestAlias.Flag.AllowDisabled | QuestAlias.Flag.AllowReserved,
            });
            var stage = new QuestStage { Index = 10 };
            stage.LogEntries.Add(new QuestLogEntry { Flags = 0 });   // written as QSDT; the fragment's StageIndex 0 is this entry
            quest.Stages.Add(stage);

            // Version 5 / object format 2 / Unknown 2: what Skyrim.esm quest VMADs carry (758 of 856 with fragments; Unknown 2 in all).
            var vm = new QuestAdapter { Version = 5, ObjectFormat = 2, Unknown = 2, FileName = Names.FragmentScript };
            vm.Scripts.Add(new ScriptEntry { Name = Names.FragmentScript });
            vm.Fragments.Add(new QuestScriptFragment
            {
                Stage = 10, StageIndex = 0, Unknown2 = 1,
                ScriptName = Names.FragmentScript, FragmentName = "Fragment_0",
            });
            var alias = new QuestFragmentAlias
            {
                Version = 5, ObjectFormat = 2,
                Property = new ScriptObjectProperty { Name = "", Object = quest.FormKey, Alias = 0 },
            };
            alias.Scripts.Add(new ScriptEntry { Name = Names.AliasScript });
            vm.Aliases.Add(alias);
            quest.VirtualMachineAdapter = vm;

            mod.Quests.Add(quest);
            mod.ModHeader.Author = "Skython test data";
            mod.ModHeader.Stats.NextFormID = Names.QuestId + 1;
            mod.WriteToBinary(path);
        }
    }
}
