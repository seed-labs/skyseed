# Shared Plan compiler

`compile_plan(plan, physical_world, previous_result, forwarding_backend="bpf")`
validates and lowers one Plan without running a Model or acquiring resources.
`CompilationResult` carries backend identity, lineage, ordered intentions and
incremental indexes. `compile_bpf` and the earlier BPF result names remain aliases
for default callers. Native material preparation rejects unsupported route expressions offline; shared
Plan lowering and logical-reference checks remain common to both backends.

The [material writer](../execution_materials/README.md) invokes only the selected
BPF or Linux translator. See [execution contracts](../../../../docs/development/compiler-and-runner.md).
