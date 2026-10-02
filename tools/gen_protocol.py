#!/usr/bin/env python3
"""Generate the protocol artifacts from protocol/protocol.json (stdlib only, Python 3.11+).

    python3 tools/gen_protocol.py            write the generated files
    python3 tools/gen_protocol.py --check    exit 1 and print a diff if a committed generated file is stale

Outputs (all committed, so the Windows build box needs no extra tooling; the C++ header is read by core/ and the plugin,
the Python module by the skython package, the dev bridge and skython.remote):
    core/include/spy/protocol_generated.h      C++ constants, ErrorCode, op and config tables
    python/src/skython/_protocol_generated.py   Python constants, error-code registry data, op table
    protocol/PROTOCOL.generated.md             the human-readable tables PROTOCOL.md links to

Output is deterministic: lists keep the order of protocol.json (the order of error codes is the enum order),
there are no timestamps, and files are written with LF line endings.
"""
from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import sys

SOURCE = "protocol/protocol.json"
OUTPUTS = {
    "cpp": "core/include/spy/protocol_generated.h",
    "py": "python/src/skython/_protocol_generated.py",
    "md": "protocol/PROTOCOL.generated.md",
}
BANNER = "GENERATED FILE - DO NOT EDIT"
REGEN = "python3 tools/gen_protocol.py"

CTYPES = {"uint32": "std::uint32_t", "size": "std::size_t", "int": "int", "int32": "std::int32_t", "double": "double"}
INT_RANGES = {"uint32": (0, 2**32 - 1), "size": (0, 2**63 - 1), "int": (-2**31, 2**31 - 1), "int32": (-2**31, 2**31 - 1)}
ARG_TYPES = {  # protocol type name -> C++ enumerator, and what it means (also used by the markdown legend)
    "name": ("name", "non-empty string of at most max_name_bytes bytes"),
    "value": ("value", "any value (see Values)"),
    "value_list": ("value_list", "JSON array of values"),
    "ref": ("ref", "form reference or alias reference"),
    "form_key": ("form_key", "form key `Plugin.esp|0x0012AB`"),
    "u32": ("u32", "unsigned 32-bit integer"),
    "timeout_ms": ("timeout_ms", "integer from min_timeout_ms to max_timeout_ms"),
    "bool": ("boolean", "true or false"),
    "call_list": ("call_list", "1..max_batch_calls objects `{\"op\", \"args\"}`"),
    "event_name": ("event_name", "a name from the events table"),
    "event_filter": ("event_filter", "object: filter field -> 1..max_filter_values values (PROTOCOL.md, Events); null means absent"),
    "queue_size": ("queue_size", "integer from 1 to max_event_queue"),
    "u64": ("u64", "unsigned 64-bit integer"),
    "save_name": ("save_name", "string matching save_name_pattern"),
}
EVENT_FIELD_TYPES = {"form": "form", "int": "integer", "float": "floating", "bool": "boolean", "string": "string"}  # protocol -> C++ enumerator
FILTERABLE_FIELD_TYPES = ("form", "int", "string")
CONFIG_TYPES = {"u16": "std::uint16_t", "u32": "std::uint32_t", "bool": "bool", "token": "std::string_view", "path": "std::string_view"}
CPP_KEYWORDS = {"default", "new", "delete", "class", "struct", "enum", "union", "namespace", "template", "this", "true",
                "false", "int", "bool", "void", "char", "float", "double", "return", "switch", "case", "if", "else",
                "for", "while", "do", "break", "continue", "try", "catch", "throw", "static", "const", "auto", "register"}
EXCEPTION_RE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
OP_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


class SchemaError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------------------------------------- validation
def validate(m: dict) -> list[str]:
    p: list[str] = []

    def need(obj, key, kinds, where):
        if not isinstance(obj, dict) or key not in obj:
            p.append(f"{where}: missing '{key}'")
            return None
        v = obj[key]
        if isinstance(v, bool) and bool not in kinds:
            p.append(f"{where}.{key}: must be {'/'.join(k.__name__ for k in kinds)}")
            return None
        if not isinstance(v, kinds):
            p.append(f"{where}.{key}: must be {'/'.join(k.__name__ for k in kinds)}")
            return None
        return v

    def ascii_only(text, where):
        if isinstance(text, str) and not text.isascii():
            p.append(f"{where}: non-ASCII text is not allowed in protocol.json")

    def unique(names, what):
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            p.append(f"duplicate {what}: {dup}")

    if need(m, "format", (int,), "root") != 1:
        p.append("root.format: this generator understands format 1 only")
    proto = need(m, "protocol", (dict,), "root") or {}
    for k in ("major", "minor"):
        v = need(proto, k, (int,), "protocol")
        if isinstance(v, int) and not 0 <= v <= 2**31 - 1:
            p.append(f"protocol.{k}: out of range")
    for key in ("message_types", "not_ready_reasons", "alias_types", "array_elem_types"):
        v = need(m, key, (list,), "root")
        if v is not None:
            if not v or not all(isinstance(x, str) and x for x in v):
                p.append(f"{key}: must be a non-empty list of strings")
            else:
                unique(v, key)
                for x in v:
                    ascii_only(x, key)
    if need(m, "default_alias_type", (str,), "root") not in m.get("alias_types", []):
        p.append("default_alias_type: must be one of alias_types")

    # error codes
    codes = need(m, "error_codes", (list,), "root") or []
    seen_exc: list[str] = []
    for i, e in enumerate(codes):
        w = f"error_codes[{i}]"
        code = need(e, "code", (str,), w)
        if code is not None and (not SNAKE_RE.match(code) or code in CPP_KEYWORDS):
            p.append(f"{w}.code: '{code}' is not a lower-case identifier usable in C++")
        if need(e, "context", (str,), w) not in ("response", "handshake", "both"):
            p.append(f"{w}.context: must be response, handshake or both")
        for k in ("remote_exception", "handshake_exception"):
            v = e.get(k) if isinstance(e, dict) else None
            if v is not None:
                if not isinstance(v, str) or not EXCEPTION_RE.match(v):
                    p.append(f"{w}.{k}: must be null or a CamelCase class name")
                else:
                    seen_exc.append(v)
        ctx = e.get("context") if isinstance(e, dict) else None
        if ctx in ("response", "both") and isinstance(e, dict) and not e.get("remote_exception"):
            p.append(f"{w}: a code sent in responses needs a remote_exception")
        if ctx == "handshake" and isinstance(e, dict) and not e.get("handshake_exception"):
            p.append(f"{w}: a handshake-only code needs a handshake_exception")
        ascii_only(e.get("doc") if isinstance(e, dict) else None, f"{w}.doc")
    unique([e.get("code") for e in codes if isinstance(e, dict)], "error code")
    unique(seen_exc, "exception class")
    if not codes:
        p.append("error_codes: empty")
    elif "internal" not in [e.get("code") for e in codes if isinstance(e, dict)]:
        p.append("error_codes: 'internal' is required (to_string falls back to it)")

    # limits
    limits = need(m, "limits", (list,), "root") or []
    names = []
    for i, l in enumerate(limits):
        w = f"limits[{i}]"
        name = need(l, "name", (str,), w)
        names.append(name)
        if name is not None and not SNAKE_RE.match(name):
            p.append(f"{w}.name: not a lower-case identifier")
        ct = need(l, "ctype", (str,), w)
        v = need(l, "value", (int, float), w)
        if ct is not None and ct not in CTYPES:
            p.append(f"{w}.ctype: unknown '{ct}'")
        elif ct in INT_RANGES and isinstance(v, (int, float)):
            lo, hi = INT_RANGES[ct]
            if isinstance(v, float) or not lo <= v <= hi:
                p.append(f"{w}.value: {v!r} does not fit {ct}")
        elif ct == "double" and isinstance(v, (int, float)) and not isinstance(v, bool):
            if v != v or abs(v) == float("inf"):
                p.append(f"{w}.value: must be finite")
        ascii_only(l.get("doc") if isinstance(l, dict) else None, f"{w}.doc")
    unique(names, "limit")
    lim = {l["name"]: l["value"] for l in limits if isinstance(l, dict) and "name" in l and "value" in l}
    for req in ("max_frame_bytes", "pre_auth_frame_bytes", "max_name_bytes", "max_batch_calls", "min_timeout_ms",
                "max_timeout_ms", "max_error_message_bytes", "max_client_name_bytes", "max_connections", "hello_timeout_ms",
                "max_json_depth", "max_queued_bytes_per_connection", "token_length", "int_min", "int_max", "float_max_text",
                "max_array_items", "default_event_queue", "max_event_queue", "max_subscriptions", "max_subscriptions_per_consumer",
                "max_queued_event_bytes_per_consumer", "max_filter_values", "max_events_per_next", "max_channel_payload_bytes",
                "max_channel_bytes"):
        if req not in lim:
            p.append(f"limits: '{req}' is required")
    if lim.get("pre_auth_frame_bytes", 0) > lim.get("max_frame_bytes", 0):
        p.append("limits: pre_auth_frame_bytes must not exceed max_frame_bytes")
    if lim.get("min_timeout_ms", 0) > lim.get("max_timeout_ms", 0):
        p.append("limits: min_timeout_ms must not exceed max_timeout_ms")
    if lim.get("max_frame_bytes", 0) > 2**32 - 1:
        p.append("limits: max_frame_bytes must fit the 4-byte length prefix")

    # form ids
    f = need(m, "form_ids", (dict,), "root") or {}
    for k in ("max_local_id", "max_light_local_id", "runtime_prefix", "light_prefix", "max_key_hex_digits", "canonical_hex_digits"):
        v = need(f, k, (int,), "form_ids")
        if isinstance(v, int) and v < 0:
            p.append(f"form_ids.{k}: must not be negative")
    for k in ("runtime_prefix", "light_prefix"):
        if isinstance(f.get(k), int) and f[k] > 255:
            p.append(f"form_ids.{k}: must fit one byte")
    exts = need(f, "plugin_extensions", (list,), "form_ids") or []
    if not all(isinstance(x, str) and re.fullmatch(r"\.[a-z]+", x) for x in exts) or not exts:
        p.append("form_ids.plugin_extensions: lower-case extensions with a leading dot")
    if need(f, "light_extension", (str,), "form_ids") not in exts:
        p.append("form_ids.light_extension: must be one of plugin_extensions")
    if len({len(x) for x in exts if isinstance(x, str)}) > 1:
        p.append("form_ids.plugin_extensions: extensions must have the same length")
    if isinstance(f.get("max_light_local_id"), int) and isinstance(f.get("max_local_id"), int) and f["max_light_local_id"] > f["max_local_id"]:
        p.append("form_ids: max_light_local_id must not exceed max_local_id")

    # config keys
    keys = need(m, "config_keys", (list,), "root") or []
    for i, k in enumerate(keys):
        w = f"config_keys[{i}]"
        name = need(k, "name", (str,), w)
        t = need(k, "type", (str,), w)
        if name is not None and not SNAKE_RE.match(name):
            p.append(f"{w}.name: not a lower-case identifier")
        if t not in CONFIG_TYPES:
            p.append(f"{w}.type: unknown '{t}'")
            continue
        d = k.get("default")
        if t in ("u16", "u32"):
            lo, hi = k.get("min"), k.get("max")
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi, d)):
                p.append(f"{w}: u16/u32 keys need integer default, min and max")
            elif not lo <= d <= hi:
                p.append(f"{w}: default {d} is outside {lo}..{hi}")
            elif hi > (65535 if t == "u16" else 2**32 - 1):
                p.append(f"{w}.max: does not fit {t}")
        elif t == "bool":
            if not isinstance(d, bool):
                p.append(f"{w}.default: must be a boolean")
        elif not isinstance(d, str):
            p.append(f"{w}.default: must be a string")
        ascii_only(k.get("doc") if isinstance(k, dict) else None, f"{w}.doc")
    unique([k.get("name") for k in keys if isinstance(k, dict)], "config key")
    ck = {k.get("name"): k for k in keys if isinstance(k, dict)}
    if "default_timeout_ms" in ck and isinstance(ck["default_timeout_ms"].get("max"), int):
        if ck["default_timeout_ms"]["max"] > lim.get("max_timeout_ms", 0) or ck["default_timeout_ms"].get("min", 0) < lim.get("min_timeout_ms", 0):
            p.append("config_keys.default_timeout_ms: min/max must stay inside the timeout limits")

    # discovery and client
    d = need(m, "discovery", (dict,), "root") or {}
    for k in ("host", "session_dir_name", "session_file_name", "env_session", "env_port", "env_token"):
        v = need(d, k, (str,), "discovery")
        ascii_only(v, f"discovery.{k}")
        if k.startswith("env_") and isinstance(v, str) and not re.fullmatch(r"[A-Z][A-Z0-9_]*", v):
            p.append(f"discovery.{k}: not an environment variable name")
    sf = need(d, "session_file_fields", (list,), "discovery") or []
    if not sf or not all(isinstance(x, str) for x in sf):
        p.append("discovery.session_file_fields: non-empty list of strings")
    c = need(m, "client", (dict,), "root") or {}
    need(c, "name", (str,), "client")
    for k in ("connect_timeout_s", "call_timeout_s", "send_timeout_s", "retry_not_ready_s", "ready_timeout_s", "grace_s"):
        v = need(c, k, (int, float), "client")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v <= 0:
            p.append(f"client.{k}: must be positive")

    # ops
    ops = need(m, "ops", (list,), "root") or []
    unique([o.get("name") for o in ops if isinstance(o, dict)], "op")
    for i, o in enumerate(ops):
        w = f"ops[{i}]"
        name = need(o, "name", (str,), w)
        if name is not None and not OP_RE.match(name):
            p.append(f"{w}.name: '{name}' is not a dotted lower-case op name")
        need(o, "batchable", (bool,), w)
        need(o, "needs_game", (bool,), w)
        need(o, "doc", (str,), w)
        need(o, "result", (str,), w)
        args = need(o, "args", (list,), w) or []
        arg_names = []
        for j, a in enumerate(args):
            wa = f"{w}.args[{j}]"
            an = need(a, "name", (str,), wa)
            arg_names.append(an)
            if an is not None and not SNAKE_RE.match(an):
                p.append(f"{wa}.name: not a lower-case identifier")
            if need(a, "type", (str,), wa) not in ARG_TYPES:
                p.append(f"{wa}.type: unknown argument type {a.get('type')!r}")
            need(a, "required", (bool,), wa)
            ascii_only(a.get("doc") if isinstance(a, dict) else None, f"{wa}.doc")
        unique(arg_names, f"{w} argument")
        one = need(o, "exactly_one_of", (list,), w) or []
        for n in one:
            if n not in arg_names:
                p.append(f"{w}.exactly_one_of: '{n}' is not an argument")
            elif isinstance(args, list) and any(a.get("name") == n and a.get("required") for a in args if isinstance(a, dict)):
                p.append(f"{w}.exactly_one_of: '{n}' must be optional")
        ascii_only(o.get("doc") if isinstance(o, dict) else None, f"{w}.doc")
        ascii_only(o.get("result") if isinstance(o, dict) else None, f"{w}.result")
    if not ops:
        p.append("ops: empty")
    # events
    events = need(m, "events", (list,), "root") or []
    unique([e.get("name") for e in events if isinstance(e, dict)], "event")
    for i, e in enumerate(events):
        w = f"events[{i}]"
        name = need(e, "name", (str,), w)
        if name is not None and (not SNAKE_RE.match(name) or name in CPP_KEYWORDS):
            p.append(f"{w}.name: '{name}' is not a lower-case identifier usable in C++")
        for k in ("doc", "source"):
            ascii_only(need(e, k, (str,), w), f"{w}.{k}")
        need(e, "deliver_while_loading", (bool,), w)
        fields = need(e, "fields", (list,), w) or []
        types = {}
        for j, f in enumerate(fields):
            wf = f"{w}.fields[{j}]"
            fn, ft = need(f, "name", (str,), wf), need(f, "type", (str,), wf)
            need(f, "nullable", (bool,), wf)
            if fn is not None and not SNAKE_RE.match(fn):
                p.append(f"{wf}.name: not a lower-case identifier")
            if ft is not None and ft not in EVENT_FIELD_TYPES:
                p.append(f"{wf}.type: unknown event field type {ft!r}")
            if fn is not None:
                types[fn] = ft
        unique([f.get("name") for f in fields if isinstance(f, dict)], f"{w} field")
        ff = need(e, "filter_fields", (list,), w) or []
        for n in ff:
            if n not in types:
                p.append(f"{w}.filter_fields: '{n}' is not a field")
            elif types[n] not in FILTERABLE_FIELD_TYPES:
                p.append(f"{w}.filter_fields: '{n}' has type {types[n]}, which cannot be filtered")
        unique(list(ff), f"{w} filter field")
        for n in need(e, "filter_required_any_of", (list,), w) or []:
            if n not in ff:
                p.append(f"{w}.filter_required_any_of: '{n}' is not a filter field")
    if not events:
        p.append("events: empty")
    pat = need(m, "save_name_pattern", (str,), "root")
    if isinstance(pat, str):
        if not (pat.startswith("^") and pat.endswith("$")):
            p.append("save_name_pattern: must be anchored with ^ and $")
        try:
            re.compile(pat)
        except re.error as ex:
            p.append(f"save_name_pattern: not a regular expression ({ex})")
    return p


def load(path: pathlib.Path) -> dict:
    try:
        model = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SchemaError([f"cannot read {path}: {e}"]) from None
    problems = validate(model)
    if problems:
        raise SchemaError(problems)
    return model


# ---------------------------------------------------------------------------------------------- helpers
def camel(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[._]", name))


def upper(name: str) -> str:
    return re.sub(r"[._]", "_", name).upper()


def cpp_int(ctype: str, v) -> str:
    if ctype == "double":
        return repr(float(v))
    if ctype == "int32":
        return f"static_cast<std::int32_t>({v}LL)"
    if ctype == "int":
        return str(v)
    return f"{v}u"


def cpp_str(s: str) -> str:
    return json.dumps(s)  # ASCII-only (validated), so a JSON string literal is a valid C++ literal


def comment(text: str, prefix: str = "// ") -> str:
    return prefix + text if text else ""


def py(v, indent: int = 0) -> str:
    """A Python literal for str/int/float/bool/None/list/tuple/dict, deterministic."""
    pad = " " * indent
    if v is None or isinstance(v, (bool, int)):
        return repr(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        inline = "(" + ", ".join(py(x) for x in v) + ("," if len(v) == 1 else "") + ")"
        if len(inline) + indent <= 96 or not v:
            return inline
        return "(\n" + "".join(f"{pad}    {py(x, indent + 4)},\n" for x in v) + pad + ")"
    if isinstance(v, dict):
        inline = "{" + ", ".join(f"{py(k)}: {py(x)}" for k, x in v.items()) + "}"
        if len(inline) + indent <= 96 or not v:
            return inline
        return "{\n" + "".join(f"{pad}    {py(k)}: {py(x, indent + 4)},\n" for k, x in v.items()) + pad + "}"
    raise TypeError(f"cannot render {v!r}")


def header_lines(prefix: str) -> list[str]:
    return [f"{prefix} {BANNER}.", f"{prefix} Source: {SOURCE}. Regenerate with: {REGEN}",
            f"{prefix} The drift check ({REGEN} --check) fails if this file differs from what the generator writes."]


def human_bytes(n: int) -> str:
    for unit, size in (("GiB", 2**30), ("MiB", 2**20), ("KiB", 2**10)):
        if n >= size and n % size == 0:
            return f"{n // size} {unit}"
    return f"{n} bytes"


# ---------------------------------------------------------------------------------------------- C++
def render_cpp(m: dict) -> str:
    L = header_lines("//")
    L += ["#pragma once", "#include <array>", "#include <cstddef>", "#include <cstdint>", "#include <span>",
          "#include <string_view>", "", "namespace spy {"]
    L += [f"inline constexpr int kProtocolMajor = {m['protocol']['major']};",
          f"inline constexpr int kProtocolMinor = {m['protocol']['minor']};", ""]

    L.append("// ---- limits")
    for l in m["limits"]:
        L.append(f"// {l['name']}: {l['doc']}" if l["doc"] else f"// {l['name']}")
        L.append(f"inline constexpr {CTYPES[l['ctype']]} k{camel(l['name'])} = {cpp_int(l['ctype'], l['value'])};")
    L.append("")

    codes = m["error_codes"]
    L.append("// ---- error codes (the order is the enum order in protocol.json)")
    L.append("enum class ErrorCode {")
    L += [f"    {c['code']}," for c in codes]
    L.append("};")
    L.append(f"struct ErrorCodeName {{ ErrorCode code; std::string_view name; }};")
    L.append(f"inline constexpr std::array<ErrorCodeName, {len(codes)}> kErrorCodeNames{{{{")
    L += [f"    {{ErrorCode::{c['code']}, {cpp_str(c['code'])}}}," for c in codes]
    L += ["}};", ""]

    def sv_array(name: str, items: list[str], doc: str) -> list[str]:
        out = [f"// {doc}", f"inline constexpr std::array<std::string_view, {len(items)}> {name}{{{{"]
        out += [f"    {cpp_str(x)}," for x in items]
        return out + ["}};"]

    L += sv_array("kMessageTypes", m["message_types"], "message types")
    L += sv_array("kNotReadyReasons", m["not_ready_reasons"], "detail.reason values of a not_ready error")
    L += sv_array("kAliasTypes", m["alias_types"], "alias_type values")
    L.append(f"inline constexpr std::string_view kDefaultAliasType = {cpp_str(m['default_alias_type'])};")
    L += sv_array("kArrayElemTypes", m["array_elem_types"], "array element types")
    L.append("")

    f = m["form_ids"]
    L += ["// ---- form ids and form keys", "namespace form_ids {",
          f"inline constexpr std::uint32_t kMaxLocalId = {f['max_local_id']}u;",
          f"inline constexpr std::uint32_t kMaxLightLocalId = {f['max_light_local_id']}u;",
          f"inline constexpr std::uint32_t kRuntimePrefix = {f['runtime_prefix']}u;",
          f"inline constexpr std::uint32_t kLightPrefix = {f['light_prefix']}u;",
          f"inline constexpr std::size_t kMaxKeyHexDigits = {f['max_key_hex_digits']}u;",
          f"inline constexpr int kCanonicalHexDigits = {f['canonical_hex_digits']};",
          f"inline constexpr std::string_view kLightExtension = {cpp_str(f['light_extension'])};"]
    L += sv_array("kPluginExtensions", f["plugin_extensions"], "plugin file extensions, lower case")
    L += ["}", ""]

    L += ["// ---- ops", "enum class ArgType { " + ", ".join(ARG_TYPES[t][0] for t in ARG_TYPES) + " };",
          "struct ArgSpec { std::string_view name; ArgType type; bool required; };",
          "struct OpSpec",
          "{",
          "    std::string_view name;",
          "    bool batchable;",
          "    bool needs_game;  // false: answered without a loaded game (no not_ready check)",
          "    std::span<const ArgSpec> args;",
          "    std::span<const std::string_view> exactly_one_of;  // empty, or the arguments of which exactly one must be present",
          "};"]
    L.append("namespace ops {")
    for o in m["ops"]:
        L.append(f"inline constexpr std::string_view k{camel(o['name'])} = {cpp_str(o['name'])};")
    L.append("}")
    for o in m["ops"]:
        ident = o["name"].replace(".", "_")
        if o["args"]:
            L.append(f"inline constexpr std::array<ArgSpec, {len(o['args'])}> kArgs_{ident}{{{{")
            for a in o["args"]:
                L.append(f"    {{{cpp_str(a['name'])}, ArgType::{ARG_TYPES[a['type']][0]}, {'true' if a['required'] else 'false'}}},")
            L.append("}};")
        if o["exactly_one_of"]:
            L.append(f"inline constexpr std::array<std::string_view, {len(o['exactly_one_of'])}> kOneOf_{ident}{{{{")
            L += [f"    {cpp_str(n)}," for n in o["exactly_one_of"]]
            L.append("}};")
    L.append(f"inline constexpr std::array<OpSpec, {len(m['ops'])}> kOps{{{{")
    for o in m["ops"]:
        ident = o["name"].replace(".", "_")
        args = f"kArgs_{ident}" if o["args"] else "{}"
        one = f"kOneOf_{ident}" if o["exactly_one_of"] else "{}"
        flags = f"{'true' if o['batchable'] else 'false'}, {'true' if o['needs_game'] else 'false'}"
        L.append(f"    {{{cpp_str(o['name'])}, {flags}, {args}, {one}}},")
    L += ["}};",
          "inline constexpr const OpSpec* find_op(std::string_view name)",
          "{",
          "    for (const auto& op : kOps)",
          "        if (op.name == name) return &op;",
          "    return nullptr;",
          "}", ""]

    L += ["// ---- events (data fields, filterable fields, the loading rule)",
          "enum class EventFieldType { " + ", ".join(EVENT_FIELD_TYPES.values()) + " };",
          "struct EventField { std::string_view name; EventFieldType type; bool nullable; };",
          "struct EventSpec",
          "{",
          "    std::string_view name;",
          "    std::span<const EventField> fields;                        // exactly the keys of the event's data object",
          "    std::span<const std::string_view> filter_fields;           // fields a subscription filter may name",
          "    std::span<const std::string_view> filter_required_any_of;  // empty, or a filter must name one of these",
          "    bool deliver_while_loading;  // false: not queued while a load is in progress (counted in suppressed)",
          "};",
          "namespace event_names {"]
    for e in m["events"]:
        L.append(f"inline constexpr std::string_view k{camel(e['name'])} = {cpp_str(e['name'])};")
    L.append("}")
    for e in m["events"]:
        n = e["name"]
        if e["fields"]:
            L.append(f"inline constexpr std::array<EventField, {len(e['fields'])}> kEventFields_{n}{{{{")
            for f in e["fields"]:
                L.append(f"    {{{cpp_str(f['name'])}, EventFieldType::{EVENT_FIELD_TYPES[f['type']]}, {'true' if f['nullable'] else 'false'}}},")
            L.append("}};")
        for key, ident in (("filter_fields", "kEventFilter_"), ("filter_required_any_of", "kEventFilterRequired_")):
            if e[key]:
                L.append(f"inline constexpr std::array<std::string_view, {len(e[key])}> {ident}{n}{{{{")
                L += [f"    {cpp_str(x)}," for x in e[key]]
                L.append("}};")
    L.append(f"inline constexpr std::array<EventSpec, {len(m['events'])}> kEvents{{{{")
    for e in m["events"]:
        n = e["name"]
        parts = [cpp_str(n), f"kEventFields_{n}" if e["fields"] else "{}", f"kEventFilter_{n}" if e["filter_fields"] else "{}",
                 f"kEventFilterRequired_{n}" if e["filter_required_any_of"] else "{}", "true" if e["deliver_while_loading"] else "false"]
        L.append("    {" + ", ".join(parts) + "},")
    L += ["}};",
          "inline constexpr const EventSpec* find_event(std::string_view name)",
          "{",
          "    for (const auto& e : kEvents)",
          "        if (e.name == name) return &e;",
          "    return nullptr;",
          "}",
          "// game.save and game.load names (ECMAScript syntax; is_valid_save_name in requests.h implements it)",
          f"inline constexpr std::string_view kSaveNamePattern = {cpp_str(m['save_name_pattern'])};", ""]

    L.append("// ---- config keys")
    L.append("enum class ConfigType { u16, u32, boolean, token, path };")
    L.append("struct ConfigKey { std::string_view name; ConfigType type; std::uint64_t min; std::uint64_t max; std::string_view default_text; };")
    ctypes = {"u16": "u16", "u32": "u32", "bool": "boolean", "token": "token", "path": "path"}
    L.append(f"inline constexpr std::array<ConfigKey, {len(m['config_keys'])}> kConfigKeys{{{{")
    for k in m["config_keys"]:
        lo, hi = k.get("min", 0), k.get("max", 0)
        d = ("true" if k["default"] else "false") if k["type"] == "bool" else str(k["default"])
        L.append(f"    {{{cpp_str(k['name'])}, ConfigType::{ctypes[k['type']]}, {lo}u, {hi}u, {cpp_str(d)}}},")
    L += ["}};",
          "inline constexpr const ConfigKey* find_config_key(std::string_view name)",
          "{",
          "    for (const auto& k : kConfigKeys)",
          "        if (k.name == name) return &k;",
          "    return nullptr;",
          "}",
          "namespace config_defaults {"]
    for k in m["config_keys"]:
        t = CONFIG_TYPES[k["type"]]
        if k["type"] == "bool":
            v = "true" if k["default"] else "false"
        elif k["type"] in ("u16", "u32"):
            v = f"{k['default']}u"
        else:
            v = cpp_str(k["default"])
        L.append(f"inline constexpr {t} k{camel(k['name'])} = {v};")
    L += ["}", ""]

    d = m["discovery"]
    L += ["// ---- discovery", "namespace discovery {"]
    for key in ("host", "session_dir_name", "session_file_name", "env_session", "env_port", "env_token"):
        L.append(f"inline constexpr std::string_view k{camel(key)} = {cpp_str(d[key])};")
    L += sv_array("kSessionFileFields", d["session_file_fields"], "keys of the session file object")
    L += ["}", "}  // namespace spy", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------- Python
def render_py(m: dict) -> str:
    L = header_lines("#")
    L += ["# Constants, error-code registry data and the op table for the skython package. Nothing here is logic.",
          "", f"PROTOCOL_MAJOR = {m['protocol']['major']}", f"PROTOCOL_MINOR = {m['protocol']['minor']}",
          "PROTOCOL_VERSION = (PROTOCOL_MAJOR, PROTOCOL_MINOR)", "", f"MESSAGE_TYPES = {py(m['message_types'])}", ""]
    L.append("# ---- limits")
    for l in m["limits"]:
        v = float(l["value"]) if l["ctype"] == "double" else l["value"]
        L.append(f"{upper(l['name'])} = {py(v)}" + (f"  # {l['unit']}" if l["unit"] else ""))
    L.append("")

    codes = m["error_codes"]
    L.append("# ---- error codes (the order is the order of protocol.json)")
    for c in codes:
        L.append(f"CODE_{upper(c['code'])} = {py(c['code'])}")
    L.append(f"ERROR_CODES = {py([c['code'] for c in codes])}")
    L.append("# wire code -> exception class name in skython.errors")
    L.append(f"REMOTE_EXCEPTIONS = {py({c['code']: c['remote_exception'] for c in codes if c['remote_exception']})}")
    L.append(f"HANDSHAKE_EXCEPTIONS = {py({c['code']: c['handshake_exception'] for c in codes if c['handshake_exception']})}")
    L.append(f"HANDSHAKE_CODES = {py([c['code'] for c in codes if c['context'] in ('handshake', 'both')])}")
    L.append(f"NOT_READY_REASONS = {py(m['not_ready_reasons'])}")
    L.append("")

    f = m["form_ids"]
    L += ["# ---- values, form ids and form keys",
          f"ALIAS_TYPES = {py(m['alias_types'])}", f"DEFAULT_ALIAS_TYPE = {py(m['default_alias_type'])}",
          f"ELEM_TYPES = {py(m['array_elem_types'])}"]
    for k in ("max_local_id", "max_light_local_id", "runtime_prefix", "light_prefix", "max_key_hex_digits", "canonical_hex_digits"):
        L.append(f"FORM_ID_{upper(k)} = {f[k]}")
    L.append(f"PLUGIN_EXTENSIONS = {py(f['plugin_extensions'])}")
    L.append(f"LIGHT_EXTENSION = {py(f['light_extension'])}")
    L.append("")

    L.append("# ---- ops: name -> {batchable, needs_game, exactly_one_of, args: ((name, type, required), ...)}")
    for o in m["ops"]:
        L.append(f"OP_{upper(o['name'])} = {py(o['name'])}")
    ops = {}
    for o in m["ops"]:
        ops[o["name"]] = {"batchable": o["batchable"], "needs_game": o["needs_game"], "exactly_one_of": tuple(o["exactly_one_of"]),
                          "args": tuple((a["name"], a["type"], a["required"]) for a in o["args"])}
    L.append(f"OPS = {py(ops)}")
    L.append(f"BATCHABLE_OPS = {py([o['name'] for o in m['ops'] if o['batchable']])}")
    L.append("")
    L.append("# ---- events: name -> {fields: ((name, type, nullable), ...), filter_fields, filter_required_any_of, deliver_while_loading}")
    for e in m["events"]:
        L.append(f"EVENT_{upper(e['name'])} = {py(e['name'])}")
    events = {}
    for e in m["events"]:
        events[e["name"]] = {"fields": tuple((f["name"], f["type"], f["nullable"]) for f in e["fields"]),
                             "filter_fields": tuple(e["filter_fields"]),
                             "filter_required_any_of": tuple(e["filter_required_any_of"]),
                             "deliver_while_loading": e["deliver_while_loading"]}
    L.append(f"EVENTS = {py(events)}")
    L.append(f"SAVE_NAME_PATTERN = {py(m['save_name_pattern'])}")
    L.append("")

    L.append("# ---- config keys (the plugin ini) and their defaults")
    keys = {}
    for k in m["config_keys"]:
        e = {"type": k["type"], "default": k["default"]}
        if k["type"] in ("u16", "u32"):
            e["min"], e["max"] = k["min"], k["max"]
        keys[k["name"]] = e
    L.append(f"CONFIG_KEYS = {py(keys)}")
    L.append("")

    d = m["discovery"]
    L.append("# ---- discovery")
    for key in ("host", "session_dir_name", "session_file_name", "env_session", "env_port", "env_token"):
        L.append(f"{upper(key)} = {py(d[key])}")
    L.append(f"SESSION_FILE_FIELDS = {py(d['session_file_fields'])}")
    L.append("")

    c = m["client"]
    L.append("# ---- client defaults")
    L.append(f"CLIENT_NAME = {py(c['name'])}")
    for key in ("connect_timeout_s", "call_timeout_s", "send_timeout_s", "retry_not_ready_s", "ready_timeout_s", "grace_s"):
        L.append(f"{upper(key)} = {py(float(c[key]))}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------- Markdown
def md_cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_md(m: dict) -> str:
    p = m["protocol"]
    L = [f"<!-- {BANNER}. Source: {SOURCE}. Regenerate with: {REGEN} -->", "",
         "# Protocol v1 tables (generated)", "",
         f"> {BANNER}. These tables are written by `{REGEN}` from `{SOURCE}`; edit that file, regenerate, and commit all of the outputs. "
         "`PROTOCOL.md` explains the rules; this file holds every number and name.", "",
         f"Protocol version: **{p['major']}.{p['minor']}** (a major mismatch is refused; a newer minor is accepted).", "",
         "## Message types", "", ", ".join(f"`{t}`" for t in m["message_types"]), "",
         "## Error codes", "",
         "| Code | Sent in | Python exception | Meaning |", "|---|---|---|---|"]
    for c in m["error_codes"]:
        where = {"response": "response", "handshake": "handshake_error", "both": "response and handshake_error"}[c["context"]]
        exc = " / ".join(f"`{x}`" for x in (c["remote_exception"], c["handshake_exception"]) if x) or "-"
        L.append(f"| `{c['code']}` | {where} | {exc} | {md_cell(c['doc'])} |")
    L += ["", "An unknown code from a newer server maps to the generic `RemoteError` (or `HandshakeError`).", "",
          "`not_ready` detail: `{\"reason\": " + "|".join(f'"{r}"' for r in m["not_ready_reasons"]) + "}`.", "",
          "## Limits", "", "| Name | Value | Unit | Meaning |", "|---|---|---|---|"]
    for l in m["limits"]:
        v = l["value"]
        shown = f"{v}" if l["ctype"] == "double" else f"{v:,}".replace(",", " ")
        if l["unit"] == "bytes" and v >= 1024:
            shown += f" ({human_bytes(v)})"
        L.append(f"| `{l['name']}` | {shown} | {l['unit'] or '-'} | {md_cell(l['doc'])} |")
    f = m["form_ids"]
    L += ["", "## Values", "",
          f"- Alias types: {', '.join('`%s`' % t for t in m['alias_types'])} (default `{m['default_alias_type']}`).",
          f"- Array element types: {', '.join('`%s`' % t for t in m['array_elem_types'])}.",
          f"- Plugin extensions in a form key: {', '.join('`%s`' % e for e in f['plugin_extensions'])} (case-insensitive); "
          f"`{f['light_extension']}` plugins have local ids up to `0x{f['max_light_local_id']:X}`, the others up to `0x{f['max_local_id']:X}`.",
          f"- A form id whose top byte is `0x{f['runtime_prefix']:02X}` is runtime-created (no form key); `0x{f['light_prefix']:02X}` is a light form.",
          f"- A form key has at most {f['max_key_hex_digits']} hex digits after `0x`; the canonical text has {f['canonical_hex_digits']} uppercase digits.",
          "", "## Ops", ""]
    for o in m["ops"]:
        L.append(f"### `{o['name']}`")
        L.append("")
        L.append(o["doc"] + (" May be used as a step of `papyrus.batch`." if o["batchable"] else "")
                 + (" Refused with `not_ready` until a game is loaded and interactive." if o["needs_game"] else
                    " Answered at any time, also with no game loaded."))
        L.append("")
        if o["args"]:
            L += ["| Argument | Type | Required | Default | Notes |", "|---|---|---|---|---|"]
            for a in o["args"]:
                req = "yes" if a["required"] else "no"
                if a["name"] in o["exactly_one_of"]:
                    req = "one of"
                dflt = "-" if "default" not in a else ("true" if a["default"] is True else "false" if a["default"] is False else str(a["default"]))
                L.append(f"| `{a['name']}` | `{a['type']}` | {req} | {dflt} | {md_cell(a['doc'])} |")
            if o["exactly_one_of"]:
                L += ["", "Give exactly one of " + ", ".join(f"`{n}`" for n in o["exactly_one_of"]) + "."]
        else:
            L.append("No arguments.")
        L += ["", "Result: " + o["result"], ""]
    L += ["### Argument types", "", "| Type | Meaning |", "|---|---|"]
    for t, (_, meaning) in ARG_TYPES.items():
        L.append(f"| `{t}` | {md_cell(meaning)} |")
    L += ["", "## Events", "",
          "Every event frame's `data` object has exactly these fields; `?` marks a field that may be `null`. Form fields are form "
          "references. A subscription filter may name only the filter fields; where a row lists fields under \"Filter required\", "
          "the filter must name at least one of them. Events not delivered while loading are counted in `suppressed` instead "
          "(PROTOCOL.md, Events).", "",
          "| Event | Data fields | Filter fields | Filter required (one of) | Delivered while loading | Engine source | Meaning |",
          "|---|---|---|---|---|---|---|"]
    for e in m["events"]:
        fields = ", ".join(f"`{f['name']}` {f['type']}{'?' if f['nullable'] else ''}" for f in e["fields"]) or "(none)"
        ff = ", ".join(f"`{x}`" for x in e["filter_fields"]) or "-"
        req = ", ".join(f"`{x}`" for x in e["filter_required_any_of"]) or "-"
        L.append(f"| `{e['name']}` | {fields} | {ff} | {req} | {'yes' if e['deliver_while_loading'] else 'no'} | "
                 f"{md_cell(e['source'])} | {md_cell(e['doc'])} |")
    L += ["", f"Save names (`game.save`, `game.load`) match `{m['save_name_pattern']}`."]
    L += ["", "## Plugin ini keys (`Skython.ini`)", "", "| Key | Type | Default | Range | Meaning |", "|---|---|---|---|---|"]
    for k in m["config_keys"]:
        dflt = ("true" if k["default"] else "false") if k["type"] == "bool" else (f"`{k['default']}`" if k["default"] != "" else "(empty)")
        if k["type"] in ("u16", "u32"):
            dflt = f"{k['default']}"
        rng = f"{k['min']}..{k['max']}" if k["type"] in ("u16", "u32") else "-"
        L.append(f"| `{k['name']}` | {k['type']} | {dflt} | {rng} | {md_cell(k['doc'])} |")
    d = m["discovery"]
    L += ["", "## Discovery", "",
          "Dev bridge only; an in-process script needs no discovery.", "",
          f"- Host: `{d['host']}`. Session file: `{d['session_dir_name']}/{d['session_file_name']}` under the game's SKSE log directory unless `session_file` says otherwise.",
          f"- Session file fields: {', '.join('`%s`' % x for x in d['session_file_fields'])}.",
          f"- Environment variables: `{d['env_session']}` (path of a session file), or `{d['env_port']}` together with `{d['env_token']}`.", "",
          "## Client defaults", "", "Used by `skython.Game` in both modes; `connect_timeout_s` and `send_timeout_s` apply to the dev bridge only.", "",
          "| Setting | Value (seconds) |", "|---|---|"]
    c = m["client"]
    for key in ("connect_timeout_s", "call_timeout_s", "send_timeout_s", "retry_not_ready_s", "ready_timeout_s", "grace_s"):
        L.append(f"| `{key}` | {float(c[key])} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------- driver
RENDERERS = {"cpp": render_cpp, "py": render_py, "md": render_md}


def render_all(model: dict) -> dict[str, str]:
    """relative output path -> exact text. Pure and deterministic."""
    return {OUTPUTS[k]: RENDERERS[k](model) for k in OUTPUTS}


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")  # a Windows checkout with autocrlf must not count as drift


def run(root: pathlib.Path, check: bool, out=sys.stdout, err=sys.stderr) -> int:
    try:
        model = load(root / SOURCE)
    except SchemaError as e:
        print(f"gen_protocol: {SOURCE} is invalid:", file=err)
        for problem in e.problems:
            print(f"  - {problem}", file=err)
        return 2
    rendered = render_all(model)
    if check:
        drift = 0
        for rel, text in rendered.items():
            path = root / rel
            if not path.is_file():
                print(f"MISSING: {rel} (run: {REGEN})", file=err)
                drift += 1
                continue
            have = _norm(path.read_bytes()).decode("utf-8", "replace")
            if have != text:
                drift += 1
                print(f"DRIFT: {rel} differs from what {SOURCE} generates (run: {REGEN}):", file=err)
                err.writelines(difflib.unified_diff(have.splitlines(True), text.splitlines(True),
                                                    fromfile=f"{rel} (committed)", tofile=f"{rel} (generated)"))
        if drift:
            print(f"gen_protocol: {drift} generated file(s) out of date", file=err)
            return 1
        print(f"gen_protocol: {len(rendered)} generated files are up to date", file=out)
        return 0
    for rel, text in rendered.items():
        path = root / rel
        data = text.encode("utf-8")
        if path.is_file() and path.read_bytes() == data:
            print(f"unchanged {rel}", file=out)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {rel}", file=out)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the protocol artifacts from protocol/protocol.json.")
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 and print a diff if a generated file is stale")
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1],
                    help="repository root (default: the parent of tools/)")
    ns = ap.parse_args(argv)
    return run(ns.root, ns.check)


if __name__ == "__main__":
    sys.exit(main())
