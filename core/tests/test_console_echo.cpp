#include "doctest/doctest.h"
#include "spy/console_echo.h"
#include <atomic>
#include <chrono>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

namespace {
struct Lines
{
    std::mutex mx;
    std::vector<std::string> v;
    void add(const std::string& s) { std::lock_guard lk(mx); v.push_back(s); }
    std::vector<std::string> get() { std::lock_guard lk(mx); return v; }
};
}

TEST_CASE("console echo: a burst of 5, then one summary when the window reopens")
{
    Lines out;
    spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 5, 100ms });
    for (int i = 0; i < 20; ++i) echo.Echo("err " + std::to_string(i));
    std::this_thread::sleep_for(50ms);
    CHECK(out.get().size() == 5);
    std::this_thread::sleep_for(250ms);
    const auto lines = out.get();
    REQUIRE(lines.size() == 6);
    CHECK(lines[0] == "err 0");
    CHECK(lines[4] == "err 4");
    CHECK(lines[5] == "Skython: 15 more script errors; see python.log");
    CHECK(echo.Printed() == 6);
    CHECK(echo.Suppressed() == 15);
}

TEST_CASE("console echo: steady errors below the rate all print")
{
    Lines out;
    spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 1, 20ms });
    for (int i = 0; i < 5; ++i) { echo.Echo("e"); std::this_thread::sleep_for(40ms); }
    std::this_thread::sleep_for(50ms);
    CHECK(out.get().size() == 5);
    CHECK(echo.Suppressed() == 0);
}

TEST_CASE("console echo: Echo never waits for a slow sink and never throws")
{
    std::atomic<int> calls{ 0 };
    spy::ConsoleEcho echo([&](const std::string&) { ++calls; std::this_thread::sleep_for(200ms); throw std::runtime_error("sink"); }, { 5, 1000ms });
    const auto t0 = std::chrono::steady_clock::now();
    for (int i = 0; i < 1000; ++i) echo.Echo("x");
    CHECK(std::chrono::steady_clock::now() - t0 < 100ms);
    std::this_thread::sleep_for(300ms);
    CHECK(calls.load() >= 1);  // the throwing sink did not stop the worker
}

TEST_CASE("console echo: long lines are cut, the destructor stops the worker")
{
    Lines out;
    {
        spy::ConsoleEcho echo([&](const std::string& l) { out.add(l); }, { 5, 100ms, 16 });
        echo.Echo(std::string(100, 'a'));
        std::this_thread::sleep_for(50ms);
    }
    REQUIRE(out.get().size() == 1);
    CHECK(out.get()[0] == std::string(16, 'a'));
}
