#pragma once
#include "spy/dispatcher.h"

namespace spy::game {
// The spy::Backend that serves the game: one AddTask per request, ops::Run inside that task.
spy::Backend& Backend();
// Built on first use from settings::Get() (default_timeout_ms, max_inflight_total), reaper started. Immortal:
// VM callbacks can complete requests after static destruction has begun, so it is never destroyed.
spy::Dispatcher& GetDispatcher();
}
