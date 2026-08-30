# Skyseed v2 examples

The maintained repository examples have three deliberately different roles.

| Example | Long-term role | Main entry | Runtime output | Inspection surfaces |
| --- | --- | --- | --- | --- |
| `complete_static_emulator` | Minimal static/canonical example kept deliberately small: two Satellites plus one GroundStation, explicit public construction, Emulator-to-Simulator projection, Topology-owned physical projection, a short temporary legacy Slice/Difference/Planner teaching chain, and static Compose smoke | `python3 -m examples.complete_static_emulator.main` | `output/runtime/`, `start.sh`, `cleanup.sh` | Topology, Zone, Physical Frame, and the small `simu.py` teaching flow |
| `mini_satellite_internet` | Small but reasonably rich reusable satellite Internet reference: the complete rolling Topology/PhysicalFrame → temporary legacy Slicer → current Planner → BPF Compiler → BPF Runner path and post-run evidence | `python3 -m examples.mini_satellite_internet.main` | `output/runtime/`, `start.sh`, `run.sh`, `launch.sh`, `cleanup.sh` | Topology, Zone, Slice / Difference, Planner, System Observation, BPF Compiler, BPF Runner, Traffic / Path Replay, InternetMap; no Physical Frame artifact |
| `starlink_gen1_satellite_internet` | Canonical S3 Starlink Gen1 shell reference with 121 timestamped physical states feeding the temporary legacy Slicer → current Planner → Compiler generation; compiler mode persists the exact realization handoff for runtime execution | `python3 -m examples.starlink_gen1_satellite_internet.main` (`--through` selects an early boundary) | fixed `output/`: `runtime/`, `inspection/`, `start.sh`, `run.sh`, `launch.sh`, `cleanup.sh` | Topology, Zone, Physical Frame, Slice / Difference, Observation, Planner, and Compiler by default; post-run Runner and Traffic views |

All three entries support direct execution from their own directories with
`python3 main.py`. They use the caller-selected Python interpreter; the
recommended local interpreter is the `skyseed_dev` Conda environment described
in the repository README.

## Minimal teaching and smoke reference

`complete_static_emulator/emu.py` owns the small handwritten Emulator.
`main.py` compiles that same object and writes the static Topology, Zone, and
timestamped Physical Frame inspection artifacts. `simu.py` separately exposes
the public Simulator construction in readable code: Topology invokes its bound
PositionModel to produce a PhysicalFrame, after which the explicit temporary
legacy Slicer computes Connection/Route/TopologySlice/Difference values for
the existing Planner. It is the single maintained two-Satellite teaching/smoke
example; it is intentionally not a rolling BPF runtime scenario.

## Canonical dynamic reference

`mini_satellite_internet/` is the comprehensive bounded canonical example.
`main.py` only generates output. Its generated `run.sh` enters the private root
`skyseed_v2.experiment.execution` composition and does not launch the
example-local canonical traffic workload. The traffic workload remains
acceptance/reference orchestration used separately from normal execution.

## Starlink Gen1 stage-bounded rolling reference

`starlink_gen1_satellite_internet/` freezes the formal Starlink Gen1 shells, a
canonical S3 selection—36 Orbits with 20 Satellites each at 570 km and 70°—a
355-site evidence-backed Ground Station body, and a global Zone candidate into
v2-local input files. The ground body includes supported current, planned, and
historical Ka/E sites and is not a simultaneous operational-network snapshot.
Its default `main.py` invocation advances the same 10-minute, 5-second,
121-state Timeline through Compiler and writes one fixed canonical `output/`
tree. `--through topology|slicer|planner` selects an explicit earlier
generation boundary; `--through slicer` is the visible temporary legacy stage,
not a lasting architecture. Execution follows Topology/PhysicalFrame → legacy
Slicer Connection/Route/TopologySlice/Difference → current Planner. Planner and
Compiler execution is rolling: enough future legacy Slice facts are generated
for the 50-second planning lookahead, a 20-second Plan prefix is published, and
public `compile_bpf(...)` is called immediately when requested. `--slice-limit`
is only an execution bound, so a short debug run and the full Timeline share
one composition and algorithm. The selected-shell Zone-service minimum is 25
degrees; this isolated 720-Satellite shell is not expected to serve every
global Zone continuously. This path does not enter Runner or create a Compose
deployment.

See each example README for exact generation and inspection commands; the two
deployment-capable examples also document launch and cleanup.

## Historical reference material

`reference/` is not a formal runnable example. It contains offline historical
v1 provenance/adaptation material used to explain retained reference data. No
formal current example imports it at runtime.

The repository-level `research/` tree is a separate persistent archive of
external evidence. Formal examples do not load it at runtime.
