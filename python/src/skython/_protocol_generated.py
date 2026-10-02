# GENERATED FILE - DO NOT EDIT.
# Source: protocol/protocol.json. Regenerate with: python3 tools/gen_protocol.py
# The drift check (python3 tools/gen_protocol.py --check) fails if this file differs from what the generator writes.
# Constants, error-code registry data and the op table for the skython package. Nothing here is logic.

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 1
PROTOCOL_VERSION = (PROTOCOL_MAJOR, PROTOCOL_MINOR)

MESSAGE_TYPES = ("hello", "welcome", "handshake_error", "request", "response", "event")

# ---- limits
MAX_FRAME_BYTES = 16777216  # bytes
PRE_AUTH_FRAME_BYTES = 65536  # bytes
MAX_NAME_BYTES = 256  # bytes
MAX_BATCH_CALLS = 256  # calls
MIN_TIMEOUT_MS = 1  # ms
MAX_TIMEOUT_MS = 3600000  # ms
MAX_ERROR_MESSAGE_BYTES = 1024  # bytes
MAX_CLIENT_NAME_BYTES = 64  # bytes
MAX_CONNECTIONS = 32  # connections
HELLO_TIMEOUT_MS = 5000  # ms
MAX_JSON_DEPTH = 64  # levels
MAX_QUEUED_BYTES_PER_CONNECTION = 67108864  # bytes
TOKEN_LENGTH = 32  # hex characters
INT_MIN = -2147483648
INT_MAX = 2147483647
FLOAT_MAX_TEXT = 3.4028235e+38
MAX_ARRAY_ITEMS = 128  # items
DEFAULT_EVENT_QUEUE = 1024  # events
MAX_EVENT_QUEUE = 65536  # events
MAX_SUBSCRIPTIONS = 1024  # subscriptions
MAX_SUBSCRIPTIONS_PER_CONSUMER = 256  # subscriptions
MAX_QUEUED_EVENT_BYTES_PER_CONSUMER = 16777216  # bytes
MAX_FILTER_VALUES = 128  # values
MAX_EVENTS_PER_NEXT = 256  # events
MAX_CHANNEL_PAYLOAD_BYTES = 65536  # bytes
MAX_CHANNEL_BYTES = 256  # bytes

# ---- error codes (the order is the order of protocol.json)
CODE_NOT_READY = "not_ready"
CODE_BUSY = "busy"
CODE_BAD_REQUEST = "bad_request"
CODE_UNKNOWN_FUNCTION = "unknown_function"
CODE_TYPE_MISMATCH = "type_mismatch"
CODE_STALE_REFERENCE = "stale_reference"
CODE_NOT_FOUND = "not_found"
CODE_TIMEOUT = "timeout"
CODE_VM_ERROR = "vm_error"
CODE_UNSUPPORTED = "unsupported"
CODE_INTERNAL = "internal"
CODE_UNAUTHORIZED = "unauthorized"
CODE_PROTOCOL_MISMATCH = "protocol_mismatch"
ERROR_CODES = (
    "not_ready",
    "busy",
    "bad_request",
    "unknown_function",
    "type_mismatch",
    "stale_reference",
    "not_found",
    "timeout",
    "vm_error",
    "unsupported",
    "internal",
    "unauthorized",
    "protocol_mismatch",
)
# wire code -> exception class name in skython.errors
REMOTE_EXCEPTIONS = {
    "not_ready": "NotReady",
    "busy": "Busy",
    "bad_request": "BadRequest",
    "unknown_function": "UnknownFunction",
    "type_mismatch": "TypeMismatch",
    "stale_reference": "StaleReference",
    "not_found": "NotFound",
    "timeout": "Timeout",
    "vm_error": "VmError",
    "unsupported": "Unsupported",
    "internal": "InternalError",
}
HANDSHAKE_EXCEPTIONS = {"unauthorized": "Unauthorized", "protocol_mismatch": "ProtocolMismatch"}
HANDSHAKE_CODES = ("bad_request", "unauthorized", "protocol_mismatch")
NOT_READY_REASONS = ("starting", "no_game", "loading", "paused", "saving")

# ---- values, form ids and form keys
ALIAS_TYPES = ("ReferenceAlias", "LocationAlias")
DEFAULT_ALIAS_TYPE = "ReferenceAlias"
ELEM_TYPES = ("Bool", "Int", "Float", "String", "Form", "Alias")
FORM_ID_MAX_LOCAL_ID = 16777215
FORM_ID_MAX_LIGHT_LOCAL_ID = 4095
FORM_ID_RUNTIME_PREFIX = 255
FORM_ID_LIGHT_PREFIX = 254
FORM_ID_MAX_KEY_HEX_DIGITS = 8
FORM_ID_CANONICAL_HEX_DIGITS = 6
PLUGIN_EXTENSIONS = (".esm", ".esp", ".esl")
LIGHT_EXTENSION = ".esl"

# ---- ops: name -> {batchable, needs_game, exactly_one_of, args: ((name, type, required), ...)}
OP_GAME_STATE = "game.state"
OP_FORM_RESOLVE = "form.resolve"
OP_PAPYRUS_CALL_STATIC = "papyrus.call_static"
OP_PAPYRUS_CALL_METHOD = "papyrus.call_method"
OP_PAPYRUS_GET_PROPERTY = "papyrus.get_property"
OP_PAPYRUS_SET_PROPERTY = "papyrus.set_property"
OP_PAPYRUS_BATCH = "papyrus.batch"
OP_EVENTS_SUBSCRIBE = "events.subscribe"
OP_EVENTS_UNSUBSCRIBE = "events.unsubscribe"
OP_GAME_SAVE = "game.save"
OP_GAME_LOAD = "game.load"
OPS = {
    "game.state": {"batchable": False, "needs_game": False, "exactly_one_of": (), "args": ()},
    "form.resolve": {
        "batchable": False,
        "needs_game": True,
        "exactly_one_of": ("form_key", "form_id", "editor_id"),
        "args": (
            ("form_key", "form_key", False),
            ("form_id", "u32", False),
            ("editor_id", "name", False),
        ),
    },
    "papyrus.call_static": {
        "batchable": True,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (
            ("script", "name", True),
            ("function", "name", True),
            ("args", "value_list", False),
            ("timeout_ms", "timeout_ms", False),
            ("require_unpaused", "bool", False),
        ),
    },
    "papyrus.call_method": {
        "batchable": True,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (
            ("object", "ref", True),
            ("script", "name", True),
            ("function", "name", True),
            ("args", "value_list", False),
            ("timeout_ms", "timeout_ms", False),
            ("require_unpaused", "bool", False),
        ),
    },
    "papyrus.get_property": {
        "batchable": True,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (("object", "ref", True), ("script", "name", True), ("property", "name", True)),
    },
    "papyrus.set_property": {
        "batchable": True,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (
            ("object", "ref", True),
            ("script", "name", True),
            ("property", "name", True),
            ("value", "value", True),
        ),
    },
    "papyrus.batch": {
        "batchable": False,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (
            ("calls", "call_list", True),
            ("stop_on_error", "bool", False),
            ("timeout_ms", "timeout_ms", False),
        ),
    },
    "events.subscribe": {
        "batchable": False,
        "needs_game": False,
        "exactly_one_of": (),
        "args": (
            ("event", "event_name", True),
            ("filter", "event_filter", False),
            ("max_queue", "queue_size", False),
        ),
    },
    "events.unsubscribe": {
        "batchable": False,
        "needs_game": False,
        "exactly_one_of": (),
        "args": (("sub", "u64", True),),
    },
    "game.save": {
        "batchable": False,
        "needs_game": True,
        "exactly_one_of": (),
        "args": (("name", "save_name", True), ("timeout_ms", "timeout_ms", False)),
    },
    "game.load": {
        "batchable": False,
        "needs_game": False,
        "exactly_one_of": (),
        "args": (("save", "save_name", True), ("timeout_ms", "timeout_ms", False)),
    },
}
BATCHABLE_OPS = ("papyrus.call_static", "papyrus.call_method", "papyrus.get_property", "papyrus.set_property")

# ---- events: name -> {fields: ((name, type, nullable), ...), filter_fields, filter_required_any_of, deliver_while_loading}
EVENT_DEATH = "death"
EVENT_DYING = "dying"
EVENT_QUEST_STAGE = "quest_stage"
EVENT_QUEST_START = "quest_start"
EVENT_QUEST_STOP = "quest_stop"
EVENT_MOD_EVENT = "mod_event"
EVENT_CHANNEL = "channel"
EVENT_HIT = "hit"
EVENT_EQUIP = "equip"
EVENT_UNEQUIP = "unequip"
EVENT_CONTAINER_CHANGED = "container_changed"
EVENT_CELL_ATTACH = "cell_attach"
EVENT_CELL_DETACH = "cell_detach"
EVENT_ACTIVATE = "activate"
EVENT_PLAYER_LOCATION = "player_location"
EVENT_MENU_OPEN = "menu_open"
EVENT_MENU_CLOSE = "menu_close"
EVENT_GAME_LOADED = "game_loaded"
EVENT_GAME_SAVED = "game_saved"
EVENT_NEW_GAME = "new_game"
EVENTS = {
    "death": {
        "fields": (("victim", "form", False), ("killer", "form", True)),
        "filter_fields": ("victim", "killer"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "dying": {
        "fields": (("victim", "form", False), ("killer", "form", True)),
        "filter_fields": ("victim", "killer"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "quest_stage": {
        "fields": (("quest", "form", False), ("stage", "int", False)),
        "filter_fields": ("quest", "stage"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "quest_start": {
        "fields": (("quest", "form", False),),
        "filter_fields": ("quest",),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "quest_stop": {
        "fields": (("quest", "form", False),),
        "filter_fields": ("quest",),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "mod_event": {
        "fields": (
            ("name", "string", False),
            ("str_arg", "string", False),
            ("num_arg", "float", False),
            ("sender", "form", True),
        ),
        "filter_fields": ("name", "sender"),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "channel": {
        "fields": (
            ("channel", "string", False),
            ("payload", "string", False),
            ("truncated", "bool", False),
        ),
        "filter_fields": ("channel",),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "hit": {
        "fields": (
            ("target", "form", False),
            ("aggressor", "form", True),
            ("source", "form", True),
            ("projectile", "form", True),
            ("power_attack", "bool", False),
            ("sneak_attack", "bool", False),
            ("bash_attack", "bool", False),
            ("blocked", "bool", False),
        ),
        "filter_fields": ("target", "aggressor"),
        "filter_required_any_of": ("target", "aggressor"),
        "deliver_while_loading": False,
    },
    "equip": {
        "fields": (("actor", "form", False), ("item", "form", False), ("reference", "form", True)),
        "filter_fields": ("actor", "item"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "unequip": {
        "fields": (("actor", "form", False), ("item", "form", False), ("reference", "form", True)),
        "filter_fields": ("actor", "item"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "container_changed": {
        "fields": (
            ("old_container", "form", True),
            ("new_container", "form", True),
            ("item", "form", False),
            ("count", "int", False),
            ("reference", "form", True),
        ),
        "filter_fields": ("old_container", "new_container", "item"),
        "filter_required_any_of": ("old_container", "new_container", "item"),
        "deliver_while_loading": False,
    },
    "cell_attach": {
        "fields": (("reference", "form", False),),
        "filter_fields": ("reference",),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "cell_detach": {
        "fields": (("reference", "form", False),),
        "filter_fields": ("reference",),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "activate": {
        "fields": (("target", "form", False), ("activator", "form", True)),
        "filter_fields": ("target", "activator"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "player_location": {
        "fields": (("old_location", "form", True), ("new_location", "form", True)),
        "filter_fields": ("old_location", "new_location"),
        "filter_required_any_of": (),
        "deliver_while_loading": False,
    },
    "menu_open": {
        "fields": (("menu", "string", False),),
        "filter_fields": ("menu",),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "menu_close": {
        "fields": (("menu", "string", False),),
        "filter_fields": ("menu",),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "game_loaded": {
        "fields": (("new_game", "bool", False),),
        "filter_fields": (),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "game_saved": {
        "fields": (("name", "string", False),),
        "filter_fields": (),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
    "new_game": {
        "fields": (),
        "filter_fields": (),
        "filter_required_any_of": (),
        "deliver_while_loading": True,
    },
}
SAVE_NAME_PATTERN = "^[A-Za-z0-9_]{1,64}$"

# ---- config keys (the plugin ini) and their defaults
CONFIG_KEYS = {
    "python_enabled": {"type": "bool", "default": True},
    "scripts_enabled": {"type": "bool", "default": True},
    "bridge_enabled": {"type": "bool", "default": False},
    "port": {"type": "u16", "default": 45977, "min": 0, "max": 65535},
    "fixed_token": {"type": "token", "default": ""},
    "session_file": {"type": "path", "default": ""},
    "default_timeout_ms": {"type": "u32", "default": 5000, "min": 1, "max": 3600000},
    "max_inflight_per_connection": {"type": "u32", "default": 256, "min": 1, "max": 4294967295},
    "max_inflight_total": {"type": "u32", "default": 1024, "min": 1, "max": 4294967295},
    "enable_console": {"type": "bool", "default": False},
    "trace_timing": {"type": "bool", "default": False},
    "console_echo_errors": {"type": "bool", "default": True},
}

# ---- discovery
HOST = "127.0.0.1"
SESSION_DIR_NAME = "Skython"
SESSION_FILE_NAME = "session.json"
ENV_SESSION = "SKYTHON_SESSION"
ENV_PORT = "SKYTHON_PORT"
ENV_TOKEN = "SKYTHON_TOKEN"
SESSION_FILE_FIELDS = ("port", "token", "pid", "plugin_version", "protocol")

# ---- client defaults
CLIENT_NAME = "skython"
CONNECT_TIMEOUT_S = 5.0
CALL_TIMEOUT_S = 30.0
SEND_TIMEOUT_S = 30.0
RETRY_NOT_READY_S = 30.0
READY_TIMEOUT_S = 120.0
GRACE_S = 2.0
