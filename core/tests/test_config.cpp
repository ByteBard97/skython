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
