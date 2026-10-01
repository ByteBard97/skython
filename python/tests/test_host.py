"""skyrim._host against the fake _skyrim: logging, the script loader, reload, and the bridge it starts."""
import io
import json
import socket
import sys
import textwrap
import threading
import time

import pytest

from support import wait_until
from skyrim import errors
from skyrim.discovery import Session, find_session, write_session_file
from skyrim.remote import connect_remote


@pytest.fixture
def host(fake, monkeypatch, tmp_path):
    """skyrim._host with short timeouts; every global it touches is restored afterwards."""
    from skyrim import _host
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(_host, "SETUP_TIMEOUT_S", 0.5)
    monkeypatch.setattr(_host, "SETUP_WARN_S", 0.2)
    (tmp_path / "scripts").mkdir()
    yield _host
    _host._reset()
    for name in [k for k in sys.modules if k.startswith("t24_")]:
        del sys.modules[name]


def script(tmp_path, name, body, package=False):
    folder = tmp_path / "scripts"
    path = folder / name / "__init__.py" if package else folder / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body))


def echoes(fake):
    return [r["args"]["args"][0] for r in fake.requests if r["args"].get("function") == "Echo"]


def log_text(tmp_path):
    return (tmp_path / "logs" / "python.log").read_text(encoding="utf-8")


ECHO_SETUP = """
def setup(game):
    game.call("Fake", "Echo", __name__)
"""


def test_main_loads_modules_and_packages_in_name_order(host, fake, tmp_path):
    script(tmp_path, "t24_b", ECHO_SETUP)
    script(tmp_path, "t24_a", ECHO_SETUP, package=True)
    script(tmp_path, "_t24_private", ECHO_SETUP)  # skipped: leading underscore
    assert host.main() is None
    assert host.status()["scripts"] == {"t24_a": "ok", "t24_b": "ok"}
    assert echoes(fake) == ["t24_a", "t24_b"]
    assert "script t24_a: ok" in log_text(tmp_path)


def test_a_failing_script_never_stops_the_others(host, fake, tmp_path):
    script(tmp_path, "t24_1_import", "raise RuntimeError('broken at import')\n")
    script(tmp_path, "t24_2_setup", "def setup(game):\n    raise ValueError('nope')\n")
    script(tmp_path, "t24_3_nosetup", "X = 1\n")
    script(tmp_path, "t24_4_exit", "def setup(game):\n    raise SystemExit(3)\n")
    script(tmp_path, "t24_5_ok", ECHO_SETUP)
    host.main()
    st = host.status()["scripts"]
    assert st["t24_1_import"] == "failed: RuntimeError: broken at import"
    assert st["t24_2_setup"] == "failed: ValueError: nope"
    assert st["t24_3_nosetup"] == "failed: no setup(game) function"
    assert st["t24_4_exit"] == "failed: SystemExit: 3"
    assert st["t24_5_ok"] == "ok"
    text = log_text(tmp_path)
    assert "Traceback" in text and "broken at import" in text
    assert any(level == "error" and "t24_2_setup" in msg for level, msg in fake.logs), "WARNING+ reaches the plugin log"


def test_a_blocking_setup_times_out_and_main_still_returns(host, fake, tmp_path):
    script(tmp_path, "t24_a_block", "import time\n\ndef setup(game):\n    time.sleep(3)\n")
    script(tmp_path, "t24_b_after", ECHO_SETUP)
    t0 = time.monotonic()
    host.main()
    assert time.monotonic() - t0 < 2.5
    st = host.status()["scripts"]
    assert st["t24_a_block"].startswith("timeout: setup still running")
    assert st["t24_b_after"] == "ok"


def test_each_script_gets_its_own_game(host, fake, tmp_path):
    script(tmp_path, "t24_a_closes", "def setup(game):\n    with game:\n        pass\n")  # closes its Game
    script(tmp_path, "t24_b_calls", ECHO_SETUP)
    host.main()
    assert host.status()["scripts"] == {"t24_a_closes": "ok", "t24_b_calls": "ok"}
    assert echoes(fake) == ["t24_b_calls"]


def test_setup_runs_before_a_game_is_loaded_so_game_work_waits_on_a_thread(host, fake, tmp_path):
    # In the game, main() runs at the main menu: a needs_game call in setup would be NotReady.
    script(tmp_path, "t24_waits", """
    import threading

    def setup(game):
        def work():
            game.wait_ready(timeout=10)
            game.call("Fake", "Echo", "after the load")

        threading.Thread(target=work, daemon=True).start()
    """)
    fake._fake_set_not_ready("no_game")
    host.main()
    assert host.status()["scripts"] == {"t24_waits": "ok"}
    assert echoes(fake) == []
    fake._fake_set_not_ready(None)
    wait_until(lambda: echoes(fake) == ["after the load"], what="the script's call after the game is ready")


def test_a_slow_setup_is_ok_but_logged(host, tmp_path):
    script(tmp_path, "t24_slow", "import time\n\ndef setup(game):\n    time.sleep(0.3)\n")
    host.main()
    assert host.status()["scripts"]["t24_slow"] == "ok"
    assert "should return quickly" in log_text(tmp_path)


def test_reload_runs_teardown_and_the_new_code(host, fake, tmp_path):
    body = """
    def setup(game):
        game.call("Fake", "Echo", "{v}")

    def teardown():
        import _skyrim
        _skyrim.log("info", "teardown {v}")
    """
    script(tmp_path, "t24_r", body.format(v="one"))
    host.main()
    script(tmp_path, "t24_r", body.format(v="second version"))  # a different size, so no stale .pyc
    assert host.reload("t24_r") == "ok"
    assert echoes(fake) == ["one", "second version"]
    assert ("info", "teardown one") in fake.logs
    assert host.reload("t24_missing").startswith("failed: no script named")


def test_reload_closes_the_scripts_previous_game(host, fake, tmp_path):
    # An old thread that teardown() did not stop must not keep driving the game after a reload.
    body = """
    import builtins, threading, time

    def setup(game):
        builtins.t24_games = getattr(builtins, "t24_games", []) + [game]
        if len(builtins.t24_games) > 1:
            return  # the new version starts no thread

        def loop():
            while True:
                try:
                    game.call("Fake", "Echo", "old thread")
                except Exception as e:
                    builtins.t24_old_error = e
                    return
                time.sleep(0.01)

        threading.Thread(target=loop, daemon=True).start()
    """
    script(tmp_path, "t24_closes", body)
    import builtins
    try:
        host.main()
        wait_until(lambda: "old thread" in echoes(fake), what="the old thread's first call")
        script(tmp_path, "t24_closes", body + "# second version\n")  # a different size, so no stale .pyc
        assert host.reload("t24_closes") == "ok"
        wait_until(lambda: hasattr(builtins, "t24_old_error"), what="the old thread to see its Game closed")
        assert isinstance(builtins.t24_old_error, errors.ConnectionLost)
        old, new = builtins.t24_games
        assert old.closed and not new.closed and new is not old
        new.call("Fake", "Echo", "new game")
        assert echoes(fake)[-1] == "new game"
    finally:
        for attr in ("t24_games", "t24_old_error"):
            if hasattr(builtins, attr):
                delattr(builtins, attr)


def test_reload_before_any_load_is_a_failure_not_an_exception(host):
    assert host.reload("anything") == "failed: no scripts have been loaded yet"


def test_scripts_can_be_disabled(host, fake, tmp_path):
    fake.config_overrides["scripts_enabled"] = False
    script(tmp_path, "t24_never", ECHO_SETUP)
    host.main()
    assert host.status()["scripts"] == {}
    assert "t24_never" not in sys.modules


def test_the_bridge_starts_when_enabled_and_serves_the_same_api(host, fake, tmp_path):
    fake.config_overrides.update({"bridge_enabled": True, "port": 0})
    fake.epoch = 3
    host.main()
    st = host.status()
    assert st["bridge"].startswith("listening on 127.0.0.1:") and st["bridge_port"]
    with connect_remote(tmp_path / "session.json") as game:  # discovery through the session file
        assert game.call("Fake", "Echo", "over the bridge") == "over the bridge"
        assert game.epoch == 3
        assert game.welcome["plugin_version"] == "0.0.0-fake"


def test_a_disabled_bridge_leaves_an_inactive_session_file(host, tmp_path):
    host.main()
    assert host.status()["bridge"] == "disabled"
    with pytest.raises(errors.NotRunning, match="bridge_enabled is false"):
        find_session(tmp_path / "session.json", env={})


def test_a_bridge_that_cannot_listen_is_reported_and_scripts_still_load(host, fake, tmp_path):
    taken = socket.create_server(("127.0.0.1", 0))
    fake.config_overrides.update({"bridge_enabled": True, "port": taken.getsockname()[1]})
    script(tmp_path, "t24_ok", ECHO_SETUP)
    try:
        host.main()
    finally:
        taken.close()
    assert host.status()["bridge"].startswith("failed:")
    assert host.status()["scripts"] == {"t24_ok": "ok"}
    with pytest.raises(errors.NotRunning, match="could not listen"):
        find_session(tmp_path / "session.json", env={})


def test_a_bridge_that_cannot_start_replaces_a_stale_session_file(host, fake, tmp_path):
    write_session_file(tmp_path / "session.json", Session("127.0.0.1", 45977, "ab" * 16, 1, "0.0.0-fake"))  # last run
    fake.config_overrides.update({"bridge_enabled": True, "port": 0, "fixed_token": "not a token"})
    host.main()
    assert host.status()["bridge"].startswith("failed:")
    with pytest.raises(errors.NotRunning, match="could not start"):
        find_session(tmp_path / "session.json", env={})


def test_main_never_raises(host, fake):
    fake.config = lambda: "this is not json"
    assert host.main() is None
    assert any(level == "error" and "skyrim._host.main failed" in msg for level, msg in fake.logs)


def test_print_and_uncaught_thread_exceptions_go_to_the_log(host, tmp_path):
    host.main()
    print("hello from a script")

    def boom():
        raise RuntimeError("boom in a thread")

    t = threading.Thread(target=boom, name="t24-thread")
    t.start()
    t.join()
    wait_until(lambda: "boom in a thread" in log_text(tmp_path), what="the thread's traceback in python.log")
    text = log_text(tmp_path)
    assert "hello from a script" in text
    assert "uncaught exception in thread t24-thread" in text


def test_stdout_is_a_text_stream_and_threads_keep_their_partial_lines(host, tmp_path):
    host.main()
    out = sys.stdout
    assert isinstance(out, io.TextIOBase) and out.writable() and out.encoding == "utf-8"
    half_written, go_on = threading.Event(), threading.Event()

    def writer():
        out.write("from A, ")  # print() writes the text and the newline separately
        half_written.set()
        go_on.wait(5)
        out.write("end of A\n")

    t = threading.Thread(target=writer)
    t.start()
    half_written.wait(5)
    out.write("from B\n")
    go_on.set()
    t.join()
    text = log_text(tmp_path)
    assert "from A, end of A" in text and "from B" in text
    assert "from A, from B" not in text


def test_the_switch_interval_is_cpythons_default_unless_s9_says_otherwise(host):
    host.main()
    assert host.SWITCH_INTERVAL_S is None
    assert host.status()["switch_interval_s"] == sys.getswitchinterval()


def test_status_names_the_log_file(host, tmp_path):
    host.main()
    assert host.status()["log_file"] == str(tmp_path / "logs" / "python.log")
    assert json.dumps(host.status())  # plain data, so it can be sent over the bridge later


def test_a_log_dir_that_cannot_be_created_does_not_stop_scripts_or_the_bridge(host, fake, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("a regular file")
    fake.config_overrides.update({"log_dir": str(blocker / "logs"), "bridge_enabled": True, "port": 0})
    script(tmp_path, "t24_ok", ECHO_SETUP)
    host.main()
    st = host.status()
    assert st["log_file"] is None
    assert st["scripts"] == {"t24_ok": "ok"}
    assert st["bridge"].startswith("listening on 127.0.0.1:")
    assert any(level == "error" and "log" in msg for level, msg in fake.logs), "the failure reaches the plugin log"


def test_a_startup_failure_leaves_an_inactive_session_file(host, fake, tmp_path, monkeypatch):
    write_session_file(tmp_path / "session.json", Session("127.0.0.1", 45977, "ab" * 16, 1, "0.0.0-fake"))  # last run
    from skyrim import inprocess

    def broken(*args, **kwargs):
        raise RuntimeError("no connection for you")

    monkeypatch.setattr(inprocess, "connect_in_process", broken)
    assert host.main() is None
    with pytest.raises(errors.NotRunning, match="could not start: no connection for you"):
        find_session(tmp_path / "session.json", env={})


def test_a_setup_that_finishes_after_its_timeout_is_recorded_and_torn_down_on_reload(host, fake, tmp_path):
    script(tmp_path, "t24_late", """
    import time

    def setup(game):
        time.sleep(0.8)

    def teardown():
        import _skyrim
        _skyrim.log("info", "late teardown")
    """)
    host.main()
    assert host.status()["scripts"]["t24_late"].startswith("timeout:")
    wait_until(lambda: host.status()["scripts"]["t24_late"] == "ok", what="the late setup to finish", timeout=5)
    host.reload("t24_late")
    assert ("info", "late teardown") in fake.logs


def test_a_failed_reimport_does_not_run_the_old_teardown_twice(host, fake, tmp_path):
    body = """
    def setup(game):
        pass

    def teardown():
        import _skyrim
        _skyrim.log("info", "teardown ran")
    """
    script(tmp_path, "t24_twice", body)
    host.main()
    script(tmp_path, "t24_twice", "raise RuntimeError('broken now')  # a different size\n")
    assert host.reload("t24_twice").startswith("failed: RuntimeError")
    assert host.reload("t24_twice").startswith("failed: RuntimeError")
    assert fake.logs.count(("info", "teardown ran")) == 1


def test_reload_drops_only_modules_from_the_scripts_folder(host, fake, tmp_path):
    import skyrim
    package = sys.modules["skyrim"]
    script(tmp_path, "skyrim", "X = 1\n")  # clashes with the package: must never drop it
    script(tmp_path, "t24_keep", ECHO_SETUP)
    host.main()
    outside = types_module("t24_keep.outside", "/somewhere/else/outside.py")
    sys.modules["t24_keep.outside"] = outside
    host.reload("skyrim")
    assert sys.modules["skyrim"] is package and skyrim is package
    host.reload("t24_keep")
    assert sys.modules["t24_keep.outside"] is outside


def types_module(name, file):
    import types
    m = types.ModuleType(name)
    m.__file__ = file
    return m


def test_concurrent_reloads_never_run_setup_twice_at_once(host, fake, tmp_path):
    script(tmp_path, "t24_conc", """
    import builtins, time

    def setup(game):
        builtins.t24_active = getattr(builtins, "t24_active", 0) + 1
        builtins.t24_max = max(getattr(builtins, "t24_max", 0), builtins.t24_active)
        time.sleep(0.1)
        builtins.t24_active -= 1
    """)
    host.main()
    import builtins
    builtins.t24_max = 0
    try:
        threads = [threading.Thread(target=host.reload, args=("t24_conc",)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert builtins.t24_max == 1
    finally:
        for attr in ("t24_active", "t24_max"):
            if hasattr(builtins, attr):
                delattr(builtins, attr)
