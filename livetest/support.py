import json
import os
import pathlib
import socket
import struct
import subprocess
import threading
import time
from dataclasses import dataclass

MAX_FRAME = 16 * 1024 * 1024
REPO = pathlib.Path(__file__).resolve().parent.parent
# The game runs on the Windows test machine: out/box-live.env sets SKYTHON_BOX=1, SKSE_LOG_DIR and
# SKYTHON_LOG to the local mirror out/box-skse, and SKYTHON_SESSION to the tunnel's session file.
BOX = os.environ.get("SKYTHON_BOX") == "1"


def spy_env(*names: str) -> tuple[str, ...]:
    """The SPY_* values a live test needs (livetest/s4test.env holds the ones for the test machine's s4test save). Under
    SKYTHON_LIVE=1 a missing one FAILS the test: a live run must not pass by skipping. Opt-in tests (SPY_SAVE_TEST,
    SPY_MANUAL, SPY_VARIANT, SPY_LOW_CAP) gate themselves with skipif and do not use this."""
    import pytest

    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        why = f"{', '.join(missing)} not set: source livetest/s4test.env (values for the s4test save on the test machine)"
        if os.environ.get("SKYTHON_LIVE") == "1":
            pytest.fail(why)
        pytest.skip(why)
    return tuple(os.environ[n] for n in names)


@dataclass
class Session:
    port: int
    token: str


def load_session() -> Session:
    path = os.environ.get("SKYTHON_SESSION")
    if not path:
        raise RuntimeError("SKYTHON_SESSION is not set (tools/box/box_session.sh up && source out/box-live.env)")
    data = json.loads(pathlib.Path(path).read_text())
    if data.get("active") is False:
        raise RuntimeError(f"the bridge is not serving: {data.get('reason')}")
    return Session(port=int(data["port"]), token=str(data["token"]))


def encode(obj) -> bytes:
    body = json.dumps(obj, separators=(",", ":")).encode()
    return struct.pack("<I", len(body)) + body


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("connection closed")
        buf += chunk
    return buf


def read_frame(sock: socket.socket) -> dict:
    (n,) = struct.unpack("<I", _read_exact(sock, 4))
    return json.loads(_read_exact(sock, n))


def hello(session: Session, **override) -> bytes:
    msg = {"type": "hello", "protocol": [1, 0], "token": session.token, "client": "livetest-raw"}
    msg.update(override)
    return encode(msg)


def raw_connect(session: Session) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall(hello(session))
    assert read_frame(s)["type"] == "welcome"
    return s


def send_request(sock: socket.socket, op: str, args: dict | None = None, req_id: int = 1) -> None:
    sock.sendall(encode({"type": "request", "id": req_id, "op": op, "args": args or {}}))


def rpc(sock: socket.socket, op: str, args: dict | None = None, req_id: int = 1, timeout: float = 30.0) -> dict:
    """Send one request and return the reply with that id (replies may arrive out of order)."""
    sock.settimeout(timeout)
    send_request(sock, op, args, req_id)
    while True:
        r = read_frame(sock)
        if r.get("id") == req_id:
            return r


def send_raw(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def closed_by_server(sock: socket.socket, timeout: float = 3.0) -> bool:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if sock.recv(65536) == b"":
                return True
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except socket.timeout:
        return False
    return False


def frame_header(n: int) -> bytes:
    return struct.pack("<I", n)


def read_frame_or_none(sock: socket.socket, timeout: float = 3.0):
    sock.settimeout(timeout)
    try:
        return read_frame(sock)
    except Exception:  # noqa: BLE001 - EOF, reset, timeout or a truncated frame all mean "no frame"
        return None


def still_serving(session: Session) -> bool:
    raw_connect(session).close()
    return True


def _box_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(REPO / "tools" / "box" / script), *args], capture_output=True, text=True, timeout=180)


def box_pull(*names: str) -> None:
    """Box mode: refresh out/box-skse/<name> with the text written on the test machine since the last launch (empty if none)."""
    r = _box_tool("box_files.sh", "pull", *names)
    if r.returncode != 0:
        raise RuntimeError(f"box_files.sh pull {' '.join(names)} failed: {r.stderr.strip()}")


def _skse_dir() -> pathlib.Path | None:
    d = os.environ.get("SKSE_LOG_DIR")
    return pathlib.Path(d) if d else None


def _launch_stamp() -> pathlib.Path:
    """tools/launch_game.sh touches launch.stamp; tools/box/box_launch.sh writes box-launch.stamp (box mode)."""
    state = os.environ.get("SKYTHON_STATE") or str(
        pathlib.Path(os.environ.get("XDG_STATE_HOME") or pathlib.Path.home() / ".local" / "state") / "skython")
    return pathlib.Path(state) / ("box-launch.stamp" if BOX else "launch.stamp")


def fresh(path: pathlib.Path) -> bool:
    """True when the file was written after the last launch stamp (so it belongs to this run). In box mode the mirror
    out/box-skse holds only text the test machine wrote since its launch stamp (box_files.sh pull), and is newer than any stamp.
    In a live run (SKYTHON_LIVE=1 or box mode) a missing stamp is an error: any old file would otherwise count."""
    stamp = _launch_stamp()
    if not stamp.exists():
        if BOX or os.environ.get("SKYTHON_LIVE") == "1":
            raise RuntimeError(f"no launch stamp at {stamp}: cannot tell this run's logs from older ones "
                               "(launch with tools/box/box_launch.sh, or set SKYTHON_STATE)")
        return path.exists()
    return path.exists() and path.stat().st_mtime >= stamp.stat().st_mtime


def log_text(name: str = "Skython.log") -> str:
    """A log in SKSE_LOG_DIR from this run, or '' (log assertions then fail with a clear message)."""
    if BOX:
        box_pull(name)
    d = _skse_dir()
    p = d / name if d else None
    return p.read_text(errors="replace") if p and fresh(p) else ""


def python_log_text() -> str:
    if BOX:
        box_pull("Skython/python.log")
    p = pathlib.Path(os.environ.get("SKYTHON_LOG", ""))
    return p.read_text(errors="replace") if p.name and fresh(p) else ""


def refused(port: int = 45977) -> bool:
    if BOX:  # through the ssh tunnel a port nobody listens on is accepted locally, then closed: ask the test machine
        r = _box_tool("box_session.sh", "probe", str(port))
        if "NO_LISTENER" in r.stdout:
            return True
        if r.stdout.startswith("LISTEN"):
            return False
        raise RuntimeError(f"box_session.sh probe {port} failed: {r.stdout}{r.stderr}")
    try:
        socket.create_connection(("127.0.0.1", port), timeout=3).close()
    except (ConnectionRefusedError, socket.timeout, OSError):
        return True
    return False


def diagnostics(session) -> dict:
    """game.state's diagnostics over a fresh raw connection (late_results_dropped, in_flight, and the smoke script's drain meter)."""
    s = raw_connect(session)
    try:
        return rpc(s, "game.state")["result"]["diagnostics"]
    finally:
        s.close()


# Other clients (e.g. an example script left in the scripts folder, which polls every second) keep a few calls of
# their own in flight; in_flight checks allow this much either way. Far below the test calls' counts (32, 1024).
OTHER_CLIENTS_SLACK = 8


def steady_late_count(session, settle: float = 1.0, tries: int = 10) -> int:
    """late_results_dropped once two readings `settle` seconds apart agree: nothing late is still on its way."""
    prev = diagnostics(session)["late_results_dropped"]
    for _ in range(tries):
        time.sleep(settle)
        now = diagnostics(session)["late_results_dropped"]
        if now == prev:
            return now
        prev = now
    raise AssertionError("late_results_dropped kept changing")


def subscribe(sock: socket.socket, event: str, filter: dict | None = None, req_id: int = 1,
              max_queue: int | None = None) -> int:
    """events.subscribe on a raw connection; returns the sub id. Event frames that arrive meanwhile are dropped by rpc,
    so subscribe before triggering anything."""
    args: dict = {"event": event}
    if filter is not None:
        args["filter"] = filter
    if max_queue is not None:
        args["max_queue"] = max_queue
    r = rpc(sock, "events.subscribe", args, req_id)
    assert r["ok"] is True, r
    return r["result"]["sub"]


def read_event(sock: socket.socket, timeout: float) -> dict | None:
    """The next event frame within timeout seconds (responses are skipped), or None."""
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            f = read_frame(sock)
        except (socket.timeout, TimeoutError):
            return None
        if f.get("type") == "event":
            return f
    return None


def read_events(sock: socket.socket, n: int, timeout: float) -> list[dict]:
    """Up to n event frames within timeout seconds in total."""
    out: list[dict] = []
    deadline = time.monotonic() + timeout
    while len(out) < n and (left := deadline - time.monotonic()) > 0:
        if (f := read_event(sock, left)) is None:
            break
        out.append(f)
    return out


# ---- World helpers. Everything here runs on s4test and is never saved. ---------------------------
from skython import errors as _errors  # noqa: E402
from skython.values import Form as _Form  # noqa: E402

PLAYER = _Form(form_id=0x14)


def spawn(game, base_key: str, dx: float = 600.0):
    """A new reference of base_key, dx units from the player (PlaceAtMe takes every argument: no defaults)."""
    ref = game.call_method(PLAYER, "ObjectReference", "PlaceAtMe", game.form(base_key), 1, False, False)
    game.call_method(ref, "ObjectReference", "MoveTo", PLAYER, dx, 0.0, 0.0, True)
    return ref


def cleanup(game, *refs) -> None:
    for r in refs:
        try:
            game.call_method(r, "ObjectReference", "Disable", False)
            game.call_method(r, "ObjectReference", "Delete")
        except _errors.SkythonError:
            pass


def neutral_fight(game, a, b, aggression: float = 0.0, health: float = 5000.0) -> None:
    """a and b fight each other and leave the player alone: the player is a ghost (undo with
    game.call_method(PLAYER, "Actor", "SetGhost", False)), both get `aggression`, then StartCombat both ways.
    Aggression 0 keeps them off the player but may let them drop out of a forced fight; the tests retry once with
    aggression 3 (frenzied: still harmless to a ghost) when no hit arrives.
    Both leave every faction first (s4test is next to Whiterun: guards attacked the wolves as predators and killed
    them in 2-4 s, so they never hit each other) and get `health` (wolves at 22 Health killed each other within
    about 6 s; the flood test needs 30 s of combat). Measured live."""
    game.call_method(PLAYER, "Actor", "SetGhost", True)
    for x in (a, b):
        game.call_method(x, "Actor", "RemoveFromAllFactions")
        game.call_method(x, "Actor", "SetActorValue", "Health", health)
        game.call_method(x, "Actor", "SetActorValue", "Aggression", aggression)
    game.call_method(a, "Actor", "StartCombat", b)
    game.call_method(b, "Actor", "StartCombat", a)


def box_menu(action: str, name: str) -> None:
    """Open or close a menu through DevBench on the test machine; no player input."""
    body = json.dumps({"action": action, "name": name})
    subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "--tool", "menu", body], check=True,
                   capture_output=True, text=True, timeout=90)


def translate_player(game, dx: float, dy: float, dz: float, speed: float = 3000.0, settle: float = 6.0) -> tuple:
    """Glide the player by (dx, dy, dz) with TranslateTo (no MoveTo, so no Loading Menu), wait, stop. Returns the
    position before the move."""
    x, y, z = (game.call_method(PLAYER, "ObjectReference", f"GetPosition{c}") for c in "XYZ")
    game.call_method(PLAYER, "ObjectReference", "TranslateTo", x + dx, y + dy, z + dz, 0.0, 0.0, 0.0, speed, 0.0)
    time.sleep(settle)
    game.call_method(PLAYER, "ObjectReference", "StopTranslation")
    return x, y, z


# ---- Saves. New names only (skython_*), each checked unused first and recorded for later cleanup. -----------
import re as _re  # noqa: E402

LOADABLE = _re.compile(r"s4test|spikebase|skythonbase|skython_[A-Za-z0-9_]{1,56}")


def box_save_count(name: str) -> int:
    """How many saves on the test machine match NAME*.ess (tools/box/box_saves.sh: a prefix match, so no new name may be a
    prefix of another: every name made here ends in a 14-digit timestamp)."""
    r = subprocess.run([str(REPO / "tools" / "box" / "box_saves.sh"), "count", name], check=True,
                       capture_output=True, text=True, timeout=90)
    return int(r.stdout.strip())


def new_save_name(tag: str) -> str:
    """skython_<tag>_<timestamp>, checked unused on the test machine (count 0) and appended to livetest/results/new_saves.txt
    so they can be cleaned up later."""
    name = f"skython_{tag}_{time.strftime('%Y%m%d%H%M%S')}"
    assert _re.fullmatch(r"[A-Za-z0-9_]{1,64}", name), name
    n = box_save_count(name)
    assert n == 0, f"STOP: a save named {name} already exists on the test machine (count {n}); never save over a save"
    out = REPO / "livetest" / "results" / "new_saves.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(name + "\n")
    return name


def wait_save_file(name: str, timeout: float = 30.0) -> None:
    """kSaveGame may come before the .ess is written: poll the test machine until exactly one file matches."""
    deadline = time.monotonic() + timeout
    while (n := box_save_count(name)) != 1:
        assert n == 0, f"{n} saves match {name}*"
        assert time.monotonic() < deadline, f"STOP: {name}.ess did not appear within {timeout} s"
        time.sleep(2.0)


def _creations_prompt_open() -> bool:
    """True while the game shows the "Creations are currently loaded" Yes/No prompt: loading a save made without
    plugins while the test ESP is active opens it before anything loads, and the load waits for its answer."""
    try:
        r = _box_tool("box_console.sh", "--tool", "menu", '{"action":"describe"}')
    except Exception:  # noqa: BLE001 - a stalled DevBench means "not known yet"
        return False
    return r.returncode == 0 and "creations are currently loaded" in r.stdout.lower()


def _accept_creations_prompt() -> None:
    """Answer Yes (button 0) to the creations prompt through DevBench; no player input."""
    subprocess.run([str(REPO / "tools" / "box" / "box_console.sh"), "--tool", "menu",
                    '{"action":"accept","index":0}'], check=True, capture_output=True, text=True, timeout=90)


def load_save(game, name: str, timeout: float = 120.0) -> dict:
    """Load one of the saves these tests or the spikes made (s4test, spikebase, skythonbase, skython_*) through Game.load and
    wait until the game is interactive again with a higher epoch. Returns that state. On the test machine a save made
    without plugins (s4test, spikebase) loaded while the test ESP is active opens the "Creations are currently loaded"
    prompt before anything loads, so the load runs on a thread while this one answers the prompt through DevBench."""
    if not LOADABLE.fullmatch(name):
        raise ValueError(f"tests load only s4test, spikebase, skythonbase or skython_* saves, not {name!r}")
    if not BOX:
        return game.load(name, timeout=timeout, wait=True)
    outcome: dict = {}

    def loader():
        try:
            outcome["state"] = game.load(name, timeout=timeout, wait=True)
        except Exception as e:  # noqa: BLE001 - re-raised on this thread below
            outcome["error"] = e

    t = threading.Thread(target=loader, name="load-save", daemon=True)
    t.start()
    answered = 0
    while t.is_alive():
        t.join(2.0)
        if t.is_alive() and _creations_prompt_open():
            _accept_creations_prompt()
            answered += 1
    t.join()
    if "error" in outcome:
        raise outcome["error"]
    if answered:
        print(f"load {name}: answered the creations-are-loaded prompt {answered} time(s)")
    return outcome["state"]
