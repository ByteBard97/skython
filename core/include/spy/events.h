#pragma once
#include "spy/errors.h"              // json, ProtocolError, dump_json
#include "spy/protocol_generated.h"  // kEvents, find_event, kDefaultEventQueue, kMaxEventQueue, kMaxSubscriptions, ...
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace spy {
using SubId = std::uint64_t;       // from a counter, never reused, starts at 1
using ConsumerId = std::uint64_t;  // from a counter, never reused, starts at 1; 0 means "none"

// A validated filter (PROTOCOL.md, Events): every listed field's value must be one of its values. Values are
// normalised by parse_event_filter: form fields hold form ids, int fields Papyrus Ints, string fields ASCII-lowercased text.
struct EventFilter
{
    std::vector<std::pair<std::string, std::vector<json>>> any_of;  // validated; empty = match all
    // Set by parse_event_filter so a push never looks them up: the event's table entry and, parallel to any_of, each
    // field's entry (both point into the static protocol tables). A filter built by hand leaves them empty and
    // filter_matches looks the fields up instead.
    const EventSpec* spec = nullptr;
    std::vector<const EventField*> fields;
};
// `filter` null means no filter. Checks field names against the event's table entry, value types, at most
// kMaxFilterValues values per field, and filter_required_any_of.
EventFilter parse_event_filter(std::string_view event, const json& filter);  // throws ProtocolError(bad_request)
// AND across fields, OR within one; a missing or null event field never matches; strings ignore ASCII case.
bool filter_matches(const EventFilter&, std::string_view event, const json& data);

// Each queued event counts dump_json(data).size() + this many bytes against its consumer's byte budget.
inline constexpr std::size_t kEventFrameOverheadBytes = 96;

struct EventHubOptions
{
    std::function<std::uint32_t()> epoch;   // LifecycleState::epoch (life::Epoch) in the plugin; a counter in tests (empty: 0)
    std::function<bool()> loading;          // life::SaveLoadInProgress in the plugin (Task 39); a switch in tests (empty: false)
    std::size_t max_queued_bytes_per_consumer = kMaxQueuedEventBytesPerConsumer;  // R61; tests lower it
    // Task 44 (its sink-detach probe passed): called when an event's subscriber count goes 0->1 (wanted = true) or
    // 1->0 (wanted = false), from Subscribe, Unsubscribe and CloseConsumer, always after the hub's locks are released
    // (it may call back into the hub). An exception it throws is swallowed. Empty: never called. Optional and last: an
    // initializer that sets it uses designated initializers (.epoch = ..., .loading = ..., .on_wanted = ...).
    std::function<void(std::string_view event, bool wanted)> on_wanted{};  // Task 44: subscriber count 0->1 (true) / 1->0 (false); optional
};

// Event consumers, subscriptions and their bounded queues (spec 4.6). Pure C++: no game, no Python. Thread-safe.
// Locking: one hub mutex for every queue and table plus one condition variable per consumer (notified after the hub
// mutex is released); and one drain mutex per consumer, held by Next while it takes events and builds their frames,
// and taken by Unsubscribe and CloseConsumer before they purge (lock order: drain, then hub). Push reads epoch() and
// loading() and measures the event's bytes once, outside the hub mutex, and holds the hub mutex only to filter, stamp
// and queue. Bounds: max_queue per subscription, max_subscriptions_per_consumer and max_subscriptions, and
// max_queued_bytes_per_consumer (the oldest events of the pushed subscription are dropped to stay under it).
class EventHub
{
public:
    explicit EventHub(EventHubOptions);
    EventHub(const EventHub&) = delete;
    EventHub& operator=(const EventHub&) = delete;

    ConsumerId OpenConsumer();
    void CloseConsumer(ConsumerId);                  // drops its subscriptions and queued events; wakes a waiting Next; idempotent
    json Subscribe(ConsumerId, const json& args);    // {sub, event, max_queue}; throws ProtocolError (bad_request, busy)
    void Unsubscribe(ConsumerId, SubId);             // purges that subscription's queue; throws ProtocolError(not_found)
    bool Wants(std::string_view event) const;        // lock-free (per-event atomic subscriber count)
    void Push(std::string_view event, json data);    // filters, stamps epoch/seq, drop-oldest; never throws, never waits
    std::vector<std::string> Next(ConsumerId, std::chrono::milliseconds max_wait,
                                  std::size_t max_events = kMaxEventsPerNext);  // frame texts in push order across
                                                     // the consumer's subscriptions; empty on timeout or unknown consumer
    std::size_t Consumers() const;
    std::size_t Subscriptions() const;
    std::uint64_t DroppedTotal() const;
    std::uint64_t SuppressedTotal() const;

private:
    struct Queued { std::uint64_t order; std::uint64_t seq; std::uint32_t epoch; std::size_t bytes; std::shared_ptr<const json> data; };
    struct Sub
    {
        SubId id = 0;
        ConsumerId consumer = 0;
        std::size_t event_index = 0;  // index into kEvents
        EventFilter filter;
        std::size_t max_queue = kDefaultEventQueue;
        std::uint64_t seq = 0;         // accepted events so far
        std::uint64_t dropped = 0;     // since the last delivered event
        std::uint64_t suppressed = 0;  // since the last delivered event
        std::deque<Queued> queue;
    };
    struct Consumer
    {
        std::vector<SubId> subs;
        std::size_t queued = 0;  // events in all of its subscriptions' queues
        std::size_t bytes = 0;   // their byte-budget sizes (Queued::bytes)
        std::shared_ptr<std::mutex> drain = std::make_shared<std::mutex>();
        std::shared_ptr<std::condition_variable> cv = std::make_shared<std::condition_variable>();  // Next waits here
    };
    using WantedChanges = std::vector<std::pair<std::string_view, bool>>;  // event name (static table text), wanted
    void RemoveSubLocked(SubId id, WantedChanges& changes);  // hub mutex held; a count 1->0 is appended to changes
    void NotifyWanted(const WantedChanges& changes) const;   // no hub lock held
    void DropOldestLocked(Sub& s, Consumer& c);  // hub mutex held; s.queue not empty

    EventHubOptions options_;
    mutable std::mutex mx_;
    std::map<ConsumerId, Consumer> consumers_;
    std::map<SubId, Sub> subs_;
    std::array<std::vector<SubId>, kEvents.size()> by_event_;
    std::array<std::atomic<std::uint32_t>, kEvents.size()> wanted_{};
    ConsumerId next_consumer_ = 1;
    SubId next_sub_ = 1;
    std::uint64_t order_ = 0;  // global push order, for Next's merge across subscriptions
    std::uint64_t dropped_total_ = 0;
    std::uint64_t suppressed_total_ = 0;
};
}
