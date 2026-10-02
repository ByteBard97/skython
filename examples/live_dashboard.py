"""A live stats page for your browser or as an OBS browser source: health, magicka, stamina, gold,
level, location and time of day, refreshed every second.

The page is served on 127.0.0.1:8765 only, and every request needs a token. Each time the script starts it makes a
random one and writes the full address to python.log, for example:
    live_dashboard: open http://127.0.0.1:8765/?token=...
Open that address while the game runs. In OBS: add a Browser source with that URL. The token changes every run; to keep
one OBS URL, set SKYTHON_DASHBOARD_TOKEN in the game's environment to a long random string of your own. The token goes
in the URL (?token=...) or in an X-Skython-Token header; anything else gets 403.

Install: copy this file into Data/SKSE/Plugins/Skython/scripts/.
Needs: Skython 0.1 (Papyrus calls). Standard library only.
"""
import hmac
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from skython.errors import ConnectionLost

PORT = 8765
PLAYER = "Skyrim.esm|0x14"
GAME_HOUR = "Skyrim.esm|0x38"

stats = {"status": "waiting for a save to load"}
stop = threading.Event()                        # set by teardown(): poll() ends, the server is shut down
server = None
token = ""                                      # set by setup(), new each run

PAGE = b"""<!doctype html><meta charset="utf-8"><title>Skyrim</title>
<style>body{font:20px Georgia,serif;color:#eee;background:transparent;text-shadow:0 0 4px #000}
.bar{height:10px;margin:2px 0 8px}</style>
<div id="s"></div>
<script>
async function tick(){
  const s = await (await fetch('/stats' + location.search)).json();   // the page's own ?token=...
  const bar = (v,c) => `<div class="bar" style="width:${Math.max(0,v)*2}px;background:${c}"></div>`;
  document.getElementById('s').innerHTML = s.status ? s.status :
    `<b>Level ${s.level}</b> &middot; ${s.location} &middot; ${s.time}<br>
     Health ${s.health}${bar(s.health,'#c33')} Magicka ${s.magicka}${bar(s.magicka,'#36c')}
     Stamina ${s.stamina}${bar(s.stamina,'#3a3')} Gold ${s.gold}`;
}
setInterval(tick, 1000); tick();
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        given = self.headers.get("X-Skython-Token") or parse_qs(url.query).get("token", [""])[0]
        if not hmac.compare_digest(given.encode(), token.encode()):
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"wrong or missing token: use the address in python.log\n")
            return
        body, kind = (json.dumps(stats).encode(), "application/json") if url.path == "/stats" else (PAGE, "text/html")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):               # keep python.log quiet
        pass


def poll(game):
    try:
        game.wait_ready(timeout=86400)
    except ConnectionLost:                  # the script was reloaded or the game closed
        return
    player, hour = game.form(PLAYER), game.form(GAME_HOUR)
    while not stop.is_set():
        b = game.batch()                        # seven reads, in order (one game task each: ordered, not atomic)
        for av in ("Health", "Magicka", "Stamina"):
            b.call_method(player, "Actor", "GetActorValue", av)
        b.call_method(player, "Actor", "GetGoldAmount")
        b.call_method(player, "Actor", "GetLevel")
        b.call_method(player, "ObjectReference", "GetCurrentLocation")
        b.call_method(hour, "GlobalVariable", "GetValue")
        try:
            results = b.run(stop_on_error=False)                         # all seven, even after a failure
            failed = [r for r in results if isinstance(r, Exception)]   # run() returns errors in place
            if failed:
                raise failed[0]
            health, magicka, stamina, gold, level, location, h = results
            place = game.call_method(location, "Form", "GetName") if location else "the wilds"
            stats.clear()
            stats.update(health=round(health), magicka=round(magicka), stamina=round(stamina), gold=gold,
                         level=level, location=place, time=f"{int(h):02d}:{int(h % 1 * 60):02d}")
        except ConnectionLost:                  # the script was reloaded or the game closed
            return
        except Exception as e:                  # paused, menu open, loading a save...: keep the last numbers
            print(f"live_dashboard: {e}")
        stop.wait(1)


def setup(game):
    global server, token
    stop.clear()
    token = os.environ.get("SKYTHON_DASHBOARD_TOKEN") or secrets.token_urlsafe(16)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"live_dashboard: open http://127.0.0.1:{PORT}/?token={token}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=poll, args=(game,), daemon=True).start()


def teardown():                                 # on reload: free the port and stop polling
    stop.set()
    if server is not None:
        server.shutdown()
        server.server_close()
