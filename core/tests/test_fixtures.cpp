#include "doctest/doctest.h"
#include "spy/requests.h"
#include "spy/refs.h"
#include "spy/values.h"
#include <fstream>

using spy::json;

static json load(const char* name)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/" + name);
    REQUIRE(f.good());
    return json::parse(f);
}

// Any exception other than ProtocolError (for example a json::type_error) escapes this helper and fails the test.
static std::string error_of(auto&& fn)
{
    try {
        fn();
    } catch (const spy::ProtocolError& e) {
        return std::string(spy::to_string(e.code));
    }
    return "none";
}

// json operator== treats 1 and 1.0 as equal; serialised text does not.
static bool same_text(const json& a, const json& b)
{
    return spy::dump_json(a) == spy::dump_json(b);
}

TEST_CASE("values fixtures")
{
    auto fx = load("values.json");
    for (const auto& c : fx["valid"]) {
        CAPTURE(c.dump());
        auto v = spy::value_from_json(c["wire"]);
        CHECK(spy::value_kind(v) == c["kind"].get<std::string>());
        const json& expected = c.contains("canonical") ? c["canonical"] : c["wire"];
        CHECK(spy::dump_json(spy::value_to_json(v)) == spy::dump_json(expected));
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::value_from_json(c["wire"]); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("the fixture comparison can tell 1 from 1.0")
{
    CHECK(json(1) == json(1.0));                       // what json == says
    CHECK_FALSE(same_text(json(1), json(1.0)));        // what the sweep uses
    CHECK(spy::dump_json(spy::value_to_json(spy::value_from_json(json(1.0)))) == "1.0");
    CHECK(spy::value_to_json(spy::value_from_json(json(1))).is_number_integer());
    CHECK(spy::value_to_json(spy::value_from_json(json(1.0))).is_number_float());
}

TEST_CASE("form key fixtures")
{
    auto fx = load("form_keys.json");
    for (const auto& c : fx["valid"]) {
        auto k = spy::parse_form_key(c["text"].get<std::string>());
        REQUIRE(k);
        CHECK(k->plugin == c["plugin"].get<std::string>());
        CHECK(k->local_id == c["local_id"].get<std::uint32_t>());
        CHECK(spy::format_form_key(*k) == c["canonical"].get<std::string>());
    }
    for (const auto& t : fx["invalid"]) {
        CAPTURE(t.dump());
        CHECK_FALSE(spy::parse_form_key(t.get<std::string>()));
    }
}

TEST_CASE("request fixtures, as JSON and as the text both modes hand to the plugin")
{
    auto fx = load("requests.json");
    for (const auto& m : fx["valid"]) {
        CAPTURE(m.dump());
        CHECK_NOTHROW(spy::parse_request(m));
        CHECK(spy::parse_request_text(spy::dump_json(m)).id == m["id"].get<std::uint64_t>());
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::parse_request(c["message"]); }) == c["error"].get<std::string>());
        CHECK(error_of([&] { spy::parse_request_text(spy::dump_json(c["message"])); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("a request without args is accepted, with empty args (fixture valid_without_args)")
{
    auto fx = load("requests.json");
    REQUIRE(fx["valid_without_args"].size() >= 1);
    for (const auto& m : fx["valid_without_args"]) {
        CAPTURE(m.dump());
        REQUIRE_FALSE(m.contains("args"));
        CHECK(spy::parse_request(m).args == json::object());
        CHECK(spy::parse_request_text(spy::dump_json(m)).id == m["id"].get<std::uint64_t>());
    }
}

TEST_CASE("responses built by the plugin match the fixture shapes")
{
    // responses.json also holds the dev bridge's welcome and handshake_error; those are built in Python (Task 23).
    auto fx = load("responses.json");
    REQUIRE(fx["valid"].size() >= 3);
    CHECK(same_text(spy::make_ok(1, 6), fx["valid"][0]));
    CHECK(same_text(spy::make_error(2, spy::ErrorCode::not_ready, "a game is loading", {{"reason", "loading"}, {"paused", false}, {"menus", spy::json::array()}}), fx["valid"][1]));
    // valid[2]: a form reference result, built by the codec the way the plugin builds it
    spy::FormRef actor;
    actor.form_id = 20;
    actor.form_key = "Skyrim.esm|0x000014";
    actor.type = "Actor";
    actor.epoch = 1;
    CHECK(same_text(spy::make_ok(3, spy::form_ref_to_json(actor)), fx["valid"][2]));
}
