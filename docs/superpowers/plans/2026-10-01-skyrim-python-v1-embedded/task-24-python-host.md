# Task 24: `skyrim._host`: logging, script loader, reload, bridge startup

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.5, 4.7 and 4.10). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 23 (`skyrim.bridge.serve`), Task 22 (`write_inactive_session_file`, `connect_remote` and `find_session` in the tests), Task 21 (`connect_in_process`, `fake_skyrim.py`, the `fake` fixture). Linux only. Task 26 calls `skyrim._host.main()` from the plugin's host thread.

**What this module is.** The Python side of the plugin's host thread. `main()` sets up logging, routes `print`, stderr and uncaught exceptions (main thread and other threads) into it, starts the dev bridge when `bridge_enabled` is set (before the scripts, so a slow script never delays the developer's tools), loads the player scripts and returns. It never raises into the plugin. Long work belongs on threads the scripts start; after `main()` returns, the host thread releases the GIL and waits forever (Global Constraints, Task 26).

**Decisions this task makes, and why.**
- **A script's import and `setup(game)` run on their own thread; the host thread waits up to `SETUP_TIMEOUT_S` (10 s).** A setup that blocks is reported as `"timeout: setup still running after 10 s"` and keeps running, and the next script still loads; a setup that finishes after `SETUP_WARN_S` (1 s) is `"ok"` with a warning in the log.
- **Each script gets its own `Game`** (`connect_in_process()` on the script's thread), so a script that closes its `Game` (`with game:` in `setup`) closes no one else's.
- **No game is loaded while `setup` runs.** In the game, `main()` runs at `kDataLoaded`, at the main menu, so a `needs_game` call in `setup` answers `NotReady`. The module docstring says so: game work belongs on a thread that `setup` starts and that first calls `game.wait_ready()`; a test pins that pattern.
- **The switch interval stays at CPython's default (0.005 s)**: `SWITCH_INTERVAL_S = None`. Spike S9 (Task 5) measures what a CPU-bound Python thread adds to other threads' game calls and records a recommendation in `spikes/RESULTS.md`; if it says `set 0.001`, change this one constant (the owner can veto). `status()` reports the interval in use and `main()` logs it.
- **Log files:** `python.log` in `_skyrim.config()["log_dir"]` (the folder that also holds `session.json`), rotating at 1 MiB with 3 backups, all levels from INFO; WARNING and above also go to the plugin's `SkyrimPython.log` through `_skyrim.log`. There is no console in the game, so stdout and stderr are replaced by line-buffered writers into the log.
- **A disabled bridge writes an inactive session file** (reason `bridge_enabled is false in SkyrimPython.ini`), so `skyrim.connect()` on Linux says why instead of following a stale port from an earlier run. A bridge that cannot start is reported in `status()` and the log, and the session file says why (`serve` writes `could not listen ...` itself on an `OSError`; for any other failure, such as a malformed `fixed_token`, `_host` writes `the bridge could not start: ...`); the scripts still load.
- **`sys.stdout` and `sys.stderr` are `io.TextIOBase` writers** (`encoding`, `writable()`), and each thread keeps its own partial line, because `print()` writes the text and the newline separately.

**Files:**
- Create: `python/src/skyrim/_host.py`, `python/tests/test_host.py`, `python/tests/test_real_module.py` (Step 6: the package against the real `_skyrim` from Task 19)

**Interfaces:**
- Consumes: the `_skyrim` API from the index Contracts (`config`, `state`, `log`, plus what `connect_in_process` and `serve` use), Task 23 (`skyrim.bridge.serve`), Task 22 (`skyrim.discovery.write_inactive_session_file`), Task 21 (`skyrim.inprocess.connect_in_process`), and in the tests the `fake` fixture (`FakeSkyrim` with `config_overrides`, `epoch`, `logs`, `requests`), `support.wait_until`, `connect_remote`, `find_session`.
- Produces (`skyrim._host`):
  - `main() -> None`: never raises; on a failure before logging is set up it reports the traceback through `_skyrim.log("error", ...)`.
  - `load_scripts(game, scripts_dir) -> dict[str, str]`: every `<name>.py` and `<name>/__init__.py` in name order (names starting with `_` or `.` skipped); name → `"ok"`, `"failed: <ExceptionType>: <message>"`, `"failed: no setup(game) function"` or `"timeout: setup still running after <n> s"`. Each script's `setup` gets a `Game` of its own; `game` is the host's, kept for `status` and `reload`. Appends the folder to `sys.path` if it is missing (in the game it is already on the search path), so a script's name must not clash with a standard-library or site-packages module.
  - `reload(name) -> str`: `teardown()` if the script has one (same timeout; a failing teardown is logged and the reload goes on), drops `name` and `name.*` from `sys.modules`, re-imports and calls `setup(game)`; returns the new status, or `"failed: no scripts have been loaded yet"` / `"failed: no script named ..."`. Best effort (spec 4.10).
  - `status() -> dict`: `scripts`, `bridge` (`"not started"`, `"disabled"`, `"listening on 127.0.0.1:<port>"` or `"failed: <why>"`), `bridge_port`, `bridge_connections`, `switch_interval_s`, `log_file`, `python`. Plain data.
  - Module constants: `SETUP_WARN_S = 1.0`, `SETUP_TIMEOUT_S = 10.0`, `SWITCH_INTERVAL_S = None`, `LOG_FILE_NAME = "python.log"`, `LOG_MAX_BYTES`, `LOG_BACKUPS`. `_reset()` is for tests only (closes the bridge, removes the log handlers, forgets everything).

- [ ] **Step 1: Write the failing tests**

`python/tests/test_host.py`:
```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd python && uv run --python 3.14 pytest -q tests/test_host.py`
Expected: FAIL with `18 errors`, each `ImportError: cannot import name '_host' from 'skyrim'` (the `host` fixture imports it).

- [ ] **Step 3: Implement the host module**

`python/src/skyrim/_host.py`:
```python
"""What the plugin runs inside the game. SkyrimPythonCore.dll (Task 26) imports this module on its host
thread right after Py_InitializeFromConfig and calls main() once.

main() sets up logging (python.log next to session.json, rotating 1 MiB x 3; WARNING and above also go to
the plugin's own log through _skyrim.log), sends stdout, stderr and uncaught exceptions to that log, starts
the dev bridge when bridge_enabled is set, loads the player scripts, and returns. It never raises: a failure
is logged and the game goes on. Long-running work lives on threads the scripts start; the host thread
itself releases the GIL once main() returns (Global Constraints).

Player scripts (spec 4.10): every module `<name>.py` and package `<name>/__init__.py` in the scripts folder,
in name order (names starting with "_" or "." are skipped), with `setup(game)` and an optional
`teardown()`. Each import + setup runs on its own thread while the host thread waits up to SETUP_TIMEOUT_S:
a script whose setup blocks is reported as "timeout: ..." and keeps running, and the next script still
loads. setup should return quickly (a setup over SETUP_WARN_S is logged) and start a thread for long work.
Each script gets its own Game, so a script that closes its Game (`with game:` in setup) does not close
anyone else's.
No game is loaded while setup runs: in the game, main() runs when the data has loaded, at the main menu,
so a needs_game call made in setup answers NotReady. Game work belongs on a thread that setup starts and
that first calls game.wait_ready().
A script name must not clash with a standard-library or site-packages module: the scripts folder comes
after them on sys.path."""
import importlib
import io
import json
import logging
import logging.handlers
import os
import pathlib
import sys
import threading
import time
import traceback

log = logging.getLogger("skyrim.host")

SETUP_WARN_S = 1.0
SETUP_TIMEOUT_S = 10.0
#: None keeps CPython's default switch interval (0.005 s). Spike S9 (Task 5) measures what a CPU-bound
#: Python thread costs other threads' game calls; if its RESULTS.md row recommends 0.001, set it here.
SWITCH_INTERVAL_S: float | None = None
LOG_FILE_NAME = "python.log"
LOG_MAX_BYTES = 1024 * 1024
LOG_BACKUPS = 3

_LOCK = threading.RLock()
_STATE: dict = {}


def _fresh_state() -> dict:
    return {"config": None, "game": None, "bridge": None, "bridge_status": "not started", "scripts": {},
            "modules": {}, "scripts_dir": None, "log_file": None}


_STATE.update(_fresh_state())


# -- logging ---------------------------------------------------------------------------------------------
class _PluginLogHandler(logging.Handler):
    """WARNING and above also go to the plugin's own log (SkyrimPython.log) through _skyrim.log."""

    def __init__(self, module):
        super().__init__(logging.WARNING)
        self._module = module

    def emit(self, record):
        try:
            self._module.log(record.levelname.lower(), self.format(record))
        except Exception:  # noqa: BLE001 - a logging failure must never reach the caller
            pass


class _StreamToLog(io.TextIOBase):
    """sys.stdout / sys.stderr replacement: whole lines go to a logger. In the game there is no console,
    so without this print() would go nowhere. A text stream (encoding, writable()) for libraries that
    inspect sys.stdout. Each thread keeps its own partial line, so print() calls from two threads (which
    write the text and the newline separately) never mix."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, logger: logging.Logger, level: int):
        super().__init__()
        self._logger, self._level = logger, level
        self._local = threading.local()  # .buf: this thread's partial line; .busy: inside our own write

    def write(self, text) -> int:
        local = self._local
        if getattr(local, "busy", False):  # logging reporting its own error to stderr: do not recurse
            return len(text)
        local.busy = True
        try:
            buf = getattr(local, "buf", "") + str(text)
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                if line:
                    self._logger.log(self._level, line)
            local.buf = buf
        finally:
            local.busy = False
        return len(text)

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _setup_logging(module, log_dir) -> str:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_skyrim_host", False):
            root.removeHandler(h)
            h.close()
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, LOG_FILE_NAME)
    fh = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s"))
    ph = _PluginLogHandler(module)
    ph.setFormatter(logging.Formatter("python: %(name)s: %(message)s"))
    for h in (fh, ph):
        h._skyrim_host = True
        root.addHandler(h)
    root.setLevel(logging.INFO)
    return path


def _install_hooks() -> None:
    def excepthook(exc_type, exc, tb):
        log.error("uncaught exception", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        log.error("uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    sys.stdout = _StreamToLog(logging.getLogger("stdout"), logging.INFO)
    sys.stderr = _StreamToLog(logging.getLogger("stderr"), logging.WARNING)


# -- scripts ---------------------------------------------------------------------------------------------
def _script_names(folder: pathlib.Path) -> list[str]:
    names = []
    for entry in folder.iterdir():
        if entry.name.startswith(("_", ".")):
            continue
        if entry.is_file() and entry.suffix == ".py" and entry.stem.isidentifier():
            names.append(entry.stem)
        elif entry.is_dir() and entry.name.isidentifier() and (entry / "__init__.py").is_file():
            names.append(entry.name)
    return sorted(set(names))


def _run_with_timeout(fn, thread_name: str, timeout: float) -> tuple[bool, str | None, str | None]:
    """Run fn() on its own thread and wait up to `timeout`. Returns (finished, error, traceback)."""
    box: dict = {}

    def run():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - SystemExit from a script is a failure, not an exit
            box["error"] = f"{type(e).__name__}: {e}"
            box["traceback"] = traceback.format_exc()

    t = threading.Thread(target=run, name=thread_name, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, None, None
    return True, box.get("error"), box.get("traceback")


def _load_one(name: str) -> str:
    from .inprocess import connect_in_process

    holder: dict = {}

    def run():
        module = importlib.import_module(name)
        holder["module"] = module
        setup = getattr(module, "setup", None)
        if not callable(setup):
            holder["missing"] = True
            return
        setup(connect_in_process())  # the script's own Game over _skyrim: closing it closes no one else's

    t0 = time.monotonic()
    finished, error, tb = _run_with_timeout(run, f"script:{name}", SETUP_TIMEOUT_S)
    took = time.monotonic() - t0
    if not finished:
        status = f"timeout: setup still running after {SETUP_TIMEOUT_S:g} s"
        log.error("script %s: %s (it keeps running; the other scripts still load)", name, status)
    elif error is not None:
        status = f"failed: {error}"
        log.error("script %s %s\n%s", name, status, tb)
    elif holder.get("missing"):
        status = "failed: no setup(game) function"
        log.error("script %s: %s", name, status)
    else:
        status = "ok"
        if took > SETUP_WARN_S:
            log.warning("script %s: setup took %.1f s; setup(game) should return quickly and start a thread "
                        "for long work", name, took)
        else:
            log.info("script %s: ok", name)
    with _LOCK:
        _STATE["scripts"][name] = status
        if "module" in holder:
            _STATE["modules"][name] = holder["module"]
    return status


def load_scripts(game, scripts_dir) -> dict[str, str]:
    """Import every script in `scripts_dir` and call its setup() with a Game of its own. Returns name -> "ok",
    "failed: <why>" or "timeout: <why>". A failing script never stops the others. `game` is the host's own
    Game, kept for status and reload."""
    folder = pathlib.Path(scripts_dir)
    with _LOCK:
        _STATE["game"] = game
        _STATE["scripts_dir"] = folder
    if not folder.is_dir():
        log.info("no scripts folder at %s", folder)
        return {}
    if str(folder) not in sys.path:
        sys.path.append(str(folder))  # in the game it is already on the search path (PyConfig)
    return {name: _load_one(name) for name in _script_names(folder)}


def reload(name: str) -> str:
    """Development aid, best effort (spec 4.10): teardown(), forget the script's modules, import it again
    and call setup(game). State in C extensions or in threads teardown() did not stop is not reset."""
    with _LOCK:
        game = _STATE["game"]
        module = _STATE["modules"].get(name)
        folder = _STATE.get("scripts_dir")
    if game is None or folder is None:
        return "failed: no scripts have been loaded yet"
    if name not in _script_names(folder):
        return f"failed: no script named {name!r} in {folder}"
    teardown = getattr(module, "teardown", None) if module is not None else None
    if callable(teardown):
        finished, error, tb = _run_with_timeout(teardown, f"script-teardown:{name}", SETUP_TIMEOUT_S)
        if not finished:
            log.error("script %s: teardown still running after %g s; reloading anyway", name, SETUP_TIMEOUT_S)
        elif error is not None:
            log.error("script %s: teardown failed: %s; reloading anyway\n%s", name, error, tb)
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]
    importlib.invalidate_caches()
    log.info("script %s: reloading", name)
    return _load_one(name)


# -- the bridge ------------------------------------------------------------------------------------------
def _start_bridge(module, cfg: dict) -> None:
    from .bridge import serve
    from .discovery import write_inactive_session_file

    session_file = cfg.get("session_file") or None
    if not cfg.get("bridge_enabled"):
        with _LOCK:
            _STATE["bridge_status"] = "disabled"
        if session_file:
            write_inactive_session_file(session_file, "bridge_enabled is false in SkyrimPython.ini",
                                        pid=os.getpid(), plugin_version=cfg.get("plugin_version"))
        log.info("bridge: disabled (bridge_enabled = false)")
        return

    def welcome_info() -> dict:
        state = json.loads(module.state())
        return {"plugin_version": cfg.get("plugin_version"), "game_version": cfg.get("game_version"),
                "runtime": cfg.get("runtime"), "epoch": state.get("epoch", 0)}

    try:
        bridge = serve(module, port=cfg["port"], token=cfg.get("fixed_token") or None, session_file=session_file,
                       welcome_info=welcome_info,
                       max_inflight_per_connection=cfg["max_inflight_per_connection"])
    except Exception as e:  # noqa: BLE001 - a bridge that cannot start is reported, the game goes on
        log.error("bridge: could not start: %s", e)
        with _LOCK:
            _STATE["bridge_status"] = f"failed: {e}"
        if session_file and not isinstance(e, OSError):  # serve() writes its own on OSError ("could not listen")
            write_inactive_session_file(session_file, f"the bridge could not start: {e}",
                                        pid=os.getpid(), plugin_version=cfg.get("plugin_version"))
        return
    with _LOCK:
        _STATE["bridge"] = bridge
        _STATE["bridge_status"] = f"listening on 127.0.0.1:{bridge.port}"


# -- entry points ----------------------------------------------------------------------------------------
def _main() -> None:
    import _skyrim

    from .inprocess import connect_in_process

    cfg = json.loads(_skyrim.config())
    with _LOCK:
        _STATE["config"] = cfg
        _STATE["log_file"] = _setup_logging(_skyrim, cfg["log_dir"])
    _install_hooks()
    if SWITCH_INTERVAL_S is not None:
        sys.setswitchinterval(SWITCH_INTERVAL_S)
    log.info("skyrim._host: Python %s, plugin %s, game %s (%s), switch interval %g s", sys.version.split()[0],
             cfg.get("plugin_version"), cfg.get("game_version"), cfg.get("runtime"), sys.getswitchinterval())
    game = connect_in_process(module=_skyrim)
    with _LOCK:
        _STATE["game"] = game
    _start_bridge(_skyrim, cfg)  # before the scripts, so a slow script never delays the developer's tools
    if cfg.get("scripts_enabled", True):
        results = load_scripts(game, cfg["scripts_dir"])
        log.info("skyrim._host: ready: %d of %d script(s) ok; bridge %s",
                 sum(1 for s in results.values() if s == "ok"), len(results), _STATE["bridge_status"])
    else:
        log.info("skyrim._host: ready: scripts disabled; bridge %s", _STATE["bridge_status"])


def main() -> None:
    """Called once by the plugin on its host thread. Returns after setup; never raises."""
    try:
        _main()
    except BaseException:  # noqa: BLE001 - nothing may propagate into the plugin's host thread
        text = "skyrim._host.main failed:\n" + traceback.format_exc()
        if _STATE.get("log_file"):
            log.error(text)  # the plugin-log handler forwards it too
            return
        try:
            import _skyrim
            _skyrim.log("error", text)
        except Exception:  # noqa: BLE001
            pass


def status() -> dict:
    """What the host did: script results, bridge state, the switch interval, where the log is."""
    with _LOCK:
        bridge = _STATE["bridge"]
        return {"scripts": dict(_STATE["scripts"]), "bridge": _STATE["bridge_status"],
                "bridge_port": bridge.port if bridge is not None else None,
                "bridge_connections": bridge.connections() if bridge is not None else 0,
                "switch_interval_s": sys.getswitchinterval(), "log_file": _STATE["log_file"],
                "python": sys.version.split()[0]}


def _reset() -> None:
    """Tests only: close the bridge, remove the log handlers, forget everything."""
    with _LOCK:
        bridge = _STATE["bridge"]
        _STATE.clear()
        _STATE.update(_fresh_state())
    if bridge is not None:
        bridge.close()
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_skyrim_host", False):
            root.removeHandler(h)
            h.close()
```

- [ ] **Step 4: Run to verify they pass, on both Python versions**

Run: `cd python && uv run --python 3.12 pytest -q && uv run --python 3.14 pytest -q`
Expected: `337 passed` twice, each in about 10 s (Task 23's 319 plus this task's 18). The blocking-setup test leaves a daemon thread sleeping for 3 s after it ends; that is intended (it shows main() returning while a script is still stuck).

Run the host, bridge and remote tests together five times, to catch races between them:
`for i in 1 2 3 4 5; do uv run --python 3.14 pytest -q tests/test_bridge.py tests/test_host.py tests/test_remote.py; done`
Expected: `97 passed` five times.

- [ ] **Step 5: Run the drift check**

Run: `python3 tools/gen_protocol.py --check`
Expected: `gen_protocol: 3 generated files are up to date`.

- [ ] **Step 6: Cross-check the whole package against the real `_skyrim`**

Every Python test so far runs against `fake_skyrim`, a Python copy of the Dispatcher rules. This test runs the in-process transport, `skyrim.bridge` and `skyrim.remote` against the real module built in Task 19 (the C++ `Dispatcher` over `FakeBackend`), so the two cannot drift apart silently. It skips unless `SKYRIM_REAL_MODULE_DIR` is set. The GIL test makes the call on a worker thread and checks, while that call is still in flight, that this thread saw it in flight and that a spinner thread advanced. (Checked while writing this plan: with the `release()` guard removed from `_skyrim.call` it fails, because the in-flight wait times out; the earlier version, which counted spins around the whole call, still passed.) The `real` fixture restores `SKYRIM_FAKE_DIR` and `sys.modules["_skyrim"]` when the file is done, so later test files see the fake again.

`python/tests/test_real_module.py`:
```python
"""The skyrim package against the REAL _skyrim (C++ Dispatcher + FakeBackend, Task 19), not fake_skyrim.

Skipped unless SKYRIM_REAL_MODULE_DIR names the folder holding the built _skyrim*.so
(pyglue/build after Task 19). It pins fake_skyrim's behaviour to the real Dispatcher's.
"""
import importlib
import json
import os
import socket
import sys
import threading
import time

import pytest

from support import wait_until
from skyrim import _framing, bridge, errors
from skyrim.inprocess import connect_in_process
from skyrim.remote import connect_remote

MODULE_DIR = os.environ.get("SKYRIM_REAL_MODULE_DIR")
pytestmark = pytest.mark.skipif(not MODULE_DIR, reason="SKYRIM_REAL_MODULE_DIR not set")
TOKEN = "ab" * 16


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    """The real module, imported for this file only: SKYRIM_FAKE_DIR and sys.modules["_skyrim"] are restored
    afterwards, so later tests see the fake again."""
    before = sys.modules.pop("_skyrim", None)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SKYRIM_FAKE_DIR", str(tmp_path_factory.mktemp("real")))
        sys.path.insert(0, MODULE_DIR)
        try:
            mod = importlib.import_module("_skyrim")
        finally:
            sys.path.remove(MODULE_DIR)
        try:
            assert mod.BACKEND == "fake"
            yield mod
            mod._fake_set_not_ready(None)
        finally:
            sys.modules.pop("_skyrim", None)
            if before is not None:
                sys.modules["_skyrim"] = before


@pytest.fixture
def game(real):
    with connect_in_process(module=real) as g:
        yield g


def test_round_trips(game):
    assert game.call("Fake", "Echo", 7) == 7
    assert game.call("Fake", "Echo", "hé") == "hé"
    assert game.call("Fake", "Echo", [1, 2, 3]) == [1, 2, 3]
    assert game.state()["backend"] == "fake"


def test_errors_map_to_the_same_classes(game):
    with pytest.raises(errors.NotFound):
        game.call("Fake", "Fail", "not_found", "nope")
    with pytest.raises(errors.InternalError):
        game.call("Fake", "Throw")
    with pytest.raises(errors.UnknownFunction):
        game.call("Fake", "Nope")
    with pytest.raises(errors.TypeMismatch):
        game.call("Fake", "Echo", 2**31)


def test_timeout_carries_game_state(game):
    with pytest.raises(errors.Timeout) as e:
        game.call("Fake", "Never", timeout=0.3)
    assert {"paused", "menus"} <= set(e.value.detail)


def test_not_ready_then_ready(game, real):
    real._fake_set_not_ready("loading")
    try:
        with pytest.raises(errors.NotReady) as e:
            game.call("Fake", "Echo", 1)
        assert e.value.detail["reason"] == "loading"
        assert game.state()["backend"] == "fake"     # game.state still answers
    finally:
        real._fake_set_not_ready(None)
    assert game.call("Fake", "Echo", 1) == 1


def test_a_load_fails_calls_in_flight(game, real):
    out = {}

    def slow():
        try:
            out["v"] = game.call("Fake", "Sleep", 1500, timeout=5)
        except Exception as exc:  # noqa: BLE001
            out["e"] = exc

    t = threading.Thread(target=slow)
    t.start()
    wait_until(lambda: real.in_flight() == 1, what="the call to be in flight")
    real._fake_bump_loads()
    t.join(5)
    assert isinstance(out.get("e"), errors.NotReady)
    assert out["e"].detail["dispatched"] is True


def test_the_gil_is_released_while_waiting(game, real):
    # The call runs on a worker. This thread can only see it in flight, and the spinner can only advance, while
    # the worker is inside _skyrim.call if call released the GIL; before and after the call they always could.
    count, stop = [0], [False]

    def spin():
        while not stop[0]:
            count[0] += 1

    caller = threading.Thread(target=lambda: game.call("Fake", "Sleep", 1000))
    spinner = threading.Thread(target=spin)
    spinner.start()
    caller.start()
    try:
        wait_until(lambda: real.in_flight() == 1, what="the call to be in flight")
        before = count[0]
        time.sleep(0.1)
        advanced = count[0] - before
        assert real.in_flight() == 1  # still inside the call
        assert advanced > 0
    finally:
        caller.join(5)
        stop[0] = True
        spinner.join()


def test_bridge_over_the_real_module(real):
    srv = bridge.serve(real, port=0, token=TOKEN,
                       welcome_info={"plugin_version": "0.0.0-fake", "game_version": "0.0.0", "runtime": "fake", "epoch": 0})
    try:
        with connect_remote(port=srv.port, token=TOKEN) as g:
            assert g.call("Fake", "Echo", [1.5, 2.5]) == [1.5, 2.5]
            with pytest.raises(errors.Timeout):
                g.call("Fake", "Never", timeout=0.3)
        # A client that disconnects with calls in flight leaves nothing behind.
        s = socket.create_connection(("127.0.0.1", srv.port))
        s.sendall(_framing.encode({"type": "hello", "protocol": [1, 0], "token": TOKEN, "client": "t"}))
        assert _framing.read_frame(s, time.monotonic() + 3)["type"] == "welcome"
        for i in range(1, 6):
            s.sendall(_framing.encode({"type": "request", "id": i, "op": "papyrus.call_static",
                                       "args": {"script": "Fake", "function": "Never", "args": [], "timeout_ms": 60000}}))
        deadline = time.monotonic() + 3
        while real.in_flight() < 5 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert real.in_flight() >= 5
        s.close()
        deadline = time.monotonic() + 3
        while real.in_flight() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert real.in_flight() == 0
    finally:
        srv.close()
```

Run (the module is the one Task 19 built; rebuild it first if `core/` or `pyglue/` changed):
```bash
cmake --build pyglue/build -j
(cd python && SKYRIM_REAL_MODULE_DIR="$PWD/../pyglue/build" uv run --python 3.14 pytest tests/test_real_module.py -q)
(cd python && uv run --python 3.12 pytest tests/test_real_module.py -q)
```
Expected: `7 passed` on 3.14; `7 skipped` on 3.12 (the module is built for 3.14 only). If a test fails here but its `fake_skyrim` counterpart passes, the fake has drifted from the real Dispatcher: fix `fake_skyrim.py` (Task 21) to match the C++ behaviour, never the other way round.

- [ ] **Step 7: Commit**

```bash
git add python/src/skyrim/_host.py python/tests/test_host.py python/tests/test_real_module.py
git commit -m "feat(python): skyrim._host: python.log, player script loader with timeouts and reload, dev bridge startup"
```
