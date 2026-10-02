#include "spy/console_echo.h"
#include <algorithm>

namespace spy {
ConsoleEcho::ConsoleEcho(Sink sink) : ConsoleEcho(std::move(sink), Options{}) {}

ConsoleEcho::ConsoleEcho(Sink sink, Options opts)
    : sink_(std::move(sink)), opts_(opts), tokens_(opts.burst), last_(Clock::now()), worker_([this] { Worker(); }) {}

ConsoleEcho::~ConsoleEcho()
{
    {
        std::lock_guard lk(mx_);
        stop_ = true;
    }
    cv_.notify_all();
    if (worker_.joinable()) worker_.join();
}

void ConsoleEcho::Refill(Clock::time_point now)
{
    const double add = std::chrono::duration<double>(now - last_) / std::chrono::duration<double>(opts_.refill);
    tokens_ = std::min<double>(opts_.burst, tokens_ + add);
    last_ = now;
}

void ConsoleEcho::Echo(std::string message) noexcept
{
    try {
        if (message.size() > opts_.max_line) message.resize(opts_.max_line);
        {
            std::lock_guard lk(mx_);
            Refill(Clock::now());
            if (tokens_ >= 1.0) {
                tokens_ -= 1.0;
                queue_.push_back(std::move(message));
            } else {
                ++pending_;
                ++suppressed_;
            }
        }
        cv_.notify_one();
    } catch (...) {}  // allocation failure: the line is lost, the caller is not disturbed
}

void ConsoleEcho::Worker()
{
    std::unique_lock lk(mx_);
    for (;;) {
        try {
            if (stop_) return;
            if (queue_.empty() && pending_ > 0) {
                Refill(Clock::now());
                if (tokens_ >= 1.0) {
                    tokens_ -= 1.0;
                    queue_.push_back("Skython: " + std::to_string(pending_) + " more script errors; see python.log");
                    pending_ = 0;
                }
            }
            if (!queue_.empty()) {
                std::string line = std::move(queue_.front());
                queue_.pop_front();
                ++printed_;
                lk.unlock();
                try { sink_(line); } catch (...) {}
                lk.lock();
                continue;
            }
            if (pending_ > 0) cv_.wait_for(lk, opts_.refill / 4);  // wake when a token may be free for the summary
            else cv_.wait(lk);
        } catch (...) {
            // No exception may end this thread: it would terminate the game. An allocation failure (the summary
            // text) drops the pending summary, so the loop cannot spin on it; the lock is held again, as at the top
            // of the loop.
            if (!lk.owns_lock()) lk.lock();
            pending_ = 0;
        }
    }
}

std::uint64_t ConsoleEcho::Printed() const { std::lock_guard lk(mx_); return printed_; }
std::uint64_t ConsoleEcho::Suppressed() const { std::lock_guard lk(mx_); return suppressed_; }
}
