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
    CHECK(spy::kProtocolMinor == 1);
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
    CHECK(spy::kDefaultEventQueue == 1024u);
    CHECK(spy::kMaxEventQueue == 65536u);
    CHECK(spy::kMaxSubscriptions == 1024u);
    CHECK(spy::kMaxSubscriptionsPerConsumer == 256u);
    CHECK(spy::kMaxQueuedEventBytesPerConsumer == 16777216u);
    CHECK(spy::kNotReadyReasons.size() == 5u);
    CHECK(spy::kNotReadyReasons.back() == "saving");
    CHECK(spy::kMaxFilterValues == 128u);
    CHECK(spy::kMaxEventsPerNext == 256u);
    CHECK(spy::kMaxChannelPayloadBytes == 65536u);
    CHECK(spy::kMaxChannelBytes == 256u);
    CHECK_FALSE(spy::config_defaults::kTraceTiming);
    CHECK(spy::config_defaults::kConsoleEchoErrors);
    CHECK(spy::kSaveNamePattern == "^[A-Za-z0-9_]{1,64}$");
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
                                          "papyrus.get_property", "papyrus.set_property", "papyrus.batch",
                                          "events.subscribe", "events.unsubscribe", "game.save", "game.load"};
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

TEST_CASE("the ops answered without a loaded game are game.state, events.subscribe, events.unsubscribe and game.load")
{
    const std::set<std::string_view> no_game = {spy::ops::kGameState, spy::ops::kEventsSubscribe, spy::ops::kEventsUnsubscribe,
                                                spy::ops::kGameLoad};
    for (const auto& spec : spy::kOps) {
        CAPTURE(std::string(spec.name));
        CHECK(spec.needs_game == (no_game.count(spec.name) == 0));
        if (spec.name.starts_with("events.") || spec.name.starts_with("game.")) CHECK_FALSE(spec.batchable);
    }
}

TEST_CASE("the events table is the plan's v1 list, with its filter and loading rules")
{
    const std::vector<std::string> names = {"death", "dying", "quest_stage", "quest_start", "quest_stop", "mod_event", "channel",
                                            "hit", "equip", "unequip", "container_changed", "cell_attach", "cell_detach", "activate",
                                            "player_location", "menu_open", "menu_close", "game_loaded", "game_saved", "new_game"};
    REQUIRE(spy::kEvents.size() == names.size());
    const std::set<std::string> while_loading = {"mod_event", "channel", "menu_open", "menu_close", "game_loaded", "game_saved", "new_game"};
    for (std::size_t i = 0; i < names.size(); ++i) {
        const auto& e = spy::kEvents[i];
        CAPTURE(names[i]);
        CHECK(e.name == names[i]);
        CHECK(spy::find_event(names[i]) == &e);
        CHECK(e.deliver_while_loading == (while_loading.count(names[i]) == 1));
        for (const auto f : e.filter_fields) {
            bool is_field = false;
            for (const auto& d : e.fields) is_field = is_field || (d.name == f && d.type != spy::EventFieldType::boolean && d.type != spy::EventFieldType::floating);
            CHECK_MESSAGE(is_field, "filter field " << f << " is not a form, int or string field");
        }
        const bool needs_filter = names[i] == "hit" || names[i] == "container_changed";
        CHECK(e.filter_required_any_of.empty() == !needs_filter);
    }
    CHECK(spy::find_event(spy::event_names::kDeath)->fields[1].nullable);         // killer may be null
    CHECK_FALSE(spy::find_event(spy::event_names::kDeath)->fields[0].nullable);   // victim never
    CHECK(spy::find_event("Death") == nullptr);                                    // names are exact
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
    for (const auto& c : hello["check"])  // the dev bridge's handshake checks; their codes must exist here too
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
    CHECK(defaults.trace_timing == spy::config_defaults::kTraceTiming);
    CHECK(defaults.console_echo_errors == spy::config_defaults::kConsoleEchoErrors);

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
