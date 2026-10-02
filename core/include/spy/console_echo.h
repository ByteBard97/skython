#pragma once
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace spy {
// Rate-limited one-line messages (R38 item 2). A token bucket: `burst` lines at once, then one per `refill`. Lines over
// the rate are counted, and when a token is free again one summary line "Skython: N more script errors; see
// python.log" is emitted. Echo never blocks beyond one short mutex section and never throws; the sink runs on this
// object's own worker thread (never the caller's), so a slow or throwing sink cannot hold anyone up.
class ConsoleEcho
{
public:
    using Sink = std::function<void(const std::string&)>;
    struct Options
    {
        int burst = 5;
        std::chrono::milliseconds refill{ 1000 };
        std::size_t max_line = 500;  // longer lines are cut (the console shows one line)
    };
    // Two constructors, not `Options opts = {}`: GCC 11 rejects a default argument that needs a nested struct's default
    // member initializers inside the enclosing class ("could not convert '{}'"; measured while checking this plan).
    explicit ConsoleEcho(Sink sink);
    ConsoleEcho(Sink sink, Options opts);
    ~ConsoleEcho();
    ConsoleEcho(const ConsoleEcho&) = delete;
    ConsoleEcho& operator=(const ConsoleEcho&) = delete;

    void Echo(std::string message) noexcept;
    std::uint64_t Printed() const;
    std::uint64_t Suppressed() const;

private:
    using Clock = std::chrono::steady_clock;
    void Refill(Clock::time_point now);  // with mx_ held
    void Worker();

    Sink sink_;
    Options opts_;
    mutable std::mutex mx_;
    std::condition_variable cv_;
    std::deque<std::string> queue_;
    double tokens_;
    Clock::time_point last_;
    std::uint64_t pending_ = 0, printed_ = 0, suppressed_ = 0;
    bool stop_ = false;
    std::thread worker_;
};
}
