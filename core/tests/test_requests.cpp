#include "doctest/doctest.h"
#include "spy/requests.h"
#include <regex>
#include <string>
#include <vector>

using spy::json;

static spy::ErrorCode code_of_request(const json& j)
{
    try {
        spy::parse_request(j);
    } catch (const spy::ProtocolError& e) {
        return e.code;
    }
    FAIL("expected ProtocolError");
    return spy::ErrorCode::internal;
}

static json request(const char* op, json args) { return {{"type", "request"}, {"id", 1}, {"op", op}, {"args", std::move(args)}}; }

TEST_CASE("parse_request throws only ProtocolError for wrongly typed envelopes")
{
    CHECK(code_of_request({{"type", 5}, {"id", 1}, {"op", "game.state"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of_request({{"type", "request"}, {"id", 1}, {"op", 5}}) == spy::ErrorCode::bad_request);
    CHECK(code_of_request({{"type", "request"}, {"id", "1"}, {"op", "game.state"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of_request({{"type", "request"}, {"id", 1.5}, {"op", "game.state"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of_request({{"type", "request"}, {"id", 1}, {"op", "game.state"}, {"args", nullptr}}) == spy::ErrorCode::bad_request);
    CHECK(code_of_request(json::array()) == spy::ErrorCode::bad_request);
    CHECK(spy::parse_request({{"type", "request"}, {"id", 18446744073709551615ULL}, {"op", "game.state"}}).id == 18446744073709551615ULL);
}

TEST_CASE("an unknown op does not echo a huge op name")
{
    try {
        spy::parse_request(request(std::string(2000000, 'x').c_str(), json::object()));
        FAIL("expected ProtocolError");
    } catch (const spy::ProtocolError& e) {
        CHECK(std::string(e.what()).size() < 300);
    }
}

TEST_CASE("call args parse values and optional fields")
{
    json a = {{"object", {{"kind", "form"}, {"form_id", 20}}}, {"script", "Actor"}, {"function", "GetActorValue"},
              {"args", {"Health"}}, {"timeout_ms", 2000}, {"require_unpaused", true}};
    auto c = spy::parse_call_args(a, true);
    CHECK(c.object.has_value());
    CHECK(c.args.size() == 1);
    CHECK(c.timeout_ms.value() == 2000u);
    CHECK(c.require_unpaused);
}

TEST_CASE("timeout_ms is an integer from 1 to one hour")
{
    auto with = [](json t) {
        return json{{"script", "Utility"}, {"function", "Wait"}, {"args", json::array()}, {"timeout_ms", std::move(t)}};
    };
    CHECK(spy::parse_call_args(with(1), false).timeout_ms.value() == 1u);
    CHECK(spy::parse_call_args(with(3600000), false).timeout_ms.value() == 3600000u);
    for (json bad : {json(0), json(-5), json(3600001), json(4294967295ULL), json(1.5), json("2000"), json(nullptr)}) {
        CAPTURE(bad.dump());
        CHECK_THROWS_AS(spy::parse_call_args(with(bad), false), spy::ProtocolError);
    }
}

TEST_CASE("property args parse, and a bad field after a good object reference does not leak")
{
    json ok = {{"object", {{"kind", "form"}, {"form_key", "Skyrim.esm|0x000014"}, {"type", "Actor"}}}, {"script", "Actor"}, {"property", "p"}, {"value", 3}};
    auto p = spy::parse_property_args(ok, true);
    CHECK(p.script == "Actor");
    CHECK(p.value.has_value());
    json bad = ok;
    bad["property"] = 5;
    CHECK_THROWS_AS(spy::parse_property_args(bad, true), spy::ProtocolError);
    bad = ok;
    bad.erase("value");
    CHECK_THROWS_AS(spy::parse_property_args(bad, true), spy::ProtocolError);
    CHECK_NOTHROW(spy::parse_property_args(bad, false));
}

TEST_CASE("names are bounded")
{
    json a = {{"script", std::string(257, 's')}, {"function", "F"}, {"args", json::array()}};
    CHECK_THROWS_AS(spy::parse_call_args(a, false), spy::ProtocolError);
    a["script"] = std::string(256, 's');
    CHECK_NOTHROW(spy::parse_call_args(a, false));
    a["script"] = 5;
    CHECK_THROWS_AS(spy::parse_call_args(a, false), spy::ProtocolError);
}

TEST_CASE("a batch has between 1 and kMaxBatchCalls calls")
{
    json step = {{"op", "papyrus.call_static"}, {"args", {{"script", "Utility"}, {"function", "RandomInt"}, {"args", {1, 6}}}}};
    json calls = json::array();
    for (std::size_t i = 0; i < spy::kMaxBatchCalls; ++i) calls.push_back(step);
    CHECK(spy::parse_batch_args({{"calls", calls}}).calls.size() == spy::kMaxBatchCalls);
    calls.push_back(step);
    CHECK_THROWS_AS(spy::parse_batch_args({{"calls", calls}}), spy::ProtocolError);
    CHECK_THROWS_AS(spy::parse_batch_args({{"calls", json::array()}}), spy::ProtocolError);
    CHECK_THROWS_AS(spy::parse_batch_args({{"calls", json::array({step})}, {"stop_on_error", "yes"}}), spy::ProtocolError);
}

TEST_CASE("errors never exceed the message cap and invalid UTF-8 never throws")
{
    auto e = spy::make_error(7, spy::ErrorCode::bad_request, std::string(3 * 1024 * 1024, 'm'));
    CHECK(e["error"]["message"].get<std::string>().size() <= spy::kMaxErrorMessageBytes + 3);
    CHECK_NOTHROW(spy::dump_json(spy::make_error(1, spy::ErrorCode::vm_error, "bad \xE9 byte")));
    CHECK_NOTHROW(spy::dump_json(spy::make_ok(1, std::string("caf\xE9"))));
    CHECK(e["type"] == "response");
    CHECK(e["id"] == 7);
    CHECK(e["ok"] == false);
    CHECK(e["error"]["detail"] == spy::json::object());
}

TEST_CASE("parse_request_text: depth is checked before parsing, bad text is bad_request")
{
    const std::string ok = R"({"type":"request","id":4,"op":"game.state","args":{}})";
    CHECK(spy::parse_request_text(ok).id == 4u);
    auto code_of_text = [](const std::string& text) {
        try {
            spy::parse_request_text(text);
        } catch (const spy::ProtocolError& e) {
            return e.code;
        }
        return spy::ErrorCode::internal;
    };
    CHECK(code_of_text("") == spy::ErrorCode::bad_request);
    CHECK(code_of_text("{not json") == spy::ErrorCode::bad_request);
    CHECK(code_of_text(R"({"type":"request","id":1,"op":"game.state","args":{"x":"caf)" "\xE9" R"("}})") == spy::ErrorCode::bad_request);  // invalid UTF-8
    std::string deep = R"({"type":"request","id":2,"op":"papyrus.call_static","args":{"script":"S","function":"F","args":[)";
    for (std::size_t i = 0; i < spy::kMaxJsonDepth; ++i) deep += "[";
    for (std::size_t i = 0; i < spy::kMaxJsonDepth; ++i) deep += "]";
    deep += "]}}";
    CHECK(code_of_text(deep) == spy::ErrorCode::bad_request);
    CHECK(code_of_text(std::string(2000000, '[')) == spy::ErrorCode::bad_request);  // never reaches the recursive parser
    CHECK(code_of_text(R"({"type":"request","id":3,"op":"papyrus.call_static","args":{"script":"Utility","function":"RandomInt","args":[1,2147483648]}})") ==
          spy::ErrorCode::type_mismatch);
}

TEST_CASE("peek_request_id finds the top-level id of text that parse_request rejects")
{
    CHECK(spy::peek_request_id(R"({"type":"request","id":42,"op":"no.such.op"})").value() == 42u);
    CHECK(spy::peek_request_id(R"({"type":5,"id":7})").value() == 7u);
    CHECK(spy::peek_request_id(R"({"id":18446744073709551615})").value() == 18446744073709551615ULL);
    CHECK_FALSE(spy::peek_request_id(R"({"id":-1})").has_value());
    CHECK_FALSE(spy::peek_request_id(R"({"id":"7"})").has_value());
    CHECK_FALSE(spy::peek_request_id(R"({"args":{"id":7}})").has_value());  // only the top level counts
    CHECK_FALSE(spy::peek_request_id("{not json").has_value());
    CHECK_FALSE(spy::peek_request_id(std::string(100, '[')).has_value());
    CHECK_FALSE(spy::peek_request_id("[1,2]").has_value());
}

TEST_CASE("known_ops lists the generated table in order")
{
    REQUIRE(spy::known_ops().size() == spy::kOps.size());
    for (std::size_t i = 0; i < spy::kOps.size(); ++i) CHECK(spy::known_ops()[i] == spy::kOps[i].name);
}

TEST_CASE("is_valid_save_name agrees with the generated save-name pattern")
{
    const std::regex pattern{std::string(spy::kSaveNamePattern)};
    const std::vector<std::string> samples = {"", "a", "skython_t41", "A_Z_0_9", std::string(64, 'x'), std::string(65, 'x'),
                                              "has space", "dot.ess", "../up", "tab\t", "\xC3\xBCmlaut", "semi;colon", "-dash",
                                              std::string(1, '\0'), "trailing\n"};
    for (const auto& s : samples) {
        CAPTURE(s);
        CHECK(spy::is_valid_save_name(s) == std::regex_match(s, pattern));
    }
    CHECK(spy::is_valid_save_name(std::string(64, 'x')));
    CHECK_FALSE(spy::is_valid_save_name(std::string(65, 'x')));
}

TEST_CASE("events.* and game.save/game.load arguments are checked from their table entries")
{
    using spy::ErrorCode;
    const json form = {{"kind", "form"}, {"form_id", 20}};
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}})));
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}, {"filter", nullptr}})));  // null = absent
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "hit"}, {"filter", {{"target", json::array({form})}}}, {"max_queue", 65536}})));
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}, {"max_queue", 1}})));
    CHECK(code_of_request(request("events.subscribe", json::object())) == ErrorCode::bad_request);
    CHECK(code_of_request(request("events.subscribe", {{"event", "Death"}})) == ErrorCode::bad_request);  // exact names only
    CHECK(code_of_request(request("events.subscribe", {{"event", "death"}, {"filter", "victim"}})) == ErrorCode::bad_request);
    for (const json& bad_queue : {json(0), json(65537), json(1.5), json(true), json("16"), json(-1)})
        CHECK(code_of_request(request("events.subscribe", {{"event", "death"}, {"max_queue", bad_queue}})) == ErrorCode::bad_request);
    try {
        spy::parse_request(request("events.subscribe", {{"event", "no_such_event"}}));
        FAIL("expected ProtocolError");
    } catch (const spy::ProtocolError& e) {
        CHECK(std::string(e.what()).find("unknown event 'no_such_event'") != std::string::npos);
    }

    CHECK_NOTHROW(spy::parse_request(request("events.unsubscribe", {{"sub", 0}})));
    CHECK_NOTHROW(spy::parse_request(request("events.unsubscribe", {{"sub", 18446744073709551615ULL}})));
    for (const json& bad_sub : {json(-1), json(1.0), json("7"), json(nullptr)})
        CHECK(code_of_request(request("events.unsubscribe", {{"sub", bad_sub}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("events.unsubscribe", json::object())) == ErrorCode::bad_request);

    CHECK_NOTHROW(spy::parse_request(request("game.save", {{"name", "skython_t33"}, {"timeout_ms", 60000}})));
    CHECK_NOTHROW(spy::parse_request(request("game.load", {{"save", "skython_t33"}})));
    CHECK(code_of_request(request("game.save", json::object())) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", 5}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", "a b"}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", "ok"}, {"timeout_ms", 0}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.load", {{"save", "../x"}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.load", {{"name", "ok"}})) == ErrorCode::bad_request);  // the load argument is "save"
}
