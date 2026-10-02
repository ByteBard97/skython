#pragma once
#include "spy/requests.h"  // json, ErrorCode, make_ok, make_error, parse_request_text
#include "spy/events.h"  // EventHub, ConsumerId, SubId
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace spy {
// The result of one op, as a backend reports it.
struct OpResult
{
    bool ok = true;
    json result;
    ErrorCode code = ErrorCode::internal;
    std::string message;
    json detail = json::object();
    static OpResult Ok(json result) { OpResult r; r.result = std::move(result); return r; }
    static OpResult Err(ErrorCode code, std::string message, json detail = json::object())
    {
        OpResult r;
        r.ok = false;
        r.code = code;
        r.message = std::move(message);
        r.detail = std::move(detail);
        return r;
    }
};
using Done = std::function<void(OpResult)>;  // call exactly once, from any thread; later calls are counted as late
using Cancelled = std::function<bool()>;     // true once the request timed out, was cancelled, or a world reset started
using Progress = std::function<void(json)>;  // batch: {"completed": n, "results": [...]} so far, any thread

// What runs ops: the game (plugin, Task 27) or the fake (Linux tests, below in fake_backend.h).
class Backend
{
public:
    virtual ~Backend() = default;
    // Must not block the caller. May call done synchronously (game.state does).
    virtual void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) = 0;
    virtual json BaseState() = 0;                                               // any thread, never blocks
    virtual std::optional<std::string> NotReadyReason(bool require_unpaused) = 0;  // any thread
    virtual std::uint32_t LoadsStarted() = 0;  // any thread; counts world resets (save loads, quits to the main menu)
};

using Ticket = std::uint64_t;
struct DispatcherOptions
{
    std::uint32_t default_timeout_ms = 5000;
    std::uint32_t max_inflight_total = 1024;
    std::uint64_t abandon_after_ms = kMaxTimeoutMs;             // a response nobody collected is dropped after this
    std::function<void(const std::string&)> on_late = nullptr;  // told about each dropped late result, outside the lock
    EventHub* events = nullptr;  // answers events.subscribe / events.unsubscribe; null -> those ops are unsupported
};

// Turns request text into backend calls and backend results into response text (spec 4.2, 4.5). Thread-safe.
// Rules (index Contracts): bad text -> bad_request at once; a needs_game op while NotReadyReason() has a value ->
// not_ready at once; more than max_inflight_total in flight -> busy; past its deadline -> timeout; a world reset
// (LoadsStarted() differs from its value at Submit) -> not_ready for that needs_game call, reason from
// NotReadyReason(false) (else "loading"), detail.dispatched = true if it had reached Backend::Run; an exception from
// Run -> internal; events.* -> EventHub (unsupported without one; bad_request without a consumer). timeout_ms and require_unpaused are read only for ops whose OpSpec declares them. Backend::Run,
// done callbacks and on_late never run while the dispatcher's lock is held.
// "In flight" means Dispatcher entries: a cancelled or timed-out call frees its slot at once, even if the backend's
// work (a Papyrus call suspended in the VM) is still running. That is a known limit of v1.
class Dispatcher
{
public:
    Dispatcher(Backend& backend, DispatcherOptions options);
    ~Dispatcher();  // stops the reaper; results that arrive later are dropped safely
    Dispatcher(const Dispatcher&) = delete;
    Dispatcher& operator=(const Dispatcher&) = delete;

    // Never throws; a refused request yields a ticket that is already done. `consumer` travels beside the request text
    // (never inside it) and is used only by events.subscribe / events.unsubscribe, which the hub answers at once: no
    // backend call, no deadline, no in-flight slot, no readiness check. 0 means "no consumer" (those ops: bad_request).
    Ticket Submit(std::string_view request_text, ConsumerId consumer = 0);
    // Response text {type, id, ok, result|error}; nullopt if not done within max_wait, or unknown. A returned ticket is consumed.
    std::optional<std::string> Wait(Ticket ticket, std::chrono::milliseconds max_wait);
    // The finished ones among `tickets` (consumed), as soon as at least one is finished or max_wait passes.
    std::vector<std::pair<Ticket, std::string>> WaitAny(const std::vector<Ticket>& tickets, std::chrono::milliseconds max_wait);
    void Cancel(Ticket ticket);  // erases the ticket; a later Wait returns nullopt; a late result is dropped and counted
    // Like Cancel, but if the ticket already has its response, returns it (consumed) instead of throwing it away.
    std::optional<std::string> CancelOrTake(Ticket ticket);
    void Tick();  // deadlines, world resets, abandoned results; the reaper calls it, tests call it directly
    void StartReaper(std::chrono::milliseconds period = std::chrono::milliseconds(50));  // a second call is a no-op
    json BaseState();            // the backend's BaseState(): _skython.state() and late_cancel replies use it
    std::size_t InFlight() const;
    std::uint64_t LateResults() const;  // backend results that arrived after their ticket ended
    std::uint64_t Abandoned() const;    // finished results nobody collected within abandon_after_ms

    struct Shared;  // the ticket table; results hold it by shared_ptr so a late done never touches a dead Dispatcher

private:
    Backend& backend_;
    DispatcherOptions options_;
    std::shared_ptr<Shared> shared_;
    std::mutex tick_mx_;  // one Tick at a time (reaper and tests)

    std::mutex reaper_mx_;
    std::condition_variable reaper_cv_;
    bool reaper_stop_ = false;
    std::thread reaper_;
};
}
