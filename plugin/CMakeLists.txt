cmake_minimum_required(VERSION 3.21)
project(Skython VERSION 0.1.0 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(SPY_TEST_HOOKS "Loader honours SKYTHON_TEST_PRELOAD and SKYTHON_TEST_UNVERIFIED_RUNTIME (live tests only)" OFF)

set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/extern/CommonLibSSE-NG commonlibsse-ng)
set(SPY_CORE_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../core spy_core)

set(SPY_ROOT ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(SPY_CPYTHON ${SPY_ROOT}/third_party/cpython/nuget/tools)
if(NOT EXISTS ${SPY_CPYTHON}/libs/python314.lib)
  message(FATAL_ERROR "${SPY_CPYTHON}/libs/python314.lib is missing: run tools/fetch_python.sh on Linux, then sync again")
endif()
set(SPY_DEFS SPY_VERSION="${PROJECT_VERSION}" WIN32_LEAN_AND_MEAN NOMINMAX)

# Loader: SKSE-facing, no Python dependency at all.
add_library(Skython SHARED loader/main.cpp loader/Win.cpp)
target_compile_definitions(Skython PRIVATE ${SPY_DEFS} $<$<BOOL:${SPY_TEST_HOOKS}>:SPY_TEST_HOOKS>)
target_link_libraries(Skython PRIVATE CommonLibSSE::CommonLibSSE)

# Core: links python314.lib normally; everything that touches Python, pybind11 or the game lives here.
set(SPY_CORE_SOURCES
  src/main.cpp src/Log.cpp src/Settings.cpp src/Lifecycle.cpp src/PythonHost.cpp
  ${SPY_ROOT}/pyglue/src/pymodule.cpp)
add_library(SkythonCore SHARED ${SPY_CORE_SOURCES})
target_compile_definitions(SkythonCore PRIVATE ${SPY_DEFS})
target_include_directories(SkythonCore PRIVATE
  ${SPY_ROOT}/pyglue/include ${SPY_ROOT}/third_party/pybind11/include ${SPY_CPYTHON}/include)
target_link_directories(SkythonCore PRIVATE ${SPY_CPYTHON}/libs)   # pyconfig.h's #pragma comment(lib, ...)
target_link_libraries(SkythonCore PRIVATE CommonLibSSE::CommonLibSSE spy_core ${SPY_CPYTHON}/libs/python314.lib)
