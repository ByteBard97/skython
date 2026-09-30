# Task 8: Core library scaffold (Linux build and test runner)

> Part of the [skyrim-python v1 foundation plan](README.md). Before starting, read the plan index's **Global Constraints** and **Fixed paths**; they apply to this task. Spec: `docs/specs/2026-09-30-skyrim-python-design.md`. Steps use checkbox (`- [ ]`) syntax for tracking.

> **M1 gate:** Start only after `spikes/RESULTS.md` shows S1 PASS and the spec has been updated for any other FAIL.


**Files:**
- Create: `core/CMakeLists.txt`, `core/tests/main.cpp`

**Interfaces:**
- Produces: CMake target `spy_core` (static library, include dir `core/include` + `third_party`), test executable `spy_core_tests`, compile definition `SPY_FIXTURES_DIR` pointing at `protocol/v1/fixtures`.

- [ ] **Step 1: Write the CMake file and test main**

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

Create an empty placeholder source so the library has one translation unit until Task 9: `core/src/version.cpp` with
```cpp
namespace spy { const char* core_version() { return "0.1.0"; } }
```

- [ ] **Step 2: Build and run the (empty) test suite**

Run: `cmake -S core -B core/build -DCMAKE_BUILD_TYPE=Debug && cmake --build core/build -j && ctest --test-dir core/build --output-on-failure`
Expected: build succeeds; `100% tests passed, 0 tests failed out of 1`.

- [ ] **Step 3: Commit**

```bash
git add core && git commit -m "build(core): pure C++20 core library scaffold with doctest"
```
