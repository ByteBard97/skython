import json
import pathlib
import time

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "protocol" / "v1" / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def wait_until(predicate, timeout=5.0, what="the condition"):
    """Poll with a deadline; never spin forever."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        time.sleep(0.01)


# ---- Task 22: a protocol-v1 socket server for the remote transport's tests -------------------------------
import socket  # noqa: E402
import threading  # noqa: E402
import traceback  # noqa: E402

from skython import _framing  # noqa: E402
from skython.errors import ConnectionLost, ProtocolViolation  # noqa: E402

TOKEN = "00112233445566778899aabbccddeeff"
WELCOME = {"protocol": [1, 0], "plugin_version": "0.1.0", "game_version": "1.6.1170.0",
           "runtime": "AE", "epoch": 1, "capabilities": ["game.state"]}


def ok(req_id, result):
    return {"type": "response", "id": req_id, "ok": True, "result": result}


def err(req_id, code, message="", detail=None):
    return {"type": "response", "id": req_id, "ok": False,
            "error": {"code": code, "message": message, "detail": detail or {}}}


def echo_handler(req, reply):
    reply(ok(req["id"], {"op": req["op"], "args": req["args"]}))


def hello_problems(hello):
    """What is wrong with a client hello, per protocol/PROTOCOL.md (empty list = fine)."""
    problems = []
    if hello.get("type") != "hello":
        problems.append("type is not 'hello'")
    proto = hello.get("protocol")
    if not (isinstance(proto, list) and len(proto) == 2 and all(type(n) is int for n in proto)):
        problems.append(f"protocol is not [major, minor]: {proto!r}")
    if not isinstance(hello.get("token"), str):
        problems.append("token is not a string")
    if not isinstance(hello.get("client"), str):
        problems.append("client is not a string")
    return problems


class FakeServer:
    """A protocol-v1 server on a random loopback port, one thread per connection: it stands in for the
    dev bridge (Task 23) so the remote transport can be driven into every failure a real peer could cause.

    modes: "normal"; "hang" (accept, read the hello, never answer); "mismatch" (handshake_error
    protocol_mismatch); "garbage" (answer with a non-protocol byte stream); "drip" (send the welcome one
    byte at a time); "partial" (send part of the welcome frame, then stop); "stall" (complete the handshake,
    then never read another byte, with a tiny receive buffer, so the client's sends back up).
    `welcome` overrides fields of the welcome message. Exceptions in the handler, and a malformed hello, are
    recorded in `.errors`; use the server as a context manager (or call check()) so they fail the test.
    """

    def __init__(self, handler=None, token=TOKEN, mode="normal", welcome=None, drip_interval=0.05):
        self.handler = handler or echo_handler
        self.token = token
        self.mode = mode
        self.welcome = {**WELCOME, **(welcome or {})}
        self.drip_interval = drip_interval
        self.hellos = []
        self.requests = []
        self.errors = []
        self.conns = []
        self._conns_lock = threading.Lock()
        self._stop = threading.Event()
        self._lsock = socket.socket()
        self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if mode == "stall":
            self._lsock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)  # accepted sockets inherit it
        self._lsock.bind(("127.0.0.1", 0))
        self._lsock.listen()
        self.port = self._lsock.getsockname()[1]
        self._accepter = threading.Thread(target=self._accept, daemon=True)
        self._accepter.start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._lsock.accept()
            except OSError:
                return
            if self._stop.is_set():  # close() poked us awake
                conn.close()
                return
            with self._conns_lock:
                self.conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        lock = threading.Lock()

        def reply(obj):
            with lock:
                try:
                    conn.sendall(_framing.encode(obj))
                except OSError:
                    pass

        try:
            hello = _framing.read_frame(conn)
            self.hellos.append(hello)
            problems = hello_problems(hello)
            if problems:
                self.errors.append(f"malformed hello {hello!r}: {problems}")
                reply({"type": "handshake_error", "code": "bad_request", "message": "; ".join(problems)})
                return
            if self.mode == "hang":
                self._stop.wait()
                return
            if self.mode == "garbage":
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            if self.mode == "mismatch":
                reply({"type": "handshake_error", "code": "protocol_mismatch", "message": "server speaks 2.0"})
                return
            if hello.get("token") != self.token:
                reply({"type": "handshake_error", "code": "unauthorized", "message": "bad token"})
                return
            welcome = _framing.encode({"type": "welcome", **self.welcome})
            if self.mode == "drip":
                for i in range(len(welcome)):
                    conn.sendall(welcome[i:i + 1])
                    if self._stop.wait(self.drip_interval):
                        return
                return
            if self.mode == "partial":
                conn.sendall(welcome[:len(welcome) // 2])
                self._stop.wait()
                return
            conn.sendall(welcome)
            if self.mode == "stall":
                self._stop.wait()
                return
            while True:
                req = _framing.read_frame(conn)
                self.requests.append(req)
                self.handler(req, reply)
        except (ConnectionLost, ProtocolViolation, OSError):
            pass  # the client went away or hung up: the normal end of a connection
        except Exception:  # noqa: BLE001 - a bug in a test handler: report it, do not swallow it
            self.errors.append(traceback.format_exc())
        finally:
            conn.close()

    def drop_all(self):
        with self._conns_lock:
            conns = list(self.conns)
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self._stop.set()
        try:  # closing a listener does not wake a blocked accept(); a throwaway connection does
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self._accepter.join(2)
        self._lsock.close()
        self.drop_all()

    def check(self):
        """Raise if a handler crashed or a client sent a malformed hello."""
        if self.errors:
            raise AssertionError("FakeServer recorded errors:\n" + "\n".join(self.errors))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        if exc_type is None:
            self.check()
