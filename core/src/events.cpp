#include "spy/events.h"
#include "spy/values.h"  // form_ref_from_json, is_uint
#include <algorithm>
#include <span>

namespace spy {
namespace {
[[noreturn]] void bad(const std::string& m) { throw ProtocolError(ErrorCode::bad_request, m); }

const EventSpec& spec_of(std::string_view event)
{
    const EventSpec* s = find_event(event);
    if (s == nullptr) bad("unknown event '" + clip_text(event, 64) + "'");
    return *s;
}

std::size_t index_of(const EventSpec& s) { return static_cast<std::size_t>(&s - kEvents.data()); }

const EventField* field_of(const EventSpec& s, std::string_view name)
{
    for (const auto& f : s.fields)
        if (f.name == name) return &f;
    return nullptr;
}

bool contains(std::span<const std::string_view> names, std::string_view name)
{
    return std::find(names.begin(), names.end(), name) != names.end();
}

std::string lower(std::string_view s)
{
    std::string out(s);
    for (auto& ch : out) ch = ascii_lower(ch);
    return out;
}

// a equals b ignoring ASCII case, where b is already lowercase; allocates nothing.
bool equals_lowered(std::string_view a, std::string_view lowered)
{
    if (a.size() != lowered.size()) return false;
    for (std::size_t i = 0; i < a.size(); ++i)
        if (ascii_lower(a[i]) != lowered[i]) return false;
    return true;
}

std::string join(std::span<const std::string_view> names, std::string_view last_sep)
{
    std::string out;
    for (std::size_t i = 0; i < names.size(); ++i) {
        if (i > 0) out += i + 1 == names.size() ? std::string(last_sep) : std::string(", ");
        out += names[i];
    }
    return out;
}

bool is_papyrus_int(const json& v)
{
    if (v.is_number_unsigned()) return v.get<std::uint64_t>() <= static_cast<std::uint64_t>(kIntMax);
    return v.is_number_integer() && v.get<std::int64_t>() >= kIntMin && v.get<std::int64_t>() <= kIntMax;
}

json normalise(const EventSpec& s, const EventField& f, const json& v)
{
    const std::string where = "filter value for " + std::string(s.name) + "." + std::string(f.name);
    switch (f.type) {
    case EventFieldType::form: {
        if (!v.is_object()) bad(where + " must be a form reference, got " + describe(v));
        const FormRef ref = form_ref_from_json(v);  // bad_request for a malformed reference
        if (!ref.form_id) bad(where + " has no form_id: resolve the form first");
        return json(*ref.form_id);
    }
    case EventFieldType::integer:
        if (!is_papyrus_int(v)) bad(where + " must be an integer (a Papyrus Int), got " + describe(v));
        return json(v.get<std::int64_t>());
    case EventFieldType::string:
        if (!v.is_string()) bad(where + " must be a string, got " + describe(v));
        return json(lower(v.get_ref<const std::string&>()));
    default:
        bad(where + ": this field cannot be filtered");
    }
}
}

EventFilter parse_event_filter(std::string_view event, const json& filter)
{
    const EventSpec& s = spec_of(event);
    EventFilter out;
    if (!filter.is_null()) {
        if (!filter.is_object()) bad("filter must be an object mapping filter fields to lists of values");
        for (const auto& [field, values] : filter.items()) {
            if (!contains(s.filter_fields, field))
                bad("event " + std::string(s.name) + " has no filter field '" + clip_text(field, 64) + "'" +
                    (s.filter_fields.empty() ? std::string(" (it cannot be filtered)") : " (filter fields: " + join(s.filter_fields, ", ") + ")"));
            if (!values.is_array() || values.empty()) bad("filter field '" + field + "' needs a non-empty array of values");
            if (values.size() > kMaxFilterValues)
                bad("filter field '" + field + "' has " + std::to_string(values.size()) + " values; at most " + std::to_string(kMaxFilterValues));
            const EventField* f = field_of(s, field);  // the generator checked that every filter field is a field
            std::vector<json> norm;
            norm.reserve(values.size());
            for (const auto& v : values) norm.push_back(normalise(s, *f, v));
            out.any_of.emplace_back(field, std::move(norm));
            out.fields.push_back(f);
        }
    }
    if (!s.filter_required_any_of.empty()) {
        const bool named = std::any_of(out.any_of.begin(), out.any_of.end(),
                                       [&](const auto& p) { return contains(s.filter_required_any_of, p.first); });
        if (!named) bad(std::string(s.name) + " needs a filter on " + join(s.filter_required_any_of, " or "));
    }
    out.spec = &s;
    return out;
}

namespace {
// filter_matches with the event's table entry already looked up (Push does it once per call, not per subscription).
// Uses the filter's cached field entries when it was parsed for this event; looks them up for a hand-built filter.
bool filter_matches_spec(const EventFilter& filter, const EventSpec& s, const json& data)
{
    if (filter.any_of.empty()) return true;
    if (!data.is_object()) return false;
    const bool cached = filter.spec == &s && filter.fields.size() == filter.any_of.size();
    for (std::size_t i = 0; i < filter.any_of.size(); ++i) {
        const auto& [field, values] = filter.any_of[i];
        const EventField* f = cached ? filter.fields[i] : field_of(s, field);
        const auto it = data.find(field);
        if (f == nullptr || it == data.end() || it->is_null()) return false;
        bool hit = false;
        switch (f->type) {
        case EventFieldType::form:
            if (it->is_object() && it->contains("form_id") && is_uint((*it)["form_id"], 0xFFFFFFFFull)) {
                const auto id = (*it)["form_id"].get<std::uint64_t>();
                hit = std::any_of(values.begin(), values.end(), [&](const json& v) { return v.get<std::uint64_t>() == id; });
            }
            break;
        case EventFieldType::integer:
            if (is_papyrus_int(*it)) {
                const auto n = it->get<std::int64_t>();
                hit = std::any_of(values.begin(), values.end(), [&](const json& v) { return v.get<std::int64_t>() == n; });
            }
            break;
        case EventFieldType::string:
            if (it->is_string()) {
                const std::string& text = it->get_ref<const std::string&>();
                hit = std::any_of(values.begin(), values.end(),
                                  [&](const json& v) { return equals_lowered(text, v.get_ref<const std::string&>()); });
            }
            break;
        default:
            break;
        }
        if (!hit) return false;
    }
    return true;
}
}

bool filter_matches(const EventFilter& filter, std::string_view event, const json& data)
{
    if (filter.any_of.empty()) return true;
    const EventSpec* s = find_event(event);
    return s != nullptr && filter_matches_spec(filter, *s, data);
}

namespace {
constexpr std::chrono::milliseconds kMaxNextWait = std::chrono::hours(1);  // Next's longest single wait
}

EventHub::EventHub(EventHubOptions options) : options_(std::move(options)) {}

ConsumerId EventHub::OpenConsumer()
{
    std::lock_guard lk(mx_);
    const ConsumerId id = next_consumer_++;
    consumers_.emplace(id, Consumer{});
    return id;
}

void EventHub::NotifyWanted(const WantedChanges& changes) const
{
    if (!options_.on_wanted) return;
    for (const auto& [event, wanted] : changes) {
        try { options_.on_wanted(event, wanted); } catch (...) {}  // a callback failure never breaks the hub
    }
}

void EventHub::RemoveSubLocked(SubId id, WantedChanges& changes)
{
    const auto it = subs_.find(id);
    if (it == subs_.end()) return;
    Sub& s = it->second;
    auto& list = by_event_[s.event_index];
    list.erase(std::remove(list.begin(), list.end(), id), list.end());
    if (wanted_[s.event_index].fetch_sub(1, std::memory_order_release) == 1)
        changes.emplace_back(kEvents[s.event_index].name, false);
    const auto c = consumers_.find(s.consumer);
    if (c != consumers_.end()) {
        c->second.queued -= s.queue.size();
        for (const auto& q : s.queue) c->second.bytes -= q.bytes;
        auto& owned = c->second.subs;
        owned.erase(std::remove(owned.begin(), owned.end(), id), owned.end());
    }
    subs_.erase(it);
}

void EventHub::DropOldestLocked(Sub& s, Consumer& c)
{
    c.bytes -= s.queue.front().bytes;
    s.queue.pop_front();
    ++s.dropped;
    ++dropped_total_;
    --c.queued;
}

void EventHub::CloseConsumer(ConsumerId consumer)
{
    std::shared_ptr<std::mutex> drain;
    std::shared_ptr<std::condition_variable> cv;
    WantedChanges changes;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return;
        drain = c->second.drain;
        cv = c->second.cv;  // kept alive past the erase: a waiting Next holds it too
    }
    {
        std::lock_guard dl(*drain);  // a Next of this consumer finishes building its frames first
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return;  // another thread closed it meanwhile
        const std::vector<SubId> owned = c->second.subs;
        for (SubId id : owned) RemoveSubLocked(id, changes);
        consumers_.erase(consumer);
    }
    cv->notify_all();  // a Next waiting for this consumer returns empty
    NotifyWanted(changes);
}

json EventHub::Subscribe(ConsumerId consumer, const json& args)
{
    if (!args.is_object()) bad("events.subscribe args must be an object");
    if (!args.contains("event") || !args["event"].is_string()) bad("missing or non-string field 'event'");
    const std::string event = args["event"].get<std::string>();
    const EventSpec& spec = spec_of(event);
    const json none;
    EventFilter filter = parse_event_filter(event, args.contains("filter") ? args["filter"] : none);
    std::size_t max_queue = kDefaultEventQueue;
    if (args.contains("max_queue")) {
        const json& q = args["max_queue"];
        if (!is_uint(q, kMaxEventQueue) || q.get<std::uint64_t>() < 1)
            bad("max_queue must be an integer from 1 to " + std::to_string(kMaxEventQueue));
        max_queue = q.get<std::size_t>();
    }
    const std::size_t index = index_of(spec);

    bool first = false;
    json answer;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) bad("no event consumer " + std::to_string(consumer) + " (never opened, or closed)");
        if (c->second.subs.size() >= kMaxSubscriptionsPerConsumer)
            throw ProtocolError(ErrorCode::busy, "consumer " + std::to_string(consumer) + " has " +
                                                     std::to_string(kMaxSubscriptionsPerConsumer) + " subscriptions (max_subscriptions_per_consumer)");
        if (subs_.size() >= kMaxSubscriptions)
            throw ProtocolError(ErrorCode::busy, "too many event subscriptions (" + std::to_string(kMaxSubscriptions) + ")");
        const SubId id = next_sub_++;
        Sub s;
        s.id = id;
        s.consumer = consumer;
        s.event_index = index;
        s.filter = std::move(filter);
        s.max_queue = max_queue;
        subs_.emplace(id, std::move(s));
        by_event_[index].push_back(id);
        c->second.subs.push_back(id);
        first = wanted_[index].fetch_add(1, std::memory_order_release) == 0;
        answer = {{"sub", id}, {"event", event}, {"max_queue", max_queue}};
    }
    if (first) NotifyWanted({{kEvents[index].name, true}});
    return answer;
}

void EventHub::Unsubscribe(ConsumerId consumer, SubId sub)
{
    const auto missing = [&] { return ProtocolError(ErrorCode::not_found, "no subscription " + std::to_string(sub) + " for this consumer"); };
    std::shared_ptr<std::mutex> drain;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        const auto s = subs_.find(sub);
        if (c == consumers_.end() || s == subs_.end() || s->second.consumer != consumer) throw missing();
        drain = c->second.drain;
    }
    WantedChanges changes;
    {
        std::lock_guard dl(*drain);
        std::lock_guard lk(mx_);
        const auto s = subs_.find(sub);
        if (s == subs_.end() || s->second.consumer != consumer) throw missing();  // a racing Unsubscribe or CloseConsumer won
        RemoveSubLocked(sub, changes);
    }
    NotifyWanted(changes);
}

bool EventHub::Wants(std::string_view event) const
{
    const EventSpec* s = find_event(event);
    return s != nullptr && wanted_[index_of(*s)].load(std::memory_order_acquire) > 0;
}

void EventHub::Push(std::string_view event, json data)
{
    try {
        const EventSpec* spec = find_event(event);
        if (spec == nullptr) return;
        const std::size_t index = index_of(*spec);
        if (wanted_[index].load(std::memory_order_acquire) == 0) return;
        // Read once, outside the hub mutex: in the plugin both take the lifecycle's own lock.
        const bool suppress = !spec->deliver_while_loading && options_.loading && options_.loading();
        const std::uint32_t epoch = options_.epoch ? options_.epoch() : 0;
        const std::size_t bytes = dump_json(data).size() + kEventFrameOverheadBytes;  // the byte budget's measure
        const auto shared = std::make_shared<const json>(std::move(data));
        std::vector<std::shared_ptr<std::condition_variable>> wake;  // consumers that got an event
        {
            std::lock_guard lk(mx_);
            const std::uint64_t order = ++order_;
            for (SubId id : by_event_[index]) {
                const auto it = subs_.find(id);
                if (it == subs_.end()) continue;
                Sub& s = it->second;
                if (!filter_matches_spec(s.filter, *spec, *shared)) continue;
                if (suppress) {
                    ++s.suppressed;
                    ++suppressed_total_;
                    continue;
                }
                Consumer& c = consumers_.at(s.consumer);
                if (s.queue.size() >= s.max_queue) DropOldestLocked(s, c);  // drop-oldest
                ++s.seq;  // accepted: delivered, or counted in dropped (so the seq gap always equals dropped)
                try {
                    s.queue.push_back({order, s.seq, epoch, bytes, shared});
                } catch (...) {  // allocation failure: this subscription loses this event, counted like a drop
                    ++s.dropped;
                    ++dropped_total_;
                    continue;
                }
                ++c.queued;
                c.bytes += bytes;
                // The consumer's byte budget, so one consumer cannot hold all the memory. The pushed subscription's
                // oldest go first; when it has nothing older, the new event itself goes (its seq is taken, so the gap
                // still equals dropped).
                while (c.bytes > options_.max_queued_bytes_per_consumer && !s.queue.empty()) DropOldestLocked(s, c);
                if (!s.queue.empty() && std::find(wake.begin(), wake.end(), c.cv) == wake.end()) wake.push_back(c.cv);
            }
        }
        for (const auto& cv : wake) cv->notify_all();  // after the hub mutex: a woken Next never waits for it
    } catch (...) {
        // Never throws: a sink calls this on a game thread. An allocation failure loses this one event.
    }
}

std::vector<std::string> EventHub::Next(ConsumerId consumer, std::chrono::milliseconds max_wait, std::size_t max_events)
{
    struct Item
    {
        SubId sub;
        std::size_t event_index;
        std::uint64_t seq;
        std::uint32_t epoch;
        std::uint64_t dropped;
        std::uint64_t suppressed;
        std::shared_ptr<const json> data;
    };
    if (max_events == 0) return {};
    std::shared_ptr<std::mutex> drain;
    {
        std::unique_lock lk(mx_);
        const auto found = consumers_.find(consumer);
        if (found == consumers_.end()) return {};
        const std::shared_ptr<std::condition_variable> cv = found->second.cv;  // outlives an erase by CloseConsumer
        // Clamped: wait_for adds max_wait to steady_clock::now(), which overflows for milliseconds::max().
        cv->wait_for(lk, std::min(max_wait, kMaxNextWait), [&] {
            const auto c = consumers_.find(consumer);
            return c == consumers_.end() || c->second.queued > 0;
        });
        // queued can be 0 after a wake-up with time left: another Next on this consumer took the events first (or the
        // clamp elapsed). Returning empty early is allowed; callers loop on Next.
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end() || c->second.queued == 0) return {};
        drain = c->second.drain;
    }
    std::vector<std::string> frames;
    std::lock_guard dl(*drain);  // held until the frames are returned: Unsubscribe/CloseConsumer wait for them
    std::vector<Item> items;
    {
        std::lock_guard lk(mx_);
        const auto c = consumers_.find(consumer);
        if (c == consumers_.end()) return frames;
        // A min-heap of (push order of the front event, subscription): k events cost O(subs + k log subs).
        using Head = std::pair<std::uint64_t, Sub*>;
        const auto later = [](const Head& a, const Head& b) { return a.first > b.first; };  // orders are unique
        std::priority_queue<Head, std::vector<Head>, decltype(later)> heads(later);
        for (SubId id : c->second.subs) {
            const auto it = subs_.find(id);
            if (it != subs_.end() && !it->second.queue.empty()) heads.emplace(it->second.queue.front().order, &it->second);
        }
        while (items.size() < max_events && !heads.empty()) {
            Sub* oldest = heads.top().second;  // the subscription whose front event was pushed first
            heads.pop();
            Queued q = std::move(oldest->queue.front());
            oldest->queue.pop_front();
            --c->second.queued;
            c->second.bytes -= q.bytes;
            items.push_back({oldest->id, oldest->event_index, q.seq, q.epoch, oldest->dropped, oldest->suppressed, std::move(q.data)});
            oldest->dropped = 0;
            oldest->suppressed = 0;
            if (!oldest->queue.empty()) heads.emplace(oldest->queue.front().order, oldest);
        }
    }
    frames.reserve(items.size());
    for (const auto& it : items) {
        json f = {{"type", "event"}, {"event", std::string(kEvents[it.event_index].name)}, {"sub", it.sub},
                  {"seq", it.seq}, {"epoch", it.epoch}, {"data", *it.data}};
        if (it.dropped > 0) f["dropped"] = it.dropped;
        if (it.suppressed > 0) f["suppressed"] = it.suppressed;
        frames.push_back(dump_json(f));
    }
    return frames;
}

std::size_t EventHub::Consumers() const
{
    std::lock_guard lk(mx_);
    return consumers_.size();
}

std::size_t EventHub::Subscriptions() const
{
    std::lock_guard lk(mx_);
    return subs_.size();
}

std::uint64_t EventHub::DroppedTotal() const
{
    std::lock_guard lk(mx_);
    return dropped_total_;
}

std::uint64_t EventHub::SuppressedTotal() const
{
    std::lock_guard lk(mx_);
    return suppressed_total_;
}
}
