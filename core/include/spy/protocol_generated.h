// GENERATED FILE - DO NOT EDIT.
// Source: protocol/protocol.json. Regenerate with: python3 tools/gen_protocol.py
// The drift check (python3 tools/gen_protocol.py --check) fails if this file differs from what the generator writes.
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

namespace spy {
inline constexpr int kProtocolMajor = 1;
inline constexpr int kProtocolMinor = 1;

// ---- limits
// max_frame_bytes: Dev bridge: largest frame payload either side accepts. A larger header closes the connection.
inline constexpr std::uint32_t kMaxFrameBytes = 16777216u;
// pre_auth_frame_bytes: Dev bridge: frame cap until the hello is accepted; a valid hello is well under 1 KiB.
inline constexpr std::uint32_t kPreAuthFrameBytes = 65536u;
// max_name_bytes: Longest script, function, property or editor_id string.
inline constexpr std::size_t kMaxNameBytes = 256u;
// max_batch_calls: Most calls in one papyrus.batch (the least is 1).
inline constexpr std::size_t kMaxBatchCalls = 256u;
// min_timeout_ms: Smallest timeout_ms.
inline constexpr std::uint32_t kMinTimeoutMs = 1u;
// max_timeout_ms: Largest timeout_ms; also the largest default_timeout_ms.
inline constexpr std::uint32_t kMaxTimeoutMs = 3600000u;
// max_error_message_bytes: Error messages are clipped to this many bytes, so a response can never exceed the frame limit.
inline constexpr std::size_t kMaxErrorMessageBytes = 1024u;
// max_client_name_bytes: The hello's client name is truncated to this many bytes.
inline constexpr std::size_t kMaxClientNameBytes = 64u;
// max_connections: Dev bridge: simultaneous client connections; further ones are closed at accept.
inline constexpr std::size_t kMaxConnections = 32u;
// hello_timeout_ms: Dev bridge: a connection that has not sent an accepted hello by then is closed.
inline constexpr std::uint32_t kHelloTimeoutMs = 5000u;
// max_json_depth: Deepest array/object nesting accepted in a request. In-process the request fails with bad_request; on the dev bridge the connection is closed.
inline constexpr std::size_t kMaxJsonDepth = 64u;
// max_queued_bytes_per_connection: Dev bridge: outbound bytes queued for one connection before it is treated as a stuck reader and closed.
inline constexpr std::size_t kMaxQueuedBytesPerConnection = 67108864u;
// token_length: Dev bridge: tokens are 128 random bits as lowercase hex.
inline constexpr std::size_t kTokenLength = 32u;
// int_min: Smallest Papyrus Int. Out of range is type_mismatch, never truncation.
inline constexpr std::int32_t kIntMin = static_cast<std::int32_t>(-2147483648LL);
// int_max: Largest Papyrus Int; also the largest alias_id and the largest hello protocol number.
inline constexpr std::int32_t kIntMax = static_cast<std::int32_t>(2147483647LL);
// float_max_text: Largest Papyrus Float magnitude, as the shortest text that round-trips FLT_MAX; a larger magnitude is type_mismatch.
inline constexpr double kFloatMaxText = 3.4028235e+38;
// max_array_items: Most items in a Papyrus array (the VM's own limit); a longer array argument is type_mismatch.
inline constexpr std::size_t kMaxArrayItems = 128u;
// default_event_queue: Queue length of a subscription that sends no max_queue. A full queue drops its oldest event; the next delivered event carries the count in dropped.
inline constexpr std::size_t kDefaultEventQueue = 1024u;
// max_event_queue: Largest max_queue of one subscription.
inline constexpr std::size_t kMaxEventQueue = 65536u;
// max_subscriptions: Subscriptions of all consumers together (in-process scripts and bridge connections); beyond it events.subscribe is busy.
inline constexpr std::size_t kMaxSubscriptions = 1024u;
// max_subscriptions_per_consumer: Subscriptions of one consumer (one Game, or one bridge connection); beyond it events.subscribe is busy, so one client cannot take every subscription.
inline constexpr std::size_t kMaxSubscriptionsPerConsumer = 256u;
// max_queued_event_bytes_per_consumer: Event bytes one consumer may hold queued (each event counts its data's compact JSON length plus 96). Over it the oldest events of the subscription being pushed to are dropped (counted in dropped) until the consumer is back under it.
inline constexpr std::size_t kMaxQueuedEventBytesPerConsumer = 16777216u;
// max_filter_values: Most values in one field of an event filter.
inline constexpr std::size_t kMaxFilterValues = 128u;
// max_events_per_next: Most events one _skython.next_events call returns.
inline constexpr std::size_t kMaxEventsPerNext = 256u;
// max_channel_payload_bytes: A Skython.Send payload longer than this is cut at a UTF-8 boundary and its channel event carries truncated: true.
inline constexpr std::size_t kMaxChannelPayloadBytes = 65536u;
// max_channel_bytes: Longest channel name of a channel event. A Skython.Send whose channel is empty or longer than this is dropped and a warning is logged once; a channel is never cut (a cut name would be a different channel).
inline constexpr std::size_t kMaxChannelBytes = 256u;

// ---- error codes (the order is the enum order in protocol.json)
enum class ErrorCode {
    not_ready,
    busy,
    bad_request,
    unknown_function,
    type_mismatch,
    stale_reference,
    not_found,
    timeout,
    vm_error,
    unsupported,
    internal,
    unauthorized,
    protocol_mismatch,
};
struct ErrorCodeName { ErrorCode code; std::string_view name; };
inline constexpr std::array<ErrorCodeName, 13> kErrorCodeNames{{
    {ErrorCode::not_ready, "not_ready"},
    {ErrorCode::busy, "busy"},
    {ErrorCode::bad_request, "bad_request"},
    {ErrorCode::unknown_function, "unknown_function"},
    {ErrorCode::type_mismatch, "type_mismatch"},
    {ErrorCode::stale_reference, "stale_reference"},
    {ErrorCode::not_found, "not_found"},
    {ErrorCode::timeout, "timeout"},
    {ErrorCode::vm_error, "vm_error"},
    {ErrorCode::unsupported, "unsupported"},
    {ErrorCode::internal, "internal"},
    {ErrorCode::unauthorized, "unauthorized"},
    {ErrorCode::protocol_mismatch, "protocol_mismatch"},
}};

// message types
inline constexpr std::array<std::string_view, 6> kMessageTypes{{
    "hello",
    "welcome",
    "handshake_error",
    "request",
    "response",
    "event",
}};
// detail.reason values of a not_ready error
inline constexpr std::array<std::string_view, 5> kNotReadyReasons{{
    "starting",
    "no_game",
    "loading",
    "paused",
    "saving",
}};
// alias_type values
inline constexpr std::array<std::string_view, 2> kAliasTypes{{
    "ReferenceAlias",
    "LocationAlias",
}};
inline constexpr std::string_view kDefaultAliasType = "ReferenceAlias";
// array element types
inline constexpr std::array<std::string_view, 6> kArrayElemTypes{{
    "Bool",
    "Int",
    "Float",
    "String",
    "Form",
    "Alias",
}};

// ---- form ids and form keys
namespace form_ids {
inline constexpr std::uint32_t kMaxLocalId = 16777215u;
inline constexpr std::uint32_t kMaxLightLocalId = 4095u;
inline constexpr std::uint32_t kRuntimePrefix = 255u;
inline constexpr std::uint32_t kLightPrefix = 254u;
inline constexpr std::size_t kMaxKeyHexDigits = 8u;
inline constexpr int kCanonicalHexDigits = 6;
inline constexpr std::string_view kLightExtension = ".esl";
// plugin file extensions, lower case
inline constexpr std::array<std::string_view, 3> kPluginExtensions{{
    ".esm",
    ".esp",
    ".esl",
}};
}

// ---- ops
enum class ArgType { name, value, value_list, ref, form_key, u32, timeout_ms, boolean, call_list, event_name, event_filter, queue_size, u64, save_name };
struct ArgSpec { std::string_view name; ArgType type; bool required; };
struct OpSpec
{
    std::string_view name;
    bool batchable;
    bool needs_game;  // false: answered without a loaded game (no not_ready check)
    std::span<const ArgSpec> args;
    std::span<const std::string_view> exactly_one_of;  // empty, or the arguments of which exactly one must be present
};
namespace ops {
inline constexpr std::string_view kGameState = "game.state";
inline constexpr std::string_view kFormResolve = "form.resolve";
inline constexpr std::string_view kPapyrusCallStatic = "papyrus.call_static";
inline constexpr std::string_view kPapyrusCallMethod = "papyrus.call_method";
inline constexpr std::string_view kPapyrusGetProperty = "papyrus.get_property";
inline constexpr std::string_view kPapyrusSetProperty = "papyrus.set_property";
inline constexpr std::string_view kPapyrusBatch = "papyrus.batch";
inline constexpr std::string_view kEventsSubscribe = "events.subscribe";
inline constexpr std::string_view kEventsUnsubscribe = "events.unsubscribe";
inline constexpr std::string_view kGameSave = "game.save";
inline constexpr std::string_view kGameLoad = "game.load";
}
inline constexpr std::array<ArgSpec, 3> kArgs_form_resolve{{
    {"form_key", ArgType::form_key, false},
    {"form_id", ArgType::u32, false},
    {"editor_id", ArgType::name, false},
}};
inline constexpr std::array<std::string_view, 3> kOneOf_form_resolve{{
    "form_key",
    "form_id",
    "editor_id",
}};
inline constexpr std::array<ArgSpec, 5> kArgs_papyrus_call_static{{
    {"script", ArgType::name, true},
    {"function", ArgType::name, true},
    {"args", ArgType::value_list, false},
    {"timeout_ms", ArgType::timeout_ms, false},
    {"require_unpaused", ArgType::boolean, false},
}};
inline constexpr std::array<ArgSpec, 6> kArgs_papyrus_call_method{{
    {"object", ArgType::ref, true},
    {"script", ArgType::name, true},
    {"function", ArgType::name, true},
    {"args", ArgType::value_list, false},
    {"timeout_ms", ArgType::timeout_ms, false},
    {"require_unpaused", ArgType::boolean, false},
}};
inline constexpr std::array<ArgSpec, 3> kArgs_papyrus_get_property{{
    {"object", ArgType::ref, true},
    {"script", ArgType::name, true},
    {"property", ArgType::name, true},
}};
inline constexpr std::array<ArgSpec, 4> kArgs_papyrus_set_property{{
    {"object", ArgType::ref, true},
    {"script", ArgType::name, true},
    {"property", ArgType::name, true},
    {"value", ArgType::value, true},
}};
inline constexpr std::array<ArgSpec, 3> kArgs_papyrus_batch{{
    {"calls", ArgType::call_list, true},
    {"stop_on_error", ArgType::boolean, false},
    {"timeout_ms", ArgType::timeout_ms, false},
}};
inline constexpr std::array<ArgSpec, 3> kArgs_events_subscribe{{
    {"event", ArgType::event_name, true},
    {"filter", ArgType::event_filter, false},
    {"max_queue", ArgType::queue_size, false},
}};
inline constexpr std::array<ArgSpec, 1> kArgs_events_unsubscribe{{
    {"sub", ArgType::u64, true},
}};
inline constexpr std::array<ArgSpec, 2> kArgs_game_save{{
    {"name", ArgType::save_name, true},
    {"timeout_ms", ArgType::timeout_ms, false},
}};
inline constexpr std::array<ArgSpec, 2> kArgs_game_load{{
    {"save", ArgType::save_name, true},
    {"timeout_ms", ArgType::timeout_ms, false},
}};
inline constexpr std::array<OpSpec, 11> kOps{{
    {"game.state", false, false, {}, {}},
    {"form.resolve", false, true, kArgs_form_resolve, kOneOf_form_resolve},
    {"papyrus.call_static", true, true, kArgs_papyrus_call_static, {}},
    {"papyrus.call_method", true, true, kArgs_papyrus_call_method, {}},
    {"papyrus.get_property", true, true, kArgs_papyrus_get_property, {}},
    {"papyrus.set_property", true, true, kArgs_papyrus_set_property, {}},
    {"papyrus.batch", false, true, kArgs_papyrus_batch, {}},
    {"events.subscribe", false, false, kArgs_events_subscribe, {}},
    {"events.unsubscribe", false, false, kArgs_events_unsubscribe, {}},
    {"game.save", false, true, kArgs_game_save, {}},
    {"game.load", false, false, kArgs_game_load, {}},
}};
inline constexpr const OpSpec* find_op(std::string_view name)
{
    for (const auto& op : kOps)
        if (op.name == name) return &op;
    return nullptr;
}

// ---- events (data fields, filterable fields, the loading rule)
enum class EventFieldType { form, integer, floating, boolean, string };
struct EventField { std::string_view name; EventFieldType type; bool nullable; };
struct EventSpec
{
    std::string_view name;
    std::span<const EventField> fields;                        // exactly the keys of the event's data object
    std::span<const std::string_view> filter_fields;           // fields a subscription filter may name
    std::span<const std::string_view> filter_required_any_of;  // empty, or a filter must name one of these
    bool deliver_while_loading;  // false: not queued while a load is in progress (counted in suppressed)
};
namespace event_names {
inline constexpr std::string_view kDeath = "death";
inline constexpr std::string_view kDying = "dying";
inline constexpr std::string_view kQuestStage = "quest_stage";
inline constexpr std::string_view kQuestStart = "quest_start";
inline constexpr std::string_view kQuestStop = "quest_stop";
inline constexpr std::string_view kModEvent = "mod_event";
inline constexpr std::string_view kChannel = "channel";
inline constexpr std::string_view kHit = "hit";
inline constexpr std::string_view kEquip = "equip";
inline constexpr std::string_view kUnequip = "unequip";
inline constexpr std::string_view kContainerChanged = "container_changed";
inline constexpr std::string_view kCellAttach = "cell_attach";
inline constexpr std::string_view kCellDetach = "cell_detach";
inline constexpr std::string_view kActivate = "activate";
inline constexpr std::string_view kPlayerLocation = "player_location";
inline constexpr std::string_view kMenuOpen = "menu_open";
inline constexpr std::string_view kMenuClose = "menu_close";
inline constexpr std::string_view kGameLoaded = "game_loaded";
inline constexpr std::string_view kGameSaved = "game_saved";
inline constexpr std::string_view kNewGame = "new_game";
}
inline constexpr std::array<EventField, 2> kEventFields_death{{
    {"victim", EventFieldType::form, false},
    {"killer", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_death{{
    "victim",
    "killer",
}};
inline constexpr std::array<EventField, 2> kEventFields_dying{{
    {"victim", EventFieldType::form, false},
    {"killer", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_dying{{
    "victim",
    "killer",
}};
inline constexpr std::array<EventField, 2> kEventFields_quest_stage{{
    {"quest", EventFieldType::form, false},
    {"stage", EventFieldType::integer, false},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_quest_stage{{
    "quest",
    "stage",
}};
inline constexpr std::array<EventField, 1> kEventFields_quest_start{{
    {"quest", EventFieldType::form, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_quest_start{{
    "quest",
}};
inline constexpr std::array<EventField, 1> kEventFields_quest_stop{{
    {"quest", EventFieldType::form, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_quest_stop{{
    "quest",
}};
inline constexpr std::array<EventField, 4> kEventFields_mod_event{{
    {"name", EventFieldType::string, false},
    {"str_arg", EventFieldType::string, false},
    {"num_arg", EventFieldType::floating, false},
    {"sender", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_mod_event{{
    "name",
    "sender",
}};
inline constexpr std::array<EventField, 3> kEventFields_channel{{
    {"channel", EventFieldType::string, false},
    {"payload", EventFieldType::string, false},
    {"truncated", EventFieldType::boolean, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_channel{{
    "channel",
}};
inline constexpr std::array<EventField, 8> kEventFields_hit{{
    {"target", EventFieldType::form, false},
    {"aggressor", EventFieldType::form, true},
    {"source", EventFieldType::form, true},
    {"projectile", EventFieldType::form, true},
    {"power_attack", EventFieldType::boolean, false},
    {"sneak_attack", EventFieldType::boolean, false},
    {"bash_attack", EventFieldType::boolean, false},
    {"blocked", EventFieldType::boolean, false},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_hit{{
    "target",
    "aggressor",
}};
inline constexpr std::array<std::string_view, 2> kEventFilterRequired_hit{{
    "target",
    "aggressor",
}};
inline constexpr std::array<EventField, 3> kEventFields_equip{{
    {"actor", EventFieldType::form, false},
    {"item", EventFieldType::form, false},
    {"reference", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_equip{{
    "actor",
    "item",
}};
inline constexpr std::array<EventField, 3> kEventFields_unequip{{
    {"actor", EventFieldType::form, false},
    {"item", EventFieldType::form, false},
    {"reference", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_unequip{{
    "actor",
    "item",
}};
inline constexpr std::array<EventField, 5> kEventFields_container_changed{{
    {"old_container", EventFieldType::form, true},
    {"new_container", EventFieldType::form, true},
    {"item", EventFieldType::form, false},
    {"count", EventFieldType::integer, false},
    {"reference", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 3> kEventFilter_container_changed{{
    "old_container",
    "new_container",
    "item",
}};
inline constexpr std::array<std::string_view, 3> kEventFilterRequired_container_changed{{
    "old_container",
    "new_container",
    "item",
}};
inline constexpr std::array<EventField, 1> kEventFields_cell_attach{{
    {"reference", EventFieldType::form, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_cell_attach{{
    "reference",
}};
inline constexpr std::array<EventField, 1> kEventFields_cell_detach{{
    {"reference", EventFieldType::form, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_cell_detach{{
    "reference",
}};
inline constexpr std::array<EventField, 2> kEventFields_activate{{
    {"target", EventFieldType::form, false},
    {"activator", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_activate{{
    "target",
    "activator",
}};
inline constexpr std::array<EventField, 2> kEventFields_player_location{{
    {"old_location", EventFieldType::form, true},
    {"new_location", EventFieldType::form, true},
}};
inline constexpr std::array<std::string_view, 2> kEventFilter_player_location{{
    "old_location",
    "new_location",
}};
inline constexpr std::array<EventField, 1> kEventFields_menu_open{{
    {"menu", EventFieldType::string, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_menu_open{{
    "menu",
}};
inline constexpr std::array<EventField, 1> kEventFields_menu_close{{
    {"menu", EventFieldType::string, false},
}};
inline constexpr std::array<std::string_view, 1> kEventFilter_menu_close{{
    "menu",
}};
inline constexpr std::array<EventField, 1> kEventFields_game_loaded{{
    {"new_game", EventFieldType::boolean, false},
}};
inline constexpr std::array<EventField, 1> kEventFields_game_saved{{
    {"name", EventFieldType::string, false},
}};
inline constexpr std::array<EventSpec, 20> kEvents{{
    {"death", kEventFields_death, kEventFilter_death, {}, false},
    {"dying", kEventFields_dying, kEventFilter_dying, {}, false},
    {"quest_stage", kEventFields_quest_stage, kEventFilter_quest_stage, {}, false},
    {"quest_start", kEventFields_quest_start, kEventFilter_quest_start, {}, false},
    {"quest_stop", kEventFields_quest_stop, kEventFilter_quest_stop, {}, false},
    {"mod_event", kEventFields_mod_event, kEventFilter_mod_event, {}, true},
    {"channel", kEventFields_channel, kEventFilter_channel, {}, true},
    {"hit", kEventFields_hit, kEventFilter_hit, kEventFilterRequired_hit, false},
    {"equip", kEventFields_equip, kEventFilter_equip, {}, false},
    {"unequip", kEventFields_unequip, kEventFilter_unequip, {}, false},
    {"container_changed", kEventFields_container_changed, kEventFilter_container_changed, kEventFilterRequired_container_changed, false},
    {"cell_attach", kEventFields_cell_attach, kEventFilter_cell_attach, {}, false},
    {"cell_detach", kEventFields_cell_detach, kEventFilter_cell_detach, {}, false},
    {"activate", kEventFields_activate, kEventFilter_activate, {}, false},
    {"player_location", kEventFields_player_location, kEventFilter_player_location, {}, false},
    {"menu_open", kEventFields_menu_open, kEventFilter_menu_open, {}, true},
    {"menu_close", kEventFields_menu_close, kEventFilter_menu_close, {}, true},
    {"game_loaded", kEventFields_game_loaded, {}, {}, true},
    {"game_saved", kEventFields_game_saved, {}, {}, true},
    {"new_game", {}, {}, {}, true},
}};
inline constexpr const EventSpec* find_event(std::string_view name)
{
    for (const auto& e : kEvents)
        if (e.name == name) return &e;
    return nullptr;
}
// game.save and game.load names (ECMAScript syntax; is_valid_save_name in requests.h implements it)
inline constexpr std::string_view kSaveNamePattern = "^[A-Za-z0-9_]{1,64}$";

// ---- config keys
enum class ConfigType { u16, u32, boolean, token, path };
struct ConfigKey { std::string_view name; ConfigType type; std::uint64_t min; std::uint64_t max; std::string_view default_text; };
inline constexpr std::array<ConfigKey, 12> kConfigKeys{{
    {"python_enabled", ConfigType::boolean, 0u, 0u, "true"},
    {"scripts_enabled", ConfigType::boolean, 0u, 0u, "true"},
    {"bridge_enabled", ConfigType::boolean, 0u, 0u, "false"},
    {"port", ConfigType::u16, 0u, 65535u, "45977"},
    {"fixed_token", ConfigType::token, 0u, 0u, ""},
    {"session_file", ConfigType::path, 0u, 0u, ""},
    {"default_timeout_ms", ConfigType::u32, 1u, 3600000u, "5000"},
    {"max_inflight_per_connection", ConfigType::u32, 1u, 4294967295u, "256"},
    {"max_inflight_total", ConfigType::u32, 1u, 4294967295u, "1024"},
    {"enable_console", ConfigType::boolean, 0u, 0u, "false"},
    {"trace_timing", ConfigType::boolean, 0u, 0u, "false"},
    {"console_echo_errors", ConfigType::boolean, 0u, 0u, "true"},
}};
inline constexpr const ConfigKey* find_config_key(std::string_view name)
{
    for (const auto& k : kConfigKeys)
        if (k.name == name) return &k;
    return nullptr;
}
namespace config_defaults {
inline constexpr bool kPythonEnabled = true;
inline constexpr bool kScriptsEnabled = true;
inline constexpr bool kBridgeEnabled = false;
inline constexpr std::uint16_t kPort = 45977u;
inline constexpr std::string_view kFixedToken = "";
inline constexpr std::string_view kSessionFile = "";
inline constexpr std::uint32_t kDefaultTimeoutMs = 5000u;
inline constexpr std::uint32_t kMaxInflightPerConnection = 256u;
inline constexpr std::uint32_t kMaxInflightTotal = 1024u;
inline constexpr bool kEnableConsole = false;
inline constexpr bool kTraceTiming = false;
inline constexpr bool kConsoleEchoErrors = true;
}

// ---- discovery
namespace discovery {
inline constexpr std::string_view kHost = "127.0.0.1";
inline constexpr std::string_view kSessionDirName = "Skython";
inline constexpr std::string_view kSessionFileName = "session.json";
inline constexpr std::string_view kEnvSession = "SKYTHON_SESSION";
inline constexpr std::string_view kEnvPort = "SKYTHON_PORT";
inline constexpr std::string_view kEnvToken = "SKYTHON_TOKEN";
// keys of the session file object
inline constexpr std::array<std::string_view, 5> kSessionFileFields{{
    "port",
    "token",
    "pid",
    "plugin_version",
    "protocol",
}};
}
}  // namespace spy
