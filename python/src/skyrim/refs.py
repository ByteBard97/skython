"""Form keys ("Plugin.esp|0x0012AB") and form-id ranges. Grammar: protocol/PROTOCOL.md "Values"; the extensions,
digit counts and ceilings are the generated constants (protocol.json "form_ids")."""
import re

from . import _protocol_generated as _g

_EXTENSIONS = "|".join(re.escape(e.lstrip(".")) for e in _g.PLUGIN_EXTENSIONS)
# fullmatch, not match: "$" would accept a trailing newline. ASCII keeps IGNORECASE from letting
# "ſ" stand in for "s" or the Kelvin sign for "k".
_KEY = re.compile(rf"(?P<plugin>[^|\x00-\x1f]+\.(?:{_EXTENSIONS}))\|0[xX](?P<id>[0-9A-Fa-f]{{1,{_g.FORM_ID_MAX_KEY_HEX_DIGITS}}})",
                  re.ASCII | re.IGNORECASE)


def parse_form_key(text: str) -> tuple[str, int]:
    m = _KEY.fullmatch(text) if isinstance(text, str) else None
    if not m:
        raise ValueError(f"not a form key (expected Plugin.esp|0x0012AB): {text!r}")
    plugin = m.group("plugin")
    stem, ext = plugin.rsplit(".", 1)
    if not stem or stem != stem.strip():
        raise ValueError(f"form key has an empty or space-padded plugin name: {text!r}")
    local_id = int(m.group("id"), 16)
    limit = _g.FORM_ID_MAX_LIGHT_LOCAL_ID if f".{ext.lower()}" == _g.LIGHT_EXTENSION else _g.FORM_ID_MAX_LOCAL_ID
    if local_id > limit:
        raise ValueError(f"local form id {local_id:#x} is over {limit.bit_length()} bits for {ext.lower()}: {text!r}")
    return plugin, local_id


def format_form_key(plugin: str, local_id: int) -> str:
    return f"{plugin}|0x{local_id:0{_g.FORM_ID_CANONICAL_HEX_DIGITS}X}"


def is_runtime_form_id(form_id: int) -> bool:
    return (form_id >> 24) == _g.FORM_ID_RUNTIME_PREFIX


def is_light_form_id(form_id: int) -> bool:
    return (form_id >> 24) == _g.FORM_ID_LIGHT_PREFIX
