#include "doctest/doctest.h"
#include "spy/refs.h"

TEST_CASE("form keys parse and format canonically")
{
    auto k = spy::parse_form_key("Skyrim.esm|0x14");
    REQUIRE(k);
    CHECK(k->plugin == "Skyrim.esm");
    CHECK(k->local_id == 0x14u);
    CHECK(spy::format_form_key(*k) == "Skyrim.esm|0x000014");
    CHECK(spy::parse_form_key("Light Mod.esl|0xabc")->local_id == 0xABCu);
    CHECK(spy::parse_form_key("SKYRIM.ESM|0X14")->local_id == 0x14u);
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1000000"));
    CHECK_FALSE(spy::parse_form_key("notes.txt|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm"));
    CHECK_FALSE(spy::parse_form_key("|0x1"));
}

TEST_CASE("form key plugin names are checked, not just suffix-matched")
{
    CHECK_FALSE(spy::parse_form_key(".esm|0x1"));                  // empty stem
    CHECK_FALSE(spy::parse_form_key(" Skyrim.esm|0x1"));           // leading space
    CHECK_FALSE(spy::parse_form_key("\tSkyrim.esm|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim .esm|0x1"));           // trailing space in the stem
    CHECK_FALSE(spy::parse_form_key("Sky\nrim.esm|0x1"));          // control character
    CHECK_FALSE(spy::parse_form_key(std::string("Sky\0rim.esm|0x1", 15)));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm\n|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1\n"));          // trailing newline
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x123456789"));    // nine digits
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x0x1"));
    CHECK(spy::parse_form_key("Light Mod.esl|0xFFF"));             // inner spaces are fine
    CHECK(spy::parse_form_key("a.esp|0x1"));
}

TEST_CASE("light plugins have 12-bit local ids")
{
    CHECK(spy::parse_form_key("L.esl|0xFFF")->local_id == 0xFFFu);
    CHECK_FALSE(spy::parse_form_key("L.esl|0x1000"));
    CHECK_FALSE(spy::parse_form_key("L.ESL|0xFFFFFF"));
    CHECK(spy::parse_form_key("L.esp|0xFFFFFF")->local_id == 0xFFFFFFu);  // a full plugin keeps 24 bits
}

TEST_CASE("form id ranges")
{
    CHECK(spy::is_runtime_form_id(0xFF000002u));
    CHECK_FALSE(spy::is_runtime_form_id(0x00000014u));
    CHECK(spy::is_light_form_id(0xFE001800u));
    CHECK_FALSE(spy::is_light_form_id(0xFF000002u));
    CHECK(spy::local_form_id(0x0500'0800u) == 0x800u);
    CHECK(spy::local_form_id(0xFE001ABCu) == 0xABCu);  // 0xFExxxyyy: xxx is the light slot, yyy the local id
}

TEST_CASE("form_key_matches_id compares local ids and refuses runtime ids")
{
    CHECK(spy::form_key_matches_id("Skyrim.esm|0x000014", 0x00000014u));
    CHECK(spy::form_key_matches_id("Mod.esp|0x000800", 0x05000800u));  // the load-order byte is not compared
    CHECK_FALSE(spy::form_key_matches_id("Skyrim.esm|0x000015", 0x00000014u));
    CHECK(spy::form_key_matches_id("Light.esl|0x000ABC", 0xFE001ABCu));
    CHECK_FALSE(spy::form_key_matches_id("Light.esl|0x000123", 0xFE001ABCu));
    CHECK_FALSE(spy::form_key_matches_id("Skyrim.esm|0x000002", 0xFF000002u));  // runtime forms have no form key
    CHECK_FALSE(spy::form_key_matches_id("nonsense", 0x14u));
}

TEST_CASE("value_from_json rejects a form_id and form_key that disagree")
{
    auto code = [](spy::json j) {
        try {
            spy::value_from_json(j);
        } catch (const spy::ProtocolError& e) {
            return e.code;
        }
        return spy::ErrorCode::internal;
    };
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_id", 0x05000800}, {"form_key", "Mod.esp|0x000800"}}));
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_id", 0xFE001ABCu}, {"form_key", "Light.esl|0x000ABC"}}));
    CHECK(code({{"kind", "form"}, {"form_id", 20}, {"form_key", "Skyrim.esm|0x000015"}}) == spy::ErrorCode::bad_request);
    CHECK(code({{"kind", "form"}, {"form_id", 0xFF000002u}, {"form_key", "Skyrim.esm|0x000002"}}) == spy::ErrorCode::bad_request);
    CHECK(code({{"kind", "form"}, {"form_key", "Light.esl|0x1000"}}) == spy::ErrorCode::bad_request);
    // either alone is fine
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_key", "Skyrim.esm|0x000015"}}));
}

TEST_CASE("fingerprint rule only applies to runtime references that carry one")
{
    spy::Fingerprint live{"Skyrim.esm|0x012EB7", "ObjectReference"};
    spy::FormRef plugin{0x00012EB7u, "Skyrim.esm|0x012EB7", "Weapon", 1, std::nullopt};
    CHECK(spy::check_fingerprint(plugin, live) == spy::RefStatus::ok);

    spy::FormRef runtime{0xFF000002u, std::nullopt, "ObjectReference", 1, live};
    CHECK(spy::check_fingerprint(runtime, live) == spy::RefStatus::ok);

    spy::FormRef other = runtime;
    other.fingerprint = spy::Fingerprint{"Skyrim.esm|0x000F", "ObjectReference"};
    CHECK(spy::check_fingerprint(other, live) == spy::RefStatus::stale);

    spy::FormRef unchecked{0xFF000002u, std::nullopt, "", std::nullopt, std::nullopt};
    CHECK(spy::check_fingerprint(unchecked, live) == spy::RefStatus::ok);
}
