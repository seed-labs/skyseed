# Getting started

Use the repository's `skyseed_dev` Conda environment or another Python 3.12
environment with the project and upstream SEED dependency installed.

```bash
scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

## Small teaching example

From the repository root:

```bash
python -m examples.complete_static_emulator.main
```

This constructs the static Emulator, creates one Simulator/PhysicalWorld and
public Planner, runs a short Plan-to-Compiler chain, compiles the static Docker
project, and writes detached physical inspection artifacts.

For the domain chain without output generation:

```bash
python -m examples.complete_static_emulator.simu
```

## Mini generated runtime

```bash
python -m examples.mini_satellite_internet.main
```

Generation drains the common incremental realization producer, writes
`output/domain-history/`, seals `output/runtime/runner-artifact/`, and creates
runtime scripts plus detached inspection. Generated execution consumes only the
verified finite artifact.

Follow the generated `output/start.sh`, `run.sh`, and `cleanup.sh` controls only
in an environment where Docker/network privileges are appropriate.

## Canonical S3 offline stages

The Starlink Gen1 example keeps the full 720-Satellite, 355-GroundStation,
732-Zone, ten-minute scenario:

```bash
python -m examples.starlink_gen1_satellite_internet.main \
  --through physical-world

python -m examples.starlink_gen1_satellite_internet.main \
  --through planner

python -m examples.starlink_gen1_satellite_internet.main \
  --through compiler
```

Planner mode exercises the public 50-second planning / 20-second publication
contract. Compiler mode drains the one producer and verifies 121 formal frames
and transitions, 30 Plans, 30 realizations, and 121 activation boundaries.

Do not treat offline Compiler success as full S3 runtime qualification. The
full persisted Runner/Traffic deployment is a later dedicated stage.

## Current chain

```text
PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ Compiler / BpfCompiledRealization
→ sealed Runner artifact
→ Runner / RealizedRuntimeState
```

See [inspection Tools](inspection-tools.md) for artifact paths and
[`../accepted-design.md`](../accepted-design.md) for ownership details.
