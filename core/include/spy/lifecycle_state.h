#pragma once
#include "spy/errors.h"  // json
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

namespace spy {
// Pure lifecycle logic (spec 4.1), driven by plain events. The plugin's Lifecycle.cpp (Task 25) translates SKSE
// messages, menu events and the paused flag into these calls; nothing here touches the game.
//
//   interactive = data_ready && game_loaded && !loading
//   loading     = a save load is pending (kPreLoadGame seen, kPostLoadGame not yet) || the Loading Menu is open
//   paused      = whatever the plugin last reported with set_paused (a paused game is still interactive)
//
// Thread-safe: one mutex guards every field. Events arrive on the main and UI threads; queries come from the
// dispatcher (any Python thread) and its reaper thread. Not copyable or movable (it holds a mutex).
class LifecycleState
{
public:
    explicit LifecycleState(std::string loading_menu = "Loading Menu", std::string main_menu = "Main Menu")
        : loading_menu_(std::move(loading_menu)), main_menu_(std::move(main_menu)) {}
    LifecycleState(const LifecycleState&) = delete;
    LifecycleState& operator=(const LifecycleState&) = delete;

    void on_data_loaded();
    void on_pre_load_game();
    // success = the packed data value of kPostLoadGame. A failed load still ends the pending load.
    void on_post_load_game(bool success);
    void on_new_game();
    void on_menu(const std::string& name, bool opening);
    void set_paused(bool paused);

    bool data_ready() const;
    bool game_loaded() const;
    bool loading() const;
    bool interactive() const;
    bool paused() const;
    std::uint32_t epoch() const;
    // Counts world resets: save loads and quits to the main menu. Every kPreLoadGame (successful or not) and every
    // Main Menu open while a game was loaded; never new games. Both resets throw away the running Papyrus stacks, so
    // the dispatcher (Task 18) compares it between ticks and fails the calls that were in flight. (The name predates
    // the main-menu case; it is kept because Tasks 18, 19, 25 and 27 use it.)
    std::uint32_t loads_started() const;
    // nullopt when a call may run; otherwise "starting", "loading" or "no_game", checked in that order (so a save
    // that is loading reports "loading" even though no game is loaded yet), then "paused" only when
    // require_unpaused is set (spec 4.1: a paused game is not "not ready" for ordinary calls).
    std::optional<std::string> not_ready_reason(bool require_unpaused = false) const;
    std::vector<std::string> open_menus() const;  // in the order they opened (the last one is on top)
    // {data_ready, game_loaded, loading, interactive, paused, menus, epoch}: game.state's base and the detail of
    // every not_ready error.
    json base_state() const;

private:
    bool loading_locked() const { return load_pending_ || loading_menu_open_; }
    bool interactive_locked() const { return data_ready_ && game_loaded_ && !loading_locked(); }

    const std::string loading_menu_, main_menu_;
    mutable std::mutex mx_;
    bool data_ready_ = false, game_loaded_ = false, load_pending_ = false, loading_menu_open_ = false, paused_ = false;
    std::uint32_t epoch_ = 0, loads_started_ = 0;
    std::vector<std::string> menus_;  // open order; a menu reported open again moves to the end
};
}
