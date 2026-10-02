#pragma once
#include "spy/errors.h"  // json
#include <atomic>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace spy {
// Pure lifecycle logic, driven by plain events. The plugin's Lifecycle.cpp translates SKSE
// messages, menu events and the paused flag into these calls; nothing here touches the game.
//
//   interactive = data_ready && game_loaded && !loading
//   loading     = a save load is pending (kPreLoadGame seen, kPostLoadGame not yet) || the Loading Menu is open
//   paused      = whatever the plugin last reported with set_paused (a paused game is still interactive)
//
// A world starts (epoch + 1, game_loaded) on the first of: a successful kPostLoadGame, kNewGame, or the world-start
// gate. SKSE sends kNewGame only from its hook on the CharGen quest's NewGame, so a console
// `coc` from the main menu raises neither message. The gate covers that route: it opens once the Main Menu has
// opened, a Loading Menu has then opened and closed (in either order with the Main Menu's close), and no menu, save
// load or game is still in the way; the plugin then checks that the player is in the world (a parent cell and 3D)
// and calls on_player_in_world().
//
// Thread-safe: one mutex guards every field (loads_started_ is also atomic so its read takes no lock). Events
// arrive on the main and UI threads; queries come from the dispatcher (any Python thread) and its reaper thread.
// Not copyable or movable (it holds a mutex).
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
    // Returns false (and changes nothing) when the gate already started this world: one world, one epoch.
    bool on_new_game();
    void on_menu(const std::string& name, bool opening);
    void set_paused(bool paused);
    // True while the world-start gate is open: data ready, the Main Menu opened and is closed again, a Loading Menu
    // opened and closed since it opened, no Loading Menu open, no save load pending, no game loaded, and no kNewGame
    // since the Main Menu opened.
    bool world_start_pending() const;
    // The plugin found the player in a cell. Starts the world (epoch + 1, game_loaded) if the gate is still open;
    // true if it did. At most one call per world returns true.
    bool on_player_in_world();

    bool data_ready() const;
    bool game_loaded() const;
    bool loading() const;
    bool interactive() const;
    bool paused() const;
    std::uint32_t epoch() const;
    // Counts world resets: save loads and quits to the main menu. Every kPreLoadGame (successful or not) and every
    // Main Menu open while a game was loaded; never new games. Both resets throw away the running Papyrus stacks, so
    // the dispatcher compares it between ticks and fails the calls that were in flight. (The name predates
    // the main-menu case; it is kept because the dispatcher, the module and the plugin use it.)
    // Lock-free (an atomic read): the dispatcher's Tick calls it while holding its own lock.
    std::uint32_t loads_started() const;
    // nullopt when a call may run; otherwise "starting", "loading" or "no_game", checked in that order (so a save
    // that is loading reports "loading" even though no game is loaded yet), then "paused" only when
    // require_unpaused is set (a paused game is not "not ready" for ordinary calls).
    std::optional<std::string> not_ready_reason(bool require_unpaused = false) const;
    std::vector<std::string> open_menus() const;  // in the order they opened (the last one is on top)
    // {data_ready, game_loaded, loading, interactive, paused, menus, epoch}: game.state's base and the detail of
    // every not_ready error.
    json base_state() const;

private:
    bool loading_locked() const { return load_pending_ || loading_menu_open_; }
    bool interactive_locked() const { return data_ready_ && game_loaded_ && !loading_locked(); }
    bool world_gate_locked() const
    {
        return data_ready_ && gate_armed_ && loading_cycle_since_arm_ && !main_menu_open_ && !loading_menu_open_ &&
               !load_pending_ && !game_loaded_;
    }

    const std::string loading_menu_, main_menu_;
    mutable std::mutex mx_;
    bool data_ready_ = false, game_loaded_ = false, load_pending_ = false, loading_menu_open_ = false, paused_ = false;
    // World-start gate. Armed when the Main Menu opens; disarmed by kPreLoadGame, kNewGame or a gate start.
    // loading_opened_since_arm_: a Loading Menu opened after the Main Menu did; loading_cycle_since_arm_: and closed.
    bool main_menu_open_ = false, gate_armed_ = false, loading_opened_since_arm_ = false, loading_cycle_since_arm_ = false;
    bool gate_started_world_ = false;  // this world was started by the gate (a late kNewGame then counts nothing)
    std::uint32_t epoch_ = 0;
    std::atomic<std::uint32_t> loads_started_{0};  // written under mx_; read without it
    std::vector<std::string> menus_;  // open order; a menu reported open again moves to the end
};
}
