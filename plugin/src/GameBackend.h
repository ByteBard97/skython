#pragma once
#include "spy/dispatcher.h"

namespace spy::game {
spy::Backend& Backend();
spy::Dispatcher& GetDispatcher();  // built on first use from settings::Get(); reaper started; immortal
}
