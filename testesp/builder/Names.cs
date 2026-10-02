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
