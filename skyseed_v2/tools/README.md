# Generation, observation and development tools

`write_experiment_tools(output, physical_world=world, publication=None)` renders
completed generation facts and available runtime records. During `serve()`, the
Controller supplies a completed publication to the optional presentation callback.
It runs separately from calculation and runtime progress; failure preserves domain
results and is reported under `results/experiment/presentation-status.json`.

Open `output/visualizations/index.html`, or the fixed [interactive demos](../docs/demos/index.html).

| Tool | What to inspect |
|---|---|
| [PhysicalWorld](observation/physical_world/README.md) | Time, physical entities and candidate feasibility |
| [Zone](observation/zone/README.md) | Static geography and boundaries |
| [Planner](observation/planner/README.md) | Selected state, acquisition, paths, changes and optional Areas |
| [Compiler](observation/bpf_compiler/README.md) | Activation work and material lineage |
| [Runner](observation/runner/README.md) | Actual initialization, preparation, activation and cleanup |
| [Traffic generation](generation/traffic/README.md) | GS/Zone schedules and preview before deployment |
| [Traffic observation](observation/traffic/README.md) | Recorded sending, available packet/path evidence and replay |
| [Performance](observation/performance/README.md) | Measured spans, work and resources by owner |

Tools never rerun Models or infer execution success from planned state. Readers pin
completed byte prefixes; browser assets live under `visualizations/`, raw runtime
records under `runs/<invocation>/`, and Tool measurements under `results/tools/`.
[Output guide](../docs/user-guides/outputs-and-tools.md) explains these distinctions.
[_presentation](observation/_presentation/README.md) contains neutral display helpers.
[Generation](generation/traffic/README.md) owns the explicitly enabled workload sender,
assembled only by experiment orchestration.

[Observation](observation/) owns read-only readers, exports and entrypoints. Imports/commands use `tools.observation.*`;
ordinary Examples keep `import tools; tools.write_experiment_tools(...)`.
[Development](development/README.md) contains independent reusable checks and the
copyable Python driver. Opening a page does not start either calculation or active
measurement. Local route dispositions are shown with unknown end-to-end reachability.
