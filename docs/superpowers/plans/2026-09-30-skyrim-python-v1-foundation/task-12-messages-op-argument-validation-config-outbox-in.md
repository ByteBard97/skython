# Task 12: Messages, op-argument validation, config, outbox, in-flight limiter, fixture sweep

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `core/include/spy/messages.h`, `core/src/messages.cpp`, `core/include/spy/config.h`, `core/src/config.cpp`, `core/include/spy/outbox.h`, `core/src/outbox.cpp`
- Create: `core/tests/test_messages.cpp`, `core/tests/test_config.cpp`, `core/tests/test_outbox.cpp`, `core/tests/test_fixtures.cpp`

**Interfaces:**
- Produces:
  - `inline constexpr int spy::kProtocolMajor = 1, spy::kProtocolMinor = 0;`
  - `struct spy::Hello { int major; int minor; std::string token; std::string client; }`; `Hello spy::parse_hello(const json&)` (throws `bad_request`)
  - `std::optional<ErrorCode> spy::check_hello(const Hello&, std::string_view expected_token)` → `unauthorized` or `protocol_mismatch` or nullopt
  - `struct spy::Request { std::uint64_t id; std::string op; json args; }`; `Request spy::parse_request(const json&)` (envelope + known op + op args; throws `bad_request`/`type_mismatch`)
  - `struct spy::CallArgs { std::optional<Value> object; std::string script; std::string function; std::vector<Value> args; std::optional<std::uint32_t> timeout_ms; bool require_unpaused; }`; `CallArgs spy::parse_call_args(const json& args, bool needs_object)`
  - `struct spy::PropertyArgs { Value object; std::string script; std::string property; std::optional<Value> value; }`; `PropertyArgs spy::parse_property_args(const json& args, bool needs_value)`
  - `struct spy::BatchStep { std::string op; json args; }`; `struct spy::BatchArgs { std::vector<BatchStep> calls; bool stop_on_error; std::optional<std::uint32_t> timeout_ms; }`; `BatchArgs spy::parse_batch_args(const json&)`
  - `struct spy::WelcomeInfo { std::string plugin_version, game_version, runtime; std::uint32_t epoch; std::vector<std::string> capabilities; }`
  - `json spy::make_welcome(const WelcomeInfo&)`, `json spy::make_handshake_error(ErrorCode, std::string_view)`, `json spy::make_ok(std::uint64_t id, json result)`, `json spy::make_error(std::uint64_t id, ErrorCode, std::string_view message, json detail = json::object())`, `json spy::make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version)`
  - `const std::vector<std::string>& spy::known_ops()`
  - `struct spy::Config { std::uint16_t port = 45977; std::string fixed_token; bool allow_external_clients = false; std::string session_file; std::uint32_t default_timeout_ms = 5000; std::uint32_t max_inflight_per_connection = 256; std::uint32_t max_inflight_total = 1024; bool enable_console = false; }`; `Config spy::parse_config(std::string_view ini_text)` (throws `std::invalid_argument` on a bad value)
  - `using spy::ConnId = std::uint64_t;` `class spy::Outbox { void open(ConnId); void close(ConnId); bool post(ConnId, std::string frame); std::vector<std::string> drain(ConnId); std::size_t open_count() const; }`
  - `class spy::InflightLimiter { InflightLimiter(std::uint32_t per_conn, std::uint32_t total); bool try_acquire(ConnId); void release(ConnId); void forget(ConnId); std::uint32_t total() const; }`

- [ ] **Step 1: Write the failing tests**

`core/tests/test_fixtures.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/messages.h"
#include "spy/refs.h"
#include "spy/values.h"
#include <fstream>

using spy::json;

static json load(const char* name)
{
    std::ifstream f(std::string(SPY_FIXTURES_DIR) + "/" + name);
    REQUIRE(f.good());
    return json::parse(f);
}

static std::string error_of(auto&& fn)
{
    try {
        fn();
    } catch (const spy::ProtocolError& e) {
        return std::string(spy::to_string(e.code));
    }
    return "none";
}

TEST_CASE("values fixtures")
{
    auto fx = load("values.json");
    for (const auto& c : fx["valid"]) {
        CAPTURE(c.dump());
        auto v = spy::value_from_json(c["wire"]);
        CHECK(spy::value_kind(v) == c["kind"].get<std::string>());
        CHECK(spy::value_to_json(v) == c["wire"]);
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::value_from_json(c["wire"]); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("form key fixtures")
{
    auto fx = load("form_keys.json");
    for (const auto& c : fx["valid"]) {
        auto k = spy::parse_form_key(c["text"].get<std::string>());
        REQUIRE(k);
        CHECK(k->plugin == c["plugin"].get<std::string>());
        CHECK(k->local_id == c["local_id"].get<std::uint32_t>());
        CHECK(spy::format_form_key(*k) == c["canonical"].get<std::string>());
    }
    for (const auto& t : fx["invalid"]) CHECK_FALSE(spy::parse_form_key(t.get<std::string>()));
}

TEST_CASE("hello fixtures")
{
    auto fx = load("hello.json");
    for (const auto& m : fx["valid"]) CHECK_NOTHROW(spy::parse_hello(m));
    for (const auto& c : fx["invalid"])
        CHECK(error_of([&] { spy::parse_hello(c["message"]); }) == c["error"].get<std::string>());
}

TEST_CASE("request fixtures")
{
    auto fx = load("requests.json");
    for (const auto& m : fx["valid"]) {
        CAPTURE(m.dump());
        CHECK_NOTHROW(spy::parse_request(m));
    }
    for (const auto& c : fx["invalid"]) {
        CAPTURE(c.dump());
        CHECK(error_of([&] { spy::parse_request(c["message"]); }) == c["error"].get<std::string>());
    }
}

TEST_CASE("responses built by the server match the fixture shapes")
{
    auto fx = load("responses.json");
    CHECK(spy::make_ok(1, 6) == fx["valid"][0]);
    CHECK(spy::make_error(2, spy::ErrorCode::not_ready, "a game is loading", {{"reason", "loading"}}) == fx["valid"][1]);
    CHECK(spy::make_welcome({"0.1.0", "1.6.1170.0", "AE", 1, {"game.state"}}) == fx["valid"][3]);
    CHECK(spy::make_handshake_error(spy::ErrorCode::unauthorized, "bad token") == fx["valid"][4]);
}
```

`core/tests/test_messages.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/messages.h"

using spy::json;

TEST_CASE("check_hello enforces token and protocol major")
{
    spy::Hello h{1, 0, "00112233445566778899aabbccddeeff", "t"};
    CHECK_FALSE(spy::check_hello(h, "00112233445566778899aabbccddeeff"));
    CHECK(spy::check_hello(h, "ffffffffffffffffffffffffffffffff") == spy::ErrorCode::unauthorized);
    h.major = 2;
    CHECK(spy::check_hello(h, "00112233445566778899aabbccddeeff") == spy::ErrorCode::protocol_mismatch);
}

TEST_CASE("call args parse values and optional fields")
{
    json a = {{"object", {{"kind", "form"}, {"form_id", 20}}}, {"script", "Actor"}, {"function", "GetActorValue"},
              {"args", {"Health"}}, {"timeout_ms", 2000}, {"require_unpaused", true}};
    auto c = spy::parse_call_args(a, true);
    CHECK(c.object.has_value());
    CHECK(c.args.size() == 1);
    CHECK(c.timeout_ms.value() == 2000u);
    CHECK(c.require_unpaused);
}

TEST_CASE("session file has port, token, pid, versions")
{
    auto s = spy::make_session_file(45977, "00112233445566778899aabbccddeeff", 1234, "0.1.0");
    CHECK(s["port"] == 45977);
    CHECK(s["protocol"] == json::array({1, 0}));
}
```

`core/tests/test_config.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/config.h"
#include <stdexcept>

TEST_CASE("defaults when empty")
{
    auto c = spy::parse_config("");
    CHECK(c.port == 45977);
    CHECK_FALSE(c.allow_external_clients);
    CHECK(c.default_timeout_ms == 5000u);
}

TEST_CASE("parses keys, ignores sections, comments and unknown keys")
{
    auto c = spy::parse_config(
        "; comment\n[Server]\nport = 45990\nallow_external_clients=true\nfixed_token = 00112233445566778899aabbccddeeff\n"
        "session_file = C:/tmp/s.json\nunknown_key = 5\n# another comment\nmax_inflight_total=10\n");
    CHECK(c.port == 45990);
    CHECK(c.allow_external_clients);
    CHECK(c.fixed_token == "00112233445566778899aabbccddeeff");
    CHECK(c.session_file == "C:/tmp/s.json");
    CHECK(c.max_inflight_total == 10u);
}

TEST_CASE("bad values throw")
{
    CHECK_THROWS_AS(spy::parse_config("port = hello\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("port = 70000\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("allow_external_clients = maybe\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("fixed_token = short\n"), std::invalid_argument);
}
```

`core/tests/test_outbox.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/outbox.h"

TEST_CASE("posts to a closed connection are dropped, not queued")
{
    spy::Outbox o;
    o.open(1);
    CHECK(o.post(1, "a"));
    o.close(1);
    CHECK_FALSE(o.post(1, "late result"));
    CHECK(o.drain(1).empty());
    CHECK(o.open_count() == 0);
}

TEST_CASE("drain returns messages in order and empties the queue")
{
    spy::Outbox o;
    o.open(7);
    o.post(7, "a");
    o.post(7, "b");
    auto d = o.drain(7);
    REQUIRE(d.size() == 2);
    CHECK(d[0] == "a");
    CHECK(o.drain(7).empty());
}

TEST_CASE("in-flight limiter enforces per-connection and total caps")
{
    spy::InflightLimiter l(2, 3);
    CHECK(l.try_acquire(1));
    CHECK(l.try_acquire(1));
    CHECK_FALSE(l.try_acquire(1));
    CHECK(l.try_acquire(2));
    CHECK_FALSE(l.try_acquire(3));
    l.release(1);
    CHECK(l.try_acquire(3));
    l.forget(2);
    CHECK(l.total() == 2u);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake --build core/build -j`
Expected: FAIL, `spy/messages.h: No such file or directory`.

- [ ] **Step 3: Implement**

`core/include/spy/messages.h`:
```cpp
#pragma once
#include "spy/values.h"
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace spy {
inline constexpr int kProtocolMajor = 1;
inline constexpr int kProtocolMinor = 0;

struct Hello { int major = 0; int minor = 0; std::string token; std::string client; };
Hello parse_hello(const json& j);
std::optional<ErrorCode> check_hello(const Hello& h, std::string_view expected_token);

struct Request { std::uint64_t id = 0; std::string op; json args; };
Request parse_request(const json& j);
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

struct WelcomeInfo
{
    std::string plugin_version, game_version, runtime;
    std::uint32_t epoch = 0;
    std::vector<std::string> capabilities;
};
json make_welcome(const WelcomeInfo& w);
json make_handshake_error(ErrorCode code, std::string_view message);
json make_ok(std::uint64_t id, json result);
json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object());
json make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version);
}
```

`core/src/messages.cpp`:
```cpp
#include "spy/messages.h"
#include "spy/refs.h"
#include <algorithm>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

std::string req_string(const json& o, const char* key)
{
    if (!o.contains(key) || !o[key].is_string() || o[key].get<std::string>().empty())
        bad(std::string("missing or empty string field '") + key + "'");
    return o[key].get<std::string>();
}

std::optional<std::uint32_t> opt_u32(const json& o, const char* key)
{
    if (!o.contains(key)) return std::nullopt;
    if (!is_uint(o[key], 0xFFFFFFFFull)) bad(std::string("field '") + key + "' must be an unsigned 32-bit integer");
    return o[key].get<std::uint32_t>();
}

Value parse_object_ref(const json& o)
{
    if (!o.contains("object")) bad("missing field 'object'");
    Value v = value_from_json(o["object"]);
    if (!std::holds_alternative<FormRef>(v.v) && !std::holds_alternative<AliasRef>(v.v))
        bad("'object' must be a form or alias reference");
    return v;
}

bool is_hex32(const std::string& t)
{
    return t.size() == 32 && std::all_of(t.begin(), t.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); });
}

void validate_op_args(const std::string& op, const json& args)
{
    if (op == "papyrus.call_static") parse_call_args(args, false);
    else if (op == "papyrus.call_method") parse_call_args(args, true);
    else if (op == "papyrus.get_property") parse_property_args(args, false);
    else if (op == "papyrus.set_property") parse_property_args(args, true);
    else if (op == "papyrus.batch") parse_batch_args(args);
    else if (op == "form.resolve") {
        const int n = args.contains("form_key") + args.contains("form_id") + args.contains("editor_id");
        if (n != 1) bad("form.resolve needs exactly one of form_key, form_id, editor_id");
        if (args.contains("form_key") && (!args["form_key"].is_string() || !is_valid_form_key(args["form_key"].get<std::string>())))
            bad("form_key must look like Plugin.esp|0x0012AB");
        if (args.contains("form_id")) opt_u32(args, "form_id");
        if (args.contains("editor_id")) req_string(args, "editor_id");
    }
}
}

const std::vector<std::string>& known_ops()
{
    static const std::vector<std::string> ops = {
        "game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
        "papyrus.get_property", "papyrus.set_property", "papyrus.batch"};
    return ops;
}

Hello parse_hello(const json& j)
{
    if (!j.is_object() || j.value("type", "") != "hello") bad("first message must be a hello");
    if (!j.contains("protocol") || !j["protocol"].is_array() || j["protocol"].size() != 2 ||
        !j["protocol"][0].is_number_integer() || !j["protocol"][1].is_number_integer())
        bad("protocol must be [major, minor]");
    auto token = j.contains("token") && j["token"].is_string() ? j["token"].get<std::string>() : std::string();
    if (!is_hex32(token)) bad("token must be 32 lowercase hex characters");
    return Hello{j["protocol"][0].get<int>(), j["protocol"][1].get<int>(), token, j.value("client", "")};
}

std::optional<ErrorCode> check_hello(const Hello& h, std::string_view expected_token)
{
    if (h.major != kProtocolMajor) return ErrorCode::protocol_mismatch;
    if (h.token != expected_token) return ErrorCode::unauthorized;
    return std::nullopt;
}

Request parse_request(const json& j)
{
    if (!j.is_object() || j.value("type", "") != "request") bad("expected a request");
    if (!j.contains("id") || !is_uint(j["id"], UINT64_MAX)) bad("request id must be a non-negative integer");
    Request r{j["id"].get<std::uint64_t>(), req_string(j, "op"), j.value("args", json::object())};
    if (!r.args.is_object()) bad("args must be an object");
    const auto& ops = known_ops();
    if (std::find(ops.begin(), ops.end(), r.op) == ops.end()) bad("unknown op '" + r.op + "'");
    validate_op_args(r.op, r.args);
    return r;
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
    c.timeout_ms = opt_u32(a, "timeout_ms");
    if (a.contains("require_unpaused")) {
        if (!a["require_unpaused"].is_boolean()) bad("require_unpaused must be a boolean");
        c.require_unpaused = a["require_unpaused"].get<bool>();
    }
    return c;
}

PropertyArgs parse_property_args(const json& a, bool needs_value)
{
    PropertyArgs p{parse_object_ref(a), req_string(a, "script"), req_string(a, "property"), std::nullopt};
    if (needs_value) {
        if (!a.contains("value")) bad("missing field 'value'");
        p.value = value_from_json(a["value"]);
    }
    return p;
}

BatchArgs parse_batch_args(const json& a)
{
    if (!a.contains("calls") || !a["calls"].is_array() || a["calls"].empty()) bad("batch needs a non-empty 'calls' array");
    BatchArgs b;
    for (const auto& step : a["calls"]) {
        if (!step.is_object()) bad("each batch call must be an object");
        auto op = req_string(step, "op");
        if (op != "papyrus.call_static" && op != "papyrus.call_method" && op != "papyrus.get_property" && op != "papyrus.set_property")
            bad("batch calls may only be papyrus.* ops, got '" + op + "'");
        auto args = step.value("args", json::object());
        validate_op_args(op, args);
        b.calls.push_back({op, args});
    }
    if (a.contains("stop_on_error")) {
        if (!a["stop_on_error"].is_boolean()) bad("stop_on_error must be a boolean");
        b.stop_on_error = a["stop_on_error"].get<bool>();
    }
    b.timeout_ms = opt_u32(a, "timeout_ms");
    return b;
}

json make_welcome(const WelcomeInfo& w)
{
    return {{"type", "welcome"}, {"protocol", {kProtocolMajor, kProtocolMinor}}, {"plugin_version", w.plugin_version},
            {"game_version", w.game_version}, {"runtime", w.runtime}, {"epoch", w.epoch}, {"capabilities", w.capabilities}};
}

json make_handshake_error(ErrorCode code, std::string_view message)
{
    return {{"type", "handshake_error"}, {"code", to_string(code)}, {"message", message}};
}

json make_ok(std::uint64_t id, json result)
{
    return {{"type", "response"}, {"id", id}, {"ok", true}, {"result", std::move(result)}};
}

json make_error(std::uint64_t id, ErrorCode code, std::string_view message, json detail)
{
    return {{"type", "response"}, {"id", id}, {"ok", false},
            {"error", {{"code", to_string(code)}, {"message", message}, {"detail", std::move(detail)}}}};
}

json make_session_file(std::uint16_t port, std::string_view token, std::uint32_t pid, std::string_view plugin_version)
{
    return {{"port", port}, {"token", token}, {"pid", pid}, {"plugin_version", plugin_version},
            {"protocol", {kProtocolMajor, kProtocolMinor}}};
}
}
```

`core/include/spy/config.h`:
```cpp
#pragma once
#include <cstdint>
#include <string>
#include <string_view>

namespace spy {
struct Config
{
    std::uint16_t port = 45977;
    std::string fixed_token;  // empty: random per start
    bool allow_external_clients = false;
    std::string session_file;  // empty: <SKSE log dir>/SkyrimPython/session.json
    std::uint32_t default_timeout_ms = 5000;
    std::uint32_t max_inflight_per_connection = 256;
    std::uint32_t max_inflight_total = 1024;
    bool enable_console = false;
};

// INI subset: "key = value" lines; [sections], ';' and '#' comments and unknown keys are ignored.
Config parse_config(std::string_view text);
}
```

`core/src/config.cpp`:
```cpp
#include "spy/config.h"
#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace spy {
namespace {
std::string trim(std::string_view s)
{
    auto b = s.find_first_not_of(" \t\r");
    if (b == std::string_view::npos) return {};
    auto e = s.find_last_not_of(" \t\r");
    return std::string(s.substr(b, e - b + 1));
}

std::uint64_t to_uint(const std::string& key, const std::string& v, std::uint64_t max)
{
    if (v.empty() || !std::all_of(v.begin(), v.end(), [](char c) { return std::isdigit(static_cast<unsigned char>(c)); }))
        throw std::invalid_argument(key + " must be a non-negative integer");
    auto n = std::stoull(v);
    if (n > max) throw std::invalid_argument(key + " is out of range");
    return n;
}

bool to_bool(const std::string& key, const std::string& v)
{
    if (v == "true" || v == "1") return true;
    if (v == "false" || v == "0") return false;
    throw std::invalid_argument(key + " must be true or false");
}
}

Config parse_config(std::string_view text)
{
    Config c;
    std::size_t start = 0;
    while (start <= text.size()) {
        auto end = text.find('\n', start);
        if (end == std::string_view::npos) end = text.size();
        auto line = trim(text.substr(start, end - start));
        start = end + 1;
        if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
        auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        auto key = trim(line.substr(0, eq));
        auto val = trim(line.substr(eq + 1));
        if (key == "port") {
            auto p = to_uint(key, val, 65535);
            c.port = static_cast<std::uint16_t>(p);
        } else if (key == "fixed_token") {
            if (!val.empty() && (val.size() != 32 || !std::all_of(val.begin(), val.end(), [](char ch) { return std::isxdigit(static_cast<unsigned char>(ch)) && !std::isupper(static_cast<unsigned char>(ch)); })))
                throw std::invalid_argument("fixed_token must be 32 lowercase hex characters");
            c.fixed_token = val;
        } else if (key == "allow_external_clients") c.allow_external_clients = to_bool(key, val);
        else if (key == "session_file") c.session_file = val;
        else if (key == "default_timeout_ms") c.default_timeout_ms = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "max_inflight_per_connection") c.max_inflight_per_connection = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "max_inflight_total") c.max_inflight_total = static_cast<std::uint32_t>(to_uint(key, val, 0xFFFFFFFFull));
        else if (key == "enable_console") c.enable_console = to_bool(key, val);
    }
    return c;
}
}
```
Note: `port = 70000` must throw — `to_uint(key, val, 65535)` does that.

`core/include/spy/outbox.h`:
```cpp
#pragma once
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace spy {
using ConnId = std::uint64_t;

// Thread-safe routing of outbound frames. Any thread may post; the IO thread drains.
// Posting to a connection that is not open drops the frame (a late reply after disconnect).
class Outbox
{
public:
    void open(ConnId id);
    void close(ConnId id);
    bool post(ConnId id, std::string frame);
    std::vector<std::string> drain(ConnId id);
    std::size_t open_count() const;

private:
    mutable std::mutex m_;
    std::unordered_map<ConnId, std::vector<std::string>> q_;
};

class InflightLimiter
{
public:
    InflightLimiter(std::uint32_t per_conn, std::uint32_t total) : per_conn_(per_conn), total_cap_(total) {}
    bool try_acquire(ConnId id);
    void release(ConnId id);
    void forget(ConnId id);  // connection closed: drop its count
    std::uint32_t total() const;

private:
    mutable std::mutex m_;
    std::uint32_t per_conn_, total_cap_, total_ = 0;
    std::unordered_map<ConnId, std::uint32_t> counts_;
};
}
```

`core/src/outbox.cpp`:
```cpp
#include "spy/outbox.h"

namespace spy {
void Outbox::open(ConnId id) { std::lock_guard l(m_); q_[id]; }
void Outbox::close(ConnId id) { std::lock_guard l(m_); q_.erase(id); }

bool Outbox::post(ConnId id, std::string frame)
{
    std::lock_guard l(m_);
    auto it = q_.find(id);
    if (it == q_.end()) return false;
    it->second.push_back(std::move(frame));
    return true;
}

std::vector<std::string> Outbox::drain(ConnId id)
{
    std::lock_guard l(m_);
    auto it = q_.find(id);
    if (it == q_.end()) return {};
    std::vector<std::string> out;
    out.swap(it->second);
    return out;
}

std::size_t Outbox::open_count() const { std::lock_guard l(m_); return q_.size(); }

bool InflightLimiter::try_acquire(ConnId id)
{
    std::lock_guard l(m_);
    auto& n = counts_[id];
    if (n >= per_conn_ || total_ >= total_cap_) return false;
    ++n;
    ++total_;
    return true;
}

void InflightLimiter::release(ConnId id)
{
    std::lock_guard l(m_);
    auto it = counts_.find(id);
    if (it == counts_.end() || it->second == 0) return;
    --it->second;
    --total_;
}

void InflightLimiter::forget(ConnId id)
{
    std::lock_guard l(m_);
    auto it = counts_.find(id);
    if (it == counts_.end()) return;
    total_ -= it->second;
    counts_.erase(it);
}

std::uint32_t InflightLimiter::total() const { std::lock_guard l(m_); return total_; }
}
```
Note: `InflightLimiter::release` after `forget` for the same connection is a no-op (the count entry is gone), so a late completion for a closed connection never double-decrements the total.

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: PASS (all fixture cases included). If a fixture case fails, fix the code, never the fixture, unless the fixture contradicts `protocol/PROTOCOL.md`.

- [ ] **Step 5: Commit**

```bash
git add core && git commit -m "feat(core): messages, op validation, config, outbox, in-flight limiter; fixture sweep"
```
