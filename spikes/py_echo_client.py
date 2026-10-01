#!/usr/bin/env python3
"""S8: Linux client of spikes/py/echo_server.py in the game. No args: 1000 round trips, a 1 MiB line,
4 parallel connections x 250. `soak SEC`: round trips for SEC seconds. Env: PY_ECHO_PORT (45979)."""
import os
import socket
import statistics
import sys
import threading
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("PY_ECHO_PORT", "45979"))


def _conn():
    s = socket.create_connection((HOST, PORT), timeout=10)
    s.settimeout(30)
    return s, s.makefile("rb")


def round_trips(n: int, tag: str) -> list[float]:
    s, f = _conn()
    lat = []
    with s:
        for i in range(n):
            msg = f"{tag}-{i}\n".encode()
            t0 = time.perf_counter()
            s.sendall(msg)
            got = f.readline()
            lat.append((time.perf_counter() - t0) * 1000)
            if got != msg:
                raise AssertionError(f"{tag}: round trip {i} got {got!r}, expected {msg!r}")
    return lat


def large(size: int = 1024 * 1024) -> float:
    payload = b"x" * (size - 1) + b"\n"
    s, f = _conn()
    with s:
        t0 = time.perf_counter()
        s.sendall(payload)
        got = f.readline()
    if got != payload:
        raise AssertionError(f"large: got {len(got)} bytes, expected {len(payload)}")
    return (time.perf_counter() - t0) * 1000


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "soak":
            end = time.monotonic() + float(argv[2])
            n, worst = 0, 0.0
            while time.monotonic() < end:
                worst = max(worst, max(round_trips(50, f"soak{n}")))
                n += 50
            print(f"S8 soak PASS: {n} round trips, worst {worst:.1f} ms")
            return 0
        lat = round_trips(1000, "ping")
        print(f"S8 socket basic PASS: 1000 round trips, median {statistics.median(lat):.2f} ms, max {max(lat):.2f} ms")
        print(f"S8 socket large PASS: 1 MiB echoed in {large():.1f} ms")
        errs = []  # an exception in a thread target is otherwise swallowed: collect it and fail below

        def worker(k: int) -> None:
            try:
                round_trips(250, f"par{k}")
            except BaseException as e:
                errs.append(f"par{k}: {e!r}")

        threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        if any(t.is_alive() for t in threads):
            raise AssertionError("parallel: a connection did not finish in 60 s")
        if errs:
            raise AssertionError("parallel: " + "; ".join(errs))
        print("S8 socket parallel PASS: 4 connections x 250 round trips")
        print("S8 socket PASS")
        return 0
    except ConnectionRefusedError:
        print(f"S8 socket FAIL: connection refused on {HOST}:{PORT} (did py_send.py spikes/py/echo_server.py reply OK?)")
        return 1
    except (OSError, AssertionError) as e:
        print(f"S8 socket FAIL: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
