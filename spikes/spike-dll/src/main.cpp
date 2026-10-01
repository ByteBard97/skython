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
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

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
        if (spike::py::AutostartRequested())  // run B (Step 7): the real plugin's timing; py::Load ran in SKSEPluginLoad
            spike::Log("spike: autostart at kDataLoaded " + spike::py::Init(true));
        break;
    default:
        break;
    }
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse, { .log = false });  // InitInfo aggregate (SKSE/API.h); the old Init(skse, bool) is [[deprecated]]
    SetupLog();
    spike::Log(std::format("SkyrimPythonSpike loading on Skyrim {}", REL::Module::get().version().string(".")));  // string() alone joins with '-' (1-6-1170-0)
    if (spike::py::AutostartRequested())  // run B (Step 7): python314.dll + SpikePyCore.dll at plugin load
        spike::Log("spike: autostart at plugin load " + spike::py::Load());
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
