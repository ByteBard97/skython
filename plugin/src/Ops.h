#pragma once
#include "spy/clock.h"
#include "spy/dispatcher.h"
#include "spy/requests.h"
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <utility>

namespace spy::ops {
// true while this thread runs a task this plugin queued (R52). Every AddTask lambda in the plugin starts with `ops::InTask in;`.
inline thread_local bool t_inTask = false;
struct InTask { InTask() : prev_(std::exchange(t_inTask, true)) {} ~InTask() { t_inTask = prev_; }
                InTask(const InTask&) = delete; InTask& operator=(const InTask&) = delete; private: bool prev_; };

// Task 31 (ruling R38): how long our queued task bodies hold the main thread in one frame. Bodies that start less than
// kGap after the previous one ended belong to the same run (one frame's drain). A run is folded into the maxima
// when the next run starts, so the latest run shows up after one more task. Written only from inside our SKSE tasks,
// which are serialized (observed, R49), on varying threads; the maxima are atomics because BaseState() reads them
// from any thread. No I/O, no allocation.
class DrainMeter
{
public:
    void Begin()
    {
        const auto now = Clock::now();
        if (count_ == 0 || now - lastEnd_ > kGap) {
            Fold();
            start_ = now;
            count_ = 0;
        }
        ++count_;
    }
    void End() { lastEnd_ = Clock::now(); }
    double MaxMs() const { return maxMs_.load(); }
    std::uint32_t MaxCount() const { return maxCount_.load(); }      // the most bodies in any one run
    std::uint32_t MaxMsCount() const { return maxMsCount_.load(); }  // the bodies in the run that set MaxMs()

private:
    using Clock = std::chrono::steady_clock;
    static constexpr auto kGap = std::chrono::milliseconds(1);
    void Fold()
    {
        if (count_ == 0) return;
        const double ms = std::chrono::duration<double, std::milli>(lastEnd_ - start_).count();
        if (ms > maxMs_.load()) {
            // The count of the run that set the max ms, stored with it, so max_ms / count is that run's per-task
            // cost. BaseState() reads the two atomics separately; a read racing a Fold may pair a new ms with the
            // old count (rare, diagnostics only).
            maxMsCount_.store(count_);
            maxMs_.store(ms);
        }
        if (count_ > maxCount_.load()) maxCount_.store(count_);
    }
    Clock::time_point start_{}, lastEnd_{};
    std::uint32_t count_ = 0;
    std::atomic<double> maxMs_{0.0};
    std::atomic<std::uint32_t> maxCount_{0};
    std::atomic<std::uint32_t> maxMsCount_{0};
};

inline DrainMeter& Meter()
{
    static auto* meter = new DrainMeter();  // immortal, like the dispatcher
    return *meter;
}

struct Measured
{
    Measured() { Meter().Begin(); }
    ~Measured() { Meter().End(); }
};

// Task 38 (trace_timing, dev only, record only): one request's stamps, spy::NowUs() microseconds. GameBackend makes one
// per request when trace_timing is on and sets t_timing while its task body runs; a Callback built in that body keeps
// it and stamps `callback` when the VM calls back (on whatever thread). Null when trace_timing is off: nothing is taken.
struct Timing
{
    std::uint64_t id = 0;
    std::int64_t submit = 0;
    std::atomic<std::int64_t> task_start{0};
    std::atomic<std::int64_t> callback{0};
    std::int64_t frame_dt = 0;  // RE::GetSecondsSinceLastFrame() in µs, read at task start
};
inline thread_local std::shared_ptr<Timing> t_timing;
struct WithTiming
{
    explicit WithTiming(std::shared_ptr<Timing> t) : prev_(std::exchange(t_timing, std::move(t))) {}
    ~WithTiming() { t_timing = std::move(prev_); }
    WithTiming(const WithTiming&) = delete;
    WithTiming& operator=(const WithTiming&) = delete;

private:
    std::shared_ptr<Timing> prev_;
};

// Inside an SKSE task only. Validation failures become Err results; nothing throws out of Run.
// `done` may be called inside an SKSE task or on a VM thread; the Dispatcher accepts either (Task 18).
void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress);

// Fills `player` and `game_days_passed` (inside an SKSE task; only when a game is loaded and not loading).
void AddStateExtras(json& state);

// Adds `paused` and `menus` to an error detail (spec 4.1).
json WithGameState(json detail = json::object());
OpResult NotReady(const std::string& reason);
}
