"""Skyrim's weather follows the real weather where you live.

Every 10 minutes it asks Open-Meteo (free, no API key) for your city's current weather and forces the
matching Skyrim weather.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and set LATITUDE and LONGITUDE below.
Needs: Skython 0.1 (Papyrus calls). Standard library only.
"""
import json
import threading
import time
import urllib.request

LATITUDE, LONGITUDE = 59.91, 10.75          # Oslo
EVERY_S = 600

# WMO weather code ranges (Open-Meteo "weather_code") -> a vanilla Skyrim weather (IDs from the STEP weather reference).
CLEAR, CLOUDY, FOG = "Skyrim.esm|0x81A", "Skyrim.esm|0x12F89", "Skyrim.esm|0xC821E"
RAIN, STORM, SNOW = "Skyrim.esm|0xC821F", "Skyrim.esm|0xC8220", "Skyrim.esm|0x4D7FB"
WEATHERS = [
    (range(0, 2), CLEAR),
    (range(2, 4), CLOUDY),
    (range(45, 49), FOG),
    (range(51, 68), RAIN),      # drizzle and rain
    (range(71, 78), SNOW),
    (range(80, 83), RAIN),      # rain showers
    (range(85, 87), SNOW),      # snow showers
    (range(95, 100), STORM),    # thunderstorm
]


def current_code():
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}"
           "&current=weather_code")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)["current"]["weather_code"]


def run(game):
    game.wait_ready(timeout=86400)           # wait out the main menu
    while True:
        try:
            code = current_code()
            key = next((k for codes, k in WEATHERS if code in codes), None)
            if key:
                game.call_method(game.form(key), "Weather", "ForceActive", True)
        except Exception as e:                  # offline, or the game is paused/loading: try next time
            print(f"real_weather: {e}")
        time.sleep(EVERY_S)


def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()
