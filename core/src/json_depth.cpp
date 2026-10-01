#include "spy/json_depth.h"

namespace spy {
bool nesting_within(std::string_view text, std::size_t max_depth)
{
    std::size_t depth = 0;
    bool in_string = false, escaped = false;
    for (const char ch : text) {
        if (in_string) {
            if (escaped) escaped = false;
            else if (ch == '\\') escaped = true;
            else if (ch == '"') in_string = false;
            continue;
        }
        if (ch == '"') in_string = true;
        else if (ch == '[' || ch == '{') {
            if (++depth > max_depth) return false;
        } else if ((ch == ']' || ch == '}') && depth > 0) --depth;
    }
    return true;
}
}
