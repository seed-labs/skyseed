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

Full-generation Starlink Gen1 input with the selected 348-Satellite S4 shell, 355 frozen
evidence-backed Starlink gateway sites, 703 total dynamic nodes, and the
732-Zone global layout:

```bash
python examples/starlink_gen1_satellite_internet/main.py
```

Use `--through topology`, `--through slicer`, or `--through planner` to select
an earlier boundary on the same rolling progression. `--slice-limit N` is only
a debug/execution bound; it does not change the formal 10-minute, 5-second,
121-Slice Timeline. Compiler mode lowers every Plan as it is published and
writes a persisted exact-realization handoff plus generated Compose/Runner
controls; it does not start Runner or containers. Begin runtime qualification
with `output/start.sh`, then for example
`SKYSEED_RUNNER_ACTIVATION_LIMIT=3 output/run.sh`; `output/launch.sh` is only
the convenience composition of those two commands.

`complete_static_emulator` is the maintained minimal two-Satellite teaching
and smoke reference. `mini_satellite_internet` is the maintained comprehensive
rolling reference. `starlink_gen1_satellite_internet` generates through Compiler by
default; earlier `--through` boundaries remain explicit review/debug modes.
Its frozen ground body
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
