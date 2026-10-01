#!/usr/bin/env python3
"""Send Python source to the spike's embedded interpreter: py_send.py FILE.py | py_send.py -c "CODE".

The harness is line based, so the source travels base64-wrapped in one `py_exec` line. It runs in one
persistent namespace (__main__); a variable `result` is returned. Prints the reply ("OK <result>" or
"ERR <traceback>", newlines folded to " | "); exit 0 for OK. Env: SPIKE_PORT (45978), PY_SEND_TIMEOUT (300 s).
"""
import base64
import os
import socket
import sys

HOST = "127.0.0.1"
PORT = int(os.environ.get("SPIKE_PORT", "45978"))
TIMEOUT = float(os.environ.get("PY_SEND_TIMEOUT", "300"))


def wrap(source: str) -> str:
    b64 = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return f"py_exec exec(__import__('base64').b64decode('{b64}').decode('utf-8'))"


def send(line: str) -> str:
    with socket.create_connection((HOST, PORT), timeout=10) as s:
        s.settimeout(TIMEOUT)
        s.sendall((line + "\n").encode("utf-8"))
        f = s.makefile("r", encoding="utf-8", newline="\n")
        reply = f.readline()
    if not reply:
        raise ConnectionError("the spike DLL closed the connection without a reply")
    return reply.rstrip("\n")


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[1] == "-c":
        source = argv[2]
    elif len(argv) == 2 and argv[1] not in ("-h", "--help"):
        with open(argv[1], encoding="utf-8") as fh:
            source = fh.read()
    else:
        print(__doc__)
        return 2
    try:
        reply = send(wrap(source))
    except (OSError, ConnectionError) as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    print(reply)
    return 0 if reply.startswith("OK") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
