# Skyseed v2 examples

The maintained repository examples have three deliberately different roles.

| Example | Long-term role | Main entry | Runtime output | Inspection surfaces |
| --- | --- | --- | --- | --- |
| `complete_static_emulator` | Minimal static/canonical example kept deliberately small: two Satellites plus one GroundStation, explicit public construction, Emulator-to-Simulator projection, a short Slice/Difference/Planner teaching chain, and static Compose smoke | `python3 -m examples.complete_static_emulator.main` | `output/runtime/`, `start.sh`, `cleanup.sh` | Topology, Zone, and the small `simu.py` teaching flow |
| `mini_satellite_internet` | Small but reasonably rich reusable satellite Internet reference: the complete rolling Simulator → Planner → BPF Compiler → BPF Runner path and post-run evidence | `python3 -m examples.mini_satellite_internet.main` | `output/runtime/`, `start.sh`, `run.sh`, `launch.sh`, `cleanup.sh` | Topology, Zone, Slice / Difference, Planner, BPF Compiler, BPF Runner, Traffic / Path Replay, InternetMap |
| `s4_satellite_internet` | S4 static-by-default review plus opt-in bounded Simulator qualification: 348 Satellites, 355 frozen evidence-backed Starlink gateway sites, and 732 global Zones | `python3 -m examples.s4_satellite_internet.main` (`--dynamic` opts in) | none; compute-only evidence | Canonical Topology and Zone views, plus the maintained lazy Slice / Difference view after dynamic qualification |

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

## S4 static input and dynamic Simulator qualification

`s4_satellite_internet/` freezes the accepted first-step S4 constellation, a
355-site evidence-backed Ground Station body, and global Zone candidate into
v2-local input files. The ground body includes supported current, planned, and
historical Ka/E sites and is not a simultaneous operational-network snapshot.
Its default `main.py` invocation intentionally stops after materializing one
static Topology and writing the existing Topology and Zone inspection
artifacts. `--dynamic` instead opts into the bounded 30-minute, 10-second
Position → Connection → Route → Slice → Difference qualification and writes
example-local evidence plus a lazy per-Slice Slice / Difference artifact under
the canonical example `output/inspection/` directory. It
uses the final S4-specific 25-degree Zone-service minimum; the isolated shell
is not expected to serve every global Zone continuously. It does not create
Plans, BPF output, or a Compose deployment.

See each example README for exact generation and inspection commands; the two
deployment-capable examples also document launch and cleanup.

## Historical reference material

`reference/` is not a formal runnable example. It contains offline historical
v1 provenance/adaptation material used to explain retained reference data. No
formal current example imports it at runtime.

The repository-level `research/` tree is a separate persistent archive of
external evidence. Formal examples do not load it at runtime.
