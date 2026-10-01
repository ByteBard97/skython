# Task 4: Spike S2 — which thread runs the VM callback; dispatch from inside it; argument ownership

Adapted from: branch `plan-out-of-process`, task 3 (`task-03-spike-s2-which-thread-runs-the-vm.md`). The C++ is unchanged; gates, task numbers and the consumers of the findings changed.

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.3 and 4.5). Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLLs are throwaway code. They are written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. S1 gates Task 3; S8 gates Tasks 18-32; **S2, S9 and S4 gate Part C (Tasks 25-32)**; S7 gates Tasks 28 and 29; S5, S6 and S10 gate only the M2 plan. **S2 FAIL of the main criteria (hang, crash, nested dispatch never completing) is a STOP: ask the owner** (the plan's fallback for nested dispatch is a fresh `AddTask` per batch step, which Task 30 already does, but the owner confirms it). Run on a throwaway save; the spike port is unauthenticated.

**Why this still matters in the embedded design:** Python never runs on the VM's threads (Global Constraints). The VM callback only completes a `Dispatcher` ticket (Task 18's `Done`: a mutex and a condition variable, no Python). S2 tells Task 18 and Task 27 *which* thread calls `Done`, whether a result that holds a Form can be converted right there or needs one more `AddTask`, whether a nested dispatch from a callback works (Task 30's `batch`), and who frees the arguments object.

**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

**Interfaces:**
- Consumes (Tasks 1-3): `spike::Log`, `spike::ParseInt`, `spike::Handle`, `spikes/spike_client.py` (Task 2). The includes `<algorithm> <atomic> <chrono> <cstdint> <format> <functional> <future> <memory> <mutex> <sstream> <string> <thread> <vector>` are already at the top of `main.cpp` (Task 1); add **no** includes inside `namespace spike`. Task 3's Python commands (`py_load`, `py_init`, `py_exec`) stay in the spike DLL untouched; S2 does not use them.
- Produces (used by Tasks 5, 6, 7, 8, 9): `spike::Tid()`, `spike::TidOf(id)`, `spike::g_mainTid`, `spike::Functor`, `spike::OnMainThread(fn, timeout)`, `spike::WaitUntil(pred, timeout)`, `spike::CountedArgs` (an `IFunctionArguments` that counts constructions and destructions), `spike::NoArgs()`, `spike::IntArgs(a, b)`, `spike::FloatArg(f)`.
- Produces for Tasks 18, 27, 29 and 30 (in the S2 row of `spikes/RESULTS.md`): the callback-thread answer for native and latent functions (which thread calls the `Dispatcher`'s `Done`; whether `GameBackend` needs one extra `AddTask` to convert a Form result), the argument-ownership answer on success and on a failed dispatch (whether Task 27's `CallStatic` and Task 29's `CallMethod` must `delete` the `DynArgs` when `Dispatch*` returns false), and the nested-dispatch answer (Task 30's batch).

- [ ] **Step 0: Check the gates, and prepare the game**

```bash
grep -E '^\| S1 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S1 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
grep -E '^\| S8 [^|]*\|[^|]*\| PASS ' spikes/RESULTS.md || { echo "no S8 PASS row in spikes/RESULTS.md: STOP and ask the owner"; false; }
```
Expected: both rows print. If either does not, **STOP and ask the owner**; do not continue (an S8 FAIL means the owner decides whether the project falls back to the out-of-process design on branch `plan-out-of-process`). Then load the throwaway save, and **leave the game unpaused with no menu open** (no inventory, map, console or journal): while paused, Papyrus does not advance and no callback fires, so the spike would time out for an incidental reason (spec 4.1). The S2 handler refuses to run while the game is paused.

- [ ] **Step 1: Add the S2 handler**

Add this block to `main.cpp` as a new `namespace spike { ... }` block above `Handle` (no new includes; they are at the top of the file):
```cpp
namespace spike {
using Var = RE::BSScript::Variable;

std::atomic<std::thread::id> g_mainTid{};  // set by the first main-thread task; read from VM threads

std::string TidOf(std::thread::id id)
{
    std::ostringstream s;
    s << id;
    return s.str();
}
std::string Tid() { return TidOf(std::this_thread::get_id()); }

class Functor : public RE::BSScript::IStackCallbackFunctor
{
public:
    explicit Functor(std::function<void(Var)> f) : f_(std::move(f)) {}
    void operator()(Var a_result) override { f_(a_result); }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}

private:
    std::function<void(Var)> f_;
};

// Arguments for one dispatch, with construction and destruction counted. Task 27's DynArgs has the
// same shape. Whether the VM destroys it (on success AND when Dispatch* returns false) is measured
// with these counters, not guessed from memory use.
class CountedArgs final : public RE::BSScript::IFunctionArguments
{
public:
    static inline std::atomic_int ctor{0}, dtor{0};
    explicit CountedArgs(std::vector<Var> v) : v_(std::move(v)) { ctor.fetch_add(1); }
    CountedArgs(const CountedArgs&) = delete;  // a copy would make the counters lie
    ~CountedArgs() { dtor.fetch_add(1); }
    bool operator()(RE::BSScrapArray<Var>& dst) const override
    {
        dst.resize(static_cast<std::uint32_t>(v_.size()));
        for (std::size_t i = 0; i < v_.size(); ++i) dst[static_cast<std::uint32_t>(i)] = v_[i];
        return true;
    }

private:
    std::vector<Var> v_;
};
CountedArgs* NoArgs() { return new CountedArgs({}); }
CountedArgs* IntArgs(std::int32_t a, std::int32_t b)
{
    std::vector<Var> v(2);
    v[0].SetSInt(a);
    v[1].SetSInt(b);
    return new CountedArgs(std::move(v));
}
CountedArgs* FloatArg(float f)
{
    std::vector<Var> v(1);
    v[0].SetFloat(f);
    return new CountedArgs(std::move(v));
}

// Runs fn on the main thread via AddTask and waits up to timeout for it to call finish().
// fn is copied into the task and captures only shared_ptrs/values: after a TIMEOUT the caller's stack
// is gone, so nothing the task or a late callback touches may live there.
std::string OnMainThread(std::function<void(std::function<void(std::string)>)> fn, std::chrono::seconds timeout)
{
    auto p = std::make_shared<std::promise<std::string>>();
    auto fut = p->get_future();
    auto once = std::make_shared<std::atomic_bool>(false);
    SKSE::GetTaskInterface()->AddTask([fn, p, once] {
        auto finish = [p, once](std::string r) {
            if (!once->exchange(true)) p->set_value(std::move(r));
        };
        g_mainTid.store(std::this_thread::get_id());
        try {
            fn(finish);
        } catch (...) {
            finish("EXCEPTION in main-thread task");
        }
    });
    if (fut.wait_for(timeout) != std::future_status::ready) return "TIMEOUT";
    return fut.get();
}

template <class Pred>
bool WaitUntil(Pred pred, std::chrono::seconds timeout)
{
    const auto end = std::chrono::steady_clock::now() + timeout;
    while (!pred()) {
        if (std::chrono::steady_clock::now() >= end) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    return true;
}

// Everything a callback touches lives here, owned by shared_ptr: a callback that fires after the
// spike gave up (TIMEOUT) still has valid memory. Nothing is captured by reference.
struct S2State
{
    std::atomic_int nestedDone{0}, nestedInts{0}, nestedFailed{0}, outerFailed{0}, latentDone{0};
    std::atomic_int cbOnMain{0}, cbOffMain{0}, nestedOnMain{0}, nestedOffMain{0}, latentOnMain{0}, latentOffMain{0};
    std::atomic_int failedFalse{0}, failedTrue{0}, latentRefused{0};
    std::mutex mx;
    std::string firstCbTid, firstNestedTid, firstLatentTid;
};

void NoteThread(S2State& st, std::atomic_int& on, std::atomic_int& off, std::string& first)
{
    (std::this_thread::get_id() == g_mainTid.load() ? on : off).fetch_add(1);
    std::lock_guard l(st.mx);
    if (first.empty()) first = Tid();
}

std::atomic_int g_s2Mode{0};  // phase C probe: 0 = existing script, missing function; 1 = existing native, wrong argument count
constexpr int kChunk = 100;  // dispatches per main-thread task: 10,000 in one task can hit VM stack limits

void DispatchChunk(const std::shared_ptr<S2State>& st, int count)
{
    auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
    for (int i = 0; i < count; ++i) {
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([st](Var) {
            NoteThread(*st, st->cbOnMain, st->cbOffMain, st->firstCbTid);
            // Nested dispatch from inside the callback (the S2 question).
            auto* vm2 = RE::BSScript::Internal::VirtualMachine::GetSingleton();
            RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb2{ new Functor([st](Var v) {
                NoteThread(*st, st->nestedOnMain, st->nestedOffMain, st->firstNestedTid);
                if (v.IsInt()) st->nestedInts.fetch_add(1);
                st->nestedDone.fetch_add(1);
            }) };
            if (!vm2->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("RandomInt"), IntArgs(1, 6), cb2))
                st->nestedFailed.fetch_add(1);
        }) };
        if (!vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("GetCurrentRealTime"), NoArgs(), cb))
            st->outerFailed.fetch_add(1);
    }
}

std::string S2(int n)
{
    if (n < 1 || n > 100000) return "S2 ERR n out of range 1..100000";
    auto st = std::make_shared<S2State>();

    // Preconditions: a game is running, unpaused, no menu open (spec 4.1: paused means no callbacks).
    auto pre = OnMainThread([](std::function<void(std::string)> finish) {
        auto* ui = RE::UI::GetSingleton();  // GameIsPaused: verify by grepping plugin/extern/CommonLibSSE-NG
        finish(!ui ? "NO_UI" : ui->GameIsPaused() ? "PAUSED" : "ok");
    }, std::chrono::seconds(10));
    if (pre != "ok")
        return "S2 NOT_RUN (" + pre + "): load the throwaway save, unpause, close every menu, then retry";
    const auto mainTid = TidOf(g_mainTid.load());
    Log("spike: S2 main thread id " + mainTid);

    const int c0 = CountedArgs::ctor, d0 = CountedArgs::dtor;
    const auto t0 = std::chrono::steady_clock::now();
    std::string verdictNote = "ok";

    // Phase A: n non-latent calls with a nested dispatch in every callback, posted in chunks.
    for (int base = 0; base < n; base += kChunk) {
        const int cnt = std::min(kChunk, n - base);
        auto r = OnMainThread([st, cnt](std::function<void(std::string)> finish) {
            DispatchChunk(st, cnt);
            finish("posted");
        }, std::chrono::seconds(10));
        if (r != "posted") { verdictNote = "chunk task " + r; break; }
        if (!WaitUntil([&] { return st->nestedDone.load() >= base + cnt; }, std::chrono::seconds(30))) {
            verdictNote = "chunk never completed (TIMEOUT); callbacks may still arrive later";
            break;
        }
    }
    const auto elapsedMs = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();

    Log("spike: S2 phase A done");
    // Phase B: a LATENT function. Utility.Wait suspends the script stack, so its callback is
    // not guaranteed to run inline on the dispatching thread the way a native's may.
    constexpr int kLatent = 10;
    auto lr = OnMainThread([st](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (int i = 0; i < kLatent; ++i) {
            RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([st](Var) {
                NoteThread(*st, st->latentOnMain, st->latentOffMain, st->firstLatentTid);
                st->latentDone.fetch_add(1);
            }) };
            if (!vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("Wait"), FloatArg(0.1f), cb))
                st->latentRefused.fetch_add(1);
        }
        finish("posted");
    }, std::chrono::seconds(10));
    const bool latentOk = lr == "posted" && WaitUntil([&] { return st->latentDone.load() >= kLatent; }, std::chrono::seconds(20));

    Log("spike: S2 phase B done");
    // Settle, then take the success-path verdict BEFORE phase C starts. The VM may free an arguments
    // object when the stack is torn down, just after the callback fires; without the settle, late
    // destructions from phases A and B would land in phase C's bucket.
    std::this_thread::sleep_for(std::chrono::seconds(3));  // spike only
    const int cC = CountedArgs::ctor, dC = CountedArgs::dtor;
    const int ctorOk = cC - c0, dtorOk = dC - d0;
    const int liveOk = ctorOk - dtorOk;
    // Logged BEFORE phase C: a failed-dispatch probe crashed the engine once (SkyrimSE.exe+1447D3E), and this must survive that.
    Log(std::format("spike: S2 AB main_tid={} nested_done={}/{} nested_ints={} outer_failed={} nested_failed={} latent={}/{} latent_refused={} "
        "cb_first_tid={} cb_on_main={} cb_off_main={} nested_first_tid={} nested_on_main={} nested_off_main={} latent_first_tid={} latent_on_main={} latent_off_main={} "
        "args_ctor_ok={} args_dtor_ok={} live_ok={} owner_ok_path={} elapsed_ms={}",
        mainTid, st->nestedDone.load(), n, st->nestedInts.load(), st->outerFailed.load(), st->nestedFailed.load(), st->latentDone.load(), kLatent,
        st->latentRefused.load(), st->firstCbTid, st->cbOnMain.load(), st->cbOffMain.load(), st->firstNestedTid, st->nestedOnMain.load(),
        st->nestedOffMain.load(), st->firstLatentTid, st->latentOnMain.load(), st->latentOffMain.load(), ctorOk, dtorOk, liveOk,
        liveOk == 0 ? "vm" : "NOT_FREED", elapsedMs));

    // Phase C: the FAILED-dispatch path, probed SAFELY. A script that does not exist crashed the engine
    // (SkyrimSE.exe+1447D3E, null deref inside DispatchStaticCall), so the target must resolve: mode 0 is an existing
    // script with a missing function, mode 1 an existing native with the wrong argument count.
    // Does the VM still destroy the arguments object when Dispatch* returns false, or must the caller?
    const int mode = g_s2Mode.load();
    Log(std::format("spike: S2 phase C starting, mode={}", mode));
    (void)OnMainThread([st, mode](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (int i = 0; i < 100; ++i) {
            RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([](Var) {}) };
            const bool ok = mode == 0
                ? vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("NoSuchFn"), NoArgs(), cb)
                : vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("RandomInt"), FloatArg(1.0f), cb);
            (ok ? st->failedTrue : st->failedFalse).fetch_add(1);
        }
        finish("posted");
    }, std::chrono::seconds(10));
    std::this_thread::sleep_for(std::chrono::seconds(3));  // spike only: let the VM release anything it is going to release

    // failed path = everything after the first settle (phase C only)
    const int ctorFail = CountedArgs::ctor - cC, dtorFail = CountedArgs::dtor - dC;
    const int liveFail = ctorFail - dtorFail;

    const bool threadPhase = verdictNote == "ok" && st->nestedDone == n && st->nestedInts == n &&
        st->nestedFailed == 0 && st->outerFailed == 0 && st->latentRefused == 0 && latentOk;
    const bool ownsOk = liveOk == 0;
    const char* failPathOwner = st->failedFalse == 0 ? "n/a(no dispatch returned false)" : liveFail == 0 ? "vm" : "CALLER(Tasks 27/29 must delete on false)";
    return std::format(
        "S2 {} note={} n={} nested_done={} nested_ints={} outer_failed={} nested_failed={} elapsed_ms={} "
        "main_tid={} first_cb_tid={} cb_on_main={} cb_off_main={} first_nested_tid={} nested_on_main={} nested_off_main={} "
        "latent={}/{} latent_refused={} first_latent_tid={} latent_on_main={} latent_off_main={} "
        "args_ctor_ok={} args_dtor_ok={} live_ok={} owner_ok_path={} failed_dispatch_false={} failed_dispatch_true={} live_failed_path={} owner_failed_path={}",
        (threadPhase && ownsOk) ? "PASS" : "FAIL", verdictNote, n, st->nestedDone.load(), st->nestedInts.load(),
        st->outerFailed.load(), st->nestedFailed.load(), elapsedMs, mainTid, st->firstCbTid, st->cbOnMain.load(),
        st->cbOffMain.load(), st->firstNestedTid, st->nestedOnMain.load(), st->nestedOffMain.load(), st->latentDone.load(),
        kLatent, st->latentRefused.load(), st->firstLatentTid, st->latentOnMain.load(), st->latentOffMain.load(), ctorOk, dtorOk,
        liveOk, ownsOk ? "vm" : "NOT_FREED", st->failedFalse.load(), st->failedTrue.load(), liveFail, failPathOwner);
}
}
```
And in `Handle`, before the fallback:
```cpp
    if (line == "s2mode0" || line == "s2mode1") { g_s2Mode = line.back() - '0'; return "OK s2 phase C mode " + line.substr(6); }
    if (line.rfind("s2", 0) == 0) return S2(ParseInt(line.size() > 3 ? line.substr(3) : "", 1000));
```
API names to confirm by grepping `plugin/extern/CommonLibSSE-NG` before building (record findings in `docs/PINS.md`): `RE::UI::GameIsPaused`, `Variable::SetSInt/SetFloat/IsInt`, `IFunctionArguments` (its virtual destructor and whether `operator()` is `const`), `BSScrapArray::resize`, and that `DispatchStaticCall` accepts a `BSFixedString` first/second argument and a non-const `BSTSmartPointer<IStackCallbackFunctor>&`. Fix compile errors by matching the header, not by guessing.

- [ ] **Step 2: Build, deploy, relaunch, run**

Only the spike DLL changes; the Python runtime and `SpikePyCore.dll` deployed by Task 3 stay where they are.
```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && CONFIRM_THROWAWAY_SAVE=1 tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game first (console: qqq), then:
tools/launch_game.sh
source tools/env.sh
SINCE="$SKYRIM_PYTHON_STATE/launch.stamp" tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPythonSpike.log" "listening on 127.0.0.1:45978" 600
# Load the throwaway save with Continue/Load, wait for gameplay, UNPAUSED, no menu open, then:
python3 spikes/spike_client.py s2 1000
python3 spikes/spike_client.py s2 10000
```
Expected PASS (first line of the run): `S2 PASS note=ok n=1000 nested_done=1000 nested_ints=1000 outer_failed=0 nested_failed=0 ... latent=10/10 ... live_ok=0 owner_ok_path=vm ...`, with `first_cb_tid`, `first_nested_tid` and `first_latent_tid` recorded next to `main_tid`, and the game still responsive after the 10000 run. The client's receive timeout is 180 s, so a slow 10000 run is not cut off; the DLL bounds itself (each 100-call chunk must finish in 30 s). `S2 NOT_RUN (PAUSED)` means a menu is open: close it and retry.

PASS criteria (all must hold):
1. every nested dispatch completed (`nested_done == n`, `nested_ints == n`) with no refused dispatch;
2. all 10 latent `Utility.Wait(0.1)` callbacks arrived;
3. **argument ownership:** on the success path `live_ok == 0` (every `CountedArgs` built in phases A and B was destroyed by the VM; it is counted after a 3 s settle and **before** phase C starts, so late destructions cannot fall into the failed-path bucket). `live_failed_path` is counted from that split point after a second 3 s settle. Separately record `owner_failed_path`: `vm` means the VM also destroys the arguments when `Dispatch*` returns false; `CALLER(...)` means it does not, and Task 27's `CallStatic` and Task 29's `CallMethod` must `delete` the `DynArgs` when `Dispatch*` returns false (write this into the RESULTS.md row; those tasks read it);
4. no hang or crash.

The thread answers are findings, not pass/fail: record whether callbacks ran on the main thread (`cb_on_main`/`cb_off_main`) for the native case and separately for the latent case. If the latent callbacks come from a different thread than the native ones, `GameBackend` (Task 27) must not assume either: the `Dispatcher`'s `Done` (Task 18) is already safe from any thread, and must stay free of Python and of engine access. Also check the logged thread ids against `main_tid`: Task 27 converts object results with one extra `AddTask` only when the callback is off the main thread.

Memory (informational only, **not a PASS criterion**: a leaked `DynArgs` is about 100 bytes, roughly 1 MB per 10,000 calls, below RSS noise; the counters above are the ownership verdict). Under Proton several processes match `SkyrimSE`, so select by exact name and confirm there is one PID:
```bash
PID=$(pgrep -x SkyrimSE.exe); echo "pid(s): $PID"   # must print exactly one PID; otherwise choose from: ps -eo pid,comm | grep -i skyrim
rss() { awk '/VmRSS/ {print $2 " kB"}' /proc/$PID/status; }
python3 spikes/spike_client.py s2 1000  >/dev/null; rss   # warm-up run; ignore this number
python3 spikes/spike_client.py s2 10000 >/dev/null; rss   # A
python3 spikes/spike_client.py s2 10000 >/dev/null; rss   # B
```
Record A and B. A steady growth of many MB per run (B minus A over about 20 MB) is worth a note in the evidence column; it is not a FAIL on its own.

- [ ] **Step 3: Record and commit**

Fill the S2 row in `spikes/RESULTS.md`: date, PASS/FAIL, the full output line (thread ids for native and latent, nested result, `live_ok`, `owner_failed_path`), A and B, and "spec section changed": 4.3/4.5 if nested dispatch failed, and Tasks 27/29 (`DynArgs` delete-on-false) if `owner_failed_path` is `CALLER`. **If nested dispatch hangs, crashes or never completes, or the game crashes: record FAIL and STOP and ask the owner** (the plan's fallback is Task 30's `batch` using a fresh `AddTask` per step, which is already the default, so the owner's answer is expected to be "proceed", but it is theirs to give).
```bash
git add spikes && git commit -m "test(spike): S2 VM callback thread, nested dispatch, latent callback, argument ownership"
```

> **Measured on the box (2026-10-01, Windows-native), ruling R35:** the original phase C (a script that does not exist) crashed the engine at `SkyrimSE.exe+1447D3E` (null deref inside `DispatchStaticCall`), and so did an existing script with a missing function (`Utility.NoSuchFn`). The code above is the safe version: phase A/B results are logged before phase C (`spike: S2 AB ...`), and `s2mode1` probes a wrong argument type/count on an existing native, which the VM accepts (`Dispatch*` returned true, no crash). Never `Dispatch*Call` an unvalidated class or function.
