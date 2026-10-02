"""Twitch chat spawns mudcrabs named after the viewers who summoned them.

A viewer types `!mudcrab` in your Twitch chat; a mudcrab carrying their name appears next to you.
No Twitch account, bot token, server, or extra mod is needed: it reads chat anonymously over IRC.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and set CHANNEL below.
Needs: Skython M1 (Papyrus calls). Standard library only.
"""
import socket
import threading
import time

from skython.errors import StaleReference

CHANNEL = "your_twitch_channel"   # lower case, without the #
COMMAND = "!mudcrab"
COOLDOWN_S = 10                   # per viewer
MAX_ALIVE = 20                    # never more than this many summoned crabs at once

MUDCRAB = "Skyrim.esm|0x0E4010"   # the base Mudcrab actor
PLAYER = "Skyrim.esm|0x14"


def chat_messages(channel):
    """Yield (user, text) for every chat line. Anonymous read-only login: nick justinfanNNNN."""
    sock = socket.create_connection(("irc.chat.twitch.tv", 6667))
    sock.sendall(f"NICK justinfan{int(time.time()) % 100000}\r\nJOIN #{channel}\r\n".encode())
    buf = b""
    while True:
        buf += sock.recv(4096)
        *lines, buf = buf.split(b"\r\n")
        for line in lines:
            text = line.decode("utf-8", "replace")
            if text.startswith("PING"):
                sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            elif " PRIVMSG " in text:            # :user!user@user.tmi.twitch.tv PRIVMSG #chan :message
                user = text[1:text.index("!")]
                yield user, text.split(" :", 1)[1]


def run(game):
    game.wait_ready()                            # wait until a save is loaded
    player = game.form(PLAYER)
    mudcrab = game.form(MUDCRAB)
    last_spawn, alive = {}, []
    for user, text in chat_messages(CHANNEL):
        if text.strip().lower() != COMMAND or time.time() - last_spawn.get(user, 0) < COOLDOWN_S:
            continue
        try:
            alive = [c for c in alive if not game.call_method(c, "Actor", "IsDead")]
            if len(alive) >= MAX_ALIVE:
                continue
            last_spawn[user] = time.time()
            # Every Papyrus parameter must be passed: PlaceAtMe(form, count, forcePersist, initiallyDisabled)
            crab = game.call_method(player, "ObjectReference", "PlaceAtMe", mudcrab, 1, False, False,
                                    require_unpaused=True)
            game.call_method(crab, "ObjectReference", "SetDisplayName", user, True)
            game.call("Debug", "Notification", f"{user} summoned a mudcrab!")
            alive.append(crab)
        except StaleReference:
            alive = []                           # a different save was loaded: the old crabs are gone
        except Exception as e:                   # paused, menu open, loading...: skip this one
            print(f"twitch_mudcrabs: {user}: {e}")


def setup(game):
    threading.Thread(target=run, args=(game,), daemon=True).start()
