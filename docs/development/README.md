# Developer and reviewer references

Start with [architecture](../architecture/overview.md) to understand the system, then
use these references for implementation and review:

- [Workflow](workflow.md): environment, focused checks, debugging and diagram maintenance.
- [Component boundaries](component-boundaries.md): owners, dependency direction and static input.
- [Model interfaces](model-interfaces.md): required results, templates, optional capabilities and defaults.
- [Planner state and Plans](planner-state-and-plans.md): time, adoption, replay and routing semantics.
- [C/eBPF DataPlane](dataplane.md): replaceable packet handlers, ELF preparation, shared support and map ownership.
- [Compiler and Runner](compiler-and-runner.md): sealed materials, activation and resource lifetime.
- [Experiment control](experiment-control.md): Etcd interaction, receipts/results, publication, revision selection, continuation and cancellation ownership. The [local source map](../../src/skyseed/experiment/README.md#interaction-and-revision-owners) locates implementations.

These boundaries are review criteria. A change to them needs an explicit reason and a
clear account of its effects on callers, owners, persisted values and execution. They
can evolve when justified; this is not a new approval process for ordinary implementation
details. Source signatures define callable spelling. Default algorithms and optional
mechanisms are distinguished from interface obligations; stated capability limits describe
the current implementation. Dated history remains evidence under its original conditions.
