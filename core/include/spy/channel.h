#pragma once
#include <cstddef>
#include <optional>
#include <string_view>
#include <nlohmann/json.hpp>

namespace spy {
// Skython.Send(channel, payload) -> the data of the `channel` event: {"channel", "payload", "truncated"}. nullopt when the
// channel is empty or longer than kMaxChannelBytes (the native drops such a call and logs once). The payload is cut to at
// most kMaxChannelPayloadBytes at a UTF-8 boundary ("truncated": true). Pure: no engine, no Python.
std::optional<nlohmann::json> make_channel_event(std::string_view channel, std::string_view payload);

// The length of the longest prefix of s that is at most max bytes and does not end inside a UTF-8 sequence (it backs up
// at most 3 bytes, so invalid input costs nothing extra; dump_json replaces invalid UTF-8 later).
std::size_t utf8_prefix_len(std::string_view s, std::size_t max);
}
