"""Talk to any NPC through a language model: look at them, press K, read their answer.

The core idea behind mods like Mantella, without a separate server, an HTTP plugin, or dozens of
Papyrus scripts. Works with any OpenAI-compatible endpoint: a local Ollama or LM Studio, or a hosted API.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and set ENDPOINT and MODEL below.
Needs: Skython M1 (Papyrus calls, SKSE's Input and Game.GetCurrentCrosshairRef). Standard library only.
"""
import json
import os
import textwrap
import threading
import time
import urllib.request

ENDPOINT = "http://localhost:11434/v1/chat/completions"   # Ollama's OpenAI-compatible endpoint
MODEL = "llama3.1"
API_KEY = os.environ.get("SKYTHON_LLM_KEY", "")            # only for hosted APIs; never hard-code it here
HOTKEY = 0x25                                              # DirectInput scan code for K

PLAYER = "Skyrim.esm|0x14"
GAME_HOUR = "Skyrim.esm|0x38"                              # the GameHour global
CHARACTER = 62                                             # Form.GetType() for an actor reference


def ask_llm(npc_name, place, hour):
    prompt = (f"You are {npc_name}, a character in Skyrim. It is {hour:02d}:00 and you are in {place}. "
              "The Dragonborn has just walked up to you. Greet them in character, in two short sentences.")
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(ENDPOINT, body, {"Content-Type": "application/json",
                                                   "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["choices"][0]["message"]["content"].strip()


def talk(game, player):
    target = game.call("Game", "GetCurrentCrosshairRef")
    if target is None or game.call_method(target, "Form", "GetType") != CHARACTER:
        return
    name = game.call_method(target, "ObjectReference", "GetDisplayName")
    location = game.call_method(player, "ObjectReference", "GetCurrentLocation")
    place = game.call_method(location, "Form", "GetName") if location else "the wilds"
    hour = int(game.call_method(game.form(GAME_HOUR), "GlobalVariable", "GetValue"))
    reply = ask_llm(name, place, hour)          # runs on this script's thread; the game keeps running
    for line in textwrap.wrap(f"{name}: {reply}", 80):   # notifications cut off long lines
        game.call("Debug", "Notification", line)


def run(game):
    game.wait_ready()
    player = game.form(PLAYER)
    was_down = False
    while True:
        try:
            down = game.call("Input", "IsKeyPressed", HOTKEY)
            if down and not was_down:
                try:
                    talk(game, player)
                except OSError as e:            # the model server is not running, timed out, ...
                    game.call("Debug", "Notification", f"LLM unavailable: {e}")
            was_down = down
        except Exception as e:                  # paused, menu open, loading a save...: try again shortly
            print(f"talk_to_npc_llm: {e}")
            time.sleep(1)
        time.sleep(0.1)


def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()
