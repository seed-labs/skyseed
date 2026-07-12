# Skyseed v1

This directory is the canonical Satellite Internet 1.0 project after the
structural refactor. The validated behavior remains anchored at Git tag
`satellite-internet-v1.0-baseline`; code only becomes canonical here after its
producer, consumer, artifact, and failure contracts pass the migration gates.

Skyseed is intended to become a composable satellite Internet emulation
platform. Users should be able to describe a logical satellite network, select
replaceable models and planners, compile that description for a concrete
backend, and run or audit an experiment without modifying the platform core.

Reusable scenario data and Python experiment compositions live under `data/`
and `examples/`. Resolved profiles are generated run artifacts.
The migration workspace may still contain a sibling legacy tree for historical
regression comparison, but it is not imported, read, written, or required by
v1. A checkout containing only this project remains the supported shape.

For Python/SEED installation, Linux/Docker/BPF requirements, host migration,
preflight, real-run commands, output ownership, and current duration limits,
see `docs/operations/environment_and_running_EN.md`.

## Canonical entry

Install the project editable during development, then run or copy an example
directory. The Python file is the primary human-facing composition root:

```bash
/home/seed/miniconda3/envs/seed310/bin/pip install -e skyseed_v1
python skyseed_v1/examples/s4_operational_clean/run.py inspect
python skyseed_v1/examples/s4_operational_clean/run.py preview
python skyseed_v1/examples/s4_operational_clean/run.py
```

The final command starts the real experiment and writes its generated inputs,
effective profile, and evidence under that example's `output/`. It immediately
prints launch acceptance, the output path, experiment shape, and the initial
phase. While the first durable snapshot is still being prepared, a bounded
heartbeat confirms that work is continuing; phase changes and completed epoch
counts then come from that snapshot. Every later unchanged phase also receives
a bounded heartbeat with its elapsed time and available stage detail. Console
projection runs in a separate daemon thread and is never part of network
scheduling or update completion.
Detailed evidence remains in the run directory. The long parameter CLI is
retained for automation:

```bash
skyseed-v1 list-examples
skyseed-v1 run --example s4_operational_clean --dry-run --progress off
```

The complete-S5 example keeps the same ground/runtime policy with fewer
satellites and can be inspected or run in exactly the same way:

```bash
python skyseed_v1/examples/s5_operational_clean/run.py inspect
python skyseed_v1/examples/s5_operational_clean/run.py
```

`cli/` parses and forwards user choices only. Experiment orchestration belongs
to `runtime/`, while topology, planning, compilation, and backend behavior stay
in their respective modules.

## Cheap checks

From the repository root:

```bash
python skyseed_v1/tests/run.py
```

`unittest discover` remains an equivalent automation interface.

The legacy baseline suite remains:

```bash
/home/seed/miniconda3/envs/seed310/bin/python \
  -m unittest discover -s satellite -p 'test_*.py'
```

See `ARCHITECTURE_EN.md` for dependency rules and `MIGRATION_EN.md` for the staged
cutover plan.
