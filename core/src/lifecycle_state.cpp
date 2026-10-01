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
