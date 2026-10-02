# Task 47: ESP/VMAD spike — Mutagen builds a quest whose stage fragment calls `Skython.Send` (gate)

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints** (Mutagen is GPL-3.0-only; box files under decision 7), **Owner decisions** (decision 7 is a STOP gate, never assumed), **Fixed paths** (.NET, Papyrus Compiler), **Contracts** (Papyrus: the fixed quest FormKey and fragment script name) and the **Gates** paragraph "ESP/VMAD spike" first. Steps use checkbox (`- [ ]`) syntax.
>
> **This task is THE gate for Tasks 48-53 and 55.** PASS means: a Mutagen-written quest with a scripted reference alias and a `QF_` stage fragment (VMAD with a fragment table) loads in the game, setting the stage runs the fragment and its `Skython.Send` arrives as a `channel` event, and the alias script is bound. **If any of that fails, STOP and ask the owner** (the alternatives are the Creation Kit on the box or xEdit scripts, spec open question 4); do not tune the VMAD by trial on the box.
>
> **Runs on:** Linux (Steps 1-7: builder, scripts, ESP, offline checks), then the Windows box with the game (Steps 8-10, Gate 0 required).
>
> **STOP and ask the owner (do not improvise) if:** Gate 0 is not open; `dotnet restore` wants the network; two builds give different SHA-256s; the plugin does not load, the fragment's event does not arrive (through either `SetStage` path) or the alias script is not bound; the game crashes or does not reach the main menu in 10 minutes; `plugins.txt` handling exits 3.

**What was settled while writing this plan (Linux, 2026-10-02, scratch builds; nothing deployed).** `Mutagen.Bethesda.Skyrim` 23.4.0 ships `net5.0`/`netstandard2.1` assemblies; restored offline from `~/.nuget/packages` they build and run under `net8.0`. Its API at this version: `new SkyrimMod(ModKey, SkyrimRelease.SkyrimSE)`, `new Quest(FormKey, SkyrimRelease)`, `Quest.VirtualMachineAdapter = new QuestAdapter { Version, ObjectFormat, Unknown, FileName, Scripts, Fragments, Aliases }`, `QuestScriptFragment { Stage, StageIndex, Unknown2, ScriptName, FragmentName }`, `QuestFragmentAlias { Property = ScriptObjectProperty { Object, Alias }, Scripts }`, `mod.WriteToBinary(path)`; FormLinks are assigned from a `FormKey` (implicit conversion), and `WriteToBinary` refuses a file name other than the mod key (`ModKeys were misaligned`), so the builder takes an output folder. Read back from Skyrim.esm: every quest VMAD with fragments has `Unknown = 2`, fragments `Unknown2 = 1`, `QF_` script also listed in `Scripts` (845/845), alias VMAD property name empty; 758 of 856 use version 5 / object format 2. An empty `QuestLogEntry` writes nothing, so the stage would have no log entry for the fragment's `StageIndex 0`; `Flags = 0` makes Mutagen write `QSDT`. Mutagen's default `QuestFormVersion` is 255; most Skyrim.esm quests have 0. Two builds of the spike are byte-identical: `00a11e2e3481e87d02f38d8ff23d0b8a42ce9df6fbd6bde0aca66c288479cbce`.

**Files:**
- Create: `testesp/builder/SkythonTestEspBuilder.csproj`, `Program.cs`, `Names.cs`, `SpikeEsp.cs`, `Dump.cs`, `LICENSE-NOTE.md`
- Create: `testesp/spike/papyrus/QF_SkythonTestQuest_01000800.psc`, `testesp/spike/papyrus/SkythonTestAlias.psc`, `testesp/spike/expected-dump.txt`, `testesp/spike/SkythonTest.esp` (committed test data; the spike's `.pex` go to `testesp/spike/compiled/`, which stays gitignored)
- Create: `livetest/test_m15_esp_spike.py`
- Modify: `.gitignore` (builder `bin/`, `obj/`), `docs/PINS.md` (Test ESP section), `spikes/RESULTS.md`

**Interfaces:**
- Consumes: `tools/papyrus_compile.sh` and `plugin/papyrus/compiled/Skython.pex` (Task 46, with the native live); `tools/box/box_deploy.sh --esp/--test-pex`, `tools/box/box_plugins.sh backup/add/show`, `box_quit.sh`'s restore (Task 45); `Game.on_channel`, `Event.data` (Task 36); `game.resolve`, `game.call_method`, `game.alias`, `game.get_property` (v1).
- Produces (Task 48 extends, never renames):
  - `testesp/builder/` console program: `SkythonTestEspBuilder spike <out_dir>` and `dump <file.esp>`; `Names` constants `PluginFile = "SkythonTest.esp"`, `QuestId = 0x000800`, `QuestEditorId = "SkythonTestQuest"`, `FragmentScript = "QF_SkythonTestQuest_01000800"`, `AliasScript = "SkythonTestAlias"`.
  - Build commands: `~/.dotnet/dotnet restore testesp/builder --source ~/.nuget/packages` then `~/.dotnet/dotnet build testesp/builder -c Release --no-restore` → `testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll`.
  - The recorded RESULTS facts Task 48 relies on: PASS/FAIL, the VMAD values above, how the stage was set.

- [ ] **Step 1: Preconditions**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
~/.dotnet/dotnet --list-sdks; ls ~/.nuget/packages/mutagen.bethesda.skyrim
test -x tools/papyrus_compile.sh && test -f plugin/papyrus/compiled/Skython.pex && echo "Task 46 outputs present"
grep -n 'Task 46, Skython.Send' spikes/RESULTS.md | head -1; grep -n 'Task 45, Gate 0' spikes/RESULTS.md | head -1
```
Expected: `8.0.424 [/home/geoff/.dotnet/sdk]`, `23.4.0`, `Task 46 outputs present`, and both RESULTS entries (Task 46's live PASS and Task 45's acceptance of decision 7). Without Task 46's live PASS the spike cannot tell a VMAD failure from a native failure: STOP. Steps 2-7 need no box.

- [ ] **Step 2: The builder project and its licence note**

Create `testesp/builder/SkythonTestEspBuilder.csproj`:
```xml
<Project Sdk="Microsoft.NET.Sdk">
  <!-- Development tool only, never distributed: links Mutagen (GPL-3.0-only). See LICENSE-NOTE.md. -->
  <!-- Mutagen.Bethesda.Skyrim 23.4.0 ships net5.0 / netstandard2.1 assemblies; they load and run under net8.0. -->
  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>disable</Nullable>
    <ImplicitUsings>disable</ImplicitUsings>
    <Deterministic>true</Deterministic>
    <RootNamespace>SkythonTestEsp</RootNamespace>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Mutagen.Bethesda.Skyrim" Version="23.4.0" />
  </ItemGroup>
</Project>
```
Create `testesp/builder/LICENSE-NOTE.md`:
```markdown
# testesp/builder: licence note

This folder is a development tool, not part of Skython.

- It links Mutagen (`Mutagen.Bethesda.Skyrim`, `Mutagen.Bethesda.Core`, `Mutagen.Bethesda.Kernel` 23.4.0; licence GPL-3.0-only; https://github.com/Noggog/Mutagen). A binary built from this folder is therefore a GPL-3.0-only work.
- It is never distributed: `bin/` and `obj/` are gitignored and no binary built from it is committed.
- No product code (`core/`, `plugin/`, `pyglue/`, `python/`) references this folder or Mutagen.
- Its output, `SkythonTest.esp`, is test data written from the record values in these sources; it is committed with its SHA-256 in `docs/PINS.md`.
- No code in this folder was copied from Mutagen, from Chronicle's patcher (AGPL, read only to see which package version works offline) or from any other project.
```
Append to `.gitignore`:
```gitignore
# testesp/builder is a GPL-3.0-only development tool (Mutagen): never commit its build output
testesp/builder/bin/
testesp/builder/obj/
```
```bash
DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 timeout 300 ~/.dotnet/dotnet restore testesp/builder --source ~/.nuget/packages
```
Expected: `Restored .../testesp/builder/SkythonTestEspBuilder.csproj`. An error naming a package that is not in `~/.nuget/packages` means the network would be needed: STOP.

- [ ] **Step 3: The expected dump (the offline test), and see it fail**

Create `testesp/spike/expected-dump.txt` (what Mutagen must read back from the spike ESP):
```text
plugin SkythonTest.esp masters=-
quest SkythonTestQuest 000800:SkythonTest.esp flags=AllowRepeatedStages form_version=0
  alias 0 SkythonTestAlias0 Reference flags=Optional, AllowDisabled, AllowReserved
  stage 10 log_entries=1
  vmad version=5 objformat=2 unknown=2 file=QF_SkythonTestQuest_01000800 scripts=QF_SkythonTestQuest_01000800
  fragment stage=10 index=0 unknown2=1 script=QF_SkythonTestQuest_01000800 function=Fragment_0
  alias-vmad alias=0 quest=000800:SkythonTest.esp scripts=SkythonTestAlias
```
```bash
DOTNET_CLI_TELEMETRY_OPTOUT=1 timeout 300 ~/.dotnet/dotnet build testesp/builder -c Release --no-restore -v q 2>&1 | grep -E 'error' | head -3
```
Expected: `error CS5001: Program does not contain a static 'Main' method suitable for an entry point` (no sources yet).

- [ ] **Step 4: The builder sources**

Create `testesp/builder/Names.cs`:
```csharp
namespace SkythonTestEsp
{
    // Names fixed by the plan's Contracts (Papyrus section). Changing one breaks the box allowlist and the tests.
    public static class Names
    {
        public const string PluginFile = "SkythonTest.esp";
        public const uint QuestId = 0x000800;                       // SkythonTest.esp|0x000800 in both builds
        public const string QuestEditorId = "SkythonTestQuest";
        public const string FragmentScript = "QF_SkythonTestQuest_01000800";
        public const string AliasScript = "SkythonTestAlias";
    }
}
```
Create `testesp/builder/SpikeEsp.cs`:
```csharp
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
```
Create `testesp/builder/Dump.cs`:
```csharp
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
```
Create `testesp/builder/Program.cs`:
```csharp
using System;
using System.IO;

namespace SkythonTestEsp
{
    // Usage: SkythonTestEspBuilder spike <out_dir>   -> <out_dir>/SkythonTest.esp (Task 47: the VMAD spike)
    //        SkythonTestEspBuilder dump <file.esp>    -> a text summary of the plugin (offline tests)
    // Mutagen refuses to write a mod under a file name other than its ModKey, so the output argument is a folder.
    public static class Program
    {
        public static int Main(string[] args)
        {
            if (args.Length == 2 && args[0] == "dump") { Dump.Print(args[1]); return 0; }
            if (args.Length != 2 || args[0] != "spike")
            {
                Console.Error.WriteLine("usage: SkythonTestEspBuilder spike <out_dir> | dump <file.esp>");
                return 2;
            }
            Directory.CreateDirectory(args[1]);
            var path = Path.Combine(args[1], Names.PluginFile);
            SpikeEsp.Write(path);
            Console.WriteLine($"wrote {path}");
            return 0;
        }
    }
}
```

- [ ] **Step 5: Build the spike ESP twice and compare with the expected dump**

```bash
B=testesp/builder/bin/Release/net8.0/SkythonTestEspBuilder.dll
DOTNET_CLI_TELEMETRY_OPTOUT=1 timeout 300 ~/.dotnet/dotnet build testesp/builder -c Release --no-restore -v q 2>&1 | grep -E 'error|warn' ; echo build=$?
timeout 120 ~/.dotnet/dotnet $B spike testesp/spike
mkdir -p out/t47-rebuild && timeout 120 ~/.dotnet/dotnet $B spike out/t47-rebuild
sha256sum testesp/spike/SkythonTest.esp out/t47-rebuild/SkythonTest.esp
timeout 120 ~/.dotnet/dotnet $B dump testesp/spike/SkythonTest.esp | diff - testesp/spike/expected-dump.txt && echo "dump matches"
timeout 60 ~/.dotnet/dotnet $B bogus x; echo rc=$?
```
Expected: no errors or warnings; `wrote testesp/spike/SkythonTest.esp`; both hashes `00a11e2e3481e87d02f38d8ff23d0b8a42ce9df6fbd6bde0aca66c288479cbce`; `dump matches`; the usage line and `rc=2`. A different hash with these exact sources is a STOP (the build is supposed to be deterministic).

- [ ] **Step 6: The spike's Papyrus scripts**

Create `testesp/spike/papyrus/QF_SkythonTestQuest_01000800.psc`:
```papyrus
;BEGIN FRAGMENT CODE - Do not edit anything between this and the end comment
;NEXT FRAGMENT INDEX 1
Scriptname QF_SkythonTestQuest_01000800 Extends Quest Hidden
{Task 47 spike: the stage 10 fragment of SkythonTestQuest. testesp/builder binds stage 10 -> Fragment_0 in the VMAD.}

;BEGIN FRAGMENT Fragment_0
Function Fragment_0()
;BEGIN CODE
Skython.Send("skython_test", "spike_stage_10")
;END CODE
EndFunction
;END FRAGMENT

;END FRAGMENT CODE - Do not edit anything between this and the begin comment
```
Create `testesp/spike/papyrus/SkythonTestAlias.psc`:
```papyrus
Scriptname SkythonTestAlias extends ReferenceAlias
{Task 47 spike: the script on alias 0 of SkythonTestQuest. Task 48's full version replaces it.}

Int Property Tag Auto
```
```bash
tools/papyrus_compile.sh testesp/spike/papyrus/QF_SkythonTestQuest_01000800.psc testesp/spike/papyrus/SkythonTestAlias.psc -o testesp/spike/compiled | grep -E '^compiled|FAILED'
tools/papyrus_compile.sh --stable-hash testesp/spike/compiled/*.pex
```
Expected: `compiled ... QF_SkythonTestQuest_01000800.pex (699 bytes)`, `compiled ... SkythonTestAlias.pex (595 bytes)`; stable hashes `ed09e1822e1908d18f8ebd032e9a7e5e3993bca9f7fece03673e13b24c84ea38` and `72e7bf65995c31b00a934efda8adc1ddbea7fab26df2a2eda1fa4b6ea26bccd7` (the fragment compiles because `Skython.psc` is on the import path).

- [ ] **Step 7: Live test file, pins, commit**

Create `livetest/test_m15_esp_spike.py`:
```python
"""Task 47 (the ESP/VMAD gate): a quest written by Mutagen, with a scripted alias and a QF_ stage fragment, binds in the
game. Needs testesp/spike/SkythonTest.esp in Data with its plugins.txt line (tools/box/box_plugins.sh add), Skython.pex
and the spike's two .pex in Data\\Scripts (owner decision 7), and s4test loaded. Never saves."""
import queue
import subprocess
import time

import pytest

from support import REPO

QUEST_KEY = "SkythonTest.esp|0x000800"
FRAGMENT_SCRIPT = "QF_SkythonTestQuest_01000800"
PLAYER_ID = 0x14


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


@pytest.fixture
def quest(game):
    return game.resolve(form_key=QUEST_KEY)


@pytest.fixture
def channel(game):
    q = queue.Queue()
    sub = game.on_channel("skython_test", q.put)
    yield q
    sub.close()


def wait_payload(q, payload, timeout=15.0):
    deadline, seen = time.monotonic() + timeout, []
    while (left := deadline - time.monotonic()) > 0:
        try:
            ev = q.get(timeout=left)
        except queue.Empty:
            break
        seen.append(ev.data.get("payload"))
        if ev.data.get("payload") == payload:
            return ev
    pytest.fail(f"no channel event with payload {payload!r} within {timeout} s (saw {seen})")


def test_plugin_loaded_and_quest_resolves(game, quest):
    assert quest.type == "Quest"
    assert quest.form_key.lower() == QUEST_KEY.lower()
    assert game.call("Game", "IsPluginInstalled", "SkythonTest.esp") is True


def test_console_setstage_runs_the_fragment(channel):
    r = subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "setstage SkythonTestQuest 10"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    wait_payload(channel, "spike_stage_10")


def test_call_method_setstage_runs_the_fragment(game, quest, channel):
    assert game.call_method(quest, "Quest", "SetStage", 10) is True   # AllowRepeatedStages: runs again
    wait_payload(channel, "spike_stage_10")
    assert game.call_method(quest, "Quest", "GetStage") == 10


def test_fragment_script_is_attached_under_its_name(game, quest):
    # S7c route: dispatch by the attached script's own class name works only if the VMAD attached it to the quest
    game.call_method(quest, "Quest", "SetStage", 10)
    assert game.call_method(quest, FRAGMENT_SCRIPT, "GetStage") == 10


def test_alias_script_is_bound(game, quest):
    if not game.call_method(quest, "Quest", "IsRunning"):
        assert game.call_method(quest, "Quest", "Start") is True
    alias = game.alias(quest, 0)
    player = game.resolve(form_id=PLAYER_ID)
    game.call_method(alias, "ReferenceAlias", "ForceRefTo", player)
    try:
        # Gate criteria: dispatch by the alias script's own class name (S7c route) and ReferenceAlias calls, which an
        # unscripted alias answers with type_mismatch (R45). The Tag read is extra: alias property reads are Task 51's.
        assert game.call_method(alias, "SkythonTestAlias", "GetOwningQuest").form_id == quest.form_id
        assert game.call_method(alias, "ReferenceAlias", "GetReference").form_id == PLAYER_ID
        assert game.get_property(alias, "SkythonTestAlias", "Tag") == 0
    finally:
        game.call_method(alias, "ReferenceAlias", "Clear")
```
Append to `docs/PINS.md`:
```markdown
## Test ESP (Tasks 47-48)
- Builder: `testesp/builder` (development tool, GPL-3.0-only through Mutagen, never distributed; see its LICENSE-NOTE.md), .NET SDK 8.0.424 at `~/.dotnet/dotnet`, target `net8.0`, restored offline from `~/.nuget/packages`.
- Mutagen 23.4.0 nupkg sha256: Skyrim 4bdec615ae07c8ac9066f62d8a6420f0355e76c94db69e730a46bfc296856696, Core fa50598700a836dfef6639fbc1893aaf276d3f9383d51437cc8d6b8f6c254313, Kernel 07f9fbd65c846f2071a3c752671c5860409766715bab0ed9450796331a774b55.
- `testesp/spike/SkythonTest.esp` (Task 47 spike) sha256 00a11e2e3481e87d02f38d8ff23d0b8a42ce9df6fbd6bde0aca66c288479cbce (deterministic: rebuilds are byte-identical).
```
```bash
uv run --project python pytest livetest/test_m15_esp_spike.py -q   # skipped without SKYTHON_LIVE
git add .gitignore docs/PINS.md testesp/builder/SkythonTestEspBuilder.csproj testesp/builder/Program.cs testesp/builder/Names.cs \
        testesp/builder/SpikeEsp.cs testesp/builder/Dump.cs testesp/builder/LICENSE-NOTE.md testesp/spike/papyrus \
        testesp/spike/expected-dump.txt testesp/spike/SkythonTest.esp livetest/test_m15_esp_spike.py
git status --short testesp/ | grep -E 'bin/|obj/|\.pex' && echo "STOP: build output staged" || echo "no build output staged"
git commit -m "feat(testesp): Mutagen builder and the VMAD spike plugin"
```
Expected: `5 skipped`; `no build output staged`.

- [ ] **Step 8: Gate 0 check, deploy**

Gate 0 must be open (Step 1; README **Owner decisions**, decision 7: without a recorded acceptance, STOP). The spike plugin already has the decision-7 file name and uses only names on Task 45's six-name `.pex` list (`Skython.pex`, `QF_SkythonTestQuest_01000800.pex`, `SkythonTestAlias.pex`), so the list does not grow; Task 48's build later replaces these same files (they are recorded in `out/box-deployed.txt`, so `put_file` overwrites only our own copies).
```bash
tools/box/box_deploy.sh --esp testesp/spike/SkythonTest.esp
tools/box/box_deploy.sh --test-pex plugin/papyrus/compiled/Skython.pex testesp/spike/compiled/QF_SkythonTestQuest_01000800.pex testesp/spike/compiled/SkythonTestAlias.pex
tools/box/box_plugins.sh backup
tools/box/box_plugins.sh add
```
Expected: `deployed testesp/spike/SkythonTest.esp -> C:\ChronicleDev\Stock Game\Data\SkythonTest.esp`, three `deployed ... -> ...\Data\Scripts\<name>` lines, `BACKUP_SAME <sha>`, `ADDED <sha>`.

- [ ] **Step 9: Live run on `s4test`**

```bash
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "native: Skython.Send registered" 600
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_esp_spike.py -v -rs
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_send.py -q -rs
tools/box/box_files.sh pull Skython.log >/dev/null; grep -E '\[error\]|\[warning\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken | head
tools/box/box_quit.sh || tools/box/box_stop.sh
tools/box/box_plugins.sh show | head -2
tools/box/box_session.sh down
```
Expected: `test_m15_esp_spike.py` 5 passed (`test_plugin_loaded_and_quest_resolves`, `test_console_setstage_runs_the_fragment`, `test_call_method_setstage_runs_the_fragment`, `test_fragment_script_is_attached_under_its_name`, `test_alias_script_is_bound`); `test_m15_send.py` 5 passed; no unexpected error lines; the quit tool ends with `RESTORED <sha A>` (the backup is back) and `show` prints `state: BACKUP`. s4test is not saved (the quest's state dies with the session).

Gate criteria are the event arriving through either `SetStage` path, the `QF_...GetStage` dispatch and the first two alias assertions (`SkythonTestAlias.GetOwningQuest`, `ReferenceAlias.GetReference`). If only the `Tag` read fails, the VMAD bound: record PASS with that failure noted for Task 51 instead of a FAIL. If a gate criterion fails: keep the game running, collect `tools/box/box_shot.sh`, `tools/box/box_files.sh pull Skython.log Skython/python.log`, and `tools/box/box_console.sh "sqv SkythonTestQuest"` (the console's quest-variable dump: shows whether `QF_SkythonTestQuest_01000800` and `SkythonTestAlias` are attached), quit, record FAIL with that evidence, and STOP.

- [ ] **Step 10: Record and commit**

Append to `spikes/RESULTS.md` (PASS or FAIL, never both):
```markdown
- **Task 47, ESP/VMAD spike: <PASS|FAIL> (box, Windows-native, <date time>, Skyrim 1.6.1170.0, s4test, never saved; 1 launch; SKSE plugins loaded: <list>):** Mutagen 23.4.0 (net8.0, offline restore) wrote `testesp/spike/SkythonTest.esp` (sha256 00a11e2e3481e87d02f38d8ff23d0b8a42ce9df6fbd6bde0aca66c288479cbce, no masters): quest `SkythonTestQuest` SkythonTest.esp|0x000800 (AllowRepeatedStages, QuestFormVersion 0), alias 0 `SkythonTestAlias0` (Optional) with VMAD script `SkythonTestAlias`, stage 10 with one log entry (QSDT), VMAD version 5 / object format 2 / Unknown 2, `FileName` and `Scripts` = `QF_SkythonTestQuest_01000800`, fragment `stage=10 index=0 unknown2=1 Fragment_0`, alias VMAD property name empty (VMAD specifics Task 48 keeps: quest adapter Unknown 2, fragments Unknown2 1, one log entry per stage for `StageIndex 0`, form version 0 where Mutagen's default is 255). Results: plugin loaded (`IsPluginInstalled` True, `form.resolve` -> Quest); console `setstage SkythonTestQuest 10` -> `channel` `spike_stage_10` in <n> ms; `call_method(Quest.SetStage, 10)` -> True and `spike_stage_10` again; dispatch by class name `QF_SkythonTestQuest_01000800.GetStage` -> 10 (the game reports the fragment script under that name); alias 0: `ForceRefTo(player)`, `GetReference` -> player, `SkythonTestAlias.Tag` -> 0, `GetOwningQuest` -> the quest, `Clear`. plugins.txt restored to sha <A> by box_quit.sh. <On FAIL: which test, the evidence, and "STOP: owner to choose Creation Kit or xEdit (spec open question 4)".> **Owner removal list:** `Data\SkythonTest.esp`, `Data\Scripts\QF_SkythonTestQuest_01000800.pex`, `Data\Scripts\SkythonTestAlias.pex` (Task 48 overwrites all three).
```
```bash
git add spikes/RESULTS.md
git commit -m "docs(results): Task 47 ESP/VMAD spike result"
```
On FAIL: STOP after this commit and ask the owner; Tasks 48-53 and 55 are re-planned.
