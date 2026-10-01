#!/usr/bin/env python3
"""S9 frame-time analysis of the spike's AddTask timestamp files.

  s9_analyze.py BASE_A SPIN BASE_B [COMBO] [--threshold 0.10] [--gap-ms 1.5]
  s9_analyze.py --selftest

Each file is written by the spike command `s9_sample`: a '# s9 key=value ...' header line (label, seconds,
pause state and the frame-cap state: iPresentInterval, displaytweaks, dxvk_frame_rate), then one integer
per line, the microseconds (steady clock) at which one AddTask ran on the main thread. The sampler posts
one task about every millisecond; the game runs queued tasks once per frame, so timestamps arrive in
clusters, one cluster per frame. A gap longer than --gap-ms starts a new cluster; frame time is the
distance between consecutive cluster starts.

Validity: with a coarse timer and frames shorter than the timer period, some frames get no sample and two
frames merge into one long "frame". A file is INVALID when frames < 0.95 * expected_frames
(expected_frames = header seconds * 1000 / median_ms) or samples_per_frame < 2.

Verdict: baseline = mean of BASE_A and BASE_B. If A and B differ by more than the threshold in median
or p99 frame time, the measurement is too noisy: INCONCLUSIVE (rerun standing still in one spot).
Otherwise PASS when SPIN's (and COMBO's, if given) median and p99 are each at most
baseline * (1 + threshold), else FAIL. long_frames (frames over twice the median) are reported per
1000 frames for every file. Capped (vsync iPresentInterval != 0, Display Tweaks loaded, a DXVK frame
limit, or an unknown cap state in any file): the median cannot move until the frame budget overflows,
so the verdict line says the verdict is carried by p99 and long_frames.
Prints one JSON line with all numbers, then the verdict line. Exit 0 PASS, 1 FAIL, 3 INCONCLUSIVE,
4 INVALID (redo the named sample).
"""
import json
import random
import sys


def load(path: str) -> tuple[list[int], dict]:
    out, header = [], {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("#"):
                for tok in line[1:].split():
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        header[k] = v
            elif line:
                out.append(int(line))
    return out, header


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


def stats(us: list[int], gap_ms: float, seconds: float) -> dict:
    ft, per = frames(us, gap_ms)
    s = sorted(ft)
    med = pct(s, 0.5)
    expected = seconds * 1000 / med
    long_frames = sum(1 for x in s if x > 2 * med)
    problems = []
    if len(s) < 0.95 * expected:
        problems.append(f"frames {len(s)} < 0.95 * expected {expected:.0f} (frames merged: timer coarser than a frame)")
    if per < 2:
        problems.append(f"samples_per_frame {per:.2f} < 2")
    return {"frames": len(s), "expected_frames": round(expected), "median_ms": round(med, 3), "p99_ms": round(pct(s, 0.99), 3),
            "max_ms": round(s[-1], 3), "samples_per_frame": round(per, 2), "long_frames": long_frames,
            "long_per_1k": round(1000 * long_frames / len(s), 2), "valid": not problems, "problems": problems}


def is_capped(header: dict) -> bool:
    return (header.get("iPresentInterval", "unknown") != "0" or header.get("displaytweaks", "unknown") != "absent"
            or header.get("dxvk_frame_rate", "unknown") != "unset")


def verdict(a: dict, b: dict, loaded: dict, threshold: float) -> tuple[str, dict]:
    """loaded: {"spin": stats, "combo": stats, ...}, each compared with the mean of a and b."""
    out = {}
    noisy = False
    ok = True
    for k in ("median_ms", "p99_ms"):
        base = (a[k] + b[k]) / 2
        noise = abs(a[k] - b[k]) / base
        out[k] = {"baseline": round(base, 3), "baseline_noise": round(noise, 3)}
        noisy |= noise > threshold
        for name, st in loaded.items():
            ratio = st[k] / base
            out[k][name] = st[k]
            out[k][f"{name}_over_baseline"] = round(ratio, 3)
            ok &= ratio <= 1 + threshold
    out["long_per_1k"] = {"baseline": round((a["long_per_1k"] + b["long_per_1k"]) / 2, 2),
                          **{name: st["long_per_1k"] for name, st in loaded.items()}}
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


def synth_coarse(frame_ms: float, timer_ms: float, seconds: float = 60) -> list[int]:
    """A sampler whose posts fire every timer_ms; each post runs at the start of the next frame."""
    out, post, frame = [], 0.0, 0.0
    while post < seconds * 1000:
        while frame < post:
            frame += frame_ms
        out.append(int(frame * 1000))
        post += timer_ms
    return out


def selftest() -> int:
    g = 1.5
    base_a, base_b = stats(synth(16.667, seed=1), g, 60), stats(synth(16.667, seed=2), g, 60)
    assert abs(base_a["median_ms"] - 16.667) < 0.5, base_a
    assert base_a["samples_per_frame"] == 10 and base_a["valid"], base_a
    assert verdict(base_a, base_b, {"spin": stats(synth(17.0, seed=3), g, 60)}, 0.10)[0] == "PASS"
    assert verdict(base_a, base_b, {"spin": stats(synth(20.0, seed=3), g, 60)}, 0.10)[0] == "FAIL"
    assert verdict(base_a, stats(synth(22.0, seed=4), g, 60), {"spin": stats(synth(17.0, seed=3), g, 60)}, 0.10)[0] == "INCONCLUSIVE"
    # a fourth (combo) sample is judged too: spin fine, combo slow -> FAIL
    assert verdict(base_a, base_b, {"spin": stats(synth(17.0, seed=3), g, 60), "combo": stats(synth(20.0, seed=5), g, 60)}, 0.10)[0] == "FAIL"
    assert verdict(base_a, base_b, {"spin": stats(synth(17.0, seed=3), g, 60), "combo": stats(synth(17.2, seed=5), g, 60)}, 0.10)[0] == "PASS"
    # one sample per frame still clusters correctly, but is flagged: fewer than 2 samples per frame
    one = stats(synth(16.667, per_frame=1), g, 60)
    assert abs(one["median_ms"] - 16.667) < 0.5 and not one["valid"], one
    # coarse timer (15.6 ms) with 13.9 ms frames: frames merge, p99 doubles; flagged INVALID
    coarse = stats(synth_coarse(13.9, 15.6), g, 60)
    assert coarse["p99_ms"] > 25 and not coarse["valid"], coarse
    assert any("expected" in p for p in coarse["problems"]), coarse
    # cap detection from the header
    assert is_capped({"iPresentInterval": "1", "displaytweaks": "absent", "dxvk_frame_rate": "unset"})
    assert is_capped({})
    assert not is_capped({"iPresentInterval": "0", "displaytweaks": "absent", "dxvk_frame_rate": "unset"})
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
    if len(args) not in (3, 4):
        print(__doc__)
        return 2
    names = ["base_a", "spin", "base_b", "combo"][: len(args)]
    st, headers = {}, {}
    for name, path in zip(names, args):
        us, h = load(path)
        if "seconds" not in h:
            print(f"S9 frame time INVALID: {path} has no 'seconds=' header (not an s9_sample file?)")
            return 4
        st[name], headers[name] = stats(us, gap, float(h["seconds"])), h
    capped = any(is_capped(h) for h in headers.values())
    cap_state = {n: {k: h.get(k, "unknown") for k in ("iPresentInterval", "displaytweaks", "dxvk_frame_rate")} for n, h in headers.items()}
    invalid = [f"{n}: {'; '.join(s['problems'])}" for n, s in st.items() if not s["valid"]]
    v, detail = verdict(st["base_a"], st["base_b"], {n: st[n] for n in names if n in ("spin", "combo")}, threshold)
    if invalid:
        v = "INVALID"
    print(json.dumps({**st, "compare": detail, "threshold": threshold, "capped": capped, "cap_state": cap_state, "invalid": invalid}))
    note = " (capped: median insensitive; verdict carried by p99 and long_frames)" if capped else " (uncapped)"
    print(f"S9 frame time {v}{note}" + (f": redo {', '.join(i.split(':')[0] for i in invalid)}" if invalid else ""))
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 3, "INVALID": 4}[v]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
