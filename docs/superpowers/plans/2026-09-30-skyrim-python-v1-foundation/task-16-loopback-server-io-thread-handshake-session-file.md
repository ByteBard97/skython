# Task 16: Loopback server: IO thread, handshake, session file

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `plugin/src/Wire.h`, `plugin/src/Server.h`, `plugin/src/Server.cpp`
- Modify: `plugin/src/main.cpp` (full replacement below)
- Create: `livetest/conftest.py`, `livetest/support.py`, `livetest/test_m1_transport.py`

**Interfaces:**
- Consumes: `spy::FrameDecoder`, `spy::encode_frame`, `spy::parse_hello`, `spy::check_hello`, `spy::parse_request`, `spy::make_welcome`, `spy::make_handshake_error`, `spy::make_error`, `spy::make_session_file`, `spy::Outbox`, `spy::ConnId`, `spy::known_ops` (Tasks 9–12); `spy::settings`, `spy::life`, `spy::log` (Task 15).
- Produces:
  - `plugin/src/Wire.h`: `std::string spy::Frame(const json&)` (invalid UTF-8 from game strings is replaced, never thrown; a reply over 16 MiB becomes an `internal` error frame for the same id)
  - `plugin/src/Server.h`: `struct spy::net::Callbacks { std::function<void(ConnId, Request)> onRequest; std::function<void(ConnId)> onDisconnect; std::function<void()> onTick; std::function<WelcomeInfo()> welcomeInfo; }`; `bool spy::net::Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox)`; `std::string spy::net::RandomToken()`; `bool spy::net::WriteSessionFile(const std::filesystem::path&, const json&)`; `std::uint32_t spy::net::ProcessId()`
  - Server rules: binds `127.0.0.1` only; refuses non-loopback peers; requests that arrive together with the client's close are still dispatched (their replies are dropped); the first frame must be a valid hello within 5 s; bad token / protocol → `handshake_error` then close; a frame that is not a JSON object or has an oversized header closes that connection; a request that fails validation gets an error response (the connection stays open); a connection whose unsent output exceeds 64 MiB is closed (a client that stopped reading).
  - `livetest/conftest.py`: fixtures `session` (a `skyrim.Session` from `SKYRIM_PYTHON_SESSION`), `game` (a connected `skyrim.Game`). `livetest/support.py`: `raw_connect(session) -> socket.socket` (handshaken raw socket), `send_raw(sock, bytes)`, `closed_by_server(sock, timeout=3.0) -> bool`

- [ ] **Step 1: Write the server**

`plugin/src/Wire.h`:
```cpp
#pragma once
#include "spy/framing.h"
#include "spy/messages.h"
#include <stdexcept>

namespace spy {
// Game strings are not always valid UTF-8; replace bad bytes instead of throwing.
inline std::string Frame(const json& j)
{
    const auto text = j.dump(-1, ' ', false, json::error_handler_t::replace);
    try {
        return encode_frame(text);
    } catch (const std::length_error&) {
        const auto id = j.contains("id") && j["id"].is_number_integer() ? j["id"].get<std::uint64_t>() : 0;
        return encode_frame(make_error(id, ErrorCode::internal, "response exceeds 16 MiB").dump());
    }
}
}
```

`plugin/src/Server.h`:
```cpp
#pragma once
#include "spy/messages.h"
#include "spy/outbox.h"
#include <filesystem>
#include <functional>

namespace spy::net {
struct Callbacks
{
    std::function<void(ConnId, Request)> onRequest;  // IO thread; must not block
    std::function<void(ConnId)> onDisconnect;        // IO thread; only for connections that finished the handshake
    std::function<void()> onTick;                    // IO thread; about every 5 ms
    std::function<WelcomeInfo()> welcomeInfo;        // IO thread
};

// Binds 127.0.0.1:port and starts the IO thread. Returns false (and logs why) if it cannot bind.
bool Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox);
std::string RandomToken();  // 128 bits from BCryptGenRandom, 32 lowercase hex chars
// Writes path.tmp then renames over path, so a client never reads a half-written file.
bool WriteSessionFile(const std::filesystem::path& path, const json& content);
std::uint32_t ProcessId();
}
```

`plugin/src/Server.cpp` (Winsock only; must not include CommonLib headers):
```cpp
#include "Server.h"
#include "Log.h"
#include "Wire.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <bcrypt.h>
#include <chrono>
#include <fstream>
#include <thread>
#include <unordered_map>
#include <vector>

namespace spy::net {
namespace {
using Clock = std::chrono::steady_clock;
constexpr auto kHelloTimeout = std::chrono::seconds(5);
constexpr std::size_t kMaxUnsent = 64u * 1024u * 1024u;

struct Conn
{
    SOCKET sock = INVALID_SOCKET;
    ConnId id = 0;
    FrameDecoder decoder;
    bool authed = false;
    bool closeAfterFlush = false;
    bool dead = false;
    std::string out;
    Clock::time_point accepted;
};

void SetNonBlocking(SOCKET s)
{
    u_long on = 1;
    ioctlsocket(s, FIONBIO, &on);
}

std::uint64_t IdOf(const json& j)
{
    if (j.is_object() && j.contains("id") && j["id"].is_number_integer() && j["id"].get<std::int64_t>() >= 0)
        return j["id"].get<std::uint64_t>();
    return 0;
}

class Server
{
public:
    Server(SOCKET listen, std::string token, Callbacks cb, Outbox& outbox)
        : listen_(listen), token_(std::move(token)), cb_(std::move(cb)), outbox_(outbox) {}

    void Run()
    {
        for (;;) {
            std::vector<WSAPOLLFD> fds;
            fds.push_back({listen_, POLLRDNORM, 0});
            for (auto& [id, c] : conns_)
                fds.push_back({c.sock, static_cast<SHORT>(POLLRDNORM | (c.out.empty() ? 0 : POLLWRNORM)), 0});
            WSAPoll(fds.data(), static_cast<ULONG>(fds.size()), 5);

            if (fds[0].revents & POLLRDNORM) Accept();
            for (std::size_t i = 1; i < fds.size(); ++i) {
                auto it = FindBySocket(fds[i].fd);
                if (it == conns_.end()) continue;
                if (fds[i].revents & (POLLRDNORM | POLLHUP | POLLERR)) Read(it->second);
            }
            if (cb_.onTick) cb_.onTick();
            const auto now = Clock::now();
            for (auto& [id, c] : conns_) {
                if (c.dead) continue;
                if (!c.authed && !c.closeAfterFlush && now - c.accepted > kHelloTimeout) {
                    log::Warn("server: connection " + std::to_string(id) + " sent no hello within 5 s; closing");
                    c.dead = true;
                    continue;
                }
                if (c.authed)
                    for (auto& f : outbox_.drain(id)) c.out += f;
                Write(c);
            }
            Reap();
        }
    }

private:
    std::unordered_map<ConnId, Conn>::iterator FindBySocket(SOCKET s)
    {
        for (auto it = conns_.begin(); it != conns_.end(); ++it)
            if (it->second.sock == s) return it;
        return conns_.end();
    }

    void Accept()
    {
        for (;;) {
            sockaddr_in peer{};
            int len = sizeof peer;
            SOCKET s = accept(listen_, reinterpret_cast<sockaddr*>(&peer), &len);
            if (s == INVALID_SOCKET) return;  // WSAEWOULDBLOCK: no more pending
            if (peer.sin_family != AF_INET || ntohl(peer.sin_addr.s_addr) != INADDR_LOOPBACK) {
                log::Warn("server: refused a non-loopback peer");
                closesocket(s);
                continue;
            }
            SetNonBlocking(s);
            BOOL nodelay = TRUE;
            setsockopt(s, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&nodelay), sizeof nodelay);
            Conn c;
            c.sock = s;
            c.id = nextId_++;
            c.accepted = Clock::now();
            conns_.emplace(c.id, std::move(c));
        }
    }

    void Read(Conn& c)
    {
        char buf[64 * 1024];
        bool eof = false;
        for (;;) {
            const int n = recv(c.sock, buf, sizeof buf, 0);
            if (n > 0) {
                c.decoder.feed(std::string_view(buf, static_cast<std::size_t>(n)));
                continue;
            }
            if (n == SOCKET_ERROR && WSAGetLastError() == WSAEWOULDBLOCK) break;
            eof = true;  // 0 = orderly close; other errors = reset
            break;
        }
        // Handle every complete frame first: a client may send requests and close in the same instant.
        while (!c.dead && !c.closeAfterFlush) {
            auto payload = c.decoder.next();
            if (!payload) break;
            auto j = json::parse(*payload, nullptr, false);
            if (j.is_discarded() || !j.is_object()) {
                log::Warn("server: connection " + std::to_string(c.id) + " sent a frame that is not a JSON object; closing");
                c.dead = true;
                break;
            }
            if (!c.authed) Handshake(c, j);
            else HandleRequest(c, j);
        }
        if (c.decoder.failed()) {
            log::Warn("server: connection " + std::to_string(c.id) + " announced a frame over 16 MiB; closing");
            c.dead = true;
        }
        if (eof) c.dead = true;
    }

    void Handshake(Conn& c, const json& j)
    {
        try {
            const auto hello = parse_hello(j);
            if (auto bad = check_hello(hello, token_)) {
                c.out += Frame(make_handshake_error(*bad, *bad == ErrorCode::unauthorized ? "bad token (stale session file?)"
                                                                                         : "protocol major version mismatch"));
                c.closeAfterFlush = true;
                log::Warn("server: handshake refused (" + std::string(to_string(*bad)) + ") for client '" + hello.client + "'");
                return;
            }
            c.authed = true;
            outbox_.open(c.id);
            c.out += Frame(make_welcome(cb_.welcomeInfo()));
            log::Info("server: client '" + hello.client + "' connected as " + std::to_string(c.id));
        } catch (const ProtocolError& e) {
            c.out += Frame(make_handshake_error(e.code, e.what()));
            c.closeAfterFlush = true;
        }
    }

    void HandleRequest(Conn& c, const json& j)
    {
        try {
            cb_.onRequest(c.id, parse_request(j));
        } catch (const ProtocolError& e) {
            c.out += Frame(make_error(IdOf(j), e.code, e.what()));
        }
    }

    void Write(Conn& c)
    {
        while (!c.dead && !c.out.empty()) {
            const int n = send(c.sock, c.out.data(), static_cast<int>(std::min<std::size_t>(c.out.size(), 1 << 20)), 0);
            if (n > 0) {
                c.out.erase(0, static_cast<std::size_t>(n));
                continue;
            }
            if (WSAGetLastError() != WSAEWOULDBLOCK) c.dead = true;
            break;
        }
        if (c.out.size() > kMaxUnsent) {
            log::Warn("server: connection " + std::to_string(c.id) + " is not reading its replies; closing");
            c.dead = true;
        }
        if (c.closeAfterFlush && c.out.empty()) c.dead = true;
    }

    void Reap()
    {
        for (auto it = conns_.begin(); it != conns_.end();) {
            if (!it->second.dead) {
                ++it;
                continue;
            }
            auto& c = it->second;
            closesocket(c.sock);
            if (c.authed) {
                outbox_.close(c.id);
                if (cb_.onDisconnect) cb_.onDisconnect(c.id);
                log::Info("server: connection " + std::to_string(c.id) + " closed");
            }
            it = conns_.erase(it);
        }
    }

    SOCKET listen_;
    std::string token_;
    Callbacks cb_;
    Outbox& outbox_;
    std::unordered_map<ConnId, Conn> conns_;
    ConnId nextId_ = 1;  // never reused, so a late reply can never reach a newer connection
};
}

bool Start(std::uint16_t port, std::string token, Callbacks cb, Outbox& outbox)
{
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        log::Error("server: WSAStartup failed");
        return false;
    }
    SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    BOOL excl = TRUE;
    setsockopt(ls, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&excl), sizeof excl);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, SOMAXCONN) != 0) {
        log::Error("server: cannot listen on 127.0.0.1:" + std::to_string(port) + " (WSA error " +
                   std::to_string(WSAGetLastError()) + "); is another game instance running?");
        closesocket(ls);
        return false;
    }
    SetNonBlocking(ls);
    auto* server = new Server(ls, std::move(token), std::move(cb), outbox);  // lives until process exit
    std::thread([server] { server->Run(); }).detach();
    log::Info("server: listening on 127.0.0.1:" + std::to_string(port));
    return true;
}

std::uint32_t ProcessId() { return GetCurrentProcessId(); }

std::string RandomToken()
{
    unsigned char bytes[16];
    BCryptGenRandom(nullptr, bytes, sizeof bytes, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    static constexpr char hex[] = "0123456789abcdef";
    std::string out;
    for (unsigned char b : bytes) {
        out.push_back(hex[b >> 4]);
        out.push_back(hex[b & 0xF]);
    }
    return out;
}

bool WriteSessionFile(const std::filesystem::path& path, const json& content)
{
    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);
    auto tmp = path;
    tmp += ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) {
            log::Error("server: cannot write " + tmp.string());
            return false;
        }
        f << content.dump(2);
    }
    if (!MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        log::Error("server: cannot move session file into place: error " + std::to_string(GetLastError()));
        return false;
    }
    log::Info("server: session file " + path.string());
    return true;
}
}
```

- [ ] **Step 2: Start the server from `main.cpp`**

Replace `plugin/src/main.cpp` with (the stub request handler answers `unsupported` until Task 17):
```cpp
#include "Lifecycle.h"
#include "Log.h"
#include "Server.h"
#include "Settings.h"
#include "Wire.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/basic_file_sink.h>

using namespace std::literals;

SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "SkyrimPython"sv,
    .Author = "skyrim-python"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace {
spy::Outbox g_outbox;

void SetupLog()
{
    auto path = SKSE::log::log_directory();
    *path /= "SkyrimPython.log"sv;
    auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(path->string(), true);
    auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
    logger->set_level(spdlog::level::info);
    logger->flush_on(spdlog::level::info);
    spdlog::set_default_logger(std::move(logger));
}

spy::WelcomeInfo Welcome()
{
    return {SPY_VERSION, REL::Module::get().version().string(), REL::Module::IsAE() ? "AE" : "SE",
            spy::life::Epoch(), spy::known_ops()};
}

void StartServer()
{
    const auto& cfg = spy::settings::Get();
    if (!cfg.allow_external_clients) {
        spy::log::Info("server: allow_external_clients=false; not listening");
        return;
    }
    const auto token = cfg.fixed_token.empty() ? spy::net::RandomToken() : cfg.fixed_token;
    spy::net::Callbacks cb;
    cb.onRequest = [](spy::ConnId conn, spy::Request req) {
        g_outbox.post(conn, spy::Frame(spy::make_error(req.id, spy::ErrorCode::unsupported, "dispatcher not built yet")));
    };
    cb.welcomeInfo = Welcome;
    if (!spy::net::Start(cfg.port, token, std::move(cb), g_outbox)) return;
    spy::net::WriteSessionFile(spy::settings::SessionFile(*SKSE::log::log_directory()),
        spy::make_session_file(cfg.port, token, spy::net::ProcessId(), SPY_VERSION));
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    spy::life::OnMessage(m);
    if (m->type == SKSE::MessagingInterface::kPostLoad) StartServer();
}
}

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    SKSE::Init(skse);
    SetupLog();
    spy::log::Info(std::format("SkyrimPython {} loading on Skyrim {} ({})", SPY_VERSION,
        REL::Module::get().version().string(), REL::Module::IsAE() ? "AE" : "SE"));
    spy::settings::Load();
    SKSE::GetMessagingInterface()->RegisterListener(OnMessage);
    return true;
}
```
(`ProcessId()` lives in `Server.cpp` so that `main.cpp` never includes `<windows.h>` alongside CommonLib.)

- [ ] **Step 3: Live-test harness and transport tests**

`livetest/conftest.py`:
```python
"""Live tests: need the game running with SkyrimPython.dll and the dev ini.
Run from the repo root:  source tools/env.sh && uv run --project python pytest livetest -q
"""
import pytest

import skyrim


@pytest.fixture(scope="session")
def session():
    return skyrim.find_session()


@pytest.fixture
def game(session):
    g = skyrim.connect(port=session.port, token=session.token, client_name="livetest")
    yield g
    g.close()
```

`livetest/support.py` (helpers; test modules import it directly, as `python/tests` does):
```python
import socket
import struct
import time

from skyrim import _framing


def raw_connect(session) -> socket.socket:
    s = socket.create_connection(("127.0.0.1", session.port), timeout=5)
    s.sendall(_framing.encode({"type": "hello", "protocol": [1, 0], "token": session.token, "client": "livetest-raw"}))
    assert _framing.read_frame(s)["type"] == "welcome"
    return s


def send_raw(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def closed_by_server(sock: socket.socket, timeout: float = 3.0) -> bool:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if sock.recv(65536) == b"":
                return True
    except (ConnectionResetError, ConnectionAbortedError):
        return True
    except socket.timeout:
        return False
    return False


def frame_header(n: int) -> bytes:
    return struct.pack("<I", n)
```

`livetest/test_m1_transport.py`:
```python
import socket

import pytest

import skyrim
from support import closed_by_server, frame_header, raw_connect, send_raw
from skyrim import _framing


def test_connect_and_welcome(session):
    with skyrim.connect(port=session.port, token=session.token) as g:
        assert g.welcome["protocol"][0] == 1
        assert "papyrus.call_static" in g.welcome["capabilities"]


def test_wrong_token_is_unauthorized(session):
    with pytest.raises(skyrim.Unauthorized):
        skyrim.connect(port=session.port, token="0" * 32)


def test_garbage_frame_closes_only_that_connection(session):
    bad = raw_connect(session)
    body = b"this is not json"
    send_raw(bad, frame_header(len(body)) + body)
    assert closed_by_server(bad)
    with skyrim.connect(port=session.port, token=session.token):
        pass


def test_oversized_header_closes_connection(session):
    bad = raw_connect(session)
    send_raw(bad, frame_header(_framing.MAX_FRAME + 1))
    assert closed_by_server(bad)


def test_invalid_request_gets_error_and_connection_stays_open(session):
    s = raw_connect(session)
    s.sendall(_framing.encode({"type": "request", "id": 5, "op": "no.such.op", "args": {}}))
    reply = _framing.read_frame(s)
    assert reply["id"] == 5 and reply["ok"] is False and reply["error"]["code"] == "bad_request"
    s.sendall(_framing.encode({"type": "request", "id": 6, "op": "no.such.op", "args": {}}))
    assert _framing.read_frame(s)["id"] == 6


def test_silent_client_is_dropped_after_hello_timeout(session):
    s = socket.create_connection(("127.0.0.1", session.port), timeout=10)
    assert closed_by_server(s, timeout=8.0)
```

- [ ] **Step 4: Build, deploy, run**

```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini
tools/launch_game.sh
source tools/env.sh
until [ -f "$SKYRIM_PYTHON_SESSION" ] && grep -q "listening on 127.0.0.1:45977" "$SKSE_LOG_DIR/SkyrimPython.log"; do sleep 2; done
uv run --project python pytest livetest/test_m1_transport.py -q
```
Expected: 6 passed (at the main menu is fine; no game needs to be loaded). The log shows `handshake refused (unauthorized)`, `not a JSON object; closing`, `over 16 MiB; closing`, `sent no hello within 5 s; closing`.

- [ ] **Step 5: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): loopback server with token handshake and session file"
```
