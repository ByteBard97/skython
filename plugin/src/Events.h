#pragma once
#include "spy/errors.h"  // spy::json
#include "spy/events.h"
#include <cstdint>
#include <functional>
#include <string_view>
#include <vector>

namespace RE { class TESForm; }

// The plugin's event plumbing. Sinks run on engine threads that vary (S5): they never touch the VM
// or Python, never block, and only copy ids and values into Push.
namespace spy::events {
spy::EventHub& Hub();                                  // immortal; epoch = life::Epoch, loading = life::SaveLoadInProgress
void Push(std::string_view event, json data) noexcept;
json FormRefJson(const RE::TESForm* form);             // a form reference, or null for a null form
void RegisterSinks();                                  // once, at kDataLoaded: batch 1 here, then RegisterWorldSinks
void RegisterWorldSinks();                             // batch 2 (EventSinksWorld.cpp)
void NoteSinkError(const char* sink, const char* what) noexcept;  // logs the first failure per sink only
void NoteFirstEvent(const char* event) noexcept;                  // one line per event name: the S5-style thread note
std::uint64_t SkippedNullTotal() noexcept;  // events not pushed because a required (non-null) form field was null

// Each engine sink, the hub events it serves, and how to add/remove it (inside an SKSE task only).
struct SinkEntry
{
    const char* sink;
    std::vector<const char*> events;
    std::function<void()> add, remove;
    bool attached = false;  // touched only inside our SKSE tasks (serialized, as observed)
};
std::vector<SinkEntry>& Sinks();  // filled once at kDataLoaded (before Python starts), never resized later
void OnWanted(std::string_view event, bool wanted);  // the hub's on_wanted: queues one reconcile task per affected sink
}
