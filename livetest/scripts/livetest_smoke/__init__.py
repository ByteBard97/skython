"""In-process smoke test and benchmark (Task 31). Deployed to Data/SKSE/Plugins/Skython/scripts/ by
`tools/box/box_deploy.sh --scripts livetest/scripts`. Development only: never ship it to players.

setup() starts one thread and returns at once. That thread waits for a loaded save, runs the same calls the
live tests make, writes livetest_smoke.json next to python.log, then serves commands from livetest_control.json.
"""
import json
import logging
import os
import pathlib
import statistics
import sys
import threading
import time

import _skython
from skython import _host, errors
from skython.values import Form

log = logging.getLogger("livetest_smoke")
PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"

_stop = threading.Event()
_spin_stop = threading.Event()
_spin_thread = None
_spin_loops = 0


def _dir() -> pathlib.Path:
    return pathlib.Path(json.loads(_skython.config())["log_dir"])


def _read(name: str):
    try:
        return json.loads((_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def _write(name: str, obj) -> None:
    path = _dir() / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def _expect(exc, fn) -> None:
    try:
        fn()
    except exc:
        return
    raise AssertionError(f"expected {exc.__name__}")


def _checks(game):
    def state():
        st = game.state()
        assert st["interactive"] is True and st["player"].form_id == PLAYER_ID, st

    def call_static_int():
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6

    def call_static_form():
        assert game.call("Game", "GetPlayer").form_id == PLAYER_ID

    def call_method_float():
        assert game.call_method(game.call("Game", "GetPlayer"), "Actor", "GetActorValue", "Health") > 0

    def resolve():
        assert game.form(IRON_SWORD).type == "Weapon"

    def latent():
        assert game.call("Utility", "Wait", 0.2) is None

    def unknown_function():
        _expect(errors.UnknownFunction, lambda: game.call("NoSuchScriptAnywhere", "Foo"))

    def type_mismatch():
        _expect(errors.TypeMismatch, lambda: game.call("Utility", "RandomInt", "one", 6))

    def threads():
        out = []
        workers = [threading.Thread(target=lambda: out.append(game.call("Utility", "RandomInt", 1, 6))) for _ in range(8)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(30)
        assert len(out) == 8 and all(1 <= x <= 6 for x in out), out

    return [state, call_static_int, call_static_form, call_method_float, resolve, latent, unknown_function,
            type_mismatch, threads]


def run_smoke(game) -> list:
    results = []
    for check in _checks(game):
        try:
            check()
            results.append({"name": check.__name__, "ok": True})
        except Exception as e:  # recorded, never raised: this runs inside the game
            results.append({"name": check.__name__, "ok": False, "error": f"{type(e).__name__}: {e}"})
    return results


def _spin() -> None:
    global _spin_loops
    while not _spin_stop.is_set():
        x = 0
        for i in range(20000):  # pure Python: holds the GIL until the interpreter's switch interval forces a hand-off
            x += i * i
        _spin_loops += 1


def _bench(game, calls: int) -> dict:
    lat = []
    t0 = time.perf_counter()
    for _ in range(calls):
        c0 = time.perf_counter()
        game.call("Utility", "RandomInt", 1, 6)
        lat.append((time.perf_counter() - c0) * 1000)
    secs = time.perf_counter() - t0
    lat.sort()
    return {"calls": calls, "seconds": round(secs, 3), "per_s": round(calls / secs, 1),
            "p50_ms": round(statistics.median(lat), 2), "p99_ms": round(lat[int(len(lat) * 0.99)], 2),
            "spinning": bool(_spin_thread and _spin_thread.is_alive())}


def _sweep(cmd: dict) -> dict:
    """Task 38: sequential Utility.RandomInt calls, `calls` per pause in `sleeps_ms`, the pause before each call.
    Straight through _skython.call with request ids from id_base, so the plugin's timing lines join with them, and
    stamped with _skython.now_us (the plugin's clock). No Game: its ids would collide with other clients'."""
    calls, base, rows, n = int(cmd.get("calls", 100)), int(cmd["id_base"]), [], 0
    for sleep_ms in cmd["sleeps_ms"]:
        for _ in range(calls):
            n += 1
            text = json.dumps({"type": "request", "id": base + n, "op": "papyrus.call_static",
                               "args": {"script": "Utility", "function": "RandomInt", "args": [1, 6]}})
            if sleep_ms:
                time.sleep(sleep_ms / 1000)
            start = _skython.now_us()
            resp = json.loads(_skython.call(text, 30.0))
            end = _skython.now_us()
            if resp.get("ok") is not True:
                return {"error": f"sweep call {base + n} failed: {resp.get('error')}"}
            rows.append([base + n, sleep_ms, start, end])
    return {"calls": rows}


def _mod_event(game, event_name: str) -> dict:
    """In-process: subscribe through Game.on_mod_event, raise the event with Form.SendModEvent, report what arrived."""
    got: list = []
    done = threading.Event()

    def on_event(ev) -> None:
        sender = ev.data.get("sender")
        got.append({"name": ev.data["name"], "str_arg": ev.data["str_arg"], "num_arg": ev.data["num_arg"],
                    "sender": sender.form_id if sender is not None else None, "seq": ev.seq, "epoch": ev.epoch})
        done.set()

    with game.on_mod_event(event_name, on_event):
        game.call_method(Form(form_id=0x14), "Form", "SendModEvent", event_name, "in-process", 2.5)
        done.wait(10)
    return {"received": got}


def _handle(game, cmd: dict) -> dict:
    global _spin_thread, _spin_loops
    name = cmd.get("cmd")
    if name == "spin_on":
        if not (_spin_thread and _spin_thread.is_alive()):
            _spin_stop.clear()
            _spin_loops = 0
            _spin_thread = threading.Thread(target=_spin, name="livetest_spin", daemon=True)
            _spin_thread.start()
        return {"spinning": True}
    if name == "spin_off":
        _spin_stop.set()
        if _spin_thread:
            _spin_thread.join(10)
        return {"spinning": False, "spin_loops": _spin_loops}
    if name == "bench":
        return _bench(game, int(cmd.get("calls", 300)))
    if name == "sweep":
        return _sweep(cmd)
    if name == "mod_event":
        return _mod_event(game, str(cmd.get("event", "skython_smoke")))
    return {"error": f"unknown command {name!r}"}


def _control_loop(game) -> None:
    # Commands written before this run (an older control file) are never replayed.
    last = max((_read("livetest_control.json") or {}).get("seq", 0), (_read("livetest_reply.json") or {}).get("seq", 0))
    while not _stop.wait(0.2):
        cmd = _read("livetest_control.json")
        if not cmd or cmd.get("seq", 0) <= last:
            continue
        last = cmd["seq"]
        try:
            result = _handle(game, cmd)
        except Exception as e:
            log.exception("livetest_smoke: command %r failed", cmd)
            result = {"error": f"{type(e).__name__}: {e}"}
        _write("livetest_reply.json", {"seq": last, "result": result})


def _main(game) -> None:
    try:
        while not _stop.is_set():
            try:
                game.wait_ready(timeout=30)
                break
            except Exception:  # no save loaded yet (or an error raised at once): wait, never spin holding the GIL
                if _stop.wait(1.0):
                    return
        if _stop.is_set():
            return
        checks = run_smoke(game)
        ok = all(c["ok"] for c in checks)
        _write("livetest_smoke.json", {"status": "pass" if ok else "fail", "checks": checks,
                                       # Evidence of the shipping mode: the game's _skython and the in-process transport.
                                       "backend": _skython.BACKEND, "transport": type(game.transport).__name__,
                                       "scripts": _host.status()["scripts"],  # how the host loaded every script, this run
                                       "thread": threading.current_thread().name,
                                       "switch_interval_s": sys.getswitchinterval(), "time": time.time()})
        _skython.log("info" if ok else "error",
                    f"livetest_smoke: {'PASS' if ok else 'FAIL'} ({sum(c['ok'] for c in checks)}/{len(checks)} checks)")
        _control_loop(game)
    except Exception:
        log.exception("livetest_smoke: crashed")


def setup(game) -> None:
    _stop.clear()
    threading.Thread(target=_main, args=(game,), name="livetest_smoke", daemon=True).start()


def teardown() -> None:
    _stop.set()
    _spin_stop.set()
