#pragma once
#include <cstddef>
#include <string_view>

namespace spy {
// Request text can nest arbitrarily deep, and the recursive JSON parser and converters can overflow a thread's
// stack. Scan the raw text (no allocation, O(n)) and refuse anything nested deeper than max_depth BEFORE parsing
// it. Brackets inside strings are ignored; malformed text is not judged here (the parser does that).
bool nesting_within(std::string_view text, std::size_t max_depth);
}
