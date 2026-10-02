ScriptName Skython Hidden
{Skython's Papyrus API. Send(channel, payload) raises the event "channel" for Python subscribers of that channel
(game.on_channel). Papyrus strings pass through the engine's case-insensitive string pool, so channel filters ignore
case. A payload longer than 65536 bytes arrives cut at a UTF-8 boundary with truncated = true.}

Function Send(String channel, String payload) global native
