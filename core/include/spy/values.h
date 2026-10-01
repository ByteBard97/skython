#pragma once
#include "spy/errors.h"  // json, dump_json, str_or, describe, ProtocolError
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace spy {
struct Fingerprint
{
    std::string base;  // base form's form key, or its decimal form id if it has no key
    std::string type;  // Papyrus script type of the reference
    bool operator==(const Fingerprint&) const = default;
};

struct FormRef
{
    std::optional<std::uint32_t> form_id;
    std::optional<std::string> form_key;
    std::string type;  // empty when unknown
    std::optional<std::uint32_t> epoch;
    std::optional<Fingerprint> fingerprint;
};

struct AliasRef
{
    FormRef quest;
    std::int32_t alias_id = 0;  // 0 .. INT32_MAX
    std::string alias_type;  // "ReferenceAlias" or "LocationAlias"
    std::optional<std::uint32_t> epoch;
};

struct Value;
struct ArrayValue
{
    std::string elem_type;  // Bool Int Float String Form Alias
    std::vector<Value> items;
};

struct Value
{
    std::variant<std::monostate, bool, std::int32_t, float, std::string, FormRef, AliasRef, ArrayValue> v;
};

bool is_valid_form_key(std::string_view s);  // implemented in refs.cpp (Task 15)
// True when `key` can name `form_id`: same local id (low 24 bits, low 12 for a 0xFE light form id), and `form_id`
// is not a runtime-created (0xFF) id, which has no form key. Implemented in refs.cpp (Task 15).
bool form_key_matches_id(std::string_view key, std::uint32_t form_id);
// True for a JSON integer in [0, max]. JSON built in C++ stores small non-negative ints as signed, so is_number_unsigned() is not enough.
bool is_uint(const json& j, std::uint64_t max);

// Arrays: at most kMaxArrayItems items in either direction, else type_mismatch.
Value value_from_json(const json& j);
json value_to_json(const Value& v);
FormRef form_ref_from_json(const json& j);
json form_ref_to_json(const FormRef& r);
AliasRef alias_ref_from_json(const json& j);
json alias_ref_to_json(const AliasRef& r);
std::string_view value_kind(const Value& v);
}
