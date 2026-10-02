#pragma once
#include "spy/protocol_generated.h"  // kMaxNameBytes, kMaxBatchCalls, kMinTimeoutMs, kMaxTimeoutMs, kMaxJsonDepth, kOps, ...
#include "spy/values.h"
#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace spy {
// One request format for both modes (spec 4.2): in-process the skython package hands this text to _skython; over the dev
// bridge the Python bridge forwards the same text. Every request carries "type": "request".
struct Request { std::uint64_t id = 0; std::string op; json args; };
// Envelope + known op + op arguments. Throws only ProtocolError (bad_request or type_mismatch), never a json exception.
Request parse_request(const json& j);
// Request text -> Request: the nesting check (kMaxJsonDepth) runs before the parser, invalid JSON or invalid UTF-8 is
// bad_request, then parse_request. Throws only ProtocolError.
Request parse_request_text(std::string_view text);
// Best effort, for the error reply to a request that failed parse_request_text: the top-level "id" when the text is
// JSON within the depth limit and its id is a non-negative 64-bit integer, else nullopt (reply with id 0).
std::optional<std::uint64_t> peek_request_id(std::string_view text);
// The op names of the generated kOps table, in table order.
const std::vector<std::string>& known_ops();

struct CallArgs
{
    std::optional<Value> object;
    std::string script;
    std::string function;
    std::vector<Value> args;
    std::optional<std::uint32_t> timeout_ms;
    bool require_unpaused = false;
};
CallArgs parse_call_args(const json& args, bool needs_object);

struct PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; };
PropertyArgs parse_property_args(const json& args, bool needs_value);

struct BatchStep { std::string op; json args; };
struct BatchArgs { std::vector<BatchStep> calls; bool stop_on_error = true; std::optional<std::uint32_t> timeout_ms; };
BatchArgs parse_batch_args(const json& args);

json make_ok(std::uint64_t id, json result);
// `message` is clipped to kMaxErrorMessageBytes; `detail` must be small (the caller builds it).
json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object());
}
