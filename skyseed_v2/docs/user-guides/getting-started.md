# Getting started

Use the repository's `skyseed_dev` Conda environment or another Python 3.12
environment with the project and upstream SEED dependency installed.

```bash
scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

For the mechanisms behind the maintained execution choices, read
[已采用优化与宿主机配置](../human-readable/optimization-decisions.md).
The [host decision record](../provenance/optimization-decision-history.md#h1)
separates this host's control-plane limits and storage layout from project
configuration; its paths and rollback discussion are a historical record,
not setup commands to apply to another machine.

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

After `main.py` completes all output, realizations, the artifact and controls,
generated `output/start.sh` deploys and `output/run.sh` starts Runner/Traffic.
`output/launch.sh` combines start and run; `output/cleanup.sh` independently
tears down the owned deployment. Use these controls in an environment with the
required Docker/network privileges. See the
[common example operating sequence](../../examples/README.md).

Runner Preparation decodes and prepares the full finite artifact and physically
activates B0. Formal run begins at B1. Generation publication occurs within Main;
the boundary completes only after withdrawals and all physical deletion jobs join.

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

Offline Compiler success and full S3 runtime qualification are separate results.
The deployment uses the same generated controls described above. Accepted finite
Runner/S3 evidence and documentation-review status are tracked in
[CURRENT](../CURRENT.md).

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
