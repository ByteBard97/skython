#include "ConsoleEcho.h"
#include "Log.h"
#include "Ops.h"
#include "spy/console_echo.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <deque>

namespace spy::console {
namespace {
// Lines that arrived before the console existed. Touched only inside our SKSE tasks (serialized, observed: R49).
std::deque<std::string>& Held() { static auto* d = new std::deque<std::string>; return *d; }

// Inside an SKSE task only.
void PrintHeld(RE::ConsoleLog* c)
{
    while (!Held().empty()) {
        c->Print("%s", Held().front().c_str());
        log::Info("console: printed " + Held().front() + " (held until the console existed)");
        Held().pop_front();
    }
}

void PrintInTask(const std::string& line)
{
    SKSE::GetTaskInterface()->AddTask([line] {
        ops::InTask in;
        ops::Measured measured;
        try {
            if (auto* c = RE::ConsoleLog::GetSingleton()) {
                PrintHeld(c);
                c->Print("%s", line.c_str());  // never the line as the format string
                log::Info("console: printed " + line);
            } else if (Held().size() < 5) {
                Held().push_back(line);
                log::Info("console: no console yet; held: " + line);
            } else {
                log::Warn("console: no console yet and 5 lines held; dropped: " + line);
            }
        } catch (...) {
            log::Error("console: printing threw");
        }
    });
}

spy::ConsoleEcho& Limiter()
{
    static auto* echo = new spy::ConsoleEcho(PrintInTask);  // immortal: its worker may run during process exit
    return *echo;
}
}

void Echo(std::string message) { Limiter().Echo(std::move(message)); }

void FlushHeld() noexcept
{
    try {
        SKSE::GetTaskInterface()->AddTask([] {
            ops::InTask in;
            ops::Measured measured;
            try {
                if (auto* c = RE::ConsoleLog::GetSingleton()) PrintHeld(c);
                else if (!Held().empty()) log::Warn("console: still no console at the main menu; lines stay held");
            } catch (...) {
                log::Error("console: printing held lines threw");
            }
        });
    } catch (...) {}
}
}
