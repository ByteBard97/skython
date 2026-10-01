# Task 2: Spike S1: Linux reaches a socket inside Proton (spike harness)

Adapted from: branch `plan-out-of-process`, task 2 (`task-02-spike-s1-linux-connects-to-a-socket.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** (Fixed paths) and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4). Steps use checkbox (`- [ ]`) syntax for tracking.

**Why this spike still exists in the embedded design.** Every later spike (Tasks 3-10) is driven from Linux through the spike DLL's line protocol on `127.0.0.1:45978`, so this proves the harness first. It also answers half of the dev bridge's premise (spec 4.2: a native Linux process connects to a socket bound inside the Wine process). Spike S8 (Task 3) repeats the check against a socket opened by **Python** inside the game, which is what the dev bridge (Task 23) actually uses.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before Task 3 starts.** S8 (Task 3) gates Tasks 18-32; S2, S9 and S4 (Tasks 4-6) gate the M1 plugin tasks (25-32); S7 (Task 9) gates Tasks 28-29; S5, S6 and S10 gate only the M2 plan and skyrim-dm's packaging. **S1 FAIL: STOP and ask the owner.** Do not start Task 3 and do not edit the spec yourself.

**Files:**
- Create: `spikes/spike_client.py`, `spikes/RESULTS.md`

**Interfaces:**
- Consumes (Task 1): the spike DLL listening on `127.0.0.1:45978`, `echo <text>` command.
- Consumes (Task 1): `tools/launch_game.sh`, `tools/wait_for_log.sh`, `source tools/env.sh` (`SKSE_LOG_DIR`, `SKYRIM_PYTHON_STATE`).
- Produces: `spikes/spike_client.py <command> [args...]` (Tasks 3-10 reuse it; it reads `SPIKE_PORT` if set, default 45978). Commands `s1`, `ping`, and any other word is sent to the DLL as a line. `spikes/RESULTS.md` with one row per spike, in the order of spec draft 4 section 9 (S1, S8, S2, S9, S4, S5, S6, S7, S10); each later spike task fills its own row.

- [ ] **Step 1: Write the spike client**

The connect timeout (10 s) is separate from the receive timeout: S2 can take up to 60 s and S6 up to 120 s, so after connecting the socket timeout is raised per command (S1 tests use 30 s so a stall is a FAIL, everything else 180 s).

```python
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
```

Check the script compiles, and exercise it against a local stand-in echo server before the game is involved (this runs on Linux only and does not prove S1; it proves the client):
```bash
python3 -m py_compile spikes/spike_client.py
python3 - <<'EOF' &
import socketserver
class H(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            s = line.decode().rstrip("\n")
            self.wfile.write((s[5:] if s.startswith("echo ") else "ERR unknown command").encode() + b"\n")
socketserver.ThreadingTCPServer.allow_reuse_address = True
with socketserver.ThreadingTCPServer(("127.0.0.1", 45999), H) as srv:
    srv.timeout = 20; srv.handle_request(); srv.handle_request(); srv.handle_request(); srv.handle_request()
EOF
sleep 1; SPIKE_PORT=45999 python3 spikes/spike_client.py s1
```
Expected: the three `S1 ... PASS` lines and `S1 PASS`. (The stand-in server exits by itself after a few requests; if it lingers, it stops after its 20 s timeout.)

- [ ] **Step 2: Run it against the running game (from Task 1)**

Run: `python3 spikes/spike_client.py s1`
Expected: `S1 basic PASS`, `S1 split PASS`, `S1 large PASS`, then `S1 PASS`. A `ConnectionRefusedError` or a timeout means S1 FAILS.

Then the four manual checks, recording each outcome in the evidence column:
1. **Main menu:** quit to the main menu (or start the game and do not load a save) and run `python3 spikes/spike_client.py ping`. Expected `pong`.
2. **Reconnect:** run `ping` twice in a row (the DLL must accept a second connection after the first closes).
3. **Restart:** quit the game (console `qqq`), run `tools/launch_game.sh`, wait for the listener line (`SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "listening on 127.0.0.1:45978" 600`), then `ping` again.
4. **Port after a crash (optional, only if the game crashes on its own during M0):** after the crash, `ss -ltnp | grep 45978` shows whether the port stays held; note it. Do not crash the game on purpose.

- [ ] **Step 3: Record the result**

Create `spikes/RESULTS.md` (one row per spike, in the order of spec draft 4 section 9; later tasks fill their own rows; the last column is the spec's "If it fails, change" text plus the plan tasks it would affect):
```markdown
# M0 spike results

Pinned: CommonLibSSE-NG commit, CPython 3.14.8 and API findings are in `docs/PINS.md`. Runtime for all spikes: AE 1.6.1170, GE-Proton10-14.

| Spike | Date | PASS/FAIL | Evidence | If it fails, change |
|---|---|---|---|---|
| S1 Linux to a socket bound inside Proton, spike harness (Task 2) | <date> | PASS/FAIL | `<exact client output>`; split-write median/max ms; main-menu ping; reconnect; restart; runtime `<version from log>` | Spike harness and spec 4.2 dev bridge: the bridge connects outbound to a Linux-side listener instead (Tasks 22-23). STOP and ask the owner. |
| S8 two-DLL load, CPython 3.14.8 embedded, Python socket from Linux (Task 3) | | | | Whole design: fall back to spec draft 3, the out-of-process plan on branch `plan-out-of-process`. If only the Python socket fails: the bridge connects outbound to a Linux-side listener. STOP and ask the owner. |
| S2 VM callback thread, nested dispatch, argument ownership (Task 4) | | | | 4.3/4.5: `batch` issues each step with a fresh `AddTask`; argument ownership decides `delete` after a failed dispatch in Task 27. STOP and ask the owner on FAIL. |
| S9 Python threads drive VM calls; frame time and call latency under CPU-bound Python (Task 5) | | | | 4.5: pace Python work (sleep between batches) or move heavy work to a worker process; if calls hang, revisit the Dispatcher's pending-result design (Task 18). STOP and ask the owner on FAIL. |
| S4 alias bind and ForceRefTo (Task 6) | | | | 4.4: aliases via a pre-authored Papyrus helper from the M1.5 test ESP; invalidates Task 29's alias code. STOP and ask the owner. |
| S5 mod events and native Send (Task 7) | | | | 4.6: `Send` only, or `ModCallbackEvent` only (M2 plan). |
| S6 load a save via AddUITask (Task 8) | | | | 4.3/G8: the harness uses new game + `coc`; save/load tests become manual (M2 plan). |
| S7 function lookup, bound-object dispatch, alias handles, object arrays (Task 9) | | | | 4.3/4.4 per criterion (see Task 9); gates Tasks 28-29. |
| S10 skyrim-dm's dependencies as cp314 win_amd64 wheels in the embedded interpreter (Task 10) | | | | 4.10: pure-Python replacements, or the dependency runs outside the game (skyrim-dm packaging, M2 plan). |
```
**If S1 fails: STOP and ask the owner.** Do not start Task 3 and do not edit the spec or re-plan Tasks 22-23 on your own: the owner decides the fallback (the dev bridge connecting outbound to a Linux-side listener, or binding 0.0.0.0 with a peer check).

- [ ] **Step 4: Commit**

```bash
git add spikes/spike_client.py spikes/RESULTS.md
git commit -m "test(spike): S1 loopback transport from Linux into Proton"
```
