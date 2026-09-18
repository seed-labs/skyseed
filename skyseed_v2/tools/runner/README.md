# Runner Tool

Inspect observed preparation, paced activations, maintenance and cleanup for one
runtime invocation. Select timeline events and activation details to see recorded
work and lag. Preparation/maintenance may overlap; their durations must not be added
to paced elapsed time. Startup boundary zero belongs to preparation, with later
boundaries in the paced execution view. Missing evidence stays visibly unavailable.

Runner facts come from `runs/<invocation>/runner/`; optional Deployer startup timing
comes from `deployment/`. Reset creates a separate invocation. No page infers actual
execution from Compiler intent.

[Open the interactive Runner demo](../../docs/demos/s5/runner/index.html).
Its [provenance](../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../docs/user-guides/outputs-and-tools.md).
