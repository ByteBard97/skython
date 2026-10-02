// Force-included (-include) into the ThreadSanitizer build only.
// GCC 11's libstdc++ waits on a condition_variable with a steady_clock deadline through pthread_cond_clockwait, which
// GCC 11's libtsan does not intercept. TSan then misses the unlock inside the wait and reports false "double lock of a
// mutex" and "data race" warnings for every timed wait. Turning the macro off makes libstdc++ use
// pthread_cond_timedwait, which libtsan does intercept. Production builds never include this file.
#pragma once
#include <bits/c++config.h>
#undef _GLIBCXX_USE_PTHREAD_COND_CLOCKWAIT
