import os

import pytest

from support import load_session, log_text, raw_connect, refused

VARIANT = os.environ.get("SPY_VARIANT", "")


def only(name):
    return pytest.mark.skipif(VARIANT != name, reason=f"run with SPY_VARIANT={name} (Task 26 Step 7)")


@only("no-python")
def test_missing_python_folder_disables_python():
    assert "Python disabled: missing" in log_text("SkythonLoader.log")
    assert "python314.dll" in log_text("SkythonLoader.log")
    assert refused()


@only("no-core")
def test_missing_core_dll_disables_python():
    assert "Python disabled: missing" in log_text("SkythonLoader.log")
    assert "SkythonCore.dll" in log_text("SkythonLoader.log")
    assert refused()


@only("no-package")
def test_missing_skython_package_is_a_logged_failure():
    text = log_text()
    assert "python host: failed: skython._host" in text and "No module named 'skython'" in text
    assert refused()


@only("python-off")
def test_python_enabled_false():
    assert "python host: disabled: python_enabled=false" in log_text()
    assert "python host: starting" not in log_text()
    assert refused()


@only("bridge-off")
def test_shipped_ini_opens_no_port():
    assert "python host: running" in log_text()
    assert refused()


@only("collision")
def test_another_python314_already_loaded_disables_python():
    text = log_text("SkythonLoader.log")
    assert "TEST HOOK: preloaded" in text
    assert "another python314.dll is already loaded from" in text
    assert refused()


@only("no-stdlib")
def test_missing_stdlib_zip_is_a_logged_failure():
    text = log_text()
    assert "python host: failed: Py_InitializeFromConfig" in text
    assert "python host: running" not in text
    assert refused()


@only("relaunch")
def test_bridge_listens_after_a_quick_relaunch():
    # The previous run was quit with a bridge client connected and the game relaunched within 60 s (Step 7).
    assert "python host: running" in log_text()
    raw_connect(load_session()).close()  # load_session raises if the session file says the bridge is not serving
