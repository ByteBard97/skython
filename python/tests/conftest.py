import sys

import pytest

from fake_skython import FakeSkython


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A FakeSkython installed as the `_skython` module; its directories live under tmp_path."""
    f = FakeSkython(root=tmp_path)
    monkeypatch.setitem(sys.modules, "_skython", f)
    yield f
    f.close()


@pytest.fixture
def game(fake):
    """A Game over the in-process transport, talking to `fake`."""
    from skython.inprocess import connect_in_process
    with connect_in_process() as g:
        yield g


# ---- socket fixtures for the remote transport's tests --------------------------------------------
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
