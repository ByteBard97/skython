# CommonLibSSE-NG API verification for the M1.5/M2 plan

Every signature not listed here is in the v1 plan's file: [`../2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md`](../2026-10-01-skyrim-python-v1-embedded/API-VERIFICATION.md). Same pin: `39f9d07a6ffabea8fb559eee87ab7d27cd463e8a` (v10.1.0), read from `plugin/extern/CommonLibSSE-NG`. Each task appends the names it checked, with the header path and the verbatim declaration.

| Name | Task | Declaration at the pin (header under `include/`) | Notes |
|---|---|---|---|
| `TESDeathEvent` | 39 | `NiPointer<TESObjectREFR> actorDying; NiPointer<TESObjectREFR> actorKiller; bool dead;` (`RE/T/TESDeathEvent.h`, offsets 00/08/10) | `dead == false` → `dying`, `true` → `death` |
| `TESQuestStageEvent` | 39 | `void* finishedCallback; FormID formID; std::uint16_t stage; std::uint8_t itemIndex;` (`RE/T/TESQuestStageEvent.h`) | quest looked up with `TESForm::LookupByID(formID)` |
| `TESQuestStartStopEvent` | 39 | `FormID formID; bool started; bool failed;` (`RE/T/TESQuestStartStopEvent.h`) | `started` → `quest_start`, else `quest_stop` |
| `SKSE::ModCallbackEvent` | 39 | `RE::BSFixedString eventName; RE::BSFixedString strArg; float numArg; RE::TESForm* sender;` (`SKSE/Events.h`) | source: `[[nodiscard]] RE::BSTEventSource<ModCallbackEvent>* GetModCallbackEventSource() noexcept;` (`SKSE/API.h`) |
| `ScriptEventSourceHolder` | 39 | `static ScriptEventSourceHolder* GetSingleton(); template <class T> inline void AddEventSink(BSTEventSink<T>* a_sink); template <class T> inline void RemoveEventSink(BSTEventSink<T>* a_sink);` (`RE/S/ScriptEventSourceHolder.h`); bases include `BSTEventSource<TESDeathEvent>` (0370), `BSTEventSource<TESQuestStageEvent>` (0A50), `BSTEventSource<TESQuestStartStopEvent>` (0B00) | `BSTEventSource::AddEventSink` takes a spin lock (`BSSpinLockGuard locker(lock);`) and defers to `pendingRegisters` while notifying (`RE/B/BSTEvent.h`) |
| `RE::FormType::Max` | 39 | last enumerator of `enum class FormType`: `Max  //	8A` (`RE/F/FormTypes.h`); `ActorCharacter` is 0x3E | class table size |
| `IVirtualMachine::GetScriptObjectType` | 39 | `bool GetScriptObjectType(VMTypeID a_typeID, BSTSmartPointer<ObjectTypeInfo>& a_outTypeInfoPtr);` (`RE/I/IVirtualMachine.h`); `using VMTypeID = std::uint32_t;` (`RE/B/BSCoreTypes.h`) | used by `pv::BuildClassTable` (inside an SKSE task only) |
