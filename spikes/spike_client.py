#!/usr/bin/env python3
"""Spike client: sends line commands to the spike DLL on 127.0.0.1:45978 (override: SPIKE_PORT)."""
import os
import socket
import statistics
import sys
import time

HOST = "127.0.0.1"
PORT = int(os.environ.get("SPIKE_PORT", "45978"))
S1_RECV_TIMEOUT = 30.0      # S1 tests: a stall longer than this is a FAIL
LONG_RECV_TIMEOUT = 180.0   # S2 (up to 60 s), S6 (up to 120 s) and the rest


def connect(recv_timeout: float) -> socket.socket:
    s = socket.create_connection((HOST, PORT), timeout=10)  # connect timeout only
    s.settimeout(recv_timeout)                              # then the per-command receive timeout
    return s


def send_line(sock_file, sock, line: str) -> str:
    sock.sendall((line + "\n").encode())
    reply = sock_file.readline()
    if not reply:
        raise ConnectionError("server closed the connection")
    return reply.rstrip("\n")


def s1_basic(n: int = 1000) -> bool:
    """One steady connection, one write per message, n round trips."""
    with connect(S1_RECV_TIMEOUT) as s:
        f = s.makefile("r", encoding="utf-8", newline="\n")
        t0 = time.perf_counter()
        for i in range(n):
            reply = send_line(f, s, f"echo ping-{i}")
            if reply != f"ping-{i}":
                print(f"S1 FAIL basic at {i}: {reply!r}")
                return False
        dt = time.perf_counter() - t0
        print(f"S1 basic PASS: {n} round trips in {dt:.3f}s ({n / dt:.0f}/s)")
        return True


def s1_split(n: int = 200) -> bool:
    """Header-then-body pattern: two sendall() calls per message, then wait for the reply.
    This is the pattern the real protocol uses (4-byte length, then the body); with Nagle plus
    delayed ACK it can stall ~40 ms per message. A stall is recorded, not a FAIL, but a hang is."""
    lat = []
    with connect(S1_RECV_TIMEOUT) as s:
        f = s.makefile("r", encoding="utf-8", newline="\n")
        for i in range(n):
            msg = f"echo split-{i}\n".encode()
            t0 = time.perf_counter()
            s.sendall(msg[:4])      # stands in for the 4-byte header
            s.sendall(msg[4:])      # the body, a separate write
            reply = f.readline().rstrip("\n")
            lat.append((time.perf_counter() - t0) * 1000)
            if reply != f"split-{i}":
                print(f"S1 FAIL split at {i}: {reply!r}")
                return False
    p50, worst = statistics.median(lat), max(lat)
    note = "  NOTE: ~40 ms medians mean Nagle/delayed-ACK stalls: the dev bridge (Task 23) and skyrim.remote (Task 22) must set TCP_NODELAY" if p50 > 30 else ""
    print(f"S1 split PASS: {n} two-write messages, median {p50:.2f} ms, max {worst:.2f} ms{note}")
    return True


def s1_large(size: int = 1024 * 1024) -> bool:
    """A 1 MiB line echoed back: the server needs many recv() calls and the reply needs several."""
    payload = "x" * (size - 1) + "\n"
    with connect(S1_RECV_TIMEOUT) as s:
        f = s.makefile("r", encoding="utf-8", newline="\n")
        t0 = time.perf_counter()
        s.sendall(("echo " + payload).encode())
        reply = f.readline()
        dt = time.perf_counter() - t0
    if reply != payload:
        print(f"S1 FAIL large: got {len(reply)} bytes, expected {len(payload)}")
        return False
    print(f"S1 large PASS: {size} bytes echoed in {dt * 1000:.1f} ms")
    return True


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "s1"
    try:
        if cmd == "s1":
            ok = s1_basic() and s1_split() and s1_large()
            print("S1 PASS" if ok else "S1 FAIL")
            return 0 if ok else 1
        if cmd == "ping":  # connect + one echo; for the main-menu and reconnect checks
            with connect(S1_RECV_TIMEOUT) as s:
                f = s.makefile("r", encoding="utf-8", newline="\n")
                print(send_line(f, s, "echo pong"))
            return 0
        with connect(LONG_RECV_TIMEOUT) as s:
            f = s.makefile("r", encoding="utf-8", newline="\n")
            print(send_line(f, s, " ".join(sys.argv[1:])))
        return 0
    except ConnectionRefusedError:
        print(f"FAIL: connection refused on {HOST}:{PORT} (for S1 this is the FAIL result)")
        return 1
    except (socket.timeout, TimeoutError):
        print("FAIL: TIMEOUT waiting for the DLL (it may still be working: check the spike log)")
        return 2
    except (ConnectionError, OSError) as e:
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
