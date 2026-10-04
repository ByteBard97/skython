import ast
import json
import os
import pathlib
import re
import socket
import threading
import time

from support import (MAX_FRAME, closed_by_server, encode, frame_header, hello, log_text, python_log_text, raw_connect,
                     read_frame, read_frame_or_none, rpc, send_raw, still_serving)

BAD_OP = {"type": "request", "id": 5, "op": "no.such.op", "args": {}}


def test_python_is_running_in_the_game():
    assert "python host: running" in log_text(), "the core log has no 'python host: running' line from this run"
    assert "SpyCore_Load returned true" in log_text("SkythonLoader.log")
    assert python_log_text(), "python.log was not written in this run"
    # The interpreter config: the embedded runtime's python.exe and the isolated search path, in order.
    line = next((ln for ln in log_text().splitlines() if "python host: initialised; sys.executable = " in ln), "")
    assert line, "the core log has no 'python host: initialised' line from this run"
    exe = line.split("sys.executable = ", 1)[1].split("; sys.path = ", 1)[0]
    assert exe.endswith("\\Skython\\python\\python.exe"), exe
    path = ast.literal_eval(line.split("; sys.path = ", 1)[1])
    order = [next((i for i, p in enumerate(path) if p.endswith(tail)), -1)
             for tail in ("\\python314.zip", "\\site-packages", "\\lib", "\\scripts")]
    assert -1 not in order and order == sorted(order), path


def test_a_failing_script_is_logged_and_python_keeps_running(session):
    text = python_log_text() + log_text()
    assert "livetest_broken" in text and "RuntimeError: livetest_broken setup raises on purpose" in text
    assert "python host: running" in log_text()
    assert still_serving(session)


def test_welcome(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(hello(session))
    w = read_frame(s)
    assert w["type"] == "welcome" and w["protocol"][0] == 1
    # The launched install's version: box_session.sh's env names it; a plain local run is the 1.6.1170 machine.
    assert w["game_version"] == os.environ.get("SPY_GAME_VERSION", "1.6.1170.0") and w["runtime"] == "AE" \
        and w["plugin_version"]


def test_wrong_token_is_unauthorized(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(hello(session, token="0" * 32))
    reply = read_frame(s)
    assert reply["type"] == "handshake_error" and reply["code"] == "unauthorized"
    assert closed_by_server(s)


def test_malformed_hellos_never_kill_the_bridge(session):
    for override in ({"type": 1}, {"client": 5}, {"protocol": "1.0"}, {"protocol": [1]}, {"token": 12}):
        s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
        s.sendall(hello(session, **override))
        reply = read_frame_or_none(s)
        assert reply is None or reply["type"] == "handshake_error", override
    assert still_serving(session)


def test_garbage_frame_closes_only_that_connection(session):
    keep = raw_connect(session)
    bad = raw_connect(session)
    body = b"this is not json"
    send_raw(bad, frame_header(len(body)) + body)
    assert closed_by_server(bad)
    assert rpc(keep, "game.state", req_id=2)["ok"] is True  # the other connection is untouched
    assert still_serving(session)


def test_oversized_header_closes_connection(session):
    bad = raw_connect(session)
    send_raw(bad, frame_header(MAX_FRAME + 1))
    assert closed_by_server(bad)
    assert still_serving(session)


def test_deeply_nested_json_closes_only_that_connection(session):
    s = raw_connect(session)
    depth = 200_000
    body = b'{"type":"request","id":1,"op":"game.state","args":{"x":' + b"[" * depth + b"]" * depth + b"}}"
    send_raw(s, frame_header(len(body)) + body)
    assert closed_by_server(s, timeout=5.0)
    assert still_serving(session)


def test_unknown_op_is_bad_request_and_connection_stays_open(session):
    s = raw_connect(session)
    s.sendall(encode(BAD_OP))
    reply = read_frame(s)
    assert reply["id"] == 5 and reply["ok"] is False and reply["error"]["code"] == "bad_request"
    assert rpc(s, "game.state", req_id=6)["ok"] is True


def test_game_state_raw(session):
    r = rpc(raw_connect(session), "game.state", req_id=7)
    assert r["ok"] is True
    st = r["result"]
    assert st["data_ready"] is True and isinstance(st["epoch"], int) and isinstance(st["menus"], list)


def test_game_state_through_skython_remote(game):
    st = game.state()
    assert st["data_ready"] is True and "interactive" in st


def test_two_clients_get_their_own_replies(session):
    results = {}

    def worker(name, base):
        s = raw_connect(session)
        for i in range(base, base + 50):
            s.sendall(encode({**BAD_OP, "id": i}))
        results[name] = sorted(read_frame(s)["id"] for _ in range(50))
        s.close()

    threads = [threading.Thread(target=worker, args=(n, b)) for n, b in (("a", 1000), ("b", 2000))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert results["a"] == list(range(1000, 1050)) and results["b"] == list(range(2000, 2050))


def test_requests_then_immediate_close(session):
    for reset in (False, True):
        s = raw_connect(session)
        for i in range(1, 21):
            s.sendall(encode({"type": "request", "id": i, "op": "game.state", "args": {}}))
        if reset:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
        s.close()
    time.sleep(0.5)
    assert still_serving(session)


def test_session_file(session):
    p = pathlib.Path(os.environ["SKYTHON_SESSION"])
    data = json.loads(p.read_text())
    assert re.fullmatch(r"[0-9a-f]{32}", data["token"]) and data["port"] > 0
    assert data["protocol"] == [1, 1] and data["plugin_version"]
    raw_connect(session).close()  # the token in the file is the one the bridge accepts
