<!-- GENERATED FILE - DO NOT EDIT. Source: protocol/protocol.json. Regenerate with: python3 tools/gen_protocol.py -->

# Protocol v1 tables (generated)

> GENERATED FILE - DO NOT EDIT. These tables are written by `python3 tools/gen_protocol.py` from `protocol/protocol.json`; edit that file, regenerate, and commit all of the outputs. `PROTOCOL.md` explains the rules; this file holds every number and name.

Protocol version: **1.0** (a major mismatch is refused; a newer minor is accepted).

## Message types

`hello`, `welcome`, `handshake_error`, `request`, `response`, `event`

## Error codes

| Code | Sent in | Python exception | Meaning |
|---|---|---|---|
| `not_ready` | response | `NotReady` | The game cannot run the request yet; `detail.reason` says why (see not_ready_reasons). |
| `busy` | response | `Busy` | An in-flight cap was reached (the plugin-wide one, or one bridge connection's); retry later. |
| `bad_request` | response and handshake_error | `BadRequest` | The message is malformed: wrong field types, missing or unknown op, bad arguments, nesting deeper than max_json_depth, a duplicate in-flight id on one bridge connection. |
| `unknown_function` | response | `UnknownFunction` | The script or function does not exist in the running game. |
| `type_mismatch` | response | `TypeMismatch` | A value has the wrong Papyrus type or is out of range (Int outside 32 bits, Float outside 32-bit range). |
| `stale_reference` | response | `StaleReference` | A runtime-created reference no longer names the object it was handed out for (fingerprint mismatch). |
| `not_found` | response | `NotFound` | A form, alias, quest or property does not exist. |
| `timeout` | response | `Timeout` | The call did not finish within its timeout_ms (or the caller stopped waiting first: detail.late_cancel is true). It may still run in the game. |
| `vm_error` | response | `VmError` | The Papyrus VM refused or failed the call. |
| `unsupported` | response | `Unsupported` | The request is valid protocol but this plugin build cannot do it. |
| `internal` | response | `InternalError` | A bug or an unexpected failure inside the plugin; never caused by the caller alone. |
| `unauthorized` | handshake_error | `Unauthorized` | Wrong token. Usually a stale session file from an earlier game run. |
| `protocol_mismatch` | handshake_error | `ProtocolMismatch` | Client and plugin speak different protocol major versions. |

An unknown code from a newer server maps to the generic `RemoteError` (or `HandshakeError`).

`not_ready` detail: `{"reason": "starting"|"no_game"|"loading"|"paused"}`.

## Limits

| Name | Value | Unit | Meaning |
|---|---|---|---|
| `max_frame_bytes` | 16 777 216 (16 MiB) | bytes | Dev bridge: largest frame payload either side accepts. A larger header closes the connection. |
| `pre_auth_frame_bytes` | 65 536 (64 KiB) | bytes | Dev bridge: frame cap until the hello is accepted; a valid hello is well under 1 KiB. |
| `max_name_bytes` | 256 | bytes | Longest script, function, property or editor_id string. |
| `max_batch_calls` | 256 | calls | Most calls in one papyrus.batch (the least is 1). |
| `min_timeout_ms` | 1 | ms | Smallest timeout_ms. |
| `max_timeout_ms` | 3 600 000 | ms | Largest timeout_ms; also the largest default_timeout_ms. |
| `max_error_message_bytes` | 1 024 (1 KiB) | bytes | Error messages are clipped to this many bytes, so a response can never exceed the frame limit. |
| `max_client_name_bytes` | 64 | bytes | The hello's client name is truncated to this many bytes. |
| `max_connections` | 32 | connections | Dev bridge: simultaneous client connections; further ones are closed at accept. |
| `hello_timeout_ms` | 5 000 | ms | Dev bridge: a connection that has not sent an accepted hello by then is closed. |
| `max_json_depth` | 64 | levels | Deepest array/object nesting accepted in a request. In-process the request fails with bad_request; on the dev bridge the connection is closed. |
| `max_queued_bytes_per_connection` | 67 108 864 (64 MiB) | bytes | Dev bridge: outbound bytes queued for one connection before it is treated as a stuck reader and closed. |
| `token_length` | 32 | hex characters | Dev bridge: tokens are 128 random bits as lowercase hex. |
| `int_min` | -2 147 483 648 | - | Smallest Papyrus Int. Out of range is type_mismatch, never truncation. |
| `int_max` | 2 147 483 647 | - | Largest Papyrus Int; also the largest alias_id and the largest hello protocol number. |
| `float_max_text` | 3.4028235e+38 | - | Largest Papyrus Float magnitude, as the shortest text that round-trips FLT_MAX; a larger magnitude is type_mismatch. |
| `max_array_items` | 128 | items | Most items in a Papyrus array (the VM's own limit); a longer array argument is type_mismatch. |

## Values

- Alias types: `ReferenceAlias`, `LocationAlias` (default `ReferenceAlias`).
- Array element types: `Bool`, `Int`, `Float`, `String`, `Form`, `Alias`.
- Plugin extensions in a form key: `.esm`, `.esp`, `.esl` (case-insensitive); `.esl` plugins have local ids up to `0xFFF`, the others up to `0xFFFFFF`.
- A form id whose top byte is `0xFF` is runtime-created (no form key); `0xFE` is a light form.
- A form key has at most 8 hex digits after `0x`; the canonical text has 6 uppercase digits.

## Ops (M1)

### `game.state`

The game's readiness and player. Answered at any time, also with no game loaded.

No arguments.

Result: `{"data_ready","game_loaded","loading","interactive","paused":bool,"menus":[...],"epoch":n,"player":<form reference or null>,"game_days_passed":float or null}`; player and game_days_passed are null unless a game is loaded.

### `form.resolve`

Name a form three ways; get back a form reference with its type and form key. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `form_key` | `form_key` | one of | - | `Plugin.esp\|0x0012AB` |
| `form_id` | `u32` | one of | - | load-order form id |
| `editor_id` | `name` | one of | - | only where the game keeps editor ids |

Give exactly one of `form_key`, `form_id`, `editor_id`.

Result: a form reference

### `papyrus.call_static`

Call a global Papyrus function. May be used as a step of `papyrus.batch`. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `script` | `name` | yes | - |  |
| `function` | `name` | yes | - |  |
| `args` | `value_list` | no | - | arguments, in order; absent means none |
| `timeout_ms` | `timeout_ms` | no | - | absent means the plugin's default_timeout_ms |
| `require_unpaused` | `bool` | no | false | refuse with not_ready while a menu pauses the game |

Result: the function's value

### `papyrus.call_method`

Call a member function on a form or alias. May be used as a step of `papyrus.batch`. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `object` | `ref` | yes | - | a form or alias reference |
| `script` | `name` | yes | - | the script type to bind, for example `Actor` |
| `function` | `name` | yes | - |  |
| `args` | `value_list` | no | - | arguments, in order; absent means none |
| `timeout_ms` | `timeout_ms` | no | - | absent means the plugin's default_timeout_ms |
| `require_unpaused` | `bool` | no | false | refuse with not_ready while a menu pauses the game |

Result: the function's value

### `papyrus.get_property`

Read a script property. May be used as a step of `papyrus.batch`. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `object` | `ref` | yes | - | a form or alias reference |
| `script` | `name` | yes | - |  |
| `property` | `name` | yes | - |  |

Result: the property's value

### `papyrus.set_property`

Write a script property. May be used as a step of `papyrus.batch`. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `object` | `ref` | yes | - | a form or alias reference |
| `script` | `name` | yes | - |  |
| `property` | `name` | yes | - |  |
| `value` | `value` | yes | - |  |

Result: null

### `papyrus.batch`

Run several papyrus.* steps in order in one round trip. Not a transaction. Refused with `not_ready` until a game is loaded and interactive.

| Argument | Type | Required | Default | Notes |
|---|---|---|---|---|
| `calls` | `call_list` | yes | - | `{"op","args"}` steps, only ops marked batchable |
| `stop_on_error` | `bool` | no | true | stop at the first failed step |
| `timeout_ms` | `timeout_ms` | no | - | one timeout for the whole batch |

Result: `{"results":[{"ok":true,"result":v}|{"ok":false,"error":{...}}]}`

### Argument types

| Type | Meaning |
|---|---|
| `name` | non-empty string of at most max_name_bytes bytes |
| `value` | any value (see Values) |
| `value_list` | JSON array of values |
| `ref` | form reference or alias reference |
| `form_key` | form key `Plugin.esp\|0x0012AB` |
| `u32` | unsigned 32-bit integer |
| `timeout_ms` | integer from min_timeout_ms to max_timeout_ms |
| `bool` | true or false |
| `call_list` | 1..max_batch_calls objects `{"op", "args"}` |

## Plugin ini keys (`Skython.ini`)

| Key | Type | Default | Range | Meaning |
|---|---|---|---|---|
| `python_enabled` | bool | true | - | Start the embedded Python interpreter at kDataLoaded. Off means the plugin loads but runs no Python. |
| `scripts_enabled` | bool | true | - | Load player scripts from the scripts folder at startup. |
| `bridge_enabled` | bool | false | - | Run the dev bridge (loopback TCP for an external process). Off in the shipped ini, on in the dev ini. |
| `port` | u16 | 45977 | 0..65535 | Dev bridge TCP port on 127.0.0.1; 0 picks a free port. |
| `fixed_token` | token | (empty) | - | Development only: a fixed 32-hex bridge token. Empty means a random token per start. |
| `session_file` | path | (empty) | - | Where the bridge writes its session file. Empty means <SKSE log dir>/Skython/session.json. |
| `default_timeout_ms` | u32 | 5000 | 1..3600000 | Timeout applied to a call that sends none. |
| `max_inflight_per_connection` | u32 | 256 | 1..4294967295 | Requests one dev-bridge connection may have in flight. |
| `max_inflight_total` | u32 | 1024 | 1..4294967295 | Requests in flight in the whole plugin (in-process and bridge together). |
| `enable_console` | bool | false | - | Allow native.console (M2). |

## Discovery

Dev bridge only; an in-process script needs no discovery.

- Host: `127.0.0.1`. Session file: `Skython/session.json` under the game's SKSE log directory unless `session_file` says otherwise.
- Session file fields: `port`, `token`, `pid`, `plugin_version`, `protocol`.
- Environment variables: `SKYTHON_SESSION` (path of a session file), or `SKYTHON_PORT` together with `SKYTHON_TOKEN`.

## Client defaults

Used by `skython.Game` in both modes; `connect_timeout_s` and `send_timeout_s` apply to the dev bridge only.

| Setting | Value (seconds) |
|---|---|
| `connect_timeout_s` | 5.0 |
| `call_timeout_s` | 30.0 |
| `send_timeout_s` | 30.0 |
| `retry_not_ready_s` | 30.0 |
| `ready_timeout_s` | 120.0 |
| `grace_s` | 2.0 |
