# Task 15: Form keys and the fingerprint rule

Adapted from: branch `plan-out-of-process`, task 11 (`task-11-form-keys-and-the-fingerprint-rule.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 14 (and through it Tasks 0, 12 and 13). Linux only; no game.

**What changed from the out-of-process plan.** Only task numbers. The form-key grammar, the light-plugin rule and the fingerprint rule are unchanged; they are used in-process and over the dev bridge alike.

**Files:**
- Create: `core/include/spy/refs.h`, `core/src/refs.cpp`, `core/tests/test_refs.cpp`
- Modify: `core/src/values.cpp` (delete the two temporary definitions at the end)
- Consumes (generated, Task 12): `spy::form_ids::{kMaxLocalId, kMaxLightLocalId, kRuntimePrefix, kLightPrefix, kMaxKeyHexDigits, kCanonicalHexDigits, kPluginExtensions, kLightExtension}` from `core/include/spy/protocol_generated.h`. The grammar below is code; every number and extension in it is a generated constant.

**Interfaces:**
- Produces:
  - `struct spy::FormKey { std::string plugin; std::uint32_t local_id; }`
  - `std::optional<FormKey> spy::parse_form_key(std::string_view)`. Grammar: `<plugin>|0x<1..kMaxKeyHexDigits hex digits>`; the plugin name ends in one of `form_ids::kPluginExtensions` (`.esm`, `.esp`, `.esl`; case-insensitive), has a non-empty stem with no leading or trailing space or tab, and contains no control characters (below 0x20 or 0x7F, so no newline); local id at most `kMaxLocalId` (`0xFFFFFF`), and at most `kMaxLightLocalId` (`0xFFF`) for `kLightExtension` (light plugins have 12-bit ids).
  - `std::string spy::format_form_key(const FormKey&)` → `Plugin|0x` + `kCanonicalHexDigits` (6) uppercase hex digits
  - `bool spy::is_valid_form_key(std::string_view)`
  - `bool spy::is_runtime_form_id(std::uint32_t)` (top byte `kRuntimePrefix`, `0xFF`), `bool spy::is_light_form_id(std::uint32_t)` (top byte `kLightPrefix`, `0xFE`)
  - `std::uint32_t spy::local_form_id(std::uint32_t form_id)`: the plugin-local part of a load-order FormID (low 12 bits for a `0xFE` light id, else low 24 bits).
  - `bool spy::form_key_matches_id(std::string_view key, std::uint32_t form_id)`: the key parses, `form_id` is not a runtime (`0xFF`) id, and the key's local id equals `local_form_id(form_id)`. The plugin name cannot be checked here (the load order is not known to core). `value_from_json` uses it to reject a reference whose `form_id` and `form_key` disagree.
  - `enum class spy::RefStatus { ok, stale }`; `RefStatus spy::check_fingerprint(const FormRef& claimed, const Fingerprint& live)`: `ok` unless the claimed `form_id` is runtime, the claim carries a fingerprint, and it differs from `live`.

- [ ] **Step 1: Write the failing tests**

`core/tests/test_refs.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/refs.h"

TEST_CASE("form keys parse and format canonically")
{
    auto k = spy::parse_form_key("Skyrim.esm|0x14");
    REQUIRE(k);
    CHECK(k->plugin == "Skyrim.esm");
    CHECK(k->local_id == 0x14u);
    CHECK(spy::format_form_key(*k) == "Skyrim.esm|0x000014");
    CHECK(spy::parse_form_key("Light Mod.esl|0xabc")->local_id == 0xABCu);
    CHECK(spy::parse_form_key("SKYRIM.ESM|0X14")->local_id == 0x14u);
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1000000"));
    CHECK_FALSE(spy::parse_form_key("notes.txt|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm"));
    CHECK_FALSE(spy::parse_form_key("|0x1"));
}

TEST_CASE("form key plugin names are checked, not just suffix-matched")
{
    CHECK_FALSE(spy::parse_form_key(".esm|0x1"));                  // empty stem
    CHECK_FALSE(spy::parse_form_key(" Skyrim.esm|0x1"));           // leading space
    CHECK_FALSE(spy::parse_form_key("\tSkyrim.esm|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim .esm|0x1"));           // trailing space in the stem
    CHECK_FALSE(spy::parse_form_key("Sky\nrim.esm|0x1"));          // control character
    CHECK_FALSE(spy::parse_form_key(std::string("Sky\0rim.esm|0x1", 15)));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm\n|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1\n"));          // trailing newline
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x123456789"));    // nine digits
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x0x1"));
    CHECK(spy::parse_form_key("Light Mod.esl|0xFFF"));             // inner spaces are fine
    CHECK(spy::parse_form_key("a.esp|0x1"));
}

TEST_CASE("light plugins have 12-bit local ids")
{
    CHECK(spy::parse_form_key("L.esl|0xFFF")->local_id == 0xFFFu);
    CHECK_FALSE(spy::parse_form_key("L.esl|0x1000"));
    CHECK_FALSE(spy::parse_form_key("L.ESL|0xFFFFFF"));
    CHECK(spy::parse_form_key("L.esp|0xFFFFFF")->local_id == 0xFFFFFFu);  // a full plugin keeps 24 bits
}

TEST_CASE("form id ranges")
{
    CHECK(spy::is_runtime_form_id(0xFF000002u));
    CHECK_FALSE(spy::is_runtime_form_id(0x00000014u));
    CHECK(spy::is_light_form_id(0xFE001800u));
    CHECK_FALSE(spy::is_light_form_id(0xFF000002u));
    CHECK(spy::local_form_id(0x0500'0800u) == 0x800u);
    CHECK(spy::local_form_id(0xFE001ABCu) == 0xABCu);  // 0xFExxxyyy: xxx is the light slot, yyy the local id
}

TEST_CASE("form_key_matches_id compares local ids and refuses runtime ids")
{
    CHECK(spy::form_key_matches_id("Skyrim.esm|0x000014", 0x00000014u));
    CHECK(spy::form_key_matches_id("Mod.esp|0x000800", 0x05000800u));  // the load-order byte is not compared
    CHECK_FALSE(spy::form_key_matches_id("Skyrim.esm|0x000015", 0x00000014u));
    CHECK(spy::form_key_matches_id("Light.esl|0x000ABC", 0xFE001ABCu));
    CHECK_FALSE(spy::form_key_matches_id("Light.esl|0x000123", 0xFE001ABCu));
    CHECK_FALSE(spy::form_key_matches_id("Skyrim.esm|0x000002", 0xFF000002u));  // runtime forms have no form key
    CHECK_FALSE(spy::form_key_matches_id("nonsense", 0x14u));
}

TEST_CASE("value_from_json rejects a form_id and form_key that disagree")
{
    auto code = [](spy::json j) {
        try {
            spy::value_from_json(j);
        } catch (const spy::ProtocolError& e) {
            return e.code;
        }
        return spy::ErrorCode::internal;
    };
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_id", 0x05000800}, {"form_key", "Mod.esp|0x000800"}}));
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_id", 0xFE001ABCu}, {"form_key", "Light.esl|0x000ABC"}}));
    CHECK(code({{"kind", "form"}, {"form_id", 20}, {"form_key", "Skyrim.esm|0x000015"}}) == spy::ErrorCode::bad_request);
    CHECK(code({{"kind", "form"}, {"form_id", 0xFF000002u}, {"form_key", "Skyrim.esm|0x000002"}}) == spy::ErrorCode::bad_request);
    CHECK(code({{"kind", "form"}, {"form_key", "Light.esl|0x1000"}}) == spy::ErrorCode::bad_request);
    // either alone is fine
    CHECK_NOTHROW(spy::value_from_json({{"kind", "form"}, {"form_key", "Skyrim.esm|0x000015"}}));
}

TEST_CASE("fingerprint rule only applies to runtime references that carry one")
{
    spy::Fingerprint live{"Skyrim.esm|0x012EB7", "ObjectReference"};
    spy::FormRef plugin{0x00012EB7u, "Skyrim.esm|0x012EB7", "Weapon", 1, std::nullopt};
    CHECK(spy::check_fingerprint(plugin, live) == spy::RefStatus::ok);

    spy::FormRef runtime{0xFF000002u, std::nullopt, "ObjectReference", 1, live};
    CHECK(spy::check_fingerprint(runtime, live) == spy::RefStatus::ok);

    spy::FormRef other = runtime;
    other.fingerprint = spy::Fingerprint{"Skyrim.esm|0x000F", "ObjectReference"};
    CHECK(spy::check_fingerprint(other, live) == spy::RefStatus::stale);

    spy::FormRef unchecked{0xFF000002u, std::nullopt, "", std::nullopt, std::nullopt};
    CHECK(spy::check_fingerprint(unchecked, live) == spy::RefStatus::ok);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | grep -m1 "error"`
Expected: FAIL, `fatal error: spy/refs.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/refs.h`:
```cpp
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
```
(`is_valid_form_key` and `form_key_matches_id` are declared in `values.h`, already included.)

`core/src/refs.cpp`:
```cpp
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
        [](char a, char b) { return std::tolower(static_cast<unsigned char>(a)) == std::tolower(static_cast<unsigned char>(b)); });
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
        v = v * 16 + static_cast<std::uint64_t>(std::isdigit(static_cast<unsigned char>(c)) ? c - '0' : std::tolower(static_cast<unsigned char>(c)) - 'a' + 10);
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
```
In `core/src/values.cpp`, delete the last two lines before the closing brace of the namespace (the temporary definitions from Task 14; leaving them in would make the build fail on Linux with a duplicate definition, and on MSVC it links with only warning LNK4006 and may keep the always-true stubs):
```cpp
bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 15
bool form_key_matches_id(std::string_view, std::uint32_t) { return true; }  // replaced in Task 15
```

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && core/build/spy_core_tests`
Expected: PASS, `[doctest] test cases:  22 |  22 passed | 0 failed | 0 skipped` and `[doctest] assertions: 162 | 162 passed | 0 failed |` (Tasks 14 and 15 together). Before the two stubs are deleted the link fails with `multiple definition of spy::is_valid_form_key`; that is expected and is why the deletion is part of this step.

- [ ] **Step 5: Commit**

Then confirm the stubs are gone: `grep -c "replaced in Task 15" core/src/values.cpp`
Expected: `0`.

```bash
git add core/include/spy/refs.h core/src/refs.cpp core/tests/test_refs.cpp core/src/values.cpp
git commit -m "feat(core): strict form keys, light-plugin ids, form_id/form_key agreement, fingerprint rule"
```
