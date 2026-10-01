#include "doctest/doctest.h"
#include "spy/values.h"
#include <cfloat>
#include <limits>

using spy::json;

static spy::ErrorCode code_of(const json& j)
{
    try {
        spy::value_from_json(j);
    } catch (const spy::ProtocolError& e) {
        return e.code;
    }
    FAIL("expected ProtocolError");
    return spy::ErrorCode::internal;
}

static std::string message_of(const json& j)
{
    try {
        spy::value_from_json(j);
    } catch (const spy::ProtocolError& e) {
        return e.what();
    }
    FAIL("expected ProtocolError");
    return {};
}

static std::string roundtrip_text(const json& wire)
{
    return spy::dump_json(spy::value_to_json(spy::value_from_json(wire)));
}

TEST_CASE("scalars decode to the right kinds")
{
    CHECK(spy::value_kind(spy::value_from_json(nullptr)) == "null");
    CHECK(spy::value_kind(spy::value_from_json(true)) == "bool");
    CHECK(spy::value_kind(spy::value_from_json(7)) == "int");
    CHECK(spy::value_kind(spy::value_from_json(1.5)) == "float");
    CHECK(spy::value_kind(spy::value_from_json("x")) == "string");
}

TEST_CASE("Int range is enforced, never truncated")
{
    CHECK(std::get<std::int32_t>(spy::value_from_json(2147483647).v) == 2147483647);
    CHECK(std::get<std::int32_t>(spy::value_from_json(-2147483648LL).v) == -2147483647 - 1);
    CHECK(code_of(json(2147483648LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(-2147483649LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(4294967296ULL)) == spy::ErrorCode::type_mismatch);
}

TEST_CASE("Float: out of 32-bit range is type_mismatch, the largest float survives a round trip")
{
    CHECK(code_of(json(3.5e38)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(-3.5e38)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(1e300)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", "Float"}, {"items", {1.5, 3.5e38}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", "Float"}, {"items", {1e39}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(std::numeric_limits<double>::infinity())) == spy::ErrorCode::type_mismatch);
    // The shortest text of FLT_MAX is 3.4028235e38, which is slightly above FLT_MAX as a double; it must be accepted back.
    const auto max_text = spy::dump_json(spy::value_to_json(spy::Value{FLT_MAX}));
    CHECK(std::get<float>(spy::value_from_json(json::parse(max_text)).v) == FLT_MAX);
    CHECK(std::get<float>(spy::value_from_json(json::parse("-" + max_text)).v) == -FLT_MAX);
    CHECK(std::get<float>(spy::value_from_json(1e-50).v) == 0.0f);  // underflow is not an error
}

TEST_CASE("Float is written in shortest round-trip text and non-finite floats become null")
{
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{0.1f})) == "0.1");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{1.0f})) == "1.0");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{16777216.0f})) == "16777216.0");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{-2.5f})) == "-2.5");
    CHECK(spy::value_to_json(spy::Value{std::numeric_limits<float>::infinity()}).is_null());
    CHECK(spy::value_to_json(spy::Value{std::numeric_limits<float>::quiet_NaN()}).is_null());
    CHECK(roundtrip_text(json(0.1)) == "0.1");
    CHECK(roundtrip_text(json::parse(R"({"array":"Float","items":[1,2.5]})")) == R"({"array":"Float","items":[1.0,2.5]})");
    CHECK(spy::value_to_json(spy::Value{std::int32_t{5}}).is_number_integer());
}

TEST_CASE("form references need an id or a key")
{
    auto v = spy::value_from_json(json{{"kind", "form"}, {"form_id", 20}});
    CHECK(std::get<spy::FormRef>(v.v).form_id.value() == 20u);
    CHECK(code_of(json{{"kind", "form"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", "form"}, {"form_id", -1}}) == spy::ErrorCode::bad_request);
    CHECK(std::get<spy::FormRef>(spy::value_from_json(json::parse(R"({"kind":"form","form_id":20})")).v).form_id.value() == 20u);
}

TEST_CASE("wrong JSON types in optional form fields are bad_request, never ignored and never a json exception")
{
    const json base = {{"kind", "form"}, {"form_id", 20}};
    auto with = [&](const char* key, json v) { auto j = base; j[key] = std::move(v); return j; };
    CHECK(code_of(with("type", 5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", "1")) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", -1)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", 4294967296ULL)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("fingerprint", "x")) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("fingerprint", json{{"base", "a"}})) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("form_key", 5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("form_id", "20")) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", 5}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", json::array()}, {"form_id", 1}}) == spy::ErrorCode::bad_request);
    // null means absent
    auto ok = spy::value_from_json(with("type", nullptr));
    CHECK(std::get<spy::FormRef>(ok.v).type.empty());
}

TEST_CASE("arrays are typed and bare JSON arrays are rejected")
{
    auto v = spy::value_from_json(json{{"array", "Float"}, {"items", {1, 2.5}}});
    auto& a = std::get<spy::ArrayValue>(v.v);
    CHECK(a.elem_type == "Float");
    CHECK(std::get<float>(a.items[0].v) == doctest::Approx(1.0));
    CHECK(code_of(json::array({1, 2})) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"array", "Int"}, {"items", {1, "x"}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", 5}, {"items", json::array()}}) == spy::ErrorCode::bad_request);
}

TEST_CASE("alias types are restricted")
{
    json bad = {{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", 0}, {"alias_type", "MagicAlias"}};
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
    bad["alias_type"] = 5;
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
}

TEST_CASE("alias_id is range-checked, not truncated")
{
    auto alias = [](json id) {
        return json{{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", std::move(id)}, {"alias_type", "ReferenceAlias"}};
    };
    CHECK(std::get<spy::AliasRef>(spy::value_from_json(alias(0)).v).alias_id == 0);
    CHECK(std::get<spy::AliasRef>(spy::value_from_json(alias(2147483647)).v).alias_id == 2147483647);
    CHECK(code_of(alias(-1)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias(2147483648LL)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias(1099511627777LL)) == spy::ErrorCode::bad_request);  // would truncate to 1
    CHECK(code_of(alias(1.5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias("0")) == spy::ErrorCode::bad_request);
}

TEST_CASE("str_or returns the default for absent or null and throws bad_request for non-strings")
{
    CHECK(spy::str_or(json{{"a", "x"}}, "a", "d") == "x");
    CHECK(spy::str_or(json::object(), "a", "d") == "d");
    CHECK(spy::str_or(json{{"a", nullptr}}, "a", "d") == "d");
    CHECK(spy::str_or(json::array(), "a", "d") == "d");  // not an object: no exception from nlohmann
    CHECK_THROWS_AS(spy::str_or(json{{"a", 5}}, "a", "d"), spy::ProtocolError);
    CHECK_THROWS_AS(spy::str_or(json{{"a", json::object()}}, "a", "d"), spy::ProtocolError);
}

TEST_CASE("invalid UTF-8 is replaced on output instead of throwing")
{
    json j = {{"s", std::string("caf\xE9 \xFF")}};  // Windows-1252 bytes
    CHECK_THROWS_AS(j.dump(), json::type_error);   // what a bare dump() does
    std::string out;
    CHECK_NOTHROW(out = spy::dump_json(j));
    CHECK(out == "{\"s\":\"caf\xEF\xBF\xBD \xEF\xBF\xBD\"}");
    CHECK(spy::dump_json(json{{"a", 1}}, 2) == "{\n  \"a\": 1\n}");
    CHECK_NOTHROW(spy::dump_json(spy::value_to_json(spy::Value{std::string("\xE9")})));
}

TEST_CASE("clip_text bounds quoted input and never splits a UTF-8 sequence")
{
    CHECK(spy::clip_text("short") == "short");
    CHECK(spy::clip_text(std::string(500, 'x'), 10) == "xxxxxxxxxx...");
    const std::string accents = "\xC3\xA9\xC3\xA9\xC3\xA9";  // three 2-byte characters
    CHECK(spy::clip_text(accents, 3) == "\xC3\xA9...");       // byte 3 would split the second character
    CHECK(spy::clip_text(std::string(2000000, 'x')).size() == 123);
}

TEST_CASE("clip_text backs up at most 3 bytes and only over a real UTF-8 sequence")
{
    // Game strings are often Windows-1252, whose punctuation (0x92 is a right single quote) looks like UTF-8
    // continuation bytes. They must not make the clip eat the whole message.
    const std::string cp1252 = "Name: " + std::string(300, '\x92');
    CHECK(spy::clip_text(cp1252, 120) == cp1252.substr(0, 120) + "...");
    const std::string emoji = "x\xF0\x9F\x98\x80y";             // a 4-byte character between two letters
    CHECK(spy::clip_text(emoji, 2) == "x...");                    // the cut is inside it: back up to its lead byte
    CHECK(spy::clip_text(emoji, 4) == "x...");                    // three bytes back is still inside it
    CHECK(spy::clip_text(emoji, 5) == "x\xF0\x9F\x98\x80...");  // the cut falls after the whole character
    CHECK(spy::clip_text("\xC3\xA9\x92\x92", 2) == "\xC3\xA9...");   // a stray continuation byte after a whole character
}

TEST_CASE("error messages never echo a large input")
{
    const std::string big(3 * 1024 * 1024, 'x');
    CHECK(message_of(json{{"array", "Int"}, {"items", {big}}}).size() < 300);
    CHECK(message_of(json{{"array", "Int"}, {"items", {json::array({1, 2, 3})}}}).size() < 300);
    CHECK(message_of(json{{"kind", big}}).size() < 300);
    CHECK(spy::describe(json(big)).size() < 100);
    CHECK(spy::describe(json::array({1, 2})) == "an array of 2 items");
    CHECK(spy::describe(json::object()) == "an object");
    CHECK(spy::describe(json(42)) == "42");
}

TEST_CASE("every error code in the generated table round trips through its string")
{
    for (const auto& e : spy::kErrorCodeNames) {
        CHECK(spy::to_string(e.code) == e.name);
        CHECK(spy::error_code_from_string(e.name).value() == e.code);
    }
    CHECK_FALSE(spy::error_code_from_string("nope").has_value());
    CHECK_FALSE(spy::error_code_from_string("").has_value());
    CHECK(spy::to_string(static_cast<spy::ErrorCode>(9999)) == "internal");
}

TEST_CASE("an array holds at most kMaxArrayItems items, in both directions")
{
    json items = json::array();
    for (std::size_t i = 0; i < spy::kMaxArrayItems; ++i) items.push_back(static_cast<int>(i));
    CHECK(std::get<spy::ArrayValue>(spy::value_from_json(json{{"array", "Int"}, {"items", items}}).v).items.size() == spy::kMaxArrayItems);
    items.push_back(0);
    CHECK(code_of(json{{"array", "Int"}, {"items", items}}) == spy::ErrorCode::type_mismatch);
    CHECK(message_of(json{{"array", "Int"}, {"items", items}}).find("at most") != std::string::npos);

    spy::ArrayValue out{"Int", {}};
    for (std::size_t i = 0; i < spy::kMaxArrayItems; ++i) out.items.push_back(spy::Value{std::int32_t{1}});
    CHECK(spy::value_to_json(spy::Value{out})["items"].size() == spy::kMaxArrayItems);
    out.items.push_back(spy::Value{std::int32_t{1}});
    try {
        spy::value_to_json(spy::Value{out});
        FAIL("expected ProtocolError");
    } catch (const spy::ProtocolError& e) {
        CHECK(e.code == spy::ErrorCode::type_mismatch);
    }
}
