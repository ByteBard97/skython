#include "doctest/doctest.h"
#include "spy/json_depth.h"
#include "spy/protocol_generated.h"
#include <string>

TEST_CASE("nesting_within counts brackets outside strings only")
{
    using spy::nesting_within;
    CHECK(nesting_within("{}", 64));
    CHECK(nesting_within("", 64));
    CHECK(nesting_within(std::string(64, '[') + std::string(64, ']'), 64));
    CHECK_FALSE(nesting_within(std::string(65, '[') + std::string(65, ']'), 64));
    CHECK_FALSE(nesting_within(std::string(2000000, '['), 64));  // an unterminated prefix is rejected in one pass
    CHECK(nesting_within(R"({"a":"\"[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[["})", 64));
    CHECK(nesting_within(R"({"a":"x\\","b":[1]})", 64));  // an escaped backslash ends before the closing quote
    CHECK_FALSE(nesting_within(R"({"a":[[[1]]]})", 3));
    CHECK(nesting_within(R"({"a":[[1]]})", 3));
}

TEST_CASE("the protocol's depth limit is the one the parser uses")
{
    CHECK(spy::kMaxJsonDepth == 64u);
    std::string ok = std::string(spy::kMaxJsonDepth, '[') + std::string(spy::kMaxJsonDepth, ']');
    CHECK(spy::nesting_within(ok, spy::kMaxJsonDepth));
    CHECK_FALSE(spy::nesting_within("[" + ok + "]", spy::kMaxJsonDepth));
}
