#include "spy/channel.h"
#include "spy/protocol_generated.h"  // kMaxChannelBytes, kMaxChannelPayloadBytes (protocol.json limits, Task 33)

namespace spy {
std::size_t utf8_prefix_len(std::string_view s, std::size_t max)
{
    if (s.size() <= max) return s.size();
    std::size_t n = max;  // s[n] is the first byte cut off; a continuation byte (10xxxxxx) there splits a sequence
    for (int i = 0; i < 3 && n > 0 && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80; ++i) --n;
    return n;
}

std::optional<nlohmann::json> make_channel_event(std::string_view channel, std::string_view payload)
{
    if (channel.empty() || channel.size() > kMaxChannelBytes) return std::nullopt;
    const std::size_t keep = utf8_prefix_len(payload, kMaxChannelPayloadBytes);
    return nlohmann::json{{"channel", std::string(channel)},
                          {"payload", std::string(payload.substr(0, keep))},
                          {"truncated", keep < payload.size()}};
}
}
