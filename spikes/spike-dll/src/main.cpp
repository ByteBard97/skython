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
    std::mutex mx;
    std::string firstCbTid, firstNestedTid, firstLatentTid;
};

void NoteThread(S2State& st, std::atomic_int& on, std::atomic_int& off, std::string& first)
{
    (std::this_thread::get_id() == g_mainTid.load() ? on : off).fetch_add(1);
    std::lock_guard l(st.mx);
    if (first.empty()) first = Tid();
}

std::atomic_bool g_s2NullCb{false};  // DEVIATION: phase C passes a real callback unless "s2nullcb" was sent
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

    // Phase C: the FAILED-dispatch path. A script that does not exist makes Dispatch* return false.
    // Does the VM still destroy the arguments object, or must the caller?
    Log(std::string("spike: S2 phase C starting, null callback=") + (g_s2NullCb ? "yes" : "no"));
    (void)OnMainThread([st](std::function<void(std::string)> finish) {
        auto* vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> none;
        if (!g_s2NullCb) none = RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor>{ new Functor([](Var) {}) };
        for (int i = 0; i < 100; ++i) {
            if (vm->DispatchStaticCall(RE::BSFixedString("SpikeNoSuchScript"), RE::BSFixedString("NoSuchFn"), NoArgs(), none))
                st->failedTrue.fetch_add(1);
            else
                st->failedFalse.fetch_add(1);
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
    if (line == "s2nullcb") { g_s2NullCb = true; return "OK null callback in phase C"; }
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
