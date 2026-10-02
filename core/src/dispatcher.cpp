#include "spy/dispatcher.h"
#include <algorithm>
#include <atomic>
#include <unordered_map>

namespace spy {
using Clock = std::chrono::steady_clock;

namespace {
struct Entry
{
    std::uint64_t id = 0;
    bool needs_game = true;
    bool batch = false;
    bool dispatched = false;                                           // Backend::Run was (about to be) called
    std::uint32_t timeout_ms = 0;
    std::uint32_t loads_at_submit = 0;                                 // LoadsStarted() before the readiness check
    Clock::time_point deadline;
    bool done = false;
    Clock::time_point finished_at;                                     // when done turned true
    std::string text;                                                  // the response, once done
    json progress = json::object();                                    // batch: the last Progress payload
    std::shared_ptr<std::atomic<bool>> cancelled = std::make_shared<std::atomic<bool>>(false);
};

std::string not_ready_message(const std::string& reason)
{
    if (reason == "starting") return "the game is still starting";
    if (reason == "loading") return "a game is loading";
    if (reason == "no_game") return "no game is loaded";
    if (reason == "paused") return "the game is paused";
    return "the game is not ready";
}

// paused and menus from a BaseState() snapshot, for every not_ready and timeout detail (spec 4.1).
json game_detail(const json& base)
{
    json d = json::object();
    d["paused"] = base.is_object() && base.contains("paused") ? base["paused"] : json(false);
    d["menus"] = base.is_object() && base.contains("menus") ? base["menus"] : json::array();
    return d;
}

std::string error_text(std::uint64_t id, ErrorCode code, std::string_view message, json detail = json::object())
{
    return dump_json(make_error(id, code, message, std::move(detail)));
}

// events.subscribe / events.unsubscribe, answered here by the hub (index Contracts, dispatcher.h changes).
std::string event_op_text(EventHub* hub, const Request& req, ConsumerId consumer)
{
    if (hub == nullptr) return error_text(req.id, ErrorCode::unsupported, "this build has no event hub");
    if (consumer == 0) return error_text(req.id, ErrorCode::bad_request, "no event consumer");
    try {
        if (req.op == ops::kEventsSubscribe) return dump_json(make_ok(req.id, hub->Subscribe(consumer, req.args)));
        hub->Unsubscribe(consumer, req.args.at("sub").get<SubId>());
        return dump_json(make_ok(req.id, nullptr));
    } catch (const ProtocolError& e) {
        return error_text(req.id, e.code, e.what());
    } catch (const std::exception& e) {
        return error_text(req.id, ErrorCode::internal, std::string("event hub error: ") + e.what());
    }
}

// True if the op's generated table entry lists `name` among its arguments; other keys in args are ignored.
bool declares(const OpSpec* spec, std::string_view name)
{
    return spec != nullptr &&
           std::any_of(spec->args.begin(), spec->args.end(), [&](const ArgSpec& a) { return a.name == name; });
}
}

struct Dispatcher::Shared
{
    mutable std::mutex mx;
    std::condition_variable cv;
    std::unordered_map<Ticket, Entry> entries;
    Ticket next = 1;
    std::size_t in_flight = 0;
    std::uint64_t late = 0;
    std::uint64_t abandoned = 0;

    // A finished ticket that never reached the backend (bad request, not_ready, busy).
    Ticket AddDone(std::string text)
    {
        std::lock_guard lk(mx);
        Ticket t = next++;
        Entry e;
        e.done = true;
        e.finished_at = Clock::now();
        e.text = std::move(text);
        entries.emplace(t, std::move(e));
        cv.notify_all();
        return t;
    }

    // Marks a ticket done with `text` unless it is gone or already done. Returns true if it won. Only a backend
    // result that loses (count_late, from the done callback) is a late result; the Dispatcher's own losers (a Tick
    // timeout or reset, Submit's catch) are not.
    bool Finish(Ticket t, std::string text, bool count_late)
    {
        std::lock_guard lk(mx);
        auto it = entries.find(t);
        if (it == entries.end() || it->second.done) {
            if (count_late) ++late;
            return false;
        }
        it->second.done = true;
        it->second.finished_at = Clock::now();
        it->second.text = std::move(text);
        it->second.cancelled->store(true);  // nothing more should run for it
        --in_flight;
        cv.notify_all();
        return true;
    }
};

Dispatcher::Dispatcher(Backend& backend, DispatcherOptions options)
    : backend_(backend), options_(std::move(options)), shared_(std::make_shared<Shared>())
{
}

Dispatcher::~Dispatcher()
{
    {
        std::lock_guard lk(reaper_mx_);
        reaper_stop_ = true;
    }
    reaper_cv_.notify_all();
    if (reaper_.joinable()) reaper_.join();
}

Ticket Dispatcher::Submit(std::string_view request_text, ConsumerId consumer)
{
    Request req;
    try {
        req = parse_request_text(request_text);
    } catch (const ProtocolError& e) {
        return shared_->AddDone(error_text(peek_request_id(request_text).value_or(0), e.code, e.what()));
    } catch (const std::exception& e) {
        return shared_->AddDone(error_text(peek_request_id(request_text).value_or(0), ErrorCode::internal, e.what()));
    } catch (...) {
        return shared_->AddDone(error_text(0, ErrorCode::internal, "request parse failed (unknown exception)"));
    }

    if (req.op == ops::kEventsSubscribe || req.op == ops::kEventsUnsubscribe)
        return shared_->AddDone(event_op_text(options_.events, req, consumer));

    const OpSpec* spec = find_op(req.op);  // parse_request_text accepted it, so it exists
    const bool needs_game = spec == nullptr || spec->needs_game;
    std::uint32_t loads_at_submit = 0;
    try {
        // Read before the readiness check: a world reset that starts after this point ends the call (Tick).
        loads_at_submit = backend_.LoadsStarted();
        if (needs_game) {
            const bool require_unpaused = declares(spec, "require_unpaused") && req.args.contains("require_unpaused") &&
                                          req.args["require_unpaused"].is_boolean() && req.args["require_unpaused"].get<bool>();
            const std::optional<std::string> reason = backend_.NotReadyReason(require_unpaused);
            if (reason) {
                json detail = game_detail(backend_.BaseState());
                detail["reason"] = *reason;
                return shared_->AddDone(error_text(req.id, ErrorCode::not_ready, not_ready_message(*reason), std::move(detail)));
            }
        }
    } catch (const std::exception& e) {
        return shared_->AddDone(error_text(req.id, ErrorCode::internal, std::string("backend error: ") + e.what()));
    } catch (...) {
        return shared_->AddDone(error_text(req.id, ErrorCode::internal, "backend error (unknown exception)"));
    }

    std::uint32_t timeout_ms = options_.default_timeout_ms;
    if (declares(spec, "timeout_ms") && req.args.contains("timeout_ms") && is_uint(req.args["timeout_ms"], kMaxTimeoutMs) &&
        req.args["timeout_ms"].get<std::uint32_t>() >= kMinTimeoutMs)
        timeout_ms = req.args["timeout_ms"].get<std::uint32_t>();

    Ticket ticket;
    std::shared_ptr<std::atomic<bool>> cancelled;
    {
        std::lock_guard lk(shared_->mx);
        if (shared_->in_flight >= options_.max_inflight_total) {
            ticket = shared_->next++;
            Entry e;
            e.done = true;
            e.finished_at = Clock::now();
            e.text = error_text(req.id, ErrorCode::busy,
                                "too many calls in flight (" + std::to_string(options_.max_inflight_total) + ")");
            shared_->entries.emplace(ticket, std::move(e));
            shared_->cv.notify_all();
            return ticket;
        }
        ticket = shared_->next++;
        Entry e;
        e.id = req.id;
        e.needs_game = needs_game;
        e.batch = req.op == ops::kPapyrusBatch;
        e.timeout_ms = timeout_ms;
        e.loads_at_submit = loads_at_submit;
        e.deadline = Clock::now() + std::chrono::milliseconds(timeout_ms);
        cancelled = e.cancelled;
        shared_->entries.emplace(ticket, std::move(e));
        ++shared_->in_flight;
    }

    // Everything below runs without the lock: the backend may call done (or progress) synchronously.
    std::weak_ptr<Shared> weak = shared_;
    const std::uint64_t id = req.id;
    Done done = [weak, ticket, id, on_late = options_.on_late](OpResult r) {
        auto s = weak.lock();
        if (!s) return;  // the Dispatcher is gone
        std::string text;
        try {
            text = dump_json(r.ok ? make_ok(id, std::move(r.result)) : make_error(id, r.code, r.message, std::move(r.detail)));
        } catch (const std::exception& e) {
            text = error_text(id, ErrorCode::internal, std::string("could not encode the result: ") + e.what());
        }
        if (!s->Finish(ticket, std::move(text), true) && on_late) {
            try {
                on_late("late result dropped: ticket " + std::to_string(ticket) + ", request " + std::to_string(id));
            } catch (...) {
                // a logger must never break the backend's thread
            }
        }
    };
    // True at once when a world reset starts, not only at the next Tick. Holding backend_ by reference is safe: the
    // game backend is immortal, and every test declares its backend before the Dispatcher that uses it.
    Cancelled is_cancelled = [cancelled, &backend = backend_, needs_game, loads_at_submit] {
        return cancelled->load() || (needs_game && backend.LoadsStarted() != loads_at_submit);
    };
    Progress progress = [weak, ticket](json p) {
        auto s = weak.lock();
        if (!s) return;
        std::lock_guard lk(s->mx);
        auto it = s->entries.find(ticket);
        if (it != s->entries.end() && !it->second.done) it->second.progress = std::move(p);
    };
    {
        std::lock_guard lk(shared_->mx);
        auto it = shared_->entries.find(ticket);
        if (it == shared_->entries.end() || it->second.done) return ticket;  // a Tick already ended it: nothing to run
        it->second.dispatched = true;
    }
    try {
        backend_.Run(req.op, req.args, done, is_cancelled, progress);
    } catch (const std::exception& e) {
        shared_->Finish(ticket, error_text(id, ErrorCode::internal, std::string("backend error: ") + e.what()), false);
    } catch (...) {
        shared_->Finish(ticket, error_text(id, ErrorCode::internal, "backend error (unknown exception)"), false);
    }
    return ticket;
}

std::optional<std::string> Dispatcher::Wait(Ticket ticket, std::chrono::milliseconds max_wait)
{
    std::unique_lock lk(shared_->mx);
    auto ready = [&] {
        auto it = shared_->entries.find(ticket);
        return it == shared_->entries.end() || it->second.done;
    };
    shared_->cv.wait_for(lk, max_wait, ready);
    auto it = shared_->entries.find(ticket);
    if (it == shared_->entries.end() || !it->second.done) return std::nullopt;
    std::string text = std::move(it->second.text);
    shared_->entries.erase(it);
    return text;
}

std::vector<std::pair<Ticket, std::string>> Dispatcher::WaitAny(const std::vector<Ticket>& tickets,
                                                               std::chrono::milliseconds max_wait)
{
    std::vector<std::pair<Ticket, std::string>> out;
    std::unique_lock lk(shared_->mx);
    auto ready = [&] {
        bool any_known = false;
        for (Ticket t : tickets) {
            auto it = shared_->entries.find(t);
            if (it == shared_->entries.end()) continue;
            if (it->second.done) return true;
            any_known = true;
        }
        return !any_known;  // nothing left to wait for
    };
    shared_->cv.wait_for(lk, max_wait, ready);
    for (Ticket t : tickets) {
        auto it = shared_->entries.find(t);
        if (it == shared_->entries.end() || !it->second.done) continue;
        out.emplace_back(t, std::move(it->second.text));
        shared_->entries.erase(it);
    }
    return out;
}

void Dispatcher::Cancel(Ticket ticket)
{
    (void)CancelOrTake(ticket);
}

std::optional<std::string> Dispatcher::CancelOrTake(Ticket ticket)
{
    std::lock_guard lk(shared_->mx);
    auto it = shared_->entries.find(ticket);
    if (it == shared_->entries.end()) return std::nullopt;
    std::optional<std::string> text;
    if (it->second.done) {
        text = std::move(it->second.text);
    } else {
        it->second.cancelled->store(true);
        --shared_->in_flight;
    }
    shared_->entries.erase(it);
    shared_->cv.notify_all();
    return text;
}

void Dispatcher::Tick()
{
    std::lock_guard tick_lk(tick_mx_);
    struct Expired
    {
        Ticket ticket;
        std::uint64_t id;
        bool reset;  // a world reset ended it (else its deadline)
        bool dispatched;
        bool batch;
        std::uint32_t timeout_ms;
        json progress;
    };
    std::vector<Expired> expired;
    {
        std::lock_guard lk(shared_->mx);
        // Read under the lock: every entry in the table read its loads_at_submit before this, so a differing value
        // can only mean a reset started after that entry's Submit.
        const std::uint32_t loads = backend_.LoadsStarted();
        const auto now = Clock::now();
        const auto abandon_after = std::chrono::milliseconds(options_.abandon_after_ms);
        for (auto it = shared_->entries.begin(); it != shared_->entries.end();) {
            Entry& e = it->second;
            if (e.done) {
                if (now - e.finished_at >= abandon_after) {  // finished, and nobody came for it
                    it = shared_->entries.erase(it);
                    ++shared_->abandoned;
                    continue;
                }
            } else if (e.needs_game && e.loads_at_submit != loads) {
                expired.push_back({it->first, e.id, true, e.dispatched, e.batch, e.timeout_ms, json()});
            } else if (now >= e.deadline) {
                expired.push_back({it->first, e.id, false, e.dispatched, e.batch, e.timeout_ms, e.progress});
            }
            ++it;
        }
    }
    if (expired.empty()) return;

    json base;
    try {
        base = backend_.BaseState();
    } catch (...) {
        base = json::object();
    }
    std::string reset_reason = "loading";  // asked once, and only if a world reset ended something
    if (std::any_of(expired.begin(), expired.end(), [](const Expired& x) { return x.reset; })) {
        try {
            reset_reason = backend_.NotReadyReason(false).value_or("loading");
        } catch (...) {
            // keep "loading"
        }
    }
    for (auto& x : expired) {
        json detail = game_detail(base);
        std::string text;
        if (x.reset) {
            detail["reason"] = reset_reason;
            if (x.dispatched) detail["dispatched"] = true;  // it reached Backend::Run: its effect may have happened
            text = error_text(x.id, ErrorCode::not_ready, not_ready_message(reset_reason), std::move(detail));
        } else {
            if (x.batch) {
                const bool has = x.progress.is_object() && x.progress.contains("completed");
                detail["completed"] = has ? x.progress["completed"] : json(0);
                detail["results"] = has && x.progress.contains("results") ? x.progress["results"] : json::array();
            }
            text = error_text(x.id, ErrorCode::timeout, "no result within " + std::to_string(x.timeout_ms) + " ms", std::move(detail));
        }
        shared_->Finish(x.ticket, std::move(text), false);
    }
}

void Dispatcher::StartReaper(std::chrono::milliseconds period)
{
    std::lock_guard lk(reaper_mx_);
    if (reaper_.joinable() || reaper_stop_) return;
    reaper_ = std::thread([this, period] {
        std::unique_lock rl(reaper_mx_);
        while (!reaper_stop_) {
            reaper_cv_.wait_for(rl, period, [this] { return reaper_stop_; });
            if (reaper_stop_) break;
            rl.unlock();
            try {
                Tick();
            } catch (...) {
                // Never let an exception end the process from this thread (Global Constraints).
            }
            rl.lock();
        }
    });
}

json Dispatcher::BaseState()
{
    return backend_.BaseState();
}

std::size_t Dispatcher::InFlight() const
{
    std::lock_guard lk(shared_->mx);
    return shared_->in_flight;
}

std::uint64_t Dispatcher::LateResults() const
{
    std::lock_guard lk(shared_->mx);
    return shared_->late;
}

std::uint64_t Dispatcher::Abandoned() const
{
    std::lock_guard lk(shared_->mx);
    return shared_->abandoned;
}
}
