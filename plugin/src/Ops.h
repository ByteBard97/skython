#pragma once
#include "spy/dispatcher.h"
#include "spy/requests.h"
#include <utility>

namespace spy::ops {
// true while this thread runs a task this plugin queued (R52). Every AddTask lambda in the plugin starts with `ops::InTask in;`.
inline thread_local bool t_inTask = false;
struct InTask { InTask() : prev_(std::exchange(t_inTask, true)) {} ~InTask() { t_inTask = prev_; }
                InTask(const InTask&) = delete; InTask& operator=(const InTask&) = delete; private: bool prev_; };

// Inside an SKSE task only. Validation failures become Err results; nothing throws out of Run.
// `done` may be called inside an SKSE task or on a VM thread; the Dispatcher accepts either (Task 18).
void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress);

// Fills `player` and `game_days_passed` (inside an SKSE task; only when a game is loaded and not loading).
void AddStateExtras(json& state);

// Adds `paused` and `menus` to an error detail (spec 4.1).
json WithGameState(json detail = json::object());
OpResult NotReady(const std::string& reason);
}
