# Skython examples

Each example is one Python file. Drop it into `Data/SKSE/Plugins/Skython/scripts/`, edit the settings at the top, and start the game. Every script defines `setup(game)`: Skython calls it at startup and hands it a connected `game` object, so most scripts import nothing but the standard library.

| Example | What it does | Lines |
|---|---|---|
| [`twitch_mudcrabs.py`](twitch_mudcrabs.py) | A viewer types `!mudcrab` in your Twitch chat and a mudcrab carrying their name appears next to you. Reads chat anonymously, so no bot account or token is needed. | ~90 |
| [`talk_to_npc_llm.py`](talk_to_npc_llm.py) | Look at any NPC, press K, and they greet you in character through a language model (local Ollama/LM Studio or any OpenAI-compatible API). | ~80 |
| [`live_dashboard.py`](live_dashboard.py) | A live stats page (health, magicka, stamina, gold, level, location, time) at `http://127.0.0.1:8765`, usable as an OBS browser source. | ~100 |
| [`real_weather.py`](real_weather.py) | Skyrim's weather follows the real weather in your city (Open-Meteo, no API key). | ~60 |

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
level, gold = b.run(stop_on_error=False)        # in order; a failed step comes back as an exception object
```

A batch does not raise for a failed step: `Batch.run()` puts the step's exception object in its place in the list. With `stop_on_error=False` every step runs and the list has one entry per step, so it unpacks safely; check each entry with `isinstance(x, Exception)` before using it. With the default `stop_on_error=True` the list ends at the first failed step (its exception is the last entry), so it can be shorter than the batch and unpacking it can fail.

Pass every Papyrus parameter, including the ones Papyrus lets you leave out: the game does not expose Papyrus default values, so `PlaceAtMe(form)` is written `PlaceAtMe(form, 1, False, False)`. (Generated typed wrappers with defaults come later.)

Game calls can fail for ordinary reasons (the game is paused, a menu is open, a save is loading), so loops catch exceptions and try again; `print` goes to `python.log`.

Scripts run on their own threads, so a slow network call never stalls the game; each game call is queued as an SKSE task that the game runs in its next task drain, so a call takes about one SKSE task drain (~46 ms on the test machine). Long-running work goes on a thread that `setup` starts and that first calls `game.wait_ready()` (no save is loaded while `setup` runs). `wait_ready()` raises `NotReady` when no save is loaded within its timeout, so the examples call it in a loop that catches `NotReady` and tries again: a long stay at the main menu never ends the thread.

## Status

I've run `live_dashboard.py` in game. For the other three I checked every Papyrus call against the running game but haven't run them end to end yet. There are no event examples yet.
