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
    bool scripts_enabled = config_defaults::kScriptsEnabled;  // false: skyrim._host loads no player scripts
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

// Every Config field under its ini key name, plus "ignored_keys" (an array). _skyrim.config() (Task 19) starts from this.
json config_to_json(const Config& c);
}
