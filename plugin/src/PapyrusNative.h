#pragma once

namespace spy::native {
// Registers the Papyrus native `Skython.Send(String channel, String payload) global native` through SKSE's Papyrus
// interface. Called once from SpyCore_Load (plugin load); the VM calls back when it is ready and the callback logs
// "native: Skython.Send registered". The native copies both strings, builds the `channel` event with
// spy::make_channel_event and calls events::Push: no AddTask, no lookup, no Python, never blocks, never throws.
void Register();
}
