# testesp/builder: licence note

This folder is a development tool, not part of Skython.

- It links Mutagen (`Mutagen.Bethesda.Skyrim`, `Mutagen.Bethesda.Core`, `Mutagen.Bethesda.Kernel` 23.4.0; licence GPL-3.0-only; https://github.com/Noggog/Mutagen). A binary built from this folder is therefore a GPL-3.0-only work.
- It is never distributed: `bin/` and `obj/` are gitignored and no binary built from it is committed.
- No product code (`core/`, `plugin/`, `pyglue/`, `python/`) references this folder or Mutagen.
- Its output, `SkythonTest.esp`, is test data written from the record values in these sources; it is committed with its SHA-256 in `docs/PINS.md`.
- No code in this folder was copied from Mutagen or from any other project.
