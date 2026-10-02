# Skython protocol v1

Every number and name below is in [PROTOCOL.generated.md](PROTOCOL.generated.md), which is generated from `protocol.json` (error codes, ops and their arguments, limits, defaults). This file explains the rules and says "the `max_frame_bytes` limit" where it means a number. If this text and a generated table seem to disagree, the table is right: report the sentence here.

## Two modes, one message format
The same request and response objects are used in both modes:
- **In-process** (a Python script running inside the game): the `skython` package serialises a request object to JSON text and hands it to the built-in `_skython` module (`_skython.call` or `_skython.submit`); it gets the response object back as JSON text. There is no framing, handshake or token.
- **Dev bridge** (an external process, development only): a Python server inside the game (`skython.bridge`) accepts TCP connections, checks the handshake below, and forwards each request text unchanged to `_skython`; the response text comes back unchanged in a frame. The client is `skython.remote`.

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
`{"type":"request","id":<uint64>,"op":"<op>","args":{...}}` (`args` may be left out; it then counts as `{}`, as for `game.state`)
`{"type":"response","id":<same>,"ok":true,"result":<value or object>}`
`{"type":"response","id":<same>,"ok":false,"error":{"code":"<code>","message":"...","detail":{...}}}`
Responses may arrive in any order. On a bridge connection, a request whose id is still in flight on that connection is refused with `bad_request`; in-process, the `skython` package numbers its own requests and the plugin does not check ids. A request whose text cannot be parsed gets an error response with the id read from the text when one can be found, else id 0. Error messages are truncated to `max_error_message_bytes` and never echo a whole request, so a response cannot exceed the frame limit. Every field of the wrong JSON type (`{"type":5}`, `"op":5`, `"kind":5`, `"client":5`) is `bad_request`; the plugin never answers with anything but a typed error. The error codes and what each means are in the generated tables.

A `not_ready` error carries `detail.reason`, one of the reasons listed there, plus `detail.paused` and `detail.menus`. Ops marked as needing a game get `not_ready` at once, without waiting, while no game is loaded or a save is loading; `game.state` is always answered. Both world resets end the calls still in flight that need a game: a save starting to load, and a quit to the main menu. Each such call ends with `not_ready`, and its `detail.reason` is the game's state at that moment (`loading` while the save loads, `no_game` at the main menu). If the call had already been handed to the game, `detail.dispatched` is `true`: its effect may already have happened, so a client must not resubmit it blindly (the `skython` package's `retry_not_ready` retries only calls without `dispatched`). A call refused at once, before it reached the game, has no `dispatched` field.

A `timeout` error's detail also carries `detail.paused` and `detail.menus`, and may carry `detail.late_cancel: true`, meaning the caller stopped waiting before the plugin's own deadline; the call may still run in the game either way. A `papyrus.batch` that times out also carries `detail.completed` (how many steps finished) and `detail.results` (their results, in the shape of a batch result's `results`); the steps that finished already had their effects.

## Events (M2)
`{"type":"event","event":"<name>","sub":<id>,"seq":<n>,"epoch":<n>,"data":{...}}`

## Values
null, true/false, integers (Papyrus Int: `int_min`..`int_max`; out of range is `type_mismatch`), floats, strings,
Float is a single-precision float: a finite number with magnitude above `float_max_text` is `type_mismatch`. A Papyrus Float parameter also accepts an integer (it is converted to Float). The plugin prints floats in their shortest round-trip form (0.1 stays `0.1`), always with a decimal point or exponent (`1.0`), and sends a non-finite float as `null`. A Float array accepts integer items.
form reference `{"kind":"form","form_id":20,"form_key":"Skyrim.esm|0x000014","type":"Actor","epoch":1}` (at least one of form_id/form_key; `type`, `epoch` and `fingerprint` are optional but, when present, must have the right type, else `bad_request`; `null` counts as absent). When both form_id and form_key are given they must agree on the local id (the local-id width of a light form is smaller; see Values in the generated tables), and a runtime-created id must not carry a form_key. Runtime-created references carry `"fingerprint":{"base":"<form key or decimal form id>","type":"<script type>"}` and no form_key.
Form key grammar: `<plugin>|0x<hex digits>`; the plugin name ends in one of the plugin extensions, case-insensitively, has a non-empty stem with no leading or trailing whitespace, and contains no control characters; the local id and digit counts are limited as the generated Values section says (light plugins have the smaller local-id range). The canonical text uses uppercase hex digits.
alias reference `{"kind":"alias","quest":<form reference>,"alias_id":0,"alias_type":"ReferenceAlias"|"LocationAlias","epoch":1}` (`alias_id` is an integer 0..`int_max`, never truncated or negative),
array `{"array":"Bool"|"Int"|"Float"|"String"|"Form"|"Alias","items":[...]}` (Float arrays accept integer items; at most `max_array_items` items, more is `type_mismatch`). Form and Alias arrays come back as results, but v1 cannot send an array of objects as an argument: a parameter declared as an object array (`Form[]`, `Actor[]`, ...) is `unsupported`; pass the objects one at a time.
Bare JSON arrays are not values.

## Ops (M1)
The ops, their arguments (names, types, which are required, defaults), whether they need a loaded game, and their result shapes are tables in the generated file. Rules that a table cannot show:
- `form.resolve` takes exactly one of its three arguments; two or none is `bad_request`.
- A `timeout_ms` outside the `min_timeout_ms`..`max_timeout_ms` range, or not an integer, is `bad_request`.
- `papyrus.batch` runs its calls in the listed order and returns one result per call (`{"ok":true,"result":v}` or `{"ok":false,"error":{...}}`); a step may only be an op marked batchable, and a batch has 1..`max_batch_calls` calls. It is not a transaction.
- `script`, `function`, `property` and `editor_id` are non-empty strings of at most `max_name_bytes` bytes.
- Unknown argument names are ignored by the plugin.
