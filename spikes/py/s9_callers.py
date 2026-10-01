# S9: the combined load for the fourth frame-time sample (skyrim-dm's real shape: CPU-heavy generation while
# delivering calls): 8 Python threads calling _spike.call_rt back to back for _S9_CALLERS_S seconds (default 70;
# set it just before with py_send.py -c; it is used once), in the background. Returns at once. Run it together
# with s9_spin.py. Afterwards: py_send.py -c "result = _s9_callers_report()". Refuses while a run is alive.
import json
import threading
import time

import _spike

if any(t.is_alive() for t in globals().get("_s9_callers", [])):
    raise RuntimeError("s9 callers are still running; wait for them to end")
_s9_callers_s = float(globals().pop("_S9_CALLERS_S", 70))
_s9_callers_stats = {"calls": 0, "errors": []}
_s9_callers_lock = threading.Lock()


def _s9_caller(i, end):
    while time.monotonic() < end:
        try:
            v = _spike.call_rt(10000)
            bad = None if v > 0 else f"thread {i}: bad value {v!r}"
        except Exception as e:  # counted; the report fails on any
            bad = f"thread {i}: {type(e).__name__}: {e}"
        with _s9_callers_lock:
            _s9_callers_stats["calls"] += 1
            if bad:
                _s9_callers_stats["errors"].append(bad)


def _s9_callers_report():
    with _s9_callers_lock:
        calls, errors = _s9_callers_stats["calls"], list(_s9_callers_stats["errors"])
    alive = sum(t.is_alive() for t in _s9_callers)
    verdict = "RUNNING" if alive else ("PASS" if calls > 0 and not errors else "FAIL")
    return json.dumps({"verdict": verdict, "threads_still_running": alive, "seconds": _s9_callers_s, "calls": calls,
                       "errors": len(errors), "first_errors": errors[:5]})


_s9_callers_end = time.monotonic() + _s9_callers_s
_s9_callers = [threading.Thread(target=_s9_caller, args=(i, _s9_callers_end), name=f"s9-caller-{i}", daemon=True) for i in range(8)]
for _t in _s9_callers:
    _t.start()
result = "callers: 8 threads for %g s" % _s9_callers_s
