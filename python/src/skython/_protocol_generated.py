# GENERATED FILE - DO NOT EDIT.
# Source: protocol/protocol.json. Regenerate with: python3 tools/gen_protocol.py
# The drift check (python3 tools/gen_protocol.py --check) fails if this file differs from what the generator writes.
# Constants, error-code registry data and the op table for the skython package. Nothing here is logic.

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0
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
NOT_READY_REASONS = ("starting", "no_game", "loading", "paused")

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
}
BATCHABLE_OPS = ("papyrus.call_static", "papyrus.call_method", "papyrus.get_property", "papyrus.set_property")

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
}

# ---- discovery
HOST = "127.0.0.1"
SESSION_DIR_NAME = "SkyrimPython"
SESSION_FILE_NAME = "session.json"
ENV_SESSION = "SKYRIM_PYTHON_SESSION"
ENV_PORT = "SKYRIM_PYTHON_PORT"
ENV_TOKEN = "SKYRIM_PYTHON_TOKEN"
SESSION_FILE_FIELDS = ("port", "token", "pid", "plugin_version", "protocol")

# ---- client defaults
CLIENT_NAME = "skyrim-python"
CONNECT_TIMEOUT_S = 5.0
CALL_TIMEOUT_S = 30.0
SEND_TIMEOUT_S = 30.0
RETRY_NOT_READY_S = 30.0
READY_TIMEOUT_S = 120.0
GRACE_S = 2.0
