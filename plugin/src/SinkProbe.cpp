#include "SinkProbe.h"
#include "Log.h"
#include "Ops.h"
#include "spy/errors.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>

namespace spy::probe {
namespace {
using namespace std::chrono_literals;

std::atomic<int> g_inProcess{ 0 };               // spare-sink ProcessEvent calls running right now (any thread)
std::atomic<std::uint64_t> g_overlaps{ 0 };      // add/remove calls that saw g_inProcess > 0
std::atomic<std::uint64_t> g_outside{ 0 };       // spare-sink events delivered outside our SKSE tasks

struct InProcess
{
    InProcess()
    {
        g_inProcess.fetch_add(1);
        if (!ops::t_inTask) g_outside.fetch_add(1);  // the InTask flag, never a thread-id test
    }
    ~InProcess() { g_inProcess.fetch_sub(1); }
    InProcess(const InProcess&) = delete;
    InProcess& operator=(const InProcess&) = delete;
};

struct SpareSink final : RE::BSTEventSink<RE::TESActivateEvent>
{
    std::atomic<std::uint64_t> delivered{ 0 };      // activations by the player only (the hub's filter in the test)
    std::atomic<std::uint64_t> delivered_any{ 0 };  // every TESActivateEvent, NPC doors too (for the record)
    RE::BSEventNotifyControl ProcessEvent(const RE::TESActivateEvent* a_event,
                                          RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        InProcess in;  // nothing else: no engine call beyond the pointer compare, no allocation
        delivered_any.fetch_add(1);
        if (a_event && a_event->actionRef.get() == RE::PlayerCharacter::GetSingleton()) delivered.fetch_add(1);
        return RE::BSEventNotifyControl::kContinue;
    }
};
struct SpareMenuSink final : RE::BSTEventSink<RE::MenuOpenCloseEvent>
{
    std::atomic<std::uint64_t> delivered{ 0 };
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent*,
                                          RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        InProcess in;
        delivered.fetch_add(1);
        return RE::BSEventNotifyControl::kContinue;
    }
};
// Immortal: a source may still hold or call a sink after we return.
SpareSink* Spare() { static auto* s = new SpareSink(); return s; }
SpareMenuSink* SpareMenu() { static auto* s = new SpareMenuSink(); return s; }

void NoteOverlap() { if (g_inProcess.load() > 0) g_overlaps.fetch_add(1); }  // called right before each add/remove

struct Wait
{
    std::mutex mx;
    std::condition_variable cv;
    bool done = false, failed = false;
};

// Queue fn as one SKSE task and wait for it. fn captures only immortal pointers, so a task that runs after a timeout
// is still safe.
bool RunInTask(std::function<void()> fn)
{
    auto w = std::make_shared<Wait>();
    SKSE::GetTaskInterface()->AddTask([w, fn = std::move(fn)] {
        ops::InTask in;
        ops::Measured measured;
        bool failed = false;
        try { fn(); } catch (...) { failed = true; }
        { std::lock_guard lk(w->mx); w->done = true; w->failed = failed; }
        w->cv.notify_all();
    });
    std::unique_lock lk(w->mx);
    return w->cv.wait_for(lk, 5s, [&] { return w->done; }) && !w->failed;
}

json Summary(const char* mode, int cycles, bool registered, int errors)
{
    return json{ { "mode", mode }, { "cycles", cycles }, { "registered", registered },
                 { "delivered", Spare()->delivered.load() }, { "delivered_any", Spare()->delivered_any.load() },
                 { "menu_delivered", SpareMenu()->delivered.load() }, { "events_outside_tasks", g_outside.load() },
                 { "overlaps", g_overlaps.load() }, { "errors", errors } };
}

void ZeroCounters()
{
    Spare()->delivered.store(0);
    Spare()->delivered_any.store(0);
    SpareMenu()->delivered.store(0);
    g_outside.store(0);
    g_overlaps.store(0);
}
}

std::string SinkCycles(int n)
{
    auto* holder = RE::ScriptEventSourceHolder::GetSingleton();
    auto* ui = RE::UI::GetSingleton();
    SpareSink* s = Spare();
    SpareMenuSink* m = SpareMenu();
    if (!holder || !ui || n < -1) {
        const auto out = Summary("refused", 0, false, 1);
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    if (n == -1) {  // arm: from here on every player activation must arrive exactly once
        const bool ok = RunInTask([holder, s] {
            NoteOverlap();
            holder->AddEventSink<RE::TESActivateEvent>(s);
            ZeroCounters();
        });
        const auto out = Summary("arm", 0, ok, ok ? 0 : 1);
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    if (n == 0) {  // finish: report what arrived since the arm, then remove the spare activate sink
        const auto out_before = Summary("finish", 0, false, 0);
        const bool ok = RunInTask([holder, s] {
            NoteOverlap();
            holder->RemoveEventSink<RE::TESActivateEvent>(s);
        });
        auto out = out_before;
        out["errors"] = ok ? 0 : 1;
        log::Info("probe: " + dump_json(out));
        return dump_json(out);
    }
    ZeroCounters();
    int cycles = 0, errors = 0;
    for (int i = 0; i < n && errors == 0; ++i) {
        if (!RunInTask([holder, ui, s, m] {
                NoteOverlap();
                holder->AddEventSink<RE::TESActivateEvent>(s);
                NoteOverlap();
                ui->AddEventSink<RE::MenuOpenCloseEvent>(m);
            })) { ++errors; break; }
        if (!RunInTask([holder, ui, s, m] {
                NoteOverlap();
                holder->RemoveEventSink<RE::TESActivateEvent>(s);
                NoteOverlap();
                ui->RemoveEventSink<RE::MenuOpenCloseEvent>(m);
            })) { ++errors; break; }
        ++cycles;
    }
    // A failed step may have left a sink registered: one more task removes both (removing an absent sink is a no-op).
    if (errors) RunInTask([holder, ui, s, m] {
        holder->RemoveEventSink<RE::TESActivateEvent>(s);
        ui->RemoveEventSink<RE::MenuOpenCloseEvent>(m);
    });
    const auto out = Summary("cycles", cycles, false, errors);
    log::Info("probe: " + dump_json(out));
    return dump_json(out);
}
}
