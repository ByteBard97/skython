#include "Events.h"
#include "GameBackend.h"
#include "Lifecycle.h"
#include "Log.h"
#include "PythonHost.h"
#include "Settings.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <spdlog/sinks/base_sink.h>
#include <filesystem>
#include <format>
#include <fstream>
#include <mutex>

using namespace std::literals;

// The core is never loaded by SKSE itself (it sits in Plugins/Skython/), but CommonLib looks up this
// declaration in the calling module (Step 2), so it is declared here as well.
SKSEPluginInfo(
    .Version = { 0, 1, 0, 0 },
    .Name = "SkythonCore"sv,
    .Author = "Skython"sv,
    .StructCompatibility = SKSE::StructCompatibility::Independent,
    .RuntimeCompatibility = SKSE::VersionIndependence::AddressLibrary)

namespace {
// spdlog's file sinks take a narrow file name, which Windows reads in the ANSI code page; a Documents folder whose
// name that code page cannot spell would get no log. This sink opens the std::filesystem::path itself (wide).
class PathFileSink final : public spdlog::sinks::base_sink<std::mutex>
{
public:
    explicit PathFileSink(const std::filesystem::path& p) : out_(p, std::ios::binary | std::ios::trunc)
    {
        if (!out_) throw std::runtime_error("cannot open the log file");
    }

protected:
    void sink_it_(const spdlog::details::log_msg& msg) override
    {
        spdlog::memory_buf_t buf;
        formatter_->format(msg, buf);
        out_.write(buf.data(), static_cast<std::streamsize>(buf.size()));
    }
    void flush_() override { out_.flush(); }

private:
    std::ofstream out_;
};

void SetupLog(const std::optional<std::filesystem::path>& skseDir)
{
    try {
        std::filesystem::path path = skseDir ? *skseDir : std::filesystem::path{};
        path /= "Skython.log"sv;
        auto sink = std::make_shared<PathFileSink>(path);
        auto logger = std::make_shared<spdlog::logger>("global", std::move(sink));
        logger->set_level(spdlog::level::info);
        logger->flush_on(spdlog::level::info);
        spy::log::Set(std::move(logger));
    } catch (const std::exception&) {
        spy::log::Set(std::make_shared<spdlog::logger>("none"));  // no sinks: logging becomes a no-op
    }
}

void OnMessage(SKSE::MessagingInterface::Message* m)
{
    try {
        spy::life::OnMessage(m);
        if (m->type == SKSE::MessagingInterface::kDataLoaded) {
            spy::game::GetDispatcher();      // the dispatcher (and through it the hub) exists first
            spy::events::RegisterSinks();    // before Python starts: no subscription can race the sink setup (Task 44 relies on it)
            spy::host::Start(spy::settings::Get());
        }
    } catch (const std::exception& e) {
        spy::log::Error(std::string("message handler threw: ") + e.what());
    } catch (...) {
        spy::log::Error("message handler threw");
    }
}
}

extern "C" __declspec(dllexport) bool SpyCore_Load(const SKSE::LoadInterface* skse)
{
    try {
        SKSE::Init(skse, { .log = false });
        const auto skseDir = SKSE::log::log_directory();
        SetupLog(skseDir);
        if (!skseDir) spy::log::Error("SKSE could not resolve the Documents folder; logging to the game folder");
        spy::settings::SetRuntime({ SPY_VERSION, REL::Module::get().version().string("."),
                                    REL::Module::IsAE() ? "AE" : "SE", skseDir });
        spy::log::Info(std::format("SkythonCore {} on Skyrim {} ({}); linked against CPython {}", SPY_VERSION,
            spy::settings::GetRuntime().game_version, spy::settings::GetRuntime().runtime,
            spy::host::LinkedPythonVersion()));
        spy::settings::Load();
        auto* messaging = SKSE::GetMessagingInterface();
        if (!messaging || !messaging->RegisterListener(OnMessage)) {
            spy::log::Error("messaging listener registration failed");
            return false;
        }
        return true;
    } catch (...) {
        return false;  // the loader logs "SpyCore_Load returned false"
    }
}
