#include "spy/values.h"
#include <algorithm>
#include <cfloat>
#include <charconv>
#include <cmath>
#include <system_error>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }
[[noreturn]] void mismatch(const std::string& m) { throw ProtocolError(ErrorCode::type_mismatch, m); }

bool present(const json& j, const char* key) { return j.contains(key) && !j[key].is_null(); }

std::int32_t to_int32(const json& j)
{
    if (j.is_number_unsigned()) {
        auto u = j.get<std::uint64_t>();
        if (u > static_cast<std::uint64_t>(kIntMax))
            mismatch("integer " + j.dump() + " is out of Papyrus Int range");
        return static_cast<std::int32_t>(u);
    }
    auto s = j.get<std::int64_t>();
    if (s < kIntMin || s > kIntMax)
        mismatch("integer " + j.dump() + " is out of Papyrus Int range");
    return static_cast<std::int32_t>(s);
}

float to_float(const json& j)
{
    constexpr double kMax = static_cast<double>(FLT_MAX);
    // The shortest text that round-trips FLT_MAX is 3.4028235e38 (kFloatMaxText), which as a double is a little above
    // FLT_MAX. value_to_json writes that text, so it has to be accepted back. Anything larger is out of range.
    constexpr double kMaxText = kFloatMaxText;
    const double d = j.get<double>();
    if (!std::isfinite(d)) mismatch("float must be finite");
    const double a = std::fabs(d);
    if (a <= kMax) return static_cast<float>(d);
    if (a <= kMaxText) return d < 0 ? -FLT_MAX : FLT_MAX;
    mismatch("float " + j.dump() + " is out of Papyrus Float range");
}

// Shortest decimal text that reads back as the same float, then widened to a double that prints the same way.
json float_to_json(float f)
{
    if (!std::isfinite(f)) return nullptr;  // JSON has no NaN or infinity
    char buf[64];
    const auto w = std::to_chars(buf, buf + sizeof buf, f);
    double d = static_cast<double>(f);
    if (w.ec == std::errc()) std::from_chars(buf, w.ptr, d);
    return d;
}

bool is_elem_type(const std::string& t)
{
    return std::find(kArrayElemTypes.begin(), kArrayElemTypes.end(), t) != kArrayElemTypes.end();
}

bool is_alias_type(const std::string& t)
{
    return std::find(kAliasTypes.begin(), kAliasTypes.end(), t) != kAliasTypes.end();
}

Value elem_from_json(const std::string& elem, const json& j)
{
    if (j.is_object() || j.is_array()) {
        if (elem == "Form" && j.is_object() && str_or(j, "kind", "") == "form") return Value{form_ref_from_json(j)};
        if (elem == "Alias" && j.is_object() && str_or(j, "kind", "") == "alias") return Value{alias_ref_from_json(j)};
        mismatch("array of " + elem + " cannot hold " + describe(j));
    }
    if (elem == "Bool" && j.is_boolean()) return Value{j.get<bool>()};
    if (elem == "Int" && j.is_number_integer()) return Value{to_int32(j)};
    if (elem == "Float" && j.is_number()) return Value{to_float(j)};
    if (elem == "String" && j.is_string()) return Value{j.get<std::string>()};
    if ((elem == "Form" || elem == "Alias") && j.is_null()) return Value{};
    mismatch("array of " + elem + " cannot hold " + describe(j));
}
}

bool is_uint(const json& j, std::uint64_t max)
{
    if (j.is_number_unsigned()) return j.get<std::uint64_t>() <= max;
    if (j.is_number_integer()) return j.get<std::int64_t>() >= 0 && static_cast<std::uint64_t>(j.get<std::int64_t>()) <= max;
    return false;
}

FormRef form_ref_from_json(const json& j)
{
    if (!j.is_object() || str_or(j, "kind", "") != "form") bad("expected a form reference");
    FormRef r;
    if (present(j, "form_id")) {
        if (!is_uint(j["form_id"], 0xFFFFFFFFull)) bad("form_id must be an unsigned 32-bit integer");
        r.form_id = j["form_id"].get<std::uint32_t>();
    }
    if (present(j, "form_key")) {
        if (!j["form_key"].is_string() || !is_valid_form_key(j["form_key"].get<std::string>()))
            bad("form_key must look like Plugin.esp|0x0012AB");
        r.form_key = j["form_key"].get<std::string>();
    }
    if (!r.form_id && !r.form_key) bad("a form reference needs form_id or form_key");
    if (r.form_id && r.form_key && !form_key_matches_id(*r.form_key, *r.form_id))
        bad("form_id and form_key do not name the same form");
    r.type = str_or(j, "type", "");
    if (present(j, "epoch")) {
        if (!is_uint(j["epoch"], 0xFFFFFFFFull)) bad("epoch must be an unsigned 32-bit integer");
        r.epoch = j["epoch"].get<std::uint32_t>();
    }
    if (present(j, "fingerprint")) {
        const auto& f = j["fingerprint"];
        if (!f.is_object() || !f.contains("base") || !f["base"].is_string() || !f.contains("type") || !f["type"].is_string())
            bad("fingerprint needs string base and type");
        r.fingerprint = Fingerprint{f["base"].get<std::string>(), f["type"].get<std::string>()};
    }
    return r;
}

json form_ref_to_json(const FormRef& r)
{
    json j = {{"kind", "form"}};
    if (r.form_id) j["form_id"] = *r.form_id;
    if (r.form_key) j["form_key"] = *r.form_key;
    if (!r.type.empty()) j["type"] = r.type;
    if (r.epoch) j["epoch"] = *r.epoch;
    if (r.fingerprint) j["fingerprint"] = {{"base", r.fingerprint->base}, {"type", r.fingerprint->type}};
    return j;
}

AliasRef alias_ref_from_json(const json& j)
{
    if (!j.is_object() || str_or(j, "kind", "") != "alias") bad("expected an alias reference");
    AliasRef a;
    if (!j.contains("quest")) bad("alias reference needs quest");
    a.quest = form_ref_from_json(j["quest"]);
    if (!j.contains("alias_id") || !is_uint(j["alias_id"], static_cast<std::uint64_t>(kIntMax)))
        bad("alias_id must be an integer from 0 to " + std::to_string(kIntMax));
    a.alias_id = static_cast<std::int32_t>(j["alias_id"].get<std::int64_t>());
    a.alias_type = str_or(j, "alias_type", kDefaultAliasType);
    if (!is_alias_type(a.alias_type)) bad("alias_type must be ReferenceAlias or LocationAlias");
    if (present(j, "epoch")) {
        if (!is_uint(j["epoch"], 0xFFFFFFFFull)) bad("epoch must be an unsigned 32-bit integer");
        a.epoch = j["epoch"].get<std::uint32_t>();
    }
    return a;
}

json alias_ref_to_json(const AliasRef& a)
{
    json j = {{"kind", "alias"}, {"quest", form_ref_to_json(a.quest)}, {"alias_id", a.alias_id}, {"alias_type", a.alias_type}};
    if (a.epoch) j["epoch"] = *a.epoch;
    return j;
}

Value value_from_json(const json& j)
{
    if (j.is_null()) return Value{};
    if (j.is_boolean()) return Value{j.get<bool>()};
    if (j.is_number_integer()) return Value{to_int32(j)};
    if (j.is_number_float()) return Value{to_float(j)};
    if (j.is_string()) return Value{j.get<std::string>()};
    if (j.is_array()) bad("bare JSON arrays are not values; use {\"array\": type, \"items\": [...]}");
    if (!j.is_object()) bad("not a value");
    if (j.contains("array")) {
        if (!j["array"].is_string() || !is_elem_type(j["array"].get<std::string>())) bad("unknown array element type");
        if (!j.contains("items") || !j["items"].is_array()) bad("array needs items");
        if (j["items"].size() > kMaxArrayItems)
            mismatch("a Papyrus array holds at most " + std::to_string(kMaxArrayItems) + " items, got " + std::to_string(j["items"].size()));
        ArrayValue a{j["array"].get<std::string>(), {}};
        for (const auto& item : j["items"]) a.items.push_back(elem_from_json(a.elem_type, item));
        return Value{std::move(a)};
    }
    const auto kind = str_or(j, "kind", "");
    if (kind == "form") return Value{form_ref_from_json(j)};
    if (kind == "alias") return Value{alias_ref_from_json(j)};
    bad("unknown value object (kind '" + clip_text(kind, 32) + "')");
}

json value_to_json(const Value& v)
{
    struct Visitor
    {
        json operator()(std::monostate) const { return nullptr; }
        json operator()(bool b) const { return b; }
        json operator()(std::int32_t i) const { return i; }
        json operator()(float f) const { return float_to_json(f); }
        json operator()(const std::string& s) const { return s; }
        json operator()(const FormRef& r) const { return form_ref_to_json(r); }
        json operator()(const AliasRef& a) const { return alias_ref_to_json(a); }
        json operator()(const ArrayValue& a) const
        {
            if (a.items.size() > kMaxArrayItems)
                mismatch("a Papyrus array holds at most " + std::to_string(kMaxArrayItems) + " items, got " + std::to_string(a.items.size()));
            json items = json::array();
            for (const auto& item : a.items) items.push_back(value_to_json(item));
            return json{{"array", a.elem_type}, {"items", items}};
        }
    };
    return std::visit(Visitor{}, v.v);
}

std::string_view value_kind(const Value& v)
{
    static constexpr std::string_view names[] = {"null", "bool", "int", "float", "string", "form", "alias", "array"};
    return names[v.v.index()];
}

bool is_valid_form_key(std::string_view) { return true; }  // replaced in Task 15
bool form_key_matches_id(std::string_view, std::uint32_t) { return true; }  // replaced in Task 15
}
