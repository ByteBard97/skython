#include "spy/refs.h"
#include <algorithm>
#include <cctype>
#include <cstdio>

namespace spy {
namespace {
bool ends_with_ci(std::string_view s, std::string_view suffix)
{
    if (s.size() < suffix.size()) return false;
    return std::equal(suffix.begin(), suffix.end(), s.end() - suffix.size(),
        [](char a, char b) { return ascii_lower(a) == ascii_lower(b); });  // ASCII only: locale-independent
}

bool is_blank(char c) { return c == ' ' || c == '\t'; }
bool is_control(char c) { return static_cast<unsigned char>(c) < 0x20 || c == 0x7F; }
}

std::optional<FormKey> parse_form_key(std::string_view s)
{
    const auto bar = s.find('|');
    if (bar == std::string_view::npos || bar == 0) return std::nullopt;
    const auto plugin = s.substr(0, bar);
    if (std::any_of(plugin.begin(), plugin.end(), is_control)) return std::nullopt;
    std::string_view ext;
    for (const auto e : form_ids::kPluginExtensions)
        if (ends_with_ci(plugin, e)) { ext = e; break; }
    if (ext.empty()) return std::nullopt;
    const bool light = ext == form_ids::kLightExtension;
    const auto stem = plugin.substr(0, plugin.size() - ext.size());
    if (stem.empty() || is_blank(stem.front()) || is_blank(stem.back())) return std::nullopt;
    auto id = s.substr(bar + 1);
    if (id.size() < 3 || id.size() > 2 + form_ids::kMaxKeyHexDigits || id[0] != '0' || (id[1] != 'x' && id[1] != 'X')) return std::nullopt;
    id.remove_prefix(2);
    std::uint64_t v = 0;
    for (char c : id) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) return std::nullopt;
        v = v * 16 + static_cast<std::uint64_t>(std::isdigit(static_cast<unsigned char>(c)) ? c - '0' : ascii_lower(c) - 'a' + 10);
    }
    if (v > (light ? form_ids::kMaxLightLocalId : form_ids::kMaxLocalId)) return std::nullopt;
    return FormKey{std::string(plugin), static_cast<std::uint32_t>(v)};
}

std::string format_form_key(const FormKey& k)
{
    char buf[16];
    std::snprintf(buf, sizeof buf, "0x%0*X", form_ids::kCanonicalHexDigits, k.local_id);
    return k.plugin + "|" + buf;
}

bool is_valid_form_key(std::string_view s) { return parse_form_key(s).has_value(); }
bool is_runtime_form_id(std::uint32_t id) { return (id >> 24) == form_ids::kRuntimePrefix; }
bool is_light_form_id(std::uint32_t id) { return (id >> 24) == form_ids::kLightPrefix; }
std::uint32_t local_form_id(std::uint32_t id)
{
    return is_light_form_id(id) ? (id & form_ids::kMaxLightLocalId) : (id & form_ids::kMaxLocalId);
}

bool form_key_matches_id(std::string_view key, std::uint32_t form_id)
{
    const auto k = parse_form_key(key);
    return k && !is_runtime_form_id(form_id) && k->local_id == local_form_id(form_id);
}

RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live)
{
    if (!claimed.form_id || !is_runtime_form_id(*claimed.form_id) || !claimed.fingerprint) return RefStatus::ok;
    return *claimed.fingerprint == live ? RefStatus::ok : RefStatus::stale;
}
}
