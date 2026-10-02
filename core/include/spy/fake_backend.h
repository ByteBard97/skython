#pragma once
#include "spy/dispatcher.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <thread>
#include <vector>

namespace spy {
// A Backend with no game, for Linux tests of the Dispatcher, the _skython module and everything above it.
// papyrus.call_static on script "Fake":
//   Echo(x) -> x          Sleep(ms) -> ms, after ms milliseconds (other calls are not held up)
//   Fail(code, message) -> that error (an unknown code is internal)
//   Throw() -> Run throws std::runtime_error      Never() -> never answers      Count() -> number of Run calls so far
// game.state -> BaseState() plus "backend": "fake", answered inside Run.
// game.save {name} -> {"name": name}, recorded; an existing name -> bad_request, detail.reason "save_exists".
// game.load {save} -> not_ready while NotReadyReason(false) is "starting" or "loading" (the Dispatcher skips readiness
//                     for this needs_game: false op); an unknown name -> not_found; else BumpLoads(), then
//                     {"save": name, "started": true}. Both answer inside Run.
// Any other function -> unknown_function; any other script or op -> unsupported.
class FakeBackend : public Backend
{
public:
    FakeBackend();
    ~FakeBackend() override;  // stops the worker; queued answers are dropped
    FakeBackend(const FakeBackend&) = delete;
    FakeBackend& operator=(const FakeBackend&) = delete;

    void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) override;
    json BaseState() override;
    std::optional<std::string> NotReadyReason(bool require_unpaused) override;
    std::uint32_t LoadsStarted() override;

    void SetNotReady(std::optional<std::string> reason);  // "starting", "loading", "no_game", or nullopt for ready
    void BumpLoads();                                      // a save started loading
    void SetPaused(bool paused);
    std::uint64_t RunCount() const;
    void AddFakeSave(std::string name);       // a save game.load can find
    std::vector<std::string> FakeSaves() const;  // sorted

private:
    void Schedule(std::chrono::steady_clock::time_point when, std::function<void()> job);
    void Worker();

    mutable std::mutex mx_;
    std::condition_variable cv_;
    std::multimap<std::chrono::steady_clock::time_point, std::function<void()>> jobs_;
    bool stop_ = false;
    std::optional<std::string> not_ready_;
    bool paused_ = false;
    std::set<std::string> saves_;
    std::atomic<std::uint32_t> loads_{0};
    std::atomic<std::uint64_t> runs_{0};
    std::thread worker_;
};
}
