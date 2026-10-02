#pragma once
#include <cstddef>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <nlohmann/json.hpp>
#include "spy/protocol_generated.h"  // enum class ErrorCode and kErrorCodeNames, generated from protocol/protocol.json

namespace spy {
using json = nlohmann::json;

// Over the generated kErrorCodeNames table. A value outside the table is "internal".
std::string_view to_string(ErrorCode code);
std::optional<ErrorCode> error_code_from_string(std::string_view s);

// At most `max` bytes of `s` (cut back to a UTF-8 boundary), then "..." if anything was cut.
std::string clip_text(std::string_view s, std::size_t max = 120);

// 'A'-'Z' to 'a'-'z', every other byte unchanged. Locale-independent (std::tolower is not: a Turkish locale maps 'I'
// elsewhere), for script, plugin and key names, which are compared as ASCII.
constexpr char ascii_lower(char c) { return c >= 'A' && c <= 'Z' ? static_cast<char>(c - 'A' + 'a') : c; }

struct ProtocolError : std::runtime_error
{
    ErrorCode code;
    ProtocolError(ErrorCode c, const std::string& message) : std::runtime_error(message), code(c) {}
};

// The only way to turn JSON into text for output. Invalid UTF-8 becomes U+FFFD; never throws for that reason.
std::string dump_json(const json& j, int indent = -1);
// j[key] if it is a string; default_value if j is not an object or key is absent or null; bad_request otherwise.
std::string str_or(const json& j, const char* key, std::string_view default_value);
// Short, bounded description of a JSON value for error messages (never dumps a container).
std::string describe(const json& j);
}
