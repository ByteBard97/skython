# Contributing

Bug reports and pull requests are welcome. For a bug, use the issue template and attach `Skython.log` and `python.log`.

## Build

The plugin DLLs build on Windows: see [docs/BUILDING.md](docs/BUILDING.md). The core library, the Python package and
a Linux build of the `_skython` module (with a fake game backend) build and test on Linux without the game.

## Tests

On Linux (what CI runs, see `.github/workflows/ci.yml`):

```
cmake -S core -B build/core && cmake --build build/core -j && ctest --test-dir build/core --output-on-failure
cd python && uv run --python 3.12 pytest && uv run --python 3.14 pytest
```

The `_skython` module tests: build `pyglue/linux` against uv's CPython 3.14 (see "Build and test" in `README.md`),
then run `pyglue/tests` with `SKYTHON_PYGLUE_BUILD` pointing at the build folder.

Changes to the plugin should also be tried in the game. Say in the pull request what you ran and on which game
version.

## Protocol

`protocol/protocol.json` is the source of the protocol constants. After changing it, run `tools/gen_protocol.py` and
commit the generated files; `tools/gen_protocol.py --check` fails if they are out of date.

## Style

- Plain, short comments and docs. No em dashes (`tools/check_no_em_dash.sh`).
- No references to internal notes, machine paths or private hosts in code, tests or tools
  (`tools/check_no_plan_refs.sh`).
- Both scripts run in CI and must pass.
