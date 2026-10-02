#include "Events.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "PapyrusValues.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <format>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>

namespace spy::events {
spy::EventHub& Hub()
{
    // Designated initializers: EventHubOptions gains members after these (Task 34's byte budget, Task 44's on_wanted).
    // loading is a save load or new game only, never a door or coc loading screen (R60).
    static spy::EventHub* hub = new spy::EventHub(spy::EventHubOptions{
        .epoch = [] { return life::Epoch(); },
        .loading = [] { return life::SaveLoadInProgress(); } });
    return *hub;
}

void Push(std::string_view event, json data) noexcept
{
    try {
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
        static std::mutex* mx = new std::mutex;
        static std::set<std::string>* seen = new std::set<std::string>;
        std::lock_guard lk(*mx);
        if (!seen->insert(event).second) return;
        std::ostringstream tid;
        tid << std::this_thread::get_id();
        // A note for RESULTS only (S5 style). Nothing in the plugin keys on a thread id (R49).
        log::Info(std::format("events: first {} event on thread {} (inside our SKSE task: {})", event, tid.str(),
                              ops::t_inTask ? "yes" : "no"));
    } catch (...) {}
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
}
}
