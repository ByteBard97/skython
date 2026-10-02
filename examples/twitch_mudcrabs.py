"""Twitch chat spawns mudcrabs named after the viewers who summoned them.

A viewer types `!mudcrab` in your Twitch chat; a mudcrab carrying their name appears next to you.
It reads chat anonymously over IRC, so there is no bot account or token to set up.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/ and set CHANNEL below.
Needs: Skython 0.1 (Papyrus calls). Standard library only.
"""
import socket
import threading
import time

from skython.errors import StaleReference

CHANNEL = "your_twitch_channel"   # lower case, without the #
COMMAND = "!mudcrab"
COOLDOWN_S = 10                   # per viewer
MAX_ALIVE = 20                    # never more than this many summoned crabs at once

READ_TIMEOUT_S = 360               # Twitch PINGs about every 5 minutes; silence longer than this is a dead link
RETRY_MIN_S, RETRY_MAX_S = 5, 60  # reconnect back-off

MUDCRAB = "Skyrim.esm|0x0E4010"   # the base Mudcrab actor
PLAYER = "Skyrim.esm|0x14"


def chat_messages(sock, channel):
    """Yield (user, text) for every chat line. Anonymous read-only login: nick justinfanNNNN."""
    sock.sendall(f"NICK justinfan{int(time.time()) % 100000}\r\nJOIN #{channel}\r\n".encode())
    buf = b""
    while True:
        data = sock.recv(4096)                   # socket.timeout (an OSError) if the link goes quiet
        if not data:
            raise ConnectionError("chat closed")
        buf += data
        *lines, buf = buf.split(b"\r\n")
        for line in lines:
            text = line.decode("utf-8", "replace")
            if text.startswith("PING"):
                sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            elif " PRIVMSG " in text:            # :user!user@user.tmi.twitch.tv PRIVMSG #chan :message
                user = text[1:text.index("!")]
                yield user, text.split(" :", 1)[1]


def chat_forever(channel):
    """chat_messages, reconnecting with back-off whenever the connection drops."""
    delay = RETRY_MIN_S
    while True:
        try:
            with socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=30) as sock:
                sock.settimeout(READ_TIMEOUT_S)
                for message in chat_messages(sock, channel):
                    delay = RETRY_MIN_S              # connected and reading: reset the back-off
                    yield message
        except OSError as e:                     # includes ConnectionError and socket.timeout
            print(f"twitch_mudcrabs: chat connection lost ({e}); retrying in {delay} s")
        time.sleep(delay)
        delay = min(delay * 2, RETRY_MAX_S)


def run(game):
    game.wait_ready(timeout=86400)
    player = game.form(PLAYER)
    mudcrab = game.form(MUDCRAB)
    last_spawn, alive = {}, []
    for user, text in chat_forever(CHANNEL):
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
