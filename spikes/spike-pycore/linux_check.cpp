// Linux check of SpikePyCore's exports against uv's CPython 3.14 (Task 3 Step 2). Not part of any DLL.
// Usage: linux_check <python prefix>   Exit 0 and "LINUX CHECK PASS" when every check holds.
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

extern "C" {
void SpikePy_SetLog(void (*)(const char*));
void SpikePy_SetCaller(int (*)(int, double*));
int SpikePy_Start(const wchar_t*, const wchar_t* const*, int);
int SpikePy_Status(char*, int);
int SpikePy_Run(const char*, char*, int);
}

static int g_failures = 0;
static void LogFn(const char* s) { std::printf("  log: %s\n", s); }
static int FakeCaller(int, double* out)  // stands in for the game: 2 ms per call, value 1.5
{
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    *out = 1.5;
    return 0;
}
static std::string Run(const char* code)
{
    static char buf[65536];
    SpikePy_Run(code, buf, sizeof buf);
    return buf;
}
static void Expect(const char* what, const std::string& got, const char* prefix)
{
    const bool ok = got.rfind(prefix, 0) == 0;
    if (!ok) ++g_failures;
    std::printf("%s %s: %.120s\n", ok ? "ok  " : "FAIL", what, got.c_str());
}

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const std::wstring home(argv[1], argv[1] + std::strlen(argv[1]));
    const std::wstring lib = home + L"/lib/python3.14", dyn = lib + L"/lib-dynload";
    const wchar_t* paths[] = { lib.c_str(), dyn.c_str() };
    SpikePy_SetLog(LogFn);
    Expect("run before start", Run("result = 1"), "ERR python is not running");
    const int first = SpikePy_Start(home.c_str(), paths, 2);
    const int second = SpikePy_Start(home.c_str(), paths, 2);
    Expect("start once", std::to_string(first) + std::to_string(second), "10");
    char st[256] = "";
    for (int i = 0; i < 250; ++i) {
        SpikePy_Status(st, sizeof st);
        if (std::strcmp(st, "starting") != 0 && std::strcmp(st, "not_started") != 0) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    Expect("status", st, "running");
    Expect("isolated, site, stdout to log",
           Run("import json, ssl, socket, sqlite3, threading, ctypes, sys\n"
               "result = json.dumps([sys.flags.isolated, sys.flags.ignore_environment, 'site' in sys.modules, type(sys.stdout).__name__])"),
           "OK [1, 1, true, \"_SpikeLogWriter\"]");
    Expect("call_rt without a caller", Run("import _spike\n_spike.call_rt(10)"), "ERR Traceback");
    SpikePy_SetCaller(FakeCaller);
    Expect("8 threads x 100 call_rt",
           Run("import _spike, threading\nout = []\n"
               "def w():\n    for _ in range(100): out.append(_spike.call_rt(1000))\n"
               "ts = [threading.Thread(target=w) for _ in range(8)]\n[t.start() for t in ts]\n[t.join() for t in ts]\n"
               "result = (len(out), sorted(set(out)))"),
           "OK (800, [1.5])");
    Run("keep = 7");
    Expect("namespace persists", Run("result = keep * 6"), "OK 42");
    Expect("exception is a reply", Run("1/0"), "ERR Traceback");
    Expect("SystemExit is a reply", Run("raise SystemExit(5)"), "ERR Traceback");
    Expect("still running", Run("result = 'alive'"), "OK alive");
    std::printf(g_failures ? "LINUX CHECK FAIL (%d)\n" : "LINUX CHECK PASS\n", g_failures);
    return g_failures ? 1 : 0;  // returns while the host thread is still blocked: exit must not hang
}
