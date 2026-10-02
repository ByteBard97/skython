using System;
using System.Linq;
using Mutagen.Bethesda;
using Mutagen.Bethesda.Skyrim;

namespace SkythonTestEsp
{
    // `dump <esp>`: a plain-text summary of what the game will see (records, VMAD, fragments, aliases), read back through
    // Mutagen. The offline tests compare it with the expected text, so a builder change shows up as a readable diff.
    public static class Dump
    {
        public static void Print(string path)
        {
            using var mod = SkyrimMod.CreateFromBinaryOverlay(path, SkyrimRelease.SkyrimSE);
            var masters = string.Join(",", mod.ModHeader.MasterReferences.Select(m => m.Master.FileName.ToString()));
            Console.WriteLine($"plugin {System.IO.Path.GetFileName(path)} masters={(masters.Length > 0 ? masters : "-")}");
            foreach (var l in mod.Locations) Console.WriteLine($"location {l.EditorID} {l.FormKey}");
            foreach (var n in mod.Npcs) Console.WriteLine($"npc {n.EditorID} {n.FormKey} race={n.Race.FormKey} template={n.Template.FormKey}");
            foreach (var b in mod.Cells.Records)
                foreach (var sb in b.SubBlocks)
                    foreach (var c in sb.Cells)
                    {
                        Console.WriteLine($"cell {c.EditorID} {c.FormKey} block={b.BlockNumber}/{sb.BlockNumber} location={c.Location.FormKey}");
                        foreach (var r in c.Persistent) Console.WriteLine($"  persistent {r.EditorID ?? "-"} {r.FormKey} base={BaseOf(r)}");
                        foreach (var r in c.Temporary) Console.WriteLine($"  temporary {r.EditorID ?? "-"} {r.FormKey} base={BaseOf(r)}");
                    }
            foreach (var q in mod.Quests)
            {
                Console.WriteLine($"quest {q.EditorID} {q.FormKey} flags={q.Flags} form_version={q.QuestFormVersion}");
                foreach (var a in q.Aliases) Console.WriteLine($"  alias {a.ID} {a.Name} {a.Type} flags={a.Flags}");
                foreach (var s in q.Stages) Console.WriteLine($"  stage {s.Index} log_entries={s.LogEntries.Count}");
                foreach (var o in q.Objectives) Console.WriteLine($"  objective {o.Index} \"{o.DisplayText?.String}\"");
                if (q.VirtualMachineAdapter is IQuestAdapterGetter vm)
                {
                    Console.WriteLine($"  vmad version={vm.Version} objformat={vm.ObjectFormat} unknown={vm.Unknown} file={vm.FileName} scripts={string.Join(",", vm.Scripts.Select(s => s.Name))}");
                    foreach (var f in vm.Fragments)
                        Console.WriteLine($"  fragment stage={f.Stage} index={f.StageIndex} unknown2={f.Unknown2} script={f.ScriptName} function={f.FragmentName}");
                    foreach (var a in vm.Aliases)
                        Console.WriteLine($"  alias-vmad alias={a.Property.Alias} quest={a.Property.Object.FormKey} scripts={string.Join(",", a.Scripts.Select(s => s.Name))}");
                }
            }
        }

        static string BaseOf(IPlacedGetter r) => r switch
        {
            IPlacedNpcGetter n => n.Base.FormKey.ToString(),
            IPlacedObjectGetter o => o.Base.FormKey.ToString(),
            _ => "?",
        };
    }
}
