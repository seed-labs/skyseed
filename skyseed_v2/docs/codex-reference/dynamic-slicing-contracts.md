# Temporary dynamic Slicing contract index

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Index of the Round-1 physical projection followed by the still-unmigrated
Connection → Route → Slice/Difference operational chain. Exact values, Models,
routes, Timeline/publication, path resolution, and Difference rules live in
the focused references linked below.

## Owners and consumers

Simulator creates one Topology with one exact PositionModel. Topology is that
Model's sole owner/caller and creates physical-only PhysicalFrames. The
temporary legacy Slicer owns distinct ConnectionModel and RouteModel
instances, consumes each PhysicalFrame, and still owns their call order,
TopologySlice publication, Difference creation, and retained-slice queries.
Callers and legacy read-only Tools consume exact retained `TopologySlice`,
`ConnectionSlice`, and `RouteSlice` values. The sibling Planner asks Slicer to
create adjacent `TopologyDifference` values on request and consumes them;
Slicer does not retain those values. Read-only Tools consume the same formal
facts without creating a parallel calculation.

This Slicer remainder is an explicit migration seam scheduled for deletion
after Connection/Parameter/Route/Coordination ownership moves to the Network
Planner. It is not a fallback, compatibility mode, or alternative accepted
architecture.

## Contract

The sole authoritative chain is:

```text
Timeline timestamp
→ Topology.frame_at(timestamp)
  → PositionModel.compute_positions
  → immutable PhysicalFrame(timestamp + Positions)
→ temporary legacy Slicer
  → ConnectionModel.compute_connection_slice
  → RouteModel.compute_route_slice
  → complete legacy TopologySlice publication
  → optional resolve_path / adjacent TopologyDifference
→ existing Planner / Compiler / Runner path
```

PhysicalFrame is not a renamed TopologySlice. Its timestamp is its identity,
its Positions cover exactly the Topology node set, and it has no Connection,
Route, Zone-serving, planning, forwarding, Difference, or realized-runtime
state. Slicer temporarily copies those Positions into TopologySlice only
because Planner/Compiler migration is deferred.

Read the detailed contracts by task:

- [Position and Connection values](position-connection-values.md);
- [Model Protocols and built-in algorithms](model-contracts.md);
- [routing universe, transit, and path resolution](routing-path-contracts.md);
- [Timeline, Slice 0, atomic retry, retention, and Difference](timeline-slice-difference-contracts.md).

## Invariants

- PhysicalFrame Position mapping covers every and only Topology Satellite and
  GroundStation;
- Connection endpoints and Zone assignments belong to Topology;
- every next hop has a current Connection direction;
- every published route chain is complete, loop-free, terminal-correct, and
  obeys transit policy;
- the selected RouteModel, not Slicer, owns the demanded source/target
  universe;
- publication occurs only after cross-result validation;
- generated IDs are continuous and monotonic even if a future retention policy
  removes an older retained prefix;
- each PositionModel belongs to only one Topology; each ConnectionModel and
  RouteModel belongs to only one legacy Slicer;
- `SimulationTimeline.iter_timestamps()` is the only simulation-progression
  timestamp authority; Topology projects a requested timestamp without
  progression.

## Failure semantics

Timeline exhaustion raises `StopIteration`; unbounded `create_all_slices`
raises `RuntimeError`; missing retained values and unknown identities raise
`KeyError`; known but unserved/unreachable destinations raise `LookupError`;
malformed/foreign Model output and corrupt paths raise `TypeError` or
`ValueError`. A failed candidate publishes nothing and consumes neither its
ID nor pending timestamp.

## Prohibited behavior

No PositionModel call outside Topology, ConnectionModel/RouteModel call outside
the explicit temporary legacy Slicer, fallback Model, partial Slice,
non-adjacent Difference, Tool-side comparator, backend operation, premature
renaming of legacy work as Planner work, or Runner involvement in production.

## Extension points

The approved extension points are the Topology-owned `PositionModel` and the
temporarily Slicer-owned `ConnectionModel`/`RouteModel` Protocol
implementations. They return complete values and do not own publication,
retention, fallback, Planning, or backend state. New Slicer consumers or
compatibility abstractions are prohibited.

## Maintenance

Update the focused contract that owns the changed fact, topology/models and
temporary slicing code, main-chain/Difference tests, Simulator chain, public
imports, Tool projection tests, invariants/failures, and examples. Do not
document the temporary seam as the accepted target.
