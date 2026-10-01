# S9: latency of 1000 sequential _spike.call_rt calls from this thread. Run once with no spinner and
# while spikes/py/s9_spin.py is running. Records the GIL switch interval, and whether a spinner was running
# at the start AND at the end (a run whose spinner ended part-way is not a "spinning" number: redo it).
import json
import sys
import threading
import time

import _spike


def _s9_spinning():
    return any(t.name == "s9-spin" and t.is_alive() for t in threading.enumerate())


_spin_start = _s9_spinning()
_lat = []
for _ in range(1000):
    _t = time.perf_counter()
    _spike.call_rt(10000)
    _lat.append((time.perf_counter() - _t) * 1000)
_spin_end = _s9_spinning()
_lat.sort()
result = json.dumps({
    "n": len(_lat), "p50_ms": round(_lat[499], 3), "p99_ms": round(_lat[989], 3), "max_ms": round(_lat[-1], 3),
    "switchinterval_s": sys.getswitchinterval(),
    "spinner_running_start": _spin_start, "spinner_running_end": _spin_end,
})
