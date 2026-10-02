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
