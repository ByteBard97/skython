# Task 17: Lifecycle state

Adapted from: branch `plan-out-of-process`, task 15 (`task-15-plugin-skeleton-build-logging-settings-lifecycle.md`), Step 1 only (the Linux-testable lifecycle logic). The plugin side of that task (`Lifecycle.cpp`, SKSE messages, menu events) is Task 25.

> Part of the [Skython v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, sections 4.1 and 5). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 14 (`spy/errors.h` for `spy::json`, which `base_state()` returns; Task 14 builds on 13). The test counts in Steps 4 and 5 assume Tasks 14-16 are done. Linux only; no game.

**What changed from the out-of-process plan.** The state machine and its load-ordering rules are unchanged. New, per the index Contracts: a `paused` flag (`set_paused`, `paused()`), set by the plugin from `UI::GameIsPaused` (Task 25); `not_ready_reason(bool require_unpaused = false)` returns `"paused"` only when the caller asked for an unpaused game and every earlier reason is clear (spec 4.1: a paused game is not "not ready" for ordinary calls); `base_state()` returns the JSON that `game.state` starts from and that every `not_ready` error carries in its detail. `loads_started()` counts world resets, so a quit to the main menu from a running game also bumps it (the VM drops running Papyrus stacks then, as on a load, and the dispatcher must fail the calls in flight). The open menus are kept in the order they opened (an insertion-ordered vector, not a sorted set), so a caller can see which menu is on top. The class now guards every field with **one mutex** instead of separate atomics, so `base_state()` is a consistent snapshot: the dispatcher (Task 18) reads it from Python threads and its reaper while the main and UI threads post events.

Lifecycle rules (spec 4.1), implemented once here and unit-tested on Linux: `data_ready` after `kDataLoaded`. `kPreLoadGame` sets `load_pending` and clears `game_loaded`; `kPostLoadGame` clears `load_pending` whether or not the load succeeded, and only on success sets `game_loaded` and increments `epoch` (`kNewGame` does both unconditionally). `game_loaded` also becomes false when the Main Menu opens; if it was true, that open also increments `loads_started` (a quit to the main menu is a world reset). `loading = load_pending || Loading Menu open`. `interactive = data_ready && game_loaded && !loading`. `not_ready_reason` checks `starting`, then `loading`, then `no_game`, then (only with `require_unpaused`) `paused`, so a save that is still loading reports `loading` and not `no_game`. The tests cover both orderings of `kPostLoadGame` against the Loading Menu closing, so the result does not depend on what S6 (Task 8) records. `kPostLoadGame`'s `data` is a packed bool, not a pointer (the plugin converts it; this class takes a `bool`).

**Files:**
- Create: `core/include/spy/lifecycle_state.h`, `core/src/lifecycle_state.cpp`, `core/tests/test_lifecycle_state.cpp`
- Consumes (generated, Task 12): `spy::kNotReadyReasons` (the test checks every reason the class returns is in it).

**Interfaces:**
- Consumes: `spy::json` (Task 14, `errors.h`).
- Produces: `class spy::LifecycleState` (not copyable or movable; the plugin creates one immortal instance in Task 25):
  - events: `on_data_loaded()`, `on_pre_load_game()`, `on_post_load_game(bool success)`, `on_new_game()`, `on_menu(const std::string& name, bool opening)`, `set_paused(bool)`
  - queries: `data_ready()`, `game_loaded()`, `loading()`, `interactive()`, `paused()`, `epoch() -> std::uint32_t`, `loads_started() -> std::uint32_t` (counts world resets: every `kPreLoadGame`, failed or not, and every Main Menu open while a game was loaded; never new games. The name is kept because Tasks 18, 19, 25 and 27 use it), `not_ready_reason(bool require_unpaused = false) -> std::optional<std::string>` (`"starting"`, `"loading"`, `"no_game"`, then `"paused"`), `open_menus() -> std::vector<std::string>` (in the order they opened, the last one on top; a menu reported open again moves to the end), `base_state() -> json` (exactly the keys `data_ready, game_loaded, loading, interactive, paused, menus, epoch`)
  - Thread-safe: one mutex; every method may be called from any thread.

- [ ] **Step 1: Write the failing tests**

The `loading` / `no_game` / `interactive` decisions are the part of the plugin most likely to be wrong and the part that cannot be checked without the game, so they live in the core as a class driven by plain events.

`core/tests/test_lifecycle_state.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/lifecycle_state.h"
#include <thread>
#include <vector>

using spy::LifecycleState;

namespace {
const char* const LM = "Loading Menu";
const char* const MM = "Main Menu";

// The game has started and sits at the main menu. (LifecycleState is not movable: it holds a mutex.)
struct AtMainMenu : LifecycleState
{
    AtMainMenu()
    {
        on_menu(MM, true);
        on_data_loaded();
    }
};
}

TEST_CASE("before data is loaded the reason is starting")
{
    LifecycleState s;
    CHECK(s.not_ready_reason() == "starting");
    CHECK_FALSE(s.interactive());
    s.on_menu(LM, true);
    CHECK(s.not_ready_reason() == "starting");  // starting wins over loading
}

TEST_CASE("main menu with no game loaded reports no_game")
{
    AtMainMenu s;
    CHECK(s.not_ready_reason() == "no_game");
    CHECK(s.epoch() == 0);
    CHECK(s.data_ready());
    CHECK_FALSE(s.game_loaded());
    CHECK(s.open_menus() == std::vector<std::string>{MM});
}

TEST_CASE("load ordering A: PostLoadGame arrives while the Loading Menu is open")
{
    AtMainMenu s;
    s.on_pre_load_game();
    CHECK(s.not_ready_reason() == "loading");  // loading, not no_game, although nothing is loaded yet
    s.on_menu(MM, false);
    s.on_menu(LM, true);
    CHECK(s.not_ready_reason() == "loading");
    s.on_post_load_game(true);
    CHECK(s.epoch() == 1);
    CHECK(s.game_loaded());
    CHECK(s.not_ready_reason() == "loading");  // still behind the loading screen
    CHECK_FALSE(s.interactive());
    s.on_menu(LM, false);
    CHECK(s.interactive());
    CHECK_FALSE(s.not_ready_reason().has_value());
}

TEST_CASE("load ordering B: the Loading Menu closes before PostLoadGame")
{
    AtMainMenu s;
    s.on_pre_load_game();
    s.on_menu(MM, false);
    s.on_menu(LM, true);
    s.on_menu(LM, false);
    // The menu is gone but the load has not finished: still loading, never no_game.
    CHECK(s.not_ready_reason() == "loading");
    CHECK_FALSE(s.interactive());
    s.on_post_load_game(true);
    CHECK(s.interactive());
    CHECK(s.epoch() == 1);
}

TEST_CASE("a failed load ends loading, bumps nothing and reports no_game")
{
    AtMainMenu s;
    s.on_pre_load_game();
    s.on_menu(LM, true);
    s.on_post_load_game(false);
    s.on_menu(LM, false);
    CHECK(s.epoch() == 0);
    CHECK_FALSE(s.game_loaded());
    CHECK(s.not_ready_reason() == "no_game");
}

TEST_CASE("loading a second save in a running game drops and restores interactivity, epoch counts loads")
{
    AtMainMenu s;
    s.on_pre_load_game();
    s.on_post_load_game(true);
    CHECK(s.interactive());
    s.on_pre_load_game();
    CHECK_FALSE(s.interactive());
    CHECK(s.not_ready_reason() == "loading");
    s.on_menu(LM, true);
    s.on_post_load_game(true);
    s.on_menu(LM, false);
    CHECK(s.interactive());
    CHECK(s.epoch() == 2);
}

TEST_CASE("new game bumps the epoch and is interactive once the Loading Menu closes")
{
    AtMainMenu s;
    s.on_menu(MM, false);
    s.on_menu(LM, true);
    s.on_new_game();
    CHECK(s.epoch() == 1);
    CHECK(s.not_ready_reason() == "loading");
    s.on_menu(LM, false);
    CHECK(s.interactive());
}

TEST_CASE("new game before the Loading Menu opens: interactive for that window (Task 25 logs the real order)")
{
    // Pins today's behaviour. If Task 25 or 31 finds kNewGame fires before the Loading Menu opens, add a pending
    // flag that on_new_game sets and the Loading Menu closing clears, and change this test.
    AtMainMenu s;
    s.on_menu(MM, false);
    s.on_new_game();
    CHECK(s.interactive());
    CHECK(s.epoch() == 1);
    s.on_menu(LM, true);
    CHECK(s.not_ready_reason() == "loading");
    s.on_menu(LM, false);
    CHECK(s.interactive());
    CHECK(s.epoch() == 1);
}

TEST_CASE("a cell-change Loading Menu in a running game is loading, then interactive again")
{
    AtMainMenu s;
    s.on_new_game();
    CHECK(s.interactive());
    s.on_menu(LM, true);
    CHECK(s.not_ready_reason() == "loading");
    s.on_menu(LM, false);
    CHECK(s.interactive());
    CHECK(s.epoch() == 1);
}

TEST_CASE("returning to the main menu clears game_loaded but keeps the epoch")
{
    AtMainMenu s;
    s.on_new_game();
    s.on_menu(MM, true);
    CHECK_FALSE(s.game_loaded());
    CHECK(s.not_ready_reason() == "no_game");
    CHECK(s.epoch() == 1);
}

TEST_CASE("menu stack lists menus in the order they opened and tracks open and close")
{
    LifecycleState s;
    s.on_menu("InventoryMenu", true);
    s.on_menu("Console", true);
    CHECK(s.open_menus() == std::vector<std::string>{"InventoryMenu", "Console"});  // the last one is on top
    s.on_menu("Console", false);
    s.on_menu("NeverOpened", false);
    CHECK(s.open_menus() == std::vector<std::string>{"InventoryMenu"});
    s.on_menu("Console", true);
    s.on_menu("InventoryMenu", true);  // reported open again: listed once, now on top
    CHECK(s.open_menus() == std::vector<std::string>{"Console", "InventoryMenu"});
}

TEST_CASE("loads_started counts every PreLoadGame, failed or not, and never new games")
{
    AtMainMenu s;
    CHECK(s.loads_started() == 0);
    s.on_pre_load_game();
    s.on_post_load_game(false);
    s.on_pre_load_game();
    s.on_post_load_game(true);
    s.on_new_game();
    CHECK(s.loads_started() == 2);
}

TEST_CASE("quitting to the main menu from a running game counts as a world reset in loads_started")
{
    AtMainMenu s;  // the Main Menu opening with no game loaded is not a reset
    CHECK(s.loads_started() == 0);
    s.on_menu(MM, false);
    s.on_new_game();
    s.on_menu(MM, true);  // quit to the main menu: the VM drops running stacks, as on a load
    CHECK(s.loads_started() == 1);
    CHECK(s.not_ready_reason() == "no_game");
    s.on_menu(MM, false);
    s.on_menu(MM, true);  // no game loaded any more: not another reset
    CHECK(s.loads_started() == 1);
    s.on_pre_load_game();
    CHECK(s.loads_started() == 2);
    s.on_post_load_game(true);
    CHECK(s.epoch() == 2);  // the epoch still counts games, not resets
}

TEST_CASE("paused is reported, but is a not-ready reason only when the caller asks for an unpaused game")
{
    AtMainMenu s;
    s.on_new_game();
    s.set_paused(true);
    CHECK(s.paused());
    CHECK(s.interactive());                                  // a paused game is still interactive (spec 4.1)
    CHECK_FALSE(s.not_ready_reason().has_value());
    CHECK_FALSE(s.not_ready_reason(false).has_value());
    CHECK(s.not_ready_reason(true) == "paused");
    s.set_paused(false);
    CHECK_FALSE(s.not_ready_reason(true).has_value());
}

TEST_CASE("the earlier reasons win over paused")
{
    LifecycleState starting;
    starting.set_paused(true);
    CHECK(starting.not_ready_reason(true) == "starting");
    AtMainMenu s;
    s.set_paused(true);
    CHECK(s.not_ready_reason(true) == "no_game");
    s.on_pre_load_game();
    CHECK(s.not_ready_reason(true) == "loading");
}

TEST_CASE("every reason the state machine returns is a generated not_ready reason")
{
    AtMainMenu s;
    LifecycleState fresh;
    std::vector<std::optional<std::string>> seen = {fresh.not_ready_reason(), s.not_ready_reason()};
    s.on_pre_load_game();
    seen.push_back(s.not_ready_reason());
    s.on_post_load_game(true);
    s.set_paused(true);
    seen.push_back(s.not_ready_reason(true));
    for (const auto& r : seen) {
        REQUIRE(r.has_value());
        bool known = false;
        for (const auto name : spy::kNotReadyReasons) known = known || name == *r;
        CHECK_MESSAGE(known, *r << " is not in protocol.json's not_ready_reasons");
    }
}

TEST_CASE("base_state carries the fields game.state and not_ready details need")
{
    AtMainMenu s;
    auto j = s.base_state();
    CHECK(j["data_ready"] == true);
    CHECK(j["game_loaded"] == false);
    CHECK(j["loading"] == false);
    CHECK(j["interactive"] == false);
    CHECK(j["paused"] == false);
    CHECK(j["epoch"] == 0);
    CHECK(j["menus"] == spy::json::array({"Main Menu"}));
    s.on_menu(MM, false);
    s.on_new_game();
    s.set_paused(true);
    s.on_menu("Console", true);
    j = s.base_state();
    CHECK(j["interactive"] == true);
    CHECK(j["game_loaded"] == true);
    CHECK(j["paused"] == true);
    CHECK(j["epoch"] == 1);
    CHECK(j["menus"] == spy::json::array({"Console"}));
    CHECK(j.size() == 7u);
}

TEST_CASE("events and queries from several threads at once do not race (run under ThreadSanitizer or ASan)")
{
    AtMainMenu s;
    std::vector<std::thread> threads;
    for (int t = 0; t < 4; ++t)
        threads.emplace_back([&s, t] {
            for (int i = 0; i < 2000; ++i) {
                if (t == 0) { s.on_pre_load_game(); s.on_post_load_game(true); }
                else if (t == 1) { s.on_menu(LM, i % 2 == 0); s.set_paused(i % 3 == 0); }
                else { (void)s.base_state(); (void)s.not_ready_reason(true); (void)s.open_menus(); }
            }
        });
    for (auto& th : threads) th.join();
    CHECK(s.loads_started() == 2000u);
    CHECK(s.epoch() == 2000u);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | grep -m1 "fatal error"`
Expected: FAIL, `fatal error: spy/lifecycle_state.h: No such file or directory`. (`core/CMakeLists.txt` globs `src/*.cpp` and `tests/*.cpp` with `CONFIGURE_DEPENDS`, so the new files need no CMake edit, only the re-configure in the command above.)

- [ ] **Step 3: Implement**

`core/include/spy/lifecycle_state.h`:
```cpp
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
```

`core/src/lifecycle_state.cpp`:
```cpp
#include "spy/lifecycle_state.h"
#include <algorithm>

namespace spy {
void LifecycleState::on_data_loaded()
{
    std::lock_guard l(mx_);
    data_ready_ = true;
}

void LifecycleState::on_pre_load_game()
{
    std::lock_guard l(mx_);
    game_loaded_ = false;
    load_pending_ = true;
    ++loads_started_;
}

void LifecycleState::on_post_load_game(bool success)
{
    std::lock_guard l(mx_);
    load_pending_ = false;
    if (success) {
        ++epoch_;
        game_loaded_ = true;
    }
}

void LifecycleState::on_new_game()
{
    std::lock_guard l(mx_);
    ++epoch_;
    game_loaded_ = true;
}

void LifecycleState::on_menu(const std::string& name, bool opening)
{
    std::lock_guard l(mx_);
    menus_.erase(std::remove(menus_.begin(), menus_.end(), name), menus_.end());
    if (opening) menus_.push_back(name);
    if (name == loading_menu_) loading_menu_open_ = opening;
    else if (name == main_menu_ && opening) {
        if (game_loaded_) ++loads_started_;  // quit to the main menu: a world reset, like a load
        game_loaded_ = false;
    }
}

void LifecycleState::set_paused(bool paused)
{
    std::lock_guard l(mx_);
    paused_ = paused;
}

bool LifecycleState::data_ready() const { std::lock_guard l(mx_); return data_ready_; }
bool LifecycleState::game_loaded() const { std::lock_guard l(mx_); return game_loaded_; }
bool LifecycleState::loading() const { std::lock_guard l(mx_); return loading_locked(); }
bool LifecycleState::interactive() const { std::lock_guard l(mx_); return interactive_locked(); }
bool LifecycleState::paused() const { std::lock_guard l(mx_); return paused_; }
std::uint32_t LifecycleState::epoch() const { std::lock_guard l(mx_); return epoch_; }
std::uint32_t LifecycleState::loads_started() const { std::lock_guard l(mx_); return loads_started_; }

std::optional<std::string> LifecycleState::not_ready_reason(bool require_unpaused) const
{
    std::lock_guard l(mx_);
    if (!data_ready_) return "starting";
    if (loading_locked()) return "loading";
    if (!game_loaded_) return "no_game";
    if (require_unpaused && paused_) return "paused";
    return std::nullopt;
}

std::vector<std::string> LifecycleState::open_menus() const
{
    std::lock_guard l(mx_);
    return menus_;
}

json LifecycleState::base_state() const
{
    std::lock_guard l(mx_);
    return {{"data_ready", data_ready_},
            {"game_loaded", game_loaded_},
            {"loading", loading_locked()},
            {"interactive", interactive_locked()},
            {"paused", paused_},
            {"menus", json(menus_)},
            {"epoch", epoch_}};
}
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cmake --build core/build -j && core/build/spy_core_tests && core/build/spy_core_tests -tc="*load ordering*,*failed load*,*paused*"`
Expected: PASS, `[doctest] test cases:  79 |  79 passed | 0 failed | 0 skipped` and `[doctest] assertions: 863 | 863 passed | 0 failed |` (Tasks 14-17 together; these counts assume Tasks 14-16 are done); the filtered run prints `[doctest] test cases:  5 |  5 passed | 0 failed | 74 skipped`.

- [ ] **Step 5: Run the suite under warnings and the sanitizers, then ThreadSanitizer**

Run: `cmake --build core/build/asan -j 2>&1 | grep -c "warning:" ; core/build/asan/spy_core_tests | tail -3`
Expected: `0`, then the same counts as Step 4 with no sanitizer report. (If `core/build/asan` does not exist yet, configure it as Task 16 Step 5 does.)

Run: `cmake -S core -B core/build/tsan -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_FLAGS="-fsanitize=thread" >/dev/null && cmake --build core/build/tsan -j >/dev/null && setarch "$(uname -m)" -R core/build/tsan/spy_core_tests -tc="*several threads*"`
Expected: `[doctest] test cases: 1 | 1 passed | 0 failed | 78 skipped`, and no `WARNING: ThreadSanitizer: data race` in the output. (`setarch -R` turns off address-space randomisation for that one run: on recent kernels GCC 11's ThreadSanitizer otherwise stops at once with `FATAL: ThreadSanitizer: unexpected memory mapping`, which is not a finding.)

- [ ] **Step 6: Commit**

```bash
git add core/include/spy/lifecycle_state.h core/src/lifecycle_state.cpp core/tests/test_lifecycle_state.cpp
git commit -m "feat(core): lifecycle state machine with paused, base_state and load-ordering tests"
```
