#include "Net.h"
#include "PyLoad.h"

#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <format>
#include <fstream>
#include <functional>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

// S7 tid probe (R49): GetCurrentThreadId, GetWindowThreadProcessId, EnumWindows.
#include <windows.h>

// windows.h (pulled in by RE/Skyrim.h) defines GetObject as a macro for GetObjectA, which turns every
// RE::BSScript::Variable::GetObject() call into "GetObjectA is not a member" (compile error C2039). Undo it
// in every translation unit that includes RE/Skyrim.h and calls GetObject().
#undef GetObject

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 0, 1, 0 },
    .Name = "SkyrimPythonSpike"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace spike {
void Log(const std::string& line) { spdlog::info("{}", line); }

// Never call std::stoi on client input: it throws, and an exception out of the listener thread kills the game.
int ParseInt(const std::string& s, int fallback)
{
    try {
        std::size_t used = 0;
        const int v = std::stoi(s, &used);
        return used == s.size() ? v : fallback;
    } catch (...) {
        return fallback;
    }
}
}

namespace spike {
std::atomic_bool g_dataLoaded{ false };  // set in OnMessage at kDataLoaded; py_init refuses before it
}

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
    std::atomic_int dDone{0}, dMismatch{0}, dRefused{0}, dExc{0}, dOnMain{0}, dOffMain{0};
    std::mutex mx;
    std::string firstCbTid, firstNestedTid, firstLatentTid, firstDTid;
};

void NoteThread(S2State& st, std::atomic_int& on, std::atomic_int& off, std::string& first)
{
    (std::this_thread::get_id() == g_mainTid.load() ? on : off).fetch_add(1);
    std::lock_guard l(st.mx);
    if (first.empty()) first = Tid();
}

std::atomic_int g_s2Mode{-1};  // phase C probe, OFF by default (it can crash the engine): 0 = existing script, missing function; 1 = existing native, wrong argument count
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
        "args_ctor_ok={} args_dtor_ok={} args_live_after_settle={} vm_frees_args={} elapsed_ms={}",
        mainTid, st->nestedDone.load(), n, st->nestedInts.load(), st->outerFailed.load(), st->nestedFailed.load(), st->latentDone.load(), kLatent,
        st->latentRefused.load(), st->firstCbTid, st->cbOnMain.load(), st->cbOffMain.load(), st->firstNestedTid, st->nestedOnMain.load(),
        st->nestedOffMain.load(), st->firstLatentTid, st->latentOnMain.load(), st->latentOffMain.load(), ctorOk, dtorOk, liveOk,
        liveOk == 0 ? "true" : "false", elapsedMs));

    // Phase D: the plugin owns the arguments. Utility.RandomInt(5, 5) always returns 5; each callback records its
    // result and THEN deletes that call's args object (never before the callback). The spike deletes on a refused dispatch only because it is throwaway; the plugin keeps the args on a false return (R37).
    constexpr int kD = 1000;
    const int cD0 = CountedArgs::ctor, dD0 = CountedArgs::dtor;
    for (int base = 0; base < kD; base += kChunk) {
        auto r = OnMainThread([st](std::function<void(std::string)> finish) {
            auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
            for (int i = 0; i < kChunk; ++i) {
                CountedArgs* args = IntArgs(5, 5);
                RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([st, args](Var v) {
                    try {
                        NoteThread(*st, st->dOnMain, st->dOffMain, st->firstDTid);
                        if (!v.IsInt() || v.GetSInt() != 5) st->dMismatch.fetch_add(1);
                    } catch (...) {
                        st->dExc.fetch_add(1);
                    }
                    delete args;  // after the result was read, never before
                    st->dDone.fetch_add(1);
                }) };
                if (!vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("RandomInt"), args, cb)) {
                    delete args;
                    st->dRefused.fetch_add(1);
                }
            }
            finish("posted");
        }, std::chrono::seconds(10));
        if (r != "posted" || !WaitUntil([&] { return st->dDone.load() + st->dRefused.load() >= base + kChunk; }, std::chrono::seconds(30))) break;
    }
    std::this_thread::sleep_for(std::chrono::seconds(3));  // spike only
    const int ctorD = CountedArgs::ctor - cD0, dtorD = CountedArgs::dtor - dD0;
    const bool dPass = st->dDone == kD && st->dMismatch == 0 && st->dRefused == 0 && st->dExc == 0 && ctorD == dtorD && ctorD == kD;
    const std::string dLine = std::format("D {} done={}/{} mismatches={} refused={} exceptions={} args_ctor_d={} args_dtor_d={} first_d_tid={} d_on_main={} d_off_main={}",
        dPass ? "PASS" : "FAIL", st->dDone.load(), kD, st->dMismatch.load(), st->dRefused.load(), st->dExc.load(), ctorD, dtorD, st->firstDTid,
        st->dOnMain.load(), st->dOffMain.load());
    Log("spike: S2 " + dLine);  // before phase C

    const int mode = g_s2Mode.load();
    const int cC2 = CountedArgs::ctor, dC2 = CountedArgs::dtor;
    // Phase C: the FAILED-dispatch path, probed SAFELY. A script that does not exist crashed the engine
    // (SkyrimSE.exe+1447D3E, null deref inside DispatchStaticCall), so the target must resolve: mode 0 is an existing
    // script with a missing function, mode 1 an existing native with the wrong argument count.
    // Does the VM still destroy the arguments object when Dispatch* returns false, or must the caller?
    Log(std::format("spike: S2 phase C starting, mode={}", mode));
    if (mode >= 0) (void)OnMainThread([st, mode](std::function<void(std::string)> finish) {
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
    const int ctorFail = CountedArgs::ctor - cC2, dtorFail = CountedArgs::dtor - dC2;
    const int liveFail = ctorFail - dtorFail;

    const bool threadPhase = verdictNote == "ok" && st->nestedDone == n && st->nestedInts == n &&
        st->nestedFailed == 0 && st->outerFailed == 0 && st->latentRefused == 0 && latentOk;
    const bool ownsOk = liveOk == 0;
    const char* failPathOwner = st->failedFalse == 0 ? "n/a(no dispatch returned false)" : liveFail == 0 ? "vm" : "NOT_MEASURED (Tasks 27/29 keep on false, R37)";
    return std::format(
        "S2 {} note={} n={} nested_done={} nested_ints={} outer_failed={} nested_failed={} elapsed_ms={} "
        "main_tid={} first_cb_tid={} cb_on_main={} cb_off_main={} first_nested_tid={} nested_on_main={} nested_off_main={} "
        "latent={}/{} latent_refused={} first_latent_tid={} latent_on_main={} latent_off_main={} "
        "args_ctor_ok={} args_dtor_ok={} args_live_after_settle={} vm_frees_args={} failed_dispatch_false={} failed_dispatch_true={} live_failed_path={} owner_failed_path={} {}",
        (threadPhase && dPass) ? "PASS" : "FAIL", verdictNote, n, st->nestedDone.load(), st->nestedInts.load(),
        st->outerFailed.load(), st->nestedFailed.load(), elapsedMs, mainTid, st->firstCbTid, st->cbOnMain.load(),
        st->cbOffMain.load(), st->firstNestedTid, st->nestedOnMain.load(), st->nestedOffMain.load(), st->latentDone.load(),
        kLatent, st->latentRefused.load(), st->firstLatentTid, st->latentOnMain.load(), st->latentOffMain.load(), ctorOk, dtorOk,
        liveOk, ownsOk ? "true" : "false", st->failedFalse.load(), st->failedTrue.load(), liveFail, failPathOwner, dLine);
}
}

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

// Frame-cap state, written into every s9 sample header. Skyrim runs vsync-capped by default
// (iPresentInterval=1); under vsync or any frame limiter the median frame time cannot move until the frame
// budget overflows, so s9_analyze.py reports such a run as capped (verdict carried by p99 and long frames).
// iPresentInterval is read on the main thread from the game's own settings (SkyrimPrefs.ini's collection
// first, then Skyrim.ini's); Display Tweaks is detected by its DLL; DXVK's limiter by DXVK_FRAME_RATE.
std::string S9CapState()
{
    auto pi = std::make_shared<std::string>("unknown");
    auto vs = std::make_shared<std::string>("unknown");  // the box's SkyrimPrefs.ini key is iVSyncPresentInterval
    (void)OnMainThread([pi, vs](std::function<void(std::string)> finish) {
        auto read = [](const char* name) -> RE::Setting* {
            RE::Setting* s = nullptr;
            if (auto* prefs = RE::INIPrefSettingCollection::GetSingleton()) s = prefs->GetSetting(name);
            if (!s)
                if (auto* ini = RE::INISettingCollection::GetSingleton()) s = ini->GetSetting(name);
            return s;
        };
        if (auto* s = read("iPresentInterval:Display")) *pi = std::to_string(s->GetInteger());
        if (auto* s = read("iVSyncPresentInterval:Display")) *vs = std::to_string(s->GetInteger());
        finish("ok");
    }, std::chrono::seconds(10));
    char dxvk[64] = {};
    const DWORD n = GetEnvironmentVariableA("DXVK_FRAME_RATE", dxvk, sizeof dxvk);
    const std::string dxvkText = (n > 0 && n < sizeof dxvk) ? std::string(dxvk) : std::string("unset");
    return std::format("iPresentInterval={} iVSyncPresentInterval={} displaytweaks={} dxvk_frame_rate={}", *pi, *vs,
                       GetModuleHandleW(L"SSEDisplayTweaks.dll") ? "loaded" : "absent", dxvkText);
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
    const auto cap = S9CapState();

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
    f << std::format("# s9 label={} seconds={} posted={} ran={} paused_before={} paused_after={} {}\n", label, seconds, posted, copy.size(), before, after, cap);
    for (const auto v : copy) f << v << '\n';
    f.close();
    return std::format("S9_SAMPLE {} label={} posted={} ran={} paused_after={} {} file={}",
                       after == "ok" && f ? "OK" : "INVALID", label, posted, copy.size(), after, cap, path.string());
}
}

namespace spike {
struct AliasCand
{
    RE::FormID quest = 0, original = 0;  // ids only: nothing here is an engine pointer
    std::uint32_t alias = 0;
    bool attached = false;  // the alias has a script attached (vm->attachedScripts has its handle)
};
struct AliasPick
{
    RE::FormID quest = 0, original = 0;  // the first candidate; ids only
    std::uint32_t alias = 0;
    bool attached = false;
    std::string note;
    std::vector<AliasCand> candidates;  // up to kMaxAliasCandidates, in data-handler order
};
constexpr std::size_t kMaxAliasCandidates = 50;

// Re-resolves quest -> alias by (quest FormID, alias ID) on the main thread, dispatches
// <ReferenceAlias|LocationAlias>.<fn>(arg) on the alias handle, and returns the result's form id as hex
// ("00000000" for None). argId 0 = no argument. Null-safe: a missing quest, alias or argument form
// returns an error string and dispatches nothing (in particular never ForceRefTo with no argument).
std::string AliasCall(RE::FormID questId, std::uint32_t aliasId, bool isLoc, std::string fn, RE::FormID argId, std::chrono::seconds wait = std::chrono::seconds(10))
{
    return OnMainThread([=](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto* quest = RE::TESForm::LookupByID<RE::TESQuest>(questId);
        if (!quest) return finish("QUEST_NOT_FOUND");

        RE::BGSBaseAlias* base = nullptr;
        {
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* a : quest->aliases)
                if (a && a->aliasID == aliasId) { base = a; break; }
        }
        if (!base) return finish("ALIAS_NOT_FOUND");

        // skyrim_cast + VMTYPEID, as Task 29 does (GetVMTypeID() exists per API-VERIFICATION.md; Task 9's S7f checks it agrees).
        void* obj = nullptr;
        RE::VMTypeID type = 0;
        if (isLoc) {
            obj = skyrim_cast<RE::BGSLocAlias*>(base);
            type = RE::BGSLocAlias::VMTYPEID;
        } else {
            obj = skyrim_cast<RE::BGSRefAlias*>(base);
            type = RE::BGSRefAlias::VMTYPEID;
        }
        if (!obj) return finish("WRONG_ALIAS_KIND");
        const auto handle = policy->GetHandleForObject(type, obj);
        if (handle == policy->EmptyHandle()) return finish("NO_HANDLE");

        RE::BSScript::IFunctionArguments* args = nullptr;
        if (argId == 0) {
            args = RE::MakeFunctionArguments();
        } else if (isLoc) {
            auto* loc = RE::TESForm::LookupByID<RE::BGSLocation>(argId);
            if (!loc) return finish("ARG_NOT_FOUND");
            args = RE::MakeFunctionArguments(std::move(loc));
        } else {
            auto* ref = RE::TESForm::LookupByID<RE::TESObjectREFR>(argId);  // 0x14 is the player, resolved here on the main thread
            if (!ref) return finish("ARG_NOT_FOUND");
            args = RE::MakeFunctionArguments(std::move(ref));
        }
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish](RE::BSScript::Variable v) {
            std::uint32_t id = 0;
            if (v.IsObject() && v.GetObject()) id = static_cast<std::uint32_t>(v.GetObject()->GetHandle() & 0xFFFFFFFF);
            finish(std::format("{:08X}", id));
        }) };
        if (!vm->DispatchMethodCall(handle, RE::BSFixedString(isLoc ? "LocationAlias" : "ReferenceAlias"),
                                    RE::BSFixedString(fn), args, cb))
            finish("DISPATCH_FAILED");
    }, wait);
}

// Collects up to kMaxAliasCandidates aliases of running quests: for references, filled and NOT the player;
// for locations, any location alias (filled or not: there is no C++ getter, S4Loc asks Papyrus). The first
// candidate is copied into quest/alias/original. Ids only leave the main thread.
std::shared_ptr<AliasPick> PickAlias(bool isLoc)
{
    auto pick = std::make_shared<AliasPick>();
    auto r = OnMainThread([pick, isLoc](std::function<void(std::string)> finish) {
        auto* dh = RE::TESDataHandler::GetSingleton();
        std::vector<std::pair<AliasCand, void*>> found;  // engine pointers stay inside this one main-thread task
        for (auto* quest : dh->GetFormArray<RE::TESQuest>()) {
            if (found.size() >= kMaxAliasCandidates) break;
            if (!quest || !quest->IsRunning()) continue;
            RE::BSReadLockGuard lock(quest->aliasAccessLock);
            for (auto* base : quest->aliases) {
                if (!base || found.size() >= kMaxAliasCandidates) continue;
                RE::FormID original = 0;
                void* obj = nullptr;
                if (isLoc) {
                    // BGSLocAlias has no C++ location getter (verified: its members are unnamed). Any location alias
                    // qualifies here; S4Loc reads the current location through Papyrus (LocationAlias.GetLocation).
                    obj = skyrim_cast<RE::BGSLocAlias*>(base);
                    if (obj) original = 1;  // sentinel for "found"; not a FormID
                } else {
                    auto* ra = skyrim_cast<RE::BGSRefAlias*>(base);
                    auto* target = ra ? ra->GetReference() : nullptr;
                    if (target) original = target->GetFormID();
                    obj = ra;
                }
                if (original == 0 || original == 0x14) continue;  // empty, or the player: proves nothing
                found.push_back({ AliasCand{ quest->GetFormID(), original, base->aliasID, false }, obj });
            }
        }
        // Scripted or not: an attached script gives the alias a bound VM object of its own (checked after the
        // alias locks are released, so no alias lock is held while the VM lock is taken).
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        for (auto& [cand, obj] : found) {
            const auto h = policy->GetHandleForObject(isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID, obj);
            RE::BSSpinLockGuard lock(vm->attachedScriptsLock);
            const auto it = vm->attachedScripts.find(h);
            cand.attached = it != vm->attachedScripts.end();
            pick->candidates.push_back(cand);
        }
        finish(found.empty() ? "none" : "found");
    }, std::chrono::seconds(10));
    if (r != "found") {
        pick->note = r;
        return pick;
    }
    pick->quest = pick->candidates[0].quest;
    pick->alias = pick->candidates[0].alias;
    pick->original = pick->candidates[0].original;
    pick->attached = pick->candidates[0].attached;
    return pick;
}

struct S4Run
{
    bool pass = false;
    std::string text;
};
// Force one reference alias to the player, read it back both ways, restore it.
S4Run S4On(const AliasCand& c)
{
    const auto q = c.quest;
    const auto a = c.alias;
    const auto orig = std::format("{:08X}", c.original);

    const auto before = AliasCall(q, a, false, "GetReference", 0);
    const auto beforeRef = AliasCall(q, a, false, "GetRef", 0);  // spec 4.4 names GetRef, the plan used GetReference: record which exist
    const auto forced = AliasCall(q, a, false, "ForceRefTo", 0x14);
    const auto after = AliasCall(q, a, false, "GetReference", 0);
    const auto afterRef = AliasCall(q, a, false, "GetRef", 0);
    const auto restored = AliasCall(q, a, false, "ForceRefTo", c.original);  // null-safe: ARG_NOT_FOUND dispatches nothing
    const auto last = AliasCall(q, a, false, "GetReference", 0);

    const bool getReferenceWorks = before == orig && after == "00000014";
    const bool getRefWorks = beforeRef == orig && afterRef == "00000014";
    const bool restoredOk = last == orig;
    const bool pass = (getReferenceWorks || getRefWorks) && restoredOk;
    return { pass, std::format("quest={:08X} alias={} original={} attached={} before={} beforeRef={} forced={} after={} afterRef={} restored={} last={} "
                               "getReference_works={} getRef_works={} restore_ok={}{}",
        q, a, orig, c.attached, before, beforeRef, forced, after, afterRef, restored, last,
        getReferenceWorks, getRefWorks, restoredOk,
        restoredOk ? "" : " RESTORE_FAILED: quit WITHOUT saving or reload s4test") };
}

std::string S4()
{
    auto pick = PickAlias(false);
    if (pick->original == 0)
        return "S4 FAIL no running quest with a filled reference alias whose target is not the player (" + pick->note + ")";
    const auto first = S4On(pick->candidates[0]);
    // A scripted alias has a bound VM object of its own, so it does not show that a plain (unscripted) alias is
    // dispatchable: when the first one is scripted, also run on the first unscripted candidate.
    std::string second = " unscripted=not_needed";
    bool secondOk = true;
    if (pick->attached) {
        const auto it = std::find_if(pick->candidates.begin(), pick->candidates.end(), [](const AliasCand& c) { return !c.attached; });
        if (it == pick->candidates.end()) {
            second = std::format(" unscripted=none_among_{}_candidates", pick->candidates.size());
        } else {
            const auto run = S4On(*it);
            secondOk = run.pass;
            second = std::format(" | unscripted: {} {}", run.pass ? "PASS" : "FAIL", run.text);
        }
    }
    return std::format("S4 {} {}{}", first.pass && secondOk ? "PASS" : "FAIL", first.text, second);
}

// LocationAlias case: find a FILLED location alias (up to kMaxAliasCandidates tried through Papyrus), force it
// to a different Location, read back, restore.
std::string S4Loc()
{
    auto pick = PickAlias(true);
    if (pick->original == 0) return "S4LOC NOT_RUN no running quest with a location alias (" + pick->note + ")";
    RE::FormID q = 0, origId = 0;
    std::uint32_t a = 0;
    std::string before;
    std::size_t tried = 0;
    for (const auto& c : pick->candidates) {
        ++tried;
        const auto got = AliasCall(c.quest, c.alias, true, "GetLocation", 0);
        const auto id = static_cast<RE::FormID>(std::strtoul(got.c_str(), nullptr, 16));
        if (id != 0) {
            q = c.quest;
            a = c.alias;
            origId = id;
            before = got;
            break;
        }
    }
    if (origId == 0) return std::format("S4LOC NOT_RUN all {} location aliases tried are empty", tried);
    // A different Location: the first BGSLocation in the data handler whose id differs from the original.
    auto other = std::make_shared<RE::FormID>(0);
    (void)OnMainThread([other, orig = origId](std::function<void(std::string)> finish) {
        for (auto* loc : RE::TESDataHandler::GetSingleton()->GetFormArray<RE::BGSLocation>())
            if (loc && loc->GetFormID() != orig) { *other = loc->GetFormID(); break; }
        finish("done");
    }, std::chrono::seconds(10));
    if (*other == 0) return "S4LOC NOT_RUN no second Location found";
    const auto orig = std::format("{:08X}", origId);
    const auto want = std::format("{:08X}", *other);

    const auto forced = AliasCall(q, a, true, "ForceLocationTo", *other);
    const auto after = AliasCall(q, a, true, "GetLocation", 0);
    const auto restored = AliasCall(q, a, true, "ForceLocationTo", origId);
    const auto last = AliasCall(q, a, true, "GetLocation", 0);
    const bool pass = before == orig && after == want && last == orig;
    return std::format("S4LOC {} quest={:08X} alias={} original={} other={} before={} forced={} after={} restored={} last={} tried={}",
        pass ? "PASS" : "FAIL", q, a, orig, want, before, forced, after, restored, last, tried);
}
}

namespace spike {
// Diagnostics (S4 deviation): list the candidates, run S4On on one by index.
std::string S4Cands()
{
    auto pick = PickAlias(false);
    std::string out = std::format("S4CANDS n={}", pick->candidates.size());
    std::size_t i = 0;
    for (const auto& c : pick->candidates)
        out += std::format(" [{}:{:08X}/{}/{:08X}/{}]", i++, c.quest, c.alias, c.original, c.attached ? "scripted" : "plain");
    return out;
}
std::string S4Bound(std::size_t idx)
{
    auto pick = PickAlias(false);
    if (idx >= pick->candidates.size()) return "S4BOUND out of range";
    const auto c = pick->candidates[idx];
    return OnMainThread([c](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto* quest = RE::TESForm::LookupByID<RE::TESQuest>(c.quest);
        RE::BGSBaseAlias* base = nullptr;
        if (quest) for (auto* a : quest->aliases) if (a && a->aliasID == c.alias) { base = a; break; }
        auto* ra = skyrim_cast<RE::BGSRefAlias*>(base);
        if (!ra) return finish("no alias");
        const auto h = policy->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ra);
        RE::BSTSmartPointer<RE::BSScript::Object> o1, o2;
        const bool f1 = vm->FindBoundObject(h, "ReferenceAlias", o1);
        const bool f2 = vm->FindBoundObject(h, "Alias", o2);
        finish(std::format("S4BOUND handle={:016X} type={} q={:08X} attached={} find_ReferenceAlias={} obj1={} find_Alias={} obj2={}", h,
            RE::BGSRefAlias::VMTYPEID, c.quest, c.attached, f1, o1 ? "yes" : "no", f2, o2 ? "yes" : "no"));
    }, std::chrono::seconds(10));
}
// Diagnostic probe for a plain (unscripted) reference alias: create a ReferenceAlias script object, bind it to the
// alias handle, dispatch GetReference on the object (read only: no ForceRefTo), report. Spike only.
std::string S4Plain(std::size_t idx)
{
    auto pick = PickAlias(false);
    if (idx >= pick->candidates.size()) return "S4PLAIN out of range";
    const auto c = pick->candidates[idx];
    auto res = std::make_shared<std::string>("none");
    const auto r = OnMainThread([c, res](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        auto* quest = RE::TESForm::LookupByID<RE::TESQuest>(c.quest);
        RE::BGSBaseAlias* base = nullptr;
        if (quest) for (auto* a : quest->aliases) if (a && a->aliasID == c.alias) { base = a; break; }
        auto* ra = skyrim_cast<RE::BGSRefAlias*>(base);
        if (!ra) return finish("no alias");
        const auto h = policy->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ra);
        RE::BSTSmartPointer<RE::BSScript::Object> obj;
        const bool created = vm->CreateObject(RE::BSFixedString("ReferenceAlias"), obj);
        if (!created || !obj) return finish(std::format("S4PLAIN create_failed created={}", created));
        vm->BindObject(obj, h, false);
        RE::BSTSmartPointer<RE::BSScript::Object> found;
        const bool f = vm->FindBoundObject(h, "ReferenceAlias", found);
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{ new Functor([finish, h, f](Var v) {
            std::uint32_t id = 0;
            if (v.IsObject() && v.GetObject()) id = static_cast<std::uint32_t>(v.GetObject()->GetHandle() & 0xFFFFFFFF);
            finish(std::format("S4PLAIN created=true bound_find={} handle={:016X} GetReference_via_object={:08X}", f, h, id));
        }) };
        if (!vm->DispatchMethodCall(obj, RE::BSFixedString("GetReference"), RE::MakeFunctionArguments(), cb))
            finish(std::format("S4PLAIN created=true bound_find={} dispatch_on_object_failed", f));
    }, std::chrono::seconds(10));
    return r;
}
std::string S4Idx(std::size_t idx)
{
    auto pick = PickAlias(false);
    if (idx >= pick->candidates.size()) return "S4IDX out of range";
    return "S4IDX " + std::to_string(idx) + " " + S4On(pick->candidates[idx]).text;
}
}

namespace spike {
std::atomic_int g_sends{0}, g_modEvents{0};
std::string g_lastSend, g_lastMod, g_sendTid, g_modTid;  // guarded by g_mx
std::mutex g_mx;

void NativeSend(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    std::lock_guard l(g_mx);
    g_lastSend = std::format("{}|{}", channel.c_str(), payload.c_str());
    g_sendTid = Tid();
    g_sends.fetch_add(1);
}

bool RegisterPapyrus(RE::BSScript::IVirtualMachine* vm)
{
    vm->RegisterFunction("Send"sv, "SkyrimPython"sv, NativeSend);  // verify the RegisterFunction overload by grepping the submodule
    return true;
}

struct ModSink : RE::BSTEventSink<SKSE::ModCallbackEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const SKSE::ModCallbackEvent* e, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        if (e && e->eventName == "spyTest") {
            std::lock_guard l(g_mx);
            // {:.6f}: std::format prints a float 42.0f as "42", not "42.000000"
            g_lastMod = std::format("{}|{}|{:.6f}", e->eventName.c_str(), e->strArg.c_str(), e->numArg);
            g_modTid = Tid();
            g_modEvents.fetch_add(1);
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

std::string S5()
{
    // Counters are never reset by earlier runs: compare against the values at the start of THIS run.
    const int ev0 = g_modEvents.load(), sends0 = g_sends.load();
    {
        std::lock_guard l(g_mx);
        g_lastMod.clear(); g_lastSend.clear(); g_modTid.clear(); g_sendTid.clear();
    }
    auto r = OnMainThread([](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto* player = RE::PlayerCharacter::GetSingleton();
        auto* policy = vm->GetObjectHandlePolicy();
        // Handle type for an Actor form: verify by grepping how CommonLibSSE-NG maps form types to VMTypeID.
        auto handle = policy->GetHandleForObject(static_cast<RE::VMTypeID>(RE::FormType::ActorCharacter), player);
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> none;
        const bool a = vm->DispatchMethodCall(handle, RE::BSFixedString("Form"), RE::BSFixedString("SendModEvent"),
            RE::MakeFunctionArguments(RE::BSFixedString("spyTest"), RE::BSFixedString("hello"), 42.0f), none);
        const bool b = vm->DispatchStaticCall(RE::BSFixedString("SkyrimPython"), RE::BSFixedString("Send"),
            RE::MakeFunctionArguments(RE::BSFixedString("chan"), RE::BSFixedString("{\"x\":1}")), none);
        finish(std::format("dispatched(modEvent={},send={})", a, b));
    }, std::chrono::seconds(10));
    // Bounded wait for both to land (was a fixed sleep).
    WaitUntil([&] { return g_modEvents.load() > ev0 && g_sends.load() > sends0; }, std::chrono::seconds(10));
    std::lock_guard l(g_mx);
    const int evD = g_modEvents.load() - ev0, sendD = g_sends.load() - sends0;
    const bool pass = evD == 1 && sendD == 1 && g_lastMod == "spyTest|hello|42.000000" && g_lastSend == "chan|{\"x\":1}";
    const auto mainTid = TidOf(g_mainTid.load());
    return std::format("S5 {} dispatch={} mod_events_delta={} last_mod={} mod_tid={} sends_delta={} last_send={} send_tid={} main_tid={}",
        pass ? "PASS" : "FAIL", r, evD, g_lastMod, g_modTid, sendD, g_lastSend, g_sendTid, mainTid);
}
}

namespace spike {
std::atomic_int g_postLoadOk{0}, g_postLoadFail{0}, g_loadingClosed{0};
std::atomic_int g_preLoad{0}, g_loadingOpened{0};  // a load STARTED (kPreLoadGame / Loading Menu opened)
std::atomic_bool g_loadingMenuOpen{false};
std::atomic_int g_seq{0}, g_postLoadSeq{0}, g_loadingClosedSeq{0};  // which event came first in the last load

struct MenuSink : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* e, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (e && e->menuName == RE::LoadingMenu::MENU_NAME) {
            Log(std::format("spike: Loading Menu {}", e->opening ? "opened" : "closed"));
            g_loadingMenuOpen = e->opening;
            if (e->opening) {
                g_loadingOpened.fetch_add(1);
            } else {
                g_loadingClosedSeq = ++g_seq;
                g_loadingClosed.fetch_add(1);
            }
        }
        return RE::BSEventNotifyControl::kContinue;
    }
};

// Empty string when no load is in progress: the Loading Menu is not open (sink state and UI::IsMenuOpen on the
// main thread) and the kPreLoadGame count stays unchanged for 10 s. Otherwise the reason.
std::string LoadInProgress()
{
    const int pre0 = g_preLoad;
    const auto end = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < end) {
        if (g_loadingMenuOpen) return "loading_menu_open";
        if (g_preLoad != pre0) return std::format("kPreLoadGame_fired_during_10s_check(delta={})", g_preLoad - pre0);
        std::this_thread::sleep_for(std::chrono::milliseconds(250));
    }
    auto menu = std::make_shared<std::atomic_bool>(false);
    const auto r = OnMainThread([menu](std::function<void(std::string)> finish) {
        auto* ui = RE::UI::GetSingleton();
        *menu = ui && ui->IsMenuOpen(RE::LoadingMenu::MENU_NAME);
        finish("ok");
    }, std::chrono::seconds(10));
    if (r != "ok") return "main_thread_check_" + r;
    return *menu ? "loading_menu_open(UI)" : "";
}

// viaUi = true: AddUITask (the design). viaUi = false: AddTask, the CONTROL run (DevBench's AddTask load
// silently does nothing under Proton; the control shows whether that is still true here). The control counts a
// load as STARTED when kPreLoadGame or Loading Menu opened fires, so a slow load still in flight after its 30 s
// is never mistaken for "did nothing"; the AddUITask run refuses to start while any load is in progress, so the
// control's late events cannot satisfy it.
std::string S6(const std::string& save, bool viaUi)
{
    if (viaUi) {
        if (const auto busy = LoadInProgress(); !busy.empty())
            return "S6 REFUSED load in progress (" + busy + "): wait for it to finish (the control may have started one), then retry";
    }
    const int ok0 = g_postLoadOk, fail0 = g_postLoadFail, lc0 = g_loadingClosed, pre0 = g_preLoad, lo0 = g_loadingOpened;
    g_seq = 0; g_postLoadSeq = 0; g_loadingClosedSeq = 0;
    auto body = [save, viaUi] {
        Log(std::format("spike: S6 calling BGSSaveLoadManager::Load({}) via {}", save, viaUi ? "AddUITask" : "AddTask"));
        // Verified in plugin/extern/CommonLibSSE-NG (39f9d07, v10.1.0) include/RE/B/BGSSaveLoadManager.h: both
        // Load(const char*) and Load(const char*, bool a_checkForMods) exist; the one-argument form calls
        // Load_Impl(a_fileName, -1, 0, true) (src/RE/B/BGSSaveLoadManager.cpp).
        RE::BGSSaveLoadManager::GetSingleton()->Load(save.c_str());
    };
    if (viaUi) SKSE::GetTaskInterface()->AddUITask(body); else SKSE::GetTaskInterface()->AddTask(body);
    const bool landed = WaitUntil([&] { return g_loadingClosed > lc0 && (g_postLoadOk + g_postLoadFail) > (ok0 + fail0); },
                                  std::chrono::seconds(viaUi ? 120 : 30));
    const int okD = g_postLoadOk - ok0, failD = g_postLoadFail - fail0, lcD = g_loadingClosed - lc0;
    const int preD = g_preLoad - pre0, loD = g_loadingOpened - lo0;
    const bool pass = landed && okD >= 1 && failD == 0 && lcD >= 1;
    const bool started = preD > 0 || loD > 0;
    const int pls = g_postLoadSeq, lcs = g_loadingClosedSeq;
    const char* first = (pls == 0 && lcs == 0) ? "none" : pls == 0 ? "LoadingMenuClosed" : lcs == 0 ? "kPostLoadGame"
                      : pls < lcs ? "kPostLoadGame" : "LoadingMenuClosed";
    const char* verdict = viaUi ? (pass ? "PASS" : "FAIL")
                        : pass ? "CONTROL_LOADED" : started ? "CONTROL_STARTED_LOAD" : "CONTROL_DID_NOTHING";
    return std::format("S6 {} via={} save={} preLoadGame_delta={} loadingOpened_delta={} postLoadGame_ok_delta={} postLoadGame_fail_delta={} loadingClosed_delta={} first={}",
        verdict, viaUi ? "AddUITask" : "AddTask", save, preD, loD, okD, failD, lcD, first);
}
}

namespace spike {
using VMT = RE::BSScript::Internal::VirtualMachine;
using Raw = RE::BSScript::TypeInfo::RawType;
using Cb = RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor>;
VMT* VM() { return VMT::GetSingleton(); }

std::string TI(const RE::BSScript::TypeInfo& t)
{
    std::string cls;
    // Rebuild 3 deviation: never TypeInfo::GetTypeInfo() on an OBJECT ARRAY. The pinned CommonLib masks the raw value
    // with ~kObjectArray (~0xB), which also clears bit 3 of the class pointer; ObjectTypeInfo pointers here are only
    // 8-byte aligned (e.g. Form[] raw 0x...ea9 -> class 0x...ea8, GetTypeInfo gives 0x...ea0), so the name read was
    // garbage in launch 1 and crashed the game in launch 2 (ucrtbase strlen, in Params(CreateFormArray) -> TI(ret)).
    // The class pointer of an object array is raw & ~1.
    if (t.IsObject()) {
        if (auto* ci = t.GetTypeInfo()) cls = ci->GetName();
    } else if (t.IsObjectArray()) {
        if (auto* ci = reinterpret_cast<RE::BSScript::ObjectTypeInfo*>(static_cast<std::uintptr_t>(t.GetRawType()) & ~std::uintptr_t{1})) cls = std::string(ci->GetName()) + "[]";
    }
    // raw is GetRawType(): for a class type that is the class POINTER (+1 for an object array), not kObject;
    // unmangled is GetUnmangledRawType(): kObject (1) / kObjectArray (11) for class types (API-VERIFICATION.md, TypeInfo).
    return std::format("raw={:#x} unmangled={:#x} obj={} arr={} objarr={} cls={}", static_cast<std::uintptr_t>(t.GetRawType()),
                       static_cast<std::uintptr_t>(t.GetUnmangledRawType()), t.IsObject(), t.IsArray(), t.IsObjectArray(), cls);
}

// The element-type formula the first dispatcher draft used (it is not in Tasks 27-30: they reject object-array
// arguments). It maps an ARRAY type to its element type. S7h compares its result with the real array's
// type_info() (the element type) by raw value only: a wrong result is not a valid TypeInfo, so it is never
// passed to TI(), GetTypeInfo() or CreateArray.
RE::BSScript::TypeInfo ElementTypeFormula(const RE::BSScript::TypeInfo& arrayType)
{
    const auto raw = static_cast<std::uintptr_t>(arrayType.GetRawType());
    if (arrayType.IsObjectArray()) return RE::BSScript::TypeInfo(static_cast<Raw>(raw & ~std::uintptr_t{1}));
    return RE::BSScript::TypeInfo(static_cast<Raw>(raw - (static_cast<std::uintptr_t>(Raw::kNoneArray) - static_cast<std::uintptr_t>(Raw::kNone))));
}

// A dispatch whose callback only copies the Variable; decode objects/arrays afterwards on the main thread.
struct Captured
{
    std::mutex mx;
    RE::BSScript::Variable v;
    bool have = false;
};
using Issue = std::function<bool(VMT*, Cb&)>;
std::shared_ptr<Captured> CaptureCall(Issue issue, std::string& status, std::chrono::seconds wait = std::chrono::seconds(10))
{
    auto cap = std::make_shared<Captured>();
    status = OnMainThread([cap, issue](std::function<void(std::string)> finish) {
        Cb cb{ new Functor([cap, finish](RE::BSScript::Variable v) {
            { std::lock_guard l(cap->mx); cap->v = v; cap->have = true; }
            finish("got");
        }) };
        if (!issue(VM(), cb)) finish("DISPATCH_FAILED");
    }, wait);
    return cap;
}

bool IEq(const char* a, const std::string& b) { return _stricmp(a, b.c_str()) == 0; }

RE::BGSBaseAlias* FindAliasBase(RE::TESQuest* quest, std::uint32_t aliasId)
{
    RE::BSReadLockGuard lock(quest->aliasAccessLock);
    for (auto* a : quest->aliases)
        if (a && a->aliasID == aliasId) return a;
    return nullptr;
}

// ---- S7a: FindFunction / GetParam over ObjectTypeInfo (Task 27: sig::FindFunction, BindArgs)
struct FnInfo { std::string member = "-", global = "-", text; };
std::string Params(const RE::BSScript::IFunction& f)
{
    std::string s = std::format("params={} [", f.GetParamCount());
    for (std::uint32_t i = 0; i < f.GetParamCount(); ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        f.GetParam(i, name, type);
        s += std::format("{}{}:{{{}}}", i ? ", " : "", name.c_str(), TI(type));
    }
    return s + "] ret={" + TI(f.GetReturnType()) + "}";  // GetReturnType: confirm in IFunction.h (Step 0)
}
FnInfo FindFn(const std::string& script, const std::string& fn)
{
    auto out = std::make_shared<FnInfo>();
    auto r = OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
        if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) { out->text = "NO_SCRIPT"; return finish("ok"); }
        for (auto* t = info.get(); t; t = t->GetParent()) {
            for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
                const auto& f = t->GetMemberFuncIter()[i].func;
                if (f && IEq(f->GetName().c_str(), fn) && out->member == "-") { out->member = t->GetName(); out->text += "member " + Params(*f) + "; "; }
            }
            for (std::uint32_t i = 0; i < t->GetNumGlobalFuncs(); ++i) {
                const auto& f = t->GetGlobalFuncIter()[i].func;
                if (f && IEq(f->GetName().c_str(), fn) && out->global == "-") { out->global = t->GetName(); out->text += "global " + Params(*f) + "; "; }
            }
        }
        finish("ok");
    }, std::chrono::seconds(10));
    if (r != "ok") out->text = r;
    return *out;
}
std::string S7a()
{
    const auto ri = FindFn("Utility", "RandomInt");             // global, 2 parameters
    const auto en = FindFn("Actor", "Enable");                  // member declared on a PARENT (ObjectReference)
    const auto wrong = FindFn("Actor", "RandomInt");            // a global asked for as a member: must not be a member
    const auto cf = FindFn("Utility", "CreateFormArray");       // used by S7h; its return type is an array
    const auto rf = FindFn("Utility", "ResizeFormArray");       // used by S7h
    const auto mv = FindFn("ObjectReference", "MoveTo");        // many object/float parameters
    // Rebuild 2 deviation: ObjectTypeInfo::GetName() returned "utility" (lowercase) on the box, so the task's exact
    // compare ri.global == "Utility" failed on case alone; the class name is compared case-insensitively.
    const bool pass = IEq(ri.global.c_str(), "Utility") && ri.text.find("params=2") != std::string::npos && en.member != "-" && wrong.member == "-";
    return std::format("S7a {} (case_exact_global_name={}) RandomInt[member={} global={} {}] Enable[member={} global={} {}] ActorRandomInt[member={} global={}] "
                       "CreateFormArray[global={} {}] ResizeFormArray[global={} {}] MoveTo[member={} {}]",
        pass ? "PASS" : "FAIL", ri.global == "Utility", ri.member, ri.global, ri.text, en.member, en.global, en.text, wrong.member, wrong.global,
        cf.global, cf.text, rf.global, rf.text, mv.member, mv.text);
}

// ---- S7b: ObjectTypeInfo enumeration and lookup case-sensitivity (sig::TypeOf, IsA)
std::string Chain(const std::string& script)
{
    auto out = std::make_shared<std::string>();
    OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
        if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) { *out = script + ": NOT_FOUND"; return finish("ok"); }
        for (auto* t = info.get(); t; t = t->GetParent())
            *out += std::format("{}(members={} globals={} props={})->", t->GetName(), t->GetNumMemberFuncs(), t->GetNumGlobalFuncs(), t->GetNumProperties());
        finish("ok");
    }, std::chrono::seconds(10));
    return *out;
}
std::string S7b()
{
    const auto exact = Chain("Actor"), lower = Chain("actor"), none = Chain("NoSuchScriptX");
    const bool pass = exact.find("Form") != std::string::npos && none.find("NOT_FOUND") != std::string::npos;
    return std::format("S7b {} Actor={} | actor(lowercase)={} | NoSuchScriptX={}  (lowercase NOT_FOUND means Task 27 must canonicalise the script name before TypeOf)",
        pass ? "PASS" : "FAIL", exact, lower, none);
}

// ---- S7c: PropertyIter on a script that really has properties, and Object::GetProperty (sig::FindProperty, get/set_property);
// plus a mod's OWN attached script as a call_method target (spec 4.3; Task 29 ResolveTarget with a non-native class):
// FindBoundObject(h, "<AttachedScript>") must return the same Object, and a parameterless member function must
// dispatch through both the Object& overload and the handle + "<AttachedScript>" overload.
struct S7cTarget
{
    RE::BSTSmartPointer<RE::BSScript::Object> obj;
    RE::VMHandle h = 0;
    std::string cls, fn = "-", fnOwner = "-";
    bool fnNative = false, boundByName = false;
};
std::string S7c(const std::string& scriptArg)
{
    auto pick = PickAlias(false);
    if (pick->quest == 0) return "S7c NOT_RUN no running quest (run Task 6's S4 first)";
    auto out = std::make_shared<std::string>();
    auto props = std::make_shared<int>(0);
    auto tgt = std::make_shared<S7cTarget>();
    // Rebuild 2 deviation: the first candidate's quest (0010FDD5 in s4test) has no attached script, so walk the
    // candidates' quests in order and use the first one that has attached scripts (the skipped ones are listed).
    std::vector<RE::FormID> quests;
    for (const auto& c : pick->candidates)
        if (std::find(quests.begin(), quests.end(), c.quest) == quests.end()) quests.push_back(c.quest);
    auto r = OnMainThread([=](std::function<void(std::string)> finish) {
        auto* vm = VM();
        auto* policy = vm->GetObjectHandlePolicy();
        RE::TESQuest* q = nullptr;
        RE::VMHandle h = 0;
        RE::FormID quest = 0;
        std::string skipped;
        for (auto id : quests) {
            auto* cq = RE::TESForm::LookupByID<RE::TESQuest>(id);
            if (!cq) continue;
            const auto ch = policy->GetHandleForObject(static_cast<RE::VMTypeID>(cq->GetFormType()), cq);
            bool has = false;
            {
                RE::BSSpinLockGuard lock(vm->attachedScriptsLock);
                auto it = vm->attachedScripts.find(ch);
                has = it != vm->attachedScripts.end() && !it->second.empty();
            }
            if (has) { q = cq; h = ch; quest = id; break; }
            skipped += std::format("{:08X} ", id);
        }
        *out = "quests_without_attached_scripts_skipped=[" + skipped + "] ";
        if (!q) return finish("NO_QUEST_WITH_ATTACHED_SCRIPTS");
        std::vector<RE::BSTSmartPointer<RE::BSScript::Object>> objs;
        {
            RE::BSSpinLockGuard lock(vm->attachedScriptsLock);  // name and element shape: confirm in VirtualMachine.h (Step 0)
            auto it = vm->attachedScripts.find(h);
            if (it != vm->attachedScripts.end())
                for (auto& s : it->second) objs.emplace_back(s.get());   // AttachedScript is BSTPointerAndFlags<BSTSmartPointer<Object>,1>: get() yields Object*, and BSTSmartPointer(Object*) is explicit
        }
        *out += std::format("quest={:08X} attached_scripts={}; ", quest, objs.size());
        for (auto& obj : objs) {
            if (!obj || !obj->GetTypeInfo()) continue;
            if (!scriptArg.empty() && !IEq(obj->GetTypeInfo()->GetName(), scriptArg)) continue;
            const auto* ti = obj->GetTypeInfo();
            if (!tgt->obj) {  // the first attached script object: resolve it by its OWN class name, pick a function
                tgt->obj = obj;
                tgt->h = h;
                tgt->cls = ti->GetName();
                RE::BSTSmartPointer<RE::BSScript::Object> byName;
                tgt->boundByName = vm->FindBoundObject(h, ti->GetName(), byName) && byName && byName.get() == obj.get();
                // A parameterless, non-static getter-looking function (Get*/Is*/Has*), never an event (On*): the
                // script's own functions first, then its parents' (Quest has IsRunning/GetStage). Reads only.
                for (const auto* t = ti; t && tgt->fn == "-"; t = t->GetParent()) {
                    for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
                        const auto& f = t->GetMemberFuncIter()[i].func;
                        if (!f || f->GetIsStatic() || f->GetParamCount() != 0) continue;
                        const std::string name = f->GetName().c_str();
                        auto starts = [&](const char* pre) { return _strnicmp(name.c_str(), pre, std::string_view(pre).size()) == 0; };
                        if (starts("On") || !(starts("Get") || starts("Is") || starts("Has"))) continue;
                        tgt->fn = name;
                        tgt->fnOwner = t->GetName();
                        tgt->fnNative = f->GetIsNative();
                        break;
                    }
                }
            }
            *out += std::format("script {} props={}: ", ti->GetName(), ti->GetNumProperties());
            for (std::uint32_t i = 0; i < ti->GetNumProperties() && i < 8; ++i) {
                const auto& p = ti->GetPropertyIter()[i];
                auto* val = obj->GetProperty(p.name);
                *out += std::format("{}:{{{}}}={} ", p.name.c_str(), TI(p.info.type), val ? TI(val->GetType()) : std::string("null"));
                ++*props;
            }
        }
        finish("ok");
    }, std::chrono::seconds(10));
    // Dispatch the picked function on the attached script both ways (each dispatch is its own main-thread task).
    std::string s1 = "NOT_RUN", s2 = "NOT_RUN";
    std::shared_ptr<Captured> viaObj, viaName;
    if (r == "ok" && tgt->obj && tgt->fn != "-") {
        viaObj = CaptureCall([tgt](VMT* vm, Cb& cb) {
            return vm->DispatchMethodCall(tgt->obj, RE::BSFixedString(tgt->fn), NoArgs(), cb);   // Object& overload
        }, s1);
        viaName = CaptureCall([tgt](VMT* vm, Cb& cb) {
            return vm->DispatchMethodCall(tgt->h, RE::BSFixedString(tgt->cls), RE::BSFixedString(tgt->fn), NoArgs(), cb);  // handle + script class
        }, s2);
    }
    auto shape = [](const std::shared_ptr<Captured>& c) {
        if (!c || !c->have) return std::string("-");
        std::lock_guard l(c->mx);
        return c->v.IsInt() ? std::format("int:{}", c->v.GetSInt()) : c->v.IsBool() ? std::format("bool:{}", c->v.GetBool())
             : c->v.IsFloat() ? std::format("float:{}", c->v.GetFloat()) : std::string(c->v.IsObject() ? "object" : "other");
    };
    const bool dispatchOk = s1 == "got" && s2 == "got";
    const bool pass = r == "ok" && *props > 0 && tgt->boundByName && dispatchOk;
    return std::format("S7c {} {} {}| attached_script={} bound_by_class_name={} fn={} declared_on={} native={} "
                       "object_overload={} result={} class_name_overload={} result={} dispatch_ok={}  "
                       "(pass an explicit script name as 's7c <Script>' if the quest has none with properties)",
        pass ? "PASS" : "FAIL", r, *out, tgt->cls.empty() ? "-" : tgt->cls, tgt->boundByName, tgt->fn, tgt->fnOwner,
        tgt->fnNative, s1, shape(viaObj), s2, shape(viaName), dispatchOk);
}

// ---- S7d: ResolveTarget: FindBoundObject / PackHandle on a form the VM has not bound yet
std::string S7d()
{
    auto out = std::make_shared<std::string>();
    auto r = OnMainThread([=](std::function<void(std::string)> finish) {
        auto* vm = VM();
        auto* policy = vm->GetObjectHandlePolicy();
        // Collect candidate ids under the forms lock, then release it BEFORE touching the VM (no lock held across PackHandle).
        std::vector<RE::FormID> ids;
        {
            const auto& [map, lock] = RE::TESForm::GetAllForms();
            RE::BSReadLockGuard g(lock);
            for (auto& [id, form] : *map) {
                if (form && form->Is(RE::FormType::Reference)) ids.push_back(id);
                if (ids.size() >= 400) break;
            }
        }
        // Warm baseline and the mismatch case on the player.
        if (auto* player = RE::PlayerCharacter::GetSingleton()) {
            const auto ph = policy->GetHandleForObject(static_cast<RE::VMTypeID>(player->GetFormType()), player);
            RE::BSTSmartPointer<RE::BSScript::Object> b1, b2;
            const bool asActor = vm->FindBoundObject(ph, "Actor", b1) && b1;
            const bool asQuest = vm->FindBoundObject(ph, "Quest", b2) && b2;
            // Deviation (advisor): does FindBoundObject accept a PARENT class of the bound object? The cold test below
            // asks for "ObjectReference"; if the player (bound as Actor) is not found that way, warm actors look cold.
            RE::BSTSmartPointer<RE::BSScript::Object> b3, b4;
            const bool asObjRef = vm->FindBoundObject(ph, "ObjectReference", b3) && b3;
            const bool asForm = vm->FindBoundObject(ph, "Form", b4) && b4;
            *out += std::format("player: FindBoundObject(Actor)={} FindBoundObject(ObjectReference)={} FindBoundObject(Form)={} FindBoundObject(Quest)={} (Quest must be false: that is the type_mismatch path); ", asActor, asObjRef, asForm, asQuest);
        }
        int cold = 0;
        for (auto id : ids) {
            auto* ref = RE::TESForm::LookupByID<RE::TESObjectREFR>(id);
            if (!ref) continue;
            const auto type = static_cast<RE::VMTypeID>(ref->GetFormType());
            const auto h = policy->GetHandleForObject(type, ref);
            if (h == policy->EmptyHandle()) continue;
            RE::BSTSmartPointer<RE::BSScript::Object> before;
            if (vm->FindBoundObject(h, "ObjectReference", before) && before) continue;  // already bound: not cold
            ++cold;
            RE::BSScript::Variable v;
            RE::BSScript::PackHandle(&v, ref, type);
            auto obj = v.GetObject();
            RE::BSTSmartPointer<RE::BSScript::Object> after;
            const bool found = vm->FindBoundObject(h, "ObjectReference", after) && after;
            *out += std::format("cold form {:08X}: FindBoundObject-before=false PackHandle->object={} class={} FindBoundObject-after={} same_object={}",
                id, obj ? 1 : 0, (obj && obj->GetTypeInfo()) ? obj->GetTypeInfo()->GetName() : "-", found, found && obj && after.get() == obj.get());
            return finish(found && obj ? "PASS" : "FAIL");
        }
        *out += std::format("no cold reference found among {} candidates", ids.size());
        finish("INCONCLUSIVE");
    }, std::chrono::seconds(20));
    return "S7d " + r + " " + *out;
}

// ---- S7e: DispatchMethodCall(BSTSmartPointer<Object>&, fn, args, cb) versus the handle+className overload (S4's)
std::string S7e()
{
    struct Holder { RE::BSTSmartPointer<RE::BSScript::Object> obj; RE::VMHandle h = 0; };
    auto held = std::make_shared<Holder>();
    auto r0 = OnMainThread([held](std::function<void(std::string)> finish) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        const auto type = static_cast<RE::VMTypeID>(player->GetFormType());
        held->h = VM()->GetObjectHandlePolicy()->GetHandleForObject(type, player);
        RE::BSScript::Variable v;
        RE::BSScript::PackHandle(&v, player, type);
        held->obj = v.GetObject();
        finish(held->obj ? "ok" : "NO_OBJECT");
    }, std::chrono::seconds(10));
    if (r0 != "ok") return "S7e FAIL could not get a bound Object for the player: " + r0;
    std::string s1, s2;
    auto viaObj = CaptureCall([held](VMT* vm, Cb& cb) {
        return vm->DispatchMethodCall(held->obj, RE::BSFixedString("GetPositionX"), NoArgs(), cb);   // Object& overload
    }, s1);
    auto viaHandle = CaptureCall([held](VMT* vm, Cb& cb) {
        return vm->DispatchMethodCall(held->h, RE::BSFixedString("ObjectReference"), RE::BSFixedString("GetPositionX"), NoArgs(), cb);
    }, s2);
    const bool ok = s1 == "got" && s2 == "got" && viaObj->v.IsFloat() && viaHandle->v.IsFloat() && viaObj->v.GetFloat() == viaHandle->v.GetFloat();
    return std::format("S7e {} object_overload={} x={} handle_overload={} x={}", ok ? "PASS" : "FAIL", s1,
        viaObj->have && viaObj->v.IsFloat() ? viaObj->v.GetFloat() : -1.0f, s2, viaHandle->have && viaHandle->v.IsFloat() ? viaHandle->v.GetFloat() : -1.0f);
}

// ---- S7f: alias kind detection: skyrim_cast<BGSRefAlias*> versus BGSBaseAlias::GetVMTypeID()
std::string S7f()
{
    auto pr = PickAlias(false), pl = PickAlias(true);
    auto out = std::make_shared<std::string>();
    auto pass = std::make_shared<bool>(true);
    OnMainThread([=](std::function<void(std::string)> finish) {
        for (const auto& [pick, isLoc] : { std::pair{ pr, false }, std::pair{ pl, true } }) {
            if (pick->quest == 0) { *out += isLoc ? "LocationAlias: none in this save; " : "ReferenceAlias: none in this save; "; continue; }
            auto* q = RE::TESForm::LookupByID<RE::TESQuest>(pick->quest);
            auto* base = q ? FindAliasBase(q, pick->alias) : nullptr;
            if (!base) { *out += "alias not found; "; *pass = false; continue; }
            const bool castRef = skyrim_cast<RE::BGSRefAlias*>(base) != nullptr, castLoc = skyrim_cast<RE::BGSLocAlias*>(base) != nullptr;
            const auto vmType = base->GetVMTypeID();  // delete this line and record "absent" if the pinned header has no GetVMTypeID
            const bool vmtypeRight = vmType == (isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID);
            *out += std::format("{}: cast_to_RefAlias={} cast_to_LocAlias={} GetVMTypeID={} expected_VMTYPEID={} GetVMTypeID_correct={}; ",
                isLoc ? "LocationAlias" : "ReferenceAlias", castRef, castLoc, static_cast<int>(vmType),
                static_cast<int>(isLoc ? RE::BGSLocAlias::VMTYPEID : RE::BGSRefAlias::VMTYPEID), vmtypeRight);
            if (castRef == isLoc || castLoc != isLoc) *pass = false;  // the skyrim_cast route must be right (GetVMTypeID is informational)
        }
        finish("ok");
    }, std::chrono::seconds(10));
    return std::format("S7f {} {}", *pass ? "PASS" : "FAIL", *out);
}

// ---- S7g: how an ALIAS returned by Papyrus is encoded (ObjectToJson). Quest.GetAlias(id) returns an Alias object.
std::string S7g(int idx = -1)
{
    auto pick = PickAlias(false);
    if (pick->quest == 0) return "S7g NOT_RUN no running quest with a filled reference alias (run Task 6's S4 first)";
    // Deviation: 's7g <N>' uses S4's candidate N (s4cands); the default is candidate 0 as the task wrote it.
    if (idx >= static_cast<int>(pick->candidates.size())) return "S7g NOT_RUN candidate index out of range";
    const auto& cand = idx < 0 ? pick->candidates[0] : pick->candidates[static_cast<std::size_t>(idx)];
    const auto questId = cand.quest, aliasId = cand.alias;
    const std::string candText = std::format("cand={} quest={:08X} alias={} {} ", idx < 0 ? 0 : idx, questId, aliasId, cand.attached ? "scripted" : "plain");
    struct Ctx { RE::VMHandle qh = 0, aliasHandle = 0; std::uintptr_t refPtr = 0; };
    auto ctx = std::make_shared<Ctx>();
    auto r0 = OnMainThread([=](std::function<void(std::string)> finish) {
        auto* q = RE::TESForm::LookupByID<RE::TESQuest>(questId);
        auto* base = q ? FindAliasBase(q, aliasId) : nullptr;
        auto* ref = base ? skyrim_cast<RE::BGSRefAlias*>(base) : nullptr;
        if (!ref) return finish("NO_ALIAS");
        auto* policy = VM()->GetObjectHandlePolicy();
        ctx->qh = policy->GetHandleForObject(static_cast<RE::VMTypeID>(q->GetFormType()), q);
        ctx->aliasHandle = policy->GetHandleForObject(RE::BGSRefAlias::VMTYPEID, ref);
        ctx->refPtr = reinterpret_cast<std::uintptr_t>(ref);
        finish("ok");
    }, std::chrono::seconds(10));
    if (r0 != "ok") return "S7g FAIL " + candText + r0;
    std::string st;
    auto cap = CaptureCall([ctx, aliasId](VMT* vm, Cb& cb) {
        std::vector<RE::BSScript::Variable> v(1);
        v[0].SetSInt(static_cast<std::int32_t>(aliasId));
        return vm->DispatchMethodCall(ctx->qh, RE::BSFixedString("Quest"), RE::BSFixedString("GetAlias"), new CountedArgs(std::move(v)), cb);
    }, st);
    // Rebuild 2 deviation: the handle + "Quest" overload returns false when the quest has no bound object (no attached
    // script; same mechanism as S4's plain aliases). Also dispatch through PackHandle(quest) + the Object& overload,
    // the route S7d showed binds a cold form, and use whichever returned an object.
    std::string st2;
    auto cap2 = CaptureCall([questId, aliasId](VMT* vm, Cb& cb) {
        auto* q = RE::TESForm::LookupByID<RE::TESQuest>(questId);
        if (!q) return false;
        RE::BSScript::Variable qv;
        RE::BSScript::PackHandle(&qv, q, static_cast<RE::VMTypeID>(q->GetFormType()));
        auto qobj = qv.GetObject();
        if (!qobj) return false;
        std::vector<RE::BSScript::Variable> v(1);
        v[0].SetSInt(static_cast<std::int32_t>(aliasId));
        return vm->DispatchMethodCall(qobj, RE::BSFixedString("GetAlias"), new CountedArgs(std::move(v)), cb);
    }, st2);
    const std::string routes = std::format("handle_overload={} packhandle_object_overload={}{} ", st, st2,
        (st2 == "got" && cap2->v.IsObject() && cap2->v.GetObject()) ? "(object)" : "");
    if (!(st == "got" && cap->v.IsObject() && cap->v.GetObject())) { cap = cap2; st = st2; }
    const std::string candText2 = candText + routes;
    if (st != "got" || !cap->v.IsObject() || !cap->v.GetObject())
        return "S7g FAIL " + candText2 + "Quest.GetAlias returned no object: " + st + (cap->have ? std::format(" (result IsNoneObject={} IsObject={})", cap->v.IsNoneObject(), cap->v.IsObject()) : std::string());
    auto out = std::make_shared<std::string>();
    auto pass = std::make_shared<bool>(false);
    auto obj = cap->v.GetObject();
    OnMainThread([=](std::function<void(std::string)> finish) {
        auto* policy = VM()->GetObjectHandlePolicy();
        const auto h2 = obj->GetHandle();
        const auto low = static_cast<RE::FormID>(h2 & 0xFFFFFFFF);
        auto* asForm = RE::TESForm::LookupByID(low);
        // Task 28's ObjectToJson checks this FIRST: if true for an alias handle, an alias result comes back as a Quest.
        const bool naiveSelectsForm = asForm && policy->HandleIsType(static_cast<RE::VMTypeID>(asForm->GetFormType()), h2);
        const bool isRefAlias = policy->HandleIsType(RE::BGSRefAlias::VMTYPEID, h2);
        auto* viaPolicy = isRefAlias ? policy->GetObjectForHandle(RE::BGSRefAlias::VMTYPEID, h2) : nullptr;
        auto* viaResolve = obj->Resolve(RE::BGSRefAlias::VMTYPEID);
        *out = std::format("alias_handle_from_policy={:016X} handle_from_result={:016X} equal={} low32={:08X} quest={:08X} low32_is_quest={} "
                           "naive_form_check_selects_a_form={} HandleIsType(RefAlias)={} GetObjectForHandle_matches={} Object::Resolve_matches={} script_class={}",
            ctx->aliasHandle, h2, ctx->aliasHandle == h2, low, questId, low == questId, naiveSelectsForm, isRefAlias,
            reinterpret_cast<std::uintptr_t>(viaPolicy) == ctx->refPtr, reinterpret_cast<std::uintptr_t>(viaResolve) == ctx->refPtr,
            obj->GetTypeInfo() ? obj->GetTypeInfo()->GetName() : "-");
        *pass = isRefAlias && (reinterpret_cast<std::uintptr_t>(viaPolicy) == ctx->refPtr || reinterpret_cast<std::uintptr_t>(viaResolve) == ctx->refPtr);
        finish("ok");
    }, std::chrono::seconds(10));
    return std::format("S7g {} {}{}  (PASS = the alias is recoverable; naive_form_check_selects_a_form=1 means Task 28 must test the alias handle types BEFORE the form lookup)",
        *pass ? "PASS" : "FAIL", candText2, *out);
}

// ---- S7h: object-array round trip. Utility.CreateFormArray(3, player) -> Form[3]; send a built Form[2] to ResizeFormArray.
std::string S7h()
{
    // Deviation (S2 rule): CreateFormArray / ResizeFormArray are SKSE additions to Utility; a Dispatch*Call to a
    // function that does not exist crashes the engine (SkyrimSE.exe+1447D3E), so look both up before dispatching.
    const auto cfa = FindFn("Utility", "CreateFormArray"), rfa = FindFn("Utility", "ResizeFormArray");
    if (!IEq(cfa.global.c_str(), "Utility") || !IEq(rfa.global.c_str(), "Utility"))  // GetName() gives "utility"
        return std::format("S7h NOT_RUN Utility.CreateFormArray global={} Utility.ResizeFormArray global={} (not found: nothing dispatched)", cfa.global, rfa.global);
    std::string st;
    auto cap = CaptureCall([](VMT* vm, Cb& cb) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("CreateFormArray"),
            RE::MakeFunctionArguments(static_cast<std::int32_t>(3), std::move(player)), cb);
    }, st);
    if (st != "got") return "S7h FAIL Utility.CreateFormArray: " + st + " (the function may be absent: see S7a)";
    // Rebuild 4 deviation: launch 3 got "did not return an array" for CreateFormArray(3, player) while DevBench's
    // CreateFormArray(3) returned [None, None, None]. Describe the result, and if it is not an array, retry with an
    // explicit None fill (CountedArgs: Int 3, None).
    auto describe = [](const std::shared_ptr<Captured>& c) {
        std::lock_guard l(c->mx);
        const bool arr = c->v.IsArray();
        return std::format("type{{{}}} IsArray={} IsNoneArray={} IsNoneObject={} IsObject={} array_ptr={}", TI(c->v.GetType()), arr,
            c->v.IsNoneArray(), c->v.IsNoneObject(), c->v.IsObject(), (arr || c->v.IsNoneArray()) && c->v.GetArray() ? "non-null" : "null");
    };
    std::string firstTry = "CreateFormArray(3, player): " + describe(cap);
    auto arrFromGame = (cap->v.IsArray() || cap->v.IsNoneArray()) ? cap->v.GetArray() : RE::BSTSmartPointer<RE::BSScript::Array>{};
    if (!arrFromGame) {
        std::string stN;
        auto capN = CaptureCall([](VMT* vm, Cb& cb) {
            std::vector<RE::BSScript::Variable> v(2);
            v[0].SetSInt(3);
            v[1].SetNone();
            return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("CreateFormArray"), new CountedArgs(std::move(v)), cb);
        }, stN);
        firstTry += " | CreateFormArray(3, None): " + stN + (stN == "got" ? " " + describe(capN) : "");
        if (stN == "got" && (capN->v.IsArray() || capN->v.IsNoneArray())) { arrFromGame = capN->v.GetArray(); cap = capN; }
    }
    if (!arrFromGame) return "S7h FAIL CreateFormArray did not return an array: " + firstTry;

    // What does the engine's own array say about its element type, and does the formula reproduce it?
    // Array::type_info() is the ELEMENT type (Form: a class pointer, bit 0 clear). The formula maps an ARRAY type
    // to its element type, so it is applied to the array value's own type (Form[]: class pointer | 1) and its
    // result is compared by raw value only (never passed to TI() or CreateArray).
    auto info = std::make_shared<std::string>();
    auto formulaWorks = std::make_shared<bool>(false);
    OnMainThread([=](std::function<void(std::string)> finish) {
        RE::BSScript::TypeInfo arrType;
        {
            std::lock_guard l(cap->mx);
            arrType = cap->v.GetType();
        }
        const auto& real = arrFromGame->type_info();
        const auto formulaRaw = static_cast<std::uintptr_t>(ElementTypeFormula(arrType).GetRawType());
        const auto realRaw = static_cast<std::uintptr_t>(real.GetRawType());
        *formulaWorks = arrType.IsObjectArray() && formulaRaw == realRaw;
        *info = std::format("real array: size={} array_type{{{}}} elem_type{{{}}}; ElementTypeFormula(array_type) raw={:#x} elem_type raw={:#x} formula_matches_real={}",
            arrFromGame->size(), TI(arrType), TI(real), formulaRaw, realRaw, *formulaWorks);
        finish("ok");
    }, std::chrono::seconds(10));

    // Build a Form[2] from a copy of the real array's element TypeInfo and send it to ResizeFormArray. The formula
    // gets no engine call: "formula_works" is only whether its result equals the real element type (above).
    std::string st2;
    auto cap2 = CaptureCall([arrFromGame](VMT* vm, Cb& cb) {
        auto* player = RE::PlayerCharacter::GetSingleton();
        const auto ptype = static_cast<RE::VMTypeID>(player->GetFormType());
        const RE::BSScript::TypeInfo et = arrFromGame->type_info();
        RE::BSTSmartPointer<RE::BSScript::Array> mine;
        if (!vm->CreateArray(et, 2, mine) || !mine) return false;
        for (std::uint32_t i = 0; i < 2; ++i) RE::BSScript::PackHandle(&(*mine)[i], player, ptype);
        std::vector<RE::BSScript::Variable> v(3);
        v[0].SetArray(mine);
        v[1].SetSInt(4);
        RE::BSScript::PackHandle(&v[2], player, ptype);
        return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("ResizeFormArray"), new CountedArgs(std::move(v)), cb);
    }, st2);
    bool copiedWorks = false;
    std::string detail = st2 + (st2 == "got" ? " " + describe(cap2) : std::string());
    if (st2 == "got" && cap2->v.IsArray() && cap2->v.GetArray()) {
        auto a = cap2->v.GetArray();
        copiedWorks = a->size() == 4;
        detail = std::format("size={} (expected 4)", a->size());
    }
    return std::format("S7h {} [{}] {} | copied TypeInfo round trip: {}  copied_TypeInfo_works={} formula_works={}  (PASS = the copied-TypeInfo route works; M2 may then accept object-array arguments by copying a real array's type_info(); formula_works=false means the formula must not be used)",
        copiedWorks ? "PASS" : "FAIL", firstTry, *info, detail, copiedWorks, *formulaWorks);
}
}

namespace spike {
// ---- S7 tid probe (controller ruling R49): does SKSE AddTask (and AddUITask) run on one stable thread, and can two
// tasks ever run at the same time? Thread ids here are Win32 ids (GetCurrentThreadId); each task also checks that
// TidOf(std::this_thread::get_id()) prints the same number (MSVC's std::thread::id wraps _Thrd_id() ==
// GetCurrentThreadId; checked, not assumed: domain_mismatch counts the tasks where they differ).
std::atomic<std::uint32_t> g_loadTid{ 0 }, g_dlTid{ 0 }, g_wndTid{ 0 }, g_mainObjTid{ 0 };

struct EnumCtx { DWORD pid; HWND found; };
BOOL CALLBACK EnumOwnWindow(HWND w, LPARAM lp)
{
    auto* c = reinterpret_cast<EnumCtx*>(lp);
    DWORD pid = 0;
    GetWindowThreadProcessId(w, &pid);
    if (pid == c->pid && IsWindowVisible(w) && GetWindow(w, GW_OWNER) == nullptr) { c->found = w; return FALSE; }
    return TRUE;
}

// Called in OnMessage at kDataLoaded (on whatever thread SKSE delivers that message).
void RecordDataLoadedThreads()
{
    g_dlTid = ::GetCurrentThreadId();
    HWND wnd = nullptr;
    std::string via = "RE::Main::wnd";
    std::uint32_t mainObjTid = 0;
    if (auto* m = RE::Main::GetSingleton()) {
        wnd = reinterpret_cast<HWND>(m->wnd);
        mainObjTid = m->threadID;
    }
    if (!wnd) {
        EnumCtx c{ ::GetCurrentProcessId(), nullptr };
        EnumWindows(EnumOwnWindow, reinterpret_cast<LPARAM>(&c));
        wnd = c.found;
        via = "EnumWindows";
    }
    DWORD wpid = 0;
    const DWORD wtid = wnd ? GetWindowThreadProcessId(wnd, &wpid) : 0;
    g_wndTid = wtid;
    g_mainObjTid = mainObjTid;
    Log(std::format("tidprobe: kDataLoaded tid={} std_tid={} window_tid={} (hwnd={:#x} via {} pid={}) RE::Main::threadID={} plugin_load_tid={}",
        g_dlTid.load(), Tid(), wtid, reinterpret_cast<std::uintptr_t>(wnd), via, wpid, mainObjTid, g_loadTid.load()));
}

struct TidProbeState
{
    std::mutex mx;
    std::map<std::uint32_t, int> counts;
    std::vector<std::uint32_t> order;  // tid of each task in run order
    std::atomic_int inside{ 0 }, maxc{ 0 }, done{ 0 }, domainMismatch{ 0 }, onMainObj{ 0 }, onWnd{ 0 };
    std::chrono::steady_clock::time_point first{}, last{};
};

std::string TidProbe(int n, bool ui)
{
    if (n <= 0 || n > 5000) n = 200;
    auto st = std::make_shared<TidProbeState>();
    const auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < n; ++i) {
        auto body = [st] {
            const auto now = std::chrono::steady_clock::now();
            const int in = st->inside.fetch_add(1) + 1;
            int m = st->maxc.load();
            while (in > m && !st->maxc.compare_exchange_weak(m, in)) {}
            const std::uint32_t tid = ::GetCurrentThreadId();
            if (Tid() != std::to_string(tid)) st->domainMismatch.fetch_add(1);
            if (tid == g_mainObjTid.load()) st->onMainObj.fetch_add(1);
            if (tid == g_wndTid.load()) st->onWnd.fetch_add(1);
            {
                std::lock_guard l(st->mx);
                ++st->counts[tid];
                st->order.push_back(tid);
                if (st->order.size() == 1) st->first = now;
                st->last = now;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
            st->inside.fetch_sub(1);
            st->done.fetch_add(1);
        };
        if (ui) SKSE::GetTaskInterface()->AddUITask(body); else SKSE::GetTaskInterface()->AddTask(body);
    }
    const auto submitMs = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    WaitUntil([st, n] { return st->done.load() >= n; }, std::chrono::seconds(60));
    std::this_thread::sleep_for(std::chrono::milliseconds(20));  // let a straggler's decrement land before reading maxc
    std::string tids;
    int switches = 0;
    double spanMs = 0, firstMs = -1;
    std::size_t distinct = 0;
    {
        std::lock_guard l(st->mx);
        for (const auto& [t, c] : st->counts) tids += std::format("{}{}:{}", tids.empty() ? "" : ",", t, c);
        for (std::size_t i = 1; i < st->order.size(); ++i) switches += st->order[i] != st->order[i - 1];
        distinct = st->counts.size();
        if (!st->order.empty()) {
            spanMs = std::chrono::duration<double, std::milli>(st->last - st->first).count();
            firstMs = std::chrono::duration<double, std::milli>(st->first - t0).count();
        }
    }
    const auto line = std::format("{} n={} done={} distinct={} tids={} max_concurrent={} dataloaded_tid={} window_tid={} "
                                  "| main_threadID={} on_main_threadID={} on_window_tid={} domain_mismatch={} switches={} "
                                  "submit_ms={:.1f} first_task_after_ms={:.1f} span_ms={:.1f} caller_tid={}",
        ui ? "TIDPROBE_UI" : "TIDPROBE", n, st->done.load(), distinct, tids, st->maxc.load(), g_dlTid.load(), g_wndTid.load(),
        g_mainObjTid.load(), st->onMainObj.load(), st->onWnd.load(), st->domainMismatch.load(), switches, submitMs, firstMs, spanMs,
        ::GetCurrentThreadId());
    Log(line);
    return line;
}
}


namespace spike {
// ---- Rebuild 2 diagnostics.
// s7b2: pass 2 of S7b found "NoSuchScriptX" as an EMPTY type (members=0 globals=0 props=0, no parent) after pass 1
// had reported NOT_FOUND. Probe it with a fresh name each call: NoLoad before, GetScriptObjectType twice, NoLoad after.
std::atomic_int g_s7b2{ 0 };
std::string S7b2()
{
    const auto name = std::format("SpyNoSuchScript{}", g_s7b2.fetch_add(1));
    auto out = std::make_shared<std::string>();
    OnMainThread([=](std::function<void(std::string)> finish) {
        auto desc = [](bool ok, const RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo>& t) {
            if (!ok || !t) return std::format("found={} null={}", ok, !t);
            return std::format("found=true name={} members={} globals={} props={} parent={} state={}", t->GetName(), t->GetNumMemberFuncs(),
                t->GetNumGlobalFuncs(), t->GetNumProperties(), t->GetParent() ? t->GetParent()->GetName() : "-", static_cast<int>(t->linkedValid));
        };
        RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> a, b, c, d;
        const bool na = VM()->GetScriptObjectTypeNoLoad(RE::BSFixedString(name), a);
        const bool lb = VM()->GetScriptObjectType(RE::BSFixedString(name), b);
        const bool lc = VM()->GetScriptObjectType(RE::BSFixedString(name), c);
        const bool nd = VM()->GetScriptObjectTypeNoLoad(RE::BSFixedString(name), d);
        *out = std::format("S7b2 name={} | NoLoad-before: {} | Load#1: {} | Load#2: {} | NoLoad-after: {}", name, desc(na, a), desc(lb, b), desc(lc, c), desc(nd, d));
        finish("ok");
    }, std::chrono::seconds(10));
    return *out;
}

// tidspread N intervalMs [mix]: a submitter thread posts one AddTask every intervalMs (and with "mix" also one
// AddUITask), so the tasks land in many drains (frames). Each task records its tid and kind, counts how many tasks
// of either kind are inside at once (max_concurrent) and whether a task of the OTHER kind was inside when it started
// (cross_overlap), sleeps 1 ms. Answers: does the drain thread change between frames, and can drains overlap?
struct SpreadState
{
    std::mutex mx;
    std::map<std::uint32_t, int> taskTids, uiTids;
    std::vector<std::uint32_t> order;
    std::atomic_int inside{ 0 }, insideTask{ 0 }, insideUi{ 0 }, maxc{ 0 }, cross{ 0 }, done{ 0 }, onWnd{ 0 };
};
std::string TidSpread(const std::string& args)
{
    std::istringstream in(args);
    int n = 200, interval = 5;
    std::string mixs;
    in >> n >> interval >> mixs;
    if (n <= 0 || n > 5000) n = 200;
    if (interval < 0 || interval > 1000) interval = 5;
    const bool mix = mixs == "mix";
    auto st = std::make_shared<SpreadState>();
    auto mk = [st](bool ui) {
        return [st, ui] {
            const int in2 = st->inside.fetch_add(1) + 1;
            int m = st->maxc.load();
            while (in2 > m && !st->maxc.compare_exchange_weak(m, in2)) {}
            if ((ui ? st->insideTask : st->insideUi).load() > 0) st->cross.fetch_add(1);
            (ui ? st->insideUi : st->insideTask).fetch_add(1);
            const std::uint32_t tid = ::GetCurrentThreadId();
            if (tid == g_wndTid.load()) st->onWnd.fetch_add(1);
            {
                std::lock_guard l(st->mx);
                ++(ui ? st->uiTids : st->taskTids)[tid];
                st->order.push_back(tid);
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            (ui ? st->insideUi : st->insideTask).fetch_sub(1);
            st->inside.fetch_sub(1);
            st->done.fetch_add(1);
        };
    };
    const auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < n; ++i) {
        SKSE::GetTaskInterface()->AddTask(mk(false));
        if (mix) SKSE::GetTaskInterface()->AddUITask(mk(true));
        if (interval) std::this_thread::sleep_for(std::chrono::milliseconds(interval));
    }
    const int total = mix ? 2 * n : n;
    WaitUntil([st, total] { return st->done.load() >= total; }, std::chrono::seconds(60));
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const auto ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    std::string tt, ut;
    int switches = 0;
    std::size_t dt = 0, du = 0, dall = 0;
    {
        std::lock_guard l(st->mx);
        std::map<std::uint32_t, int> all;
        for (const auto& [t, c] : st->taskTids) { tt += std::format("{}{}:{}", tt.empty() ? "" : ",", t, c); all[t] += c; }
        for (const auto& [t, c] : st->uiTids) { ut += std::format("{}{}:{}", ut.empty() ? "" : ",", t, c); all[t] += c; }
        for (std::size_t i = 1; i < st->order.size(); ++i) switches += st->order[i] != st->order[i - 1];
        dt = st->taskTids.size(); du = st->uiTids.size(); dall = all.size();
    }
    const auto line = std::format("TIDSPREAD n={} interval_ms={} mix={} done={}/{} distinct_task={} distinct_ui={} distinct_all={} max_concurrent={} cross_overlap={} "
                                  "on_window_tid={} switches={} elapsed_ms={:.0f} window_tid={} task_tids={} ui_tids={}",
        n, interval, mix, st->done.load(), total, dt, du, dall, st->maxc.load(), st->cross.load(), st->onWnd.load(), switches, ms, g_wndTid.load(), tt, ut.empty() ? "-" : ut);
    Log(line);
    return line;
}
}


namespace spike {
// ---- Rebuild 5 diagnostic s7h2: why did CreateFormArray(3, player) return None while (3, None) worked, and is
// Array::type_info() (0 on the SKSE-made Form[]) the problem in the ResizeFormArray round trip?
// Part 1: CreateFormArray(1, X) with X packed several ways; reports the result shape and element 0's form id.
// Part 2: ResizeFormArray(arr, 4, None) with arr = (a) the array CreateFormArray(3, None) returned, (b) a new array
// made with CreateArray(TypeInfo(array_raw & ~1)) (the formula's element type), (c) a new array made with a copy of
// the returned array's type_info(). Every target was found by S7a (Utility, global); nothing else is dispatched.
std::string ArrShape(const std::shared_ptr<Captured>& c, const std::string& st)
{
    if (st != "got") return st;
    std::lock_guard l(c->mx);
    if (c->v.IsArray() && c->v.GetArray()) {
        auto a = c->v.GetArray();
        std::uint32_t id0 = 0;
        if (a->size() > 0 && (*a)[0].IsObject() && (*a)[0].GetObject()) id0 = static_cast<std::uint32_t>((*a)[0].GetObject()->GetHandle() & 0xFFFFFFFF);
        return std::format("array size={} elem_type_raw={:#x} elem0={:08X}", a->size(), static_cast<std::uintptr_t>(a->type_info().GetRawType()), id0);
    }
    return std::format("not_array IsNoneObject={} raw={:#x}", c->v.IsNoneObject(), static_cast<std::uintptr_t>(c->v.GetType().GetRawType()));
}
std::string S7h2()
{
    const auto cfa = FindFn("Utility", "CreateFormArray"), rfa = FindFn("Utility", "ResizeFormArray");
    if (!IEq(cfa.global.c_str(), "Utility") || !IEq(rfa.global.c_str(), "Utility")) return "S7h2 NOT_RUN functions not found";
    std::string out = "S7h2 part1:";
    struct Way { const char* name; RE::FormID id; int vmtype; };  // vmtype -1: the form's own GetFormType()
    const Way ways[] = { { "player_as_62", 0x14, 62 }, { "player_as_61", 0x14, 61 }, { "player_own_type", 0x14, -1 },
                         { "ref_1A677_own_type", 0x1A677, -1 }, { "gold_F_own_type", 0xF, -1 }, { "none", 0, 0 } };
    for (const auto& w : ways) {
        std::string st;
        auto cap = CaptureCall([w](VMT* vm, Cb& cb) {
            std::vector<RE::BSScript::Variable> v(2);
            v[0].SetSInt(1);
            if (w.id == 0) v[1].SetNone();
            else {
                auto* f = RE::TESForm::LookupByID(w.id);
                if (!f) return false;
                RE::BSScript::PackHandle(&v[1], f, w.vmtype < 0 ? static_cast<RE::VMTypeID>(f->GetFormType()) : static_cast<RE::VMTypeID>(w.vmtype));
            }
            return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("CreateFormArray"), new CountedArgs(std::move(v)), cb);
        }, st);
        std::string argType;
        if (w.id) {
            auto t = std::make_shared<std::string>();
            OnMainThread([w, t](std::function<void(std::string)> finish) {
                auto* f = RE::TESForm::LookupByID(w.id);
                *t = f ? std::format("formtype={}", static_cast<int>(f->GetFormType())) : "missing";
                finish("ok");
            }, std::chrono::seconds(5));
            argType = *t;
        }
        out += std::format(" [{} {}: {}]", w.name, argType, ArrShape(cap, st));
    }
    // Part 2
    std::string st0;
    auto base = CaptureCall([](VMT* vm, Cb& cb) {
        std::vector<RE::BSScript::Variable> v(2);
        v[0].SetSInt(3);
        v[1].SetNone();
        return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("CreateFormArray"), new CountedArgs(std::move(v)), cb);
    }, st0);
    if (st0 != "got" || !base->v.IsArray() || !base->v.GetArray()) return out + " part2: no base array " + ArrShape(base, st0);
    auto baseArr = base->v.GetArray();
    const auto arrRaw = static_cast<std::uintptr_t>(base->v.GetType().GetRawType());
    out += std::format(" part2: base {} array_raw={:#x};", ArrShape(base, st0), arrRaw);
    for (int mode = 0; mode < 3; ++mode) {
        std::string st;
        auto cap = CaptureCall([mode, baseArr, arrRaw](VMT* vm, Cb& cb) {
            RE::BSTSmartPointer<RE::BSScript::Array> arr = baseArr;
            if (mode > 0) {
                const RE::BSScript::TypeInfo et = mode == 1 ? RE::BSScript::TypeInfo(static_cast<Raw>(arrRaw & ~std::uintptr_t{1})) : baseArr->type_info();
                RE::BSTSmartPointer<RE::BSScript::Array> mine;
                if (!vm->CreateArray(et, 2, mine) || !mine) return false;
                arr = mine;
            }
            std::vector<RE::BSScript::Variable> v(3);
            v[0].SetArray(arr);
            v[1].SetSInt(4);
            v[2].SetNone();
            return vm->DispatchStaticCall(RE::BSFixedString("Utility"), RE::BSFixedString("ResizeFormArray"), new CountedArgs(std::move(v)), cb);
        }, st);
        static const char* names[] = { "returned_array", "CreateArray(formula_elem_type)", "CreateArray(copied_type_info)" };
        out += std::format(" [{} -> {}]", names[mode], ArrShape(cap, st));
    }
    return out;
}
}

// Handle has a namespace block of its own: Tasks 3-9 add their code as new `namespace spike { ... }` blocks
// directly above this one, after Log and ParseInt, which they use.
namespace spike {
std::string Handle(const std::string& line)
{
    if (line.rfind("echo ", 0) == 0) return line.substr(5);
    // Tasks 3-9 add their commands here, before the fallback.
    if (line == "py_load") return py::Load();
    if (line == "py_init") return py::Init(g_dataLoaded.load());
    if (line == "py_status") return py::Status();
    if (line.rfind("py_exec ", 0) == 0) return py::Exec(line.substr(8));
    if (line == "py_arm_rt") {
        py::SetCaller(&CallRealTime);
        return "PY_ARM_RT OK";
    }
    if (line == "s7a") return S7a();
    if (line == "s7b") return S7b();
    if (line == "s7c" || line.rfind("s7c ", 0) == 0) return S7c(line.size() > 4 ? line.substr(4) : "");
    if (line == "s7d") return S7d();
    if (line == "s7e") return S7e();
    if (line == "s7f") return S7f();
    if (line == "s7g") return S7g();
    if (line.rfind("s7g ", 0) == 0) return S7g(ParseInt(line.substr(4), -1));
    if (line == "s7h") return S7h();
    if (line == "s7b2") return S7b2();
    if (line == "s7h2") return S7h2();
    if (line.rfind("tidspread", 0) == 0) return TidSpread(line.size() > 10 ? line.substr(10) : "");
    if (line == "tidprobe" || line.rfind("tidprobe ", 0) == 0) return TidProbe(ParseInt(line.size() > 9 ? line.substr(9) : "", 200), false);
    if (line == "tidprobe_ui" || line.rfind("tidprobe_ui ", 0) == 0) return TidProbe(ParseInt(line.size() > 12 ? line.substr(12) : "", 200), true);
    if (line == "s5") return S5();
    if (line.rfind("s6 ", 0) == 0) return S6(line.substr(3), true);
    if (line.rfind("s6task ", 0) == 0) return S6(line.substr(7), false);
    if (line == "s4") return S4();
    if (line == "s4loc") return S4Loc();
    if (line.rfind("s4bound ", 0) == 0) return S4Bound(static_cast<std::size_t>(ParseInt(line.substr(8), 0)));
    if (line.rfind("s4plain ", 0) == 0) return S4Plain(static_cast<std::size_t>(ParseInt(line.substr(8), 0)));
    if (line == "s4cands") return S4Cands();
    if (line.rfind("s4idx ", 0) == 0) return S4Idx(static_cast<std::size_t>(ParseInt(line.substr(6), 0)));
    if (line.rfind("s9_sample ", 0) == 0) return S9Sample(line.substr(10));
    if (line == "s2mode0" || line == "s2mode1") { g_s2Mode = line.back() - '0'; return "OK s2 phase C mode " + line.substr(6); }
    if (line.rfind("s2", 0) == 0) return S2(ParseInt(line.size() > 3 ? line.substr(3) : "", 1000));
    return "ERR unknown command";
}
}

namespace {
void SetupLog()
{
    try {
        auto dir = SKSE::log::log_directory();
        if (!dir) return;  // Documents not resolvable: keep spdlog's default logger
        auto path = *dir;
        path /= "SkyrimPythonSpike.log"sv;
        auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path.string(), true);
        auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
        logger->set_level(spdlog::level::info);
        logger->flush_on(spdlog::level::info);
        spdlog::set_default_logger(std::move(logger));
    } catch (...) {
        // no log file; the spike still runs
    }
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    switch (m->type) {
    case SKSE::MessagingInterface::kPostLoad:
        spike::StartListener(45978, spike::Handle);
        break;
    case SKSE::MessagingInterface::kDataLoaded:
        spike::Log("kDataLoaded");
        spike::g_dataLoaded.store(true);
        spike::RecordDataLoadedThreads();  // S7 tid probe (R49)
        { static spike::ModSink sink; SKSE::GetModCallbackEventSource()->AddEventSink(&sink); }
        { static spike::MenuSink menuSink; RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(&menuSink); }
        if (spike::py::AutostartRequested())  // run B (Step 7): the real plugin's timing; py::Load ran in SKSEPluginLoad
            spike::Log("spike: autostart at kDataLoaded " + spike::py::Init(true));
        break;
    case SKSE::MessagingInterface::kPostLoadGame: {
        // data is a packed bool, not a pointer (SKSE64 PluginAPI.h: "data: bool, true if game successfully loaded");
        // the raw value is logged either way.
        const auto raw = reinterpret_cast<std::uintptr_t>(m->data);
        spike::Log(std::format("kPostLoadGame data={:#x} ({})", raw, raw ? "success" : "FAILED load"));
        spike::g_postLoadSeq = ++spike::g_seq;
        (raw ? spike::g_postLoadOk : spike::g_postLoadFail).fetch_add(1);
        break;
    }
    case SKSE::MessagingInterface::kPreLoadGame:
        spike::Log("kPreLoadGame");
        spike::g_preLoad.fetch_add(1);
        break;
    case SKSE::MessagingInterface::kNewGame: spike::Log("kNewGame"); break;
    default:
        break;
    }
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse, { .log = false });  // InitInfo aggregate (SKSE/API.h); the old Init(skse, bool) is [[deprecated]]
    SetupLog();
    spike::g_loadTid = ::GetCurrentThreadId();  // S7 tid probe (R49)
    SKSE::GetPapyrusInterface()->Register(spike::RegisterPapyrus);
    spike::Log(std::format("SkyrimPythonSpike loading on Skyrim {}", REL::Module::get().version().string(".")));  // string() alone joins with '-' (1-6-1170-0)
    if (spike::py::AutostartRequested())  // run B (Step 7): python314.dll + SpikePyCore.dll at plugin load
        spike::Log("spike: autostart at plugin load " + spike::py::Load());
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
