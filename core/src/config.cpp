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

std::string ignored_entry_label(std::string_view entry)
{
    static constexpr std::string_view kToken = "token";
    for (std::size_t i = 0; i + kToken.size() <= entry.size(); ++i) {
        bool match = true;
        for (std::size_t j = 0; j < kToken.size() && match; ++j) {
            char ch = entry[i + j];
            if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch - 'A' + 'a');  // ASCII only: no locale
            match = ch == kToken[j];
        }
        if (match) return "<redacted>";
    }
    return clip_text(entry.substr(0, entry.find_first_of(" \t:=")), 64);
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
            c.ignored_keys.push_back(ignored_entry_label(line));
            continue;
        }
        auto key = trim(line.substr(0, eq));
        auto val = trim(strip_inline_comment(std::string_view(line).substr(eq + 1)));
        const ConfigKey* spec = find_config_key(key);  // names, ranges and defaults come from protocol.json
        if (spec == nullptr) {
            if (!key.empty()) c.ignored_keys.push_back(ignored_entry_label(key));
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
