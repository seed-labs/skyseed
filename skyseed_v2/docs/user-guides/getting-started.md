# Getting started

## Environment

The canonical local environment is Conda `skyseed_dev`:

```bash
scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

The Python package itself also works in another environment with equivalent
dependencies. Examples never create or switch environments.

## Readable examples

Static Emulator, Simulator projection, compiler, Compose launcher, and static
Tools:

```bash
python examples/complete_static_emulator/main.py
```

Mini satellite Internet reference with Topology, Zone, Slice / Difference, and
Planner Tools:

```bash
python examples/mini_satellite_internet/main.py
```

`complete_static_emulator` is the maintained minimal two-Satellite teaching
and smoke reference. `mini_satellite_internet` is the maintained comprehensive
rolling reference. `examples/reference/` contains historical
provenance/investigation helpers and is not a formal runnable example or public
API.

Generated example output is ignored under `examples/*/output/`.

## Architecture entry

Read `../CURRENT.md`, `../accepted-design.md`, then the single relevant
contract under `../codex-reference/`. Package navigation is graphical in
`../human-readable/`.
