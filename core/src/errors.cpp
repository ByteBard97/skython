#include "spy/errors.h"

namespace spy {
std::string_view to_string(ErrorCode code)
{
    for (const auto& e : kErrorCodeNames)
        if (e.code == code) return e.name;
    return "internal";
}

std::optional<ErrorCode> error_code_from_string(std::string_view s)
{
    for (const auto& e : kErrorCodeNames)
        if (e.name == s) return e.code;
    return std::nullopt;
}

namespace {
// Length of the UTF-8 sequence a lead byte starts (2-4), or 0 for any other byte.
std::size_t utf8_sequence_length(unsigned char lead)
{
    if ((lead & 0xE0) == 0xC0) return 2;
    if ((lead & 0xF0) == 0xE0) return 3;
    if ((lead & 0xF8) == 0xF0) return 4;
    return 0;
}
}

std::string clip_text(std::string_view s, std::size_t max)
{
    if (s.size() <= max) return std::string(s);
    std::size_t cut = max;
    // If the cut splits a UTF-8 sequence, move it back to that sequence's lead byte: at most 3 bytes, and only when a
    // real lead byte starts a sequence that runs past the cut. Windows-1252 bytes 0x80-0xBF look like continuation
    // bytes; backing up over them without a bound would eat the whole message.
    if ((static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) {
        for (std::size_t back = 1; back <= 3 && back <= cut; ++back) {
            const auto b = static_cast<unsigned char>(s[cut - back]);
            if ((b & 0xC0) == 0x80) continue;                     // another continuation byte: keep looking
            if (utf8_sequence_length(b) > back) cut -= back;      // a lead byte whose sequence crosses the cut
            break;
        }
    }
    return std::string(s.substr(0, cut)) + "...";
}

std::string dump_json(const json& j, int indent)
{
    return j.dump(indent, ' ', false, json::error_handler_t::replace);
}

std::string str_or(const json& j, const char* key, std::string_view default_value)
{
    if (!j.is_object() || !j.contains(key) || j[key].is_null()) return std::string(default_value);
    const json& v = j[key];
    if (!v.is_string()) throw ProtocolError(ErrorCode::bad_request, std::string("field '") + key + "' must be a string");
    return v.get<std::string>();
}

std::string describe(const json& j)
{
    if (j.is_string()) return dump_json(json(clip_text(j.get_ref<const std::string&>(), 64)));
    if (j.is_array()) return "an array of " + std::to_string(j.size()) + " items";
    if (j.is_object()) return "an object";
    return dump_json(j);
}
}
