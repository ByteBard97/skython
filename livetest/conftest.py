"""Live tests: the game runs with Skython (loader + core), the embedded Python and the dev ini.
Run from the repo root, the game on the Windows test machine:
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


@pytest.fixture(scope="module")
def fresh_save(session):
    """Load a fixed save before the module runs: skythonbase when it exists on the test machine, else s4test. Returns {"save": name, "state": the interactive state after the load}.
    skythonbase holds SkythonTest.esp, so a launch that may load it is preceded by `tools/box/box_plugins.sh add`
    ; the quit/stop tools restore plugins.txt."""
    import skython
    from skython import errors
    from support import box_save_count, load_save
    # box_save_count is a prefix match (skythonbase*.ess); the exact name is checked by the plugin's game.load, which
    # answers not_found unless skythonbase.ess itself exists.
    name = "skythonbase" if box_save_count("skythonbase") >= 1 else "s4test"
    with skython.connect(port=session.port, token=session.token, client_name="livetest-fresh-save") as g:
        try:
            state = load_save(g, name)
        except errors.NotFound:
            if name != "skythonbase":
                raise
            name = "s4test"  # only other saves start with skythonbase: no base save yet
            state = load_save(g, name)
    return {"save": name, "state": state}
