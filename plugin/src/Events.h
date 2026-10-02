#pragma once
#include "spy/errors.h"  // spy::json
#include "spy/events.h"
#include <cstdint>
#include <string_view>

namespace RE { class TESForm; }

// The plugin's event plumbing (Contracts: Plugin). Sinks run on engine threads that vary (S5): they never touch the VM
// or Python, never block, and only copy ids and values into Push.
namespace spy::events {
spy::EventHub& Hub();                                  // immortal; epoch = life::Epoch, loading = life::SaveLoadInProgress
void Push(std::string_view event, json data) noexcept;
json FormRefJson(const RE::TESForm* form);             // a form reference, or null for a null form
void RegisterSinks();                                  // once, at kDataLoaded: batch 1 here, then RegisterWorldSinks
void RegisterWorldSinks();                             // batch 2 (Task 40, EventSinksWorld.cpp)
void NoteSinkError(const char* sink, const char* what) noexcept;  // logs the first failure per sink only
void NoteFirstEvent(const char* event) noexcept;                  // one line per event name: the S5-style thread note
std::uint64_t SkippedNullTotal() noexcept;  // events not pushed because a required (non-null) form field was null
}
