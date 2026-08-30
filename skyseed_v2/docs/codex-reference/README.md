# Codex reference

Read only the files touched by the task. These documents are normative detail
under `../accepted-design.md`. Implementation state is recorded only in
[`CURRENT.md`](../CURRENT.md).

Each contract document uses the same searchable headings:

```text
Authority
Scope
Owners and consumers
Contract
Invariants
Failure semantics
Prohibited behavior
Extension points
Maintenance
```

Read the physical foundation and the migration seam as different things. The
accepted path begins `Topology / PhysicalFrame → Network Planner`; the current
`PhysicalFrame → legacy Slicer → current Planner` path is an explicit temporary
implementation seam. Topology owns `PositionModel`, `frame_at()`, and
`PhysicalFrame`; slicing retains only temporary legacy
Connection/Route/Slice/Difference responsibilities until their scheduled
removal.

Index:

- [public imports](public-imports.md)
- [ownership and lifecycle](ownership-lifecycle.md)
- [Emulator–Simulator boundary](emulator-simulator-boundary.md)
- [Emulator construction](emulator-construction.md)
- [Compose launcher](compose-launcher.md)
- [Inspection Tools, including Physical Frame and the temporary legacy Slice consumer](inspection-tools.md)
- [Topology, PhysicalFrame, and Zone contracts](topology-zone-contracts.md)
- [Temporary legacy operational slicing contracts](dynamic-slicing-contracts.md)
- [Position and Connection values](position-connection-values.md)
- [Model contracts and built-in algorithms](model-contracts.md)
- [routing and path contracts](routing-path-contracts.md)
- [Timeline and temporary legacy Slice retention/Difference](timeline-slice-difference-contracts.md)
- [Simulator Planner contracts](planning-contracts.md)
- [invariants and failures](invariants-failures.md)
- [extension points](extension-points.md)
- [maintenance map](maintenance-map.md)
