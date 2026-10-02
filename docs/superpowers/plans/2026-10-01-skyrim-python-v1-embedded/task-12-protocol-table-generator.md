# Task 12: Protocol table generator (`protocol.json` is the single source of truth)

Adapted from: branch `plan-out-of-process`, task 7b (`task-07b-protocol-table-generator.md`).

> Part of the [Skython v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2-4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 11 (the fixtures and `PROTOCOL.md`). **Tasks 13-24 depend on this task**: the C++ core, the `_skython` module and the Python package consume the files it generates, and none of them re-types a code, a limit or a default.

**Why this task exists.** The protocol's facts are needed in C++ (the request parser, `ErrorCode`, the dispatcher's readiness rule, the ini parser) and in Python (exception classes, the bridge's handshake limits, the client's timeouts and op table). Written by hand in several places they drift (the out-of-process plan once had a client that allowed a `timeout_ms` the server refused). This task writes each fact once, in `protocol/protocol.json`, and generates the rest.

**What changed from the out-of-process plan.** Every op carries `needs_game` (false only for `game.state`), which core's `Dispatcher` (Task 18) uses to answer `not_ready` without reaching the backend; the C++ `OpSpec` gains `bool needs_game` after `batchable`, and the Python `OPS` entries gain `"needs_game"`. A new limit `max_array_items` (128, the Papyrus VM's array limit) is generated as `spy::kMaxArrayItems` / `MAX_ARRAY_ITEMS`. The ini keys are now exactly the fields of `spy::Config` in the index Contracts (`python_enabled`, `scripts_enabled`, `bridge_enabled`, `port`, `fixed_token`, `session_file`, `default_timeout_ms`, `max_inflight_per_connection`, `max_inflight_total`, `enable_console`); `allow_external_clients` is gone. The transport limits (frame sizes, hello timeout, connections, queued bytes, token length) stay, now used by the Python dev bridge (Task 23) instead of a C++ IO thread. Tests run on Python 3.12 and 3.14.

**Files:**
- Create: `protocol/protocol.json`
- Create: `tools/gen_protocol.py`, `tools/test_gen_protocol.py`
- Generate and commit (never edit by hand): `core/include/spy/protocol_generated.h`, `python/src/skython/_protocol_generated.py`, `protocol/PROTOCOL.generated.md`

**Interfaces:**
- Produces `protocol/protocol.json`, format 1. Top-level sections: `protocol` (major, minor); `message_types`; `error_codes` (code, `context` of `response`, `handshake` or `both`, the Python `remote_exception` and `handshake_exception` class names, one-line meaning; **the list order is the C++ enum order**); `not_ready_reasons`; `limits` (name, value, C++ type, unit, meaning: max frame, pre-auth frame, name length, batch size, timeout range, error-message cap, client-name cap, connection cap, hello timeout, JSON depth, queued bytes per connection, token length, Papyrus Int and Float range, array items); `form_ids` (local-id ceilings, runtime and light prefixes, plugin extensions); `alias_types`, `default_alias_type`, `array_elem_types`; `config_keys` (the plugin ini: type, default, range); `discovery` (host, session file and directory names, session-file fields, environment variable names; dev bridge only); `client` (default timeouts); `ops` (name, `batchable`, `needs_game`, `exactly_one_of`, arguments with type and required flag, result).
- Produces `tools/gen_protocol.py` (standard library only; tested on Python 3.12 and 3.14):
  - `python3 tools/gen_protocol.py` writes the three generated files; `--check` writes nothing, prints `DRIFT: <file> ...` with a unified diff for each stale or `MISSING` file to stderr and exits 1, and otherwise prints `gen_protocol: 3 generated files are up to date`; `--root DIR` points at another checkout (the tests use it). An invalid `protocol.json` prints every problem and exits 2. Output is deterministic: lists keep the order of `protocol.json`, no timestamps, LF line endings (`--check` ignores CRLF, so a Windows checkout with `autocrlf` does not count as drift). Each generated file opens with `GENERATED FILE - DO NOT EDIT`, the source file name and the regenerate command.
  - Importable: `validate(model) -> list[str]`, `load(path)`, `render_cpp/render_py/render_md(model) -> str`, `render_all(model) -> {relative path: text}`, `run(root, check)`, `human_bytes(n)`, `SOURCE`, `OUTPUTS`.
- Produces, for C++ (`core/include/spy/protocol_generated.h`, header only, standard library only): `spy::kProtocolMajor/Minor`; one `inline constexpr` per limit named `k<CamelName>` (`kMaxFrameBytes`, `kPreAuthFrameBytes`, `kMaxNameBytes`, `kMaxBatchCalls`, `kMinTimeoutMs`, `kMaxTimeoutMs`, `kMaxErrorMessageBytes`, `kMaxClientNameBytes`, `kMaxConnections`, `kHelloTimeoutMs`, `kMaxJsonDepth`, `kMaxQueuedBytesPerConnection`, `kTokenLength`, `kIntMin`, `kIntMax`, `kFloatMaxText`, `kMaxArrayItems`, each with the type `protocol.json` names); `enum class spy::ErrorCode` and `spy::kErrorCodeNames` (`std::array<ErrorCodeName{code, name}, N>`); `spy::kMessageTypes`, `kNotReadyReasons`, `kAliasTypes`, `kDefaultAliasType`, `kArrayElemTypes`; `namespace spy::form_ids`; the op table `spy::kOps` (`OpSpec{name, batchable, needs_game, args, exactly_one_of}`, `ArgSpec{name, type, required}`, `ArgType`), `spy::find_op(name)` and `spy::ops::k<CamelOp>` name constants (`kGameState`, `kFormResolve`, `kPapyrusCallStatic`, `kPapyrusCallMethod`, `kPapyrusGetProperty`, `kPapyrusSetProperty`, `kPapyrusBatch`); the config table `spy::kConfigKeys` (`ConfigKey{name, type, min, max, default_text}`, `ConfigType { u16, u32, boolean, token, path }`), `spy::find_config_key(name)` and `spy::config_defaults::k<CamelKey>` (`kPythonEnabled`, `kScriptsEnabled`, `kBridgeEnabled`, `kPort`, `kFixedToken`, `kSessionFile`, `kDefaultTimeoutMs`, `kMaxInflightPerConnection`, `kMaxInflightTotal`, `kEnableConsole`); `namespace spy::discovery`.
- Produces, for Python (`skython._protocol_generated`, plain constants and tuples, no logic): `PROTOCOL_VERSION`, one `UPPER_SNAKE` constant per limit (including `MAX_ARRAY_ITEMS`), `CODE_<NAME>` per error code, `ERROR_CODES`, `REMOTE_EXCEPTIONS` and `HANDSHAKE_EXCEPTIONS` (wire code to exception class name), `HANDSHAKE_CODES`, `NOT_READY_REASONS`, `ALIAS_TYPES`, `DEFAULT_ALIAS_TYPE`, `ELEM_TYPES`, `FORM_ID_*`, `PLUGIN_EXTENSIONS`, `LIGHT_EXTENSION`, `OP_<NAME>` per op, `OPS` (`name -> {batchable, needs_game, exactly_one_of, args: ((name, type, required), ...)}`), `BATCHABLE_OPS`, `CONFIG_KEYS`, the discovery names (`HOST`, `SESSION_DIR_NAME`, `SESSION_FILE_NAME`, `ENV_SESSION`, `ENV_PORT`, `ENV_TOKEN`, `SESSION_FILE_FIELDS`) and the client defaults (`CALL_TIMEOUT_S`, `CONNECT_TIMEOUT_S`, `SEND_TIMEOUT_S`, `RETRY_NOT_READY_S`, `READY_TIMEOUT_S`, `GRACE_S`, `CLIENT_NAME`).
- Produces `protocol/PROTOCOL.generated.md`: the human-readable tables (error codes, limits, values, ops with argument tables and whether each needs a game, ini keys, discovery, client defaults) that `protocol/PROTOCOL.md` links to.
- **How to change the protocol afterwards:** edit `protocol/protocol.json`; run `python3 tools/gen_protocol.py`; if a rule changed, change `PROTOCOL.md` and the fixtures in the same commit; run the C++ and Python suites; commit the JSON and the three generated files together. A generated file that differs from the generator's output fails `--check`, and the generator's tests run that check against the real repository (`test_the_committed_generated_files_are_current`). Generated files are committed so the Windows build box never needs Python or the generator.
- The tests in this task also check `protocol.json` against the golden fixtures (every op has a valid fixture, fixtures use only known argument names and error codes, the fixtures sit exactly on the limits, including both sides of `max_array_items`), pin the two rules the index Contracts fix (`game.state` is the only op without `needs_game`; the ini keys and their defaults), and check that `PROTOCOL.md` links the generated tables and repeats none of their numbers. Tasks 16 and 20 add the same agreement tests on the C++ and Python sides.

- [ ] **Step 1: Write `protocol/protocol.json`**

`protocol/protocol.json`:
```json
{
  "format": 1,
  "protocol": {"major": 1, "minor": 0},
  "message_types": ["hello", "welcome", "handshake_error", "request", "response", "event"],
  "error_codes": [
    {"code": "not_ready", "context": "response", "remote_exception": "NotReady", "handshake_exception": null,
     "doc": "The game cannot run the request yet; `detail.reason` says why (see not_ready_reasons)."},
    {"code": "busy", "context": "response", "remote_exception": "Busy", "handshake_exception": null,
     "doc": "An in-flight cap was reached (the plugin-wide one, or one bridge connection's); retry later."},
    {"code": "bad_request", "context": "both", "remote_exception": "BadRequest", "handshake_exception": null,
     "doc": "The message is malformed: wrong field types, missing or unknown op, bad arguments, nesting deeper than max_json_depth, a duplicate in-flight id on one bridge connection."},
    {"code": "unknown_function", "context": "response", "remote_exception": "UnknownFunction", "handshake_exception": null,
     "doc": "The script or function does not exist in the running game."},
    {"code": "type_mismatch", "context": "response", "remote_exception": "TypeMismatch", "handshake_exception": null,
     "doc": "A value has the wrong Papyrus type or is out of range (Int outside 32 bits, Float outside 32-bit range)."},
    {"code": "stale_reference", "context": "response", "remote_exception": "StaleReference", "handshake_exception": null,
     "doc": "A runtime-created reference no longer names the object it was handed out for (fingerprint mismatch)."},
    {"code": "not_found", "context": "response", "remote_exception": "NotFound", "handshake_exception": null,
     "doc": "A form, alias, quest or property does not exist."},
    {"code": "timeout", "context": "response", "remote_exception": "Timeout", "handshake_exception": null,
     "doc": "The call did not finish within its timeout_ms (or the caller stopped waiting first: detail.late_cancel is true). It may still run in the game."},
    {"code": "vm_error", "context": "response", "remote_exception": "VmError", "handshake_exception": null,
     "doc": "The Papyrus VM refused or failed the call."},
    {"code": "unsupported", "context": "response", "remote_exception": "Unsupported", "handshake_exception": null,
     "doc": "The request is valid protocol but this plugin build cannot do it."},
    {"code": "internal", "context": "response", "remote_exception": "InternalError", "handshake_exception": null,
     "doc": "A bug or an unexpected failure inside the plugin; never caused by the caller alone."},
    {"code": "unauthorized", "context": "handshake", "remote_exception": null, "handshake_exception": "Unauthorized",
     "doc": "Wrong token. Usually a stale session file from an earlier game run."},
    {"code": "protocol_mismatch", "context": "handshake", "remote_exception": null, "handshake_exception": "ProtocolMismatch",
     "doc": "Client and plugin speak different protocol major versions."}
  ],
  "not_ready_reasons": ["starting", "no_game", "loading", "paused"],
  "limits": [
    {"name": "max_frame_bytes", "value": 16777216, "ctype": "uint32", "unit": "bytes",
     "doc": "Dev bridge: largest frame payload either side accepts. A larger header closes the connection."},
    {"name": "pre_auth_frame_bytes", "value": 65536, "ctype": "uint32", "unit": "bytes",
     "doc": "Dev bridge: frame cap until the hello is accepted; a valid hello is well under 1 KiB."},
    {"name": "max_name_bytes", "value": 256, "ctype": "size", "unit": "bytes",
     "doc": "Longest script, function, property or editor_id string."},
    {"name": "max_batch_calls", "value": 256, "ctype": "size", "unit": "calls",
     "doc": "Most calls in one papyrus.batch (the least is 1)."},
    {"name": "min_timeout_ms", "value": 1, "ctype": "uint32", "unit": "ms",
     "doc": "Smallest timeout_ms."},
    {"name": "max_timeout_ms", "value": 3600000, "ctype": "uint32", "unit": "ms",
     "doc": "Largest timeout_ms; also the largest default_timeout_ms."},
    {"name": "max_error_message_bytes", "value": 1024, "ctype": "size", "unit": "bytes",
     "doc": "Error messages are clipped to this many bytes, so a response can never exceed the frame limit."},
    {"name": "max_client_name_bytes", "value": 64, "ctype": "size", "unit": "bytes",
     "doc": "The hello's client name is truncated to this many bytes."},
    {"name": "max_connections", "value": 32, "ctype": "size", "unit": "connections",
     "doc": "Dev bridge: simultaneous client connections; further ones are closed at accept."},
    {"name": "hello_timeout_ms", "value": 5000, "ctype": "uint32", "unit": "ms",
     "doc": "Dev bridge: a connection that has not sent an accepted hello by then is closed."},
    {"name": "max_json_depth", "value": 64, "ctype": "size", "unit": "levels",
     "doc": "Deepest array/object nesting accepted in a request. In-process the request fails with bad_request; on the dev bridge the connection is closed."},
    {"name": "max_queued_bytes_per_connection", "value": 67108864, "ctype": "size", "unit": "bytes",
     "doc": "Dev bridge: outbound bytes queued for one connection before it is treated as a stuck reader and closed."},
    {"name": "token_length", "value": 32, "ctype": "size", "unit": "hex characters",
     "doc": "Dev bridge: tokens are 128 random bits as lowercase hex."},
    {"name": "int_min", "value": -2147483648, "ctype": "int32", "unit": "",
     "doc": "Smallest Papyrus Int. Out of range is type_mismatch, never truncation."},
    {"name": "int_max", "value": 2147483647, "ctype": "int32", "unit": "",
     "doc": "Largest Papyrus Int; also the largest alias_id and the largest hello protocol number."},
    {"name": "float_max_text", "value": 3.4028235e38, "ctype": "double", "unit": "",
     "doc": "Largest Papyrus Float magnitude, as the shortest text that round-trips FLT_MAX; a larger magnitude is type_mismatch."},
    {"name": "max_array_items", "value": 128, "ctype": "size", "unit": "items",
     "doc": "Most items in a Papyrus array (the VM's own limit); a longer array argument is type_mismatch."}
  ],
  "form_ids": {
    "max_local_id": 16777215,
    "max_light_local_id": 4095,
    "runtime_prefix": 255,
    "light_prefix": 254,
    "max_key_hex_digits": 8,
    "canonical_hex_digits": 6,
    "plugin_extensions": [".esm", ".esp", ".esl"],
    "light_extension": ".esl"
  },
  "alias_types": ["ReferenceAlias", "LocationAlias"],
  "default_alias_type": "ReferenceAlias",
  "array_elem_types": ["Bool", "Int", "Float", "String", "Form", "Alias"],
  "config_keys": [
    {"name": "python_enabled", "type": "bool", "default": true,
     "doc": "Start the embedded Python interpreter at kDataLoaded. Off means the plugin loads but runs no Python."},
    {"name": "scripts_enabled", "type": "bool", "default": true,
     "doc": "Load player scripts from the scripts folder at startup."},
    {"name": "bridge_enabled", "type": "bool", "default": false,
     "doc": "Run the dev bridge (loopback TCP for an external process). Off in the shipped ini, on in the dev ini."},
    {"name": "port", "type": "u16", "default": 45977, "min": 0, "max": 65535,
     "doc": "Dev bridge TCP port on 127.0.0.1; 0 picks a free port."},
    {"name": "fixed_token", "type": "token", "default": "",
     "doc": "Development only: a fixed 32-hex bridge token. Empty means a random token per start."},
    {"name": "session_file", "type": "path", "default": "",
     "doc": "Where the bridge writes its session file. Empty means <SKSE log dir>/Skython/session.json."},
    {"name": "default_timeout_ms", "type": "u32", "default": 5000, "min": 1, "max": 3600000,
     "doc": "Timeout applied to a call that sends none."},
    {"name": "max_inflight_per_connection", "type": "u32", "default": 256, "min": 1, "max": 4294967295,
     "doc": "Requests one dev-bridge connection may have in flight."},
    {"name": "max_inflight_total", "type": "u32", "default": 1024, "min": 1, "max": 4294967295,
     "doc": "Requests in flight in the whole plugin (in-process and bridge together)."},
    {"name": "enable_console", "type": "bool", "default": false,
     "doc": "Allow native.console (M2)."}
  ],
  "discovery": {
    "host": "127.0.0.1",
    "session_dir_name": "Skython",
    "session_file_name": "session.json",
    "session_file_fields": ["port", "token", "pid", "plugin_version", "protocol"],
    "env_session": "SKYTHON_SESSION",
    "env_port": "SKYTHON_PORT",
    "env_token": "SKYTHON_TOKEN"
  },
  "client": {
    "name": "skython",
    "connect_timeout_s": 5.0,
    "call_timeout_s": 30.0,
    "send_timeout_s": 30.0,
    "retry_not_ready_s": 30.0,
    "ready_timeout_s": 120.0,
    "grace_s": 2.0
  },
  "ops": [
    {"name": "game.state", "batchable": false, "needs_game": false, "exactly_one_of": [],
     "doc": "The game's readiness and player.",
     "result": "`{\"data_ready\",\"game_loaded\",\"loading\",\"interactive\",\"paused\":bool,\"menus\":[...],\"epoch\":n,\"player\":<form reference or null>,\"game_days_passed\":float or null}`; player and game_days_passed are null unless a game is loaded.",
     "args": []},
    {"name": "form.resolve", "batchable": false, "needs_game": true, "exactly_one_of": ["form_key", "form_id", "editor_id"],
     "doc": "Name a form three ways; get back a form reference with its type and form key.",
     "result": "a form reference",
     "args": [
       {"name": "form_key", "type": "form_key", "required": false, "doc": "`Plugin.esp|0x0012AB`"},
       {"name": "form_id", "type": "u32", "required": false, "doc": "load-order form id"},
       {"name": "editor_id", "type": "name", "required": false, "doc": "only where the game keeps editor ids"}]},
    {"name": "papyrus.call_static", "batchable": true, "needs_game": true, "exactly_one_of": [],
     "doc": "Call a global Papyrus function.",
     "result": "the function's value",
     "args": [
       {"name": "script", "type": "name", "required": true, "doc": ""},
       {"name": "function", "type": "name", "required": true, "doc": ""},
       {"name": "args", "type": "value_list", "required": false, "doc": "arguments, in order; absent means none"},
       {"name": "timeout_ms", "type": "timeout_ms", "required": false, "doc": "absent means the plugin's default_timeout_ms"},
       {"name": "require_unpaused", "type": "bool", "required": false, "default": false, "doc": "refuse with not_ready while a menu pauses the game"}]},
    {"name": "papyrus.call_method", "batchable": true, "needs_game": true, "exactly_one_of": [],
     "doc": "Call a member function on a form or alias.",
     "result": "the function's value",
     "args": [
       {"name": "object", "type": "ref", "required": true, "doc": "a form or alias reference"},
       {"name": "script", "type": "name", "required": true, "doc": "the script type to bind, for example `Actor`"},
       {"name": "function", "type": "name", "required": true, "doc": ""},
       {"name": "args", "type": "value_list", "required": false, "doc": "arguments, in order; absent means none"},
       {"name": "timeout_ms", "type": "timeout_ms", "required": false, "doc": "absent means the plugin's default_timeout_ms"},
       {"name": "require_unpaused", "type": "bool", "required": false, "default": false, "doc": "refuse with not_ready while a menu pauses the game"}]},
    {"name": "papyrus.get_property", "batchable": true, "needs_game": true, "exactly_one_of": [],
     "doc": "Read a script property.",
     "result": "the property's value",
     "args": [
       {"name": "object", "type": "ref", "required": true, "doc": "a form or alias reference"},
       {"name": "script", "type": "name", "required": true, "doc": ""},
       {"name": "property", "type": "name", "required": true, "doc": ""}]},
    {"name": "papyrus.set_property", "batchable": true, "needs_game": true, "exactly_one_of": [],
     "doc": "Write a script property.",
     "result": "null",
     "args": [
       {"name": "object", "type": "ref", "required": true, "doc": "a form or alias reference"},
       {"name": "script", "type": "name", "required": true, "doc": ""},
       {"name": "property", "type": "name", "required": true, "doc": ""},
       {"name": "value", "type": "value", "required": true, "doc": ""}]},
    {"name": "papyrus.batch", "batchable": false, "needs_game": true, "exactly_one_of": [],
     "doc": "Run several papyrus.* steps in order in one round trip. Not a transaction.",
     "result": "`{\"results\":[{\"ok\":true,\"result\":v}|{\"ok\":false,\"error\":{...}}]}`",
     "args": [
       {"name": "calls", "type": "call_list", "required": true, "doc": "`{\"op\",\"args\"}` steps, only ops marked batchable"},
       {"name": "stop_on_error", "type": "bool", "required": false, "default": true, "doc": "stop at the first failed step"},
       {"name": "timeout_ms", "type": "timeout_ms", "required": false, "doc": "one timeout for the whole batch"}]}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tools/test_gen_protocol.py`:
```python
"""Tests for tools/gen_protocol.py. Standard library plus pytest; never touches the committed generated files
(everything that writes works in a pytest tmp_path copy of the repository's protocol/ and tools/)."""
import copy
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

import gen_protocol as gen

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "gen_protocol.py"
FIXTURES = ROOT / "protocol" / "v1" / "fixtures"


def model():
    return json.loads((ROOT / gen.SOURCE).read_text())


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def repo(tmp_path):
    """A scratch repository root holding a copy of protocol.json, with the generated files already written."""
    (tmp_path / "protocol").mkdir()
    shutil.copy(ROOT / gen.SOURCE, tmp_path / gen.SOURCE)
    assert gen.run(tmp_path, check=False, out=open(os.devnull, "w")) == 0
    return tmp_path


def run_cli(root, *args, hashseed="0"):
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    return subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args], capture_output=True, text=True, env=env)


# ---- schema sanity -----------------------------------------------------------------------------------------
def test_the_committed_protocol_json_is_valid():
    assert gen.validate(model()) == []


def cfg(m, name):
    return next(k for k in m["config_keys"] if k["name"] == name)


def mutate(fn):
    m = copy.deepcopy(model())
    fn(m)
    return gen.validate(m)


@pytest.mark.parametrize("name, fn, expect", [
    ("duplicate error code", lambda m: m["error_codes"].append(dict(m["error_codes"][0])), "duplicate error code"),
    ("duplicate exception class", lambda m: m["error_codes"][1].update(remote_exception="NotReady"), "duplicate exception class"),
    ("internal removed", lambda m: m["error_codes"].pop(10), "'internal' is required"),
    ("response code without exception", lambda m: m["error_codes"][0].update(remote_exception=None), "needs a remote_exception"),
    ("code that is not an identifier", lambda m: m["error_codes"][0].update(code="Not Ready"), "lower-case identifier"),
    ("unknown limit ctype", lambda m: m["limits"][0].update(ctype="uint128"), "unknown 'uint128'"),
    ("limit does not fit its type", lambda m: m["limits"][0].update(value=2**40), "does not fit uint32"),
    ("pre-auth cap above the frame cap", lambda m: m["limits"][1].update(value=2**24 + 1), "pre_auth_frame_bytes must not exceed"),
    ("frame cap above the 4-byte prefix", lambda m: m["limits"][0].update(ctype="size", value=2**32), "4-byte length prefix"),
    ("required limit removed", lambda m: m["limits"].pop(3), "'max_batch_calls' is required"),
    ("duplicate limit", lambda m: m["limits"].append(dict(m["limits"][0])), "duplicate limit"),
    ("required limit max_array_items removed", lambda m: m["limits"].pop(-1), "'max_array_items' is required"),
    ("config default outside its range", lambda m: cfg(m, "port").update(default=70000), "outside 0..65535"),
    ("default timeout outside the timeout limits", lambda m: cfg(m, "default_timeout_ms").update(max=7200000), "inside the timeout limits"),
    ("unknown config type", lambda m: cfg(m, "port").update(type="float"), "unknown 'float'"),
    ("bool config key with a non-bool default", lambda m: cfg(m, "bridge_enabled").update(default="no"), "must be a boolean"),
    ("unknown argument type", lambda m: m["ops"][2]["args"][0].update(type="blob"), "unknown argument type"),
    ("duplicate op", lambda m: m["ops"].append(dict(m["ops"][0])), "duplicate op"),
    ("op without needs_game", lambda m: m["ops"][0].pop("needs_game"), "missing 'needs_game'"),
    ("op name without a dot", lambda m: m["ops"][0].update(name="gamestate"), "dotted lower-case op name"),
    ("exactly_one_of names a missing argument", lambda m: m["ops"][1].update(exactly_one_of=["nope"]), "is not an argument"),
    ("exactly_one_of names a required argument", lambda m: m["ops"][1]["args"][0].update(required=True), "must be optional"),
    ("non-ASCII documentation", lambda m: m["limits"][0].update(doc="café"), "non-ASCII"),
    ("default alias type not an alias type", lambda m: m.update(default_alias_type="MagicAlias"), "default_alias_type"),
    ("light extension not in the list", lambda m: m["form_ids"].update(light_extension=".esx"), "light_extension"),
    ("negative protocol version", lambda m: m["protocol"].update(major=-1), "out of range"),
    ("unknown format", lambda m: m.update(format=2), "format 1 only"),
])
def test_schema_problems_are_reported(name, fn, expect):
    problems = mutate(fn)
    assert any(expect in p for p in problems), (name, problems)


def test_an_unreadable_source_is_a_clean_exit_2(tmp_path):
    (tmp_path / "protocol").mkdir()
    (tmp_path / gen.SOURCE).write_text("{not json")
    r = run_cli(tmp_path)
    assert r.returncode == 2 and "invalid" in r.stderr


def test_an_invalid_schema_prints_every_problem_and_exits_2(repo):
    m = model()
    m["error_codes"].append(dict(m["error_codes"][0]))
    m["limits"][0]["ctype"] = "nope"
    (repo / gen.SOURCE).write_text(json.dumps(m))
    r = run_cli(repo, "--check")
    assert r.returncode == 2
    assert "duplicate error code" in r.stderr and "unknown 'nope'" in r.stderr


# ---- output shape and determinism --------------------------------------------------------------------------
def test_every_output_carries_the_banner_and_the_source_name():
    for rel, text in gen.render_all(model()).items():
        head = "\n".join(text.splitlines()[:3])
        assert "GENERATED FILE - DO NOT EDIT" in head and "protocol/protocol.json" in head, rel
        assert text.endswith("\n") and "\r" not in text and "\t" not in text, rel


def test_rendering_is_deterministic_in_process():
    assert gen.render_all(model()) == gen.render_all(copy.deepcopy(model()))


def test_output_bytes_do_not_depend_on_hash_randomisation(tmp_path):
    outs = []
    for seed in ("0", "1", "12345"):
        root = tmp_path / f"seed{seed}"
        (root / "protocol").mkdir(parents=True)
        shutil.copy(ROOT / gen.SOURCE, root / gen.SOURCE)
        r = run_cli(root, hashseed=seed)
        assert r.returncode == 0, r.stderr
        outs.append({rel: (root / rel).read_bytes() for rel in gen.OUTPUTS.values()})
    assert outs[0] == outs[1] == outs[2]


def test_a_second_run_changes_nothing(repo):
    r = run_cli(repo)
    assert r.returncode == 0
    assert r.stdout.count("unchanged") == 3 and "wrote" not in r.stdout


def test_error_code_order_is_the_declaration_order_not_alphabetical():
    m = model()
    header = gen.render_cpp(m)
    pos = [header.index(f"    {c['code']},") for c in m["error_codes"]]
    assert pos == sorted(pos)
    assert [c["code"] for c in m["error_codes"]] != sorted(c["code"] for c in m["error_codes"])


def test_the_generated_python_module_matches_the_source():
    ns = {}
    exec(compile(gen.render_py(model()), "_protocol_generated.py", "exec"), ns)
    m = model()
    assert ns["PROTOCOL_VERSION"] == (m["protocol"]["major"], m["protocol"]["minor"])
    assert ns["ERROR_CODES"] == tuple(c["code"] for c in m["error_codes"])
    for l in m["limits"]:
        assert ns[l["name"].upper()] == l["value"], l["name"]
    assert set(ns["OPS"]) == {o["name"] for o in m["ops"]}
    assert set(ns["REMOTE_EXCEPTIONS"]) | set(ns["HANDSHAKE_EXCEPTIONS"]) <= set(ns["ERROR_CODES"])
    assert ns["BATCHABLE_OPS"] == tuple(o["name"] for o in m["ops"] if o["batchable"])
    assert {n: o["needs_game"] for n, o in ns["OPS"].items()} == {o["name"]: o["needs_game"] for o in m["ops"]}
    assert set(ns["CONFIG_KEYS"]) == {k["name"] for k in m["config_keys"]}


@pytest.mark.skipif(shutil.which("g++") is None, reason="no C++ compiler on this machine")
def test_the_generated_header_compiles_standalone(tmp_path):
    (tmp_path / "t.cpp").write_text('#include "spy/protocol_generated.h"\n'
                                    "static_assert(spy::kErrorCodeNames.size() > 0);\n"
                                    "static_assert(spy::find_op(spy::ops::kGameState) != nullptr);\n"
                                    "static_assert(!spy::find_op(spy::ops::kGameState)->needs_game);\n"
                                    "static_assert(spy::find_op(spy::ops::kPapyrusCallStatic)->needs_game);\n"
                                    "static_assert(spy::kMaxArrayItems == 128u);\n"
                                    "static_assert(!spy::config_defaults::kBridgeEnabled && spy::config_defaults::kPythonEnabled);\n"
                                    "int main() { return 0; }\n")
    inc = tmp_path / "inc" / "spy"
    inc.mkdir(parents=True)
    (inc / "protocol_generated.h").write_text(gen.render_cpp(model()))
    r = subprocess.run(["g++", "-std=c++20", "-Wall", "-Wextra", "-Wconversion", "-pedantic", "-Werror", "-I", str(tmp_path / "inc"),
                        "-fsyntax-only", str(tmp_path / "t.cpp")], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---- the drift check ---------------------------------------------------------------------------------------
def test_check_passes_on_fresh_output(repo):
    r = run_cli(repo, "--check")
    assert r.returncode == 0 and "up to date" in r.stdout


@pytest.mark.parametrize("rel", sorted(gen.OUTPUTS.values()))
def test_mutating_a_generated_file_fails_the_check_and_shows_a_diff(repo, rel):
    path = repo / rel
    text = path.read_text()
    marker = {"core": "16777216u", "python": "16777216", "protocol": "16 777 216"}[rel.split("/")[0]]
    assert marker in text
    path.write_text(text.replace(marker, marker.replace("16", "17", 1), 1))
    r = run_cli(repo, "--check")
    assert r.returncode == 1
    assert f"DRIFT: {rel}" in r.stderr and "\n-" in r.stderr and "\n+" in r.stderr
    assert "(committed)" in r.stderr and "(generated)" in r.stderr
    assert run_cli(repo).returncode == 0 and run_cli(repo, "--check").returncode == 0  # regenerating repairs it


def test_a_missing_generated_file_fails_the_check(repo):
    target = repo / gen.OUTPUTS["md"]
    target.rename(target.with_suffix(".moved"))
    r = run_cli(repo, "--check")
    assert r.returncode == 1 and "MISSING" in r.stderr


def test_editing_the_source_without_regenerating_fails_the_check(repo):
    m = model()
    limit = next(l for l in m["limits"] if l["name"] == "max_batch_calls")
    limit["value"] += 1
    (repo / gen.SOURCE).write_text(json.dumps(m, indent=2))
    r = run_cli(repo, "--check")
    assert r.returncode == 1 and "kMaxBatchCalls" in r.stderr
    assert run_cli(repo).returncode == 0
    assert f"kMaxBatchCalls = {limit['value']}u" in (repo / gen.OUTPUTS["cpp"]).read_text()
    assert run_cli(repo, "--check").returncode == 0


def test_crlf_line_endings_from_a_windows_checkout_are_not_drift(repo):
    for rel in gen.OUTPUTS.values():
        p = repo / rel
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    assert run_cli(repo, "--check").returncode == 0


def test_check_never_writes(repo):
    rel = gen.OUTPUTS["py"]
    (repo / rel).write_text("# tampered\n")
    run_cli(repo, "--check")
    assert (repo / rel).read_text() == "# tampered\n"


def test_the_committed_generated_files_are_current():
    """The real drift check, run against the real repository (this is the test that fails when someone edits
    protocol.json, or a generated file, and forgets the other half)."""
    assert gen.run(ROOT, check=True, out=open(os.devnull, "w"), err=sys.stderr) == 0


# ---- rules the index's Contracts fix ------------------------------------------------------------------------
def test_only_game_state_works_without_a_game():
    assert [o["name"] for o in model()["ops"] if not o["needs_game"]] == ["game.state"]


def test_the_config_keys_are_the_plugin_config_fields_with_their_defaults():
    keys = {k["name"]: k["default"] for k in model()["config_keys"]}
    assert keys == {"python_enabled": True, "scripts_enabled": True, "bridge_enabled": False, "port": 45977, "fixed_token": "",
                    "session_file": "", "default_timeout_ms": 5000, "max_inflight_per_connection": 256,
                    "max_inflight_total": 1024, "enable_console": False}


# ---- protocol.json agrees with the golden fixtures ---------------------------------------------------------
def test_fixture_error_codes_are_in_the_table():
    codes = {c["code"] for c in model()["error_codes"]}
    seen = set()
    for name in ("hello.json", "requests.json", "values.json"):
        seen |= {e["error"] for e in fixture(name)["invalid"]}
    seen |= {c["result"] for c in fixture("hello.json")["check"] if c["result"]}
    for r in fixture("responses.json")["valid"]:
        if r.get("ok") is False:
            seen.add(r["error"]["code"])
        if r.get("type") == "handshake_error":
            seen.add(r["code"])
    assert seen and seen <= codes, seen - codes
    handshake = {c["code"] for c in model()["error_codes"] if c["context"] in ("handshake", "both")}
    assert {c["result"] for c in fixture("hello.json")["check"] if c["result"]} <= handshake


def test_every_op_has_a_valid_fixture_and_fixtures_use_only_known_argument_names():
    ops = {o["name"]: o for o in model()["ops"]}
    seen = {name: set() for name in ops}
    for req in fixture("requests.json")["valid"]:
        assert req["op"] in ops, req["op"]
        names = {a["name"] for a in ops[req["op"]]["args"]}
        assert set(req["args"]) <= names, (req["op"], set(req["args"]) - names)
        seen[req["op"]] |= set(req["args"])
        if req["op"] == "papyrus.batch":
            for step in req["args"]["calls"]:
                assert ops[step["op"]]["batchable"], step["op"]
                assert set(step["args"]) <= {a["name"] for a in ops[step["op"]]["args"]}
    for name, o in ops.items():
        assert seen[name] or not o["args"], f"no valid fixture exercises {name}"
        assert seen[name] >= {a["name"] for a in o["args"] if a["required"]}, f"{name}: no fixture sets every required argument"


def test_fixtures_pin_the_limits_in_the_table_to_the_byte():
    lim = {l["name"]: l["value"] for l in model()["limits"]}
    timeouts = {r["message"]["args"]["timeout_ms"] for r in fixture("requests.json")["invalid"]
                if isinstance(r["message"].get("args"), dict) and "timeout_ms" in r["message"]["args"]}
    assert lim["min_timeout_ms"] - 1 in timeouts and lim["max_timeout_ms"] + 1 in timeouts
    valid_t = {r["args"]["timeout_ms"] for r in fixture("requests.json")["valid"] if "timeout_ms" in r["args"]}
    assert valid_t and all(lim["min_timeout_ms"] <= t <= lim["max_timeout_ms"] for t in valid_t)
    wires = [c["wire"] for c in fixture("values.json")["valid"]]
    bad = [c["wire"] for c in fixture("values.json")["invalid"]]
    assert lim["int_max"] in wires and lim["int_min"] in wires and lim["int_max"] + 1 in bad and lim["int_min"] - 1 in bad
    assert lim["float_max_text"] in wires and 3.5e38 in bad and lim["float_max_text"] < 3.5e38
    alias_ids = [c["wire"]["alias_id"] for c in fixture("values.json")["valid"] if isinstance(c["wire"], dict) and c["wire"].get("kind") == "alias"]
    assert lim["int_max"] in alias_ids
    arrays = [c["wire"] for c in fixture("values.json")["valid"] if isinstance(c["wire"], dict) and "array" in c["wire"]]
    long_bad = [c for c in fixture("values.json")["invalid"] if isinstance(c["wire"], dict) and "array" in c["wire"]
                and len(c["wire"]["items"]) > lim["max_array_items"]]
    assert lim["max_array_items"] in [len(a["items"]) for a in arrays]
    assert [len(c["wire"]["items"]) for c in long_bad] == [lim["max_array_items"] + 1] and long_bad[0]["error"] == "type_mismatch"
    hello_tokens = [h["token"] for h in fixture("hello.json")["valid"]]
    assert all(len(t) == lim["token_length"] for t in hello_tokens)
    for h in fixture("hello.json")["valid"]:
        assert h["protocol"][0] == model()["protocol"]["major"]
    f = model()["form_ids"]
    keys = fixture("form_keys.json")
    assert f"Skyrim.esm|0x{f['max_local_id']:X}" in [c["text"] for c in keys["valid"]]
    assert f"Skyrim.esm|0x{f['max_local_id'] + 1:X}" in keys["invalid"]
    assert f"Light Mod.esl|0x{f['max_light_local_id']:X}" in [c["text"] for c in keys["valid"]]
    assert f"Light Mod.esl|0x{f['max_light_local_id'] + 1:X}" in keys["invalid"]


def test_the_fixtures_use_only_known_message_types_and_alias_types():
    m = model()
    for r in fixture("responses.json")["valid"]:
        assert r["type"] in m["message_types"]
    for c in fixture("values.json")["valid"]:
        w = c["wire"]
        if isinstance(w, dict) and w.get("kind") == "alias":
            assert w["alias_type"] in m["alias_types"]
        if isinstance(w, dict) and "array" in w:
            assert w["array"] in m["array_elem_types"]


def test_protocol_md_links_the_generated_tables_and_repeats_no_number_from_them():
    md = (ROOT / "protocol" / "PROTOCOL.md").read_text()
    assert "PROTOCOL.generated.md" in md
    m = model()
    lim = {l["name"]: l["value"] for l in m["limits"]}
    for name, value in lim.items():
        if abs(value) < 32:
            continue  # 1 or 5 would match anywhere
        for text in {str(value), gen.human_bytes(value) if isinstance(value, int) else str(value)}:
            hit = re.search(rf"(?<![\w.]){re.escape(text)}(?![\w.])", md)
            assert not hit, f"PROTOCOL.md repeats {name} ({text!r}); refer to the limit by name instead"
    for c in m["error_codes"]:
        assert f"`{c['code']}` |" not in md, "PROTOCOL.md must not carry its own error-code table"
    assert str(cfg(m, "port")["default"]) not in md  # the default port
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q`
Expected: FAIL at collection, `ModuleNotFoundError: No module named 'gen_protocol'`.

- [ ] **Step 4: Write the generator**

`tools/gen_protocol.py`:
```python
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
}
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
                "max_array_items"):
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
          "", "## Ops (M1)", ""]
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
```

- [ ] **Step 5: Generate the files and run the tests**

Run: `python3 tools/gen_protocol.py`
Expected: `wrote core/include/spy/protocol_generated.h`, `wrote python/src/skython/_protocol_generated.py`, `wrote protocol/PROTOCOL.generated.md`. A second run prints `unchanged` for all three.

Run: `uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q`
Expected: `53 passed`. Run it once more with `--python 3.14`, same count. The run takes about a second and writes nothing outside pytest's temporary directories.

The header compiles on its own; this is also one of the tests (`test_the_generated_header_compiles_standalone`, skipped on a machine without `g++`), and the hand check is: `g++ -std=c++20 -Wall -Wextra -Wconversion -pedantic -fsyntax-only -x c++ -I core/include - <<< '#include "spy/protocol_generated.h"'` prints nothing.

Spot-check the two new facts: `grep -n "kMaxArrayItems\|bool needs_game" core/include/spy/protocol_generated.h` prints `inline constexpr std::size_t kMaxArrayItems = 128u;` and the `bool needs_game;` member of `OpSpec`; `grep -n '"game.state"' python/src/skython/_protocol_generated.py` shows `"needs_game": False`.

- [ ] **Step 6: The drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`, exit status 0.

Prove it can fail (the unit tests do this on a copy; this does it on the real tree and then repairs it): `sed -i 's/kMaxBatchCalls = 256u/kMaxBatchCalls = 255u/' core/include/spy/protocol_generated.h && python3 tools/gen_protocol.py --check; echo "exit $?"`
Expected: a `DRIFT: core/include/spy/protocol_generated.h differs from what protocol/protocol.json generates` message with a diff showing `-...255u` and `+...256u`, `gen_protocol: 1 generated file(s) out of date`, `exit 1`. Then `python3 tools/gen_protocol.py` rewrites the file (`wrote core/include/spy/protocol_generated.h`) and `--check` passes again.

From here on, every task that touches `core/`, `pyglue/` or `python/` ends by running `python3 tools/gen_protocol.py --check`, and Task 32's final run includes it.

- [ ] **Step 7: Commit**

```bash
git add protocol tools core/include python/src && git commit -m "feat(protocol): protocol.json as the single source of truth, generated C++, Python and Markdown tables, drift check"
```
