"""Writes its game.settings (made JSON-able) and a mutation check next to python.log, for test_m2_console_settings."""
import json
import os
import pathlib

import _skython


def _plain(v):
    if hasattr(v, "items"):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    return v


def setup(game) -> None:
    try:
        game.settings["greeting"] = "changed"
        frozen = False
    except TypeError:
        frozen = True
    out = pathlib.Path(json.loads(_skython.config())["log_dir"]) / "livetest_settings.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps({"settings": _plain(game.settings), "frozen": frozen}))
    os.replace(tmp, out)
