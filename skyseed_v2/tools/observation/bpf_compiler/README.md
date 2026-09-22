# Compiler Tool

Inspect Plan/material lineage, activation timestamps and translated resource,
parameter and forwarding work. Select a boundary or delivery seam to compare the
workload with intended-state counts. Trend lines and operation-family counts describe
compiled work; they do not prove it executed successfully. The reader uses compact
Compiler history and material metadata without creating a Runner.

Backend identity comes from the execution definition or supplied CompilationResult.
Standalone historical histories without that identity show backend not recorded.
The `bpf_compiler` imports and output paths remain compatible for both backends.
Empty/partial arrangements do not establish kernel unreachability, and this view
does not display routing-daemon tables or observed packet paths.

[Open the interactive Compiler demo](../../../docs/demos/s5/bpf-compiler/index.html).
Its [provenance](../../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../../docs/user-guides/outputs-and-tools.md).
