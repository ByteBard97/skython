# S9: one CPU-bound Python thread for _S9_SPIN_S seconds (default 70: covers a 60 s measurement; set it just
# before with py_send.py -c "_S9_SPIN_S = 200"; it is used once). Returns at once. Refuses while one is spinning.
import threading
import time

if "_s9_spinner" in globals() and _s9_spinner.is_alive():
    raise RuntimeError("an s9 spinner is still running; wait for it to end (result = _s9_spinner.is_alive())")
_s9_spin_s = float(globals().pop("_S9_SPIN_S", 70))


def _s9_spin(seconds):
    end, n = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        n += 1
    globals()["_s9_spin_iterations"] = n


_s9_spinner = threading.Thread(target=_s9_spin, args=(_s9_spin_s,), name="s9-spin", daemon=True)
_s9_spinner.start()
result = "spinning %g s" % _s9_spin_s
