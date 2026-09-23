# Forwarding backends

One early `ForwardingBackend` choice selects BPF (default) or Linux. The shared
[compiler](compiler/README.md), [materials](execution_materials/README.md) and
[Runner](runner/README.md) preserve one calculation/execution lifecycle.
[BPF DataPlane](bpf/dataplane/README.md) owns C/ELF, tables and generation publication;
[Linux](linux/README.md) owns native IPv4 execution and shared kernel primitives.
`compile_plan` and `Runner` are available from this package; existing BPF public
imports remain compatibility names for the shared owners.

See the [authoritative execution contract](../../../docs/development/compiler-and-runner.md).
