#pragma once
#include <chrono>
#include <cstdint>

namespace spy {
// Microseconds on std::chrono::steady_clock: the one clock of every trace_timing stamp: GameBackend, the VM
// callback, and Python through _skython.now_us (the bridge), so stamps taken in any of them can be subtracted.
inline std::int64_t NowUs() noexcept
{
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
}
