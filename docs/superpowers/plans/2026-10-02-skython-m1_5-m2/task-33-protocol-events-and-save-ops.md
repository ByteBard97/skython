# Task 33: Protocol: event ops, events table, filters, save/load ops, fixtures, generator

> Part of the [Skython M1.5/M2 plan](README.md). Before starting, read the plan index's **Global Constraints**, **Contracts** ("Protocol additions", "The v1 events table") and the Part D brief for Task 33. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** nothing in this plan (the v1 tree at commit 47636bf). Linux only; no game.

**What this task does.** It is the only task in this plan that edits `protocol/protocol.json` (Global Constraints: "One owner for `protocol/protocol.json`"). It raises the protocol to `[1, 1]`, adds the four ops `events.subscribe`, `events.unsubscribe`, `game.save`, `game.load`, the 20-entry `events` table, seven limits, the save-name pattern and the config keys `trace_timing` and `console_echo_errors`. `tools/gen_protocol.py` learns to validate and emit the events table (C++ `spy::kEvents` / `spy::find_event`, Python `EVENTS`, a Markdown table) and the five new argument types. `parse_request` learns the four ops, checked argument by argument from their table entries. A filter only has to be a JSON object (or `null`, meaning absent) at this level: its deep rules (field names, value types, `filter_required_any_of`) belong to `parse_event_filter` in Task 34, which consumes the `invalid_filters` entries of the new `events.json` fixture. `python/tests/fake_skython.py` gets the same parse-level checks so the Python fake keeps refusing what core refuses (Task 36 later adds its consumers).

Because the minor bump and the new ops change pinned literals, several existing tests outside the index's File Structure change too: `core/tests/test_generated.cpp`, `core/tests/test_requests.cpp`, `core/tests/test_fake_backend.cpp` (the "while a save loads" replay must skip every op that does not need a game, not only `game.state`), `python/tests/test_generated.py`, `python/tests/test_game.py` (the four new ops have no `Game` method until Task 36), `python/tests/test_inprocess.py`, `pyglue/tests/test_module.py` and `livetest/test_m1_bridge.py` (session file `protocol`).

**Files:**
- Modify: `protocol/protocol.json` (minor 1, limits, `save_name_pattern`, config keys, ops, `events`)
- Modify (regenerated, never by hand): `core/include/spy/protocol_generated.h`, `python/src/skython/_protocol_generated.py`, `protocol/PROTOCOL.generated.md`
- Modify: `tools/gen_protocol.py`, `tools/test_gen_protocol.py`
- Modify: `protocol/PROTOCOL.md` (handshake example, "Events" section rewritten, "Ops" rules for save/load)
- Create: `protocol/v1/fixtures/events.json`
- Modify: `protocol/v1/fixtures/requests.json`, `responses.json`, `hello.json`
- Modify: `core/include/spy/requests.h`, `core/src/requests.cpp`, `core/include/spy/config.h`, `core/src/config.cpp`
- Modify: `plugin/config/Skython.ini`, `plugin/config/Skython.dev.ini`
- Test: `core/tests/test_generated.cpp`, `core/tests/test_requests.cpp`, `core/tests/test_config.cpp`, `core/tests/test_fixtures.cpp`, `core/tests/test_fake_backend.cpp`
- Test: `python/tests/test_generated.py`, `python/tests/test_fixtures.py`, `python/tests/test_game.py`, `python/tests/test_inprocess.py`, `python/tests/fake_skython.py`, `pyglue/tests/test_module.py`, `livetest/test_m1_bridge.py`

**Interfaces:**
- Consumes: the v1 generator (`tools/gen_protocol.py`: `validate`, `render_cpp`, `render_py`, `render_md`, `camel`, `upper`, `py`), `spy::parse_request`, `spy::is_uint`, `spy::form_ref_from_json`, `spy::Config`/`parse_config`/`config_to_json`.
- Produces (exactly the index Contracts):
  - Protocol `[1, 1]`; ops `events.subscribe` (batchable no, needs_game **no**; args `event` `event_name` required, `filter` `event_filter` optional, `max_queue` `queue_size` optional), `events.unsubscribe` (no, **no**; `sub` `u64` required), `game.save` (no, yes; `name` `save_name` required, `timeout_ms` optional), `game.load` (no, **no**; `save` `save_name` required, `timeout_ms` optional).
  - Arg types `event_name`, `event_filter`, `queue_size`, `u64`, `save_name` (generated `spy::ArgType::event_name`, `event_filter`, `queue_size`, `u64`, `save_name`).
  - Limits (generated `spy::kDefaultEventQueue` = 1024, `kMaxEventQueue` = 65536, `kMaxSubscriptions` = 1024, `kMaxFilterValues` = 128, `kMaxEventsPerNext` = 256, `kMaxChannelPayloadBytes` = 65536, `kMaxChannelBytes` = 256; Python `DEFAULT_EVENT_QUEUE`, `MAX_EVENT_QUEUE`, `MAX_SUBSCRIPTIONS`, `MAX_FILTER_VALUES`, `MAX_EVENTS_PER_NEXT`, `MAX_CHANNEL_PAYLOAD_BYTES`, `MAX_CHANNEL_BYTES`).
  - C++: `enum class spy::EventFieldType { form, integer, floating, boolean, string }`, `struct spy::EventField { std::string_view name; EventFieldType type; bool nullable; }`, `struct spy::EventSpec { std::string_view name; std::span<const EventField> fields; std::span<const std::string_view> filter_fields; std::span<const std::string_view> filter_required_any_of; bool deliver_while_loading; }`, `spy::kEvents` (20 entries, table order), `constexpr const EventSpec* spy::find_event(std::string_view)`, `spy::event_names::kDeath` ... `kNewGame`, `spy::ops::kEventsSubscribe`, `kEventsUnsubscribe`, `kGameSave`, `kGameLoad`, `spy::kSaveNamePattern`.
  - Python: `_protocol_generated.EVENTS` (name → `{"fields": ((name, type, nullable), ...), "filter_fields": (...), "filter_required_any_of": (...), "deliver_while_loading": bool}`), `EVENT_DEATH` ... `EVENT_NEW_GAME`, `SAVE_NAME_PATTERN`, `OP_EVENTS_SUBSCRIBE`, `OP_EVENTS_UNSUBSCRIBE`, `OP_GAME_SAVE`, `OP_GAME_LOAD`.
  - `bool spy::is_valid_save_name(std::string_view)` in `requests.h` (agrees with `kSaveNamePattern`; Task 35's fake and Task 41's plugin code may use it).
  - `Config::trace_timing` (bool, default false), `Config::console_echo_errors` (bool, default true), parsed by `parse_config`, emitted by `config_to_json`; generated `config_defaults::kTraceTiming`, `config_defaults::kConsoleEchoErrors`.
  - Fixture `protocol/v1/fixtures/events.json` with keys `about`, `subscribe` (`{request, result}`), `unsubscribe` (`{request, result}` or `{request, error}`), `frames` (event objects), `invalid_filters` (`{event, filter, error, message}`; `message` is a substring of the error message Task 34's `parse_event_filter` must produce).
  - Golden responses `responses.json` ids 62 (`game.save` ok), 63 (`game.load` ok), 64 (`bad_request` message `a save named skython_t41 already exists`, `detail.reason` `save_exists`), 65 (`not_found` message `no save named no_such_save_skython`). Task 35's `FakeBackend` must produce exactly these texts.

---

- [ ] **Step 1: Write the failing generator tests**

In `tools/test_gen_protocol.py`:

(a) In the `test_schema_problems_are_reported` parameter list, the case `("required limit max_array_items removed", lambda m: m["limits"].pop(-1), ...)` relies on that limit being last; this task appends limits after it. Replace that one line with:
```python
    ("required limit max_array_items removed",
     lambda m: m["limits"].remove(next(l for l in m["limits"] if l["name"] == "max_array_items")), "'max_array_items' is required"),
```

(b) Append after `test_schema_problems_are_reported`:
```python
def ev(m, name):
    return next(e for e in m["events"] if e["name"] == name)


def limit_removed(name):
    return lambda m: m["limits"].remove(next(l for l in m["limits"] if l["name"] == name))


@pytest.mark.parametrize("name, fn, expect", [
    ("duplicate event", lambda m: m["events"].append(dict(m["events"][0])), "duplicate event"),
    ("event name not an identifier", lambda m: ev(m, "death").update(name="Death!"), "lower-case identifier"),
    ("unknown event field type", lambda m: ev(m, "death")["fields"][0].update(type="pointer"), "unknown event field type"),
    ("field without nullable", lambda m: ev(m, "death")["fields"][0].pop("nullable"), "missing 'nullable'"),
    ("filter field that is not a field", lambda m: ev(m, "death")["filter_fields"].append("nope"), "'nope' is not a field"),
    ("filter on a bool field", lambda m: ev(m, "hit")["filter_fields"].append("blocked"), "cannot be filtered"),
    ("required filter that is not a filter field", lambda m: ev(m, "hit")["filter_required_any_of"].append("source"),
     "'source' is not a filter field"),
    ("event without deliver_while_loading", lambda m: ev(m, "death").pop("deliver_while_loading"), "missing 'deliver_while_loading'"),
    ("no events", lambda m: m.update(events=[]), "events: empty"),
    ("save name pattern not anchored", lambda m: m.update(save_name_pattern="[a-z]+"), "anchored"),
    ("save name pattern not a regex", lambda m: m.update(save_name_pattern="^[a-$"), "not a regular expression"),
    ("required limit max_event_queue removed", limit_removed("max_event_queue"), "'max_event_queue' is required"),
    ("required limit max_filter_values removed", limit_removed("max_filter_values"), "'max_filter_values' is required"),
    ("unknown argument type on a new op",
     lambda m: next(o for o in m["ops"] if o["name"] == "game.save")["args"][0].update(type="savename"), "unknown argument type"),
])
def test_event_schema_problems_are_reported(name, fn, expect):
    problems = mutate(fn)
    assert any(expect in p for p in problems), (name, problems)
```

(c) In `test_the_generated_python_module_matches_the_source`, append before the end of the function:
```python
    assert set(ns["EVENTS"]) == {e["name"] for e in m["events"]}
    for e in m["events"]:
        spec = ns["EVENTS"][e["name"]]
        assert spec["fields"] == tuple((f["name"], f["type"], f["nullable"]) for f in e["fields"])
        assert spec["filter_fields"] == tuple(e["filter_fields"])
        assert spec["filter_required_any_of"] == tuple(e["filter_required_any_of"])
        assert spec["deliver_while_loading"] is e["deliver_while_loading"]
        assert ns["EVENT_" + e["name"].upper()] == e["name"]
    assert ns["SAVE_NAME_PATTERN"] == m["save_name_pattern"]
```

(d) In `test_the_generated_header_compiles_standalone`, add these lines to the test source, before `"int main() { return 0; }\n"`:
```python
                                    "static_assert(spy::find_event(\"hit\")->filter_required_any_of.size() == 2);\n"
                                    "static_assert(spy::find_event(spy::event_names::kNewGame)->fields.empty());\n"
                                    "static_assert(spy::find_event(\"no_such_event\") == nullptr);\n"
                                    "static_assert(!spy::find_op(spy::ops::kGameLoad)->needs_game);\n"
                                    "static_assert(spy::find_op(spy::ops::kGameSave)->needs_game);\n"
                                    "static_assert(spy::kDefaultEventQueue == 1024u && spy::kMaxEventsPerNext == 256u);\n"
                                    "static_assert(spy::config_defaults::kConsoleEchoErrors && !spy::config_defaults::kTraceTiming);\n"
```

(e) Replace `test_only_game_state_works_without_a_game` and `test_the_config_keys_are_the_plugin_config_fields_with_their_defaults` with:
```python
def test_the_ops_answered_without_a_game():
    assert [o["name"] for o in model()["ops"] if not o["needs_game"]] == ["game.state", "events.subscribe", "events.unsubscribe", "game.load"]


def test_the_config_keys_are_the_plugin_config_fields_with_their_defaults():
    keys = {k["name"]: k["default"] for k in model()["config_keys"]}
    assert keys == {"python_enabled": True, "scripts_enabled": True, "bridge_enabled": False, "port": 45977, "fixed_token": "",
                    "session_file": "", "default_timeout_ms": 5000, "max_inflight_per_connection": 256,
                    "max_inflight_total": 1024, "enable_console": False, "trace_timing": False, "console_echo_errors": True}


def test_the_generated_markdown_lists_every_event_and_the_save_name_pattern():
    md = gen.render_md(model())
    for e in model()["events"]:
        assert f"| `{e['name']}` |" in md, e["name"]
    assert model()["save_name_pattern"] in md


def test_the_events_fixture_uses_only_names_from_the_table():
    m = model()
    events = {e["name"]: e for e in m["events"]}
    fx = fixture("events.json")
    for c in fx["subscribe"]:
        assert c["request"]["op"] == "events.subscribe" and c["request"]["args"]["event"] in events
        assert set(c["request"]["args"].get("filter") or {}) <= set(events[c["request"]["args"]["event"]]["filter_fields"])
    for f in fx["frames"]:
        assert f["type"] == "event" and f["event"] in events
        assert set(f["data"]) == {x["name"] for x in events[f["event"]]["fields"]}
    codes = {c["code"] for c in m["error_codes"]}
    for c in fx["invalid_filters"]:
        assert c["event"] in events and c["error"] in codes and c["message"]
```

- [ ] **Step 2: Run the generator tests to verify they fail**

Run: `uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q 2>&1 | tail -3`
Expected: FAIL; several `KeyError: 'events'` / `StopIteration` failures (protocol.json has no events, `events.json` does not exist).

- [ ] **Step 3: Teach the generator the events table, the new argument types and the save-name pattern**

In `tools/gen_protocol.py`:

(a) Extend `ARG_TYPES` (append after `"call_list"`) and add two tables under it:
```python
    "event_name": ("event_name", "a name from the events table"),
    "event_filter": ("event_filter", "object: filter field -> 1..max_filter_values values (PROTOCOL.md, Events); null means absent"),
    "queue_size": ("queue_size", "integer from 1 to max_event_queue"),
    "u64": ("u64", "unsigned 64-bit integer"),
    "save_name": ("save_name", "string matching save_name_pattern"),
}
EVENT_FIELD_TYPES = {"form": "form", "int": "integer", "float": "floating", "bool": "boolean", "string": "string"}  # protocol -> C++ enumerator
FILTERABLE_FIELD_TYPES = ("form", "int", "string")
```
(the closing `}` shown replaces the existing one of `ARG_TYPES`).

(b) In `validate`, add the seven limit names to the `for req in (...)` tuple of required limits: `"default_event_queue", "max_event_queue", "max_subscriptions", "max_filter_values", "max_events_per_next", "max_channel_payload_bytes", "max_channel_bytes"`. Then, just before `return p` at the end of `validate`, add:
```python
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
```

(c) In `render_cpp`, insert after the `find_op` block (the `"}", ""]` that closes it) and before `L.append("// ---- config keys")`:
```python
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
```

(d) In `render_py`, insert after the `L.append(f"BATCHABLE_OPS = ...")` line and its `L.append("")`:
```python
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
```

(e) In `render_md`, change the `"## Ops (M1)"` heading string to `"## Ops"`, and insert after the argument-types loop (`for t, (_, meaning) in ARG_TYPES.items(): ...`) and before the `"## Plugin ini keys"` line:
```python
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
```

- [ ] **Step 4: Edit `protocol/protocol.json`**

(a) `"protocol": {"major": 1, "minor": 1}`.

(b) Append to `"limits"` (after `max_array_items`):
```json
    {"name": "default_event_queue", "value": 1024, "ctype": "size", "unit": "events",
     "doc": "Queue length of a subscription that sends no max_queue. A full queue drops its oldest event; the next delivered event carries the count in dropped."},
    {"name": "max_event_queue", "value": 65536, "ctype": "size", "unit": "events",
     "doc": "Largest max_queue of one subscription."},
    {"name": "max_subscriptions", "value": 1024, "ctype": "size", "unit": "subscriptions",
     "doc": "Subscriptions of all consumers together (in-process scripts and bridge connections); beyond it events.subscribe is busy."},
    {"name": "max_filter_values", "value": 128, "ctype": "size", "unit": "values",
     "doc": "Most values in one field of an event filter."},
    {"name": "max_events_per_next", "value": 256, "ctype": "size", "unit": "events",
     "doc": "Most events one _skython.next_events call returns."},
    {"name": "max_channel_payload_bytes", "value": 65536, "ctype": "size", "unit": "bytes",
     "doc": "A Skython.Send payload longer than this is cut at a UTF-8 boundary and its channel event carries truncated: true."},
    {"name": "max_channel_bytes", "value": 256, "ctype": "size", "unit": "bytes",
     "doc": "Longest channel name of a channel event; a longer name is cut at a UTF-8 boundary."}
```

(c) After the `"array_elem_types"` line add: `"save_name_pattern": "^[A-Za-z0-9_]{1,64}$",`

(d) Append to `"config_keys"` (after `enable_console`):
```json
    {"name": "trace_timing", "type": "bool", "default": false,
     "doc": "Development only: log one timing line per request (submit, task start, callback, done)."},
    {"name": "console_echo_errors", "type": "bool", "default": true,
     "doc": "A failing player script also prints one rate-limited line in the in-game console."}
```

(e) Append to `"ops"` (after `papyrus.batch`):
```json
    {"name": "events.subscribe", "batchable": false, "needs_game": false, "exactly_one_of": [],
     "doc": "Subscribe the calling consumer to one event. The consumer travels beside the request text, never inside it.",
     "result": "`{\"sub\":n,\"event\":\"<name>\",\"max_queue\":n}`",
     "args": [
       {"name": "event", "type": "event_name", "required": true, "doc": "a name from the events table"},
       {"name": "filter", "type": "event_filter", "required": false, "doc": "absent or null means every event"},
       {"name": "max_queue", "type": "queue_size", "required": false, "doc": "absent means default_event_queue"}]},
    {"name": "events.unsubscribe", "batchable": false, "needs_game": false, "exactly_one_of": [],
     "doc": "End one of the calling consumer's subscriptions; its queued events are discarded.",
     "result": "null",
     "args": [
       {"name": "sub", "type": "u64", "required": true, "doc": "the sub that events.subscribe returned"}]},
    {"name": "game.save", "batchable": false, "needs_game": true, "exactly_one_of": [],
     "doc": "Save the game under a new name. An existing save of that name is never overwritten.",
     "result": "`{\"name\":\"<name>\"}` once the game reports that save",
     "args": [
       {"name": "name", "type": "save_name", "required": true, "doc": ""},
       {"name": "timeout_ms", "type": "timeout_ms", "required": false, "doc": "absent means the plugin's default_timeout_ms"}]},
    {"name": "game.load", "batchable": false, "needs_game": false, "exactly_one_of": [],
     "doc": "Load a save. Works at the main menu; refused while the game is starting or already loading.",
     "result": "`{\"save\":\"<name>\",\"started\":true}` once the load has started",
     "args": [
       {"name": "save", "type": "save_name", "required": true, "doc": ""},
       {"name": "timeout_ms", "type": "timeout_ms", "required": false, "doc": "absent means the plugin's default_timeout_ms"}]}
```

(f) Add the `"events"` table after `"ops"` (a comma after the closing `]` of `"ops"`):
```json
  "events": [
    {"name": "death", "doc": "An actor died.", "source": "TESDeathEvent, dead == true", "deliver_while_loading": false,
     "fields": [{"name": "victim", "type": "form", "nullable": false}, {"name": "killer", "type": "form", "nullable": true}],
     "filter_fields": ["victim", "killer"], "filter_required_any_of": []},
    {"name": "dying", "doc": "An actor started dying (raised before death).", "source": "TESDeathEvent, dead == false", "deliver_while_loading": false,
     "fields": [{"name": "victim", "type": "form", "nullable": false}, {"name": "killer", "type": "form", "nullable": true}],
     "filter_fields": ["victim", "killer"], "filter_required_any_of": []},
    {"name": "quest_stage", "doc": "A quest stage was set.", "source": "TESQuestStageEvent", "deliver_while_loading": false,
     "fields": [{"name": "quest", "type": "form", "nullable": false}, {"name": "stage", "type": "int", "nullable": false}],
     "filter_fields": ["quest", "stage"], "filter_required_any_of": []},
    {"name": "quest_start", "doc": "A quest started.", "source": "TESQuestStartStopEvent, started", "deliver_while_loading": false,
     "fields": [{"name": "quest", "type": "form", "nullable": false}],
     "filter_fields": ["quest"], "filter_required_any_of": []},
    {"name": "quest_stop", "doc": "A quest stopped.", "source": "TESQuestStartStopEvent, stopped", "deliver_while_loading": false,
     "fields": [{"name": "quest", "type": "form", "nullable": false}],
     "filter_fields": ["quest"], "filter_required_any_of": []},
    {"name": "mod_event", "doc": "A Papyrus mod event (SendModEvent).", "source": "SKSE ModCallbackEvent", "deliver_while_loading": true,
     "fields": [{"name": "name", "type": "string", "nullable": false}, {"name": "str_arg", "type": "string", "nullable": false},
                {"name": "num_arg", "type": "float", "nullable": false}, {"name": "sender", "type": "form", "nullable": true}],
     "filter_fields": ["name", "sender"], "filter_required_any_of": []},
    {"name": "channel", "doc": "A Skython.Send call from Papyrus.", "source": "native Skython.Send", "deliver_while_loading": true,
     "fields": [{"name": "channel", "type": "string", "nullable": false}, {"name": "payload", "type": "string", "nullable": false},
                {"name": "truncated", "type": "bool", "nullable": false}],
     "filter_fields": ["channel"], "filter_required_any_of": []},
    {"name": "hit", "doc": "Something was hit. A subscription must filter on target or aggressor.", "source": "TESHitEvent", "deliver_while_loading": false,
     "fields": [{"name": "target", "type": "form", "nullable": false}, {"name": "aggressor", "type": "form", "nullable": true},
                {"name": "source", "type": "form", "nullable": true}, {"name": "projectile", "type": "form", "nullable": true},
                {"name": "power_attack", "type": "bool", "nullable": false}, {"name": "sneak_attack", "type": "bool", "nullable": false},
                {"name": "bash_attack", "type": "bool", "nullable": false}, {"name": "blocked", "type": "bool", "nullable": false}],
     "filter_fields": ["target", "aggressor"], "filter_required_any_of": ["target", "aggressor"]},
    {"name": "equip", "doc": "An actor equipped an item (item is the base object).", "source": "TESEquipEvent, equipped", "deliver_while_loading": false,
     "fields": [{"name": "actor", "type": "form", "nullable": false}, {"name": "item", "type": "form", "nullable": false},
                {"name": "reference", "type": "form", "nullable": true}],
     "filter_fields": ["actor", "item"], "filter_required_any_of": []},
    {"name": "unequip", "doc": "An actor unequipped an item (item is the base object).", "source": "TESEquipEvent, unequipped", "deliver_while_loading": false,
     "fields": [{"name": "actor", "type": "form", "nullable": false}, {"name": "item", "type": "form", "nullable": false},
                {"name": "reference", "type": "form", "nullable": true}],
     "filter_fields": ["actor", "item"], "filter_required_any_of": []},
    {"name": "container_changed", "doc": "Items moved between containers. A subscription must filter on a container or the item.", "source": "TESContainerChangedEvent", "deliver_while_loading": false,
     "fields": [{"name": "old_container", "type": "form", "nullable": true}, {"name": "new_container", "type": "form", "nullable": true},
                {"name": "item", "type": "form", "nullable": false}, {"name": "count", "type": "int", "nullable": false},
                {"name": "reference", "type": "form", "nullable": true}],
     "filter_fields": ["old_container", "new_container", "item"], "filter_required_any_of": ["old_container", "new_container", "item"]},
    {"name": "cell_attach", "doc": "A reference was attached to the loaded cells. It can repeat for one reference.", "source": "TESCellAttachDetachEvent, attached", "deliver_while_loading": false,
     "fields": [{"name": "reference", "type": "form", "nullable": false}],
     "filter_fields": ["reference"], "filter_required_any_of": []},
    {"name": "cell_detach", "doc": "A reference was detached from the loaded cells.", "source": "TESCellAttachDetachEvent, detached", "deliver_while_loading": false,
     "fields": [{"name": "reference", "type": "form", "nullable": false}],
     "filter_fields": ["reference"], "filter_required_any_of": []},
    {"name": "activate", "doc": "A reference was activated.", "source": "TESActivateEvent", "deliver_while_loading": false,
     "fields": [{"name": "target", "type": "form", "nullable": false}, {"name": "activator", "type": "form", "nullable": true}],
     "filter_fields": ["target", "activator"], "filter_required_any_of": []},
    {"name": "player_location", "doc": "The player moved to another location.", "source": "TESActorLocationChangeEvent for the player", "deliver_while_loading": false,
     "fields": [{"name": "old_location", "type": "form", "nullable": true}, {"name": "new_location", "type": "form", "nullable": true}],
     "filter_fields": ["old_location", "new_location"], "filter_required_any_of": []},
    {"name": "menu_open", "doc": "A menu opened.", "source": "MenuOpenCloseEvent, opening", "deliver_while_loading": true,
     "fields": [{"name": "menu", "type": "string", "nullable": false}],
     "filter_fields": ["menu"], "filter_required_any_of": []},
    {"name": "menu_close", "doc": "A menu closed.", "source": "MenuOpenCloseEvent, closing", "deliver_while_loading": true,
     "fields": [{"name": "menu", "type": "string", "nullable": false}],
     "filter_fields": ["menu"], "filter_required_any_of": []},
    {"name": "game_loaded", "doc": "A loaded save or a new game became interactive: the end of a load.", "source": "interactive signal after kPostLoadGame (success) or kNewGame", "deliver_while_loading": true,
     "fields": [{"name": "new_game", "type": "bool", "nullable": false}],
     "filter_fields": [], "filter_required_any_of": []},
    {"name": "game_saved", "doc": "The game was saved.", "source": "kSaveGame message", "deliver_while_loading": true,
     "fields": [{"name": "name", "type": "string", "nullable": false}],
     "filter_fields": [], "filter_required_any_of": []},
    {"name": "new_game", "doc": "A new game started.", "source": "kNewGame message", "deliver_while_loading": true,
     "fields": [],
     "filter_fields": [], "filter_required_any_of": []}
  ]
```

- [ ] **Step 5: Regenerate and run the generator tests**

Run: `python3 tools/gen_protocol.py && uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q 2>&1 | tail -3`
Expected: `wrote` for all three generated files, then FAIL only in `test_the_events_fixture_uses_only_names_from_the_table` (`events.json` does not exist yet) and in fixture tests that need the new ops' fixtures (`no valid fixture exercises events.subscribe`). Everything else passes.

- [ ] **Step 6: Write the fixtures**

`protocol/v1/fixtures/events.json`:
```json
{
  "about": "Golden events.* requests and results, event frames, and filters that events.subscribe refuses. The consumer is never in a request: it travels beside the request text (PROTOCOL.md, Events). Frames are exactly what _skython.next_events returns and the dev bridge writes. invalid_filters: message is a substring of the error message.",
  "subscribe": [
    {"request": {"type": "request", "id": 60, "op": "events.subscribe", "args": {"event": "death"}},
     "result": {"sub": 1, "event": "death", "max_queue": 1024}},
    {"request": {"type": "request", "id": 61, "op": "events.subscribe", "args": {"event": "death", "filter": {"victim": [{"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor"}]}, "max_queue": 16}},
     "result": {"sub": 2, "event": "death", "max_queue": 16}},
    {"request": {"type": "request", "id": 62, "op": "events.subscribe", "args": {"event": "hit", "filter": {"target": [{"kind": "form", "form_id": 20}], "aggressor": [{"kind": "form", "form_id": 20}, {"kind": "form", "form_id": 4278190081}]}, "max_queue": 65536}},
     "result": {"sub": 3, "event": "hit", "max_queue": 65536}},
    {"request": {"type": "request", "id": 63, "op": "events.subscribe", "args": {"event": "quest_stage", "filter": {"quest": [{"kind": "form", "form_id": 79652}], "stage": [10, 20]}, "max_queue": 1}},
     "result": {"sub": 4, "event": "quest_stage", "max_queue": 1}},
    {"request": {"type": "request", "id": 64, "op": "events.subscribe", "args": {"event": "channel", "filter": {"channel": ["my_mod", "Other_Mod"]}}},
     "result": {"sub": 5, "event": "channel", "max_queue": 1024}},
    {"request": {"type": "request", "id": 65, "op": "events.subscribe", "args": {"event": "game_loaded", "filter": null}},
     "result": {"sub": 6, "event": "game_loaded", "max_queue": 1024}}
  ],
  "unsubscribe": [
    {"request": {"type": "request", "id": 70, "op": "events.unsubscribe", "args": {"sub": 2}}, "result": null},
    {"request": {"type": "request", "id": 71, "op": "events.unsubscribe", "args": {"sub": 18446744073709551615}}, "error": "not_found"}
  ],
  "frames": [
    {"type": "event", "event": "death", "sub": 7, "seq": 12, "epoch": 3, "data": {"victim": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 3}, "killer": null}},
    {"type": "event", "event": "death", "sub": 7, "seq": 17, "epoch": 3, "data": {"victim": {"kind": "form", "form_id": 4278190081, "type": "Actor", "epoch": 3, "fingerprint": {"base": "Skyrim.esm|0x01A69E", "type": "Actor"}}, "killer": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 3}}, "dropped": 4, "suppressed": 2},
    {"type": "event", "event": "cell_attach", "sub": 9, "seq": 1, "epoch": 4, "data": {"reference": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 4}}, "suppressed": 2},
    {"type": "event", "event": "quest_stage", "sub": 4, "seq": 1, "epoch": 3, "data": {"quest": {"kind": "form", "form_id": 79652, "form_key": "Skyrim.esm|0x013724", "type": "Quest", "epoch": 3}, "stage": 10}},
    {"type": "event", "event": "mod_event", "sub": 8, "seq": 2, "epoch": 3, "data": {"name": "my_mod_ready", "str_arg": "s", "num_arg": 4.5, "sender": null}},
    {"type": "event", "event": "channel", "sub": 5, "seq": 3, "epoch": 3, "data": {"channel": "my_mod", "payload": "stage_10", "truncated": false}},
    {"type": "event", "event": "hit", "sub": 3, "seq": 1, "epoch": 3, "data": {"target": {"kind": "form", "form_id": 20, "form_key": "Skyrim.esm|0x000014", "type": "Actor", "epoch": 3}, "aggressor": null, "source": null, "projectile": null, "power_attack": false, "sneak_attack": false, "bash_attack": true, "blocked": false}},
    {"type": "event", "event": "game_loaded", "sub": 6, "seq": 1, "epoch": 4, "data": {"new_game": false}},
    {"type": "event", "event": "new_game", "sub": 10, "seq": 1, "epoch": 0, "data": {}}
  ],
  "invalid_filters": [
    {"event": "death", "filter": {"nope": [1]}, "error": "bad_request", "message": "has no filter field 'nope'"},
    {"event": "death", "filter": {"victim": []}, "error": "bad_request", "message": "needs a non-empty array"},
    {"event": "death", "filter": {"victim": {"kind": "form", "form_id": 20}}, "error": "bad_request", "message": "needs a non-empty array"},
    {"event": "death", "filter": {"victim": [{"kind": "form", "form_key": "Skyrim.esm|0x000014"}]}, "error": "bad_request", "message": "resolve the form first"},
    {"event": "death", "filter": {"victim": [20]}, "error": "bad_request", "message": "must be a form reference"},
    {"event": "death", "filter": {"victim": [{"kind": "form", "form_id": 1}, {"kind": "form", "form_id": 2}, {"kind": "form", "form_id": 3}, {"kind": "form", "form_id": 4}, {"kind": "form", "form_id": 5}, {"kind": "form", "form_id": 6}, {"kind": "form", "form_id": 7}, {"kind": "form", "form_id": 8}, {"kind": "form", "form_id": 9}, {"kind": "form", "form_id": 10}, {"kind": "form", "form_id": 11}, {"kind": "form", "form_id": 12}, {"kind": "form", "form_id": 13}, {"kind": "form", "form_id": 14}, {"kind": "form", "form_id": 15}, {"kind": "form", "form_id": 16}, {"kind": "form", "form_id": 17}, {"kind": "form", "form_id": 18}, {"kind": "form", "form_id": 19}, {"kind": "form", "form_id": 20}, {"kind": "form", "form_id": 21}, {"kind": "form", "form_id": 22}, {"kind": "form", "form_id": 23}, {"kind": "form", "form_id": 24}, {"kind": "form", "form_id": 25}, {"kind": "form", "form_id": 26}, {"kind": "form", "form_id": 27}, {"kind": "form", "form_id": 28}, {"kind": "form", "form_id": 29}, {"kind": "form", "form_id": 30}, {"kind": "form", "form_id": 31}, {"kind": "form", "form_id": 32}, {"kind": "form", "form_id": 33}, {"kind": "form", "form_id": 34}, {"kind": "form", "form_id": 35}, {"kind": "form", "form_id": 36}, {"kind": "form", "form_id": 37}, {"kind": "form", "form_id": 38}, {"kind": "form", "form_id": 39}, {"kind": "form", "form_id": 40}, {"kind": "form", "form_id": 41}, {"kind": "form", "form_id": 42}, {"kind": "form", "form_id": 43}, {"kind": "form", "form_id": 44}, {"kind": "form", "form_id": 45}, {"kind": "form", "form_id": 46}, {"kind": "form", "form_id": 47}, {"kind": "form", "form_id": 48}, {"kind": "form", "form_id": 49}, {"kind": "form", "form_id": 50}, {"kind": "form", "form_id": 51}, {"kind": "form", "form_id": 52}, {"kind": "form", "form_id": 53}, {"kind": "form", "form_id": 54}, {"kind": "form", "form_id": 55}, {"kind": "form", "form_id": 56}, {"kind": "form", "form_id": 57}, {"kind": "form", "form_id": 58}, {"kind": "form", "form_id": 59}, {"kind": "form", "form_id": 60}, {"kind": "form", "form_id": 61}, {"kind": "form", "form_id": 62}, {"kind": "form", "form_id": 63}, {"kind": "form", "form_id": 64}, {"kind": "form", "form_id": 65}, {"kind": "form", "form_id": 66}, {"kind": "form", "form_id": 67}, {"kind": "form", "form_id": 68}, {"kind": "form", "form_id": 69}, {"kind": "form", "form_id": 70}, {"kind": "form", "form_id": 71}, {"kind": "form", "form_id": 72}, {"kind": "form", "form_id": 73}, {"kind": "form", "form_id": 74}, {"kind": "form", "form_id": 75}, {"kind": "form", "form_id": 76}, {"kind": "form", "form_id": 77}, {"kind": "form", "form_id": 78}, {"kind": "form", "form_id": 79}, {"kind": "form", "form_id": 80}, {"kind": "form", "form_id": 81}, {"kind": "form", "form_id": 82}, {"kind": "form", "form_id": 83}, {"kind": "form", "form_id": 84}, {"kind": "form", "form_id": 85}, {"kind": "form", "form_id": 86}, {"kind": "form", "form_id": 87}, {"kind": "form", "form_id": 88}, {"kind": "form", "form_id": 89}, {"kind": "form", "form_id": 90}, {"kind": "form", "form_id": 91}, {"kind": "form", "form_id": 92}, {"kind": "form", "form_id": 93}, {"kind": "form", "form_id": 94}, {"kind": "form", "form_id": 95}, {"kind": "form", "form_id": 96}, {"kind": "form", "form_id": 97}, {"kind": "form", "form_id": 98}, {"kind": "form", "form_id": 99}, {"kind": "form", "form_id": 100}, {"kind": "form", "form_id": 101}, {"kind": "form", "form_id": 102}, {"kind": "form", "form_id": 103}, {"kind": "form", "form_id": 104}, {"kind": "form", "form_id": 105}, {"kind": "form", "form_id": 106}, {"kind": "form", "form_id": 107}, {"kind": "form", "form_id": 108}, {"kind": "form", "form_id": 109}, {"kind": "form", "form_id": 110}, {"kind": "form", "form_id": 111}, {"kind": "form", "form_id": 112}, {"kind": "form", "form_id": 113}, {"kind": "form", "form_id": 114}, {"kind": "form", "form_id": 115}, {"kind": "form", "form_id": 116}, {"kind": "form", "form_id": 117}, {"kind": "form", "form_id": 118}, {"kind": "form", "form_id": 119}, {"kind": "form", "form_id": 120}, {"kind": "form", "form_id": 121}, {"kind": "form", "form_id": 122}, {"kind": "form", "form_id": 123}, {"kind": "form", "form_id": 124}, {"kind": "form", "form_id": 125}, {"kind": "form", "form_id": 126}, {"kind": "form", "form_id": 127}, {"kind": "form", "form_id": 128}, {"kind": "form", "form_id": 129}]}, "error": "bad_request", "message": "at most 128"},
    {"event": "quest_stage", "filter": {"stage": ["10"]}, "error": "bad_request", "message": "must be an integer"},
    {"event": "quest_stage", "filter": {"stage": [2147483648]}, "error": "bad_request", "message": "must be an integer"},
    {"event": "channel", "filter": {"channel": [5]}, "error": "bad_request", "message": "must be a string"},
    {"event": "game_loaded", "filter": {"new_game": [true]}, "error": "bad_request", "message": "has no filter field 'new_game'"},
    {"event": "death", "filter": "victim", "error": "bad_request", "message": "filter must be an object"},
    {"event": "hit", "filter": null, "error": "bad_request", "message": "hit needs a filter on target or aggressor"},
    {"event": "hit", "filter": {}, "error": "bad_request", "message": "hit needs a filter on target or aggressor"},
    {"event": "container_changed", "filter": {}, "error": "bad_request", "message": "container_changed needs a filter on old_container, new_container or item"}
  ]
}
```
(The 129-value entry is one long line; generate it rather than typing it: `python3 -c 'import json;print(json.dumps([{"kind":"form","form_id":i} for i in range(1,130)]))'` and paste the output as the `victim` array.)

`protocol/v1/fixtures/requests.json`: append to the end of the `"valid"` array (a comma after its current last entry):
```json
    {"type": "request", "id": 60, "op": "events.subscribe", "args": {"event": "death", "filter": {"victim": [{"kind": "form", "form_id": 20}]}, "max_queue": 16}},
    {"type": "request", "id": 61, "op": "events.unsubscribe", "args": {"sub": 7}},
    {"type": "request", "id": 62, "op": "game.save", "args": {"name": "skython_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "timeout_ms": 60000}},
    {"type": "request", "id": 63, "op": "game.load", "args": {"save": "skython_t41", "timeout_ms": 120000}}
```
(the `game.save` name is exactly 64 characters: the longest valid one). Append to the end of the `"invalid"` array:
```json
    {"message": {"type": "request", "id": 80, "op": "events.subscribe", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 81, "op": "events.subscribe", "args": {"event": "no_such_event"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 82, "op": "events.subscribe", "args": {"event": 5}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 83, "op": "events.subscribe", "args": {"event": "death", "filter": [["victim"]]}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 84, "op": "events.subscribe", "args": {"event": "death", "max_queue": 0}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 85, "op": "events.subscribe", "args": {"event": "death", "max_queue": 65537}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 86, "op": "events.subscribe", "args": {"event": "death", "max_queue": "16"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 87, "op": "events.unsubscribe", "args": {"sub": -1}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 88, "op": "events.unsubscribe", "args": {"sub": "7"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 89, "op": "events.unsubscribe", "args": {}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 90, "op": "game.save", "args": {"name": "has space"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 91, "op": "game.save", "args": {"name": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 92, "op": "game.save", "args": {"name": ""}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 93, "op": "game.load", "args": {"save": "../escape"}}, "error": "bad_request"},
    {"message": {"type": "request", "id": 94, "op": "game.load", "args": {"save": "skython_ok", "timeout_ms": 0}}, "error": "bad_request"}
```
(id 91's name is 65 characters.)

`protocol/v1/fixtures/responses.json`: change the welcome entry to
```json
    {"type": "welcome", "protocol": [1, 1], "plugin_version": "0.1.0", "game_version": "1.6.1170.0", "runtime": "AE", "epoch": 1, "capabilities": ["game.state", "events.subscribe", "events.unsubscribe", "game.save", "game.load"]},
```
append to `"valid"`:
```json
    {"type": "response", "id": 62, "ok": true, "result": {"name": "skython_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}},
    {"type": "response", "id": 63, "ok": true, "result": {"save": "skython_t41", "started": true}},
    {"type": "response", "id": 64, "ok": false, "error": {"code": "bad_request", "message": "a save named skython_t41 already exists", "detail": {"reason": "save_exists"}}},
    {"type": "response", "id": 65, "ok": false, "error": {"code": "not_found", "message": "no save named no_such_save_skython", "detail": {}}}
```
and extend `"about"` with: `; 62 game.save; 63 game.load started; 64 game.save of an existing name (save_exists); 65 game.load of a missing save`.

`protocol/v1/fixtures/hello.json`: add to `"valid"`: `{"type": "hello", "protocol": [1, 1], "token": "00112233445566778899aabbccddeeff", "client": "test"},` (the `[1, 0]` hello stays: an older-minor client still connects).

- [ ] **Step 7: Run the generator tests and the drift check**

Run: `uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q 2>&1 | tail -2 && python3 tools/gen_protocol.py --check`
Expected: all pass (`... passed`), then `gen_protocol: 3 generated files are up to date`.

- [ ] **Step 8: Write the failing core tests**

`core/tests/test_generated.cpp`, in "the v1 wire contract is pinned": change `CHECK(spy::kProtocolMinor == 0);` to `CHECK(spy::kProtocolMinor == 1);`, add after the `kMaxArrayItems` line:
```cpp
    CHECK(spy::kDefaultEventQueue == 1024u);
    CHECK(spy::kMaxEventQueue == 65536u);
    CHECK(spy::kMaxSubscriptions == 1024u);
    CHECK(spy::kMaxFilterValues == 128u);
    CHECK(spy::kMaxEventsPerNext == 256u);
    CHECK(spy::kMaxChannelPayloadBytes == 65536u);
    CHECK(spy::kMaxChannelBytes == 256u);
    CHECK_FALSE(spy::config_defaults::kTraceTiming);
    CHECK(spy::config_defaults::kConsoleEchoErrors);
    CHECK(spy::kSaveNamePattern == "^[A-Za-z0-9_]{1,64}$");
```
and replace the `ops` vector with:
```cpp
    const std::vector<std::string> ops = {"game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
                                          "papyrus.get_property", "papyrus.set_property", "papyrus.batch",
                                          "events.subscribe", "events.unsubscribe", "game.save", "game.load"};
```
Replace the test case "only game.state is answered without a loaded game" with:
```cpp
TEST_CASE("the ops answered without a loaded game are game.state, events.subscribe, events.unsubscribe and game.load")
{
    const std::set<std::string_view> no_game = {spy::ops::kGameState, spy::ops::kEventsSubscribe, spy::ops::kEventsUnsubscribe,
                                                spy::ops::kGameLoad};
    for (const auto& spec : spy::kOps) {
        CAPTURE(std::string(spec.name));
        CHECK(spec.needs_game == (no_game.count(spec.name) == 0));
        if (spec.name.starts_with("events.") || spec.name.starts_with("game.")) CHECK_FALSE(spec.batchable);
    }
}

TEST_CASE("the events table is the plan's v1 list, with its filter and loading rules")
{
    const std::vector<std::string> names = {"death", "dying", "quest_stage", "quest_start", "quest_stop", "mod_event", "channel",
                                            "hit", "equip", "unequip", "container_changed", "cell_attach", "cell_detach", "activate",
                                            "player_location", "menu_open", "menu_close", "game_loaded", "game_saved", "new_game"};
    REQUIRE(spy::kEvents.size() == names.size());
    const std::set<std::string> while_loading = {"mod_event", "channel", "menu_open", "menu_close", "game_loaded", "game_saved", "new_game"};
    for (std::size_t i = 0; i < names.size(); ++i) {
        const auto& e = spy::kEvents[i];
        CAPTURE(names[i]);
        CHECK(e.name == names[i]);
        CHECK(spy::find_event(names[i]) == &e);
        CHECK(e.deliver_while_loading == (while_loading.count(names[i]) == 1));
        for (const auto f : e.filter_fields) {
            bool is_field = false;
            for (const auto& d : e.fields) is_field = is_field || (d.name == f && d.type != spy::EventFieldType::boolean && d.type != spy::EventFieldType::floating);
            CHECK_MESSAGE(is_field, "filter field " << f << " is not a form, int or string field");
        }
        const bool needs_filter = names[i] == "hit" || names[i] == "container_changed";
        CHECK(e.filter_required_any_of.empty() == !needs_filter);
    }
    CHECK(spy::find_event(spy::event_names::kDeath)->fields[1].nullable);         // killer may be null
    CHECK_FALSE(spy::find_event(spy::event_names::kDeath)->fields[0].nullable);   // victim never
    CHECK(spy::find_event("Death") == nullptr);                                    // names are exact
}
```
Add to the `#include` list at the top: nothing new (`<set>` is already included). In "every ini key in the table is parsed by parse_config", add after the `enable_console` default check:
```cpp
    CHECK(defaults.trace_timing == spy::config_defaults::kTraceTiming);
    CHECK(defaults.console_echo_errors == spy::config_defaults::kConsoleEchoErrors);
```

`core/tests/test_config.cpp`: in "defaults when empty", add `CHECK_FALSE(c.trace_timing);` and `CHECK(c.console_echo_errors);`; in "config_to_json carries every field under its ini name", add `CHECK(j["trace_timing"] == false);` and `CHECK(j["console_echo_errors"] == true);`; append:
```cpp
TEST_CASE("trace_timing and console_echo_errors parse as booleans")
{
    auto c = spy::parse_config("trace_timing = true\nconsole_echo_errors = 0\n");
    CHECK(c.trace_timing);
    CHECK_FALSE(c.console_echo_errors);
    CHECK(c.ignored_keys.empty());
    CHECK_THROWS_AS(spy::parse_config("trace_timing = yes\n"), std::invalid_argument);
    CHECK_THROWS_AS(spy::parse_config("console_echo_errors = 2\n"), std::invalid_argument);
}
```

`core/tests/test_requests.cpp`: add `#include <regex>` and `#include <vector>` at the top, and append:
```cpp
TEST_CASE("is_valid_save_name agrees with the generated save-name pattern")
{
    const std::regex pattern{std::string(spy::kSaveNamePattern)};
    const std::vector<std::string> samples = {"", "a", "skython_t41", "A_Z_0_9", std::string(64, 'x'), std::string(65, 'x'),
                                              "has space", "dot.ess", "../up", "tab\t", "\xC3\xBCmlaut", "semi;colon", "-dash",
                                              std::string(1, '\0'), "trailing\n"};
    for (const auto& s : samples) {
        CAPTURE(s);
        CHECK(spy::is_valid_save_name(s) == std::regex_match(s, pattern));
    }
    CHECK(spy::is_valid_save_name(std::string(64, 'x')));
    CHECK_FALSE(spy::is_valid_save_name(std::string(65, 'x')));
}

TEST_CASE("events.* and game.save/game.load arguments are checked from their table entries")
{
    using spy::ErrorCode;
    const json form = {{"kind", "form"}, {"form_id", 20}};
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}})));
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}, {"filter", nullptr}})));  // null = absent
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "hit"}, {"filter", {{"target", json::array({form})}}}, {"max_queue", 65536}})));
    CHECK_NOTHROW(spy::parse_request(request("events.subscribe", {{"event", "death"}, {"max_queue", 1}})));
    CHECK(code_of_request(request("events.subscribe", json::object())) == ErrorCode::bad_request);
    CHECK(code_of_request(request("events.subscribe", {{"event", "Death"}})) == ErrorCode::bad_request);  // exact names only
    CHECK(code_of_request(request("events.subscribe", {{"event", "death"}, {"filter", "victim"}})) == ErrorCode::bad_request);
    for (const json& bad_queue : {json(0), json(65537), json(1.5), json(true), json("16"), json(-1)})
        CHECK(code_of_request(request("events.subscribe", {{"event", "death"}, {"max_queue", bad_queue}})) == ErrorCode::bad_request);
    try {
        spy::parse_request(request("events.subscribe", {{"event", "no_such_event"}}));
        FAIL("expected ProtocolError");
    } catch (const spy::ProtocolError& e) {
        CHECK(std::string(e.what()).find("unknown event 'no_such_event'") != std::string::npos);
    }

    CHECK_NOTHROW(spy::parse_request(request("events.unsubscribe", {{"sub", 0}})));
    CHECK_NOTHROW(spy::parse_request(request("events.unsubscribe", {{"sub", 18446744073709551615ULL}})));
    for (const json& bad_sub : {json(-1), json(1.0), json("7"), json(nullptr)})
        CHECK(code_of_request(request("events.unsubscribe", {{"sub", bad_sub}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("events.unsubscribe", json::object())) == ErrorCode::bad_request);

    CHECK_NOTHROW(spy::parse_request(request("game.save", {{"name", "skython_t33"}, {"timeout_ms", 60000}})));
    CHECK_NOTHROW(spy::parse_request(request("game.load", {{"save", "skython_t33"}})));
    CHECK(code_of_request(request("game.save", json::object())) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", 5}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", "a b"}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.save", {{"name", "ok"}, {"timeout_ms", 0}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.load", {{"save", "../x"}})) == ErrorCode::bad_request);
    CHECK(code_of_request(request("game.load", {{"name", "ok"}})) == ErrorCode::bad_request);  // the load argument is "save"
}
```

`core/tests/test_fixtures.cpp`: add `#include <algorithm>` and `#include <set>`, and append:
```cpp
namespace {
// A data value against its events-table field: form references parse, nullable fields may be null.
bool field_ok(const spy::EventField& f, const json& v)
{
    if (v.is_null()) return f.nullable;
    switch (f.type) {
    case spy::EventFieldType::form:
        try {
            spy::form_ref_from_json(v);
            return true;
        } catch (const spy::ProtocolError&) {
            return false;
        }
    case spy::EventFieldType::integer: return v.is_number_integer();
    case spy::EventFieldType::floating: return v.is_number();
    case spy::EventFieldType::boolean: return v.is_boolean();
    case spy::EventFieldType::string: return v.is_string();
    }
    return false;
}
}

TEST_CASE("events fixture: requests parse, and results, frames and bad filters use the events table")
{
    const auto fx = load("events.json");
    for (const auto& c : fx["subscribe"]) {
        CAPTURE(c.dump());
        const auto r = spy::parse_request(c["request"]);
        CHECK(r.op == spy::ops::kEventsSubscribe);
        const auto* spec = spy::find_event(r.args["event"].get<std::string>());
        REQUIRE(spec != nullptr);
        CHECK(c["result"]["event"] == r.args["event"]);
        CHECK(spy::is_uint(c["result"]["sub"], UINT64_MAX));
        const std::size_t expected_queue = r.args.contains("max_queue") ? r.args["max_queue"].get<std::size_t>() : spy::kDefaultEventQueue;
        CHECK(c["result"]["max_queue"].get<std::size_t>() == expected_queue);
        if (r.args.contains("filter") && r.args["filter"].is_object())
            for (const auto& [field, values] : r.args["filter"].items()) {
                CHECK(std::find(spec->filter_fields.begin(), spec->filter_fields.end(), field) != spec->filter_fields.end());
                CHECK((values.is_array() && !values.empty() && values.size() <= spy::kMaxFilterValues));
            }
    }
    for (const auto& c : fx["unsubscribe"]) {
        CAPTURE(c.dump());
        CHECK(spy::parse_request(c["request"]).op == spy::ops::kEventsUnsubscribe);
        CHECK((c.contains("result") ? c["result"].is_null() : spy::error_code_from_string(c["error"].get<std::string>()).has_value()));
    }
    for (const auto& f : fx["frames"]) {
        CAPTURE(f.dump());
        CHECK(f["type"] == "event");
        const auto* spec = spy::find_event(f["event"].get<std::string>());
        REQUIRE(spec != nullptr);
        std::set<std::string> keys;
        for (const auto& [k, v] : f.items()) keys.insert(k);
        for (const char* k : {"type", "event", "sub", "seq", "epoch", "data"}) CHECK(keys.erase(k) == 1);
        for (const char* k : {"dropped", "suppressed"})
            if (keys.erase(k) == 1) CHECK(f[k].get<std::uint64_t>() > 0);  // present only when non-zero
        CHECK(keys.empty());
        CHECK(f["seq"].get<std::uint64_t>() >= 1);
        REQUIRE(f["data"].is_object());
        CHECK(f["data"].size() == spec->fields.size());
        for (const auto& field : spec->fields) {
            CAPTURE(std::string(field.name));
            REQUIRE(f["data"].contains(std::string(field.name)));
            CHECK(field_ok(field, f["data"][std::string(field.name)]));
        }
    }
    for (const auto& c : fx["invalid_filters"]) {
        CAPTURE(c.dump());
        CHECK(spy::find_event(c["event"].get<std::string>()) != nullptr);
        CHECK(spy::error_code_from_string(c["error"].get<std::string>()).has_value());
        CHECK_FALSE(c["message"].get<std::string>().empty());
    }
}
```

`core/tests/test_fake_backend.cpp`, in "fixture replay while a save loads": replace the line `if (m["op"] == "game.state") { CHECK(code_of(r) == "ok"); continue; }` with
```cpp
        if (!spy::find_op(m["op"].get<std::string>())->needs_game) continue;  // answered with no game (game.state, events.*, game.load)
```

- [ ] **Step 9: Run the core tests to verify they fail**

Run: `cmake --build core/build -j 2>&1 | grep -m3 "error:"`
Expected: compile errors such as `'is_valid_save_name' is not a member of 'spy'` and `'struct spy::Config' has no member named 'trace_timing'`.

- [ ] **Step 10: Implement the request checks and the two config keys**

`core/include/spy/requests.h`, after `known_ops()`:
```cpp
// A save name game.save and game.load accept: 1 to 64 bytes of A-Z, a-z, 0-9 and '_' (the generated kSaveNamePattern;
// core/tests/test_requests.cpp checks that the two agree).
bool is_valid_save_name(std::string_view s);
```

`core/src/requests.cpp`: add `#include <algorithm>` and `#include <stdexcept>` after the existing includes. Inside the anonymous namespace, after `parse_object_ref`, add:
```cpp
// The ops added in protocol 1.1 are checked from their table entries, argument by argument. A filter only has to be
// an object (or null, meaning absent) here: its field names, value types and required fields are EventHub's rules
// (parse_event_filter, core/src/events.cpp), applied when the subscription is made.
void check_typed_args(const OpSpec& spec, const json& args)
{
    for (const auto& a : spec.args) {
        const std::string name(a.name);
        if (!args.contains(name)) {
            if (a.required) bad("missing field '" + name + "'");
            continue;
        }
        const json& v = args[name];
        switch (a.type) {
        case ArgType::event_name:
            if (!v.is_string()) bad("'" + name + "' must be a string naming an event");
            if (find_event(v.get_ref<const std::string&>()) == nullptr) bad("unknown event '" + clip_text(v.get_ref<const std::string&>(), 64) + "'");
            break;
        case ArgType::event_filter:
            if (!v.is_null() && !v.is_object()) bad("'" + name + "' must be an object mapping filter fields to lists of values");
            break;
        case ArgType::queue_size:
            if (!is_uint(v, kMaxEventQueue) || v.get<std::uint64_t>() < 1)
                bad("'" + name + "' must be an integer from 1 to " + std::to_string(kMaxEventQueue));
            break;
        case ArgType::u64:
            if (!is_uint(v, UINT64_MAX)) bad("'" + name + "' must be a non-negative integer");
            break;
        case ArgType::save_name:
            if (!v.is_string() || !is_valid_save_name(v.get_ref<const std::string&>()))
                bad("'" + name + "' must be 1 to 64 letters, digits or underscores");
            break;
        case ArgType::timeout_ms:
            opt_timeout(args);
            break;
        default:
            throw std::logic_error("check_typed_args: the type of argument '" + name + "' is not handled");
        }
    }
}
```
In `validate_op_args`, add a branch before the `form.resolve` one:
```cpp
    else if (op == ops::kEventsSubscribe || op == ops::kEventsUnsubscribe || op == ops::kGameSave || op == ops::kGameLoad)
        check_typed_args(*find_op(op), args);
```
After `known_ops()`, add:
```cpp
bool is_valid_save_name(std::string_view s)
{
    constexpr std::size_t kMaxSaveNameBytes = 64;  // the {1,64} of kSaveNamePattern
    if (s.empty() || s.size() > kMaxSaveNameBytes) return false;
    return std::all_of(s.begin(), s.end(), [](char c) {
        return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
    });
}
```

`core/include/spy/config.h`, after `enable_console`:
```cpp
    bool trace_timing = config_defaults::kTraceTiming;               // dev only: GameBackend logs per-request timestamps (Task 38)
    bool console_echo_errors = config_defaults::kConsoleEchoErrors;  // a player script failure echoes one console line (Task 43)
```
`core/src/config.cpp`: in `parse_config`, after the `enable_console` branch:
```cpp
        else if (key == "trace_timing") c.trace_timing = to_bool(key, val);
        else if (key == "console_echo_errors") c.console_echo_errors = to_bool(key, val);
```
and in `config_to_json`, after `{"enable_console", c.enable_console},`:
```cpp
            {"trace_timing", c.trace_timing},
            {"console_echo_errors", c.console_echo_errors},
```

`plugin/config/Skython.ini` and `plugin/config/Skython.dev.ini`: append to both:
```ini
[Diagnostics]
; Development only: one timing line per request in the plugin log.
trace_timing = false
; A failing player script also prints one short line in the in-game console.
console_echo_errors = true
```

- [ ] **Step 11: Run the core tests**

Run: `cmake --build core/build -j 2>&1 | grep -c "warning:" ; ctest --test-dir core/build --output-on-failure 2>&1 | tail -3`
Expected: `0`, then `100% tests passed, 0 tests failed out of 1`.

- [ ] **Step 12: Update the Python pins and the Python fake**

`python/tests/test_generated.py`, in `test_v1_wire_contract_is_pinned`: `assert g.PROTOCOL_VERSION == (1, 1)`; replace the `tuple(g.OPS)`, `needs_game` and `CONFIG_KEYS` assertions with:
```python
    assert tuple(g.OPS) == ("game.state", "form.resolve", "papyrus.call_static", "papyrus.call_method",
                            "papyrus.get_property", "papyrus.set_property", "papyrus.batch",
                            "events.subscribe", "events.unsubscribe", "game.save", "game.load")
    assert [name for name, spec in g.OPS.items() if not spec["needs_game"]] == ["game.state", "events.subscribe", "events.unsubscribe", "game.load"]
    assert tuple(g.CONFIG_KEYS) == ("python_enabled", "scripts_enabled", "bridge_enabled", "port", "fixed_token", "session_file",
                                    "default_timeout_ms", "max_inflight_per_connection", "max_inflight_total", "enable_console",
                                    "trace_timing", "console_echo_errors")
    assert g.CONFIG_KEYS["trace_timing"]["default"] is False and g.CONFIG_KEYS["console_echo_errors"]["default"] is True
    assert (g.DEFAULT_EVENT_QUEUE, g.MAX_EVENT_QUEUE, g.MAX_SUBSCRIPTIONS, g.MAX_FILTER_VALUES) == (1024, 65536, 1024, 128)
    assert (g.MAX_EVENTS_PER_NEXT, g.MAX_CHANNEL_PAYLOAD_BYTES, g.MAX_CHANNEL_BYTES) == (256, 65536, 256)
    assert g.SAVE_NAME_PATTERN == r"^[A-Za-z0-9_]{1,64}$"
    assert len(g.EVENTS) == 20 and g.EVENTS["hit"]["filter_required_any_of"] == ("target", "aggressor")
    assert {n for n, e in g.EVENTS.items() if e["deliver_while_loading"]} == {
        "mod_event", "channel", "menu_open", "menu_close", "game_loaded", "game_saved", "new_game"}
```

`python/tests/test_game.py` replays every valid request fixture through a `Game` method and checks `set(CALLERS) == set(g.OPS)`; the `Game` methods for the new ops (`on`, `save`, `load`) arrive in Task 36. Add above `def replay_handler`:
```python
# Protocol 1.1 ops whose Game methods (Game.on, Game.save, Game.load) arrive in Task 36, which adds them to CALLERS
# and deletes this set.
PENDING_OPS = {"events.subscribe", "events.unsubscribe", "game.save", "game.load"}
```
change the replay's parametrisation to `@pytest.mark.parametrize("fx", [r for r in REQUESTS["valid"] if r["op"] not in PENDING_OPS], ids=lambda r: f"{r['id']}-{r['op']}")`, and in `test_the_game_speaks_exactly_the_ops_in_the_table` change the first assertion to `assert set(CALLERS) == set(g.OPS) - PENDING_OPS`.

`python/tests/test_inprocess.py` line 28: `assert t.welcome["protocol"] == [1, 1] and t.welcome["epoch"] == 4`.
`pyglue/tests/test_module.py` line 17: `assert _skython.PROTOCOL == (1, 1)`.
`livetest/test_m1_bridge.py` line 143: `assert data["protocol"] == [1, 1] and data["plugin_version"]` (the bridge writes the package's protocol version into the session file; this live test runs in Part E against a rebuilt deploy).

`python/tests/test_fixtures.py`: add `import json` at the top and `EVENTS_FX = load_fixture("events.json")` beside the other fixture loads, plus `from skython import _protocol_generated as g` and `from skython.values import Form` (add `Form` to the existing `skython.values` import). Append:
```python
@pytest.mark.parametrize("frame", EVENTS_FX["frames"], ids=lambda f: f"{f['event']}-{f['seq']}")
def test_event_frames_have_exactly_the_tables_fields_and_decode(frame):
    spec = g.EVENTS[frame["event"]]
    assert frame["type"] == "event"
    assert set(frame["data"]) == {name for name, _t, _n in spec["fields"]}
    for name, typ, nullable in spec["fields"]:
        v = frame["data"][name]
        if v is None:
            assert nullable, name
        elif typ == "form":
            assert isinstance(from_wire(v), Form), name
        else:
            assert isinstance(v, {"int": int, "float": (int, float), "bool": bool, "string": str}[typ]), name
    assert set(frame) - {"type", "event", "sub", "seq", "epoch", "data"} <= {"dropped", "suppressed"}
    assert all(frame[k] > 0 for k in ("dropped", "suppressed") if k in frame)


@pytest.mark.parametrize("case", EVENTS_FX["subscribe"], ids=lambda c: str(c["request"]["id"]))
def test_subscribe_fixtures_name_only_filter_fields(case):
    args = case["request"]["args"]
    assert set(args.get("filter") or {}) <= set(g.EVENTS[args["event"]]["filter_fields"])
    assert case["result"]["max_queue"] == args.get("max_queue", g.DEFAULT_EVENT_QUEUE)


NEW_OPS = {"events.subscribe", "events.unsubscribe", "game.save", "game.load"}


@pytest.mark.parametrize("case", [c for c in REQUESTS["invalid"] if c["message"].get("op") in NEW_OPS], ids=lambda c: str(c["message"]["id"]))
def test_the_fake_refuses_bad_arguments_of_the_new_ops_like_core(fake, case):
    resp = json.loads(fake.call(json.dumps(case["message"]), 5.0))
    assert resp["ok"] is False and resp["error"]["code"] == case["error"], resp


@pytest.mark.parametrize("req", [r for r in REQUESTS["valid"] if r["op"] in NEW_OPS], ids=lambda r: r["op"])
def test_the_fake_accepts_valid_requests_of_the_new_ops(fake, req):
    resp = json.loads(fake.call(json.dumps(req), 5.0))
    assert resp["ok"] is True or resp["error"]["code"] != "bad_request", resp
```

`python/tests/fake_skython.py`: add `import re` to the imports, and in `_op_problem`, insert before the `names = [...]` line:
```python
        for name, typ, required in _g.OPS[op]["args"]:  # the protocol 1.1 argument types, as core's check_typed_args
            if typ not in ("event_name", "event_filter", "queue_size", "u64", "save_name"):
                continue
            if name not in args:
                if required:
                    return f"missing field {name!r}"
                continue
            v = args[name]
            if typ == "event_name" and (not isinstance(v, str) or v not in _g.EVENTS):
                return f"unknown event {v!r}"
            if typ == "event_filter" and v is not None and not isinstance(v, dict):
                return f"{name!r} must be an object mapping filter fields to lists of values"
            if typ == "queue_size" and (type(v) is not int or not 1 <= v <= _g.MAX_EVENT_QUEUE):
                return f"{name!r} must be an integer from 1 to {_g.MAX_EVENT_QUEUE}"
            if typ == "u64" and (type(v) is not int or not 0 <= v <= _U64_MAX):
                return f"{name!r} must be a non-negative integer"
            if typ == "save_name" and (not isinstance(v, str) or re.fullmatch(_g.SAVE_NAME_PATTERN, v) is None):
                return f"{name!r} must be 1 to 64 letters, digits or underscores"
```
Also update the module docstring's first bullet list with one line: `- the protocol 1.1 ops' arguments are checked like core's check_typed_args (event names, queue sizes, u64, save names; a filter only has to be an object or null);`.

- [ ] **Step 13: Run the Python suites and the module tests**

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -2 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -2`
Expected: both end `... passed` (no failures).

Run: `cmake --build pyglue/build -j 2>&1 | tail -1 && uv run --no-project --python 3.14 --with pytest python -m pytest pyglue/tests -q 2>&1 | tail -1`
Expected: `[100%] Built target _skython`, then `... passed`.

- [ ] **Step 14: Rewrite PROTOCOL.md's events section and the save/load rules**

`protocol/PROTOCOL.md`:

(a) Handshake: replace `{"type":"hello","protocol":[1,0],...` and `{"type":"welcome","protocol":[1,0],...` with `[1,1]`, and add after the server line: `A client of an older minor (for example \`[1,0]\`) still connects; it simply does not use what the newer minor added (protocol 1.1 added the events ops, the events table and \`game.save\`/\`game.load\`).`

(b) Replace the whole section `## Events (M2)` (the heading and its one line) with:
````markdown
## Events
The game pushes events; each one is delivered to every subscription that wants it. In-process, each `skython.Game` owns one event consumer; on the dev bridge each connection owns one. **The consumer is never named in a request:** it travels beside the request text (`_skython.submit(text, consumer)`), and the bridge forwards request text unchanged, so a client cannot subscribe or unsubscribe for another consumer. An `events.*` request submitted without a consumer is `bad_request` ("no event consumer").

`events.subscribe` answers `{"sub", "event", "max_queue"}`. `events.unsubscribe` of a sub that does not exist, or that another consumer owns, is `not_found` (the two cases look the same). Subscriptions of all consumers together are limited by `max_subscriptions`; beyond it `events.subscribe` is `busy`. Both ops are answered with no game loaded.

An event frame (the dev bridge writes it as one frame; in-process, `_skython.next_events` returns a JSON list of these objects, at most `max_events_per_next` per call, in push order across the consumer's subscriptions):
`{"type":"event","event":"death","sub":7,"seq":12,"epoch":3,"data":{"victim":{"kind":"form",...},"killer":null},"dropped":4,"suppressed":2}`
- `seq` counts per subscription, from 1, every event accepted for that subscription (later delivered or dropped), so gaps in `seq` equal drops.
- `epoch` is the game's load counter when the event was pushed (the `epoch` of `game.state`).
- `dropped` (present only when non-zero): this subscription's events discarded by overflow since its previous delivered event.
- `suppressed` (present only when non-zero): this subscription's events not queued because a load was in progress, since its previous delivered event.
- `data` has exactly the fields the generated events table lists for that event. Form fields are form references (see Values), or `null` where the table marks them; events never carry engine pointers.

**Queues.** Each subscription has its own queue of `max_queue` events (default `default_event_queue`, at most `max_event_queue`). A full queue drops its oldest event and counts it in `dropped`. The game never waits for a reader, and a slow subscription never delays another one.

**Filters.** `filter` maps field names from the event's filter fields to non-empty arrays of at most `max_filter_values` values. An event matches when every listed field's value is in its array (AND across fields, OR within one); absent or `null` means every event. Form fields: each value is a form reference carrying `form_id` (a form key alone is `bad_request`: resolve the form first), matched by form id; a `null` event field never matches. Int fields: Papyrus Int values. String fields: strings, matched ASCII-case-insensitively (the engine's string pool can change the case). An unknown field, an empty array or a value of the wrong type is `bad_request`. Where the table names fields under "Filter required", the filter must name at least one of them, else `bad_request` (for example "hit needs a filter on target or aggressor").

**Loading.** While a load is in progress (a save load pending, or the Loading Menu open), an event whose table entry is not delivered while loading is not queued; each subscription that would have taken it counts it in `suppressed`. Menu events, `mod_event`, `channel` and the lifecycle events (`game_loaded`, `game_saved`, `new_game`) are delivered. `game_loaded` is raised when a loaded save or a new game becomes interactive: it marks the end of a load. Subscriptions survive a load.

**Channel events.** The Papyrus native `Skython.Send(channel, payload)` raises `channel`. A payload longer than `max_channel_payload_bytes` is cut at a UTF-8 boundary and the event carries `truncated: true`; a channel name longer than `max_channel_bytes` is cut the same way.
````

(c) Rename `## Ops (M1)` to `## Ops` and append these bullets to its rule list:
```markdown
- `game.save` never overwrites: a save that already has that name is `bad_request` with `detail.reason` `"save_exists"`. It answers once the game reports the save; no report within the timeout is `timeout`.
- `game.load` is answered without a loaded game, so it works at the main menu; while the game is starting or already loading it is `not_ready`. A missing save is `not_found`; the engine refusing to load it is `internal`. It answers once the load has started. The load is a world reset, so every call in flight that needs a game ends `not_ready` (see above).
- Save names match the save-name pattern in the generated tables (letters, digits and underscores; no path, no extension).
```

- [ ] **Step 15: Run every check in this task once more**

Run: `python3 tools/gen_protocol.py --check && uv run --no-project --python 3.12 --with pytest pytest tools/test_gen_protocol.py -q 2>&1 | tail -1`
Expected: `gen_protocol: 3 generated files are up to date`, then `... passed` (this includes the PROTOCOL.md check that no limit's number is repeated in the text).

Run: `cmake --build core/build -j && ctest --test-dir core/build --output-on-failure 2>&1 | tail -1`
Expected: `100% tests passed, 0 tests failed out of 1`.

Run: `cd python && uv run --managed-python --python 3.12 pytest -q 2>&1 | tail -1 && uv run --managed-python --python 3.14 pytest -q 2>&1 | tail -1`
Expected: two `... passed` lines.

- [ ] **Step 16: Commit**

```bash
git add protocol/protocol.json protocol/PROTOCOL.md protocol/PROTOCOL.generated.md protocol/v1/fixtures/events.json \
        protocol/v1/fixtures/requests.json protocol/v1/fixtures/responses.json protocol/v1/fixtures/hello.json \
        tools/gen_protocol.py tools/test_gen_protocol.py core/include/spy/protocol_generated.h python/src/skython/_protocol_generated.py \
        core/include/spy/requests.h core/src/requests.cpp core/include/spy/config.h core/src/config.cpp \
        core/tests/test_generated.cpp core/tests/test_requests.cpp core/tests/test_config.cpp core/tests/test_fixtures.cpp \
        core/tests/test_fake_backend.cpp plugin/config/Skython.ini plugin/config/Skython.dev.ini \
        python/tests/test_generated.py python/tests/test_fixtures.py python/tests/test_game.py python/tests/test_inprocess.py \
        python/tests/fake_skython.py \
        pyglue/tests/test_module.py livetest/test_m1_bridge.py
git commit -m "feat(protocol): protocol 1.1 with event ops, the events table, game.save/game.load and two config keys"
```
