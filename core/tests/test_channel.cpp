#include "doctest/doctest.h"
#include "spy/channel.h"
#include "spy/errors.h"
#include "spy/protocol_generated.h"
#include <string>

using spy::json;
using spy::make_channel_event;

namespace {
std::string payload_of(const std::optional<json>& e) { return (*e)["payload"].get<std::string>(); }
}

TEST_CASE("channel: the limits are the protocol's")
{
    CHECK(spy::kMaxChannelPayloadBytes == 65536u);
    CHECK(spy::kMaxChannelBytes == 256u);
}

TEST_CASE("channel: a short payload passes unchanged")
{
    auto e = make_channel_event("skython_test", "stage_10");
    REQUIRE(e);
    CHECK(*e == json{{"channel", "skython_test"}, {"payload", "stage_10"}, {"truncated", false}});
    CHECK((*make_channel_event("c", ""))["payload"] == "");
}

TEST_CASE("channel: a payload at the limit is kept, one byte more is cut")
{
    const std::string at(spy::kMaxChannelPayloadBytes, 'a');
    auto kept = make_channel_event("c", at);
    CHECK(payload_of(kept).size() == spy::kMaxChannelPayloadBytes);
    CHECK((*kept)["truncated"] == false);
    auto cut = make_channel_event("c", at + "b");
    CHECK(payload_of(cut) == at);
    CHECK((*cut)["truncated"] == true);
}

TEST_CASE("channel: a 70000-byte payload arrives as the first 65536 bytes")
{
    const std::string big(70000, 'x');
    auto e = make_channel_event("c", big);
    CHECK(payload_of(e) == big.substr(0, spy::kMaxChannelPayloadBytes));
    CHECK((*e)["truncated"] == true);
}

TEST_CASE("channel: the cut never splits a UTF-8 sequence")
{
    for (const std::string seq : {"\xC3\xA9", "\xE2\x82\xAC", "\xF0\x9F\x98\x80"}) {  // 2, 3 and 4 bytes
        for (std::size_t lead = 1; lead <= seq.size(); ++lead) {
            // the sequence starts `lead` bytes before the limit: it straddles the cut unless lead == its length
            std::string p(spy::kMaxChannelPayloadBytes - lead, 'a');
            p += seq;
            p += "tail";
            auto e = make_channel_event("c", p);
            const std::size_t want = lead == seq.size() ? spy::kMaxChannelPayloadBytes : spy::kMaxChannelPayloadBytes - lead;
            CHECK(payload_of(e).size() == want);
            CHECK((*e)["truncated"] == true);
        }
    }
}

TEST_CASE("channel: an empty or over-long channel is dropped")
{
    CHECK_FALSE(make_channel_event("", "x"));
    CHECK(make_channel_event(std::string(spy::kMaxChannelBytes, 'c'), "x"));
    CHECK_FALSE(make_channel_event(std::string(spy::kMaxChannelBytes + 1, 'c'), "x"));
}

TEST_CASE("channel: invalid UTF-8 in a payload is safe to dump")
{
    auto e = make_channel_event("c", std::string("\xFF\xFE ok", 5));
    REQUIRE(e);
    CHECK_NOTHROW((void)spy::dump_json(*e));
}

TEST_CASE("channel: utf8_prefix_len backs up at most 3 bytes")
{
    const std::string cont(10, '\x80');
    CHECK(spy::utf8_prefix_len(cont, 5) == 2);
    CHECK(spy::utf8_prefix_len("abc", 10) == 3);
}
