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
