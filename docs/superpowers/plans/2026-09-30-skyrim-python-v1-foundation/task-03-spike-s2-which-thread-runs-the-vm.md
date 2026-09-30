# Task 3: Spike S2 — which thread runs the VM callback; dispatch from inside it; argument ownership

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M0 spike rules:** The spike DLL is throwaway code. It is written for speed, not quality, and never linked into the real plugin. Every spike records PASS/FAIL with evidence in `spikes/RESULTS.md`. **S1 must pass before anything else in this plan starts.**


**Files:**
- Modify: `spikes/spike-dll/src/main.cpp`

- [ ] **Step 1: Add the S2 handler**

Add above `Handle` in `main.cpp`:
```cpp
#include <atomic>
#include <future>
#include <sstream>
#include <thread>

namespace spike {
std::string Tid()
{
    std::ostringstream s;
    s << std::this_thread::get_id();
    return s.str();
}

class Functor : public RE::BSScript::IStackCallbackFunctor
{
public:
    explicit Functor(std::function<void(RE::BSScript::Variable)> f) : f_(std::move(f)) {}
    void operator()(RE::BSScript::Variable a_result) override { f_(a_result); }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}

private:
    std::function<void(RE::BSScript::Variable)> f_;
};

// Runs fn on the main thread via AddTask and waits up to timeout for it to call done().
std::string OnMainThread(std::function<void(std::function<void(std::string)>)> fn, std::chrono::seconds timeout)
{
    auto p = std::make_shared<std::promise<std::string>>();
    auto fut = p->get_future();
    auto once = std::make_shared<std::atomic_bool>(false);
    SKSE::GetTaskInterface()->AddTask([fn, p, once] {
        fn([p, once](std::string r) {
            if (!once->exchange(true)) p->set_value(std::move(r));
        });
    });
    if (fut.wait_for(timeout) != std::future_status::ready) return "TIMEOUT";
    return fut.get();
}

std::string S2(int n)
{
    auto done = std::make_shared<std::atomic_int>(0);
    auto nested = std::make_shared<std::atomic_int>(0);
    auto cbTid = std::make_shared<std::string>();
    std::string mainTid;
    auto t0 = std::chrono::steady_clock::now();
    auto r = OnMainThread([&, n, done, nested, cbTid](std::function<void(std::string)> finish) {
        mainTid = Tid();
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        for (int i = 0; i < n; ++i) {
            RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor(
                [vm, n, done, nested, cbTid, finish](RE::BSScript::Variable) {
                    if (cbTid->empty()) *cbTid = Tid();
                    // Nested dispatch from inside the callback (the S2 question).
                    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb2{ new Functor(
                        [n, done, nested, finish](RE::BSScript::Variable v) {
                            if (v.IsInt()) nested->fetch_add(1);
                            if (done->fetch_add(1) + 1 == n) finish("ok");
                        }) };
                    auto* args2 = RE::MakeFunctionArguments(static_cast<std::int32_t>(1), static_cast<std::int32_t>(6));
                    vm->DispatchStaticCall("Utility"sv, "RandomInt"sv, args2, cb2);
                }) };
            auto* args = RE::MakeFunctionArguments();
            vm->DispatchStaticCall("Utility"sv, "GetCurrentRealTime"sv, args, cb);
        }
    }, std::chrono::seconds(60));
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count();
    return std::format("S2 {} n={} completed={} nested_ints={} main_tid={} callback_tid={} elapsed_ms={}",
        r, n, done->load(), nested->load(), mainTid, *cbTid, ms);
}
}
```
And in `Handle`, before the fallback:
```cpp
    if (line.rfind("s2", 0) == 0) return S2(line.size() > 3 ? std::stoi(line.substr(3)) : 1000);
```

- [ ] **Step 2: Build, deploy, relaunch, run**

```bash
tools/remote_build.sh spikes/spike-dll SkyrimPythonSpike.dll && tools/deploy.sh out/SkyrimPythonSpike.dll
# quit the game first (console: qqq), then:
tools/launch_game.sh
# load a save / coc into a cell so the game is running, then:
python3 spikes/spike_client.py s2 1000
python3 spikes/spike_client.py s2 10000
```
Expected PASS: `S2 ok n=1000 completed=1000 nested_ints=1000`, `callback_tid` recorded (compare to `main_tid`), no hang, and the game still responsive after the 10000 run. While the 10000 run executes, watch the game's memory in `top -p $(pgrep -f SkyrimSE.exe)`: RES must not grow by more than ~50 MB after the run completes (tests that the VM frees `MakeFunctionArguments` objects; a steady per-call growth means we must free them ourselves).

- [ ] **Step 3: Record and commit**

Add rows to `spikes/RESULTS.md` for S2 (callback thread, nested dispatch works?, per-call latency = elapsed/n, memory before/after). If nested dispatch hangs or crashes: record FAIL; Task 18's `batch` then uses a fresh `AddTask` per step (already the plan's default, so no re-plan needed).
```bash
git add spikes && git commit -m "test(spike): S2 VM callback thread, nested dispatch, argument ownership"
```
