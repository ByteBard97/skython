#include "Net.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <chrono>
#include <exception>
#include <thread>

namespace spike {
namespace {
void SendAll(SOCKET c, const std::string& s)
{
    std::size_t off = 0;
    while (off < s.size()) {
        const int n = send(c, s.data() + off, static_cast<int>(s.size() - off), 0);
        if (n <= 0) return;
        off += static_cast<std::size_t>(n);
    }
}
}

void StartListener(std::uint16_t port, std::function<std::string(const std::string&)> handler)
{
    std::thread([port, handler] {
        WSADATA wsa;
        if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
            Log("spike: WSAStartup failed");
            return;
        }
        SOCKET ls = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (ls == INVALID_SOCKET) {
            Log("spike: socket() failed, WSA error " + std::to_string(WSAGetLastError()));
            return;
        }
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(port);
        inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
        if (bind(ls, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0 || listen(ls, 4) != 0) {
            Log("spike: bind/listen failed, WSA error " + std::to_string(WSAGetLastError()));
            closesocket(ls);
            return;
        }
        Log("spike: listening on 127.0.0.1:" + std::to_string(port));
        int acceptFailures = 0;
        for (;;) {
            SOCKET c = accept(ls, nullptr, nullptr);
            if (c == INVALID_SOCKET) {
                // Do not spin: back off, and give up if accept keeps failing.
                if (++acceptFailures >= 50) { Log("spike: accept keeps failing, listener stopping"); break; }
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
                continue;
            }
            acceptFailures = 0;
            Log("spike: client connected");
            std::string buf;
            char tmp[4096];
            for (;;) {
                int n = recv(c, tmp, sizeof tmp, 0);
                if (n <= 0) break;
                buf.append(tmp, n);
                for (auto pos = buf.find('\n'); pos != std::string::npos; pos = buf.find('\n')) {
                    std::string line = buf.substr(0, pos);
                    buf.erase(0, pos + 1);
                    std::string reply;
                    try {
                        reply = handler(line);
                    } catch (const std::exception& e) {
                        reply = std::string("ERR exception: ") + e.what();
                    } catch (...) {
                        reply = "ERR unknown exception";
                    }
                    reply += "\n";
                    SendAll(c, reply);
                }
            }
            closesocket(c);
            Log("spike: client disconnected");
        }
        closesocket(ls);
    }).detach();
}
}
