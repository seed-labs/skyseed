# Invariants and failure semantics

Each owner validates at its input boundary. Contract errors fail closed; no
consumer invents fallback state.

| Boundary | Required invariant | Typical failure |
|---|---|---|
| configuration/YAML | immutable known schema and validated values | typed load error |
| Emulator entity construction | owner-local uniqueness; no partial batch publication | `KeyError` / `ValueError` |
| Emulator-to-Simulator projection | complete immutable scalar copy; unique NodeId; no upstream object reference | `TypeError` / `ValueError` |
| Simulator lifecycle | one PhysicalWorld, then one Planner with distinct injected roles | `RuntimeError` / `ValueError` |
| PhysicalWorld projection | aware timestamp; exact complete immutable positions from owned PositionModel | `TypeError` / `ValueError` |
| physical-link observation | exact world/frame/request; sparse feasible candidates; deterministic work counts | `TypeError` / `ValueError` |
| Planner input | strictly ordered matching formal frame/observation pairs | `ValueError` |
| Planner Models | requested authoritative candidates, capability compliance, complete valid operational outputs | `TypeError` / `ValueError` |
| Planner publication | replay-complete transition, continuous frontiers/lineage, exact fingerprint | `ValueError` / `RuntimeError` |
| Compiler | exact world, predecessor, input state, replay, and backend feasibility | `TypeError` / `ValueError` |
| persisted handoff | version/schema/checksum/sequence/reference integrity | `ValueError` / `RuntimeError` |
| BpfRunner | exact-next gap-free activation and owner-local resource cleanup | `TypeError` / `ValueError` / `RuntimeError` |
| Tools | current-invocation detached records or conspicuous failure | explicit read/write failure |

## Global invariants

- `NodeId` is a pure structural value and never performs lookup.
- Public datetime inputs are UTC-aware and normalized.
- Immutable values defensively snapshot iterables and mappings.
- Published collections have canonical deterministic order.
- PhysicalWorld is the only PositionModel owner/caller.
- PhysicalFrame contains positions only and owns no progression.
- Candidate feasibility, operational selection, compiled intent, and realized
  state have separate owners.
- Planner creates one transition for every formal timestamp, including no-op.
- Planned state changes only through the common transition apply function.
- Coordination adds precedence only over transition-derived event references.
- Every active Route demand has a reachable outcome or explicit unreachable
  reason.
- Plan and realization world/state lineage is continuous.
- Compiler does not rerun physical or planning algorithms.
- Every transition remains an activation boundary for Runner.
- Heavy semantic work never runs synchronously in Runner maintenance.
- A Tool cannot invalidate valid compute evidence or a sealed Runner artifact.

## Atomicity and cleanup

Physical projection failure publishes no frame. Planner snapshots owner state
around each advance and restores lifecycle, parameter memory, Route state,
frontiers, and publication buffers when an injected role or validation fails.
The contract cannot roll back private mutation inside an arbitrary third-party
Model.

Producer failure closes Planner and its Route workers before propagating the
exception. Compiler failure publishes no realization and cannot mutate the
previous realization. Persisted-reader failure supplies nothing to Runner.

External subprocess failure includes bounded diagnostic context but does not
trigger a second execution path.
