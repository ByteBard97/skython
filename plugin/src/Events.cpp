#include "Events.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "PapyrusValues.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <format>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>

namespace spy::events {
spy::EventHub& Hub()
{
    // Designated initializers: EventHubOptions has more members after these (the byte budget, on_wanted).
    // loading is a save load or new game only, never a door or coc loading screen.
    static spy::EventHub* hub = new spy::EventHub(spy::EventHubOptions{
        .epoch = [] { return life::Epoch(); },
        .loading = [] { return life::SaveLoadInProgress(); },
        .on_wanted = [](std::string_view e, bool w) { OnWanted(e, w); } });
    return *hub;
}

namespace {
std::atomic<std::uint64_t> g_skippedNull{ 0 };
std::array<std::atomic_bool, spy::kEvents.size()> g_firstSeen{};      // NoteFirstEvent: one flag per table entry
std::array<std::atomic_bool, spy::kEvents.size()> g_nullNoted{};      // MissingRequiredForm: logged once per event

std::size_t EventIndex(const spy::EventSpec* spec) { return static_cast<std::size_t>(spec - spy::kEvents.data()); }

// A form field the events table marks non-null (victim, quest, target, actor, item, reference, ...) that the sink could
// not resolve: the event is not pushed (a consumer never sees null where the protocol promises a form) and is counted.
bool MissingRequiredForm(std::string_view event, const json& data) noexcept
{
    const spy::EventSpec* spec = spy::find_event(event);
    if (!spec) return false;
    for (const auto& f : spec->fields) {
        if (f.type != spy::EventFieldType::form || f.nullable) continue;
        const auto it = data.find(f.name);
        if (it != data.end() && !it->is_null()) continue;
        g_skippedNull.fetch_add(1, std::memory_order_relaxed);
        if (!g_nullNoted[EventIndex(spec)].exchange(true)) {
            try {
                log::Warn(std::format("events: {} skipped: required form field {} is null (counted in "
                                      "events_skipped_null_total; logged once per event)", event, f.name));
            } catch (...) {}
        }
        return true;
    }
    return false;
}
}

std::uint64_t SkippedNullTotal() noexcept { return g_skippedNull.load(std::memory_order_relaxed); }

void Push(std::string_view event, json data) noexcept
{
    try {
        if (MissingRequiredForm(event, data)) return;
        Hub().Push(event, std::move(data));
    } catch (...) {  // Push is documented never to throw; this is the belt to that brace
        static std::atomic_bool once{ false };
        if (!once.exchange(true)) log::Error("events: EventHub::Push threw; further failures are not logged");
    }
}

json FormRefJson(const RE::TESForm* form)
{
    if (!form) return nullptr;
    return form_ref_to_json(pv::DescribeFormForEvent(form));
}

void NoteSinkError(const char* sink, const char* what) noexcept
{
    try {
        static std::mutex* mx = new std::mutex;
        static std::set<std::string>* seen = new std::set<std::string>;
        std::lock_guard lk(*mx);
        if (seen->insert(sink).second) log::Error(std::format("events: {} sink threw: {}; later failures of this sink are not logged", sink, what));
    } catch (...) {}
}

void NoteFirstEvent(const char* event) noexcept
{
    try {
        // Fast path for every event after the first: one table lookup and one atomic exchange, no lock, no allocation
        // (a hit flood calls this on engine threads for every event that passes Wants).
        const spy::EventSpec* spec = spy::find_event(event);
        if (!spec || g_firstSeen[EventIndex(spec)].exchange(true)) return;
        std::ostringstream tid;
        tid << std::this_thread::get_id();
        // Diagnostic only. Nothing in the plugin keys on a thread id: SKSE tasks run on varying job threads.
        log::Info(std::format("events: first {} event on thread {} (inside our SKSE task: {})", event, tid.str(),
                              ops::t_inTask ? "yes" : "no"));
    } catch (...) {}
}

std::vector<SinkEntry>& Sinks() { static auto* v = new std::vector<SinkEntry>; return *v; }  // filled at kDataLoaded, then fixed

namespace {
void Reconcile(std::size_t i)
{
    auto& e = Sinks()[i];
    const bool want = std::ranges::any_of(e.events, [](const char* n) { return Hub().Wants(n); });
    if (want == e.attached) return;
    (want ? e.add : e.remove)();
    e.attached = want;
    log::Info(std::string("events: sink ") + (want ? "attached " : "detached ") + e.sink);
}
}

void OnWanted(std::string_view event, bool)
{
    // Any order of callbacks ends right: the task reads Wants() when it runs.
    for (std::size_t i = 0; i < Sinks().size(); ++i) {
        const auto& names = Sinks()[i].events;
        if (std::ranges::none_of(names, [&](const char* n) { return event == n; })) continue;
        SKSE::GetTaskInterface()->AddTask([i] {
            ops::InTask in;
            ops::Measured measured;
            try { Reconcile(i); } catch (...) { NoteSinkError("reconcile", "unknown"); }
        });
    }
}

void RegisterBatch1();  // EventSinks.cpp

void RegisterSinks()
{
    // The class table needs the VM, so it is built inside one SKSE task (the first frame after kDataLoaded, at the main
    // menu). Sinks register now, directly, like the lifecycle's MenuSink; until the table exists an event's type is "Form".
    SKSE::GetTaskInterface()->AddTask([] {
        ops::InTask in;
        ops::Measured measured;
        try {
            pv::BuildClassTable();
        } catch (const std::exception& e) {
            log::Error(std::string("events: class table failed: ") + e.what());
        } catch (...) {
            log::Error("events: class table failed");
        }
    });
    RegisterBatch1();
    RegisterWorldSinks();
    // A batch whose source was null logged an error and added no entries; the line names how many sinks exist.
    log::Info("events: sinks attach on demand (" + std::to_string(Sinks().size()) + " sinks)");
}
}
