# Task 2: Spike S1 — Linux connects to a socket bound inside Proton

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Create: `spikes/spike_client.py`, `spikes/RESULTS.md`

- [ ] **Step 1: Write the spike client**

```python
#!/usr/bin/env python3
"""Spike client: sends line commands to the spike DLL on 127.0.0.1:45978."""
import socket
import sys
import time


def send(sock_file, sock, line: str) -> str:
    sock.sendall((line + "\n").encode())
    return sock_file.readline().rstrip("\n")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "s1"
    with socket.create_connection(("127.0.0.1", 45978), timeout=30) as s:
        f = s.makefile("r", encoding="utf-8")
        if cmd == "s1":
            t0 = time.perf_counter()
            for i in range(1000):
                reply = send(f, s, f"echo ping-{i}")
                if reply != f"ping-{i}":
                    print(f"S1 FAIL at {i}: {reply!r}")
                    return 1
            dt = time.perf_counter() - t0
            print(f"S1 PASS: 1000 round trips in {dt:.3f}s ({1000 / dt:.0f}/s)")
            return 0
        print(send(f, s, " ".join(sys.argv[1:])))
        return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it against the running game (from Task 1)**

Run: `python3 spikes/spike_client.py s1`
Expected: `S1 PASS: 1000 round trips in ...`. A `ConnectionRefusedError` means S1 FAILS.

- [ ] **Step 3: Record the result**

Create `spikes/RESULTS.md`:
```markdown
# M0 spike results

| Spike | Date | Result | Evidence |
|---|---|---|---|
| S1 Linux → Wine-bound socket | <date> | PASS/FAIL | `<exact client output>`; runtime `<version from log>`; GE-Proton10-14 |
```
**If S1 fails: stop this plan.** Update spec section 4.2 to the fallback (the plugin connects outbound to a Linux-side broker), then re-plan Tasks 16 and 14.

- [ ] **Step 4: Commit**

```bash
git add spikes/spike_client.py spikes/RESULTS.md
git commit -m "test(spike): S1 loopback transport from Linux into Proton"
```
