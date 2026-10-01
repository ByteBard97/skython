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


# ---- Task 22: socket fixtures for the remote transport's tests --------------------------------------------
import socket  # noqa: E402

from support import FakeServer  # noqa: E402


@pytest.fixture
def server():
    with FakeServer() as s:
        yield s


@pytest.fixture
def sockets(monkeypatch):
    """Every client socket created through socket.create_connection, so a test can check it was closed."""
    made = []
    real = socket.create_connection

    def spy(*args, **kwargs):
        sock = real(*args, **kwargs)
        made.append(sock)
        return sock

    monkeypatch.setattr(socket, "create_connection", spy)
    return made
