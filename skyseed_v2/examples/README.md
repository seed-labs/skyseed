# Skyseed v2 examples

The maintained repository examples have three deliberately different roles.

| Example | Long-term role | Main entry | Runtime output | Inspection surfaces |
| --- | --- | --- | --- | --- |
| `complete_static_emulator` | Minimal static/canonical example kept deliberately small: two Satellites plus one GroundStation, explicit public construction, Emulator-to-Simulator projection, a short Slice/Difference/Planner teaching chain, and static Compose smoke | `python3 -m examples.complete_static_emulator.main` | `output/runtime/`, `start.sh`, `cleanup.sh` | Topology, Zone, and the small `simu.py` teaching flow |
| `mini_satellite_internet` | Small but reasonably rich reusable satellite Internet reference: the complete rolling Simulator → Planner → BPF Compiler → BPF Runner path and post-run evidence | `python3 -m examples.mini_satellite_internet.main` | `output/runtime/`, `start.sh`, `run.sh`, `launch.sh`, `cleanup.sh` | Topology, Zone, Slice / Difference, Planner, BPF Compiler, BPF Runner, Traffic / Path Replay, InternetMap |
| `s4_satellite_internet` | Large compute-only S4 reference with one 181-Slice formal Timeline and stage-selected rolling Slicer → Planner → Compiler execution | `python3 -m examples.s4_satellite_internet.main` (`--through` selects the boundary) | none; compute-only evidence | Topology and Zone by default; Slice, Observation, Planner, and Compiler views at their executed boundaries |

All three entries support direct execution from their own directories with
`python3 main.py`. They use the caller-selected Python interpreter; the
recommended local interpreter is the `skyseed_dev` Conda environment described
in the repository README.

## Minimal teaching and smoke reference

`complete_static_emulator/emu.py` owns the small handwritten Emulator.
`main.py` compiles that same object and writes the static Topology and Zone
inspection artifacts. `simu.py` separately exposes the public Simulator,
Slicer, Difference, and Planner construction steps in readable code. It is the
single maintained two-Satellite teaching/smoke example; it is intentionally not
a rolling BPF runtime scenario.

## Canonical dynamic reference

`mini_satellite_internet/` is the comprehensive bounded canonical example.
`main.py` only generates output. Its generated `run.sh` enters the private root
`skyseed_v2._generated_bpf_execution` composition and does not launch the
example-local canonical traffic workload. The traffic workload remains
acceptance/reference orchestration used separately from normal execution.

## S4 stage-bounded rolling reference

`s4_satellite_internet/` freezes the accepted first-step S4 constellation, a
355-site evidence-backed Ground Station body, and global Zone candidate into
v2-local input files. The ground body includes supported current, planned, and
historical Ka/E sites and is not a simultaneous operational-network snapshot.
Its default `main.py` invocation builds the one full Simulator composition but
leaves Slicer at S0 while writing Topology and Zone inspection artifacts.
`--through slicer|planner|compiler` advances that same 30-minute, 10-second
Timeline to the requested early-stop boundary. Planner and Compiler execution
is rolling: enough future Slice facts are generated for the 50-second planning
lookahead, a 20-second Plan prefix is published, and public `compile_bpf(...)`
is called immediately when requested. `--slice-limit` is only an execution
bound, so a short debug run and full S4 share one composition and algorithm.
The final S4-specific Zone-service minimum is 25 degrees; the isolated shell is
not expected to serve every global Zone continuously. This path does not enter
Runner or create a Compose deployment.

See each example README for exact generation and inspection commands; the two
deployment-capable examples also document launch and cleanup.

## Historical reference material

`reference/` is not a formal runnable example. It contains offline historical
v1 provenance/adaptation material used to explain retained reference data. No
formal current example imports it at runtime.

The repository-level `research/` tree is a separate persistent archive of
external evidence. Formal examples do not load it at runtime.
