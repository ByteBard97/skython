# Task 11: Form keys and the fingerprint rule

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `core/include/spy/refs.h`, `core/src/refs.cpp`, `core/tests/test_refs.cpp`
- Modify: `core/src/values.cpp` (delete the temporary `is_valid_form_key`)

**Interfaces:**
- Produces:
  - `struct spy::FormKey { std::string plugin; std::uint32_t local_id; }`
  - `std::optional<FormKey> spy::parse_form_key(std::string_view)` (plugin must end `.esm/.esp/.esl`, case-insensitive; id `0x` + 1–8 hex digits; local id ≤ `0xFFFFFF`)
  - `std::string spy::format_form_key(const FormKey&)` → `Plugin|0x` + 6 uppercase hex digits
  - `bool spy::is_valid_form_key(std::string_view)`
  - `bool spy::is_runtime_form_id(std::uint32_t)` (top byte `0xFF`), `bool spy::is_light_form_id(std::uint32_t)` (top byte `0xFE`)
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
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm|0x1000000"));
    CHECK_FALSE(spy::parse_form_key("notes.txt|0x1"));
    CHECK_FALSE(spy::parse_form_key("Skyrim.esm"));
    CHECK_FALSE(spy::parse_form_key("|0x1"));
}

TEST_CASE("form id ranges")
{
    CHECK(spy::is_runtime_form_id(0xFF000002u));
    CHECK_FALSE(spy::is_runtime_form_id(0x00000014u));
    CHECK(spy::is_light_form_id(0xFE001800u));
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

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/refs.h: No such file or directory`.

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

enum class RefStatus { ok, stale };
RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live);
}
```

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
}

std::optional<FormKey> parse_form_key(std::string_view s)
{
    const auto bar = s.find('|');
    if (bar == std::string_view::npos || bar == 0) return std::nullopt;
    const auto plugin = s.substr(0, bar);
    if (!ends_with_ci(plugin, ".esm") && !ends_with_ci(plugin, ".esp") && !ends_with_ci(plugin, ".esl")) return std::nullopt;
    auto id = s.substr(bar + 1);
    if (id.size() < 3 || id.size() > 10 || id[0] != '0' || (id[1] != 'x' && id[1] != 'X')) return std::nullopt;
    id.remove_prefix(2);
    std::uint64_t v = 0;
    for (char c : id) {
        if (!std::isxdigit(static_cast<unsigned char>(c))) return std::nullopt;
        v = v * 16 + static_cast<std::uint64_t>(std::isdigit(static_cast<unsigned char>(c)) ? c - '0' : std::tolower(static_cast<unsigned char>(c)) - 'a' + 10);
    }
    if (v > 0xFFFFFFull) return std::nullopt;
    return FormKey{std::string(plugin), static_cast<std::uint32_t>(v)};
}

std::string format_form_key(const FormKey& k)
{
    char buf[16];
    std::snprintf(buf, sizeof buf, "0x%06X", k.local_id);
    return k.plugin + "|" + buf;
}

bool is_valid_form_key(std::string_view s) { return parse_form_key(s).has_value(); }
bool is_runtime_form_id(std::uint32_t id) { return (id >> 24) == 0xFFu; }
bool is_light_form_id(std::uint32_t id) { return (id >> 24) == 0xFEu; }

RefStatus check_fingerprint(const FormRef& claimed, const Fingerprint& live)
{
    if (!claimed.form_id || !is_runtime_form_id(*claimed.form_id) || !claimed.fingerprint) return RefStatus::ok;
    return *claimed.fingerprint == live ? RefStatus::ok : RefStatus::stale;
}
}
```
In `core/src/values.cpp`, delete the line `bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 11`.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): form keys and runtime-reference fingerprint rule"
```
