# Skython examples

Each example is one Python file. Drop it into `Data/SKSE/Plugins/Skython/scripts/`, edit the settings at the top, and start the game. Every script defines `setup(game)`: Skython calls it at startup and hands it a ready `game` object, so most scripts import nothing but the standard library.

| Example | What it does | Lines |
|---|---|---|
| [`twitch_mudcrabs.py`](twitch_mudcrabs.py) | A viewer types `!mudcrab` in your Twitch chat and a mudcrab carrying their name appears next to you. Anonymous chat read: no bot account, token, or server. | ~60 |
| [`talk_to_npc_llm.py`](talk_to_npc_llm.py) | Look at any NPC, press K, and they greet you in character through a language model (local Ollama/LM Studio or any OpenAI-compatible API). | ~80 |
| [`live_dashboard.py`](live_dashboard.py) | A live stats page (health, magicka, stamina, gold, level, location, time) at `http://127.0.0.1:8765`, usable as an OBS browser source. | ~80 |
| [`real_weather.py`](real_weather.py) | Skyrim's weather follows the real weather in your city (Open-Meteo, no API key). | ~50 |

## Why Python

Talking to the outside world from Papyrus takes a separate HTTP plugin, a server process, and code like this (from the SKSE_HTTP plugin's README):

```papyrus
int handle = SKSE_HTTP.createDictionary()
SKSE_HTTP.setString(handle, "requestType", "startConversation")
SKSE_HTTP.setBool(handle, "isInCombatWithPlayer", false)
SKSE_HTTP.sendLocalhostHttpRequest(handle, 5000, "mantella")
; ...then register for "SKSE_HTTP_OnHttpReplyReceived" and read the reply back one key at a time
```

In Skython the request, the reply and the game calls are ordinary Python:

```python
reply = json.load(urllib.request.urlopen(url, json.dumps({"requestType": "startConversation"}).encode()))
game.call("Debug", "Notification", reply["text"])
```

## The API in five lines

```python
player = game.form("Skyrim.esm|0x14")                                  # any form, by plugin and local ID
health = game.call_method(player, "Actor", "GetActorValue", "Health")  # any Papyrus method
game.call("Debug", "Notification", f"Health: {health:.0f}")             # any global Papyrus function
b = game.batch(); b.call_method(player, "Actor", "GetLevel"); b.call_method(player, "Actor", "GetGoldAmount")
level, gold = b.run()                                                   # several calls in one game frame
```

Pass every Papyrus parameter, including the ones Papyrus lets you leave out: the game does not expose Papyrus default values, so `PlaceAtMe(form)` is written `PlaceAtMe(form, 1, False, False)`. (Generated typed wrappers with defaults come later.) A Papyrus Float parameter takes a Python float (`3.0`, not `3`).

Game calls can fail for ordinary reasons (the game is paused, a menu is open, a save is loading), so loops catch exceptions and try again; `print` goes to `python.log`.

Scripts run on their own threads, so a slow network call never stalls the game; game calls are carried out safely on the game's own thread. Long-running work goes on a thread that `setup` starts and that first calls `game.wait_ready()` (no save is loaded while `setup` runs).

## Status

Written against Skython's v1 API; they run once the in-game plugin (milestone 1) ships. Examples that react to game events (deaths, quest stages, a Papyrus script calling `Skython.Send`) come with milestone 2.
