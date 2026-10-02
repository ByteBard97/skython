using System;
using System.IO;

namespace SkythonTestEsp
{
    // Usage: SkythonTestEspBuilder spike <out_dir>   -> <out_dir>/SkythonTest.esp (Task 47: the VMAD spike)
    //        SkythonTestEspBuilder full <out_dir>    -> <out_dir>/SkythonTest.esp (Task 48: the test plugin; reads
    //                                                  Skyrim.esm, path in SKYTHON_SKYRIM_ESM or TestEsp.DefaultSkyrimEsm)
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
            if (args[0] == "spike") SpikeEsp.Write(path);
            else TestEsp.Write(path, Environment.GetEnvironmentVariable("SKYTHON_SKYRIM_ESM") ?? TestEsp.DefaultSkyrimEsm);
            Console.WriteLine($"wrote {path}");
            return 0;
        }
    }
}
