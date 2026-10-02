#include "SaveLoad.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Settings.h"
#include "spy/errors.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <map>
#include <mutex>
#include <optional>
#include <system_error>
#include <vector>

namespace spy::saveload {
namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;
namespace {
// Legal access to the protected bool-returning Load_Impl (a pointer to member named through a derived class). Saving
// does not use the save manager directly: a synchronous Save_Impl from an SKSE task deadlocked the game.
struct Access : RE::BGSSaveLoadManager
{
    using RE::BGSSaveLoadManager::Load_Impl;
};

bool EngineLoad(RE::BGSSaveLoadManager* m, const std::string& name)
{
    auto fn = &Access::Load_Impl;
    return (m->*fn)(name.c_str(), -1, 0, true);  // the arguments of CommonLib's Load(const char*) (S6 used that one)
}

std::string U8(const fs::path& p)
{
    const auto s = p.u8string();
    return std::string(reinterpret_cast<const char*>(s.data()), s.size());
}

// Save names are compared case-insensitively: Windows file names are, and the engine may hand a name back in
// another case. Names Skython accepts are [A-Za-z0-9_]; other saves (the player's) only need a stable key.
std::string Fold(std::string_view s)
{
    std::string r(s);
    std::ranges::transform(r, r.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return r;
}

struct Pending
{
    std::uint64_t id;  // this request: a retry of the same name gets a new id, so a stale task never forgets it
    std::string name;  // as requested (the result echoes it)
    std::string key;   // Fold(name)
    Done done;
    Cancelled cancelled;
    bool dispatched = false;  // set under Mx() right before Game.SaveGame is dispatched
    Clock::time_point dispatchedAt{};  // when it was set: a cancel after dispatch reserves the name from here
};
std::mutex& Mx() { static auto* m = new std::mutex; return *m; }  // immortal, like the dispatcher
std::vector<Pending>& Saves() { static auto* v = new std::vector<Pending>; return *v; }
std::uint64_t g_nextId = 0;  // under Mx()
// Reserved names, key -> Reserved. Two kinds, both refuse a second game.save of the name:
// - announced: kSaveGame arrived, the engine started writing the save, but the .ess may not be on disk yet (kSaveGame
//   can precede the file); a game.load of the name answers not_ready "saving".
// - not announced: a pending save cancelled (timeout, disconnect, world reset) after its Game.SaveGame was dispatched;
//   the engine may still write it, so the name stays reserved and every game.load is busy until kSaveGame arrives.
// Dropped lazily once the file exists or kAnnouncedMaxAge after `since` (kSaveGame, or the dispatch for a cancelled
// save: the engine's write is timed from the request, not from the cancel).
struct Reserved
{
    Clock::time_point since;
    bool announced;
};
std::map<std::string, Reserved>& Announced() { static auto* m = new std::map<std::string, Reserved>; return *m; }
constexpr auto kAnnouncedMaxAge = std::chrono::seconds(120);
std::atomic_bool g_loadQueued{ false };  // a game.load's UI task is queued or running

// A loadable save is its .ess; the .skse cosave alone (an orphan) cannot be loaded.
bool EssOnDisk(const std::string& name)
{
    std::error_code ec;  // never throws: an unreadable folder counts as "not there"
    return fs::exists(SaveDir() / (name + ".ess"), ec);
}

// A save is there when its .ess is, or its .skse cosave alone (an orphan): game.save never writes over either.
bool OnDisk(const std::string& name)
{
    std::error_code ec;  // never throws: an unreadable folder counts as "not there"
    const fs::path dir = SaveDir();
    if (fs::exists(dir / (name + ".ess"), ec)) return true;
    return fs::exists(dir / (name + ".skse"), ec);
}

// Under Mx(): forget reserved names whose .ess is now on disk or that are older than kAnnouncedMaxAge. The .ess, not
// the .skse: a cosave written first must not end the reservation while game.load would still find no .ess (game.save
// refuses the name either way, through OnDisk). This does file system I/O (one exists() per entry) under the lock;
// acceptable while the set holds a handful of names.
void PruneAnnouncedLocked()
{
    const auto now = Clock::now();
    std::erase_if(Announced(), [&](const auto& kv) { return now - kv.second.since > kAnnouncedMaxAge || EssOnDisk(kv.first); });
}

// Under Mx(): drop pending saves nobody waits for any more. One cancelled before its dispatch is simply dropped (its
// task sees cancelled() or a missing entry and dispatches nothing). One cancelled after its Game.SaveGame was
// dispatched loses only its callbacks: its name moves to Announced() (not announced yet) and stays reserved.
void DropCancelledLocked()
{
    for (auto it = Saves().begin(); it != Saves().end();) {
        if (!it->cancelled()) { ++it; continue; }
        if (it->dispatched) {
            Announced().try_emplace(it->key, Reserved{ it->dispatchedAt, false });
            log::Info("saveload: save " + it->name + " cancelled after dispatch; name stays reserved");
        }
        it = Saves().erase(it);
    }
}

// Erases the pending save with that id. True only when this call erased it, so `done` runs exactly once: kSaveGame
// may complete the request (OnSaveGame) before the Game.SaveGame dispatch returns, throws or calls back.
bool Forget(std::uint64_t id)
{
    std::lock_guard lk(Mx());
    return std::erase_if(Saves(), [&](const Pending& p) { return p.id == id; }) > 0;
}

// Marks the pending save with that id as dispatched. False when it is gone (a cancel was swept, or kSaveGame came):
// then nothing may be dispatched for it, since a retry of the same name may already own the name.
bool MarkDispatched(std::uint64_t id)
{
    std::lock_guard lk(Mx());
    const auto it = std::ranges::find_if(Saves(), [&](const Pending& p) { return p.id == id; });
    if (it == Saves().end()) return false;
    it->dispatched = true;
    it->dispatchedAt = Clock::now();
    return true;
}

OpResult Internal(std::string message) { return OpResult::Err(ErrorCode::internal, std::move(message)); }
OpResult NoSaveFolder() { return Internal("save folder unknown (no SKSE log folder at kDataLoaded; see the plugin log)"); }
OpResult InProgress(const std::string& name)
{
    return OpResult::Err(ErrorCode::bad_request, "a save named " + name + " is already in progress", json{ { "reason", "save_exists" } });
}

void Save(const std::string& name, Done done, Cancelled cancelled)
{
    if (SaveDir().empty()) return done(NoSaveFolder());
    const std::string key = Fold(name);
    bool exists = false;
    bool refused = false;
    std::uint64_t id = 0;
    {
        std::lock_guard lk(Mx());
        DropCancelledLocked();
        PruneAnnouncedLocked();
        // The disk is checked here, after the prune: a reserved name the prune just dropped because its file appeared
        // must answer "already exists", never pass as free.
        exists = OnDisk(name);
        // Pending (no kSaveGame yet) or reserved (kSaveGame came, or a dispatched save was cancelled; file not written
        // yet): either way a second save of that name could overwrite the first, so it is refused.
        if (!exists)
            refused = Announced().contains(key) || std::ranges::any_of(Saves(), [&](const Pending& p) { return p.key == key; });
        if (!exists && !refused) {
            id = ++g_nextId;
            Saves().push_back({ id, name, key, done, cancelled });  // before the engine call: kSaveGame may arrive inside it
        }
    }
    // Answers after the lock is released: done may complete the request at once.
    if (exists)
        return done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " already exists",
                                  json{ { "reason", "save_exists" } }));
    if (refused) return done(InProgress(name));
    // The save is requested through SKSE's Papyrus native Game.SaveGame(name), dispatched like papyrus.call_static
    // from one AddTask. A synchronous BGSSaveLoadManager Save_Impl from an SKSE task deadlocks against the main
    // thread's Papyrus VM lock (seen in game). The VM call returns once the save is requested; the request
    // completes on kSaveGame (OnSaveGame).
    // A cancel before the dispatch drops the request; after it, the name stays reserved (DropCancelledLocked).
    auto task = [name, id, done, cancelled] {
        ops::InTask in;
        ops::Measured measured;
        if (cancelled()) { Forget(id); return; }
        // Called by the VM callback once Game.SaveGame returned (or with vm_error if the VM refused the dispatch).
        // Success answers nothing: kSaveGame completes the request. `done` is never called under Mx().
        Done onCall = [name, id, done](OpResult r) {
            if (r.ok) { log::Info("saveload: save requested " + name); return; }
            if (Forget(id)) done(std::move(r));
            else log::Error("saveload: save " + name + " failed after kSaveGame or a cancel had already ended it");
        };
        std::optional<OpResult> failure;
        try {
            if (auto why = life::NotReadyReason(false)) failure = ops::NotReady(*why);  // recheck inside the task
            else if (!MarkDispatched(id)) return;  // cancelled and dropped meanwhile: nothing is requested
            else {
                // Marked first: if the request is cancelled from here on, the name stays reserved even when the
                // dispatch below then fails (harmless: the reservation ends after kAnnouncedMaxAge).
                ops::DispatchStatic("Game", "SaveGame", { Value{ name } }, onCall);
                log::Info("saveload: save dispatched " + name);
            }
        } catch (const ProtocolError& e) {
            if (e.code == ErrorCode::unknown_function)
                failure = OpResult::Err(ErrorCode::unsupported, std::string("game.save needs SKSE's Papyrus native Game.SaveGame, "
                                        "which is not loaded (are SKSE's scripts installed?): ") + e.what());
            else failure = OpResult::Err(e.code, std::string("game.save: ") + e.what());
        } catch (const std::exception& e) {
            failure = Internal(std::string("requesting the save threw: ") + e.what());
        } catch (...) {
            failure = Internal("requesting the save threw");
        }
        if (!failure) return;
        if (Forget(id)) done(std::move(*failure));  // still pending: answer the error
        else log::Error("saveload: save " + name + " failed after kSaveGame or a cancel had already ended it");
    };
    try {
        SKSE::GetTaskInterface()->AddTask(std::move(task));
    } catch (const std::exception& e) {
        if (Forget(id)) done(Internal(std::string("queueing the save task threw: ") + e.what()));
    } catch (...) {
        if (Forget(id)) done(Internal("queueing the save task threw"));
    }
}

void Load(const std::string& name, Done done, Cancelled cancelled)
{
    // needs_game is false for game.load, so this is the only readiness check: a main menu is fine.
    if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
    if (SaveDir().empty()) return done(NoSaveFolder());
    const std::string key = Fold(name);
    std::optional<OpResult> refusal;
    {
        std::lock_guard lk(Mx());
        DropCancelledLocked();
        PruneAnnouncedLocked();
        // After the prune, like Save: the prune drops a name once its file is there. Only the .ess counts here: an
        // orphan .skse is not a save the game can load (not_found), while game.save still refuses its name.
        const bool onDisk = EssOnDisk(name);
        // A pending save, or a dispatched save that was cancelled before its kSaveGame, may still be written: busy.
        if (!Saves().empty() || std::ranges::any_of(Announced(), [](const auto& kv) { return !kv.second.announced; }))
            refusal = OpResult::Err(ErrorCode::busy, "a game.save is in progress; load after it completes");
        else if (!onDisk && Announced().contains(key))  // announced (the busy check above took the others)
            refusal = OpResult::Err(ErrorCode::not_ready, "save " + name + " is still being written",
                                    ops::WithGameState({ { "reason", "saving" } }));
        else if (!onDisk)
            refusal = OpResult::Err(ErrorCode::not_found, "no save named " + name);
        else if (g_loadQueued.exchange(true))
            refusal = OpResult::Err(ErrorCode::busy, "another game.load is already queued");
    }
    if (refusal) return done(std::move(*refusal));  // outside the lock
    auto task = [name, done, cancelled] {
        ops::InTask in;
        struct ClearQueued { ~ClearQueued() { g_loadQueued.store(false); } } clearQueued;  // whatever happens below
        if (cancelled()) return;  // the caller gave up before the UI thread got here: do not start a load nobody waits for
        try {
            if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
            auto* mgr = RE::BGSSaveLoadManager::GetSingleton();
            if (!mgr) return done(Internal("the game's save manager is not available"));
            if (!EngineLoad(mgr, name)) return done(Internal("the game's load call returned false for save " + name));
            log::Info("saveload: load started " + name);
            done(OpResult::Ok(json{ { "save", name }, { "started", true } }));
        } catch (const std::exception& e) {
            done(Internal(std::string("loading threw: ") + e.what()));
        } catch (...) {
            done(Internal("loading threw"));
        }
    };
    try {
        SKSE::GetTaskInterface()->AddUITask(std::move(task));
    } catch (const std::exception& e) {
        g_loadQueued.store(false);
        done(Internal(std::string("queueing the load task threw: ") + e.what()));
    } catch (...) {
        g_loadQueued.store(false);
        done(Internal("queueing the load task threw"));
    }
}

// The engine may pass a bare name, a name with ".ess", or a path: compare on the bare stem.
std::string Stem(std::string_view raw)
{
    std::string s(raw);
    if (const auto slash = s.find_last_of("\\/"); slash != std::string::npos) s.erase(0, slash + 1);
    if (s.size() > 4 && _stricmp(s.c_str() + s.size() - 4, ".ess") == 0) s.resize(s.size() - 4);
    return s;
}
}

fs::path SaveDir()
{
    static std::once_flag once;
    static fs::path* dir = new fs::path;  // stays empty when the folder is unknown: both ops then answer internal
    std::call_once(once, [] {
        const auto& rt = settings::GetRuntime();
        if (!rt.skse_dir) {
            log::Error("saveload: save folder unknown (no SKSE log folder); game.save and game.load will answer internal");
            return;
        }
        std::string rel = "Saves\\";
        if (auto* ini = RE::INISettingCollection::GetSingleton())
            if (auto* s = ini->GetSetting("sLocalSavePath:General"); s && s->GetType() == RE::Setting::Type::kString && s->GetString())
                rel = s->GetString();
        *dir = rt.skse_dir->parent_path() / fs::path(rel);
        if (!dir->has_filename()) *dir = dir->parent_path();  // "Saves\" -> "...\Saves": the logged folder ends in its name
        log::Info("saveload: save folder " + U8(*dir));
    });
    return *dir;
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled)
{
    try {
        if (op == "game.save") return Save(args.at("name").get<std::string>(), std::move(done), std::move(cancelled));
        if (op == "game.load") return Load(args.at("save").get<std::string>(), std::move(done), std::move(cancelled));
        done(Internal("saveload: unexpected op " + op));
    } catch (const std::exception& e) {
        done(Internal(std::string("saveload: ") + e.what()));
    }
}

void OnSaveGame(std::string_view raw)
{
    const std::string name = Stem(raw);
    const std::string key = Fold(name);
    std::vector<Pending> hit;
    {
        std::lock_guard lk(Mx());
        Announced()[key] = Reserved{ Clock::now(), true };  // the engine started this save; its .ess may not be on disk yet
        for (auto it = Saves().begin(); it != Saves().end();) {
            if (it->key == key) { hit.push_back(std::move(*it)); it = Saves().erase(it); }
            else ++it;
        }
    }
    for (auto& p : hit) {  // outside the lock
        if (p.cancelled()) continue;
        log::Info("saveload: saved " + p.name);  // the engine started the save; the file may follow
        p.done(OpResult::Ok(json{ { "name", p.name } }));
    }
}
}
