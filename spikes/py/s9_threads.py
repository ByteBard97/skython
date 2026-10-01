# S9: 8 Python threads x 1000 _spike.call_rt round trips (AddTask -> Utility.GetCurrentRealTime -> VM callback).
import json
import threading
import time

import _spike

N_THREADS, N_CALLS = 8, 1000
_errors, _counts, _backwards = [], [0] * N_THREADS, [0] * N_THREADS


def _s9_worker(i):
    last = -1.0
    for _ in range(N_CALLS):
        try:
            v = _spike.call_rt(10000)
        except Exception as e:  # counted; the verdict below fails on any
            _errors.append(f"thread {i}: {type(e).__name__}: {e}")
            continue
        if not v > 0:
            _errors.append(f"thread {i}: bad value {v!r}")
        if v < last:
            _backwards[i] += 1
        last = v
        _counts[i] += 1


_t0 = time.perf_counter()
_ts = [threading.Thread(target=_s9_worker, args=(i,), name=f"s9-{i}", daemon=True) for i in range(N_THREADS)]
for _t in _ts:
    _t.start()
for _t in _ts:
    _t.join(150)
_alive = sum(t.is_alive() for t in _ts)
_dt = time.perf_counter() - _t0
_ok = _alive == 0 and not _errors and sum(_counts) == N_THREADS * N_CALLS
result = json.dumps({
    "verdict": "PASS" if _ok else "FAIL", "calls": sum(_counts), "expected": N_THREADS * N_CALLS,
    "errors": len(_errors), "first_errors": _errors[:5], "threads_still_running": _alive,
    "seconds": round(_dt, 2), "calls_per_s": round(sum(_counts) / _dt, 1),
    "backwards_values": sum(_backwards),
})
