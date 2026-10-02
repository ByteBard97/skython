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
Client first: `{"type":"hello","protocol":[1,1],"token":"<token>","client":"<name>"}`
Server: `{"type":"welcome","protocol":[1,1],"plugin_version":"0.1.0","game_version":"1.6.1170.0","runtime":"AE","epoch":3,"capabilities":["papyrus.call_static", ...]}`
A client of an older minor (for example `[1,0]`) still connects; it simply does not use what the newer minor added (protocol 1.1 added the events ops, the events table and `game.save`/`game.load`).
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

## Events
The game pushes events; each one is delivered to every subscription that wants it. In-process, each `skython.Game` owns one event consumer; on the dev bridge each connection owns one. **The consumer is never named in a request:** it travels beside the request text (`_skython.submit(text, consumer)`), and the bridge forwards request text unchanged, so a client cannot subscribe or unsubscribe for another consumer. An `events.*` request submitted without a consumer is `bad_request` ("no event consumer").

`events.subscribe` answers `{"sub", "event", "max_queue"}`. `events.unsubscribe` of a sub that does not exist, or that another consumer owns, is `not_found` (the two cases look the same). One consumer holds at most `max_subscriptions_per_consumer` subscriptions, and all consumers together at most `max_subscriptions`; beyond either, `events.subscribe` is `busy` (so one bridge client cannot take every subscription from the in-process scripts). Both ops are answered with no game loaded.

An event frame (the dev bridge writes it as one frame; in-process, `_skython.next_events` returns a JSON list of these objects, at most `max_events_per_next` per call, in push order across the consumer's subscriptions):
`{"type":"event","event":"death","sub":7,"seq":12,"epoch":3,"data":{"victim":{"kind":"form",...},"killer":null},"dropped":4,"suppressed":2}`
- `seq` counts per subscription, from 1, every event accepted for that subscription (later delivered or dropped), so gaps in `seq` equal drops.
- `epoch` is the game's load counter when the event was pushed (the `epoch` of `game.state`).
- `dropped` (present only when non-zero): this subscription's events discarded by overflow since its previous delivered event.
- `suppressed` (present only when non-zero): this subscription's events not queued because a load was in progress, since its previous delivered event.
- `data` has exactly the fields the generated events table lists for that event. Form fields are form references (see Values), or `null` where the table marks them; events never carry engine pointers.

**Queues.** Each subscription has its own queue of `max_queue` events (default `default_event_queue`, at most `max_event_queue`). A full queue drops its oldest event and counts it in `dropped`. A consumer's queued events together are also bounded in bytes by `max_queued_event_bytes_per_consumer` (each event counts its data's compact JSON length plus 96): when a push takes the consumer over it, the oldest events of the subscription being pushed to are dropped, counted in that subscription's `dropped`, until the consumer is under it again (if that subscription has nothing older, the new event itself is dropped; its `seq` was still taken). So a consumer's memory is bounded however large the events are. The game never waits for a reader, and a slow subscription never delays another one.

**Filters.** `filter` maps field names from the event's filter fields to non-empty arrays of at most `max_filter_values` values. An event matches when every listed field's value is in its array (AND across fields, OR within one); absent or `null` means every event. Form fields: each value is a form reference carrying `form_id` (a form key alone is `bad_request`: resolve the form first), matched by form id; a `null` event field never matches. Int fields: Papyrus Int values. String fields: strings, matched ASCII-case-insensitively (the engine's string pool can change the case). An unknown field, an empty array or a value of the wrong type is `bad_request`. Where the table names fields under "Filter required", the filter must name at least one of them, else `bad_request` (for example "hit needs a filter on target or aggressor").

**Loading.** While a save load or a new game is in progress (from the moment the game announces the load or the new game until `game_loaded`; the loading screens of doors, fast travel and `coc` do not count), an event whose table entry is not delivered while loading is not queued; each subscription that would have taken it counts it in `suppressed`. Menu events, `mod_event`, `channel` and the lifecycle events (`game_loaded`, `game_saved`, `new_game`) are delivered. `game_loaded` is raised when a loaded save or a new game becomes interactive: it marks the end of a load. Subscriptions survive a load. An event the game raised just before a load started can still be queued after that load's `game_loaded`, stamped with the old `epoch`: a consumer that must tell old from new compares the event's `epoch` with `game_loaded`'s, never its position in the stream.

**Channel events.** The Papyrus native `Skython.Send(channel, payload)` raises `channel`. A payload longer than `max_channel_payload_bytes` is cut at a UTF-8 boundary and the event carries `truncated: true`; a call whose channel is empty or longer than `max_channel_bytes` is **dropped** (no event) and the plugin logs one warning the first time; a channel name is never cut, because a cut name would be a different channel.

## Values
null, true/false, integers (Papyrus Int: `int_min`..`int_max`; out of range is `type_mismatch`), floats, strings,
Float is a single-precision float: a finite number with magnitude above `float_max_text` is `type_mismatch`. A Papyrus Float parameter also accepts an integer (it is converted to Float). The plugin prints floats in their shortest round-trip form (0.1 stays `0.1`), always with a decimal point or exponent (`1.0`), and sends a non-finite float as `null`. A Float array accepts integer items.
form reference `{"kind":"form","form_id":20,"form_key":"Skyrim.esm|0x000014","type":"Actor","epoch":1}` (at least one of form_id/form_key; `type`, `epoch` and `fingerprint` are optional but, when present, must have the right type, else `bad_request`; `null` counts as absent). When both form_id and form_key are given they must agree on the local id (the local-id width of a light form is smaller; see Values in the generated tables), and a runtime-created id must not carry a form_key. Runtime-created references carry `"fingerprint":{"base":"<form key or decimal form id>","type":"<script type>"}` and no form_key.
Form key grammar: `<plugin>|0x<hex digits>`; the plugin name ends in one of the plugin extensions, case-insensitively, has a non-empty stem with no leading or trailing whitespace, and contains no control characters; the local id and digit counts are limited as the generated Values section says (light plugins have the smaller local-id range). The canonical text uses uppercase hex digits.
alias reference `{"kind":"alias","quest":<form reference>,"alias_id":0,"alias_type":"ReferenceAlias"|"LocationAlias","epoch":1}` (`alias_id` is an integer 0..`int_max`, never truncated or negative),
array `{"array":"Bool"|"Int"|"Float"|"String"|"Form"|"Alias","items":[...]}` (Float arrays accept integer items; at most `max_array_items` items, more is `type_mismatch`). Form and Alias arrays come back as results, but v1 cannot send an array of objects as an argument: a parameter declared as an object array (`Form[]`, `Actor[]`, ...) is `unsupported`; pass the objects one at a time.
Bare JSON arrays are not values.

## Ops
The ops, their arguments (names, types, which are required, defaults), whether they need a loaded game, and their result shapes are tables in the generated file. Rules that a table cannot show:
- `form.resolve` takes exactly one of its three arguments; two or none is `bad_request`.
- A `timeout_ms` outside the `min_timeout_ms`..`max_timeout_ms` range, or not an integer, is `bad_request`.
- `papyrus.batch` runs its calls in the listed order and returns one result per call (`{"ok":true,"result":v}` or `{"ok":false,"error":{...}}`); a step may only be an op marked batchable, and a batch has 1..`max_batch_calls` calls. It is not a transaction.
- `script`, `function`, `property` and `editor_id` are non-empty strings of at most `max_name_bytes` bytes.
- Unknown argument names are ignored by the plugin.
- `game.save` never overwrites: a save that already has that name is `bad_request` with the message `a save named <name> already exists` and `detail.reason` `"save_exists"`. It answers once the engine has started the save (the game's save message for that name), which can be a moment before the file is on disk; no such message within the timeout is `timeout`. A second `game.save` of a name whose save was started but whose file is not written yet is the same `bad_request` (`a save named <name> is already in progress`, reason `"save_exists"`).
- `game.load` is answered without a loaded game, so it works at the main menu; while the game is starting or already loading it is `not_ready`. A load of a save whose `game.save` has been answered but whose file is not written yet is `not_ready` with `detail.reason` `"saving"`; retry it shortly. A missing save is `not_found` with the message `no save named <name>`; the engine refusing to load it is `internal`. It answers once the load has started. The load is a world reset, so every call in flight that needs a game ends `not_ready` (see above).
- Save names match the save-name pattern in the generated tables (letters, digits and underscores; no path, no extension).
