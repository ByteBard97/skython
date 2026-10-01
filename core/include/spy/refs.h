#pragma once
#include "spy/values.h"
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace spy {
struct FormKey
{
    std::string plugin;
    std::uint32_t local_id = 0;
};

std::optional<FormKey> parse_form_key(std::string_view s);
std::string format_form_key(const FormKey& k);
bool is_runtime_form_id(std::uint32_t id);
bool is_light_form_id(std::uint32_t id);
std::uint32_t local_form_id(std::uint32_t form_id);

enum class RefStatus { ok, stale };
RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live);
}
