cmake_minimum_required(VERSION 3.21)
project(SkyrimPythonSpike VERSION 0.0.1 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(BUILD_TESTS OFF CACHE BOOL "" FORCE)
# Use the option names recorded in docs/PINS.md (Task 0); VR off.
set(ENABLE_SKYRIM_VR OFF CACHE BOOL "" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../plugin/extern/CommonLibSSE-NG commonlibsse-ng)
add_library(SkyrimPythonSpike SHARED src/main.cpp src/Net.cpp src/PyLoad.cpp)
# NOMINMAX: windows.h's min/max macros break std::min/std::max. WIN32_LEAN_AND_MEAN keeps winsock2.h before windows.h.
target_compile_definitions(SkyrimPythonSpike PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(SkyrimPythonSpike PRIVATE CommonLibSSE::CommonLibSSE ws2_32)
