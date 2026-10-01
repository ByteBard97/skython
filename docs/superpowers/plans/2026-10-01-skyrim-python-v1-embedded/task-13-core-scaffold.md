# Task 13: Core library scaffold

Adapted from: branch `plan-out-of-process`, task 8 (`task-08-core-library-scaffold-linux-build-and-test.md`).

> Part of the [skyrim-python v1 plan](README.md). Before starting, read the plan index's **Global Constraints**, **File Structure** and **Contracts**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4, section 8). Steps use checkbox (`- [ ]`) syntax for tracking.

> **Depends on:** Task 0 (`third_party/nlohmann/json.hpp`, `third_party/doctest/doctest.h`) and Task 12 (`core/include/spy/protocol_generated.h`, committed by the generator). This Linux task needs no game and may run in parallel with the M0 spikes.

**What changed from the out-of-process plan.** Nothing in substance. The core library is still pure C++20 with no Windows, CommonLib or Python headers. It is now linked into the plugin's core DLL (Task 25) and into the Linux build of the `_skyrim` module (Task 19), so it must stay free of anything platform-specific. The old C++ framing task (old task 9) is gone: the dev bridge is Python (Task 23).

**Files:**
- Create: `core/CMakeLists.txt`, `core/tests/main.cpp`, `core/src/version.cpp`

**Interfaces:**
- Consumes: `third_party/nlohmann/json.hpp`, `third_party/doctest/doctest.h` (Task 0); `core/include/spy/protocol_generated.h` (Task 12).
- Produces: CMake target `spy_core` (static library; include dirs `core/include` and `third_party`; C++20; position-independent code so the Linux `_skyrim` extension module in Task 19 can link it), test executable `spy_core_tests`, compile definition `SPY_FIXTURES_DIR` pointing at `protocol/v1/fixtures`. Tasks 14-18 add sources under `core/src/` and tests under `core/tests/`; both directories are globbed with `CONFIGURE_DEPENDS`, so they need no CMake edits, only a re-configure.

- [ ] **Step 1: Write the CMake file, the test main and a placeholder source**

`core/CMakeLists.txt`:
```cmake
cmake_minimum_required(VERSION 3.21)
project(spy_core LANGUAGES CXX)

file(GLOB SPY_CORE_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
add_library(spy_core STATIC ${SPY_CORE_SOURCES})
target_include_directories(spy_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/../third_party)
target_compile_features(spy_core PUBLIC cxx_std_20)
# The Linux build of the _skyrim extension module (Task 19) links this static library into a shared object.
set_target_properties(spy_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
find_package(Threads REQUIRED)
target_link_libraries(spy_core PUBLIC Threads::Threads)

option(SPY_CORE_TESTS "Build core unit tests" ON)
if(SPY_CORE_TESTS)
  enable_testing()
  file(GLOB SPY_CORE_TEST_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cpp)
  add_executable(spy_core_tests ${SPY_CORE_TEST_SOURCES})
  target_link_libraries(spy_core_tests PRIVATE spy_core)
  target_compile_definitions(spy_core_tests PRIVATE
    SPY_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../protocol/v1/fixtures")
  add_test(NAME spy_core_tests COMMAND spy_core_tests)
endif()
```

`core/tests/main.cpp`:
```cpp
#define DOCTEST_CONFIG_IMPLEMENT_WITH_MAIN
#include "doctest/doctest.h"
```

`core/src/version.cpp` (one translation unit so the library builds before Task 14 adds real sources):
```cpp
namespace spy {
const char* core_version() { return "0.1.0"; }
}
```

The core links `Threads::Threads` from the start because Task 18's dispatcher runs a reaper thread and its fake backend a worker thread; declaring it here keeps every later task free of CMake edits.

- [ ] **Step 2: Build and run the (empty) test suite**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug && cmake --build core/build -j && ctest --test-dir core/build --output-on-failure && core/build/spy_core_tests`
Expected: the build succeeds; `100% tests passed, 0 tests failed out of 1`; the test binary prints `[doctest] test cases: 0 | 0 passed | 0 failed | 0 skipped` and `[doctest] assertions: 0 | 0 passed | 0 failed`. From Task 14 on, the plan runs `core/build/spy_core_tests` directly because its summary line carries the test and assertion counts that each task states.

Also check the generated header compiles on its own inside the library's include path: `echo '#include "spy/protocol_generated.h"' | g++ -std=c++20 -fsyntax-only -Icore/include -x c++ -`
Expected: no output, exit status 0.

`core/build/` is ignored by the repository's `.gitignore` (`*/build/`); nothing under it is committed.

- [ ] **Step 3: Commit**

```bash
git add core/CMakeLists.txt core/tests/main.cpp core/src/version.cpp
git commit -m "build(core): pure C++20 core library scaffold with doctest"
```
</content>
</invoke>
<invoke name="Bash">
<parameter name="command">W=/tmp/claude-1000/-home-geoff-projects-games-skyrim-skyrim-python/deae5291-8793-4315-a833-6e1a352deb09/scratchpad/w13; S=$W/../repo; P=/home/geoff/projects/games/skyrim/skyrim-python/docs/superpowers/plans/2026-10-01-skyrim-python-v1-embedded; cd $S && python3 $W/extract.py $P/task-13-core-scaffold.md . && cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug >/dev/null && cmake --build core/build -j 2>&1 | tail -2 && ctest --test-dir core/build --output-on-failure | tail -3 && core/build/spy_core_tests | tail -3; echo '#include "spy/protocol_generated.h"' | g++ -std=c++20 -fsyntax-only -Icore/include -x c++ - && echo hdr-ok</parameter>
<parameter name="description">Apply and verify Task 13 in scratch repo</parameter>
</invoke>
