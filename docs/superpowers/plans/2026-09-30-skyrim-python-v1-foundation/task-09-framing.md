# Task 9: Framing

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `core/include/spy/framing.h`, `core/src/framing.cpp`, `core/tests/test_framing.cpp`

**Interfaces:**
- Produces: `spy::kMaxFrameBytes`, `std::string spy::encode_frame(std::string_view)`, `class spy::FrameDecoder { void feed(std::string_view); std::optional<std::string> next(); bool failed() const; }`

- [ ] **Step 1: Write the failing tests**

`core/tests/test_framing.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/framing.h"
#include <stdexcept>

TEST_CASE("encode_frame prefixes a little-endian length")
{
    auto f = spy::encode_frame("abc");
    REQUIRE(f.size() == 7);
    CHECK(f.substr(0, 4) == std::string("\x03\x00\x00\x00", 4));
    CHECK(f.substr(4) == "abc");
}

TEST_CASE("empty payload round trips")
{
    spy::FrameDecoder d;
    d.feed(spy::encode_frame(""));
    auto p = d.next();
    REQUIRE(p.has_value());
    CHECK(p->empty());
}

TEST_CASE("decoder reassembles split frames and yields several in order")
{
    spy::FrameDecoder d;
    auto all = spy::encode_frame("one") + spy::encode_frame("two");
    d.feed(all.substr(0, 5));
    CHECK_FALSE(d.next().has_value());
    d.feed(all.substr(5));
    CHECK(d.next().value() == "one");
    CHECK(d.next().value() == "two");
    CHECK_FALSE(d.next().has_value());
    CHECK_FALSE(d.failed());
}

TEST_CASE("decoder fails permanently on an oversized header")
{
    spy::FrameDecoder d;
    d.feed(std::string("\xFF\xFF\xFF\x7F", 4));
    CHECK_FALSE(d.next().has_value());
    CHECK(d.failed());
    d.feed(spy::encode_frame("later"));
    CHECK_FALSE(d.next().has_value());
}

TEST_CASE("a frame exactly at the limit is accepted, one byte over is rejected")
{
    std::string header(4, '\0');
    auto put = [&](std::uint32_t n) {
        for (int i = 0; i < 4; ++i) header[i] = static_cast<char>((n >> (8 * i)) & 0xFF);
    };
    spy::FrameDecoder ok;
    put(spy::kMaxFrameBytes);
    ok.feed(header);
    ok.next();
    CHECK_FALSE(ok.failed());
    spy::FrameDecoder bad;
    put(spy::kMaxFrameBytes + 1);
    bad.feed(header);
    bad.next();
    CHECK(bad.failed());
}

TEST_CASE("encode_frame refuses payloads over the limit")
{
    std::string big(spy::kMaxFrameBytes + 1, 'x');
    CHECK_THROWS_AS(spy::encode_frame(big), std::length_error);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL to compile, `spy/framing.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/framing.h`:
```cpp
#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>

namespace spy {
inline constexpr std::uint32_t kMaxFrameBytes = 16u * 1024u * 1024u;

// 4-byte little-endian length prefix, then the payload. Throws std::length_error over kMaxFrameBytes.
std::string encode_frame(std::string_view payload);

class FrameDecoder
{
public:
    void feed(std::string_view bytes);
    // The next complete payload, or nullopt if more bytes are needed or the decoder has failed.
    std::optional<std::string> next();
    // True once a header announced more than kMaxFrameBytes. The connection must be closed.
    bool failed() const { return failed_; }

private:
    std::string buf_;
    bool failed_ = false;
};
}
```

`core/src/framing.cpp`:
```cpp
#include "spy/framing.h"
#include <stdexcept>

namespace spy {
std::string encode_frame(std::string_view payload)
{
    if (payload.size() > kMaxFrameBytes) throw std::length_error("frame exceeds 16 MiB");
    const auto n = static_cast<std::uint32_t>(payload.size());
    std::string out;
    out.reserve(4 + payload.size());
    for (int i = 0; i < 4; ++i) out.push_back(static_cast<char>((n >> (8 * i)) & 0xFF));
    out.append(payload);
    return out;
}

void FrameDecoder::feed(std::string_view bytes)
{
    if (!failed_) buf_.append(bytes);
}

std::optional<std::string> FrameDecoder::next()
{
    if (failed_ || buf_.size() < 4) return std::nullopt;
    std::uint32_t n = 0;
    for (int i = 0; i < 4; ++i)
        n |= static_cast<std::uint32_t>(static_cast<unsigned char>(buf_[i])) << (8 * i);
    if (n > kMaxFrameBytes) {
        failed_ = true;
        buf_.clear();
        return std::nullopt;
    }
    if (buf_.size() < 4 + static_cast<std::size_t>(n)) return std::nullopt;
    std::string payload = buf_.substr(4, n);
    buf_.erase(0, 4 + static_cast<std::size_t>(n));
    return payload;
}
}
```
Remove the placeholder: `gio trash core/src/version.cpp`.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A core && git commit -m "feat(core): length-prefixed framing with 16 MiB limit"
```
