# Tools: browse completed facts

`write_experiment_tools(output, physical_world=world, publication=None)` renders
completed generation facts and available runtime records. During `serve()`, the
Controller supplies a completed publication to the optional presentation callback.
It runs separately from calculation and runtime progress; failure preserves domain
results and is reported under `results/experiment/presentation-status.json`.

Open `output/visualizations/index.html`, or the fixed [interactive demos](../docs/demos/index.html).

| Tool | What to inspect |
|---|---|
| [PhysicalWorld](physical_world/README.md) | Time, physical entities and candidate feasibility |
| [Zone](zone/README.md) | Static geography and boundaries |
| [Planner](planner/README.md) | Selected state, acquisition, paths, changes and optional Areas |
| [Compiler](bpf_compiler/README.md) | Activation work and material lineage |
| [Runner](runner/README.md) | Actual initialization, preparation, activation and cleanup |
| [Traffic](traffic/README.md) | Planned paths alongside recorded probes and telemetry |
| [Performance](performance/README.md) | Measured spans, work and resources by owner |

Tools never rerun Models or infer execution success from planned state. Readers pin
completed byte prefixes; browser assets live under `visualizations/`, raw runtime
records under `runs/<invocation>/`, and Tool measurements under `results/tools/`.
[Output guide](../docs/user-guides/outputs-and-tools.md) explains these distinctions.
[_presentation](_presentation/README.md) contains neutral display helpers.
The optional `traffic.active` code is an explicit runtime consumer, separate from
Traffic's detached reader and browser presentation.
