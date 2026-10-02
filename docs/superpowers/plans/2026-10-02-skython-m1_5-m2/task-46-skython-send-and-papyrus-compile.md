# Task 46: `Skython.Send` native, `Skython.psc`/`.pex`, `tools/papyrus_compile.sh`

> Part of the [Skython M1.5/M2 plan](README.md). Read the index's **Global Constraints**, **Fixed paths**, **Contracts** (Plugin: `spy::native::Register`; Papyrus: `Skython.psc`, `papyrus_compile.sh`; the `channel` row of the events table; limits `max_channel_payload_bytes`, `max_channel_bytes`) and the Part F brief for Task 46 first. Steps use checkbox (`- [ ]`) syntax.
>
> **Runs on:** Linux (Steps 1-9: core unit tests, the Papyrus compile, the plugin build on the box through `tools/remote_build.sh`), then the Windows box with the game (Steps 10-12, after Gate 0 only: `Skython.pex` is a new `Data\Scripts` file).
>
> **Depends on:** Task 33 (generated `spy::kMaxChannelPayloadBytes`, `spy::kMaxChannelBytes`), Task 39 (`plugin/src/Events.h`: `spy::events::Hub()`, `spy::events::Push`; `API-VERIFICATION.md` in this folder), Task 36 (`Game.on_channel`), Task 45 (`box_deploy.sh --test-pex`) for the live step.
>
> **STOP and ask the owner (do not improvise) if:** a name in Step 1 is missing (README: a missing protocol name is reported, never added here); Gate 0 is not open when Step 10 is reached; `RegisterFunction`'s signature at the pin differs from Step 7's comment; the game crashes or logs no `native: Skython.Send registered`; the 70000-byte payload arrives shorter than 65536 bytes with `truncated: false` (an engine string limit before our native: record it, do not change the test); the build box does not answer.

**What S5 proved and what changes.** Spike S5 registered `SkyrimPython.Send` with `vm->RegisterFunction("Send"sv, "SkyrimPython"sv, NativeSend)` (`void(RE::StaticFunctionTag*, RE::BSFixedString, RE::BSFixedString)`) from `SKSE::GetPapyrusInterface()->Register(...)`; Send ran on varying engine threads. The product native is the same shape under the class `Skython`, and instead of storing the strings it builds the `channel` event and pushes it into Task 39's hub. Two facts measured while writing this plan (Linux, 2026-10-02) that S5 did not need: **`ScriptName Skython Hidden` does not compile without the flags file** (`Unknown user flag Hidden`), so the script always passes `-f=TESV_Papyrus_Flags.flg` (it is in the game's `Data/Scripts.zip` at `Source/Scripts/`); and **the compiler exits 0 when a script fails, and a second `-i` replaces the first**, so the script judges success from the compiler's summary line and the `.pex`, and joins import dirs with `;`. A `.pex` also embeds its compile time and its source file's modification time (and the user and machine name), so raw SHA-256s change on every build and a `.pex` is never pinned by its raw SHA-256; `--stable-hash` zeroes both time fields, and the result is reproducible only on the same machine and account (README Global Constraints, "Papyrus compiler facts").

**Files:**
- Create: `core/include/spy/channel.h`, `core/src/channel.cpp`, `core/tests/test_channel.cpp` (the pure part of the native: event data and the UTF-8 cut; picked up by `core/CMakeLists.txt`'s globs)
- Create: `tools/papyrus_compile.sh`, `tools/test_papyrus_compile.sh`, `plugin/papyrus/Skython.psc`, `plugin/papyrus/compiled/Skython.pex` (committed build output)
- Create: `plugin/src/PapyrusNative.h`, `plugin/src/PapyrusNative.cpp`; Modify: `plugin/CMakeLists.txt` (one source entry), `plugin/src/main.cpp` (one include, one call)
- Create: `livetest/test_m15_send.py`
- Modify: `.gitignore` (commit the two compiled `.pex` folders), `docs/PINS.md` (Papyrus section), `docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md` (Task 46 lines), `spikes/RESULTS.md`

**Interfaces:**
- Consumes: `spy::kMaxChannelPayloadBytes` (65536), `spy::kMaxChannelBytes` (256) from `spy/protocol_generated.h` (Task 33); `spy::dump_json` (`spy/errors.h`); `spy::events::Hub() -> spy::EventHub&`, `EventHub::Wants(std::string_view) const`, `spy::events::Push(std::string_view, json) noexcept` (Task 39); `spy::log::Info/Warn/Error(const std::string&)` (`plugin/src/Log.h`); `Game.on_channel(channel, fn) -> Subscription`, `Event.name/.data/.seq/.dropped/.suppressed` (Task 36); `tools/box/box_deploy.sh --test-pex` (Task 45).
- Produces:
  - `std::optional<nlohmann::json> spy::make_channel_event(std::string_view channel, std::string_view payload)` → `{"channel", "payload", "truncated"}` or `nullopt` (empty channel, or longer than 256 bytes); `std::size_t spy::utf8_prefix_len(std::string_view, std::size_t max)`.
  - `spy::native::Register() -> void` (Contracts) and the log lines `native: Skython.Send registered` (Contracts) and `native: Skython.Send dropped a call whose channel is empty or longer than 256 bytes (logged once)`.
  - `tools/papyrus_compile.sh <source.psc>... -o <out_dir> [-i <dir>]...`, `--stable-hash <file.pex>...` (prints `<sha256>  <file>`), `--vanilla-dir`. Imports: the source's folder, `-i` dirs, `plugin/papyrus`, `out/papyrus-import/vanilla/Source/Scripts` (extracted from `Data/Scripts.zip`, gitignored). Tasks 47-48 compile with it.
  - `plugin/papyrus/compiled/Skython.pex`, stable hash `36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341` (this exact `Skython.psc`, built as `geoff` on this machine).
  - `.gitignore` negations `!plugin/papyrus/compiled/*.pex` and `!testesp/compiled/*.pex` (Task 48 commits into the second).

- [ ] **Step 1: Check the names this task consumes exist**

```bash
cd /home/geoff/projects/games/skyrim/skyrim-python
grep -nE 'kMaxChannel(Payload)?Bytes' core/include/spy/protocol_generated.h
grep -nE 'Hub\(\)|void Push\(' plugin/src/Events.h
grep -n 'def on_channel' python/src/skython/game.py
grep -n -- '--test-pex' tools/box/box_deploy.sh | head -2
test -f docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md && echo API-VERIFICATION present
```
Expected: `inline constexpr std::size_t kMaxChannelBytes = 256u;` and `... kMaxChannelPayloadBytes = 65536u;` (the integer type may differ; the values may not), the `Hub()` and `Push(` declarations, `def on_channel`, the `--test-pex` lines, `API-VERIFICATION present`. Anything missing: STOP and report which task owes it.

- [ ] **Step 2: Write the core unit test and see it fail**

Create `core/tests/test_channel.cpp`:
```cpp
#include "doctest/doctest.h"
#include "spy/channel.h"
#include "spy/errors.h"
#include "spy/protocol_generated.h"
#include <string>

using spy::json;
using spy::make_channel_event;

namespace {
std::string payload_of(const std::optional<json>& e) { return (*e)["payload"].get<std::string>(); }
}

TEST_CASE("channel: the limits are the protocol's")
{
    CHECK(spy::kMaxChannelPayloadBytes == 65536u);
    CHECK(spy::kMaxChannelBytes == 256u);
}

TEST_CASE("channel: a short payload passes unchanged")
{
    auto e = make_channel_event("skython_test", "stage_10");
    REQUIRE(e);
    CHECK(*e == json{{"channel", "skython_test"}, {"payload", "stage_10"}, {"truncated", false}});
    CHECK((*make_channel_event("c", ""))["payload"] == "");
}

TEST_CASE("channel: a payload at the limit is kept, one byte more is cut")
{
    const std::string at(spy::kMaxChannelPayloadBytes, 'a');
    auto kept = make_channel_event("c", at);
    CHECK(payload_of(kept).size() == spy::kMaxChannelPayloadBytes);
    CHECK((*kept)["truncated"] == false);
    auto cut = make_channel_event("c", at + "b");
    CHECK(payload_of(cut) == at);
    CHECK((*cut)["truncated"] == true);
}

TEST_CASE("channel: a 70000-byte payload arrives as the first 65536 bytes")
{
    const std::string big(70000, 'x');
    auto e = make_channel_event("c", big);
    CHECK(payload_of(e) == big.substr(0, spy::kMaxChannelPayloadBytes));
    CHECK((*e)["truncated"] == true);
}

TEST_CASE("channel: the cut never splits a UTF-8 sequence")
{
    for (const std::string seq : {"\xC3\xA9", "\xE2\x82\xAC", "\xF0\x9F\x98\x80"}) {  // 2, 3 and 4 bytes
        for (std::size_t lead = 1; lead <= seq.size(); ++lead) {
            // the sequence starts `lead` bytes before the limit: it straddles the cut unless lead == its length
            std::string p(spy::kMaxChannelPayloadBytes - lead, 'a');
            p += seq;
            p += "tail";
            auto e = make_channel_event("c", p);
            const std::size_t want = lead == seq.size() ? spy::kMaxChannelPayloadBytes : spy::kMaxChannelPayloadBytes - lead;
            CHECK(payload_of(e).size() == want);
            CHECK((*e)["truncated"] == true);
        }
    }
}

TEST_CASE("channel: an empty or over-long channel is dropped")
{
    CHECK_FALSE(make_channel_event("", "x"));
    CHECK(make_channel_event(std::string(spy::kMaxChannelBytes, 'c'), "x"));
    CHECK_FALSE(make_channel_event(std::string(spy::kMaxChannelBytes + 1, 'c'), "x"));
}

TEST_CASE("channel: invalid UTF-8 in a payload is safe to dump")
{
    auto e = make_channel_event("c", std::string("\xFF\xFE ok", 5));
    REQUIRE(e);
    CHECK_NOTHROW((void)spy::dump_json(*e));
}

TEST_CASE("channel: utf8_prefix_len backs up at most 3 bytes")
{
    const std::string cont(10, '\x80');
    CHECK(spy::utf8_prefix_len(cont, 5) == 2);
    CHECK(spy::utf8_prefix_len("abc", 10) == 3);
}
```
```bash
cmake -S core -B core/build >/dev/null && cmake --build core/build -j 2>&1 | grep -E 'error' | head -3
```
Expected: `fatal error: spy/channel.h: No such file or directory`.

- [ ] **Step 3: Implement `channel.h` / `channel.cpp`**

Create `core/include/spy/channel.h`:
```cpp
#pragma once
#include <cstddef>
#include <optional>
#include <string_view>
#include <nlohmann/json.hpp>

namespace spy {
// Skython.Send(channel, payload) -> the data of the `channel` event: {"channel", "payload", "truncated"}. nullopt when the
// channel is empty or longer than kMaxChannelBytes (the native drops such a call and logs once). The payload is cut to at
// most kMaxChannelPayloadBytes at a UTF-8 boundary ("truncated": true). Pure: no engine, no Python (Task 46).
std::optional<nlohmann::json> make_channel_event(std::string_view channel, std::string_view payload);

// The length of the longest prefix of s that is at most max bytes and does not end inside a UTF-8 sequence (it backs up
// at most 3 bytes, so invalid input costs nothing extra; dump_json replaces invalid UTF-8 later).
std::size_t utf8_prefix_len(std::string_view s, std::size_t max);
}
```
Create `core/src/channel.cpp`:
```cpp
#include "spy/channel.h"
#include "spy/protocol_generated.h"  // kMaxChannelBytes, kMaxChannelPayloadBytes (protocol.json limits, Task 33)

namespace spy {
std::size_t utf8_prefix_len(std::string_view s, std::size_t max)
{
    if (s.size() <= max) return s.size();
    std::size_t n = max;  // s[n] is the first byte cut off; a continuation byte (10xxxxxx) there splits a sequence
    for (int i = 0; i < 3 && n > 0 && (static_cast<unsigned char>(s[n]) & 0xC0) == 0x80; ++i) --n;
    return n;
}

std::optional<nlohmann::json> make_channel_event(std::string_view channel, std::string_view payload)
{
    if (channel.empty() || channel.size() > kMaxChannelBytes) return std::nullopt;
    const std::size_t keep = utf8_prefix_len(payload, kMaxChannelPayloadBytes);
    return nlohmann::json{{"channel", std::string(channel)},
                          {"payload", std::string(payload.substr(0, keep))},
                          {"truncated", keep < payload.size()}};
}
}
```
```bash
cmake --build core/build -j 2>&1 | grep -E 'error|warning' | head -5; core/build/spy_core_tests -tc='channel*'
core/build/spy_core_tests | tail -2
```
Expected: no errors; `[doctest] test cases:  8 |  8 passed | 0 failed`; the full suite `Status: SUCCESS!`.

- [ ] **Step 4: Commit**

```bash
git add core/include/spy/channel.h core/src/channel.cpp core/tests/test_channel.cpp
git commit -m "feat(core): channel event data with a UTF-8-safe payload cut"
```

- [ ] **Step 5: `tools/papyrus_compile.sh`, its offline test and `Skython.psc`**

Create `tools/papyrus_compile.sh` (then `chmod +x`):
```bash
#!/usr/bin/env bash
# Compile Papyrus sources on Linux, headless, with the SimpleSkyrim Papyrus Compiler under GE-Proton10-14's wine64
# (spike S5's recipe, spikes/RESULTS.md). Compiles only: it never starts the game or Steam.
# Usage:
#   tools/papyrus_compile.sh <source.psc>... -o <out_dir> [-i <extra import dir>]...
#   tools/papyrus_compile.sh --stable-hash <file.pex>...    sha256 of each .pex with its two time fields zeroed
#   tools/papyrus_compile.sh --vanilla-dir                  prints the vanilla import dir (extracting it first)
# Imports, in this order: each source's own folder, every -i dir, plugin/papyrus (Skython.psc), the vanilla script
# sources. The vanilla sources and TESV_Papyrus_Flags.flg come from the game's Data/Scripts.zip, extracted once to
# out/papyrus-import/vanilla (out/ is gitignored: vanilla sources are never committed).
# The compiler exits 0 even when a script fails, so success is judged from its summary line
# ("<n> succeeded, 0 failed") and from each .pex existing and being newer than this run's start.
# A .pex holds the compile time (header bytes 8..15) and, in its debug info, the source file's modification time, so a
# rebuild never matches a recorded raw SHA-256. --stable-hash zeroes both; it is the value docs/PINS.md compares across
# rebuilds. The header also holds the source file name, user name and machine name, so the stable hash holds only for
# the same file name, machine and account that built the recorded file.
# Env (defaults are the S5 values): PAPYRUS_COMPILER, PAPYRUS_WINEPREFIX, PAPYRUS_WINE, SKYRIM_SCRIPTS_ZIP.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PAPYRUS_COMPILER="${PAPYRUS_COMPILER:-/home/geoff/Games/SimpleSkyrim/Stock Game/Papyrus Compiler/PapyrusCompiler.exe}"
PAPYRUS_WINEPREFIX="${PAPYRUS_WINEPREFIX:-/home/geoff/.local/share/Steam/steamapps/compatdata/4190904831/pfx}"
PAPYRUS_WINE="${PAPYRUS_WINE:-$HOME/.local/share/Steam/compatibilitytools.d/GE-Proton10-14/files/bin/wine64}"
SKYRIM_SCRIPTS_ZIP="${SKYRIM_SCRIPTS_ZIP:-/mnt/games/SteamLibrary/steamapps/common/Skyrim Special Edition/Data/Scripts.zip}"
VANILLA="$ROOT/out/papyrus-import/vanilla/Source/Scripts"
die() { echo "papyrus_compile: $*" >&2; exit 1; }

stable_hash() {  # stable_hash <file.pex>: sha256 with the compile time and the source modification time zeroed
  python3 - "$1" <<'PY'
import hashlib, struct, sys
def stable(path):
    b = bytearray(open(path, "rb").read())
    if b[:4] != bytes.fromhex("fa57c0de"):
        sys.exit(f"not a Skyrim .pex: {path}")
    b[8:16] = bytes(8)                      # header: compilation time
    pos = 16
    def skip_str(p):
        (n,) = struct.unpack_from(">H", b, p)
        return p + 2 + n
    for _ in range(3):                      # source file name, user name, machine name
        pos = skip_str(pos)
    (count,) = struct.unpack_from(">H", b, pos); pos += 2
    for _ in range(count):                  # string table
        pos = skip_str(pos)
    if b[pos] == 1:                         # debug info present: its first field is the source's modification time
        b[pos + 1:pos + 9] = bytes(8)
    return hashlib.sha256(b).hexdigest()
print(stable(sys.argv[1]))
PY
}
vanilla() {  # extract the vanilla sources and the flags file once
  if [ ! -f "$VANILLA/TESV_Papyrus_Flags.flg" ] || [ ! -f "$VANILLA/Quest.psc" ]; then
    [ -f "$SKYRIM_SCRIPTS_ZIP" ] || die "no $SKYRIM_SCRIPTS_ZIP (set SKYRIM_SCRIPTS_ZIP to the game's Data/Scripts.zip)"
    mkdir -p "$ROOT/out/papyrus-import/vanilla"
    unzip -q -o "$SKYRIM_SCRIPTS_ZIP" 'Source/Scripts/*' -d "$ROOT/out/papyrus-import/vanilla" || die "could not extract $SKYRIM_SCRIPTS_ZIP"
  fi
  [ -f "$VANILLA/TESV_Papyrus_Flags.flg" ] || die "TESV_Papyrus_Flags.flg is not in $SKYRIM_SCRIPTS_ZIP"
}
winpath() { echo "Z:$(cd "$1" && pwd)"; }   # Wine maps / to Z:

case "${1:-}" in
  --stable-hash) shift; [ $# -ge 1 ] || die "usage: --stable-hash <file.pex>..."
                 for f in "$@"; do h="$(stable_hash "$f")" || die "--stable-hash: $f is not a Skyrim .pex"; echo "$h  $f"; done
                 exit 0 ;;
  --vanilla-dir) vanilla; echo "$VANILLA"; exit 0 ;;
  ""|-h|--help)  sed -n '2,8p' "$0"; exit 2 ;;
esac

sources=(); imports=(); out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="${2:?-o needs a directory}"; shift 2 ;;
    -i) [ -d "${2:?-i needs a directory}" ] || die "no such import dir: $2"; imports+=("$(cd "$2" && pwd)"); shift 2 ;;
    -*) die "unknown option $1" ;;
    *.psc) [ -f "$1" ] || die "no such source: $1"; sources+=("$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"); shift ;;
    *) die "not a .psc: $1" ;;
  esac
done
[ ${#sources[@]} -ge 1 ] || die "no sources"
[ -n "$out" ] || die "no -o <out_dir>"
[ -f "$PAPYRUS_COMPILER" ] || die "no compiler at $PAPYRUS_COMPILER"
[ -x "$PAPYRUS_WINE" ] || die "no wine64 at $PAPYRUS_WINE"
vanilla
mkdir -p "$out"; out="$(cd "$out" && pwd)"
start="$(mktemp)"; log="$(mktemp)"
for src in "${sources[@]}"; do
  # The compiler takes ONE -i (a second -i replaces the first): join the import dirs with ';'.
  dirs=("$(dirname "$src")" "${imports[@]}" "$ROOT/plugin/papyrus" "$VANILLA")
  imp=""; for d in "${dirs[@]}"; do imp+="${imp:+;}$(winpath "$d")"; done
  name="$(basename "$src")"
  # env -u DISPLAY -u WAYLAND_DISPLAY: no window can open on the owner's desktop (S5). WINEDEBUG=-all: quiet.
  ( cd "$(dirname "$src")" && timeout 300 env -u DISPLAY -u WAYLAND_DISPLAY WINEPREFIX="$PAPYRUS_WINEPREFIX" WINEDEBUG=-all \
      "$PAPYRUS_WINE" "$PAPYRUS_COMPILER" "$name" -i="$imp" -o="$(winpath "$out")" -f=TESV_Papyrus_Flags.flg ) > "$log" 2>&1 || true
  tr -d '\r' < "$log" | grep -v '^wineserver:' || true
  pex="$out/${name%.psc}.pex"
  if ! tr -d '\r' < "$log" | grep -qE '^Batch compile of 1 files finished\. 1 succeeded, 0 failed\.$' || [ ! "$pex" -nt "$start" ]; then
    die "FAILED: $src (see the compiler output above)"
  fi
  echo "compiled $src -> $pex ($(stat -c %s "$pex") bytes)"
done
```
Create `tools/test_papyrus_compile.sh` (then `chmod +x`):
```bash
#!/usr/bin/env bash
# Offline checks for tools/papyrus_compile.sh (Linux, headless Wine; never the game or Steam). Exit 0 = all pass.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PC="$ROOT/tools/papyrus_compile.sh"
fail=0
ok()  { echo "ok   $*"; }
bad() { echo "FAIL $*"; fail=1; }
T="$(mktemp -d)"

# 1. never a window on the owner's desktop: both display variables unset on the compiler's command line
grep -qF 'env -u DISPLAY -u WAYLAND_DISPLAY' "$PC" && ok "the compiler runs with DISPLAY and WAYLAND_DISPLAY unset" \
  || bad "papyrus_compile.sh does not unset DISPLAY and WAYLAND_DISPLAY"
# 2. Skython.psc compiles to a non-empty Skyrim .pex, also when the caller's environment has a display
DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 "$PC" "$ROOT/plugin/papyrus/Skython.psc" -o "$T/out" > "$T/log" 2>&1; rc=$?
magic="$(head -c4 "$T/out/Skython.pex" 2>/dev/null | od -An -tx1 | tr -d ' \n')"
[ "$rc" = 0 ] && [ -s "$T/out/Skython.pex" ] && [ "$magic" = fa57c0de ] && ok "Skython.psc -> non-empty Skython.pex" \
  || { bad "compiling Skython.psc: rc=$rc magic=$magic"; tail -5 "$T/log"; }
# 3. a fresh build has the committed file's stable hash, and docs/PINS.md records it
a="$("$PC" --stable-hash "$T/out/Skython.pex" 2>/dev/null | cut -d' ' -f1)"
b="$("$PC" --stable-hash "$ROOT/plugin/papyrus/compiled/Skython.pex" 2>/dev/null | cut -d' ' -f1)"
[ -n "$a" ] && [ "$a" = "$b" ] && ok "a fresh build matches the committed Skython.pex (stable hash $a)" || bad "stable hash: fresh '$a', committed '$b'"
[ -n "$b" ] && grep -qF "$b" "$ROOT/docs/PINS.md" && ok "docs/PINS.md records that stable hash" || bad "docs/PINS.md lacks the stable hash '$b'"
# 4. a compile error fails loudly (the compiler itself exits 0 on errors)
printf 'ScriptName Broken\nFunction X(\n' > "$T/Broken.psc"
"$PC" "$T/Broken.psc" -o "$T/bout" > "$T/blog" 2>&1; rc=$?
[ "$rc" != 0 ] && grep -q 'FAILED' "$T/blog" && [ ! -e "$T/bout/Broken.pex" ] && ok "a compile error exits non-zero, no .pex" || bad "broken script: rc=$rc"
# 5. argument checks
if "$PC" "$ROOT/plugin/papyrus/Skython.psc" >/dev/null 2>&1; then bad "accepted no -o"; else ok "refuses without -o"; fi
if "$PC" "$T/nope.psc" -o "$T/x" >/dev/null 2>&1; then bad "accepted a missing source"; else ok "refuses a missing source"; fi
if "$PC" --stable-hash "$T/Broken.psc" >/dev/null 2>&1; then bad "--stable-hash accepted a non-.pex"; else ok "--stable-hash refuses a non-.pex"; fi
# 6. the vanilla script sources live only under out/ (gitignored, never committed)
d="$("$PC" --vanilla-dir 2>/dev/null)"
case "$d" in "$ROOT/out/"*) ok "vanilla imports under out/" ;; *) bad "vanilla import dir '$d'" ;; esac
git -C "$ROOT" check-ignore -q "$d/Quest.psc" && ok "vanilla sources are gitignored" || bad "vanilla sources are not gitignored"

[ "$fail" = 0 ] && echo "PAPYRUS COMPILE PASS" || echo "PAPYRUS COMPILE FAIL"
exit "$fail"
```
Create `plugin/papyrus/Skython.psc` (byte for byte: the stable hash below depends on it):
```papyrus
ScriptName Skython Hidden
{Skython's Papyrus API. Send(channel, payload) raises the event "channel" for Python subscribers of that channel
(game.on_channel). Papyrus strings pass through the engine's case-insensitive string pool, so channel filters ignore
case. A payload longer than 65536 bytes arrives cut at a UTF-8 boundary with truncated = true.}

Function Send(String channel, String payload) global native
```
Append to `.gitignore` (after the `*.pex` line's block):
```gitignore
# committed Papyrus build outputs (stable hashes in docs/PINS.md)
!plugin/papyrus/compiled/*.pex
!testesp/compiled/*.pex
```
- [ ] **Step 6: Compile, pin, test**

```bash
tools/papyrus_compile.sh plugin/papyrus/Skython.psc -o plugin/papyrus/compiled | tail -2
tools/papyrus_compile.sh --stable-hash plugin/papyrus/compiled/Skython.pex
sha256sum plugin/papyrus/compiled/Skython.pex "/home/geoff/Games/SimpleSkyrim/Stock Game/Papyrus Compiler/PapyrusCompiler.exe" \
  "/mnt/games/SteamLibrary/steamapps/common/Skyrim Special Edition/Data/Scripts.zip"
git check-ignore -q plugin/papyrus/compiled/Skython.pex || echo "Skython.pex is committable"
```
Expected: `Batch compile of 1 files finished. 1 succeeded, 0 failed.`, `compiled .../Skython.psc -> .../plugin/papyrus/compiled/Skython.pex (959 bytes)`; stable hash `36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341` (a different value means `Skython.psc` differs from Step 5, or another user/machine built it: fix the source, do not pin a new value silently); the compiler `f441afa877ce0b315c6b613db7fe700a7bbc66b71a72fc4d0d8bc94577ceb0ce`, `Scripts.zip` `45ff21e55be76f3bce86385770cba878ec8dacabb8df4cd3e86976fbdf3ebdc6`; `Skython.pex is committable`. No window opens. Append to `docs/PINS.md`:
```markdown
## Papyrus (Task 46)
- Compiler: SimpleSkyrim `Stock Game/Papyrus Compiler/PapyrusCompiler.exe` sha256 f441afa877ce0b315c6b613db7fe700a7bbc66b71a72fc4d0d8bc94577ceb0ce, run headless by `tools/papyrus_compile.sh` (GE-Proton10-14 `wine64`, prefix `compatdata/4190904831`, DISPLAY and WAYLAND_DISPLAY unset; spike S5).
- Imports and flags: the game's `Data/Scripts.zip` sha256 45ff21e55be76f3bce86385770cba878ec8dacabb8df4cd3e86976fbdf3ebdc6, `Source/Scripts/*.psc` and `TESV_Papyrus_Flags.flg` extracted to `out/papyrus-import/vanilla` (gitignored; vanilla sources are never committed).
- `.pex` hashes: "raw" is the committed file; "stable" is `tools/papyrus_compile.sh --stable-hash` (compile time and source modification time zeroed). A rebuild is checked against the stable hash; it holds for the same source file name, machine and user (`geoff` on this machine).
- pin plugin/papyrus/compiled/Skython.pex stable 36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341
  (informational only, never compared: the committed file's raw sha256 <the sha256sum printed above>)
```
Then:
```bash
tools/test_papyrus_compile.sh
```
Expected: ten `ok` lines (`the compiler runs with DISPLAY and WAYLAND_DISPLAY unset`, `Skython.psc -> non-empty Skython.pex`, `a fresh build matches the committed Skython.pex (stable hash 36082f6c...)`, `docs/PINS.md records that stable hash`, `a compile error exits non-zero, no .pex`, three argument refusals, `vanilla imports under out/`, `vanilla sources are gitignored`) and `PAPYRUS COMPILE PASS`.

- [ ] **Step 7: The native in the plugin**

Create `plugin/src/PapyrusNative.h`:
```cpp
#pragma once

namespace spy::native {
// Registers the Papyrus native `Skython.Send(String channel, String payload) global native` through SKSE's Papyrus
// interface. Called once from SpyCore_Load (plugin load); the VM calls back when it is ready and the callback logs
// "native: Skython.Send registered". The native copies both strings, builds the `channel` event with
// spy::make_channel_event and calls events::Push: no AddTask, no lookup, no Python, never blocks, never throws.
void Register();
}
```
Create `plugin/src/PapyrusNative.cpp`:
```cpp
#include "PapyrusNative.h"
#include "Events.h"
#include "Log.h"
#include "spy/channel.h"
#include "spy/protocol_generated.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <string>
#include <utility>

using namespace std::literals;

namespace spy::native {
namespace {
std::atomic_bool g_badChannelLogged{false};

// Runs on a Papyrus VM thread (or inside an SKSE task: never assume either, R49). Copies, pushes, returns.
void Send(RE::StaticFunctionTag*, RE::BSFixedString channel, RE::BSFixedString payload)
{
    try {
        const char* c = channel.c_str();
        const std::string_view ch = c ? std::string_view(c) : ""sv;
        // A bad channel is a script bug: warn once whether or not anyone is subscribed, so it is seen during
        // development even before a subscriber exists. The check is a length test: no copy, no JSON.
        if (ch.empty() || ch.size() > spy::kMaxChannelBytes) {
            if (!g_badChannelLogged.exchange(true))
                spy::log::Warn("native: Skython.Send dropped a call whose channel is empty or longer than " +
                          std::to_string(spy::kMaxChannelBytes) + " bytes (logged once)");
            return;
        }
        if (!spy::events::Hub().Wants("channel")) return;  // nobody subscribed: no copy, no JSON
        const char* p = payload.c_str();
        auto data = spy::make_channel_event(ch, p ? std::string_view(p) : ""sv);
        if (!data) return;  // unreachable after the check above (make_channel_event applies the same rule)
        spy::events::Push("channel", std::move(*data));
    } catch (...) {
        // nothing may escape into the VM (Global Constraints); Push itself never throws
    }
}

bool RegisterFunctions(RE::BSScript::IVirtualMachine* vm)
{
    try {
        // IVirtualMachine::RegisterFunction(std::string_view fn, std::string_view cls, F callback,
        //                                   bool callableFromTasklets = false)  (pinned clone, IVirtualMachine.h:147)
        vm->RegisterFunction("Send"sv, "Skython"sv, Send);
        spy::log::Info("native: Skython.Send registered");
        return true;
    } catch (...) {
        spy::log::Error("native: registering Skython.Send threw");
        return false;
    }
}
}

void Register()
{
    auto* papyrus = SKSE::GetPapyrusInterface();
    if (!papyrus || !papyrus->Register(RegisterFunctions))
        spy::log::Error("native: SKSE Papyrus interface refused the registration; Skython.Send is unavailable");
}
}
```
In `plugin/CMakeLists.txt`, add `src/PapyrusNative.cpp` to the `SPY_CORE_SOURCES` list (after Task 39's `src/Events.cpp`/`src/EventSinks.cpp` entries; add the entry, do not replace the line). In `plugin/src/main.cpp` add `#include "PapyrusNative.h"` to the includes and, in `SpyCore_Load`, directly after `spy::settings::Load();`:
```cpp
        spy::native::Register();  // Skython.Send; the VM calls back when it is ready (Task 46)
```
(Plugin load is the only time SKSE accepts Papyrus registrations; `SpyCore_Load` runs inside the loader's `SKSEPluginLoad`. The native is registered even when Python is disabled: a mod calling `Skython.Send` then pushes into a hub nobody reads, instead of hitting an unbound native.)

Check the two signatures against the pinned clone and append them to this folder's `API-VERIFICATION.md`:
```bash
grep -n 'void RegisterFunction(std::string_view a_fnName, std::string_view a_className, F a_callback' plugin/extern/CommonLibSSE-NG/include/RE/I/IVirtualMachine.h
grep -n 'using RegFunction2\|bool Register(' plugin/extern/CommonLibSSE-NG/include/SKSE/Interfaces.h
```
Expected: `147: void RegisterFunction(std::string_view a_fnName, std::string_view a_className, F a_callback, bool a_callableFromTasklets = false);` and `using RegFunction2 = bool(RE::BSScript::IVirtualMachine* a_vm);` plus a `bool Register(` line. Append:
```markdown
## Task 46 (Skython.Send)
- `RE::BSScript::IVirtualMachine::RegisterFunction(std::string_view a_fnName, std::string_view a_className, F a_callback, bool a_callableFromTasklets = false)` — include/RE/I/IVirtualMachine.h:147 (template body include/RE/N/NativeFunction.h:174). Used with `F = void(RE::StaticFunctionTag*, RE::BSFixedString, RE::BSFixedString)` and the default `a_callableFromTasklets = false`, as spike S5 measured.
- `SKSE::PapyrusInterface::Register(RegFunction2*)`, `RegFunction2 = bool(RE::BSScript::IVirtualMachine*)` — include/SKSE/Interfaces.h.
```

- [ ] **Step 8: Build on the box**

```bash
tools/remote_build.sh plugin Skython.dll && tools/remote_build.sh plugin SkythonCore.dll
```
Expected: both DLLs copied back to `out/`, 0 warnings from `PapyrusNative.cpp`.

- [ ] **Step 9: Write the live test, then commit the offline work**

Create `livetest/test_m15_send.py`:
```python
"""Task 46: the native Skython.Send raises `channel` events. Needs Skython.pex on the box (tools/box/box_deploy.sh
--test-pex, owner decision 7) and a save loaded (interactive). Every channel and payload carries a fresh per-test tag:
Papyrus strings pass through the engine's case-insensitive string pool (R56), so a string some other script interned
first could come back in that script's casing; a fresh tag cannot collide."""
import queue
import uuid

import pytest

from support import log_text

PAYLOAD_LIMIT = 65536  # max_channel_payload_bytes (protocol.json, Task 33)


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def collect(game, channel):
    q = queue.Queue()
    return game.on_channel(channel, q.put), q


def next_event(q, timeout=10.0):
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"no channel event within {timeout} s")


def test_native_registered_at_load():
    assert "native: Skython.Send registered" in log_text()


def test_send_reaches_a_channel_subscriber(game):
    tag = uuid.uuid4().hex
    ch = f"skython_live_{tag}"
    sub, q = collect(game, ch)
    try:
        assert game.call("Skython", "Send", ch, f"hello_{tag}") is None
        ev = next_event(q)
        assert ev.name == "channel"
        assert ev.data == {"channel": ch, "payload": f"hello_{tag}", "truncated": False}
        assert ev.seq == 1 and ev.dropped == 0 and ev.suppressed == 0
    finally:
        sub.close()


def test_long_payload_arrives_cut_at_the_limit(game):
    tag = uuid.uuid4().hex
    ch = f"skython_long_{tag}"
    payload = tag + "x" * (70000 - len(tag))
    sub, q = collect(game, ch)
    try:
        game.call("Skython", "Send", ch, payload)
        ev = next_event(q)
        assert ev.data["truncated"] is True
        assert len(ev.data["payload"].encode()) == PAYLOAD_LIMIT
        assert ev.data["payload"] == payload[:PAYLOAD_LIMIT]
    finally:
        sub.close()


def test_channel_filter_ignores_case(game):
    tag = uuid.uuid4().hex
    sub, q = collect(game, f"SKYTHON_CASE_{tag.upper()}")
    try:
        game.call("Skython", "Send", f"skython_case_{tag}", f"x_{tag}")
        ev = next_event(q)
        assert ev.data["channel"].lower() == f"skython_case_{tag}"
        assert ev.data["payload"] == f"x_{tag}"
    finally:
        sub.close()


def test_other_and_invalid_channels_never_arrive(game):
    tag = uuid.uuid4().hex
    ch = f"skython_only_{tag}"
    sub, q = collect(game, ch)
    try:
        game.call("Skython", "Send", f"skython_other_{tag}", "not for this subscriber")
        game.call("Skython", "Send", "", "empty channel")
        game.call("Skython", "Send", "c" * 257, "channel longer than max_channel_bytes")
        game.call("Skython", "Send", ch, f"yes_{tag}")
        ev = next_event(q)
        assert ev.data["payload"] == f"yes_{tag}"  # each call returned before the next, so order is call order
        with pytest.raises(queue.Empty):
            q.get(timeout=1.0)
    finally:
        sub.close()
    assert "native: Skython.Send dropped a call whose channel is empty or longer than 256 bytes" in log_text()
```
```bash
uv run --project python pytest livetest/test_m15_send.py -q   # without SKYTHON_LIVE: every test skipped
git add tools/papyrus_compile.sh tools/test_papyrus_compile.sh plugin/papyrus/Skython.psc plugin/papyrus/compiled/Skython.pex \
        .gitignore docs/PINS.md plugin/src/PapyrusNative.h plugin/src/PapyrusNative.cpp plugin/CMakeLists.txt plugin/src/main.cpp \
        docs/superpowers/plans/2026-10-02-skython-m1_5-m2/API-VERIFICATION.md livetest/test_m15_send.py
git commit -m "feat(plugin): Skython.Send native pushes channel events; headless Papyrus compile"
```
Expected: `5 skipped`; the commit lists `plugin/papyrus/compiled/Skython.pex`.

- [ ] **Step 10: Gate 0 check**

Task 45's RESULTS entry must record owner decision 7 as accepted (README **Owner decisions** table: a STOP gate, never assumed). If not: report "Task 46 offline part committed; the live step waits for Gate 0" and STOP. Task 45 Step 10 also wrote `out/gate0-accepted` (`test -s out/gate0-accepted && cat out/gate0-accepted`); without it `box_deploy.sh --test-pex` refuses (`Gate 0 is not recorded`): STOP, never write the file here.

- [ ] **Step 11: Live run on `s4test` (no ESP needed)**

```bash
tools/box/box_deploy.sh out/Skython.dll plugin/config/Skython.dev.ini
tools/box/box_deploy.sh --package
tools/box/box_deploy.sh --test-pex plugin/papyrus/compiled/Skython.pex
tools/box/box_launch.sh
tools/box/box_wait_log.sh Skython.log "native: Skython.Send registered" 600 || echo "STOP: the native did not register"
tools/box/box_wait_log.sh Skython.log "python host: (running|failed|disabled)" 600
tools/box/box_session.sh up && source out/box-live.env && source livetest/s4test.env
tools/box/box_wait_log.sh Skython.log "lifecycle: Main Menu open" 600
tools/box/box_console.sh "load s4test"
tools/box/box_wait_log.sh Skython.log "lifecycle: interactive = true" 300
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m15_send.py -q -rs
SKYTHON_LIVE=1 SKYTHON_BOX=1 uv run --project python pytest livetest/test_m2_events_batch1.py -q -rs   # no regression in Task 39's events
tools/box/box_files.sh pull Skython.log >/dev/null; grep -E 'native:|\[error\]' "$SKSE_LOG_DIR/Skython.log" | grep -v livetest_broken
tools/box/box_quit.sh || tools/box/box_stop.sh
tools/box/box_session.sh down
```
Expected: `deployed plugin/papyrus/compiled/Skython.pex -> C:\ChronicleDev\Stock Game\Data\Scripts\Skython.pex`; `MATCH ... native: Skython.Send registered`; `test_m15_send.py` 5 passed; Task 39's batch-1 file passes as before; the grep shows the `registered` line and the one `dropped a call` warning, no errors; `box_quit.sh` prints `NO_NEW_CRASH_LOG` (`NOT_CLEAN` without a crash log is the known exit hang, R46/R55: `box_stop.sh`) and `plugins.txt: nothing to restore` (this task adds no plugin line). s4test is never saved.

- [ ] **Step 12: Record and commit**

Append to `spikes/RESULTS.md`:
```markdown
- **Task 46, Skython.Send (box, Windows-native, <date time>, Skyrim 1.6.1170.0, s4test, never saved; 1 launch; SKSE plugins loaded: <list from skse64.log>):** `native: Skython.Send registered` at <time>. `test_m15_send.py` 5 passed: payload round trip `{"channel", "payload", "truncated": false}`, seq 1; 70000-byte payload -> 65536 bytes, `truncated: true`; filter `SKYTHON_CASE_<TAG>` matched `skython_case_<tag>`; other, empty and 257-byte channels never arrived, one `dropped a call` warning. Deployed `Data\Scripts\Skython.pex` (stable 36082f6c01b9f9e125fe2a239e4a7d8afc787c7d2b3424182cecbdbcaad22341). Compile facts: `Hidden` needs `TESV_Papyrus_Flags.flg`; the compiler exits 0 on errors; one `-i` only; `.pex` embeds compile time and source mtime. **Owner removal list:** `Data\Scripts\Skython.pex`.
```
```bash
git add spikes/RESULTS.md
git commit -m "docs(results): Task 46 Skython.Send verified live"
```
