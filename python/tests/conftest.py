import sys

import pytest

from fake_skyrim import FakeSkyrim


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A FakeSkyrim installed as the `_skyrim` module; its directories live under tmp_path."""
    f = FakeSkyrim(root=tmp_path)
    monkeypatch.setitem(sys.modules, "_skyrim", f)
    yield f
    f.close()


@pytest.fixture
def game(fake):
    """A Game over the in-process transport, talking to `fake`."""
    from skyrim.inprocess import connect_in_process
    with connect_in_process() as g:
        yield g
