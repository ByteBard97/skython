# Task 10: Errors and the value codec

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `core/include/spy/errors.h`, `core/src/errors.cpp`, `core/include/spy/values.h`, `core/src/values.cpp`, `core/tests/test_values.cpp`

**Interfaces:**
- Produces:
  - `enum class spy::ErrorCode { not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference, not_found, timeout, vm_error, unsupported, internal, unauthorized, protocol_mismatch }`
  - `std::string_view spy::to_string(ErrorCode)`; `std::optional<ErrorCode> spy::error_code_from_string(std::string_view)`
  - `struct spy::ProtocolError : std::runtime_error { ErrorCode code; ProtocolError(ErrorCode, const std::string&); }`
  - `using spy::json = nlohmann::json;`
  - `struct spy::Fingerprint { std::string base; std::string type; }`
  - `struct spy::FormRef { std::optional<std::uint32_t> form_id; std::optional<std::string> form_key; std::string type; std::optional<std::uint32_t> epoch; std::optional<Fingerprint> fingerprint; }`
  - `struct spy::AliasRef { FormRef quest; std::int32_t alias_id; std::string alias_type; std::optional<std::uint32_t> epoch; }`
  - `struct spy::ArrayValue { std::string elem_type; std::vector<Value> items; }`
  - `struct spy::Value { std::variant<std::monostate, bool, std::int32_t, float, std::string, FormRef, AliasRef, ArrayValue> v; }`
  - `Value spy::value_from_json(const json&)`, `json spy::value_to_json(const Value&)`, `FormRef spy::form_ref_from_json(const json&)`, `json spy::form_ref_to_json(const FormRef&)`, `AliasRef spy::alias_ref_from_json(const json&)`, `json spy::alias_ref_to_json(const AliasRef&)`, `std::string_view spy::value_kind(const Value&)` (returns `"null","bool","int","float","string","form","alias","array"`)
- Consumes: `spy::parse_form_key` from Task 11 is needed to validate `form_key`; to keep this task self-contained, `values.cpp` declares and uses `bool spy::is_valid_form_key(std::string_view)` which Task 11 implements in `refs.cpp`. Until Task 11 lands, add a temporary definition in `values.cpp` marked `// replaced in Task 11` that returns true, and delete it in Task 11 Step 3.

- [ ] **Step 1: Write the failing tests**

`core/tests/test_values.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/values.h"

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

TEST_CASE("form references need an id or a key")
{
    auto v = spy::value_from_json(json{{"kind", "form"}, {"form_id", 20}});
    CHECK(std::get<spy::FormRef>(v.v).form_id.value() == 20u);
    CHECK(code_of(json{{"kind", "form"}}) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"kind", "form"}, {"form_id", -1}}) == spy::ErrorCode::bad_request);
    CHECK(std::get<spy::FormRef>(spy::value_from_json(json::parse(R"({"kind":"form","form_id":20})")).v).form_id.value() == 20u);
}

TEST_CASE("arrays are typed and bare JSON arrays are rejected")
{
    auto v = spy::value_from_json(json{{"array", "Float"}, {"items", {1, 2.5}}});
    auto& a = std::get<spy::ArrayValue>(v.v);
    CHECK(a.elem_type == "Float");
    CHECK(std::get<float>(a.items[0].v) == doctest::Approx(1.0));
    CHECK(code_of(json::array({1, 2})) == spy::ErrorCode::bad_request);
    CHECK(code_of(json{{"array", "Int"}, {"items", {1, "x"}}}) == spy::ErrorCode::type_mismatch);
}

TEST_CASE("alias types are restricted")
{
    json bad = {{"kind", "alias"}, {"quest", {{"kind", "form"}, {"form_id", 1}}}, {"alias_id", 0}, {"alias_type", "MagicAlias"}};
    CHECK(code_of(bad) == spy::ErrorCode::bad_request);
}

TEST_CASE("error codes round trip through strings")
{
    for (auto c : {spy::ErrorCode::not_ready, spy::ErrorCode::stale_reference, spy::ErrorCode::protocol_mismatch})
        CHECK(spy::error_code_from_string(spy::to_string(c)).value() == c);
    CHECK_FALSE(spy::error_code_from_string("nope").has_value());
}
```
(The full fixture sweep over `values.json` is in Task 12.)

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/values.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/errors.h`:
```cpp
#pragma once
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>

namespace spy {
enum class ErrorCode {
    not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference,
    not_found, timeout, vm_error, unsupported, internal, unauthorized, protocol_mismatch
};

std::string_view to_string(ErrorCode code);
std::optional<ErrorCode> error_code_from_string(std::string_view s);

struct ProtocolError : std::runtime_error
{
    ErrorCode code;
    ProtocolError(ErrorCode c, const std::string& message) : std::runtime_error(message), code(c) {}
};
}
```

`core/src/errors.cpp`:
```cpp
#include "spy/errors.h"
#include <array>
#include <utility>

namespace spy {
namespace {
constexpr std::array<std::pair<ErrorCode, std::string_view>, 13> kNames{{
    {ErrorCode::not_ready, "not_ready"}, {ErrorCode::busy, "busy"},
    {ErrorCode::bad_request, "bad_request"}, {ErrorCode::unknown_function, "unknown_function"},
    {ErrorCode::type_mismatch, "type_mismatch"}, {ErrorCode::stale_reference, "stale_reference"},
    {ErrorCode::not_found, "not_found"}, {ErrorCode::timeout, "timeout"},
    {ErrorCode::vm_error, "vm_error"}, {ErrorCode::unsupported, "unsupported"},
    {ErrorCode::internal, "internal"}, {ErrorCode::unauthorized, "unauthorized"},
    {ErrorCode::protocol_mismatch, "protocol_mismatch"},
}};
}

std::string_view to_string(ErrorCode code)
{
    for (auto& [c, n] : kNames)
        if (c == code) return n;
    return "internal";
}

std::optional<ErrorCode> error_code_from_string(std::string_view s)
{
    for (auto& [c, n] : kNames)
        if (n == s) return c;
    return std::nullopt;
}
}
```

`core/include/spy/values.h`:
```cpp
#pragma once
#include "spy/errors.h"
#include <nlohmann/json.hpp>
#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace spy {
using json = nlohmann::json;

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
    std::int32_t alias_id = 0;
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

bool is_valid_form_key(std::string_view s);  // implemented in refs.cpp (Task 11)
// True for a JSON integer in [0, max]. JSON built in C++ stores small non-negative ints as signed, so is_number_unsigned() is not enough.
bool is_uint(const json& j, std::uint64_t max);

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
#include <cmath>
#include <limits>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }
[[noreturn]] void mismatch(const std::string& m) { throw ProtocolError(ErrorCode::type_mismatch, m); }

std::int32_t to_int32(const json& j)
{
    if (j.is_number_unsigned()) {
        auto u = j.get<std::uint64_t>();
        if (u > static_cast<std::uint64_t>(std::numeric_limits<std::int32_t>::max()))
            mismatch("integer " + j.dump() + " is out of Papyrus Int range");
        return static_cast<std::int32_t>(u);
    }
    auto s = j.get<std::int64_t>();
    if (s < std::numeric_limits<std::int32_t>::min() || s > std::numeric_limits<std::int32_t>::max())
        mismatch("integer " + j.dump() + " is out of Papyrus Int range");
    return static_cast<std::int32_t>(s);
}

bool is_elem_type(const std::string& t)
{
    return t == "Bool" || t == "Int" || t == "Float" || t == "String" || t == "Form" || t == "Alias";
}

Value elem_from_json(const std::string& elem, const json& j)
{
    if (j.is_object() || j.is_array()) {
        if (elem == "Form" && j.is_object() && j.value("kind", "") == "form") return Value{form_ref_from_json(j)};
        if (elem == "Alias" && j.is_object() && j.value("kind", "") == "alias") return Value{alias_ref_from_json(j)};
        mismatch("array of " + elem + " cannot hold " + j.dump());
    }
    if (elem == "Bool" && j.is_boolean()) return Value{j.get<bool>()};
    if (elem == "Int" && j.is_number_integer()) return Value{to_int32(j)};
    if (elem == "Float" && j.is_number()) return Value{j.get<float>()};
    if (elem == "String" && j.is_string()) return Value{j.get<std::string>()};
    if ((elem == "Form" || elem == "Alias") && j.is_null()) return Value{};
    mismatch("array of " + elem + " cannot hold " + j.dump());
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
    if (!j.is_object() || j.value("kind", "") != "form") bad("expected a form reference");
    FormRef r;
    if (j.contains("form_id")) {
        if (!is_uint(j["form_id"], 0xFFFFFFFFull)) bad("form_id must be an unsigned 32-bit integer");
        r.form_id = j["form_id"].get<std::uint32_t>();
    }
    if (j.contains("form_key") && !j["form_key"].is_null()) {
        if (!j["form_key"].is_string() || !is_valid_form_key(j["form_key"].get<std::string>()))
            bad("form_key must look like Plugin.esp|0x0012AB");
        r.form_key = j["form_key"].get<std::string>();
    }
    if (!r.form_id && !r.form_key) bad("a form reference needs form_id or form_key");
    if (j.contains("type") && j["type"].is_string()) r.type = j["type"].get<std::string>();
    if (j.contains("epoch") && is_uint(j["epoch"], 0xFFFFFFFFull)) r.epoch = j["epoch"].get<std::uint32_t>();
    if (j.contains("fingerprint") && j["fingerprint"].is_object()) {
        const auto& f = j["fingerprint"];
        if (!f.contains("base") || !f["base"].is_string() || !f.contains("type") || !f["type"].is_string())
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
    if (!j.is_object() || j.value("kind", "") != "alias") bad("expected an alias reference");
    AliasRef a;
    if (!j.contains("quest")) bad("alias reference needs quest");
    a.quest = form_ref_from_json(j["quest"]);
    if (!j.contains("alias_id") || !j["alias_id"].is_number_integer()) bad("alias_id must be an integer");
    a.alias_id = j["alias_id"].get<std::int32_t>();
    a.alias_type = j.value("alias_type", "ReferenceAlias");
    if (a.alias_type != "ReferenceAlias" && a.alias_type != "LocationAlias")
        bad("alias_type must be ReferenceAlias or LocationAlias");
    if (j.contains("epoch") && is_uint(j["epoch"], 0xFFFFFFFFull)) a.epoch = j["epoch"].get<std::uint32_t>();
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
    if (j.is_number_float()) {
        double d = j.get<double>();
        if (!std::isfinite(d)) mismatch("float must be finite");
        return Value{static_cast<float>(d)};
    }
    if (j.is_string()) return Value{j.get<std::string>()};
    if (j.is_array()) bad("bare JSON arrays are not values; use {\"array\": type, \"items\": [...]}");
    if (j.contains("array")) {
        if (!j["array"].is_string() || !is_elem_type(j["array"].get<std::string>())) bad("unknown array element type");
        if (!j.contains("items") || !j["items"].is_array()) bad("array needs items");
        ArrayValue a{j["array"].get<std::string>(), {}};
        for (const auto& item : j["items"]) a.items.push_back(elem_from_json(a.elem_type, item));
        return Value{std::move(a)};
    }
    const auto kind = j.value("kind", "");
    if (kind == "form") return Value{form_ref_from_json(j)};
    if (kind == "alias") return Value{alias_ref_from_json(j)};
    bad("unknown value object: " + j.dump());
}

json value_to_json(const Value& v)
{
    struct Visitor
    {
        json operator()(std::monostate) const { return nullptr; }
        json operator()(bool b) const { return b; }
        json operator()(std::int32_t i) const { return i; }
        json operator()(float f) const { return static_cast<double>(f); }
        json operator()(const std::string& s) const { return s; }
        json operator()(const FormRef& r) const { return form_ref_to_json(r); }
        json operator()(const AliasRef& a) const { return alias_ref_to_json(a); }
        json operator()(const ArrayValue& a) const
        {
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

bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 11
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): error codes and Papyrus value codec with Int range checks"
```
