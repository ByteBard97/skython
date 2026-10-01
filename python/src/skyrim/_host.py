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
