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

Static-by-default S4 scale input with 348 Satellites, 355 frozen
evidence-backed Starlink gateway sites, 703 total dynamic nodes, and the
732-Zone global layout:

```bash
python examples/s4_satellite_internet/main.py
```

Use `--through slicer`, `--through planner`, or `--through compiler` to select
a later boundary on the same rolling S4 progression. `--slice-limit N` is only
a debug/execution bound; it does not change the formal 30-minute, 10-second,
181-Slice Timeline. Compiler mode lowers every Plan as it is published and
stops before Runner or containers.

`complete_static_emulator` is the maintained minimal two-Satellite teaching
and smoke reference. `mini_satellite_internet` is the maintained comprehensive
rolling reference. `s4_satellite_internet` remains cheap and static by default;
later `--through` boundaries add detached Slice, Observation, Planner, and
Compiler artifacts under its example output directory. Its frozen ground body
includes supported current, planned, and historical Ka/E gateway sites rather
than a simultaneous operational-network snapshot.
`examples/reference/` contains historical
provenance/investigation helpers and is not a formal runnable example or public
API.

Generated example output is ignored under `examples/*/output/`.

## Architecture entry

Read `../CURRENT.md`, `../accepted-design.md`, then the single relevant
contract under `../codex-reference/`. Package navigation is graphical in
`../human-readable/`.
