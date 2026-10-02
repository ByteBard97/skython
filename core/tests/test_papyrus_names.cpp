#include "doctest/doctest.h"
#include "spy/errors.h"
#include "spy/papyrus_names.h"
#include "spy/refs.h"
#include <clocale>
#include <cctype>
#include <string>

TEST_CASE("engine class names get their .psc spelling in any casing")
{
    CHECK(spy::canonical_script_name("WEAPON") == "Weapon");  // seen on the box (Task 28)
    CHECK(spy::canonical_script_name("SPELL") == "Spell");    // seen on the box (Task 28)
    CHECK(spy::canonical_script_name("utility") == "Utility"); // seen on the box (S7a)
    CHECK(spy::canonical_script_name("weapon") == "Weapon");
    CHECK(spy::canonical_script_name("Weapon") == "Weapon");
    CHECK(spy::canonical_script_name("actor") == "Actor");
    CHECK(spy::canonical_script_name("OBJECTREFERENCE") == "ObjectReference");
    CHECK(spy::canonical_script_name("referencealias") == "ReferenceAlias");
    CHECK(spy::canonical_script_name("locationALIAS") == "LocationAlias");
    CHECK(spy::canonical_script_name("activemagiceffect") == "ActiveMagicEffect");
    CHECK(spy::canonical_script_name("form") == "Form");
    CHECK(spy::canonical_script_name("wordofpower") == "WordOfPower");
    CHECK(spy::canonical_script_name("talkingactivator") == "TalkingActivator");
    CHECK(spy::canonical_script_name("armoraddon") == "ArmorAddon");  // SKSE
    CHECK(spy::canonical_script_name("stringutil") == "StringUtil");  // SKSE
    CHECK(spy::canonical_script_name("skse") == "SKSE");              // SKSE
    CHECK(spy::canonical_script_name("ui") == "UI");                  // SKSE
    // Entries at the ends of the table's source lines (a missing comma there would join two names).
    for (const char* n : {"Apparatus", "Debug", "Form", "ImpactDataSet", "Location", "Outfit", "Scene", "Static",
             "Weapon", "WorldSpace", "DefaultObjectManager", "SKSE", "WornObject"})
    {
        std::string low(n);
        for (auto& c : low) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        CHECK(spy::canonical_script_name(low) == n);  // lowercase in: an identity call would pass through untested
    }
}

TEST_CASE("unknown script names pass through unchanged")
{
    CHECK(spy::canonical_script_name("QF_TGTQ01Handler_001093E4") == "QF_TGTQ01Handler_001093E4");
    CHECK(spy::canonical_script_name("qf_tgtq01handler_001093e4") == "qf_tgtq01handler_001093e4");
    CHECK(spy::canonical_script_name("livetest_smoke") == "livetest_smoke");
    CHECK(spy::canonical_script_name("Weapon[]") == "Weapon[]");  // only bare class names are in the table
    CHECK(spy::canonical_script_name("weapons") == "weapons");
    CHECK(spy::canonical_script_name(" weapon") == " weapon");
}

TEST_CASE("an empty name stays empty")
{
    CHECK(spy::canonical_script_name("") == "");
}

TEST_CASE("name folding is ASCII-only and does not depend on the C locale")
{
    CHECK(spy::ascii_lower('I') == 'i');
    CHECK(spy::ascii_lower('Z') == 'z');
    CHECK(spy::ascii_lower('i') == 'i');
    CHECK(spy::ascii_lower('_') == '_');
    CHECK(spy::ascii_lower('\xC9') == '\xC9');  // a Latin-1 locale's tolower would make this 0xE9
    // Under whichever non-C locale this machine has (a Turkish one maps 'I' away from 'i'), lookups still fold ASCII.
    const std::string before = std::setlocale(LC_ALL, nullptr);
    for (const char* name : {"tr_TR.ISO-8859-9", "tr_TR.UTF-8", "tr_TR.utf8", "de_DE.ISO-8859-1", "en_US.UTF-8", "C.UTF-8"})
        if (std::setlocale(LC_ALL, name) != nullptr) break;
    CHECK(spy::canonical_script_name("IMAGESPACEMODIFIER") == "ImageSpaceModifier");
    CHECK(spy::canonical_script_name("ObjectReferencE") == "ObjectReference");
    const auto key = spy::parse_form_key("SKYRIM.ESM|0XABCDEF");
    CHECK((key && key->local_id == 0xABCDEFu));
    CHECK(spy::parse_form_key("LIGHT.ESL|0XFFF"));
    std::setlocale(LC_ALL, before.c_str());  // only CHECKs above: nothing aborts the case before this
}
