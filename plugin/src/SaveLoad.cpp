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
// does not use the save manager directly (R63): a synchronous Save_Impl from an SKSE task deadlocked the game.
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
    std::string name;  // as requested (the result echoes it)
    std::string key;   // Fold(name)
    Done done;
    Cancelled cancelled;
};
std::mutex& Mx() { static auto* m = new std::mutex; return *m; }  // immortal, like the dispatcher
std::vector<Pending>& Saves() { static auto* v = new std::vector<Pending>; return *v; }
// Names whose kSaveGame arrived: the engine started writing them, but the .ess may not be on disk yet (kSaveGame
// can precede the file). Key -> when kSaveGame arrived. Dropped lazily once the file exists or after kAnnouncedMaxAge.
std::map<std::string, Clock::time_point>& Announced() { static auto* m = new std::map<std::string, Clock::time_point>; return *m; }
constexpr auto kAnnouncedMaxAge = std::chrono::seconds(120);
std::atomic_bool g_loadQueued{ false };  // a game.load's UI task is queued or running

fs::path EssPath(const std::string& name) { return SaveDir() / (name + ".ess"); }

bool OnDisk(const std::string& name)
{
    std::error_code ec;  // never throws: an unreadable folder counts as "not there"
    return fs::exists(EssPath(name), ec);
}

// Under Mx(): forget announced names whose file is now on disk or that are older than kAnnouncedMaxAge.
void PruneAnnouncedLocked()
{
    const auto now = Clock::now();
    std::erase_if(Announced(), [&](const auto& kv) { return now - kv.second > kAnnouncedMaxAge || OnDisk(kv.first); });
}

// Erases the pending save of that key. True only when this call erased it, so `done` runs exactly once: kSaveGame
// may complete the request (OnSaveGame) before the Game.SaveGame dispatch returns, throws or calls back.
bool Forget(const std::string& key)
{
    std::lock_guard lk(Mx());
    return std::erase_if(Saves(), [&](const Pending& p) { return p.key == key; }) > 0;
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
    if (OnDisk(name))
        return done(OpResult::Err(ErrorCode::bad_request, "a save named " + name + " already exists",
                                  json{ { "reason", "save_exists" } }));
    const std::string key = Fold(name);
    bool refused = false;
    {
        std::lock_guard lk(Mx());
        std::erase_if(Saves(), [](const Pending& p) { return p.cancelled(); });  // drop requests nobody waits for
        PruneAnnouncedLocked();
        // Pending (no kSaveGame yet) or announced (kSaveGame came, file not written yet): either way a second save of
        // that name would overwrite the first, so it is refused.
        refused = Announced().contains(key) || std::ranges::any_of(Saves(), [&](const Pending& p) { return p.key == key; });
        if (!refused) Saves().push_back({ name, key, done, cancelled });  // before the engine call: kSaveGame may arrive inside it
    }
    if (refused) return done(InProgress(name));  // after the lock is released: done may complete the request at once
    // R63: the save is requested through SKSE's Papyrus native Game.SaveGame(name), dispatched like papyrus.call_static
    // from one AddTask. A synchronous BGSSaveLoadManager Save_Impl from an SKSE task deadlocks against the main
    // thread's Papyrus VM lock (Task 41, first live run). The VM call returns once the save is requested; the request
    // completes on kSaveGame (OnSaveGame), as before.
    SKSE::GetTaskInterface()->AddTask([name, key, done, cancelled] {
        ops::InTask in;
        ops::Measured measured;
        if (cancelled()) { Forget(key); return; }
        // Called by the VM callback once Game.SaveGame returned (or with vm_error if the VM refused the dispatch).
        // Success answers nothing: kSaveGame completes the request. `done` is never called under Mx().
        Done onCall = [name, key, done](OpResult r) {
            if (r.ok) { log::Info("saveload: save requested " + name); return; }
            if (Forget(key)) done(std::move(r));
            else log::Error("saveload: save " + name + " failed after kSaveGame had already completed it");
        };
        std::optional<OpResult> failure;
        try {
            if (auto why = life::NotReadyReason(false)) failure = ops::NotReady(*why);  // recheck inside the task
            else ops::DispatchStatic("Game", "SaveGame", { Value{ name } }, onCall);
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
        if (Forget(key)) done(std::move(*failure));  // still pending: answer the error
        else log::Error("saveload: save " + name + " failed after kSaveGame had already completed it");
    });
}

void Load(const std::string& name, Done done, Cancelled cancelled)
{
    // needs_game is false for game.load (Contracts), so this is the only readiness check: a main menu is fine.
    if (auto why = life::NotReadyReason(false); why && (*why == "starting" || *why == "loading")) return done(ops::NotReady(*why));
    if (SaveDir().empty()) return done(NoSaveFolder());
    const std::string key = Fold(name);
    const bool onDisk = OnDisk(name);
    std::optional<OpResult> refusal;
    {
        std::lock_guard lk(Mx());
        std::erase_if(Saves(), [](const Pending& p) { return p.cancelled(); });
        PruneAnnouncedLocked();
        if (!Saves().empty())
            refusal = OpResult::Err(ErrorCode::busy, "a game.save is in progress; load after it completes");
        else if (!onDisk && Announced().contains(key))
            refusal = OpResult::Err(ErrorCode::not_ready, "save " + name + " is still being written",
                                    ops::WithGameState({ { "reason", "saving" } }));
        else if (!onDisk)
            refusal = OpResult::Err(ErrorCode::not_found, "no save named " + name);
        else if (g_loadQueued.exchange(true))
            refusal = OpResult::Err(ErrorCode::busy, "another game.load is already queued");
    }
    if (refusal) return done(std::move(*refusal));  // outside the lock
    SKSE::GetTaskInterface()->AddUITask([name, done, cancelled] {
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
    });
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
        Announced()[key] = Clock::now();  // the engine started this save; its .ess may not be on disk yet
        for (auto it = Saves().begin(); it != Saves().end();) {
            if (it->key == key) { hit.push_back(std::move(*it)); it = Saves().erase(it); }
            else ++it;
        }
    }
    for (auto& p : hit) {  // outside the lock
        if (p.cancelled()) continue;
        log::Info("saveload: saved " + p.name);  // the engine started the save (Contracts); the file may follow
        p.done(OpResult::Ok(json{ { "name", p.name } }));
    }
}
}
