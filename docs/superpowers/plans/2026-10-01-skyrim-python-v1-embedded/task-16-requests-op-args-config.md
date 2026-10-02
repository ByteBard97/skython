# Task 16: Requests, op arguments, JSON depth, config

Adapted from: branch `plan-out-of-process`, task 12 (`task-12-messages-op-argument-validation-config-outbox-in.md`), and the `JsonDepth.h` helper from task 16 (`task-16-loopback-server-io-thread-handshake-session-file.md`).

> Part of the [Skython v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2-4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 11 (the fixtures) and Task 15 (and through it Tasks 0, 12-14). Linux only; no game.

**What changed from the out-of-process plan.** The C++ side no longer speaks the dev bridge: the handshake (`Hello`, `parse_hello`, `check_hello`, `WelcomeInfo`, `make_welcome`, `make_handshake_error`), the session file (`make_session_file`), framing (`frame_message`), the `Outbox` and the `InflightLimiter` are gone; the Python bridge (Task 23) owns them, and the dispatcher (Task 18) owns the in-flight cap. What stays is the request parser, the op-argument checks and the response builders, which now serve both modes: in-process, the `skython` package hands request **text** to `_skython`, so this task adds `parse_request_text` (depth check, then parse, then `parse_request`) and `peek_request_id` (the id for the error reply to text that could not be parsed). The raw-text depth scan from the old server task moves into core as `spy::nesting_within`. The header is renamed `messages.h` → `requests.h`. `Config` gets the new ini keys from the index (`python_enabled`, `scripts_enabled`, `bridge_enabled` instead of `allow_external_clients`) and a `config_to_json` for `_skython.config()` (Task 19).

**Files:**
- Create: `core/include/spy/json_depth.h`, `core/src/json_depth.cpp`, `core/include/spy/requests.h`, `core/src/requests.cpp`, `core/include/spy/config.h`, `core/src/config.cpp`
- Create: `core/tests/test_json_depth.cpp`, `core/tests/test_requests.cpp`, `core/tests/test_config.cpp`, `core/tests/test_fixtures.cpp`, `core/tests/test_generated.cpp`
- Consumes (generated, Task 12): `core/include/spy/protocol_generated.h`: `kProtocolMajor/Minor`, the limits (`kMaxNameBytes`, `kMaxBatchCalls`, `kMinTimeoutMs`, `kMaxTimeoutMs`, `kMaxErrorMessageBytes`, `kMaxJsonDepth`, `kTokenLength`, `kMaxArrayItems`, ...), the op table (`kOps`, `find_op`, `spy::ops::k*`, `OpSpec::needs_game`), the config table (`kConfigKeys`, `find_config_key`, `spy::config_defaults::k*`). This task defines none of them.

**Interfaces:**
- Consumes: Task 14 (`json`, `dump_json`, `str_or`, `clip_text`, `ProtocolError`, `ErrorCode`, `to_string`, `Value`, `FormRef`, `AliasRef`, `value_from_json`, `is_uint`), Task 15 (`is_valid_form_key`, `parse_form_key`).
- Produces (`json_depth.h`): `bool spy::nesting_within(std::string_view text, std::size_t max_depth)`: a raw-text scan, O(n), no allocation; brackets inside strings (and escaped quotes) are ignored.
- Produces (`requests.h`):
  - `struct spy::Request { std::uint64_t id; std::string op; json args; }`; `Request spy::parse_request(const json&)`: envelope (`"type": "request"`, an unsigned 64-bit `id`, a non-empty `op` of at most `kMaxNameBytes` bytes, `args` an object or absent) + known op + op arguments; throws `bad_request` or `type_mismatch`, only ever `ProtocolError`.
  - `Request spy::parse_request_text(std::string_view text)`: `nesting_within(text, kMaxJsonDepth)` first (`bad_request` "nested deeper than 64 levels"), then the non-throwing parser (invalid JSON or invalid UTF-8 → `bad_request`), then `parse_request`. Throws only `ProtocolError`.
  - `std::optional<std::uint64_t> spy::peek_request_id(std::string_view text)`: the top-level `id` when the text is JSON within the depth limit and the id is a non-negative 64-bit integer; else nullopt (the caller replies with id 0, as `PROTOCOL.md` says).
  - `const std::vector<std::string>& spy::known_ops()`: the names in the generated `kOps` table, in table order.
  - `struct spy::CallArgs { std::optional<Value> object; std::string script; std::string function; std::vector<Value> args; std::optional<std::uint32_t> timeout_ms; bool require_unpaused; }`; `CallArgs spy::parse_call_args(const json& args, bool needs_object)` (`timeout_ms` must be an integer `kMinTimeoutMs..kMaxTimeoutMs`, else `bad_request`)
  - `struct spy::PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; }`; `PropertyArgs spy::parse_property_args(const json& args, bool needs_value)`
  - `struct spy::BatchStep { std::string op; json args; }`; `struct spy::BatchArgs { std::vector<BatchStep> calls; bool stop_on_error; std::optional<std::uint32_t> timeout_ms; }`; `BatchArgs spy::parse_batch_args(const json&)` (1..`kMaxBatchCalls` calls; a step's op must be marked `batchable` in the generated op table)
  - `json spy::make_ok(std::uint64_t id, json result)`; `json spy::make_error(std::uint64_t id, ErrorCode, std::string_view message, json detail = json::object())` (the message is clipped to `kMaxErrorMessageBytes`; `detail` is the caller's responsibility and must be small). Both build `{"type":"response", ...}` exactly as `responses.json` shows; callers turn them into text with `dump_json`.
- Produces (`config.h`): `struct spy::Config { bool python_enabled; bool scripts_enabled; bool bridge_enabled; std::uint16_t port; std::string fixed_token; std::string session_file; std::uint32_t default_timeout_ms; std::uint32_t max_inflight_per_connection; std::uint32_t max_inflight_total; bool enable_console; std::vector<std::string> ignored_keys; }`, every default from `spy::config_defaults` (true, true, false, 45977, "", "", 5000, 256, 1024, false); `Config spy::parse_config(std::string_view ini_text)` (throws `std::invalid_argument` on a bad value, always that type; `ignored_keys` lists the unrecognised keys, and every line without `=` clipped to 64 bytes, so the plugin can log them); `json spy::config_to_json(const Config&)` (every field under its ini key name plus `ignored_keys`).

- [ ] **Step 1: Write the failing tests**

`core/tests/test_json_depth.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/json_depth.h"
#include "spy/protocol_generated.h"
#include <string>

TEST_CASE("nesting_within counts brackets outside strings only")
{
    using spy::nesting_within;
    CHECK(nesting_within("{}", 64));
    CHECK(nesting_within("", 64));
    CHECK(nesting_within(std::string(64, '[') + std::string(64, ']'), 64));
    CHECK_FALSE(nesting_within(std::string(65, '[') + std::string(65, ']'), 64));
    CHECK_FALSE(nesting_within(std::string(2000000, '['), 64));  // an unterminated prefix is rejected in one pass
    CHECK(nesting_within(R"({"a":"\"[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["})", 64));
    CHECK(nesting_within(R"({"a":"x\\","b":[1]})", 64));  // an escaped backslash ends before the closing quote
    CHECK_FALSE(nesting_within(R"({"a":[[[1]]]})", 3));
    CHECK(nesting_within(R"({"a":[[1]]})", 3));
}

TEST_CASE("the protocol's depth limit is the one the parser uses")
{
    CHECK(spy::kMaxJsonDepth == 64u);
    std::string ok = std::string(spy::kMaxJsonDepth, '[') + std::string(spy::kMaxJsonDepth, ']');
    CHECK(spy::nesting_within(ok, spy::kMaxJsonDepth));
    CHECK_FALSE(spy::nesting_within("[" + ok + "]", spy::kMaxJsonDepth));
}
```

`core/tests/test_requests.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/requests.h"
#include <string>

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
```

`core/tests/test_config.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/config.h"
#include <stdexcept>
#include <string>

TEST_CASE("defaults when empty: Python and scripts on, the dev bridge off")
{
    auto c = spy::parse_config("");
    CHECK(c.python_enabled);
    CHECK(c.scripts_enabled);
    CHECK_FALSE(c.bridge_enabled);
    CHECK(c.port == 45977);
    CHECK(c.fixed_token.empty());
    CHECK(c.default_timeout_ms == 5000u);
    CHECK(c.max_inflight_per_connection == 256u);
    CHECK(c.max_inflight_total == 1024u);
    CHECK_FALSE(c.enable_console);
    CHECK(c.ignored_keys.empty());
}

TEST_CASE("parses keys, ignores sections, comments and unknown keys")
{
    auto c = spy::parse_config(
        "; comment\n[Python]\npython_enabled = false\nscripts_enabled=0\n[Bridge]\nbridge_enabled=true\nport = 45990\n"
        "fixed_token = 00112233445566778899aabbccddeeff\nsession_file = C:/tmp/s.json\nunknown_key = 5\n# another comment\n"
        "max_inflight_total=10\n");
    CHECK_FALSE(c.python_enabled);
    CHECK_FALSE(c.scripts_enabled);
    CHECK(c.bridge_enabled);
    CHECK(c.port == 45990);
    CHECK(c.fixed_token == "00112233445566778899aabbccddeeff");
    CHECK(c.session_file == "C:/tmp/s.json");
    CHECK(c.max_inflight_total == 10u);
    REQUIRE(c.ignored_keys.size() == 1);
    CHECK(c.ignored_keys[0] == "unknown_key");
}

TEST_CASE("a line without '=' is reported in ignored_keys, clipped")
{
    auto c = spy::parse_config("bridge_enabled true\nport: 45990\n" + std::string(500, 'k') + "\n");
    CHECK_FALSE(c.bridge_enabled);  // the typo does not set the key, but it is no longer silent
    CHECK(c.port == 45977);
    REQUIRE(c.ignored_keys.size() == 3);
    CHECK(c.ignored_keys[0] == "bridge_enabled true");
    CHECK(c.ignored_keys[1] == "port: 45990");
    CHECK(c.ignored_keys[2] == std::string(64, 'k') + "...");
}

TEST_CASE("the old allow_external_clients key is no longer recognised")
{
    auto c = spy::parse_config("allow_external_clients = true\n");
    CHECK_FALSE(c.bridge_enabled);
    REQUIRE(c.ignored_keys.size() == 1);
    CHECK(c.ignored_keys[0] == "allow_external_clients");
}

TEST_CASE("bad values throw std::invalid_argument")
{
    CHECK_THROWS_AS(spy::parse_config("port = hello\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("port = 70000\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("bridge_enabled = maybe\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("python_enabled = yes\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("fixed_token = short\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("fixed_token = 00112233445566778899AABBCCDDEEFF\n"), std::invalid_argument);  // upper case
    CHECK_THROWS_AS(spy::parse_config("port = -1\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("port = \n"), std::invalid_argument);
}

TEST_CASE("a number too big for 64 bits is invalid_argument, not std::out_of_range")
{
    CHECK_THROWS_AS(spy::parse_config("port = 99999999999999999999999\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("default_timeout_ms = 18446744073709551616\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("max_inflight_total = 4294967296\n"), std::invalid_argument);
}

TEST_CASE("a UTF-8 byte order mark at the start of the file is skipped")
{
    auto c = spy::parse_config("\xEF\xBB\xBFport = 45990\nmax_inflight_total = 7\n");
    CHECK(c.port == 45990);
    CHECK(c.max_inflight_total == 7u);
    CHECK(c.ignored_keys.empty());
}

TEST_CASE("in-flight caps and the default timeout must be at least 1")
{
    CHECK_THROWS_AS(spy::parse_config("max_inflight_per_connection = 0\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("max_inflight_total = 0\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("default_timeout_ms = 0\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("default_timeout_ms = 3600001\n"), std::invalid_argument);
    CHECK(spy::parse_config("max_inflight_total = 1\n").max_inflight_total == 1u);
    CHECK(spy::parse_config("port = 0\n").port == 0);  // 0 means pick a free port
}

TEST_CASE("an inline comment starts at whitespace followed by a semicolon")
{
    auto c = spy::parse_config(
        "port = 45990 ; dev port\nbridge_enabled = true\t; dev only\n"
        "fixed_token = 00112233445566778899aabbccddeeff   ;token\nsession_file = C:/a;b/s.json ; note\n");
    CHECK(c.port == 45990);
    CHECK(c.bridge_enabled);
    CHECK(c.fixed_token == "00112233445566778899aabbccddeeff");
    CHECK(c.session_file == "C:/a;b/s.json");  // a semicolon with no space before it is part of the value
    CHECK_THROWS_AS(spy::parse_config("port = ; none\n"), std::invalid_argument);
    CHECK(spy::parse_config("session_file = C:/x#y.json\n").session_file == "C:/x#y.json");  // '#' is a comment only at line start
}

TEST_CASE("keys and boolean values are case-sensitive and lower case; wrong-case keys are reported")
{
    auto c = spy::parse_config("Port = 1234\nBridge_Enabled = true\nport = 45991\n");
    CHECK(c.port == 45991);                   // the lower-case key is used, the others ignored
    CHECK_FALSE(c.bridge_enabled);
    REQUIRE(c.ignored_keys.size() == 2);
    CHECK(c.ignored_keys[0] == "Port");
    CHECK(c.ignored_keys[1] == "Bridge_Enabled");
    CHECK_THROWS_AS(spy::parse_config("bridge_enabled = True\n"), std::invalid_argument);
    CHECK(spy::parse_config("port = 1\nport = 2\n").port == 2);  // a repeated key: the last one wins
}

TEST_CASE("config_to_json carries every field under its ini name")
{
    auto c = spy::parse_config("bridge_enabled = true\nport = 0\nfixed_token = 00112233445566778899aabbccddeeff\nWrong = 1\n");
    auto j = spy::config_to_json(c);
    CHECK(j["python_enabled"] == true);
    CHECK(j["scripts_enabled"] == true);
    CHECK(j["bridge_enabled"] == true);
    CHECK(j["port"] == 0);
    CHECK(j["fixed_token"] == "00112233445566778899aabbccddeeff");
    CHECK(j["session_file"] == "");
    CHECK(j["default_timeout_ms"] == 5000);
    CHECK(j["max_inflight_per_connection"] == 256);
    CHECK(j["max_inflight_total"] == 1024);
    CHECK(j["enable_console"] == false);
    CHECK(j["ignored_keys"] == spy::json::array({"Wrong"}));
    for (const auto& key : spy::kConfigKeys) CHECK_MESSAGE(j.contains(std::string(key.name)), "config_to_json lacks " << key.name);
    CHECK(j.size() == spy::kConfigKeys.size() + 1);  // the table's keys plus ignored_keys
}
```

`core/tests/test_fixtures.cpp` (the golden fixtures from Task 11 against the C++ side):
```cpp
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
```

`core/tests/test_generated.cpp` (ties the generated tables to the code and to the fixtures; compares against literals and fixture files, never a table against itself):
```cpp
#include "doctest/doctest.h"
#include "spy/config.h"
#include "spy/json_depth.h"
#include "spy/refs.h"
#include "spy/requests.h"
#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

// These tests tie the generated tables (protocol/protocol.json) to the hand-written code and to the golden
// fixtures. They compare the tables with the fixture files and with literal v1 values, never a table with itself,
// so editing protocol.json changes at least one result here.

using spy::json;

static json load(const char* name)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/" + name);
    REQUIRE(f.good());
    return json::parse(f);
}

static std::string hex(std::uint64_t v)
{
    char buf[24];
    std::snprintf(buf, sizeof buf, "%llX", static_cast<unsigned long long>(v));
    return buf;
}

// The code of the ProtocolError fn throws, or nullopt if it throws nothing.
static std::optional<spy::ErrorCode> thrown(auto&& fn)
{
    try {
        fn();
    } catch (const spy::ProtocolError& e) {
        return e.code;
    }
    return std::nullopt;
}

static const json* first_valid_request(const json& fx, std::string_view op)
{
    for (const auto& r : fx["valid"])
        if (r["op"].get<std::string>() == op) return &r;
    return nullptr;
}

TEST_CASE("the v1 wire contract is pinned: change these literals only together with a protocol version bump")
{
    CHECK(spy::kProtocolMajor == 1);
    CHECK(spy::kProtocolMinor == 0);
    CHECK(spy::kMaxFrameBytes == 16u * 1024u * 1024u);
    CHECK(spy::kPreAuthFrameBytes == 64u * 1024u);
    CHECK(spy::kMaxNameBytes == 256u);
    CHECK(spy::kMaxBatchCalls == 256u);
    CHECK(spy::kMinTimeoutMs == 1u);
    CHECK(spy::kMaxTimeoutMs == 3600000u);
    CHECK(spy::kMaxErrorMessageBytes == 1024u);
    CHECK(spy::kMaxClientNameBytes == 64u);
    CHECK(spy::kTokenLength == 32u);
    CHECK(spy::kIntMin == -2147483647 - 1);
    CHECK(spy::kIntMax == 2147483647);
    CHECK(spy::kFloatMaxText == 3.4028235e38);
    CHECK(spy::kMaxQueuedBytesPerConnection == 64u * 1024u * 1024u);
    CHECK(spy::kMaxConnections == 32u);
    CHECK(spy::kHelloTimeoutMs == 5000u);
    CHECK(spy::kMaxJsonDepth == 64u);
    CHECK(spy::kMaxArrayItems == 128u);
    CHECK(spy::form_ids::kMaxLocalId == 0xFFFFFFu);
    CHECK(spy::form_ids::kMaxLightLocalId == 0xFFFu);
    CHECK(spy::form_ids::kRuntimePrefix == 0xFFu);
    CHECK(spy::form_ids::kLightPrefix == 0xFEu);
    CHECK(spy::config_defaults::kPythonEnabled);
    CHECK(spy::config_defaults::kScriptsEnabled);
    CHECK_FALSE(spy::config_defaults::kBridgeEnabled);
    CHECK_FALSE(spy::config_defaults::kEnableConsole);
    CHECK(spy::config_defaults::kPort == 45977);
    CHECK(spy::config_defaults::kDefaultTimeoutMs == 5000u);
    CHECK(spy::config_defaults::kMaxInflightPerConnection == 256u);
    CHECK(spy::config_defaults::kMaxInflightTotal == 1024u);
    CHECK(spy::discovery::kHost == "127.0.0.1");
    CHECK(spy::discovery::kEnvSession == "SKYTHON_SESSION");
    CHECK(spy::discovery::kSessionFileName == "session.json");

    const std::vector<std::string> codes = {"not_ready", "busy", "bad_request", "unknown_function", "type_mismatch", "stale_reference",
                                            "not_found", "timeout", "vm_error", "unsupported", "internal", "unauthorized", "protocol_mismatch"};
    REQUIRE(spy::kErrorCodeNames.size() == codes.size());
    for (std::size_t i = 0; i < codes.size(); ++i) CHECK(spy::kErrorCodeNames[i].name == codes[i]);
    const std::vector<std::string> ops = {"game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
                                          "papyrus.get_property", "papyrus.set_property", "papyrus.batch"};
    CHECK(spy::known_ops() == ops);
}

TEST_CASE("the ErrorCode enum values index the name table, and names are unique")
{
    std::set<std::string_view> seen;
    for (std::size_t i = 0; i < spy::kErrorCodeNames.size(); ++i) {
        CHECK(static_cast<std::size_t>(spy::kErrorCodeNames[i].code) == i);
        CHECK(seen.insert(spy::kErrorCodeNames[i].name).second);
    }
}

TEST_CASE("only game.state is answered without a loaded game")
{
    for (const auto& spec : spy::kOps) {
        CAPTURE(std::string(spec.name));
        CHECK(spec.needs_game == (spec.name != spy::ops::kGameState));
    }
}

TEST_CASE("every op in the generated table has a fixture, and the validators enforce the table's required arguments")
{
    const auto fx = load("requests.json");
    for (const auto& spec : spy::kOps) {
        CAPTURE(std::string(spec.name));
        const json* valid = first_valid_request(fx, spec.name);
        REQUIRE(valid != nullptr);
        CHECK_FALSE(thrown([&] { spy::parse_request(*valid); }).has_value());
        for (const auto& arg : spec.args) {
            if (!arg.required) continue;
            CAPTURE(std::string(arg.name));
            json without = *valid;
            without["args"].erase(std::string(arg.name));
            CHECK(thrown([&] { spy::parse_request(without); }) == spy::ErrorCode::bad_request);
        }
        if (!spec.exactly_one_of.empty()) {
            json none = *valid;
            for (const auto alt : spec.exactly_one_of) none["args"].erase(std::string(alt));
            CHECK(thrown([&] { spy::parse_request(none); }) == spy::ErrorCode::bad_request);
            json two = *valid;
            for (const auto alt : spec.exactly_one_of) {
                const auto* a = &spec.args[0];
                for (const auto& cand : spec.args)
                    if (cand.name == alt) a = &cand;
                two["args"][std::string(alt)] = a->type == spy::ArgType::form_key ? json("Skyrim.esm|0x000001")
                                                : a->type == spy::ArgType::u32    ? json(1)
                                                                                  : json("x");
            }
            CHECK(thrown([&] { spy::parse_request(two); }) == spy::ErrorCode::bad_request);
        }
    }
    CHECK(spy::find_op("no.such.op") == nullptr);
}

TEST_CASE("a batch step must be an op the table marks batchable")
{
    const auto fx = load("requests.json");
    for (const auto& spec : spy::kOps) {
        CAPTURE(std::string(spec.name));
        const json step = {{"op", std::string(spec.name)}, {"args", (*first_valid_request(fx, spec.name))["args"]}};
        const auto code = thrown([&] { spy::parse_batch_args({{"calls", json::array({step})}}); });
        if (spec.batchable) CHECK_FALSE(code.has_value());
        else CHECK(code == spy::ErrorCode::bad_request);
    }
}

TEST_CASE("fixtures use only ops, argument names and error codes that the generated tables know")
{
    const auto requests = load("requests.json");
    for (const auto& r : requests["valid"]) {
        const auto* spec = spy::find_op(r["op"].get<std::string>());
        REQUIRE(spec != nullptr);
        for (const auto& [name, value] : r["args"].items()) {
            bool known = false;
            for (const auto& a : spec->args) known = known || a.name == name;
            CHECK_MESSAGE(known, "unknown argument '" << name << "' for " << r["op"].get<std::string>());
        }
    }
    std::set<std::string> codes;
    for (const char* file : {"hello.json", "requests.json", "values.json"}) {
        const auto fx = load(file);
        for (const auto& c : fx["invalid"]) codes.insert(c["error"].get<std::string>());
    }
    const auto hello = load("hello.json");
    for (const auto& c : hello["check"])  // the dev bridge's handshake checks (Task 23); their codes must exist here too
        if (!c["result"].is_null()) codes.insert(c["result"].get<std::string>());
    const auto responses = load("responses.json");
    for (const auto& r : responses["valid"]) {
        if (r["type"] == "handshake_error") codes.insert(r["code"].get<std::string>());
        if (r["type"] == "response" && r["ok"] == false) codes.insert(r["error"]["code"].get<std::string>());
        if (r["type"] == "welcome")
            for (const auto& cap : r["capabilities"]) CHECK(spy::find_op(cap.get<std::string>()) != nullptr);
        const auto& types = spy::kMessageTypes;
        CHECK(std::find(types.begin(), types.end(), r["type"].get<std::string>()) != types.end());
    }
    CHECK(codes.size() >= 4);
    for (const auto& c : codes) CHECK_MESSAGE(spy::error_code_from_string(c).has_value(), "fixture error code '" << c << "' is not in the table");
}

TEST_CASE("the fixtures sit exactly on the limits in the table")
{
    const auto requests = load("requests.json");
    const auto hello = load("hello.json");
    std::set<std::int64_t> bad_timeouts;
    for (const auto& c : requests["invalid"]) {
        const auto& a = c["message"]["args"];
        if (a.is_object() && a.contains("timeout_ms") && a["timeout_ms"].is_number_integer()) bad_timeouts.insert(a["timeout_ms"].get<std::int64_t>());
    }
    CHECK(bad_timeouts.count(static_cast<std::int64_t>(spy::kMinTimeoutMs) - 1) == 1);
    CHECK(bad_timeouts.count(static_cast<std::int64_t>(spy::kMaxTimeoutMs) + 1) == 1);
    for (const auto& r : requests["valid"])
        if (r["args"].contains("timeout_ms")) {
            CHECK(r["args"]["timeout_ms"].get<std::int64_t>() >= static_cast<std::int64_t>(spy::kMinTimeoutMs));
            CHECK(r["args"]["timeout_ms"].get<std::int64_t>() <= static_cast<std::int64_t>(spy::kMaxTimeoutMs));
        }

    const auto values = load("values.json");
    std::size_t longest_valid = 0, shortest_invalid = SIZE_MAX;
    for (const auto& c : values["valid"])
        if (c["wire"].is_object() && c["wire"].contains("items")) longest_valid = std::max(longest_valid, c["wire"]["items"].size());
    for (const auto& c : values["invalid"])
        if (c["wire"].is_object() && c["wire"].contains("items") && c["error"] == "type_mismatch" && c["wire"]["items"].size() > 2)
            shortest_invalid = std::min(shortest_invalid, c["wire"]["items"].size());
    CHECK(longest_valid == spy::kMaxArrayItems);
    CHECK(shortest_invalid == spy::kMaxArrayItems + 1);

    std::set<std::string> valid, invalid;
    for (const auto& c : values["valid"]) valid.insert(spy::dump_json(c["wire"]));
    for (const auto& c : values["invalid"]) invalid.insert(spy::dump_json(c["wire"]));
    CHECK(valid.count(std::to_string(spy::kIntMax)) == 1);
    CHECK(valid.count(std::to_string(spy::kIntMin)) == 1);
    CHECK(invalid.count(std::to_string(static_cast<std::int64_t>(spy::kIntMax) + 1)) == 1);
    CHECK(invalid.count(std::to_string(static_cast<std::int64_t>(spy::kIntMin) - 1)) == 1);
    CHECK(spy::value_from_json(json::parse("3.4028235e38")).v.index() == 3);
    CHECK(spy::kFloatMaxText < 3.5e38);
    CHECK(invalid.count(spy::dump_json(json(3.5e38))) == 1);

    for (const auto& h : hello["valid"]) {
        CHECK(h["token"].get<std::string>().size() == spy::kTokenLength);
        CHECK(h["protocol"][0].get<int>() == spy::kProtocolMajor);
    }
    const auto keys = load("form_keys.json");
    std::set<std::string> valid_keys, invalid_keys;
    for (const auto& c : keys["valid"]) valid_keys.insert(c["text"].get<std::string>());
    for (const auto& c : keys["invalid"]) invalid_keys.insert(c.get<std::string>());
    CHECK(valid_keys.count("Skyrim.esm|0x" + hex(spy::form_ids::kMaxLocalId)) == 1);
    CHECK(invalid_keys.count("Skyrim.esm|0x" + hex(spy::form_ids::kMaxLocalId + 1u)) == 1);
    CHECK(valid_keys.count("Light Mod.esl|0x" + hex(spy::form_ids::kMaxLightLocalId)) == 1);
    CHECK(invalid_keys.count("Light Mod.esl|0x" + hex(spy::form_ids::kMaxLightLocalId + 1u)) == 1);
}

TEST_CASE("every plugin extension in the table parses with its own local-id ceiling")
{
    for (const auto ext : spy::form_ids::kPluginExtensions) {
        const bool light = ext == spy::form_ids::kLightExtension;
        const std::uint32_t ceiling = light ? spy::form_ids::kMaxLightLocalId : spy::form_ids::kMaxLocalId;
        const std::string plugin = "a" + std::string(ext);
        CAPTURE(plugin);
        CHECK(spy::parse_form_key(plugin + "|0x" + hex(ceiling)).has_value());
        CHECK_FALSE(spy::parse_form_key(plugin + "|0x" + hex(ceiling + 1u)).has_value());
        std::string upper(ext);
        for (auto& ch : upper) ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
        CHECK(spy::parse_form_key("a" + upper + "|0x1").has_value());
    }
    CHECK_FALSE(spy::parse_form_key("a.txt|0x1").has_value());
    CHECK(spy::is_runtime_form_id(spy::form_ids::kRuntimePrefix << 24));
    CHECK(spy::is_light_form_id(spy::form_ids::kLightPrefix << 24));
    CHECK(spy::format_form_key({"a.esp", 1}).size() == 5 + 3 + static_cast<std::size_t>(spy::form_ids::kCanonicalHexDigits));
}

TEST_CASE("every ini key in the table is parsed by parse_config, at its default and at both ends of its range")
{
    const spy::Config defaults = spy::parse_config("");
    CHECK(defaults.python_enabled == spy::config_defaults::kPythonEnabled);
    CHECK(defaults.scripts_enabled == spy::config_defaults::kScriptsEnabled);
    CHECK(defaults.bridge_enabled == spy::config_defaults::kBridgeEnabled);
    CHECK(defaults.port == spy::config_defaults::kPort);
    CHECK(defaults.fixed_token == spy::config_defaults::kFixedToken);
    CHECK(defaults.session_file == spy::config_defaults::kSessionFile);
    CHECK(defaults.default_timeout_ms == spy::config_defaults::kDefaultTimeoutMs);
    CHECK(defaults.max_inflight_per_connection == spy::config_defaults::kMaxInflightPerConnection);
    CHECK(defaults.max_inflight_total == spy::config_defaults::kMaxInflightTotal);
    CHECK(defaults.enable_console == spy::config_defaults::kEnableConsole);

    for (const auto& key : spy::kConfigKeys) {
        const std::string name(key.name);
        CAPTURE(name);
        const auto c = spy::parse_config(name + " = " + std::string(key.default_text) + "\n");
        CHECK(c.ignored_keys.empty());  // a table key the parser does not know would land here (or throw logic_error)
        if (key.type == spy::ConfigType::u16 || key.type == spy::ConfigType::u32) {
            CHECK_NOTHROW(spy::parse_config(name + " = " + std::to_string(key.min) + "\n"));
            CHECK_NOTHROW(spy::parse_config(name + " = " + std::to_string(key.max) + "\n"));
            CHECK_THROWS_AS(spy::parse_config(name + " = " + std::to_string(key.max + 1) + "\n"), std::invalid_argument);
            if (key.min > 0) CHECK_THROWS_AS(spy::parse_config(name + " = " + std::to_string(key.min - 1) + "\n"), std::invalid_argument);
        }
    }
    CHECK(spy::parse_config("port = " + std::to_string(spy::config_defaults::kPort + 1) + "\n").port == spy::config_defaults::kPort + 1);
    CHECK(spy::find_config_key("default_timeout_ms")->min == spy::kMinTimeoutMs);
    CHECK(spy::find_config_key("default_timeout_ms")->max == spy::kMaxTimeoutMs);
    CHECK(spy::find_config_key("not_a_key") == nullptr);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | grep -m1 "fatal error"`
Expected: FAIL to compile, `fatal error:` ... `No such file or directory` for the first of `spy/json_depth.h`, `spy/requests.h` and `spy/config.h` that a test file includes.

- [ ] **Step 3: Implement**

`core/include/spy/json_depth.h`:
```cpp
#pragma once
#include <cstddef>
#include <string_view>

namespace spy {
// Request text can nest arbitrarily deep, and the recursive JSON parser and converters can overflow a thread's
// stack. Scan the raw text (no allocation, O(n)) and refuse anything nested deeper than max_depth BEFORE parsing
// it. Brackets inside strings are ignored; malformed text is not judged here (the parser does that).
bool nesting_within(std::string_view text, std::size_t max_depth);
}
```

`core/src/json_depth.cpp`:
```cpp
#include "spy/json_depth.h"

namespace spy {
bool nesting_within(std::string_view text, std::size_t max_depth)
{
    std::size_t depth = 0;
    bool in_string = false, escaped = false;
    for (const char ch : text) {
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') in_string = true;
        else if (ch == '[' || ch == '{') {
            if (++depth > max_depth) return false;
        } else if ((ch == ']' || ch == '}') && depth > 0) --depth;
    }
    return true;
}
}
```

`core/include/spy/requests.h`:
```cpp
#pragma once
#include "spy/protocol_generated.h"  // kMaxNameBytes, kMaxBatchCalls, kMinTimeoutMs, kMaxTimeoutMs, kMaxJsonDepth, kOps, ...
#include "spy/values.h"
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace spy {
// One request format for both modes (spec 4.2): in-process the skython package hands this text to _skython; over the dev
// bridge the Python bridge forwards the same text. Every request carries "type": "request".
struct Request { std::uint64_t id = 0; std::string op; json args; };
// Envelope + known op + op arguments. Throws only ProtocolError (bad_request or type_mismatch), never a json exception.
Request parse_request(const json& j);
// Request text -> Request: the nesting check (kMaxJsonDepth) runs before the parser, invalid JSON or invalid UTF-8 is
// bad_request, then parse_request. Throws only ProtocolError.
Request parse_request_text(std::string_view text);
// Best effort, for the error reply to a request that failed parse_request_text: the top-level "id" when the text is
// JSON within the depth limit and its id is a non-negative 64-bit integer, else nullopt (reply with id 0).
std::optional<std::uint64_t> peek_request_id(std::string_view text);
// The op names of the generated kOps table, in table order.
const std::vector<std::string>& known_ops();

struct CallArgs
{
    std::optional<Value> object;
    std::string script;
    std::string function;
    std::vector<Value> args;
    std::optional<std::uint32_t> timeout_ms;
    bool require_unpaused = false;
};
CallArgs parse_call_args(const json& args, bool needs_object);

struct PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; };
PropertyArgs parse_property_args(const json& args, bool needs_value);

struct BatchStep { std::string op; json args; };
struct BatchArgs { std::vector<BatchStep> calls; bool stop_on_error = true; std::optional<std::uint32_t> timeout_ms; };
BatchArgs parse_batch_args(const json& args);

json make_ok(std::uint64_t id, json result);
// `message` is clipped to kMaxErrorMessageBytes; `detail` must be small (the caller builds it).
json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object());
}
```

`core/src/requests.cpp`:
```cpp
#include "spy/requests.h"
#include "spy/json_depth.h"

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

std::string req_string(const json& o, const char* key)
{
    if (!o.contains(key) || !o[key].is_string()) bad(std::string("missing or non-string field '") + key + "'");
    const auto& s = o[key].get_ref<const std::string&>();
    if (s.empty()) bad(std::string("field '") + key + "' is empty");
    if (s.size() > kMaxNameBytes) bad(std::string("field '") + key + "' is longer than " + std::to_string(kMaxNameBytes) + " bytes");
    return s;
}

std::optional<std::uint32_t> opt_u32(const json& o, const char* key)
{
    if (!o.contains(key)) return std::nullopt;
    if (!is_uint(o[key], 0xFFFFFFFFull)) bad(std::string("field '") + key + "' must be an unsigned 32-bit integer");
    return o[key].get<std::uint32_t>();
}

std::optional<std::uint32_t> opt_timeout(const json& o)
{
    if (!o.contains("timeout_ms")) return std::nullopt;
    const auto& t = o["timeout_ms"];
    if (!is_uint(t, kMaxTimeoutMs) || t.get<std::uint64_t>() < kMinTimeoutMs)
        bad("timeout_ms must be an integer from " + std::to_string(kMinTimeoutMs) + " to " + std::to_string(kMaxTimeoutMs));
    return t.get<std::uint32_t>();
}

Value parse_object_ref(const json& o)
{
    if (!o.contains("object")) bad("missing field 'object'");
    Value v = value_from_json(o["object"]);
    if (!std::holds_alternative<FormRef>(v.v) && !std::holds_alternative<AliasRef>(v.v))
        bad("'object' must be a form or alias reference");
    return v;
}

// The checks themselves are code; which ops exist, which are batchable and which arguments are alternatives come from
// the generated op table (core/tests/test_generated.cpp checks that this function agrees with that table).
void validate_op_args(const std::string& op, const json& args)
{
    if (op == ops::kPapyrusCallStatic) parse_call_args(args, false);
    else if (op == ops::kPapyrusCallMethod) parse_call_args(args, true);
    else if (op == ops::kPapyrusGetProperty) parse_property_args(args, false);
    else if (op == ops::kPapyrusSetProperty) parse_property_args(args, true);
    else if (op == ops::kPapyrusBatch) parse_batch_args(args);
    else if (op == ops::kFormResolve) {
        const OpSpec* spec = find_op(op);
        int n = 0;
        std::string names;
        for (const auto alt : spec->exactly_one_of) {
            n += args.contains(std::string(alt)) ? 1 : 0;
            names += (names.empty() ? "" : ", ") + std::string(alt);
        }
        if (n != 1) bad(op + " needs exactly one of " + names);
        if (args.contains("form_key") && (!args["form_key"].is_string() || !is_valid_form_key(args["form_key"].get<std::string>())))
            bad("form_key must look like Plugin.esp|0x0012AB");
        if (args.contains("form_id")) opt_u32(args, "form_id");
        if (args.contains("editor_id")) req_string(args, "editor_id");
    }
}
}

const std::vector<std::string>& known_ops()
{
    static const std::vector<std::string> names = [] {
        std::vector<std::string> v;
        for (const auto& spec : kOps) v.emplace_back(spec.name);
        return v;
    }();
    return names;
}

Request parse_request(const json& j)
{
    if (!j.is_object() || str_or(j, "type", "") != "request") bad("expected a request");
    if (!j.contains("id") || !is_uint(j["id"], UINT64_MAX)) bad("request id must be a non-negative integer");
    Request r;
    r.id = j["id"].get<std::uint64_t>();
    r.op = req_string(j, "op");
    r.args = j.value("args", json::object());
    if (!r.args.is_object()) bad("args must be an object");
    if (find_op(r.op) == nullptr) bad("unknown op '" + clip_text(r.op, 64) + "'");
    validate_op_args(r.op, r.args);
    return r;
}

Request parse_request_text(std::string_view text)
{
    if (!nesting_within(text, kMaxJsonDepth)) bad("request is nested deeper than " + std::to_string(kMaxJsonDepth) + " levels");
    const json j = json::parse(text.begin(), text.end(), nullptr, /*allow_exceptions=*/false);
    if (j.is_discarded()) bad("request is not valid JSON (or not valid UTF-8)");
    return parse_request(j);
}

std::optional<std::uint64_t> peek_request_id(std::string_view text)
{
    if (!nesting_within(text, kMaxJsonDepth)) return std::nullopt;
    const json j = json::parse(text.begin(), text.end(), nullptr, /*allow_exceptions=*/false);
    if (!j.is_object() || !j.contains("id") || !is_uint(j["id"], UINT64_MAX)) return std::nullopt;
    return j["id"].get<std::uint64_t>();
}

CallArgs parse_call_args(const json& a, bool needs_object)
{
    CallArgs c;
    if (needs_object) c.object = parse_object_ref(a);
    c.script = req_string(a, "script");
    c.function = req_string(a, "function");
    if (a.contains("args")) {
        if (!a["args"].is_array()) bad("'args' must be a JSON array of values");
        for (const auto& v : a["args"]) c.args.push_back(value_from_json(v));
    }
    c.timeout_ms = opt_timeout(a);
    if (a.contains("require_unpaused")) {
        if (!a["require_unpaused"].is_boolean()) bad("require_unpaused must be a boolean");
        c.require_unpaused = a["require_unpaused"].get<bool>();
    }
    return c;
}

PropertyArgs parse_property_args(const json& a, bool needs_value)
{
    PropertyArgs p;  // member by member: brace-initialising leaks the members already built when a later initialiser throws (GCC 11, seen under ASan)
    p.object = parse_object_ref(a);
    p.script = req_string(a, "script");
    p.property = req_string(a, "property");
    if (needs_value) {
        if (!a.contains("value")) bad("missing field 'value'");
        p.value = value_from_json(a["value"]);
    }
    return p;
}

BatchArgs parse_batch_args(const json& a)
{
    if (!a.contains("calls") || !a["calls"].is_array() || a["calls"].empty()) bad("batch needs a non-empty 'calls' array");
    if (a["calls"].size() > kMaxBatchCalls)
        bad("a batch may hold at most " + std::to_string(kMaxBatchCalls) + " calls, got " + std::to_string(a["calls"].size()));
    BatchArgs b;
    for (const auto& step : a["calls"]) {
        if (!step.is_object()) bad("each batch call must be an object");
        auto op = req_string(step, "op");
        const OpSpec* spec = find_op(op);
        if (spec == nullptr || !spec->batchable) bad("a batch step must be a batchable papyrus.* op, got '" + clip_text(op, 64) + "'");
        auto args = step.value("args", json::object());
        validate_op_args(op, args);
        b.calls.push_back({op, args});
    }
    if (a.contains("stop_on_error")) {
        if (!a["stop_on_error"].is_boolean()) bad("stop_on_error must be a boolean");
        b.stop_on_error = a["stop_on_error"].get<bool>();
    }
    b.timeout_ms = opt_timeout(a);
    return b;
}

json make_ok(std::uint64_t id, json result)
{
    return {{"type", "response"}, {"id", id}, {"ok", true}, {"result", std::move(result)}};
}

json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail)
{
    return {{"type", "response"}, {"id", id}, {"ok", false},
            {"error", {{"code", to_string(code)}, {"message", clip_text(message, kMaxErrorMessageBytes)}, {"detail", std::move(detail)}}}};
}
}
```

`core/include/spy/config.h`:
```cpp
#pragma once
#include "spy/errors.h"              // json
#include "spy/protocol_generated.h"  // config_defaults::*, kConfigKeys: names, ranges and defaults of the ini keys
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace spy {
// Every default and range below is a protocol/protocol.json "config_keys" entry; none is written here.
struct Config
{
    bool python_enabled = config_defaults::kPythonEnabled;    // false: the plugin never initialises CPython
    bool scripts_enabled = config_defaults::kScriptsEnabled;  // false: skython._host loads no player scripts
    bool bridge_enabled = config_defaults::kBridgeEnabled;    // the dev bridge; false in the shipped ini
    std::uint16_t port = config_defaults::kPort;              // dev bridge port; 0 = pick a free port
    std::string fixed_token{config_defaults::kFixedToken};    // empty: a random token per start
    std::string session_file{config_defaults::kSessionFile};  // empty: <SKSE log dir>/Skython/session.json
    std::uint32_t default_timeout_ms = config_defaults::kDefaultTimeoutMs;  // kMinTimeoutMs .. kMaxTimeoutMs
    std::uint32_t max_inflight_per_connection = config_defaults::kMaxInflightPerConnection;  // dev bridge; at least 1
    std::uint32_t max_inflight_total = config_defaults::kMaxInflightTotal;  // dispatcher; at least 1
    bool enable_console = config_defaults::kEnableConsole;
    std::vector<std::string> ignored_keys;  // unrecognised keys and lines without '=' (log them: "Port" instead of "port" is silent otherwise)
};

// INI subset, UTF-8 (a leading byte order mark is skipped).
//  - "key = value" lines. Sections ([...]) are ignored. A line starting with ';' or '#' is a comment.
//  - An inline comment starts at whitespace followed by ';' ("port = 1 ; dev"). A ';' with no whitespace before it stays in
//    the value, and '#' is never an inline comment.
//  - Keys and the boolean values (true/false/1/0) are case-sensitive and lower case. An unrecognised key, including one
//    with the wrong case, is ignored but listed in Config::ignored_keys. So is a line without '=' (clipped to 64 bytes).
//    A repeated key: the last one wins.
//  - Every bad value throws std::invalid_argument, including numbers too large for 64 bits.
Config parse_config(std::string_view text);

// Every Config field under its ini key name, plus "ignored_keys" (an array). _skython.config() (Task 19) starts from this.
json config_to_json(const Config& c);
}
```

`core/src/config.cpp`:
```cpp
#include "spy/config.h"
#include <algorithm>
#include <cctype>
#include <charconv>
#include <stdexcept>
#include <system_error>

namespace spy {
namespace {
std::string trim(std::string_view s)
{
    auto b = s.find_first_not_of(" \t\r");
    if (b == std::string_view::npos) return {};
    auto e = s.find_last_not_of(" \t\r");
    return std::string(s.substr(b, e - b + 1));
}

// "value ; comment": the comment starts at a ';' that follows a space or tab.
std::string_view strip_inline_comment(std::string_view s)
{
    for (std::size_t i = 1; i < s.size(); ++i)
        if (s[i] == ';' && (s[i - 1] == ' ' || s[i - 1] == '\t')) return s.substr(0, i);
    return s;
}

std::uint64_t to_uint(const std::string& key, const std::string& v, std::uint64_t min, std::uint64_t max)
{
    if (v.empty() || !std::all_of(v.begin(), v.end(), [](char c) { return std::isdigit(static_cast<unsigned char>(c)); }))
        throw std::invalid_argument(key + " must be a non-negative integer");
    std::uint64_t n = 0;
    const auto r = std::from_chars(v.data(), v.data() + v.size(), n);
    if (r.ec != std::errc() || n > max) throw std::invalid_argument(key + " is out of range");
    if (n < min) throw std::invalid_argument(key + " must be at least " + std::to_string(min));
    return n;
}

bool to_bool(const std::string& key, const std::string& v)
{
    if (v == "true" || v == "1") return true;
    if (v == "false" || v == "0") return false;
    throw std::invalid_argument(key + " must be true or false");
}

bool is_token(const std::string& val)
{
    return val.size() == kTokenLength && std::all_of(val.begin(), val.end(), [](char ch) { return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'); });
}
}

Config parse_config(std::string_view text)
{
    if (text.substr(0, 3) == "\xEF\xBB\xBF") text.remove_prefix(3);
    Config c;
    std::size_t start = 0;
    while (start <= text.size()) {
        auto end = text.find('\n', start);
        if (end == std::string_view::npos) end = text.size();
        auto line = trim(text.substr(start, end - start));
        start = end + 1;
        if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
        auto eq = line.find('=');
        if (eq == std::string::npos) {  // "bridge_enabled true": a typo must not be silent
            c.ignored_keys.push_back(clip_text(line, 64));
            continue;
        }
        auto key = trim(line.substr(0, eq));
        auto val = trim(strip_inline_comment(std::string_view(line).substr(eq + 1)));
        const ConfigKey* spec = find_config_key(key);  // names, ranges and defaults come from protocol.json
        if (spec == nullptr) {
            if (!key.empty()) c.ignored_keys.push_back(key);
            continue;
        }
        if (key == "python_enabled") c.python_enabled = to_bool(key, val);
        else if (key == "scripts_enabled") c.scripts_enabled = to_bool(key, val);
        else if (key == "bridge_enabled") c.bridge_enabled = to_bool(key, val);
        else if (key == "port") c.port = static_cast<std::uint16_t>(to_uint(key, val, spec->min, spec->max));
        else if (key == "fixed_token") {
            if (!val.empty() && !is_token(val))
                throw std::invalid_argument("fixed_token must be " + std::to_string(kTokenLength) + " lowercase hex characters");
            c.fixed_token = val;
        } else if (key == "session_file") c.session_file = val;
        else if (key == "default_timeout_ms") c.default_timeout_ms = static_cast<std::uint32_t>(to_uint(key, val, spec->min, spec->max));
        else if (key == "max_inflight_per_connection") c.max_inflight_per_connection = static_cast<std::uint32_t>(to_uint(key, val, spec->min, spec->max));
        else if (key == "max_inflight_total") c.max_inflight_total = static_cast<std::uint32_t>(to_uint(key, val, spec->min, spec->max));
        else if (key == "enable_console") c.enable_console = to_bool(key, val);
        else throw std::logic_error("config key '" + key + "' is in protocol.json but parse_config does not handle it");
    }
    return c;
}

json config_to_json(const Config& c)
{
    return {{"python_enabled", c.python_enabled},
            {"scripts_enabled", c.scripts_enabled},
            {"bridge_enabled", c.bridge_enabled},
            {"port", c.port},
            {"fixed_token", c.fixed_token},
            {"session_file", c.session_file},
            {"default_timeout_ms", c.default_timeout_ms},
            {"max_inflight_per_connection", c.max_inflight_per_connection},
            {"max_inflight_total", c.max_inflight_total},
            {"enable_console", c.enable_console},
            {"ignored_keys", c.ignored_keys}};
}
}
```
Note: `port = 70000` must throw: the `port` entry of the generated table has `max` 65535 and `to_uint` enforces it. The `logic_error` at the end of the chain can only fire if `protocol.json` gains an ini key this function does not parse; `core/tests/test_generated.cpp` feeds every table key to `parse_config`, so that mistake fails a test, not a player.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && core/build/spy_core_tests`
Expected: PASS, `[doctest] test cases:  61 |  61 passed | 0 failed | 0 skipped` and `[doctest] assertions: 777 | 777 passed | 0 failed |` (Tasks 14-16 together). If a fixture case fails, fix the code, never the fixture, unless the fixture contradicts `protocol/PROTOCOL.md`.

- [ ] **Step 5: Run the suite under warnings and the sanitizers**

Run: `cmake -S core -B core/build/asan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-Wall -Wextra -Wconversion -fsanitize=address,undefined -fno-sanitize-recover=undefined" >/dev/null && cmake --build core/build/asan -j 2>&1 | grep -c "warning:" ; core/build/asan/spy_core_tests | tail -3`
Expected: the `grep -c` prints `0` (no warnings from the core sources or tests), and the test run prints the same counts as Step 4 with no AddressSanitizer, LeakSanitizer or UndefinedBehaviorSanitizer report. (`core/build/` is git-ignored, and the sanitizer tree lives inside it.) Rerun this step after any later change to `core/`. It caught a real leak on the branch: brace-initialising an aggregate leaks the members already built when a later initialiser throws, on GCC 11, which is why the parsers assign member by member.

- [ ] **Step 6: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`. Nothing in `core/` may spell out a protocol constant, an error code or an op name that `protocol/protocol.json` owns; if a later task needs one, it takes it from `spy/protocol_generated.h` (and if a fact is missing there, add it to `protocol.json` and regenerate, in the same commit).

- [ ] **Step 7: Commit**

```bash
git add core/include/spy/json_depth.h core/src/json_depth.cpp core/include/spy/requests.h core/src/requests.cpp \
        core/include/spy/config.h core/src/config.cpp core/tests/test_json_depth.cpp core/tests/test_requests.cpp \
        core/tests/test_config.cpp core/tests/test_fixtures.cpp core/tests/test_generated.cpp
git commit -m "feat(core): request text parsing with a depth limit, op-argument checks, response builders, config; fixture sweep and table-driven checks"
```
