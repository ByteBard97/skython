cmake_minimum_required(VERSION 3.21)
project(spy_core LANGUAGES CXX)

file(GLOB SPY_CORE_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/*.cpp)
add_library(spy_core STATIC ${SPY_CORE_SOURCES})
target_include_directories(spy_core PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/../third_party)
target_compile_features(spy_core PUBLIC cxx_std_20)
# The Linux build of the _skython extension module links this static library into a shared object.
set_target_properties(spy_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
find_package(Threads REQUIRED)
target_link_libraries(spy_core PUBLIC Threads::Threads)

option(SPY_CORE_TESTS "Build core unit tests" ON)
if(SPY_CORE_TESTS)
  enable_testing()
  file(GLOB SPY_CORE_TEST_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cpp)
  add_executable(spy_core_tests ${SPY_CORE_TEST_SOURCES})
  target_link_libraries(spy_core_tests PRIVATE spy_core)
  # One test file puts raw string literals inside doctest macros; MSVC's legacy preprocessor cannot parse that.
  if(MSVC)
    target_compile_options(spy_core_tests PRIVATE /Zc:preprocessor)
  endif()
  target_compile_definitions(spy_core_tests PRIVATE
    SPY_FIXTURES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../protocol/v1/fixtures")
  add_test(NAME spy_core_tests COMMAND spy_core_tests)
  set_tests_properties(spy_core_tests PROPERTIES TIMEOUT 300)  # a lock regression in the on_wanted test hangs
endif()
