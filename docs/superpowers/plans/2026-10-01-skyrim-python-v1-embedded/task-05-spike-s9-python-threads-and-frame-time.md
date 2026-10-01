# Task 5: Spike S9: Python threads drive VM calls; frame time and call latency under CPU-bound Python

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Contracts** and **Fixed paths** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.5 (threading rules) and 9 (spike S9). New in this plan. Steps use checkbox (`- [ ]`) syntax.

> **M0 spike rules:** throwaway code; PASS/FAIL with evidence in `spikes/RESULTS.md`. **S9 gates Part C (Tasks 25-32): on FAIL, STOP and ask the owner.** Also STOP if the game does not reach the log line within 10 minutes, the build box does not answer, or the runtime is not AE 1.6.1170. Throwaway save only; the harness runs arbitrary Python (Task 3).

**What this measures (spec 9, S9):**
1. **Correctness under concurrency:** 8 Python threads × 1000 calls of `_spike.call_rt`, each one a full round trip Python thread → `AddTask` → main thread → `DispatchStaticCall("Utility", "GetCurrentRealTime")` → VM callback → back to the waiting Python thread. All must succeed; no hang. The main thread and the VM thread run only C++ (spec 4.5 rule 1); the GIL is released for the whole wait.
2. **Frame time under CPU-bound Python:** median and 99th-percentile frame time while one Python thread spins for 60 s must stay within 10% of the baseline (the mean of one run before and one after).
3. **GIL hand-off cost (recorded, not pass/fail):** p50/p99 latency of `call_rt` from one Python thread with and without a spinning thread, at the default `sys.getswitchinterval()` (0.005 s) and at 0.001 s. Expect each call to cost up to one switch interval more while a spinner runs: the waiting thread needs the GIL back to return, and the spinner gives it up only at the interval. A Linux stand-in (2 ms fake call) showed exactly that: p50 2.06 ms alone, 7.12 ms with a spinner. This number tells Task 24 (`skyrim._host`) whether to lower the switch interval, which matters to skyrim-dm (CPU-heavy generation while it delivers calls).

**Frame time method.** No engine frame counter is used (none is verified in `API-VERIFICATION.md`). A sampler thread posts one `AddTask` about every millisecond; the game runs queued tasks once per frame, so each frame's tasks run back to back and their timestamps form a cluster. `spikes/s9_analyze.py` splits clusters at gaps above 1.5 ms and takes frame time as the distance between consecutive cluster starts. A coarse Windows timer only makes the posts bursty; at least one task per frame is enough. The sampler costs the same in every run, so runs compare fairly.

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp` (one include, one block, two commands)
- Create: `spikes/py/s9_threads.py`, `spikes/py/s9_spin.py`, `spikes/py/s9_latency.py`, `spikes/s9_analyze.py`
- Modify: `spikes/RESULTS.md` (S9 row)

**Interfaces:**
- Consumes: Task 3 (`spike::py::SetCaller`, `spike::py::CallerFn`, SpikePyCore's `_spike.call_rt`, `spikes/py_send.py`, `py_load`/`py_init`, `tools/deploy.sh --python`); Task 4 (`spike::Var`, `spike::Functor`, `spike::NoArgs()`, `spike::OnMainThread`, S2 PASS, which also says on which thread the callback runs); Task 1 (`spike::ParseInt`, `spike::Handle`, `spike::Log`); Task 2 (`spikes/spike_client.py`, generic `<command> [args]` form with a 180 s receive timeout). SpikePyCore is **not** rebuilt: Task 3 already built `call_rt`.
- Produces: harness commands `py_arm_rt` (registers `spike::CallRealTime` as the `call_rt` caller) and `s9_sample <10..120 s> <label>` (writes `<SKSE log dir>/SkyrimPythonSpike_s9_<label>.txt`); `spikes/s9_analyze.py BASE_A SPIN BASE_B` / `--selftest`; the S9 row (throughput of Python-driven calls, frame verdict, latency table, switch-interval recommendation) that Tasks 24, 26 and 31 read.

- [ ] **Step 0: Check the gates**

```bash
grep -E '^\| S8 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S8 PASS row: STOP and ask the owner"; false; }
grep -E '^\| S2 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S2 PASS row: STOP and ask the owner"; false; }
```
Both rows must print. Then load the throwaway save in an **interior** (a quiet room: frame time is steadier), and leave the game **unpaused with no menu open** for every measurement; stand still and do not touch the mouse during the three 60 s samples.

- [ ] **Step 1: Write the analysis script and test it on synthetic data**

`spikes/s9_analyze.py`:
```python
#!/usr/bin/env python3
"""S9 frame-time analysis of the spike's AddTask timestamp files.

  s9_analyze.py BASE_A SPIN BASE_B [--threshold 0.10] [--gap-ms 1.5]
  s9_analyze.py --selftest

Each file is written by the spike command `s9_sample`: '#' header lines, then one integer per line,
the microseconds (steady clock) at which one AddTask ran on the main thread. The sampler posts one
task about every millisecond; the game runs queued tasks once per frame, so timestamps arrive in
clusters, one cluster per frame. A gap longer than --gap-ms starts a new cluster; frame time is the
distance between consecutive cluster starts.

Verdict: baseline = mean of BASE_A and BASE_B. If A and B differ by more than the threshold in median
or p99 frame time, the measurement is too noisy: INCONCLUSIVE (rerun standing still in one spot).
Otherwise PASS when SPIN's median and p99 are each at most baseline * (1 + threshold), else FAIL.
Prints one JSON line with all numbers, then the verdict line. Exit 0 PASS, 1 FAIL, 3 INCONCLUSIVE.
"""
import json
import random
import sys


def load(path: str) -> list[int]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(int(line))
    return out


def frames(us: list[int], gap_ms: float) -> tuple[list[float], float]:
    """Frame times in ms, and mean samples per frame."""
    us = sorted(us)
    if len(us) < 3:
        raise ValueError("fewer than 3 samples")
    starts, sizes = [us[0]], [1]
    for prev, cur in zip(us, us[1:]):
        if cur - prev > gap_ms * 1000:
            starts.append(cur)
            sizes.append(1)
        else:
            sizes[-1] += 1
    ft = [(b - a) / 1000 for a, b in zip(starts, starts[1:])]
    return ft, sum(sizes) / len(sizes)


def pct(sorted_vals: list[float], q: float) -> float:
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def stats(us: list[int], gap_ms: float) -> dict:
    ft, per = frames(us, gap_ms)
    s = sorted(ft)
    med = pct(s, 0.5)
    return {"frames": len(s), "median_ms": round(med, 3), "p99_ms": round(pct(s, 0.99), 3),
            "max_ms": round(s[-1], 3), "samples_per_frame": round(per, 2),
            "long_frames": sum(1 for x in s if x > 2 * med)}


def verdict(a: dict, spin: dict, b: dict, threshold: float) -> tuple[str, dict]:
    out = {}
    noisy = False
    ok = True
    for k in ("median_ms", "p99_ms"):
        base = (a[k] + b[k]) / 2
        noise = abs(a[k] - b[k]) / base
        ratio = spin[k] / base
        out[k] = {"baseline": round(base, 3), "baseline_noise": round(noise, 3), "spin": spin[k], "spin_over_baseline": round(ratio, 3)}
        noisy |= noise > threshold
        ok &= ratio <= 1 + threshold
    return ("INCONCLUSIVE" if noisy else "PASS" if ok else "FAIL"), out


def synth(frame_ms: float, seconds: float = 60, per_frame: int = 10, hitch_every: int = 600, seed: int = 1) -> list[int]:
    rnd = random.Random(seed)
    t, out, n = 0.0, [], 0
    while t < seconds * 1000:
        for k in range(per_frame):
            out.append(int(t * 1000) + k * 30 + rnd.randint(0, 5))
        n += 1
        step = frame_ms * 2 if n % hitch_every == 0 else frame_ms
        t += step + rnd.uniform(-0.3, 0.3)
    return out


def selftest() -> int:
    g = 1.5
    base_a, base_b = stats(synth(16.667, seed=1), g), stats(synth(16.667, seed=2), g)
    assert abs(base_a["median_ms"] - 16.667) < 0.5, base_a
    assert base_a["samples_per_frame"] == 10, base_a
    assert verdict(base_a, stats(synth(17.0, seed=3), g), base_b, 0.10)[0] == "PASS"
    assert verdict(base_a, stats(synth(20.0, seed=3), g), base_b, 0.10)[0] == "FAIL"
    assert verdict(base_a, stats(synth(17.0, seed=3), g), stats(synth(22.0, seed=4), g), 0.10)[0] == "INCONCLUSIVE"
    # one sample per frame still clusters correctly
    assert abs(stats(synth(16.667, per_frame=1), g)["median_ms"] - 16.667) < 0.5
    print("s9_analyze selftest PASS")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    args, threshold, gap = [], 0.10, 1.5
    it = iter(argv[1:])
    for x in it:
        if x == "--threshold":
            threshold = float(next(it))
        elif x == "--gap-ms":
            gap = float(next(it))
        else:
            args.append(x)
    if len(args) != 3:
        print(__doc__)
        return 2
    a, spin, b = (stats(load(p), gap) for p in args)
    v, detail = verdict(a, spin, b, threshold)
    print(json.dumps({"base_a": a, "spin": spin, "base_b": b, "compare": detail, "threshold": threshold}))
    print(f"S9 frame time {v}")
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3}[v]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```
Run:
```bash
python3 spikes/s9_analyze.py --selftest
```
Expected: `s9_analyze selftest PASS` (verified while writing the plan). The self-test builds 60 s of synthetic 60 fps clusters with jitter and occasional double-length hitches, and checks: a 17.0 ms spin run against 16.67 ms baselines is `PASS`, a 20 ms spin run is `FAIL`, and baselines of 16.67 and 22 ms are `INCONCLUSIVE`; one sample per frame still clusters correctly.

- [ ] **Step 2: Add the S9 code to the spike DLL**

In `spikes/spike-dll/src/main.cpp`:
1. At the top of the file, with the other standard includes (not inside a namespace): `#include <fstream>`.
2. A new `namespace spike` block **after** Task 4's S2 block (it uses `Var`, `Functor`, `NoArgs`, `OnMainThread`) and above the block that defines `Handle`:
```cpp
namespace spike {
// ---- S9 (Task 5) ----
// One Python -> game round trip. SpikePyCore's _spike.call_rt calls this on a Python thread with the GIL
// released. The main-thread task and the VM callback run C++ only (never Python); everything they touch
// is owned by shared_ptr, so a callback that fires after a timeout still has valid memory.
// Returns 0 ok, 1 timeout, 2 dispatch refused, 3 anything else.
int CallRealTime(int timeoutMs, double* out)
{
    try {
        if (timeoutMs < 1 || timeoutMs > 600000) return 3;
        auto p = std::make_shared<std::promise<double>>();
        auto fut = p->get_future();
        auto once = std::make_shared<std::atomic_bool>(false);
        SKSE::GetTaskInterface()->AddTask([p, once] {
            auto finish = [p, once](double v) {
                if (!once->exchange(true)) p->set_value(v);
            };
            try {
                auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
                RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish](Var v) {
                    finish(v.IsFloat() ? static_cast<double>(v.GetFloat()) : -1.0);
                }) };
                if (!vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("GetCurrentRealTime"), NoArgs(), cb))
                    finish(-2.0);
            } catch (...) {
                finish(-3.0);
            }
        });
        if (fut.wait_for(std::chrono::milliseconds(timeoutMs)) != std::future_status::ready) return 1;
        const double v = fut.get();
        if (v == -2.0) return 2;
        if (v < 0) return 3;
        *out = v;
        return 0;
    } catch (...) {
        return 3;
    }
}

std::string S9PauseState()
{
    return OnMainThread([](std::function<void(std::string)> finish) {
        auto* ui = RE::UI::GetSingleton();
        finish(!ui ? "NO_UI" : ui->GameIsPaused() ? "PAUSED" : "ok");
    }, std::chrono::seconds(10));
}

struct S9Samples
{
    std::mutex mx;
    std::vector<long long> us;
};

// s9_sample <seconds> <label>: a sampler thread (this one) posts one AddTask about every millisecond for
// <seconds>; each task records when it ran. Tasks run once per frame, so the timestamps cluster by frame
// (spikes/s9_analyze.py turns them into frame times). Writes <SKSE log dir>/SkyrimPythonSpike_s9_<label>.txt.
std::string S9Sample(const std::string& args)
{
    std::istringstream in(args);
    std::string secText, label;
    in >> secText >> label;
    const int seconds = ParseInt(secText, -1);
    if (seconds < 10 || seconds > 120) return "S9_SAMPLE ERR usage: s9_sample <10..120 seconds> <label>";
    const bool labelOk = !label.empty() && label.size() <= 32 &&
        std::all_of(label.begin(), label.end(), [](char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_'; });
    if (!labelOk) return "S9_SAMPLE ERR label: 1..32 of a-z 0-9 _";
    const auto before = S9PauseState();
    if (before != "ok") return "S9_SAMPLE NOT_RUN (" + before + "): load the save, unpause, close every menu";
    const auto dir = SKSE::log::log_directory();
    if (!dir) return "S9_SAMPLE ERR no SKSE log directory";

    auto st = std::make_shared<S9Samples>();
    st->us.reserve(static_cast<std::size_t>(seconds) * 1200);
    const auto t0 = std::chrono::steady_clock::now();
    const auto end = t0 + std::chrono::seconds(seconds);
    long long posted = 0;
    auto next = t0;
    while (std::chrono::steady_clock::now() < end) {
        SKSE::GetTaskInterface()->AddTask([st, t0] {
            const auto us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count();
            std::lock_guard l(st->mx);
            st->us.push_back(us);
        });
        ++posted;
        next += std::chrono::milliseconds(1);
        std::this_thread::sleep_until(next);  // a coarse timer makes bursts; the clustering still works
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(500));  // let the last tasks run
    const auto after = S9PauseState();
    std::vector<long long> copy;
    {
        std::lock_guard l(st->mx);
        copy = st->us;
    }
    const auto path = *dir / std::format("SkyrimPythonSpike_s9_{}.txt", label);
    std::ofstream f(path, std::ios::trunc);
    f << std::format("# s9 label={} seconds={} posted={} ran={} paused_before={} paused_after={}\n", label, seconds, posted, copy.size(), before, after);
    for (const auto v : copy) f << v << '\n';
    f.close();
    return std::format("S9_SAMPLE {} label={} posted={} ran={} paused_after={} file={}",
                       after == "ok" && f ? "OK" : "INVALID", label, posted, copy.size(), after, path.string());
}
}
```
3. In `Handle`, before the fallback:
```cpp
    if (line == "py_arm_rt") {
        py::SetCaller(&CallRealTime);
        return "PY_ARM_RT OK";
    }
    if (line.rfind("s9_sample ", 0) == 0) return S9Sample(line.substr(10));
```
API used, all in `API-VERIFICATION.md`: `SKSE::GetTaskInterface()->AddTask(std::function<void()>)`, `VirtualMachine::DispatchStaticCall(const BSFixedString&, const BSFixedString&, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)` (lvalue smart pointer), `Variable::IsFloat/GetFloat`, `UI::GameIsPaused`, `SKSE::log::log_directory()` (optional). `std::format("{}", float)` is not used for output values here, so no fixed-point issue.

- [ ] **Step 3: The Python side**

`spikes/py/s9_threads.py`:
```python
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
```

`spikes/py/s9_spin.py`:
```python
# S9: one CPU-bound Python thread for 70 s (covers a 60 s measurement). Returns at once.
import threading
import time


def _s9_spin(seconds):
    end, n = time.monotonic() + seconds, 0
    while time.monotonic() < end:
        n += 1
    globals()["_s9_spin_iterations"] = n


_s9_spinner = threading.Thread(target=_s9_spin, args=(70,), name="s9-spin", daemon=True)
_s9_spinner.start()
result = "spinning 70 s"
```

`spikes/py/s9_latency.py`:
```python
# S9: latency of 1000 sequential _spike.call_rt calls from this thread. Run once with no spinner and
# once while spikes/py/s9_spin.py is running. Records the GIL switch interval.
import json
import sys
import threading
import time

import _spike

_lat = []
for _ in range(1000):
    _t = time.perf_counter()
    _spike.call_rt(10000)
    _lat.append((time.perf_counter() - _t) * 1000)
_lat.sort()
result = json.dumps({
    "n": len(_lat), "p50_ms": round(_lat[499], 3), "p99_ms": round(_lat[989], 3), "max_ms": round(_lat[-1], 3),
    "switchinterval_s": sys.getswitchinterval(),
    "spinner_running": any(t.name == "s9-spin" and t.is_alive() for t in threading.enumerate()),
})
```
These three were run while writing the plan against a Linux stand-in whose `call_rt` sleeps 2 ms: `s9_threads.py` gave `"verdict": "PASS", "calls": 8000`; `s9_latency.py` gave p50 2.06 ms alone and 7.12 ms with `s9_spin.py` running. Check they compile: `python3 -m py_compile spikes/py/s9_*.py`.

- [ ] **Step 4: Build, deploy, start Python, arm the caller**

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll
# quit the game first (console: qqq)
CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
tools/launch_game.sh
source tools/env.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "kDataLoaded" 600
python3 spikes/spike_client.py py_status     # "running" if Task 3's spike-autostart marker is present
python3 spikes/spike_client.py py_load       # skip both if py_status already says running
python3 spikes/spike_client.py py_init
for i in $(seq 1 30); do r=$(python3 spikes/spike_client.py py_status); case "$r" in *running*|*failed*) echo "$r"; break;; esac; sleep 1; done
python3 spikes/spike_client.py py_arm_rt
```
Expected: `PY_STATUS running`, `PY_ARM_RT OK`. Now load the throwaway save, go to the interior, unpause, close every menu.

- [ ] **Step 5: Correctness under concurrency**

```bash
python3 spikes/py_send.py -c "import _spike; result = _spike.call_rt(5000)"
python3 spikes/py_send.py spikes/py/s9_threads.py
python3 spikes/spike_client.py echo still-here
```
Expected: `OK <seconds since launch, e.g. 431.2>`; `OK {"verdict": "PASS", "calls": 8000, "expected": 8000, "errors": 0, ... "threads_still_running": 0, "seconds": <s>, "calls_per_s": <rate>, ...}`; `still-here`. Each call costs about one or two frames, so expect roughly 8 × (30..60) calls per second in total, i.e. a run of 20-60 s. A `"verdict": "FAIL"`, a `py_send` timeout (300 s), a hang or a crash is an **S9 FAIL**: record it and STOP. Note `backwards_values` (should be 0) and the rate.

- [ ] **Step 6: Frame time: baseline A, spin, baseline B**

Stand still; do not touch the game during the samples.
```bash
python3 spikes/spike_client.py s9_sample 60 base_a
python3 spikes/py_send.py spikes/py/s9_spin.py && python3 spikes/spike_client.py s9_sample 60 spin
python3 spikes/py_send.py -c "result = _s9_spinner.is_alive()"     # True: the 60 s sample ran inside the 70 s spin
for i in $(seq 1 30); do r=$(python3 spikes/py_send.py -c "result = _s9_spinner.is_alive()"); [ "$r" = "OK False" ] && break; sleep 2; done; echo "$r"
python3 spikes/spike_client.py s9_sample 60 base_b
mkdir -p out/s9 && cp "$SKSE_LOG_DIR"/SkyrimPythonSpike_s9_{base_a,spin,base_b}.txt out/s9/
python3 spikes/s9_analyze.py out/s9/SkyrimPythonSpike_s9_base_a.txt out/s9/SkyrimPythonSpike_s9_spin.txt out/s9/SkyrimPythonSpike_s9_base_b.txt
```
Expected: three `S9_SAMPLE OK label=... posted=<about 60000> ran=<same> ...` lines (`INVALID` means the game paused or a menu opened during the sample: redo that sample), `OK True`, then `OK False`, then a JSON line and `S9 frame time PASS`. `samples_per_frame` should be well above 1; if it is close to 1, the timer is coarse but the result is still valid.
- `S9 frame time INCONCLUSIVE`: the two baselines differ by more than 10%. Redo all three samples (standing still, same spot). After a second `INCONCLUSIVE`, record it and STOP and ask the owner (the machine is too noisy for a 10% test; the owner may accept a wider threshold).
- `S9 frame time FAIL`: record it and STOP. The spec's fallback (4.5) is to pace Python work or move heavy work to a worker process; the owner decides.

`out/` is gitignored: the raw sample files are not committed; the JSON line goes into the evidence column.

- [ ] **Step 7: Latency with and without a spinning thread**

```bash
python3 spikes/py_send.py spikes/py/s9_latency.py                                   # alone, switch interval 0.005
python3 spikes/py_send.py spikes/py/s9_spin.py && python3 spikes/py_send.py spikes/py/s9_latency.py   # spinning, 0.005
python3 spikes/py_send.py -c "import sys; sys.setswitchinterval(0.001)"
python3 spikes/py_send.py spikes/py/s9_latency.py                                   # spinning, 0.001 (check spinner_running)
python3 spikes/py_send.py -c "import sys; sys.setswitchinterval(0.005); result = sys.getswitchinterval()"
```
Expected: four JSON replies with `p50_ms`, `p99_ms`, `max_ms`, `switchinterval_s` and `spinner_running` (`false`, `true`, `true`). If the third run shows `"spinner_running": false` (the 70 s spin ended), send `s9_spin.py` again and repeat that run. Expect the spinning runs to add up to about one switch interval per call (about 5 ms, then about 1 ms). Nothing here is pass/fail; a hang or crash is a FAIL.

- [ ] **Step 8: Record the result**

Fill the S9 row of `spikes/RESULTS.md` (add it below S2 if the table has none):
```markdown
| S9 Python threads drive VM calls; frame time and latency under CPU-bound Python (Task 5) | <date> | PASS/FAIL | threads: `<s9_threads JSON>`; frames: `<s9_analyze JSON>` → `<verdict>`; latency p50/p99 ms: alone `<a>`, spin@5ms `<b>`, spin@1ms `<c>`; switch-interval recommendation for Task 24: `<keep 0.005 / set 0.001>`; runtime `<version>` | 4.5 (FAIL: pace Python work or run heavy work in a worker process; if calls hang, revisit the pending-result design; STOP and ask the owner) |
```
**PASS needs:** `s9_threads` verdict `PASS`, `S9 frame time PASS`, and no hang or crash in any step. Recommendation rule for the evidence column: if spinning p99 at 0.005 s is more than 3× the alone p99 and 0.001 s brings it within 2×, recommend that `skyrim._host` sets `sys.setswitchinterval(0.001)` (Task 24 decides; the owner can veto). Then quit the game (`qqq`).

- [ ] **Step 9: Commit**

```bash
git add spikes/spike-dll/src/main.cpp spikes/py/s9_threads.py spikes/py/s9_spin.py spikes/py/s9_latency.py spikes/s9_analyze.py spikes/RESULTS.md
git commit -m "test(spike): S9 Python threads driving VM calls, frame time and GIL hand-off latency"
```
