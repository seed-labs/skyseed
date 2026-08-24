# Skyseed v2 examples

The maintained repository examples have two deliberately different roles.

| Example | Long-term role | Main entry | Runtime output | Inspection surfaces |
| --- | --- | --- | --- | --- |
| `complete_static_emulator` | Minimal static/canonical example kept deliberately small: two Satellites plus one GroundStation, explicit public construction, Emulator-to-Simulator projection, a short Slice/Difference/Planner teaching chain, and static Compose smoke | `python3 -m examples.complete_static_emulator.main` | `output/runtime/`, `start.sh`, `cleanup.sh` | Topology, Zone, and the small `simu.py` teaching flow |
| `mini_satellite_internet` | Small but reasonably rich reusable satellite Internet reference: the complete rolling Simulator → Planner → BPF Compiler → BPF Runner path and post-run evidence | `python3 -m examples.mini_satellite_internet.main` | `output/runtime/`, `start.sh`, `run.sh`, `launch.sh`, `cleanup.sh` | Topology, Zone, Slice / Difference, Planner, BPF Compiler, BPF Runner, Traffic / Path Replay, InternetMap |

Both entries support direct execution from their own directories with
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

See each example README for exact generation, launch, inspection, and cleanup
commands.

## Historical reference material

`reference/` is not a formal runnable example. It contains offline historical
v1 provenance/adaptation material used to explain retained reference data. No
formal current example imports it at runtime.
