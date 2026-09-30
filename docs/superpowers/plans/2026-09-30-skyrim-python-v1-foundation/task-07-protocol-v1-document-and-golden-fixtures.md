# Task 7: Protocol v1 document and golden fixtures

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `protocol/PROTOCOL.md`
- Create: `protocol/v1/fixtures/hello.json`, `requests.json`, `values.json`, `form_keys.json`, `responses.json`

**Interfaces:**
- Produces: fixture files with the exact JSON shapes below. C++ tests (Task 12) and Python tests (Task 13) load them from `protocol/v1/fixtures/`.

- [ ] **Step 1: Write `protocol/PROTOCOL.md`**

````markdown
# skyrim-python protocol v1

Transport: TCP on 127.0.0.1. Each message is a frame: 4-byte little-endian unsigned length, then that many bytes of UTF-8 JSON (one object). Max 16 MiB. A frame header over the limit, or a payload that is not a JSON object, closes the connection.

Every message has `"type"`: `hello`, `welcome`, `handshake_error`, `request`, `response`, `event`.

## Handshake
Client first: `{"type":"hello","protocol":[1,0],"token":"<32 lowercase hex>","client":"<name>"}`
Server: `{"type":"welcome","protocol":[1,0],"plugin_version":"0.1.0","game_version":"1.6.1170.0","runtime":"AE","epoch":3,"capabilities":["papyrus.call_static", ...]}`
or `{"type":"handshake_error","code":"unauthorized"|"protocol_mismatch"|"bad_request","message":"..."}` and the server closes.

## Requests and responses
`{"type":"request","id":<uint64>,"op":"<op>","args":{...}}`
`{"type":"response","id":<same>,"ok":true,"result":<value or object>}`
`{"type":"response","id":<same>,"ok":false,"error":{"code":"<code>","message":"...","detail":{...}}}`
Responses may arrive in any order. Error codes: not_ready, busy, bad_request, unknown_function, type_mismatch, stale_reference, not_found, timeout, vm_error, unsupported, internal. `not_ready` detail: `{"reason":"starting"|"no_game"|"loading"|"paused"}`.

## Events (M2)
`{"type":"event","event":"<name>","sub":<id>,"seq":<n>,"epoch":<n>,"data":{...}}`

## Values
null, true/false, integers (Papyrus Int: -2147483648..2147483647), floats, strings,
form reference `{"kind":"form","form_id":20,"form_key":"Skyrim.esm|0x000014","type":"Actor","epoch":1}` (at least one of form_id/form_key; runtime-created references carry `"fingerprint":{"base":"<form key or decimal form id>","type":"<script type>"}` and no form_key),
alias reference `{"kind":"alias","quest":<form reference>,"alias_id":0,"alias_type":"ReferenceAlias"|"LocationAlias","epoch":1}`,
array `{"array":"Bool"|"Int"|"Float"|"String"|"Form"|"Alias","items":[...]}` (Float arrays accept integer items).
Bare JSON arrays are not values.

## Ops (M1)
- `game.state` `{}` → `{"data_ready":bool,"game_loaded":bool,"loading":bool,"interactive":bool,"paused":bool,"menus":[...],"epoch":n,"player":<form ref or null>,"game_days_passed":float or null}` (player and game_days_passed are null unless a game is loaded)
- `form.resolve` `{"form_key":...}` | `{"form_id":n}` | `{"editor_id":"..."}` → form reference
- `papyrus.call_static` `{"script","function","args":[values],"timeout_ms"?,"require_unpaused"?}` → value
- `papyrus.call_method` `{"object":<form or alias ref>,"script","function","args":[...],"timeout_ms"?,"require_unpaused"?}` → value
- `papyrus.get_property` `{"object","script","property"}` → value
- `papyrus.set_property` `{"object","script","property","value"}` → null
- `papyrus.batch` `{"calls":[{"op":"papyrus.call_static"|"papyrus.call_method"|"papyrus.get_property"|"papyrus.set_property","args":{...}}],"stop_on_error":bool,"timeout_ms"?}` → `{"results":[{"ok":true,"result":v}|{"ok":false,"error":{...}}]}`
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
    {"message": {"type": "request", "id": 1, "op": "game.state", "args": {}}, "error": "bad_request"}
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
    {"type": "request", "id": 5, "op": "papyrus.batch", "args": {"calls": [{"op": "papyrus.call_static", "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}}], "stop_on_error": true}}
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
    {"message": {"type": "request", "id": 13, "op": "papyrus.batch", "args": {"calls": [{"op": "game.state", "args": {}}], "stop_on_error": true}}, "error": "bad_request"}
  ]
}
```

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
    {"wire": "Iron Sword", "kind": "string"},
    {"wire": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}, "kind": "form"},
    {"wire": {"kind": "form", "form_id": 4278190082, "type": "ObjectReference", "epoch": 2, "fingerprint": {"base": "Skyrim.esm|0x012EB7", "type": "ObjectReference"}}, "kind": "form"},
    {"wire": {"kind": "form", "form_key": "Skyrim.esm|0x01A69F"}, "kind": "form"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 123456, "form_key": "SkyrimPythonTest.esp|0x000800", "type": "Quest", "epoch": 1}, "alias_id": 0, "alias_type": "ReferenceAlias", "epoch": 1}, "kind": "alias"},
    {"wire": {"array": "Int", "items": [1, 2, 3]}, "kind": "array"},
    {"wire": {"array": "Float", "items": [1, 2.5]}, "kind": "array"},
    {"wire": {"array": "Form", "items": [{"kind": "form", "form_id": 20}]}, "kind": "array"}
  ],
  "invalid": [
    {"wire": 2147483648, "error": "type_mismatch"},
    {"wire": -2147483649, "error": "type_mismatch"},
    {"wire": {"kind": "form"}, "error": "bad_request"},
    {"wire": {"kind": "form", "form_key": "nonsense"}, "error": "bad_request"},
    {"wire": {"kind": "widget"}, "error": "bad_request"},
    {"wire": {"array": "Int", "items": [1, "x"]}, "error": "type_mismatch"},
    {"wire": {"array": "Bogus", "items": []}, "error": "bad_request"},
    {"wire": [1, 2], "error": "bad_request"},
    {"wire": {"kind": "alias", "quest": {"kind": "form", "form_id": 1}, "alias_id": 0, "alias_type": "MagicAlias"}, "error": "bad_request"}
  ]
}
```

`protocol/v1/fixtures/form_keys.json`:
```json
{
  "valid": [
    {"text": "Skyrim.esm|0x01A69F", "plugin": "Skyrim.esm", "local_id": 108191, "canonical": "Skyrim.esm|0x01A69F"},
    {"text": "Skyrim.esm|0x14", "plugin": "Skyrim.esm", "local_id": 20, "canonical": "Skyrim.esm|0x000014"},
    {"text": "SkyrimPythonTest.esp|0x000800", "plugin": "SkyrimPythonTest.esp", "local_id": 2048, "canonical": "SkyrimPythonTest.esp|0x000800"},
    {"text": "Light Mod.esl|0xabc", "plugin": "Light Mod.esl", "local_id": 2748, "canonical": "Light Mod.esl|0x000ABC"}
  ],
  "invalid": ["Skyrim.esm", "Skyrim.esm|12", "notes.txt|0x1", "Skyrim.esm|0x1000000", "|0x1", "Skyrim.esm|0x", "Skyrim.esm|0xZZ"]
}
```

`protocol/v1/fixtures/responses.json`:
```json
{
  "valid": [
    {"type": "response", "id": 1, "ok": true, "result": 6},
    {"type": "response", "id": 2, "ok": false, "error": {"code": "not_ready", "message": "a game is loading", "detail": {"reason": "loading"}}},
    {"type": "response", "id": 3, "ok": true, "result": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 1}},
    {"type": "welcome", "protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]},
    {"type": "handshake_error", "code": "unauthorized", "message": "bad token"}
  ]
}
```

- [ ] **Step 3: Commit**

```bash
git add protocol && git commit -m "docs(protocol): protocol v1 description and golden fixtures"
```
