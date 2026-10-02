"""Linux side of livetest_smoke: its files live next to python.log (SKYTHON_LOG). With the game on the box
(SKYTHON_BOX=1, out/box-live.env) that is the local mirror out/box-skse/Skython: each read pulls the
file's text written since the launch stamp, and a command is pushed with tools/box/box_files.sh put-control."""
import json
import os
import pathlib
import subprocess
import time

from support import BOX, REPO, box_pull, fresh


def py_dir() -> pathlib.Path:
    return pathlib.Path(os.environ["SKYTHON_LOG"]).parent


def _read(name: str):
    if BOX:
        box_pull(f"Skython/{name}")
    try:
        return json.loads((py_dir() / name).read_text())
    except (OSError, ValueError):
        return None


def wait_smoke(timeout: float = 180.0) -> dict:
    """The smoke results of THIS game run (written after the last launch: support.fresh; on the box, box_pull)."""
    path = py_dir() / "livetest_smoke.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (data := _read(path.name)) and fresh(path):  # _read first: in box mode it pulls the mirror file
            return data
        time.sleep(0.5)
    raise AssertionError(f"no fresh {path} within {timeout} s: is livetest_smoke deployed "
                         "(tools/box/box_deploy.sh --scripts livetest/scripts) and a save loaded?")


def command(cmd: str, timeout: float = 300.0, **kw) -> dict:
    # Monotonic across launches: the game's control loop starts from the highest seq it finds on disk, and in box mode
    # the mirror holds only this launch's text, so a seq derived from the mirror could restart below it.
    seq = time.time_ns()
    path = py_dir() / "livetest_control.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"seq": seq, "cmd": cmd, **kw}))
    os.replace(tmp, path)
    if BOX:  # the only file Linux writes on the box
        subprocess.run([str(REPO / "tools" / "box" / "box_files.sh"), "put-control", str(path)], check=True,
                       capture_output=True, text=True, timeout=120)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = _read("livetest_reply.json")
        if r and r.get("seq") == seq:
            assert "error" not in r["result"], r["result"]
            return r["result"]
        time.sleep(0.2)
    raise AssertionError(f"livetest_smoke did not answer {cmd!r} within {timeout} s (see python.log)")
