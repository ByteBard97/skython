# Task 14: Errors and the value codec

Adapted from: branch `plan-out-of-process`, task 10 (`task-10-errors-and-the-value-codec.md`).

> Part of the [Skython v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 13 (and through it Tasks 0 and 12). Linux only; no game.

**What changed from the out-of-process plan.** The JSON helpers (`json`, `dump_json`, `str_or`, `describe`) moved from `values.h` to `errors.h`, as the index Contracts list them, so code that only builds error responses (Task 16's `make_error`, Task 18's dispatcher) does not need the value types. New rule: a Papyrus array holds at most `kMaxArrayItems` (128) items; a longer array is `type_mismatch` when it is decoded (`value_from_json`) and when it is encoded (`value_to_json`), so neither a request nor a result can carry one. Task 11's `values.json` has a fixture on each side of the limit; Task 16's fixture sweep checks them. Everything else is the reviewed code from the branch, which also passed under AddressSanitizer and UBSan and compiled on the build box.

**Files:**
- Create: `core/include/spy/errors.h`, `core/src/errors.cpp`, `core/include/spy/values.h`, `core/src/values.cpp`, `core/tests/test_values.cpp`
- Consumes (generated, Task 12): `core/include/spy/protocol_generated.h` for the `ErrorCode` enum and its name table `kErrorCodeNames`, `kIntMin`/`kIntMax`, `kFloatMaxText`, `kMaxArrayItems`, `kAliasTypes`/`kDefaultAliasType`/`kArrayElemTypes`. Nothing below spells out an error code, an alias type or a numeric limit.

**Interfaces:**
- Produces (`errors.h`):
  - `enum class spy::ErrorCode` (defined in the generated header, in the order of `protocol.json`: `not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference, not_found, timeout, vm_error, unsupported, internal, unauthorized, protocol_mismatch`), re-exported by `errors.h`
  - `std::string_view spy::to_string(ErrorCode)` and `std::optional<ErrorCode> spy::error_code_from_string(std::string_view)`: lookups over the generated `kErrorCodeNames` table (`to_string` of a value outside the table is `"internal"`)
  - `struct spy::ProtocolError : std::runtime_error { ErrorCode code; ProtocolError(ErrorCode, const std::string&); }`
  - `std::string spy::clip_text(std::string_view s, std::size_t max = 120)`: at most `max` bytes (never splitting a UTF-8 sequence: it backs up at most 3 bytes, and only to a real lead byte whose sequence crosses the cut, so Windows-1252 bytes 0x80-0xBF do not shorten the text) plus `"..."` when cut. Every message that quotes caller input goes through it.
  - `using spy::json = nlohmann::json;`
  - `std::string spy::dump_json(const json&, int indent = -1)`: **the only way core or the plugin turns JSON into text.** It uses `error_handler_t::replace`, so invalid UTF-8 in a game string becomes U+FFFD instead of throwing `json::type_error` 316. Never call `json::dump()` directly on outgoing data.
  - `std::string spy::str_or(const json& j, const char* key, std::string_view default_value)`: `default_value` when `j` is not an object or `key` is missing or `null`; the string when it is a string; otherwise throws `ProtocolError(bad_request)`. Use it instead of `j.value(key, "")`, which throws `json::type_error` for a non-string.
  - `std::string spy::describe(const json&)`: a short, bounded description of a JSON value for error messages (scalars verbatim, strings clipped to 64 bytes, containers as "an array of N items" / "an object").
- Produces (`values.h`):
  - `struct spy::Fingerprint { std::string base; std::string type; }`
  - `struct spy::FormRef { std::optional<std::uint32_t> form_id; std::optional<std::string> form_key; std::string type; std::optional<std::uint32_t> epoch; std::optional<Fingerprint> fingerprint; }`
  - `struct spy::AliasRef { FormRef quest; std::int32_t alias_id; std::string alias_type; std::optional<std::uint32_t> epoch; }` (`alias_id` is 0..2147483647)
  - `struct spy::ArrayValue { std::string elem_type; std::vector<Value> items; }`
  - `struct spy::Value { std::variant<std::monostate, bool, std::int32_t, float, std::string, FormRef, AliasRef, ArrayValue> v; }`
  - `Value spy::value_from_json(const json&)`, `json spy::value_to_json(const Value&)`, `FormRef spy::form_ref_from_json(const json&)`, `json spy::form_ref_to_json(const FormRef&)`, `AliasRef spy::alias_ref_from_json(const json&)`, `json spy::alias_ref_to_json(const AliasRef&)`, `std::string_view spy::value_kind(const Value&)` (returns `"null","bool","int","float","string","form","alias","array"`), `bool spy::is_uint(const json&, std::uint64_t max)`
  - Float rules: a finite double above `kFloatMaxText` (3.4028235e38) in magnitude is `type_mismatch` (also for array items); `value_to_json` prints a float in its shortest round-trip text (0.1f is `0.1`, not `0.10000000149011612`) and a non-finite float as `null`.
  - Array rule: more than `kMaxArrayItems` items is `type_mismatch` in `value_from_json` and in `value_to_json`.
  - A form reference with both `form_id` and `form_key` must agree (Task 15's `form_key_matches_id`); `type`, `epoch`, `fingerprint` and `alias_type` of the wrong JSON type are `bad_request`, not ignored.
- Consumes: `bool spy::is_valid_form_key(std::string_view)` and `bool spy::form_key_matches_id(std::string_view key, std::uint32_t form_id)` from Task 15 (`refs.cpp`). To keep this task self-contained, `values.cpp` ends with temporary definitions of both, marked `// replaced in Task 15` (the first accepts every key, the second accepts every pair); Task 15 Step 3 deletes them. **Do not skip that deletion:** on the build box MSVC links the duplicate definitions with only warning LNK4006 and may keep the always-true stubs, which would silently disable form-key validation in the plugin.

- [ ] **Step 1: Write the failing tests**

`core/tests/test_values.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/values.h"
#include <cfloat>
#include <limits>

using spy::json;

static spy::ErrorCode code_of(const json& j)
{
    try {
        spy::value_from_json(j);
    } catch (const spy::ProtocolError& e) {
        return e.code;
    }
    FAIL("expected ProtocolError");
    return spy::ErrorCode::internal;
}

static std::string message_of(const json& j)
{
    try {
        spy::value_from_json(j);
    } catch (const spy::ProtocolError& e) {
        return e.what();
    }
    FAIL("expected ProtocolError");
    return {};
}

static std::string roundtrip_text(const json& wire)
{
    return spy::dump_json(spy::value_to_json(spy::value_from_json(wire)));
}

TEST_CASE("scalars decode to the right kinds")
{
    CHECK(spy::value_kind(spy::value_from_json(nullptr)) == "null");
    CHECK(spy::value_kind(spy::value_from_json(true)) == "bool");
    CHECK(spy::value_kind(spy::value_from_json(7)) == "int");
    CHECK(spy::value_kind(spy::value_from_json(1.5)) == "float");
    CHECK(spy::value_kind(spy::value_from_json("x")) == "string");
}

TEST_CASE("Int range is enforced, never truncated")
{
    CHECK(std::get<std::int32_t>(spy::value_from_json(2147483647).v) == 2147483647);
    CHECK(std::get<std::int32_t>(spy::value_from_json(-2147483648LL).v) == -2147483647 - 1);
    CHECK(code_of(json(2147483648LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(-2147483649LL)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(4294967296ULL)) == spy::ErrorCode::type_mismatch);
}

TEST_CASE("Float: out of 32-bit range is type_mismatch, the largest float survives a round trip")
{
    CHECK(code_of(json(3.5e38)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(-3.5e38)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(1e300)) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", "Float"}, {"items", {1.5, 3.5e38}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", "Float"}, {"items", {1e39}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json(std::numeric_limits<double>::infinity())) == spy::ErrorCode::type_mismatch);
    // The shortest text of FLT_MAX is 3.4028235e38, which is slightly above FLT_MAX as a double; it must be accepted back.
    const auto max_text = spy::dump_json(spy::value_to_json(spy::Value{FLT_MAX}));
    CHECK(std::get<float>(spy::value_from_json(json::parse(max_text)).v) == FLT_MAX);
    CHECK(std::get<float>(spy::value_from_json(json::parse("-" + max_text)).v) == -FLT_MAX);
    CHECK(std::get<float>(spy::value_from_json(1e-50).v) == 0.0f);  // underflow is not an error
}

TEST_CASE("Float is written in shortest round-trip text and non-finite floats become null")
{
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{0.1f})) == "0.1");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{1.0f})) == "1.0");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{16777216.0f})) == "16777216.0");
    CHECK(spy::dump_json(spy::value_to_json(spy::Value{-2.5f})) == "-2.5");
    CHECK(spy::value_to_json(spy::Value{std::numeric_limits<float>::infinity()}).is_null());
    CHECK(spy::value_to_json(spy::Value{std::numeric_limits<float>::quiet_NaN()}).is_null());
    CHECK(roundtrip_text(json(0.1)) == "0.1");
    CHECK(roundtrip_text(json::parse(R"({"array":"Float","items":[1,2.5]})")) == R"({"array":"Float","items":[1.0,2.5]})");
    CHECK(spy::value_to_json(spy::Value{std::int32_t{5}}).is_number_integer());
}

TEST_CASE("form references need an id or a key")
{
    auto v = spy::value_from_json(json{{"kind", "form"}, {"form_id", 20}});
    CHECK(std::get<spy::FormRef>(v.v).form_id.value() == 20u);
    CHECK(code_of(json{{"kind", "form"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", "form"}, {"form_id", -1}}) == spy::ErrorCode::bad_request);
    CHECK(std::get<spy::FormRef>(spy::value_from_json(json::parse(R"({"kind":"form","form_id":20})")).v).form_id.value() == 20u);
}

TEST_CASE("wrong JSON types in optional form fields are bad_request, never ignored and never a json exception")
{
    const json base = {{"kind", "form"}, {"form_id", 20}};
    auto with = [&](const char* key, json v) { auto j = base; j[key] = std::move(v); return j; };
    CHECK(code_of(with("type", 5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", "1")) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", -1)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("epoch", 4294967296ULL)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("fingerprint", "x")) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("fingerprint", json{{"base", "a"}})) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("form_key", 5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(with("form_id", "20")) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", 5}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", json::array()}, {"form_id", 1}}) == spy::ErrorCode::bad_request);
    // null means absent
    auto ok = spy::value_from_json(with("type", nullptr));
    CHECK(std::get<spy::FormRef>(ok.v).type.empty());
}

TEST_CASE("arrays are typed and bare JSON arrays are rejected")
{
    auto v = spy::value_from_json(json{{"array", "Float"}, {"items", {1, 2.5}}});
    auto& a = std::get<spy::ArrayValue>(v.v);
    CHECK(a.elem_type == "Float");
    CHECK(std::get<float>(a.items[0].v) == doctest::Approx(1.0));
    CHECK(code_of(json::array({1, 2})) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"array", "Int"}, {"items", {1, "x"}}}) == spy::ErrorCode::type_mismatch);
    CHECK(code_of(json{{"array", 5}, {"items", json::array()}}) == spy::ErrorCode::bad_request);
}

TEST_CASE("alias types are restricted")
{
    json bad = {{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", 0}, {"alias_type", "MagicAlias"}};
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
    bad["alias_type"] = 5;
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
}

TEST_CASE("alias_id is range-checked, not truncated")
{
    auto alias = [](json id) {
        return json{{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", std::move(id)}, {"alias_type", "ReferenceAlias"}};
    };
    CHECK(std::get<spy::AliasRef>(spy::value_from_json(alias(0)).v).alias_id == 0);
    CHECK(std::get<spy::AliasRef>(spy::value_from_json(alias(2147483647)).v).alias_id == 2147483647);
    CHECK(code_of(alias(-1)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias(2147483648LL)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias(1099511627777LL)) == spy::ErrorCode::bad_request);  // would truncate to 1
    CHECK(code_of(alias(1.5)) == spy::ErrorCode::bad_request);
    CHECK(code_of(alias("0")) == spy::ErrorCode::bad_request);
}

TEST_CASE("str_or returns the default for absent or null and throws bad_request for non-strings")
{
    CHECK(spy::str_or(json{{"a", "x"}}, "a", "d") == "x");
    CHECK(spy::str_or(json::object(), "a", "d") == "d");
    CHECK(spy::str_or(json{{"a", nullptr}}, "a", "d") == "d");
    CHECK(spy::str_or(json::array(), "a", "d") == "d");  // not an object: no exception from nlohmann
    CHECK_THROWS_AS(spy::str_or(json{{"a", 5}}, "a", "d"), spy::ProtocolError);
    CHECK_THROWS_AS(spy::str_or(json{{"a", json::object()}}, "a", "d"), spy::ProtocolError);
}

TEST_CASE("invalid UTF-8 is replaced on output instead of throwing")
{
    json j = {{"s", std::string("caf\xE9 \xFF")}};  // Windows-1252 bytes
    CHECK_THROWS_AS(j.dump(), json::type_error);   // what a bare dump() does
    std::string out;
    CHECK_NOTHROW(out = spy::dump_json(j));
    CHECK(out == "{\"s\":\"caf\xEF\xBF\xBD \xEF\xBF\xBD\"}");
    CHECK(spy::dump_json(json{{"a", 1}}, 2) == "{\n  \"a\": 1\n}");
    CHECK_NOTHROW(spy::dump_json(spy::value_to_json(spy::Value{std::string("\xE9")})));
}

TEST_CASE("clip_text bounds quoted input and never splits a UTF-8 sequence")
{
    CHECK(spy::clip_text("short") == "short");
    CHECK(spy::clip_text(std::string(500, 'x'), 10) == "xxxxxxxxxx...");
    const std::string accents = "\xC3\xA9\xC3\xA9\xC3\xA9";  // three 2-byte characters
    CHECK(spy::clip_text(accents, 3) == "\xC3\xA9...");       // byte 3 would split the second character
    CHECK(spy::clip_text(std::string(2000000, 'x')).size() == 123);
}

TEST_CASE("clip_text backs up at most 3 bytes and only over a real UTF-8 sequence")
{
    // Game strings are often Windows-1252, whose punctuation (0x92 is a right single quote) looks like UTF-8
    // continuation bytes. They must not make the clip eat the whole message.
    const std::string cp1252 = "Name: " + std::string(300, '\x92');
    CHECK(spy::clip_text(cp1252, 120) == cp1252.substr(0, 120) + "...");
    const std::string emoji = "x\xF0\x9F\x98\x80y";             // a 4-byte character between two letters
    CHECK(spy::clip_text(emoji, 2) == "x...");                    // the cut is inside it: back up to its lead byte
    CHECK(spy::clip_text(emoji, 4) == "x...");                    // three bytes back is still inside it
    CHECK(spy::clip_text(emoji, 5) == "x\xF0\x9F\x98\x80...");  // the cut falls after the whole character
    CHECK(spy::clip_text("\xC3\xA9\x92\x92", 2) == "\xC3\xA9...");   // a stray continuation byte after a whole character
}

TEST_CASE("error messages never echo a large input")
{
    const std::string big(3 * 1024 * 1024, 'x');
    CHECK(message_of(json{{"array", "Int"}, {"items", {big}}}).size() < 300);
    CHECK(message_of(json{{"array", "Int"}, {"items", {json::array({1, 2, 3})}}}).size() < 300);
    CHECK(message_of(json{{"kind", big}}).size() < 300);
    CHECK(spy::describe(json(big)).size() < 100);
    CHECK(spy::describe(json::array({1, 2})) == "an array of 2 items");
    CHECK(spy::describe(json::object()) == "an object");
    CHECK(spy::describe(json(42)) == "42");
}

TEST_CASE("every error code in the generated table round trips through its string")
{
    for (const auto& e : spy::kErrorCodeNames) {
        CHECK(spy::to_string(e.code) == e.name);
        CHECK(spy::error_code_from_string(e.name).value() == e.code);
    }
    CHECK_FALSE(spy::error_code_from_string("nope").has_value());
    CHECK_FALSE(spy::error_code_from_string("").has_value());
    CHECK(spy::to_string(static_cast<spy::ErrorCode>(9999)) == "internal");
}

TEST_CASE("an array holds at most kMaxArrayItems items, in both directions")
{
    json items = json::array();
    for (std::size_t i = 0; i < spy::kMaxArrayItems; ++i) items.push_back(static_cast<int>(i));
    CHECK(std::get<spy::ArrayValue>(spy::value_from_json(json{{"array", "Int"}, {"items", items}}).v).items.size() == spy::kMaxArrayItems);
    items.push_back(0);
    CHECK(code_of(json{{"array", "Int"}, {"items", items}}) == spy::ErrorCode::type_mismatch);
    CHECK(message_of(json{{"array", "Int"}, {"items", items}}).find("at most") != std::string::npos);

    spy::ArrayValue out{"Int", {}};
    for (std::size_t i = 0; i < spy::kMaxArrayItems; ++i) out.items.push_back(spy::Value{std::int32_t{1}});
    CHECK(spy::value_to_json(spy::Value{out})["items"].size() == spy::kMaxArrayItems);
    out.items.push_back(spy::Value{std::int32_t{1}});
    try {
        spy::value_to_json(spy::Value{out});
        FAIL("expected ProtocolError");
    } catch (const spy::ProtocolError& e) {
        CHECK(e.code == spy::ErrorCode::type_mismatch);
    }
}
```
(The full fixture sweep over `values.json` is in Task 16. Form-key agreement and the form-key grammar arrive with Task 15, because they need its parser.)

- [ ] **Step 2: Run to verify it fails**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | grep -m1 "error"`
Expected: FAIL, `fatal error: spy/values.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/errors.h`:
```cpp
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
```

`core/src/errors.cpp`:
```cpp
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
```

`core/include/spy/values.h`:
```cpp
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
```

`core/src/values.cpp`:
```cpp
#include "spy/values.h"
#include <algorithm>
#include <cfloat>
#include <charconv>
#include <cmath>
#include <system_error>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }
[[noreturn]] void mismatch(const std::string& m) { throw ProtocolError(ErrorCode::type_mismatch, m); }

bool present(const json& j, const char* key) { return j.contains(key) && !j[key].is_null(); }

std::int32_t to_int32(const json& j)
{
    if (j.is_number_unsigned()) {
        auto u = j.get<std::uint64_t>();
        if (u > static_cast<std::uint64_t>(kIntMax))
            mismatch("integer " + j.dump() + " is out of Papyrus Int range");
        return static_cast<std::int32_t>(u);
    }
    auto s = j.get<std::int64_t>();
    if (s < kIntMin || s > kIntMax)
        mismatch("integer " + j.dump() + " is out of Papyrus Int range");
    return static_cast<std::int32_t>(s);
}

float to_float(const json& j)
{
    constexpr double kMax = static_cast<double>(FLT_MAX);
    // The shortest text that round-trips FLT_MAX is 3.4028235e38 (kFloatMaxText), which as a double is a little above
    // FLT_MAX. value_to_json writes that text, so it has to be accepted back. Anything larger is out of range.
    constexpr double kMaxText = kFloatMaxText;
    const double d = j.get<double>();
    if (!std::isfinite(d)) mismatch("float must be finite");
    const double a = std::fabs(d);
    if (a <= kMax) return static_cast<float>(d);
    if (a <= kMaxText) return d < 0 ? -FLT_MAX : FLT_MAX;
    mismatch("float " + j.dump() + " is out of Papyrus Float range");
}

// Shortest decimal text that reads back as the same float, then widened to a double that prints the same way.
json float_to_json(float f)
{
    if (!std::isfinite(f)) return nullptr;  // JSON has no NaN or infinity
    char buf[64];
    const auto w = std::to_chars(buf, buf + sizeof buf, f);
    double d = static_cast<double>(f);
    if (w.ec == std::errc()) std::from_chars(buf, w.ptr, d);
    return d;
}

bool is_elem_type(const std::string& t)
{
    return std::find(kArrayElemTypes.begin(), kArrayElemTypes.end(), t) != kArrayElemTypes.end();
}

bool is_alias_type(const std::string& t)
{
    return std::find(kAliasTypes.begin(), kAliasTypes.end(), t) != kAliasTypes.end();
}

Value elem_from_json(const std::string& elem, const json& j)
{
    if (j.is_object() || j.is_array()) {
        if (elem == "Form" && j.is_object() && str_or(j, "kind", "") == "form") return Value{form_ref_from_json(j)};
        if (elem == "Alias" && j.is_object() && str_or(j, "kind", "") == "alias") return Value{alias_ref_from_json(j)};
        mismatch("array of " + elem + " cannot hold " + describe(j));
    }
    if (elem == "Bool" && j.is_boolean()) return Value{j.get<bool>()};
    if (elem == "Int" && j.is_number_integer()) return Value{to_int32(j)};
    if (elem == "Float" && j.is_number()) return Value{to_float(j)};
    if (elem == "String" && j.is_string()) return Value{j.get<std::string>()};
    if ((elem == "Form" || elem == "Alias") && j.is_null()) return Value{};
    mismatch("array of " + elem + " cannot hold " + describe(j));
}
}

bool is_uint(const json& j, std::uint64_t max)
{
    if (j.is_number_unsigned()) return j.get<std::uint64_t>() <= max;
    if (j.is_number_integer()) return j.get<std::int64_t>() >= 0 && static_cast<std::uint64_t>(j.get<std::int64_t>()) <= max;
    return false;
}

FormRef form_ref_from_json(const json& j)
{
    if (!j.is_object() || str_or(j, "kind", "") != "form") bad("expected a form reference");
    FormRef r;
    if (present(j, "form_id")) {
        if (!is_uint(j["form_id"], 0xFFFFFFFFull)) bad("form_id must be an unsigned 32-bit integer");
        r.form_id = j["form_id"].get<std::uint32_t>();
    }
    if (present(j, "form_key")) {
        if (!j["form_key"].is_string() || !is_valid_form_key(j["form_key"].get<std::string>()))
            bad("form_key must look like Plugin.esp|0x0012AB");
        r.form_key = j["form_key"].get<std::string>();
    }
    if (!r.form_id && !r.form_key) bad("a form reference needs form_id or form_key");
    if (r.form_id && r.form_key && !form_key_matches_id(*r.form_key, *r.form_id))
        bad("form_id and form_key do not name the same form");
    r.type = str_or(j, "type", "");
    if (present(j, "epoch")) {
        if (!is_uint(j["epoch"], 0xFFFFFFFFull)) bad("epoch must be an unsigned 32-bit integer");
        r.epoch = j["epoch"].get<std::uint32_t>();
    }
    if (present(j, "fingerprint")) {
        const auto& f = j["fingerprint"];
        if (!f.is_object() || !f.contains("base") || !f["base"].is_string() || !f.contains("type") || !f["type"].is_string())
            bad("fingerprint needs string base and type");
        r.fingerprint = Fingerprint{f["base"].get<std::string>(), f["type"].get<std::string>()};
    }
    return r;
}

json form_ref_to_json(const FormRef& r)
{
    json j = {{"kind", "form"}};
    if (r.form_id) j["form_id"] = *r.form_id;
    if (r.form_key) j["form_key"] = *r.form_key;
    if (!r.type.empty()) j["type"] = r.type;
    if (r.epoch) j["epoch"] = *r.epoch;
    if (r.fingerprint) j["fingerprint"] = {{"base", r.fingerprint->base}, {"type", r.fingerprint->type}};
    return j;
}

AliasRef alias_ref_from_json(const json& j)
{
    if (!j.is_object() || str_or(j, "kind", "") != "alias") bad("expected an alias reference");
    AliasRef a;
    if (!j.contains("quest")) bad("alias reference needs quest");
    a.quest = form_ref_from_json(j["quest"]);
    if (!j.contains("alias_id") || !is_uint(j["alias_id"], static_cast<std::uint64_t>(kIntMax)))
        bad("alias_id must be an integer from 0 to " + std::to_string(kIntMax));
    a.alias_id = static_cast<std::int32_t>(j["alias_id"].get<std::int64_t>());
    a.alias_type = str_or(j, "alias_type", kDefaultAliasType);
    if (!is_alias_type(a.alias_type)) bad("alias_type must be ReferenceAlias or LocationAlias");
    if (present(j, "epoch")) {
        if (!is_uint(j["epoch"], 0xFFFFFFFFull)) bad("epoch must be an unsigned 32-bit integer");
        a.epoch = j["epoch"].get<std::uint32_t>();
    }
    return a;
}

json alias_ref_to_json(const AliasRef& a)
{
    json j = {{"kind", "alias"}, {"quest", form_ref_to_json(a.quest)}, {"alias_id", a.alias_id}, {"alias_type", a.alias_type}};
    if (a.epoch) j["epoch"] = *a.epoch;
    return j;
}

Value value_from_json(const json& j)
{
    if (j.is_null()) return Value{};
    if (j.is_boolean()) return Value{j.get<bool>()};
    if (j.is_number_integer()) return Value{to_int32(j)};
    if (j.is_number_float()) return Value{to_float(j)};
    if (j.is_string()) return Value{j.get<std::string>()};
    if (j.is_array()) bad("bare JSON arrays are not values; use {\"array\": type, \"items\": [...]}");
    if (!j.is_object()) bad("not a value");
    if (j.contains("array")) {
        if (!j["array"].is_string() || !is_elem_type(j["array"].get<std::string>())) bad("unknown array element type");
        if (!j.contains("items") || !j["items"].is_array()) bad("array needs items");
        if (j["items"].size() > kMaxArrayItems)
            mismatch("a Papyrus array holds at most " + std::to_string(kMaxArrayItems) + " items, got " + std::to_string(j["items"].size()));
        ArrayValue a{j["array"].get<std::string>(), {}};
        for (const auto& item : j["items"]) a.items.push_back(elem_from_json(a.elem_type, item));
        return Value{std::move(a)};
    }
    const auto kind = str_or(j, "kind", "");
    if (kind == "form") return Value{form_ref_from_json(j)};
    if (kind == "alias") return Value{alias_ref_from_json(j)};
    bad("unknown value object (kind '" + clip_text(kind, 32) + "')");
}

json value_to_json(const Value& v)
{
    struct Visitor
    {
        json operator()(std::monostate) const { return nullptr; }
        json operator()(bool b) const { return b; }
        json operator()(std::int32_t i) const { return i; }
        json operator()(float f) const { return float_to_json(f); }
        json operator()(const std::string& s) const { return s; }
        json operator()(const FormRef& r) const { return form_ref_to_json(r); }
        json operator()(const AliasRef& a) const { return alias_ref_to_json(a); }
        json operator()(const ArrayValue& a) const
        {
            if (a.items.size() > kMaxArrayItems)
                mismatch("a Papyrus array holds at most " + std::to_string(kMaxArrayItems) + " items, got " + std::to_string(a.items.size()));
            json items = json::array();
            for (const auto& item : a.items) items.push_back(value_to_json(item));
            return json{{"array", a.elem_type}, {"items", items}};
        }
    };
    return std::visit(Visitor{}, v.v);
}

std::string_view value_kind(const Value& v)
{
    static constexpr std::string_view names[] = {"null", "bool", "int", "float", "string", "form", "alias", "array"};
    return names[v.v.index()];
}

bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 15
bool form_key_matches_id(std::string_view, std::uint32_t) { return true; }  // replaced in Task 15
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && core/build/spy_core_tests`
Expected: PASS, `[doctest] test cases:  16 |  16 passed | 0 failed | 0 skipped` and `[doctest] assertions: 118 | 118 passed | 0 failed |`.

- [ ] **Step 5: Commit**

```bash
git add core/include/spy/errors.h core/src/errors.cpp core/include/spy/values.h core/src/values.cpp core/tests/test_values.cpp
git commit -m "feat(core): error codes and Papyrus value codec: Int, Float and array-size checks, typed-field errors, UTF-8-safe output"
```
