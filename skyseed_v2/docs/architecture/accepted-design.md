# Accepted Architecture Anchor

This document is the concise authority anchor for accepted Skyseed v2
architecture. It records status, ownership, dependency direction and
Architecture Deltas without duplicating detailed public contracts.

The complete accepted Dynamic Slice contract is maintained only in
[`dynamic-slice-public-contract.md`](dynamic-slice-public-contract.md).
[`../CURRENT.md`](../CURRENT.md) records current implementation status.

## Authority

When sources disagree, use this order:

1. explicit user decisions;
2. this anchor and the linked detailed accepted contract;
3. accepted class diagrams and frozen public contracts;
4. accepted code and tests for current implementation facts;
5. repository investigations;
6. design candidates;
7. v1 and other historical references.

Candidate and historical records retain provenance but cannot override this
anchor or the linked detailed contract without a later explicit Architecture
Delta.

## Current status

The implemented accepted foundation comprises:

- `Simulator`, `SimulationDefinition` and the current
  `SimulationTimeline`;
- immutable static `Topology` with Simulator-owned AS, Shell, Orbit,
  Satellite and GroundStation objects;
- optional Topology-owned `ZoneLayout`, its static Zones and fixed
  `ZoneMembershipModel`;
- the existing Emulator-to-Simulator static-input construction boundary.

The Dynamic Slice contract for Position, Connection, Route, Slicer,
TopologySlice and TopologyDifference is **accepted/frozen design but not
implemented**. No dynamic type or package-root export is implied by acceptance.
Planning, Plan, DifferenceWindow, backend execution and runtime resource
policy remain deferred.

## Core ownership and dependency direction

`Simulator` remains the composition root:

```text
Simulator
├── exact SimulationDefinition
├── at most one explicitly created Topology
└── after Topology, at most one explicitly created Slicer
    ├── exact Topology
    ├── exact SimulationTimeline
    ├── exact PositionModel
    ├── exact ConnectionModel
    ├── exact RouteModel
    └── ordered published TopologySlices
```

Topology owns the immutable static world and static query facade. It owns no
Timeline, Slicer, Position, Connection, Route, TopologySlice,
TopologyDifference, Planning or backend state.

Slicer owns the sole dynamic generation order, Model coordination, validation,
atomic TopologySlice publication, retained-Slice queries and adjacent
TopologyDifference entry. Model instances are explicit, fixed to one Slicer
running context and provide no hidden default or fallback.

The sole accepted dynamic state direction is:

```text
Static Topology + Timeline timestamp
→ PositionModel
→ ConnectionModel
→ RouteModel
→ TopologySlice
→ adjacent TopologyDifference
→ future Planning boundary
```

The linked detailed contract is the sole complete authority for Model
signatures, value fields and validation, built-in algorithms, Slice lifecycle,
Difference patches, failure semantics, Tool reads and deferred boundaries.

## Architecture Delta from the implemented baseline

The accepted, not-yet-implemented delta is:

1. Timeline becomes timezone-aware-only and UTC-normalized.
2. Orbit static definition gains UTC `epoch_utc`; Satellite mean anomaly is
   interpreted at its parent Orbit epoch.
3. GroundStation height becomes finite WGS84 ellipsoidal metres.
4. WGS84 ECEF metres become the sole authoritative Position representation,
   with geographic coordinates as read-only derived views.
5. Simulator explicitly creates and owns Slicer after Topology.
6. ZoneLayout gains the accepted SphericalCap range query.
7. Position, Connection, Route, TopologySlice, Slicer and
   TopologyDifference become accepted public design.
8. Tools may read formal dynamic results but do not become a second
   authoritative computation path.
9. Built-in same-Orbit ordering uses mean anomaly; built-in cross-Orbit
   structure uses adjacent-RAAN stable cyclic pairing.

## Prohibited directions

- Topology does not create or compare TopologySlices.
- TopologySlice does not create or own TopologyDifference.
- ConnectionModel, Planner and Tools do not independently propagate Position.
- Tools do not recompute authoritative Connection or Route results and are
  never Model fallbacks.
- Dynamic Zone membership or service state is not stored on static entities or
  ZoneLayout.
- Model instances do not own authoritative Slice history or silently switch
  algorithms.
- Same-Orbit ordering is not `orbit_index`; cross-Orbit pairing is not
  same-index pairing.
- Candidate and geometry evidence are not ConnectionSlice state.
- v1 owners, types, constants and algorithms do not automatically become v2
  architecture.

## Approved dependency order and authorization boundary

The labels A through G describe the approved implementation dependency order.
They are grouped into three engineering authorization units:

1. **A–E: complete Dynamic Slice main chain** — UTC/epoch/Position, Zone
   range, Connection, Route, Slicer and TopologySlice.
2. **F: TopologyDifference**.
3. **G: Tool, canonical example and final synchronization**.

Each engineering group requires explicit authorization, implementation,
validation, architecture audit and acceptance. A, B, C, D and E do not require
mechanical authorization pauses between their internal dependency steps.

This architecture-contract synchronization does not authorize implementation
of A–E, F or G.
