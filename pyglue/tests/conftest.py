"""Shared setup for the _skython module tests (Task 19).

The module under test is the real C++ extension, built by pyglue/linux/CMakeLists.txt against uv's CPython 3.14 with
the fake backend. Its build directory goes on sys.path; SKYTHON_PYGLUE_BUILD overrides where it is.
"""
import json
import os
import pathlib
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = pathlib.Path(os.environ.get("SKYTHON_PYGLUE_BUILD", ROOT / "pyglue" / "build"))
sys.path.insert(0, str(BUILD))

# _skython.config() and _skython.log() use this folder; set it before the first import.
FAKE_DIR = pathlib.Path(tempfile.mkdtemp(prefix="skython-fake-"))
os.environ["SKYTHON_FAKE_DIR"] = str(FAKE_DIR)

import _skython  # noqa: E402  (needs the path above)


def request(req_id, function, *args, op="papyrus.call_static", **extra):
    """Request text for Fake.<function>(*args); extra keys (timeout_ms, require_unpaused) go into args."""
    if op == "papyrus.call_static":
        body = {"script": "Fake", "function": function, "args": list(args), **extra}
    else:
        body = dict(extra)
    return json.dumps({"type": "request", "id": req_id, "op": op, "args": body})


def response(text):
    """Parse response text and check the envelope every response has."""
    r = json.loads(text)
    assert r["type"] == "response"
    assert isinstance(r["ok"], bool)
    return r


def code(r):
    return "ok" if r["ok"] else r["error"]["code"]


@pytest.fixture(autouse=True)
def ready_game():
    """Every test starts and ends with a ready, unpaused fake game."""
    _skython._fake_set_not_ready(None)
    _skython._fake_set_paused(False)
    yield
    _skython._fake_set_not_ready(None)
    _skython._fake_set_paused(False)
