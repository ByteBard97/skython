#include "spy/pymodule.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>
#include <pybind11/stl.h>

namespace py = pybind11;

namespace spy {
namespace {
ModuleHost& Host()
{
    static ModuleHost host;
    return host;
}

Dispatcher& Disp()
{
    if (Host().dispatcher == nullptr) throw std::runtime_error("_skyrim: no dispatcher installed (InstallModuleHost was not called)");
    return *Host().dispatcher;
}

// Seconds (Python float) to milliseconds, rounded up. Negative or NaN is a ValueError; longer than a week is clamped.
std::chrono::milliseconds ToMs(double seconds)
{
    if (std::isnan(seconds) || seconds < 0.0) throw std::invalid_argument("timeout must be a number of seconds, 0 or more");
    constexpr double kMaxSeconds = 7.0 * 24 * 3600;
    return std::chrono::milliseconds(static_cast<std::int64_t>(std::ceil(std::min(seconds, kMaxSeconds) * 1000.0)));
}

// The reply call() gives when its own wait runs out before the dispatcher answered: always the timeout class.
std::string LateCancelReply(std::string_view request, double seconds)
{
    json detail = json::object();
    try {
        const json base = Disp().BaseState();
        detail["paused"] = base.value("paused", false);
        detail["menus"] = base.value("menus", json::array());
    } catch (...) {
        detail["paused"] = false;
        detail["menus"] = json::array();
    }
    detail["late_cancel"] = true;
    char secs[32];
    std::snprintf(secs, sizeof secs, "%g", seconds);  // 0.2, not 0.200000
    return dump_json(make_error(peek_request_id(request).value_or(0), ErrorCode::timeout,
                                std::string("no response within ") + secs + " s; the call was cancelled", std::move(detail)));
}
}

void InstallModuleHost(ModuleHost host)
{
    Host() = std::move(host);
}

void DefineModule(py::module_& m)
{
    const ModuleHost& h = Host();
    m.doc() = "skyrim-python's boundary between Python and the game: request and response JSON text (spec 4.2).";
    m.attr("BACKEND") = std::string(h.backend != nullptr ? h.backend : "game");
    m.attr("PROTOCOL") = py::make_tuple(kProtocolMajor, kProtocolMinor);

    // Arguments are converted before the guard releases the GIL and results after it is taken back, so only the C++
    // work in between runs without the GIL.
    using release = py::call_guard<py::gil_scoped_release>;

    m.def("submit", [](const std::string& request) { return Disp().Submit(request); }, py::arg("request"), release(),
          "Submit request text; returns a ticket. Never blocks on the game.");
    m.def("wait", [](Ticket ticket, double timeout) { return Disp().Wait(ticket, ToMs(timeout)); },
          py::arg("ticket"), py::arg("timeout"), release(),
          "Response text, or None if not finished within timeout seconds. A returned ticket is consumed.");
    m.def("wait_any", [](const std::vector<Ticket>& tickets, double timeout) { return Disp().WaitAny(tickets, ToMs(timeout)); },
          py::arg("tickets"), py::arg("timeout"), release(),
          "[(ticket, response text)] for the finished ones, as soon as one is finished or timeout seconds pass.");
    m.def("cancel", [](Ticket ticket) { Disp().Cancel(ticket); }, py::arg("ticket"), release(),
          "Forget a ticket; a late result is dropped.");
    m.def("call",
          [](const std::string& request, double timeout) -> std::string {
              const auto ms = ToMs(timeout);
              Dispatcher& d = Disp();
              const Ticket t = d.Submit(request);
              if (auto text = d.Wait(t, ms)) return std::move(*text);
              // A result that landed between the end of Wait and now is returned, not thrown away.
              if (auto text = d.CancelOrTake(t)) return std::move(*text);
              return LateCancelReply(request, timeout);
          },
          py::arg("request"), py::arg("timeout"), release(),
          "submit + wait. If timeout seconds pass first, the call is cancelled (unless its result has just arrived) and a "
          "timeout response with detail.late_cancel = true is returned.");
    m.def("state", [] { return dump_json(Disp().BaseState()); }, release(), "The lifecycle state as JSON text; never blocks.");
    m.def("config", [] { return Host().config_json ? Host().config_json() : std::string("{}"); }, release(),
          "The plugin's settings and paths as JSON text.");
    m.def("log", [](const std::string& level, const std::string& message) { if (Host().log) Host().log(level, message); },
          py::arg("level"), py::arg("message"), release(), "Write one line to the plugin log.");
    m.def("in_flight", [] { return Disp().InFlight(); }, release(), "Calls submitted and not yet finished.");

    if (h.fake != nullptr) {
        FakeBackend* fake = h.fake;
        m.def("_fake_set_not_ready", [fake](std::optional<std::string> reason) { fake->SetNotReady(std::move(reason)); },
              py::arg("reason"), "Fake build only: 'starting', 'loading', 'no_game', or None for ready.");
        m.def("_fake_bump_loads", [fake] { fake->BumpLoads(); }, "Fake build only: a save starts loading.");
        m.def("_fake_set_paused", [fake](bool paused) { fake->SetPaused(paused); }, py::arg("flag"), "Fake build only.");
    }
}
}
