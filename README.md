# Skython

Write Skyrim mods in real Python. Skython is a Skyrim SE/AE mod (an SKSE C++ plugin) that embeds CPython and gives Python scripts a native-feeling API into the game, in the way KiCad exposes scripting for PCB design. Scripts run inside the game and `import skython`.

Status: in development, not released. The Linux-side code (core, dispatcher, Python package, dev bridge) is built and tested; the spikes that test the riskiest premises are running on a Windows test machine; the plugin itself (the game-side DLLs) is next. Code, file names and the repo folder still say `skyrim-python` / `SkyrimPython`; they are renamed to Skython before the plugin is built.

## Scope

In scope: embedding or hosting Python, a generic Papyrus call dispatcher, typed Python stubs generated from Papyrus `.psc` headers, an event layer, game-thread safety, packaging for non-technical players.

Out of scope: skyrim-dm (quest and contract generation), text-to-speech, lip sync, language models. skyrim-dm will drive the game through Skython's dev bridge.

## Constraints

Skyrim only (AE 1.6.1170 target, SE 1.5.97 optional); developed and played on Linux under Proton; C++ compiled on a Windows machine; long-term goal is a one-mod install for non-technical players.

## Related projects (siblings in `../`)

- `Chronicle`: has a working SKSE plugin (ChronicleBridge), an HTTP protocol, and a live-test harness over DevBench.
- `skyrim-re-toolkit`: runtime inspection plugin, verified class layouts, CommonLibSSE-NG parsing.
- `skyrim-dm`: the contract generator that drives the game through Skython.

## Docs

- `docs/research/research-prompts.md`: four research questions and shared background, ready to hand to research agents.
