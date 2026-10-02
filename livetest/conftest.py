"""Live tests: the game runs with Skython (loader + core), the embedded Python and the dev ini.
Run from the repo root, the game on the Windows box (Task 1c):
  tools/box/box_session.sh up && source out/box-live.env && SKYTHON_LIVE=1 uv run --project python pytest livetest -q
Without SKYTHON_LIVE=1 every test is skipped (so a plain pytest run on a machine without the game is clean).
"""
import os

import pytest

from support import load_session


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SKYTHON_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set SKYTHON_LIVE=1 with the game running")
    for item in items:
        item.add_marker(skip)


@pytest.fixture(scope="session")
def session():
    return load_session()


@pytest.fixture
def game(session):
    import skython
    g = skython.connect(port=session.port, token=session.token, client_name="livetest")
    yield g
    g.close()
