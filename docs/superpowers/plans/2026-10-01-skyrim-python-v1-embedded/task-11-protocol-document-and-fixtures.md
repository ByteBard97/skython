# Task 11: Protocol v1 document and golden fixtures

Adapted from: branch `plan-out-of-process`, task 7 (`task-07-protocol-v1-document-and-golden-fixtures.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.2-4.4). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** nothing; this Linux task may run in parallel with the M0 spikes. **Task 12 depends on this task** (it generates `protocol/PROTOCOL.generated.md`, which the document below links, and its tests read these fixtures); Tasks 13-24 depend on Task 12.

**What changed from the out-of-process plan.** The message format is unchanged, but it now serves two modes: in-process, the `skyrim` package hands request text to the built-in `_skyrim` module; over the dev bridge, the Python bridge server (Task 23) forwards the same text from a TCP connection. Framing, handshake, token and session file are therefore described as dev-bridge-only. New rules: ops that need a game answer `not_ready` at once (with `detail.reason`, `detail.paused`, `detail.menus`); a call in flight when a save starts loading ends with `not_ready`; `timeout` may carry `detail.late_cancel`; arrays hold at most the `max_array_items` limit (128) items, with a fixture on each side of it; request ids are checked for duplicates per bridge connection only.

**Files:**
- Create: `protocol/PROTOCOL.md` (the rules in prose; every number, name and table lives in `protocol/PROTOCOL.generated.md`, generated in Task 12)
- Create: `protocol/v1/fixtures/hello.json`, `requests.json`, `values.json`, `form_keys.json`, `responses.json`

**Interfaces:**
- Produces: `protocol/PROTOCOL.md`, which links `protocol/PROTOCOL.generated.md` (created by Task 12; until then the link dangles) and says "the `max_frame_bytes` limit" wherever it means a number. Task 12's test fails if this file repeats a limit's value (32 or more) or carries its own error-code table, so a number can live in `protocol.json` only.
- Produces: fixture files with the exact JSON shapes below, loaded from `protocol/v1/fixtures/` by:
  - C++ (core): Task 14 (`values.json`), Task 15 (`form_keys.json`), Task 16 (`requests.json`, `responses.json`, the fixture sweep), Task 18 (replays `requests.json` through `FakeBackend`, optional).
  - Python: Task 20 (`values.json`, `form_keys.json`, `responses.json`, agreement tests), Task 22 (`skyrim.remote` replays `hello.json`, `requests.json`, `responses.json`), Task 23 (the bridge server checks every `hello.json` `invalid` entry and every `check` case).
  - Fixture file format: every file is an object with `"valid"` and/or `"invalid"` arrays. Additive keys (readers that do not know them must ignore them): in `values.json`, a `valid` entry may carry `"canonical"`, the exact JSON the plugin must emit for `wire` when that differs from `wire` (for example `1` in a Float array is emitted as `1.0`); in `hello.json`, a top-level `"check"` array of `{"name","hello","expected_token","result"}` cases for the token-then-major check order (the bridge's hello check, Task 23).
  - C++ compares serialised text, not `json ==`, so `1` and `1.0` are different values; a reader in any language must compare type as well as value for numbers.
- **Decision: the protocol artifact is these fixtures plus `PROTOCOL.md`; there are no JSON Schema files in v1** (spec 4.2 says "exact shapes and golden fixtures live in `protocol/`").

- [ ] **Step 1: Write `protocol/PROTOCOL.md`**

`protocol/PROTOCOL.md`:
````markdown
# skyrim-python protocol v1

Every number and name below is in [PROTOCOL.generated.md](PROTOCOL.generated.md), which is generated from `protocol.json` (error codes, ops and their arguments, limits, defaults). This file explains the rules and says "the `max_frame_bytes` limit" where it means a number. If this text and a generated table seem to disagree, the table is right: report the sentence here.

## Two modes, one message format
The same request and response objects are used in both modes:
- **In-process** (a Python script running inside the game): the `skyrim` package serialises a request object to JSON text and hands it to the built-in `_skyrim` module (`_skyrim.call` or `_skyrim.submit`); it gets the response object back as JSON text. There is no framing, handshake or token.
- **Dev bridge** (an external process, development only): a Python server inside the game (`skyrim.bridge`) accepts TCP connections, checks the handshake below, and forwards each request text unchanged to `_skyrim`; the response text comes back unchanged in a frame. The client is `skyrim.remote`.

So the C++ request parser, op-argument checks and response builders serve both modes, and the golden fixtures in `v1/fixtures/` pin both.

Text: requests must be valid UTF-8. The plugin replaces invalid UTF-8 in its own output (Skyrim strings are often Windows-1252) with U+FFFD rather than failing, so a String result may differ from the game's bytes. A request nested deeper than the `max_json_depth` limit is refused with `bad_request` before it is parsed.

Every message has `"type"`, one of the message types in the generated tables.

## Dev bridge transport
TCP on 127.0.0.1 only. Each message is a frame: 4-byte little-endian unsigned length, then that many bytes of UTF-8 JSON (one object). A frame header over the `max_frame_bytes` limit, a payload that is not a JSON object, or a payload nested deeper than `max_json_depth` closes the connection. Before the hello is accepted the server applies the smaller `pre_auth_frame_bytes` limit; a larger first frame closes the connection. A connection whose unsent replies exceed `max_queued_bytes_per_connection` is treated as a stuck reader and closed.

## Handshake (dev bridge only)
Client first: `{"type":"hello","protocol":[1,0],"token":"<token>","client":"<name>"}`
Server: `{"type":"welcome","protocol":[1,0],"plugin_version":"0.1.0","game_version":"1.6.1170.0","runtime":"AE","epoch":3,"capabilities":["papyrus.call_static", ...]}`
or `{"type":"handshake_error","code":"unauthorized"|"protocol_mismatch"|"bad_request","message":"..."}` and the server closes.
`capabilities` lists the op names the plugin serves. The token is `token_length` lowercase hex characters. The server checks in this order: the hello is well formed (`bad_request`: wrong field types, `protocol` not two integers in 0..`int_max`, token not `token_length` lowercase hex), then the token (`unauthorized`, compared in constant time), then the protocol major (`protocol_mismatch`). A wrong token with a wrong major is therefore `unauthorized`: a caller without the token learns nothing about the version. `client` is optional, must be a string, and is truncated to `max_client_name_bytes` bytes. A connection that has not sent an accepted hello within `hello_timeout_ms` is closed, and at most `max_connections` connections are served at once.

The bridge writes a session file (fields in the generated Discovery table) so a client can find its port and token; a bridge that is not running leaves `{"active": false, "reason": "..."}` there instead, with no port or token.

## Requests and responses
`{"type":"request","id":<uint64>,"op":"<op>","args":{...}}`
`{"type":"response","id":<same>,"ok":true,"result":<value or object>}`
`{"type":"response","id":<same>,"ok":false,"error":{"code":"<code>","message":"...","detail":{...}}}`
Responses may arrive in any order. On a bridge connection, a request whose id is still in flight on that connection is refused with `bad_request`; in-process, the `skyrim` package numbers its own requests and the plugin does not check ids. A request whose text cannot be parsed gets an error response with the id read from the text when one can be found, else id 0. Error messages are truncated to `max_error_message_bytes` and never echo a whole request, so a response cannot exceed the frame limit. Every field of the wrong JSON type (`{"type":5}`, `"op":5`, `"kind":5`, `"client":5`) is `bad_request`; the plugin never answers with anything but a typed error. The error codes and what each means are in the generated tables.

A `not_ready` error carries `detail.reason`, one of the reasons listed there, plus `detail.paused` and `detail.menus`. Ops marked as needing a game get `not_ready` at once, without waiting, while no game is loaded or a save is loading; `game.state` is always answered. When a save starts loading, every call still in flight that needs a game ends with `not_ready` (reason `loading`). A `timeout` error's detail also carries `detail.paused` and `detail.menus`, and may carry `detail.late_cancel: true`, meaning the caller stopped waiting before the plugin's own deadline; the call may still run in the game either way.

## Events (M2)
`{"type":"event","event":"<name>","sub":<id>,"seq":<n>,"epoch":<n>,"data":{...}}`

## Values
null, true/false, integers (Papyrus Int: `int_min`..`int_max`; out of range is `type_mismatch`), floats, strings,
Float is a single-precision float: a finite number with magnitude above `float_max_text` is `type_mismatch`. The plugin prints floats in their shortest round-trip form (0.1 stays `0.1`), always with a decimal point or exponent (`1.0`), and sends a non-finite float as `null`. A Float array accepts integer items.
form reference `{"kind":"form","form_id":20,"form_key":"Skyrim.esm|0x000014","type":"Actor","epoch":1}` (at least one of form_id/form_key; `type`, `epoch` and `fingerprint` are optional but, when present, must have the right type, else `bad_request`; `null` counts as absent). When both form_id and form_key are given they must agree on the local id (the local-id width of a light form is smaller; see Values in the generated tables), and a runtime-created id must not carry a form_key. Runtime-created references carry `"fingerprint":{"base":"<form key or decimal form id>","type":"<script type>"}` and no form_key.
Form key grammar: `<plugin>|0x<hex digits>`; the plugin name ends in one of the plugin extensions, case-insensitively, has a non-empty stem with no leading or trailing whitespace, and contains no control characters; the local id and digit counts are limited as the generated Values section says (light plugins have the smaller local-id range). The canonical text uses uppercase hex digits.
alias reference `{"kind":"alias","quest":<form reference>,"alias_id":0,"alias_type":"ReferenceAlias"|"LocationAlias","epoch":1}` (`alias_id` is an integer 0..`int_max`, never truncated or negative),
array `{"array":"Bool"|"Int"|"Float"|"String"|"Form"|"Alias","items":[...]}` (Float arrays accept integer items; at most `max_array_items` items, more is `type_mismatch`).
Bare JSON arrays are not values.

## Ops (M1)
The ops, their arguments (names, types, which are required, defaults), whether they need a loaded game, and their result shapes are tables in the generated file. Rules that a table cannot show:
- `form.resolve` takes exactly one of its three arguments; two or none is `bad_request`.
- A `timeout_ms` outside the `min_timeout_ms`..`max_timeout_ms` range, or not an integer, is `bad_request`.
- `papyrus.batch` runs its calls in the listed order and returns one result per call (`{"ok":true,"result":v}` or `{"ok":false,"error":{...}}`); a step may only be an op marked batchable, and a batch has 1..`max_batch_calls` calls. It is not a transaction.
- `script`, `function`, `property` and `editor_id` are non-empty strings of at most `max_name_bytes` bytes.
- Unknown argument names are ignored by the plugin.
````

- [ ] **Step 2: Write the fixtures**

`protocol/v1/fixtures/hello.json`:
```json
{
  "valid": [
    {"type": "hello", "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": "test"},
    {"type": "hello", "protocol": [1, 3], "token": "ffffffffffffffffffffffffffffffff", "client": "skyrim-dm"}
  ],
  "invalid": [
    {"message": {"type": "hello", "protocol": [1, 0], "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": "1.0", "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1, 0], "token": "XYZ", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "request", "id": 1, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": 1, "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": 5}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1, 0], "token": 5, "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [4294967297, 0], "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [-1, 0], "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1.5, 0], "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"},
    {"message": {"type": "hello", "protocol": [1, 0, 0], "token": "00112233445566778899aabbccddeeff", "client": "x"}, "error": "bad_request"}
  ],
  "check": [
    {"name": "right token, right major", "hello": {"type": "hello", "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": "t"}, "expected_token": "00112233445566778899aabbccddeeff", "result": null},
    {"name": "a newer minor is accepted", "hello": {"type": "hello", "protocol": [1, 9], "token": "00112233445566778899aabbccddeeff", "client": "t"}, "expected_token": "00112233445566778899aabbccddeeff", "result": null},
    {"name": "wrong token", "hello": {"type": "hello", "protocol": [1, 0], "token": "00112233445566778899aabbccddeeff", "client": "t"}, "expected_token": "ffffffffffffffffffffffffffffffff", "result": "unauthorized"},
    {"name": "wrong major", "hello": {"type": "hello", "protocol": [2, 0], "token": "00112233445566778899aabbccddeeff", "client": "t"}, "expected_token": "00112233445566778899aabbccddeeff", "result": "protocol_mismatch"},
    {"name": "wrong token and wrong major: the token is checked first", "hello": {"type": "hello", "protocol": [2, 0], "token": "00112233445566778899aabbccddeeff", "client": "t"}, "expected_token": "ffffffffffffffffffffffffffffffff", "result": "unauthorized"}
  ]
}
```

`protocol/v1/fixtures/requests.json`:
```json
{
  "valid": [
    {"type": "request", "id": 1, "op": "game.state", "args": {}},
    {"type": "request", "id": 2, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}},
    {"type": "request", "id": 3, "op": "papyrus.call_method", "args": {"object": {"kind": "form", "form_id": 20}, "script": "Actor", "function": "GetActorValue", "args": ["Health"], "timeout_ms": 2000, "require_unpaused": true}},
    {"type": "request", "id": 4, "op": "form.resolve", "args": {"form_key": "Skyrim.esm|0x012EB7"}},
    {"type": "request", "id": 5, "op": "papyrus.batch", "args": {"calls": [{"op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}}], "stop_on_error": true}},
    {"type": "request", "id": 18446744073709551615, "op": "game.state", "args": {}},
    {"type": "request", "id": 30, "op": "papyrus.get_property", "args": {"object": {"kind": "form", "form_id": 83888128}, "script": "SkyrimPythonTestScript", "property": "Counter"}},
    {"type": "request", "id": 31, "op": "papyrus.set_property", "args": {"object": {"kind": "alias", "quest": {"kind": "form", "form_id": 83888128}, "alias_id": 0, "alias_type": "ReferenceAlias"}, "script": "ReferenceAlias", "property": "Tag", "value": 7}},
    {"type": "request", "id": 32, "op": "form.resolve", "args": {"editor_id": "IronSword"}},
    {"type": "request", "id": 33, "op": "form.resolve", "args": {"form_id": 20}}
  ],
  "invalid": [
    {"message": {"type": "request", "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": -1, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 6, "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 7, "op": "game.state", "args": []}, "error": "bad_request"},
    {"message": {"type": "request", "id": 8, "op": "papyrus.call_static", "args": {"script": "Utility", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 9, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 2147483648]}}, "error": "type_mismatch"},
    {"message": {"type": "request", "id": 10, "op": "papyrus.call_method", "args": {"script": "Actor", "function": "Kill", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 11, "op": "form.resolve", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 12, "op": "no.such.op", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 13, "op": "papyrus.batch", "args": {"calls": [{"op": "game.state", "args": {}}], "stop_on_error": true}}, "error": "bad_request"},
    {"message": {"type": 5, "id": 14, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": "15", "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 1.5, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 18446744073709551616, "op": "game.state", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 16, "op": 5, "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 17, "op": "game.state", "args": null}, "error": "bad_request"},
    {"message": {"type": "request", "id": 18, "op": "papyrus.call_static", "args": {"script": 5, "function": "RandomInt", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 19, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [], "timeout_ms": 0}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 20, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [], "timeout_ms": "2000"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 21, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [], "timeout_ms": 3600001}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 22, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [], "require_unpaused": "yes"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 23, "op": "papyrus.call_method", "args": {"object": {"kind": 5}, "script": "Actor", "function": "Kill", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 24, "op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [{"kind": 5}]}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 25, "op": "papyrus.call_method", "args": {"object": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": -1}, "script": "ReferenceAlias", "function": "GetRef", "args": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 26, "op": "papyrus.call_static", "args": {"script": "Math", "function": "Abs", "args": [3.5e38]}}, "error": "type_mismatch"},
    {"message": {"type": "request", "id": 27, "op": "papyrus.batch", "args": {"calls": [{"op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}}], "stop_on_error": "yes"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 28, "op": "papyrus.batch", "args": {"calls": []}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 29, "op": "form.resolve", "args": {"form_key": "Skyrim.esm|0x1", "form_id": 1}}, "error": "bad_request"}
  ]
}
```

The `requests.json` fixtures include a valid request for every op (Task 12's coverage test fails for an op that no fixture exercises), and Task 22 replays them through `skyrim.remote`.

`values.json` has one long line on each side of the array limit: a valid `Int` array of exactly 128 items (0..127) and an invalid one of 129 items (0..128, `type_mismatch`). Task 12's limit test checks both lengths against `max_array_items`.

`protocol/v1/fixtures/values.json`:
```json
{
  "valid": [
    {"wire": null, "kind": "null"},
    {"wire": true, "kind": "bool"},
    {"wire": 0, "kind": "int"},
    {"wire": 2147483647, "kind": "int"},
    {"wire": -2147483648, "kind": "int"},
    {"wire": 1.5, "kind": "float"},
    {"wire": 1.0, "kind": "float"},
    {"wire": 0.1, "kind": "float"},
    {"wire": 3.4028235e38, "kind": "float"},
    {"wire": -3.4028235e38, "kind": "float"},
    {"wire": "Iron Sword", "kind": "string"},
    {"wire": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}, "kind": "form"},
    {"wire": {"kind": "form", "form_id": 4278190082, "type": "ObjectReference", "epoch": 2, "fingerprint": {"base": "Skyrim.esm|0x012EB7", "type": "ObjectReference"}}, "kind": "form"},
    {"wire": {"kind": "form", "form_key": "Skyrim.esm|0x01A69F"}, "kind": "form"},
    {"wire": {"kind": "form", "form_id": 83888128, "form_key": "SkyrimPythonTest.esp|0x000800"}, "kind": "form"},
    {"wire": {"kind": "form", "form_id": 4261415612, "form_key": "Light Mod.esl|0x000ABC"}, "kind": "form"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 83888128, "form_key": "SkyrimPythonTest.esp|0x000800", "type": "Quest", "epoch": 1}, "alias_id": 0, "alias_type": "ReferenceAlias", "epoch": 1}, "kind": "alias"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 83888128}, "alias_id": 2147483647, "alias_type": "LocationAlias"}, "kind": "alias"},
    {"wire": {"array": "Int", "items": [1, 2, 3]}, "kind": "array"},
    {"wire": {"array": "Float", "items": [1, 2.5]}, "canonical": {"array": "Float", "items": [1.0, 2.5]}, "kind": "array"},
    {"wire": {"array": "Form", "items": [{"kind": "form", "form_id": 20}]}, "kind": "array"},
    {"wire": {"array": "Int", "items": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127]}, "kind": "array"}
  ],
  "invalid": [
    {"wire": 2147483648, "error": "type_mismatch"},
    {"wire": -2147483649, "error": "type_mismatch"},
    {"wire": 3.5e38, "error": "type_mismatch"},
    {"wire": -3.5e38, "error": "type_mismatch"},
    {"wire": {"array": "Float", "items": [1.5, 3.5e38]}, "error": "type_mismatch"},
    {"wire": {"array": "Float", "items": [1e39]}, "error": "type_mismatch"},
    {"wire": {"kind": "form"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_key": "nonsense"}, "error": "bad_request"},
    {"wire": {"kind": "widget"}, "error": "bad_request"},
    {"wire": {"kind": 5}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "type": 5}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "epoch": "1"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "epoch": -1}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "fingerprint": "x"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "fingerprint": {"base": "Skyrim.esm|0x000014"}}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000015"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 4261415612, "form_key": "Light Mod.esl|0x000123"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_id": 4278190082, "form_key": "Skyrim.esm|0x000002"}, "error": "bad_request"},
    {"wire": {"array": "Int", "items": [1, "x"]}, "error": "type_mismatch"},
    {"wire": {"array": "Bogus", "items": []}, "error": "bad_request"},
    {"wire": {"array": "Int", "items": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128]}, "error": "type_mismatch"},
    {"wire": [1, 2], "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 0, "alias_type": "MagicAlias"}, "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 0, "alias_type": 5}, "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": -1, "alias_type": "ReferenceAlias"}, "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 1099511627777, "alias_type": "ReferenceAlias"}, "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 2147483648, "alias_type": "ReferenceAlias"}, "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": "0", "alias_type": "ReferenceAlias"}, "error": "bad_request"}
  ]
}
```

`protocol/v1/fixtures/form_keys.json`:
```json
{
  "valid": [
    {"text": "Skyrim.esm|0x01A69F", "plugin": "Skyrim.esm", "local_id": 108191, "canonical": "Skyrim.esm|0x01A69F"},
    {"text": "Skyrim.esm|0x14", "plugin": "Skyrim.esm", "local_id": 20, "canonical": "Skyrim.esm|0x000014"},
    {"text": "Skyrim.esm|0xFFFFFF", "plugin": "Skyrim.esm", "local_id": 16777215, "canonical": "Skyrim.esm|0xFFFFFF"},
    {"text": "SkyrimPythonTest.esp|0x000800", "plugin": "SkyrimPythonTest.esp", "local_id": 2048, "canonical": "SkyrimPythonTest.esp|0x000800"},
    {"text": "Light Mod.esl|0xabc", "plugin": "Light Mod.esl", "local_id": 2748, "canonical": "Light Mod.esl|0x000ABC"},
    {"text": "Light Mod.esl|0xFFF", "plugin": "Light Mod.esl", "local_id": 4095, "canonical": "Light Mod.esl|0x000FFF"}
  ],
  "invalid": ["Skyrim.esm", "Skyrim.esm|12", "notes.txt|0x1", "Skyrim.esm|0x1000000", "|0x1", "Skyrim.esm|0x", "Skyrim.esm|0xZZ",
    ".esm|0x1", " Skyrim.esm|0x1", "Skyrim .esm|0x1", "\tSkyrim.esm|0x1", "Sky\nrim.esm|0x1", "Skyrim.esm|0x1\n", "Skyrim.esm\n|0x1", "Skyrim.esm|0x123456789",
    "Light Mod.esl|0x1000", "Light Mod.esl|0xFFFFFF"]
}
```

`protocol/v1/fixtures/responses.json`:
```json
{
  "valid": [
    {"type": "response", "id": 1, "ok": true, "result": 6},
    {"type": "response", "id": 2, "ok": false, "error": {"code": "not_ready", "message": "a game is loading", "detail": {"reason": "loading", "paused": false, "menus": []}}},
    {"type": "response", "id": 3, "ok": true, "result": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}},
    {"type": "welcome", "protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]},
    {"type": "handshake_error", "code": "unauthorized", "message": "bad token"},
    {"type": "response", "id": 22, "ok": false, "error": {"code": "timeout", "message": "papyrus.call_static timed out after 5000 ms", "detail": {"paused": false, "menus": []}}}
  ]
}
```

- [ ] **Step 3: Check that every fixture parses**

Run: `python3 -c "import glob, json; [json.load(open(f)) for f in sorted(glob.glob('protocol/v1/fixtures/*.json'))]; print('fixtures ok')"`
Expected: `fixtures ok`. (The fixtures are data for Tasks 14-16, 20, 22 and 23; Task 16's C++ sweep and Task 20's Python agreement tests check their meaning. If a rule in `PROTOCOL.md` changes, change the fixture and the rule in the same commit.)

Run: `python3 -c "import json; v=json.load(open('protocol/v1/fixtures/values.json')); print(sorted(len(e['wire']['items']) for e in v['valid'] + v['invalid'] if isinstance(e['wire'], dict) and 'array' in e['wire'] and len(e['wire']['items']) > 100))"`
Expected: `[128, 129]`.

- [ ] **Step 4: Commit**

```bash
git add protocol && git commit -m "docs(protocol): protocol v1 description and golden fixtures for in-process and bridge use"
```
