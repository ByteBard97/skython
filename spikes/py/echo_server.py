# S8: a line-echo server inside the game's interpreter, on 127.0.0.1:45979 (the harness owns 45978).
# Safe to send twice: the second send reports the running server.
import socketserver
import threading


class _EchoHandler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            self.wfile.write(line)
            self.wfile.flush()


class _EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if "_spike_echo" not in globals():
    _spike_echo = _EchoServer(("127.0.0.1", 45979), _EchoHandler)
    threading.Thread(target=_spike_echo.serve_forever, name="spike-echo", daemon=True).start()
result = "listening 127.0.0.1:%d" % _spike_echo.server_address[1]
