# Task 27: Game backend, `call_static` and `game.state`

Adapted from: branch `plan-out-of-process`, task 17a (`task-17a-dispatch-skeleton-call-static-and-game-state.md`). The IO thread, `Outbox`, `ConnId`, frames and the per-connection cap are gone from C++: core's `spy::Dispatcher` (Task 18) now owns caps, readiness, deadlines, load cancellation and exception handling. What is left here is the game side: a `spy::Backend` that turns each request into one `AddTask` on the main thread, plus the signature lookup and the primitive value conversion that `call_static` needs.

> Part of the [skyrim-python v1 plan](README.md). Read the index's **Global Constraints**, **Fixed paths** and **Contracts** first. Spec: `docs/specs/2026-09-30-skyrim-python-design.md` (draft 4), sections 4.1, 4.3-4.5. Steps use checkbox (`- [ ]`) syntax.

> **Gate:** start after Task 26 is done and `spikes/RESULTS.md` shows S2 (Task 4), S9 (Task 5) and S4 (Task 6) PASS. S7 (Task 9) is needed before Tasks 28 and 29, not here; if you start before S7 exists, the signature code below is unverified and the first compile is the check (grep `plugin/extern/CommonLibSSE-NG` for each name in the Verify list).
>
> **Runs on:** the Windows build box plus the game; live tests run on Linux through the dev bridge (`skyrim.connect()` from Linux, or the raw helpers in `livetest/support.py`).
>
> **STOP and ask the owner (do not improvise) if:** S2, S4 or S9 is FAIL; a name in the Verify list does not exist and no obvious equivalent is found; the game does not reach the main menu within 10 minutes; the build box does not answer `ssh` within 10 s; the log shows a runtime other than AE 1.6.1170.
>
> **Verify (grep `plugin/extern/CommonLibSSE-NG`; `API-VERIFICATION.md` has already checked these at the pin):** `VirtualMachine::GetScriptObjectType(BSFixedString, BSTSmartPointer<ObjectTypeInfo>&)`; `ObjectTypeInfo::GetParent`, `GetNumMemberFuncs`, `GetMemberFuncIter`, `GetNumGlobalFuncs`, `GetGlobalFuncIter`; `IFunction::GetName`, `GetParamCount`, `GetParam(i, BSFixedString&, TypeInfo&)`; `Variable::GetType`, `IsBool/IsInt/IsFloat/IsString`, `SetSInt/SetFloat/SetBool/SetString`, `IsNoneObject`; `DispatchStaticCall(BSFixedString, BSFixedString, IFunctionArguments*, BSTSmartPointer<IStackCallbackFunctor>&)` (non-const lvalue callback); `IStackCallbackFunctor::CanSave() const` (virtual, not pure); `RE::UI::GameIsPaused()`; `RE::Calendar::GetDaysPassed()`.

**Files:**
- Replace: `plugin/src/GameBackend.h`, `plugin/src/GameBackend.cpp` (Task 26 created interim versions with the same names; this task overwrites both files whole)
- Create: `plugin/src/Ops.h` (final), `plugin/src/Ops.cpp` (`game.state` extras and `papyrus.call_static`; Tasks 29 and 30 add ops)
- Create: `plugin/src/Signatures.h` (final), `plugin/src/Signatures.cpp` (`IEquals`, `FindFunction`, `BindArgs`; Task 28 adds `IsA` and `FindProperty`)
- Create: `plugin/src/PapyrusValues.h` (final), `plugin/src/PapyrusValues.cpp` (primitives, `None`, `DescribeForm`; Tasks 28 and 29 fill in the rest)
- Unchanged: `plugin/src/PythonHost.cpp`'s `InstallModuleHost` call (marked `// interim backend: replaced in Task 27`); it already uses `spy::game::GetDispatcher()`, which now resolves to the real backend. Only that comment is updated.
- Modify: `plugin/CMakeLists.txt` (add the four new `.cpp` files to the `SkyrimPythonCore` target)
- Modify: `livetest/support.py` (append `send_request` if Task 26 did not define it)
- Create: `livetest/test_m1_mainmenu.py`, `livetest/test_m1_calls.py` (Tasks 28 and 29 append to `test_m1_calls.py`)

**Interfaces:**
- Consumes (core, Tasks 14-18): `spy::Value`, `FormRef`, `Fingerprint`, `form_ref_to_json`, `value_kind` (`spy/values.h`); `is_runtime_form_id`, `format_form_key` (`spy/refs.h`); `CallArgs`, `parse_call_args` (`spy/requests.h`); `ProtocolError`, `ErrorCode`, `json` (`spy/errors.h`); `OpResult`, `Done`, `Cancelled`, `Progress`, `Backend`, `Dispatcher`, `DispatcherOptions` (`spy/dispatcher.h`); `Config` (`spy/config.h`).
- Consumes (plugin, Task 25): `spy::log::Info/Warn/Error/Debug`, `spy::settings::Get()`, and these lifecycle wrappers in `Lifecycle.h`: `spy::life::Epoch()`, `GameLoaded()`, `Loading()`, `Paused()`, `OpenMenus()`, `LoadsStarted()`, `NotReadyReason(bool require_unpaused) -> std::optional<std::string>`, `BaseState() -> json` (`LifecycleState::base_state()`). If Task 25 spelled any of these differently, use its names; do not add a second lifecycle.
- Consumes (Task 26): `spy::InstallModuleHost(ModuleHost)` call site; `livetest/conftest.py` fixtures `session` and `game` (a `skyrim.Game` connected through the bridge); `livetest/support.py` `raw_connect(session) -> socket`, `rpc(sock, op, args=None, req_id=1, timeout=30.0) -> dict`, `encode`, `read_frame`.
- Consumes from spikes: S2 (Task 4, measured; index Global Constraints, Papyrus dispatch rules): the VM never frees the `IFunctionArguments` passed to a dispatch, so the plugin owns them: the `Callback` holds the call's `DynArgs` and deletes it after reading the result, never earlier (phase D, 1000/1000); when `Dispatch*Call` returns false the args are kept, not freed, and logged once (ruling R37: unmeasured, the callback might still fire); the callback may run on the main thread or a VM thread.
- Produces:
  - `spy::game::Backend() -> spy::Backend&` (the `GameBackend` singleton) and `spy::game::GetDispatcher() -> spy::Dispatcher&` (built on first use from `settings::Get()`: `default_timeout_ms`, `max_inflight_total`, and `on_late` logging each dropped late result with `log::Warn`; reaper started; immortal).
  - `spy::ops::Run(const std::string& op, const json& args, Done, Cancelled, Progress)` (main thread only; never throws), `spy::ops::AddStateExtras(json&)` (main thread), `spy::ops::WithGameState(json detail) -> json`, `spy::ops::NotReady(const std::string& reason) -> OpResult`.
  - `spy::pv::*` and `spy::sig::*` declarations (final headers; bodies arrive in Tasks 27-29 as named in the headers). `pv::FloatToJson(float)` is core's float encoder (`value_to_json`): shortest round-trip text, a non-finite Float becomes `null` (`PROTOCOL.md`); every Float leaving the plugin goes through it. `sig::FindProperty` returns the whole `PropertyTypeInfo` (Task 30 refuses full properties).
  - `game.state` result keys: `data_ready, game_loaded, loading, interactive, paused, menus, epoch, player, game_days_passed, diagnostics{late_results_dropped, in_flight}` (Task 31 adds `max_task_drain_ms` and `max_task_drain_count` to `diagnostics`; `player` and `game_days_passed` are `null` unless a game is loaded). `Backend::BaseState()`, which `_skyrim.state()` and every `timeout`/`not_ready` detail read, carries the same keys **without** `player` and `game_days_passed`: it never blocks, so it cannot read the engine, and it leaves the keys out rather than report a wrong `null`.

Rules this task implements (spec 4.3-4.5):
- `GameBackend::Run` never blocks the caller (a Python thread inside `_skyrim.submit`): it queues exactly one `AddTask` and returns. The queued task checks `cancelled()` first (timed out, cancelled or a load started while queued) and does nothing if so.
- `game.state` is answered **immediately** from `life::BaseState()` when no game is loaded or a save is loading. Only when a game is loaded does it queue one `AddTask` for `player` and `game_days_passed`, and that task rechecks `Loading()` before touching the engine.
- Main thread: recheck readiness, find the function, check every argument against the declared parameter types (no default values: the count must match), dispatch.
- VM callback: it owns the call's `DynArgs` and deletes it after reading the result (S2); on a false `Dispatch*Call` return the dispatch site keeps the args and logs once (R37). It overrides `CanSave()` to return false (ruling R38, as SkyrimPlatform does): our functor is never written into a save. **Primitives and `None` are converted on the VM thread** (no engine state). An object result or an array of objects costs one more `AddTask`, because turning a Form into a reference calls `LookupByID`/`GetFile`. No Python runs on any of these threads (Global Constraints).
- Readiness at submit, deadlines, `busy`, load cancellation and late-result counting are the Dispatcher's (Task 18); this task does not duplicate them.
- Member functions are looked up in the script's empty state only.

**Known limits (Task 32 puts them in the README):**
- Papyrus runtime errors are invisible: the VM callback gets only a result `Variable`. A script error inside the called function is logged by the game to `Papyrus.0.log`; the caller sees `None` or, if no callback fires, `Timeout`. `VmError` means only "the VM refused to dispatch".
- The functor returns false from `CanSave()`: a save made while a call is in flight does not record our callback. After loading that save the call never completes in the loaded game; the caller sees `NotReady` (the load cancels it) or `Timeout`. Task 31's live test "save during a latent call" pins this.
- A `DynArgs` whose callback never fires (the VM dropped the call, or a refused dispatch whose callback never comes) is never freed: about 100 bytes each (Task 4's estimate). Freeing it in the `Callback` destructor instead is unmeasured (SkyrimPlatform comparison, Top 3, deferred).
- A call made while the game is paused waits: Papyrus does not advance. Use `require_unpaused` or a generous timeout.

- [ ] **Step 1: Write the live tests first** (they fail until the plugin is rebuilt)

Append to `livetest/support.py` only if Task 26 did not already define it:
```python
def send_request(sock, op: str, args: dict | None = None, req_id: int = 1) -> None:
    """Send one request frame without waiting (for pipelining tests); read replies with read_frame."""
    sock.sendall(encode({"type": "request", "id": req_id, "op": op, "args": args or {}}))
```
(The request shape is the one in `protocol/v1/fixtures/requests.json`, Task 11. If that file has no `"type"` member, drop it here too.)

`livetest/test_m1_mainmenu.py` (run with the game at the main menu, no save loaded):
```python
"""Run with the game launched and sitting at the main menu (no save loaded)."""
import pytest

from skyrim import errors
from support import raw_connect, rpc


@pytest.fixture
def main_menu(game):
    st = game.state()
    if st["game_loaded"]:
        pytest.skip("needs the main menu with no save loaded")
    return st


def test_state_at_main_menu(game, main_menu):
    assert main_menu["data_ready"] is True and main_menu["interactive"] is False
    assert main_menu["player"] is None and main_menu["game_days_passed"] is None
    assert "Main Menu" in main_menu["menus"]


def test_state_shape_is_stable(session, main_menu):
    s = raw_connect(session)
    try:
        st = rpc(s, "game.state")["result"]
    finally:
        s.close()
    for key in ("data_ready", "game_loaded", "loading", "interactive", "epoch", "menus", "paused", "player",
                "game_days_passed", "diagnostics"):
        assert key in st, key
    assert st["diagnostics"]["late_results_dropped"] >= 0 and st["diagnostics"]["in_flight"] >= 0


def test_calls_are_not_ready_with_reason(game, main_menu):
    with pytest.raises(errors.NotReady) as ei:
        game.call("Utility", "RandomInt", 1, 6)
    d = ei.value.detail
    assert d["reason"] == "no_game" and d["paused"] in (True, False) and "Main Menu" in d["menus"]
    # Readiness is checked by the Dispatcher before any op runs, so ops built later are already not_ready.
    with pytest.raises(errors.NotReady):
        game.form("Skyrim.esm|0x012EB7")
```

`livetest/test_m1_calls.py` (needs a loaded save; Tasks 28 and 29 append):
```python
"""Needs a save loaded (interactive). Spike S4's recorded quest/alias go in env vars (used by Task 29):
SPY_S4_QUEST=0x<quest form id> SPY_S4_ALIAS=<alias id> SPY_S4_REF=0x<expected reference form id>
"""
import time

import pytest

from skyrim import errors
from support import raw_connect, rpc

PLAYER_ID = 0x14
IRON_SWORD = "Skyrim.esm|0x012EB7"


@pytest.fixture(autouse=True)
def ready(game):
    game.wait_ready(timeout=120)


def raw_state(session) -> dict:
    s = raw_connect(session)
    try:
        return rpc(s, "game.state")["result"]
    finally:
        s.close()


def raw_call(session, script, function, args, **extra) -> dict:
    s = raw_connect(session)
    try:
        return rpc(s, "papyrus.call_static", {"script": script, "function": function, "args": args, **extra}, timeout=30)
    finally:
        s.close()


def test_state_reports_player(game):
    st = game.state()
    assert st["interactive"] is True and st["loading"] is False and st["epoch"] >= 1
    assert st["player"].form_id == PLAYER_ID and st["player"].form_key == "Skyrim.esm|0x000014"
    assert st["player"].type == "Actor" and st["game_days_passed"] > 0


def test_static_int_call(game):
    for _ in range(20):
        assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6


def test_primitive_result_types(game):
    assert game.call("Game", "IsPluginInstalled", "Skyrim.esm") is True                  # Bool
    assert 0.0 <= game.call("Utility", "RandomFloat", 0.0, 1.0) <= 1.0                   # Float
    assert game.call("StringUtil", "Substring", "hello", 1, 3) == "ell"                  # String (SKSE)
    assert game.call("StringUtil", "GetLength", "hello") == 5                            # Int


def test_float_results_do_not_widen_noisily(game):
    assert game.call("Math", "Sqrt", 0.0001) == 0.01  # not 0.009999999776482582


def test_latent_function_completes_with_none(game):
    t0 = time.monotonic()
    assert game.call("Utility", "Wait", 0.5) is None
    assert time.monotonic() - t0 >= 0.4


def test_unknown_script_and_function(game):
    with pytest.raises(errors.UnknownFunction):
        game.call("NoSuchScriptAnywhere", "Foo")
    with pytest.raises(errors.UnknownFunction):
        game.call("Utility", "NoSuchFunction")


def test_wrong_argument_type_names_the_parameter(game):
    with pytest.raises(errors.TypeMismatch, match="aiMin"):
        game.call("Utility", "RandomInt", "one", 6)


def test_wrong_argument_count(game):
    with pytest.raises(errors.TypeMismatch, match="takes 2"):
        game.call("Utility", "RandomInt", 1)


def test_int_out_of_range_is_rejected_by_the_plugin(session):
    # Sent raw: the Python client would refuse it before it left the process.
    assert raw_call(session, "Utility", "RandomInt", [1, 2147483648])["error"]["code"] == "type_mismatch"


def test_object_results_are_unsupported_until_task_28(game):
    # Replaced in Task 28 by test_static_call_returns_form.
    with pytest.raises(errors.Unsupported):
        game.call("Game", "GetPlayer")


def test_timeout_carries_game_state(game):
    with pytest.raises(errors.Timeout) as ei:
        game.call("Utility", "Wait", 3.0, timeout=0.5)
    assert "paused" in ei.value.detail and isinstance(ei.value.detail["menus"], list)
    assert 1 <= game.call("Utility", "RandomInt", 1, 6) <= 6  # the bridge and the game carry on


@pytest.mark.parametrize("bad", ["soon", -5, 0, 1.5, None, 10**12])
def test_odd_timeout_values_never_hang_or_crash(session, bad):
    r = raw_call(session, "Utility", "RandomInt", [1, 6], timeout_ms=bad)
    if r["ok"]:
        assert 1 <= r["result"] <= 6
    else:  # core rejects the value (bad_request) or a 1 ms deadline expires (timeout)
        assert r["error"]["code"] in ("bad_request", "timeout")
    assert raw_state(session)["data_ready"] is True


def steady_late_count(session, settle: float = 3.5, tries: int = 5) -> int:
    """late_results_dropped once nothing is still on its way: two equal readings `settle` seconds apart."""
    prev = raw_state(session)["diagnostics"]["late_results_dropped"]
    for _ in range(tries):
        time.sleep(settle)
        now = raw_state(session)["diagnostics"]["late_results_dropped"]
        if now == prev:
            return now
        prev = now
    raise AssertionError("late_results_dropped kept changing: late results from earlier tests are still arriving")


def test_late_result_after_timeout_is_dropped_and_counted(session, game):
    # test_timeout_carries_game_state left a 3 s Utility.Wait running in the game; its late result must be counted
    # before `before` is read, or this test sees two drops instead of one.
    before = steady_late_count(session)
    with pytest.raises(errors.Timeout):
        game.call("Utility", "Wait", 1.0, timeout=0.2)
    time.sleep(2.0)  # the wait finishes in the game; its result must be dropped, not delivered
    assert raw_state(session)["diagnostics"]["late_results_dropped"] == before + 1
```
If `Math.Sqrt` or `StringUtil` is missing on this runtime, swap in any function with the same result type and record which in `spikes/RESULTS.md`.

- [ ] **Step 2: Signatures (the part `call_static` needs)**

`plugin/src/Signatures.h` (final; Task 28 adds the two missing bodies):
```cpp
#pragma once
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>
#include <string_view>
#include <vector>

namespace spy::sig {
bool IEquals(std::string_view a, std::string_view b);  // Papyrus names are case-insensitive
// True if `script` is `ancestor` or extends it. False if `script` is not a loaded script. (Task 28)
bool IsA(const std::string& script, const std::string& ancestor);
// Throws unknown_function (no such script or function) or bad_request (global vs member mix-up).
// Callers run this BEFORE resolving any object, so "global called as member" is bad_request, not type_mismatch.
RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member);
// Converts arguments to the declared parameter types. Throws type_mismatch naming the parameter.
std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified);
// The property `property` of `script` or its parents (its declared `type`; `getFunction`/`setFunction` set means a
// full property). Throws not_found. (Task 28)
RE::BSScript::PropertyTypeInfo FindProperty(const std::string& script, const std::string& property);
}
```

`plugin/src/Signatures.cpp`:
```cpp
#include "Signatures.h"
#include "PapyrusValues.h"
#include "spy/errors.h"
#include <algorithm>
#include <cctype>
#include <format>

namespace spy::sig {
namespace {
RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> TypeOf(const std::string& script)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (!VM()->GetScriptObjectType(RE::BSFixedString(script), info) || !info) return {};
    return info;
}
}

bool IEquals(std::string_view a, std::string_view b)
{
    return a.size() == b.size() && std::equal(a.begin(), a.end(), b.begin(), [](char x, char y) {
        return std::tolower(static_cast<unsigned char>(x)) == std::tolower(static_cast<unsigned char>(y));
    });
}

RE::BSTSmartPointer<RE::BSScript::IFunction> FindFunction(const std::string& script, const std::string& function, bool member)
{
    auto info = TypeOf(script);
    if (!info) throw ProtocolError(ErrorCode::unknown_function, "no script named " + script + " is loaded");
    bool otherKind = false;
    // Global functions belong to the declaring script only; member functions are inherited.
    for (auto* t = info.get(); t; t = member ? t->GetParent() : nullptr) {
        for (std::uint32_t i = 0; i < t->GetNumMemberFuncs(); ++i) {
            const auto& f = t->GetMemberFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (member) return f;
                otherKind = true;
            }
        }
        for (std::uint32_t i = 0; i < t->GetNumGlobalFuncs(); ++i) {
            const auto& f = t->GetGlobalFuncIter()[i].func;
            if (f && IEquals(f->GetName().c_str(), function)) {
                if (!member) return f;
                otherKind = true;
            }
        }
    }
    if (otherKind)
        throw ProtocolError(ErrorCode::bad_request, script + "." + function +
            (member ? " is a global function; use papyrus.call_static" : " is a member function; use papyrus.call_method on an object"));
    throw ProtocolError(ErrorCode::unknown_function, "script " + script + " has no function " + function);
}

std::vector<RE::BSScript::Variable> BindArgs(const RE::BSScript::IFunction& fn, const std::vector<Value>& args, const std::string& qualified)
{
    const auto n = fn.GetParamCount();
    if (args.size() != n)
        throw ProtocolError(ErrorCode::type_mismatch, std::format(
            "{} takes {} argument(s), got {} (the plugin does not fill in Papyrus default values)", qualified, n, args.size()));
    std::vector<RE::BSScript::Variable> out(n);
    for (std::uint32_t i = 0; i < n; ++i) {
        RE::BSFixedString name;
        RE::BSScript::TypeInfo type;
        fn.GetParam(i, name, type);
        pv::ToVariable(args[i], type, std::format("{} (argument {} of {})", name.c_str(), i, qualified), out[i]);
    }
    return out;
}
}
```

- [ ] **Step 3: Papyrus values (primitives and `None`; forms are described, not yet resolved)**

`plugin/src/PapyrusValues.h` (final):
```cpp
#pragma once
#include "spy/errors.h"
#include "spy/values.h"
#include <RE/Skyrim.h>
#include <string>

// Main thread only, except NeedsMainThread and TypeName: every other function touches forms or the VM.
namespace spy::pv {
RE::TESForm* ResolveForm(const FormRef& ref);            // Task 28. throws not_found (missing or deleted) / stale_reference
RE::BGSBaseAlias* ResolveAlias(const AliasRef& ref);     // Task 29. throws not_found / type_mismatch
FormRef DescribeForm(const RE::TESForm* form);           // Task 27
AliasRef DescribeAlias(const RE::BGSBaseAlias* alias);   // Task 29
std::string ScriptClassOf(const RE::TESForm* form);      // Task 27. "Actor", "Weapon", ...; "Form" if unknown

struct Target
{
    RE::BSTSmartPointer<RE::BSScript::Object> object;  // bound script object to dispatch on
    std::string desc;
};
// Task 29. The object a method or property is called on. `script` must be attached to it, or be its native
// class or an ancestor of that; otherwise type_mismatch. A cold form is bound through PackHandle (spike S7).
Target ResolveTarget(const Value& object, const std::string& script);

void ToVariable(const Value& v, const RE::BSScript::TypeInfo& declared, const std::string& param, RE::BSScript::Variable& out);  // 27 primitives; 28 forms, arrays; 29 aliases
json FromVariable(const RE::BSScript::Variable& v);      // 27 primitives and None; 28 forms and arrays; 29 aliases
bool NeedsMainThread(const RE::BSScript::Variable& v);   // Task 27. true for an object or an array of objects
std::string TypeName(const RE::BSScript::TypeInfo& t);   // Task 27
json FloatToJson(float f);                               // Task 27. core's encoder: shortest round-trip text; non-finite -> null
}
```

`plugin/src/PapyrusValues.cpp` (Task 27 version; Task 28 replaces the whole file):
```cpp
#include "PapyrusValues.h"
#include "Lifecycle.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <format>

namespace spy::pv {
namespace {
using RE::BSScript::TypeInfo;
using RE::BSScript::Variable;
using Raw = TypeInfo::RawType;

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

std::string KeyOrId(const RE::TESForm* form)
{
    const auto id = form->GetFormID();
    if (!is_runtime_form_id(id))
        if (const auto* file = form->GetFile(0))
            return format_form_key({std::string(file->GetFilename()), form->GetLocalFormID()});
    return std::to_string(id);
}

Fingerprint LiveFingerprint(const RE::TESForm* form)
{
    const RE::TESForm* base = form;
    if (const auto* ref = form->As<RE::TESObjectREFR>())
        if (const auto* b = ref->GetBaseObject()) base = b;
    return {KeyOrId(base), ScriptClassOf(form)};
}
}

// A Papyrus Float is 32-bit. Widening it prints 0.1 as 0.10000000149011612. Core's encoder (value_to_json) prints the
// shortest text that round-trips the float, and sends NaN and infinity as null (PROTOCOL.md): one rule for every Float.
json FloatToJson(float f)
{
    Value v;
    v.v = f;
    return value_to_json(v);
}

std::string ScriptClassOf(const RE::TESForm* form)
{
    RE::BSTSmartPointer<RE::BSScript::ObjectTypeInfo> info;
    if (VM()->GetScriptObjectType(static_cast<RE::VMTypeID>(form->GetFormType()), info) && info) return info->GetName();
    return "Form";
}

FormRef DescribeForm(const RE::TESForm* form)
{
    FormRef r;
    r.form_id = form->GetFormID();
    r.type = ScriptClassOf(form);
    r.epoch = life::Epoch();
    if (is_runtime_form_id(*r.form_id)) r.fingerprint = LiveFingerprint(form);
    else if (form->GetFile(0)) r.form_key = KeyOrId(form);
    return r;
}

std::string TypeName(const TypeInfo& t)
{
    if (t.IsObject() && t.GetTypeInfo()) return t.GetTypeInfo()->GetName();
    switch (t.GetRawType()) {
    case Raw::kInt: return "Int";
    case Raw::kFloat: return "Float";
    case Raw::kBool: return "Bool";
    case Raw::kString: return "String";
    case Raw::kIntArray: return "Int[]";
    case Raw::kFloatArray: return "Float[]";
    case Raw::kBoolArray: return "Bool[]";
    case Raw::kStringArray: return "String[]";
    default: return t.IsArray() ? "Object[]" : "None";
    }
}

bool NeedsMainThread(const Variable& v)
{
    const auto t = v.GetType();
    if (t.IsObjectArray()) return true;  // conservative: any array of objects
    return t.IsObject() && !v.IsNoneObject();
}

void ToVariable(const Value& v, const TypeInfo& t, const std::string& param, Variable& out)
{
    auto mismatch = [&]() -> void {
        throw ProtocolError(ErrorCode::type_mismatch,
            "parameter " + param + " expects " + TypeName(t) + ", got " + std::string(value_kind(v)));
    };
    if (t.IsArray() || t.IsObject())
        throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " is a " + TypeName(t) +
            "; objects and arrays can be sent from Task 28 on");
    switch (t.GetRawType()) {
    case Raw::kInt:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetSInt(*i);
        break;
    case Raw::kFloat:
        if (const auto* i = std::get_if<std::int32_t>(&v.v)) return out.SetFloat(static_cast<float>(*i));
        if (const auto* f = std::get_if<float>(&v.v)) return out.SetFloat(*f);
        break;
    case Raw::kBool:
        if (const auto* b = std::get_if<bool>(&v.v)) return out.SetBool(*b);
        break;
    case Raw::kString:
        if (const auto* s = std::get_if<std::string>(&v.v)) return out.SetString(*s);
        break;
    default:
        throw ProtocolError(ErrorCode::unsupported, "parameter " + param + " has a type protocol v1 cannot send: " + TypeName(t));
    }
    mismatch();
}

json FromVariable(const Variable& v)
{
    const auto t = v.GetType();
    if (t.GetRawType() == Raw::kNone || v.IsNoneObject()) return nullptr;
    if (v.IsBool()) return v.GetBool();
    if (v.IsInt()) return v.GetSInt();
    if (v.IsFloat()) return FloatToJson(v.GetFloat());
    if (v.IsString()) return std::string(v.GetString());
    throw ProtocolError(ErrorCode::unsupported, "the result is an object or array, which this build cannot return yet (Task 28)");
}
}
```
`ResolveForm`, `ResolveAlias`, `DescribeAlias` and `ResolveTarget` have no body yet; nothing in this task calls them.

- [ ] **Step 4: Ops (`game.state` extras and `call_static`)**

`plugin/src/Ops.h` (final):
```cpp
#pragma once
#include "spy/dispatcher.h"
#include "spy/requests.h"

namespace spy::ops {
// Main thread only. Validation failures become Err results; nothing throws out of Run.
// `done` may be called on the main thread or a VM thread; the Dispatcher accepts either (Task 18).
void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress);

// Fills `player` and `game_days_passed` (main thread; only when a game is loaded and not loading).
void AddStateExtras(json& state);

// Adds `paused` and `menus` to an error detail (spec 4.1).
json WithGameState(json detail = json::object());
OpResult NotReady(const std::string& reason);
}
```

`plugin/src/Ops.cpp`:
```cpp
#include "Ops.h"
#include "Lifecycle.h"
#include "Log.h"
#include "PapyrusValues.h"
#include "Signatures.h"
#include "spy/refs.h"
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>
#include <utility>

namespace spy::ops {
namespace {
using RE::BSScript::Variable;

std::atomic<std::thread::id> g_mainThread{};  // written on the main thread by Run, read on VM threads

RE::BSScript::Internal::VirtualMachine* VM() { return RE::BSScript::Internal::VirtualMachine::GetSingleton(); }

// Arguments for one dispatch. The VM never frees them (spike S2, Task 4): the Callback owns this object and deletes
// it after reading the result. On a false Dispatch*Call return it is kept (R37), see KeptOnRefusal.
class DynArgs final : public RE::BSScript::IFunctionArguments
{
public:
    explicit DynArgs(std::vector<Variable> v) : v_(std::move(v)) {}
    bool operator()(RE::BSScrapArray<Variable>& dst) const override
    {
        dst.resize(static_cast<std::uint32_t>(v_.size()));
        for (std::size_t i = 0; i < v_.size(); ++i) dst[static_cast<std::uint32_t>(i)] = v_[i];
        return true;
    }

private:
    std::vector<Variable> v_;
};

void Convert(const Done& done, const Variable& result)
{
    try {
        done(OpResult::Ok(pv::FromVariable(result)));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    } catch (const std::exception& e) {
        log::Error(std::string("ops: converting a result threw: ") + e.what());
        done(OpResult::Err(ErrorCode::internal, "internal error converting the result"));
    } catch (...) {
        log::Error("ops: converting a result threw an unknown exception");
        done(OpResult::Err(ErrorCode::internal, "internal error converting the result"));
    }
}

// A refused dispatch (Dispatch*Call returned false): the args are NOT freed, because the callback might still fire
// (unmeasured, R37); if it does, it frees them. With FindFunction and BindArgs first, a refusal is not expected.
void KeptOnRefusal(const std::string& qualified)
{
    static std::once_flag once;
    std::call_once(once, [&] {
        log::Warn("ops: the VM refused a dispatch (" + qualified + "); its arguments are kept, not freed (R37). "
                  "Logged once; later refusals leak silently.");
    });
}

class Callback final : public RE::BSScript::IStackCallbackFunctor
{
public:
    Callback(Done d, DynArgs* args) : done_(std::move(d)), args_(args) {}
    void operator()(Variable result) override
    {
        // This call's arguments (S2: the VM never frees them). Freed when this scope ends, after the result was read
        // and converted or queued; `result` is our own copy. The destructor does not free them (see Known limits).
        std::unique_ptr<DynArgs> owned{std::exchange(args_, nullptr)};
        // Primitives and None touch no engine state: convert here, on whatever thread the VM called us on.
        // Objects (forms, aliases, arrays of them) need LookupByID/GetFile: main thread only.
        if (!pv::NeedsMainThread(result) || std::this_thread::get_id() == g_mainThread.load())
            return Convert(done_, result);
        auto done = done_;
        SKSE::GetTaskInterface()->AddTask([done, result] { Convert(done, result); });
    }
    void SetObject(const RE::BSTSmartPointer<RE::BSScript::Object>&) override {}
    // Never written into a save (as SkyrimPlatform does): a save during a latent call does not record this functor,
    // and after loading that save the call ends NotReady or Timeout (Task 31 tests it). R38.
    bool CanSave() const override { return false; }

private:
    Done done_;
    DynArgs* args_;  // owned; freed in operator()
};

// The main thread's own view; life::Paused() is what the Dispatcher saw at submit.
bool PausedNow()
{
    auto* ui = RE::UI::GetSingleton();
    return ui && ui->GameIsPaused();
}

OpResult PausedError() { return OpResult::Err(ErrorCode::not_ready, "the game is paused", WithGameState({{"reason", "paused"}})); }

void CallStatic(const CallArgs& c, const Done& done)
{
    if (c.require_unpaused && PausedNow()) return done(PausedError());
    const auto qualified = c.script + "." + c.function;
    auto fn = sig::FindFunction(c.script, c.function, false);
    auto vars = sig::BindArgs(*fn, c.args, qualified);
    auto* args = new DynArgs(std::move(vars));
    RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb{new Callback(done, args)};  // cb owns args
    if (!VM()->DispatchStaticCall(RE::BSFixedString(c.script), RE::BSFixedString(c.function), args, cb)) {
        KeptOnRefusal(qualified);  // never `delete args` here (R37)
        done(OpResult::Err(ErrorCode::vm_error, "the VM refused the call to " + qualified));
    }
}
}

void AddStateExtras(json& s)
{
    if (!life::GameLoaded() || life::Loading()) return;  // recheck: a load may have started since submit
    if (auto* player = RE::PlayerCharacter::GetSingleton()) s["player"] = form_ref_to_json(pv::DescribeForm(player));
    if (auto* cal = RE::Calendar::GetSingleton()) s["game_days_passed"] = pv::FloatToJson(cal->GetDaysPassed());  // 32-bit float
}

json WithGameState(json detail)
{
    detail["paused"] = life::Paused();
    detail["menus"] = life::OpenMenus();
    return detail;
}

OpResult NotReady(const std::string& reason)
{
    return OpResult::Err(ErrorCode::not_ready, "no game is loaded and interactive", WithGameState({{"reason", reason}}));
}

void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress)
{
    g_mainThread = std::this_thread::get_id();
    try {
        if (auto why = life::NotReadyReason(false)) return done(NotReady(*why));  // recheck on the main thread
        if (op == "papyrus.call_static") return CallStatic(parse_call_args(args, false), done);
        // Task 29 adds form.resolve and papyrus.call_method; Task 30 adds properties and batch.
        done(OpResult::Err(ErrorCode::unsupported, "op " + op + " is not available in this build"));
    } catch (const ProtocolError& e) {
        done(OpResult::Err(e.code, e.what()));
    } catch (const std::exception& e) {
        log::Error("ops: " + op + " threw: " + e.what());
        done(OpResult::Err(ErrorCode::internal, "internal error"));
    } catch (...) {
        log::Error("ops: " + op + " threw an unknown exception");
        done(OpResult::Err(ErrorCode::internal, "internal error"));
    }
    (void)cancelled;  // used by papyrus.batch (Task 30)
    (void)progress;
}
}
```
`game.state` never reaches `Run`: `GameBackend` answers it (Step 5). Task 30 rejects it as a batch step.

- [ ] **Step 5: The game backend and the dispatcher**

`plugin/src/GameBackend.h`:
```cpp
#pragma once
#include "spy/dispatcher.h"

namespace spy::game {
// The spy::Backend that serves the game: one AddTask per request, ops::Run on the main thread.
spy::Backend& Backend();
// Built on first use from settings::Get() (default_timeout_ms, max_inflight_total), reaper started. Immortal:
// VM callbacks can complete requests after static destruction has begun, so it is never destroyed.
spy::Dispatcher& GetDispatcher();
}
```

`plugin/src/GameBackend.cpp`:
```cpp
#include "GameBackend.h"
#include "Lifecycle.h"
#include "Log.h"
#include "Ops.h"
#include "Settings.h"
#include <SKSE/SKSE.h>
#include <mutex>

namespace spy::game {
namespace {
OpResult Internal() { return OpResult::Err(ErrorCode::internal, "internal error"); }

class GameBackend final : public spy::Backend
{
public:
    void Run(const std::string& op, const json& args, Done done, Cancelled cancelled, Progress progress) override
    {
        if (op == "game.state") return State(std::move(done), std::move(cancelled));
        // Exactly one AddTask; never block the submitting Python thread.
        SKSE::GetTaskInterface()->AddTask([op, args, done, cancelled, progress] {
            if (cancelled()) return;  // timed out, cancelled or a load started while queued
            try {
                ops::Run(op, args, done, cancelled, progress);
            } catch (const std::exception& e) {
                log::Error("game backend: " + op + " threw out of Run: " + e.what());
                done(Internal());
            } catch (...) {
                log::Error("game backend: " + op + " threw an unknown exception out of Run");
                done(Internal());
            }
        });
    }

    // No player / game_days_passed here: BaseState never blocks, so it cannot ask the main thread (index Contracts).
    json BaseState() override { return WithDiagnostics(life::BaseState()); }

    std::optional<std::string> NotReadyReason(bool require_unpaused) override { return life::NotReadyReason(require_unpaused); }

    std::uint32_t LoadsStarted() override { return life::LoadsStarted(); }

private:
    static json WithDiagnostics(json s)
    {
        auto& d = GetDispatcher();
        s["diagnostics"] = {{"late_results_dropped", d.LateResults()}, {"in_flight", d.InFlight()}};
        return s;
    }

    // The game.state op: BaseState() plus player and game_days_passed (null until AddStateExtras fills them).
    void State(Done done, Cancelled cancelled)
    {
        json s = WithDiagnostics(life::BaseState());
        s["player"] = nullptr;
        s["game_days_passed"] = nullptr;
        // Answer at once at the main menu or while loading; ask the main thread only when it can answer.
        if (!life::GameLoaded() || life::Loading()) return done(OpResult::Ok(std::move(s)));
        SKSE::GetTaskInterface()->AddTask([done, cancelled, s = std::move(s)]() mutable {
            if (cancelled()) return;
            try {
                ops::AddStateExtras(s);
                done(OpResult::Ok(std::move(s)));
            } catch (const std::exception& e) {
                log::Error(std::string("game backend: game.state threw: ") + e.what());
                done(Internal());
            } catch (...) {
                done(Internal());
            }
        });
    }
};
}

spy::Backend& Backend()
{
    static auto* backend = new GameBackend();  // immortal
    return *backend;
}

spy::Dispatcher& GetDispatcher()
{
    static spy::Dispatcher* dispatcher = [] {
        const auto& cfg = settings::Get();
        spy::DispatcherOptions opts;
        opts.default_timeout_ms = cfg.default_timeout_ms;
        opts.max_inflight_total = cfg.max_inflight_total;
        opts.on_late = [](const std::string& line) { log::Warn("game backend: " + line); };  // a result nobody waits for
        auto* d = new spy::Dispatcher(Backend(), opts);
        d->StartReaper();
        log::Info("game backend: dispatcher ready (default timeout " + std::to_string(cfg.default_timeout_ms) +
                  " ms, max in flight " + std::to_string(cfg.max_inflight_total) + ")");
        return d;
    }();
    return *dispatcher;
}
}
```
`BaseState()` calls `GetDispatcher()` for diagnostics; that is safe because `GetDispatcher()` is fully constructed before any request can reach the backend. `life::BaseState()` already holds `data_ready, game_loaded, loading, interactive, paused, menus, epoch` (Task 17's `base_state()`).

- [ ] **Step 6: Point `_skyrim` at the game dispatcher**

Task 26's `plugin/src/PythonHost.cpp` installs the module host with `.dispatcher = &spy::game::GetDispatcher()` and marks the call `// interim backend: replaced in Task 27`. Overwriting `GameBackend.{h,cpp}` in Step 5 is the whole switch: the call site needs no code change. Update only its comment:
```bash
grep -n "interim backend: replaced in Task 27" plugin/src/PythonHost.cpp
```
Expected: one line. Replace that comment line (and the one continuation comment line under it) with `// _skyrim talks to the game dispatcher (GameBackend, Task 27).`

`plugin/CMakeLists.txt`: Task 26 already added `src/GameBackend.cpp src/PyModule.cpp` to `SPY_CORE_SOURCES`. Add `src/Ops.cpp src/Signatures.cpp src/PapyrusValues.cpp` to the same list. They go in the core DLL, never in the loader.

Check that no Python header is included by any of the four new files (they must compile without `Python.h`):
```bash
grep -n "Python.h\|pybind11" plugin/src/GameBackend.* plugin/src/Ops.* plugin/src/Signatures.* plugin/src/PapyrusValues.* || echo "no Python includes: OK"
```
Expected: `no Python includes: OK`.

- [ ] **Step 7: Build, deploy, run the live tests**

Every wait is bounded and starts from this launch (`SINCE=`), so a log from an earlier run cannot satisfy it; loading a save is a manual step (the owner presses Continue). If nobody is at the keyboard, STOP and ask.
```bash
tools/remote_build.sh plugin SkyrimPython.dll && tools/remote_build.sh plugin SkyrimPythonCore.dll
tools/deploy.sh out/SkyrimPython.dll plugin/config/SkyrimPython.dev.ini --python --package --scripts livetest/scripts
tools/launch_game.sh
source tools/env.sh
export SINCE="$SKYRIM_PYTHON_STATE/launch.stamp"
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "game backend: dispatcher ready" 600 || echo "STOP: the dispatcher did not start; ask the owner"
# The bridge (and this run's session.json) exists only once the host reports; before that the tests would read the
# previous run's port and token.
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "python host: (running|failed|disabled)" 600
grep -q "python host: running" "$SKSE_LOG_DIR/SkyrimPython.log" || echo "STOP: Python is not running; read the log"
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "lifecycle: Main Menu open" 600
SKYRIM_LIVE=1 uv run --project python pytest livetest/test_m1_mainmenu.py -q -rs
# Manual: press Continue in the game. Then:
tools/wait_for_log.sh "$SKSE_LOG_DIR/SkyrimPython.log" "lifecycle: interactive = true" 300
SKYRIM_LIVE=1 uv run --project python pytest livetest/test_m1_calls.py -q -rs
unset SINCE
```
(Two `remote_build.sh` calls: the script fetches one DLL per call, and `deploy.sh` refuses a core DLL older than the loader. `livetest/scripts` exists since Task 26.)

Expected at the main menu: `test_m1_mainmenu.py`: `3 passed`. After the save is loaded: `test_m1_calls.py` passes (all tests; `test_odd_timeout_values_never_hang_or_crash` counts 6). Any skip whose reason is `live test: set SKYRIM_LIVE=1` is a failure: nothing ran. The core log shows one `game backend: late result dropped: ...` warning per dropped late result (from `on_late`). This is the first time a Papyrus call travels from Linux through the dev bridge, `_skyrim`, the Dispatcher and the VM. If `StringUtil` fails, SKSE's script is not loaded: check `Scripts/StringUtil.pex` and substitute, recording which. If `Utility.RandomInt` fails with `vm_error`, read `Papyrus.0.log` and STOP: S2 saw no refused dispatch for a validated target, so a refusal here means the dispatch rules from S2 do not hold (the log has one `ops: the VM refused a dispatch` warning; the args were kept, R37). If `test_timeout_carries_game_state` fails only because `paused`/`menus` are missing from the timeout detail, the Dispatcher (Task 18) is not adding `BaseState()`'s `paused` and `menus` to timeout errors as spec 4.1 requires: fix it there, not here.

- [ ] **Step 8: Commit**

```bash
git add plugin livetest && git commit -m "feat(plugin): game backend with call_static and game.state"
```
