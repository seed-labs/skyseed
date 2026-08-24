# Dynamic Slicing contract index

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Index of the accepted Position → Connection → Route dynamic state chain.
Exact values, Models, routes, Timeline/publication, path resolution, and
Difference rules live in the focused references linked below.

## Owners and consumers

Simulator creates one Slicer with one distinct instance per Model role. Slicer
owns the call order, atomic publication, and retained-slice queries. Callers
and read-only Tools consume exact retained `TopologySlice`,
`ConnectionSlice`, and `RouteSlice` values. The sibling Planner asks Slicer to
create adjacent `TopologyDifference` values on request and consumes them;
Slicer does not retain those values. Read-only Tools consume the same formal
facts without creating a parallel calculation.

## Contract

The sole authoritative chain is:

```text
Timeline timestamp
→ PositionModel.compute_positions
→ ConnectionModel.compute_connection_slice
→ RouteModel.compute_route_slice
→ complete TopologySlice publication
→ optional resolve_path / adjacent TopologyDifference
```

Read the detailed contracts by task:

- [Position and Connection values](position-connection-values.md);
- [Model Protocols and built-in algorithms](model-contracts.md);
- [routing universe, transit, and path resolution](routing-path-contracts.md);
- [Timeline, Slice 0, atomic retry, retention, and Difference](timeline-slice-difference-contracts.md).

## Invariants

- Position mapping covers every and only spatial Topology node;
- Connection endpoints and Zone assignments belong to Topology;
- every next hop has a current Connection direction;
- route chains are complete, loop-free, terminal-correct, and obey transit
  policy;
- all reachable required source/target routes are present;
- publication occurs only after cross-result validation;
- generated IDs are continuous and monotonic even if a future retention policy
  removes an older retained prefix;
- each Model instance belongs to only one Slicer;
- `SimulationTimeline.iter_timestamps()` is the only timestamp authority.

## Failure semantics

Timeline exhaustion raises `StopIteration`; unbounded `create_all_slices`
raises `RuntimeError`; missing retained values and unknown identities raise
`KeyError`; known but unserved/unreachable destinations raise `LookupError`;
malformed/foreign Model output and corrupt paths raise `TypeError` or
`ValueError`. A failed candidate publishes nothing and consumes neither its
ID nor pending timestamp.

## Prohibited behavior

No Model calls outside Slicer, fallback Model, partial Slice, non-adjacent
Difference, Tool-side comparator, backend operation, Planner-owned Slice/
Difference production, or Runner involvement.

## Extension points

The approved extension points are `PositionModel`, `ConnectionModel`, and
`RouteModel` Protocol implementations. They return complete values and do not
own publication, retention, fallback, Planning, or backend state.

## Maintenance

Update the focused contract that owns the changed fact, slicing/models,
main-chain/Difference tests, Simulator chain, public imports, Tool projection
tests, invariants/failures, and examples.
