# Linux build of the _skython extension module with the fake backend. The game build of the same module
# source is part of the plugin core.
cmake_minimum_required(VERSION 3.21)
project(skython_pyglue_linux LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Pass -DPython_EXECUTABLE="$(uv python find 3.14)" so the module matches the interpreter the tests run on.
find_package(Python 3.14 REQUIRED COMPONENTS Interpreter Development.Module)

set(SPY_CORE_TESTS OFF CACHE BOOL "core unit tests are built from core/ itself" FORCE)
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../core ${CMAKE_CURRENT_BINARY_DIR}/core)

Python_add_library(_skython MODULE WITH_SOABI ../src/pymodule.cpp fake_module.cpp)
target_include_directories(_skython PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/../include
  ${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/pybind11/include)
target_link_libraries(_skython PRIVATE spy_core)
set_target_properties(_skython PROPERTIES CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
