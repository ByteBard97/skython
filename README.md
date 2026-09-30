# skyrim-python

A Skyrim SE/AE mod (SKSE C++ plugin) that hosts Python and gives Python scripts a native-feeling API into the game, in the way KiCad exposes scripting for PCB design.

Status: pre-research. Nothing is built.

## Scope

In scope: embedding or hosting Python, a generic Papyrus call dispatcher, typed Python stubs generated from Papyrus `.psc` headers, an event layer, game-thread safety, packaging for non-technical players.

Out of scope: skyrim-dm (quest and contract generation), text-to-speech, lip sync, language models. skyrim-dm may later depend on this mod; that is decided after the research in `docs/research/research-prompts.md` comes back.

## Constraints

Skyrim only (AE 1.6.1170 target, SE 1.5.97 optional); developed and played on Linux under Proton; C++ compiled on a Windows machine; long-term goal is a one-mod install for non-technical players.

## Related projects (siblings in `../`)

- `Chronicle`: has a working SKSE plugin (ChronicleBridge), an HTTP protocol, and a live-test harness over DevBench.
- `skyrim-re-toolkit`: runtime inspection plugin, verified class layouts, CommonLibSSE-NG parsing.
- `skyrim-dm`: the contract generator that may eventually use this mod.

## Docs

- `docs/research/research-prompts.md`: four research questions and shared background, ready to hand to research agents.
