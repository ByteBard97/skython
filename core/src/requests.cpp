#include "spy/requests.h"
#include "spy/json_depth.h"

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

std::string req_string(const json& o, const char* key)
{
    if (!o.contains(key) || !o[key].is_string()) bad(std::string("missing or non-string field '") + key + "'");
    const auto& s = o[key].get_ref<const std::string&>();
    if (s.empty()) bad(std::string("field '") + key + "' is empty");
    if (s.size() > kMaxNameBytes) bad(std::string("field '") + key + "' is longer than " + std::to_string(kMaxNameBytes) + " bytes");
    return s;
}

std::optional<std::uint32_t> opt_u32(const json& o, const char* key)
{
    if (!o.contains(key)) return std::nullopt;
    if (!is_uint(o[key], 0xFFFFFFFFull)) bad(std::string("field '") + key + "' must be an unsigned 32-bit integer");
    return o[key].get<std::uint32_t>();
}

std::optional<std::uint32_t> opt_timeout(const json& o)
{
    if (!o.contains("timeout_ms")) return std::nullopt;
    const auto& t = o["timeout_ms"];
    if (!is_uint(t, kMaxTimeoutMs) || t.get<std::uint64_t>() < kMinTimeoutMs)
        bad("timeout_ms must be an integer from " + std::to_string(kMinTimeoutMs) + " to " + std::to_string(kMaxTimeoutMs));
    return t.get<std::uint32_t>();
}

Value parse_object_ref(const json& o)
{
    if (!o.contains("object")) bad("missing field 'object'");
    Value v = value_from_json(o["object"]);
    if (!std::holds_alternative<FormRef>(v.v) && !std::holds_alternative<AliasRef>(v.v))
        bad("'object' must be a form or alias reference");
    return v;
}

// The checks themselves are code; which ops exist, which are batchable and which arguments are alternatives come from
// the generated op table (core/tests/test_generated.cpp checks that this function agrees with that table).
void validate_op_args(const std::string& op, const json& args)
{
    if (op == ops::kPapyrusCallStatic) parse_call_args(args, false);
    else if (op == ops::kPapyrusCallMethod) parse_call_args(args, true);
    else if (op == ops::kPapyrusGetProperty) parse_property_args(args, false);
    else if (op == ops::kPapyrusSetProperty) parse_property_args(args, true);
    else if (op == ops::kPapyrusBatch) parse_batch_args(args);
    else if (op == ops::kFormResolve) {
        const OpSpec* spec = find_op(op);
        int n = 0;
        std::string names;
        for (const auto alt : spec->exactly_one_of) {
            n += args.contains(std::string(alt)) ? 1 : 0;
            names += (names.empty() ? "" : ", ") + std::string(alt);
        }
        if (n != 1) bad(op + " needs exactly one of " + names);
        if (args.contains("form_key") && (!args["form_key"].is_string() || !is_valid_form_key(args["form_key"].get<std::string>())))
            bad("form_key must look like Plugin.esp|0x0012AB");
        if (args.contains("form_id")) opt_u32(args, "form_id");
        if (args.contains("editor_id")) req_string(args, "editor_id");
    }
}
}

const std::vector<std::string>& known_ops()
{
    static const std::vector<std::string> names = [] {
        std::vector<std::string> v;
        for (const auto& spec : kOps) v.emplace_back(spec.name);
        return v;
    }();
    return names;
}

Request parse_request(const json& j)
{
    if (!j.is_object() || str_or(j, "type", "") != "request") bad("expected a request");
    if (!j.contains("id") || !is_uint(j["id"], UINT64_MAX)) bad("request id must be a non-negative integer");
    Request r;
    r.id = j["id"].get<std::uint64_t>();
    r.op = req_string(j, "op");
    r.args = j.value("args", json::object());
    if (!r.args.is_object()) bad("args must be an object");
    if (find_op(r.op) == nullptr) bad("unknown op '" + clip_text(r.op, 64) + "'");
    validate_op_args(r.op, r.args);
    return r;
}

Request parse_request_text(std::string_view text)
{
    if (!nesting_within(text, kMaxJsonDepth)) bad("request is nested deeper than " + std::to_string(kMaxJsonDepth) + " levels");
    const json j = json::parse(text.begin(), text.end(), nullptr, /*allow_exceptions=*/false);
    if (j.is_discarded()) bad("request is not valid JSON (or not valid UTF-8)");
    return parse_request(j);
}

std::optional<std::uint64_t> peek_request_id(std::string_view text)
{
    if (!nesting_within(text, kMaxJsonDepth)) return std::nullopt;
    const json j = json::parse(text.begin(), text.end(), nullptr, /*allow_exceptions=*/false);
    if (!j.is_object() || !j.contains("id") || !is_uint(j["id"], UINT64_MAX)) return std::nullopt;
    return j["id"].get<std::uint64_t>();
}

CallArgs parse_call_args(const json& a, bool needs_object)
{
    CallArgs c;
    if (needs_object) c.object = parse_object_ref(a);
    c.script = req_string(a, "script");
    c.function = req_string(a, "function");
    if (a.contains("args")) {
        if (!a["args"].is_array()) bad("'args' must be a JSON array of values");
        for (const auto& v : a["args"]) c.args.push_back(value_from_json(v));
    }
    c.timeout_ms = opt_timeout(a);
    if (a.contains("require_unpaused")) {
        if (!a["require_unpaused"].is_boolean()) bad("require_unpaused must be a boolean");
        c.require_unpaused = a["require_unpaused"].get<bool>();
    }
    return c;
}

PropertyArgs parse_property_args(const json& a, bool needs_value)
{
    PropertyArgs p;  // member by member: brace-initialising leaks the members already built when a later initialiser throws (GCC 11, seen under ASan)
    p.object = parse_object_ref(a);
    p.script = req_string(a, "script");
    p.property = req_string(a, "property");
    if (needs_value) {
        if (!a.contains("value")) bad("missing field 'value'");
        p.value = value_from_json(a["value"]);
    }
    return p;
}

BatchArgs parse_batch_args(const json& a)
{
    if (!a.contains("calls") || !a["calls"].is_array() || a["calls"].empty()) bad("batch needs a non-empty 'calls' array");
    if (a["calls"].size() > kMaxBatchCalls)
        bad("a batch may hold at most " + std::to_string(kMaxBatchCalls) + " calls, got " + std::to_string(a["calls"].size()));
    BatchArgs b;
    for (const auto& step : a["calls"]) {
        if (!step.is_object()) bad("each batch call must be an object");
        auto op = req_string(step, "op");
        const OpSpec* spec = find_op(op);
        if (spec == nullptr || !spec->batchable) bad("a batch step must be a batchable papyrus.* op, got '" + clip_text(op, 64) + "'");
        auto args = step.value("args", json::object());
        validate_op_args(op, args);
        b.calls.push_back({op, args});
    }
    if (a.contains("stop_on_error")) {
        if (!a["stop_on_error"].is_boolean()) bad("stop_on_error must be a boolean");
        b.stop_on_error = a["stop_on_error"].get<bool>();
    }
    b.timeout_ms = opt_timeout(a);
    return b;
}

json make_ok(std::uint64_t id, json result)
{
    return {{"type", "response"}, {"id", id}, {"ok", true}, {"result", std::move(result)}};
}

json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail)
{
    return {{"type", "response"}, {"id", id}, {"ok", false},
            {"error", {{"code", to_string(code)}, {"message", clip_text(message, kMaxErrorMessageBytes)}, {"detail", std::move(detail)}}}};
}
}
