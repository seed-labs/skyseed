# Routing and path contracts

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

G/Z route targets, sparse route universe, terminal/unreachable semantics,
GroundStation transit eligibility, built-in hop-count behavior, validation,
and full-path resolution.

## Owners and consumers

RouteModel creates `RouteSlice`; TopologySlice retains it; Slicer validates it
and resolves full paths using its exact Topology and current ConnectionSlice.
Tools only read those formal results.

## Contract

`GroundStationRouteTarget(ground_station_id)` is tagged G and carries an exact
GroundStation `NodeId`. `ZoneRouteTarget(zone_id)` is tagged Z and always
carries ZoneId. Service Satellite, user terminal, label, FEC, interface,
timestamp, path, and backend generation are not target/key fields.
`RouteKey` is exactly `(source NodeId, RouteTarget)`.

`RouteSlice` is a frozen, slotted, deeply immutable sparse
`source → target → next-hop NodeId` table. Its source universe is every
Topology Satellite and GroundStation. It stores no entry at a terminal and no
full path, metric, candidate, alternate, label, interface, or Slice ID. Empty
is legal.

G targets are all Topology GroundStations. Z targets are only Zones currently
assigned a service Satellite. Satellites get routes for every reachable G and
served Z target. GroundStations get no G-to-G routes and may get routes to
reachable served Z targets. A G target GroundStation and a Z service Satellite
are terminals and therefore have no self entry.

GroundStation access does not grant backbone-transit capability:

- for G, the source is a Satellite, the target GroundStation is the only
  permitted GroundStation and terminal, and every intermediate is a
  Satellite;
- for Z, a GroundStation source may take one of its own access Connections
  into the Satellite network; every subsequent intermediate and the
  service-Satellite terminal is a Satellite;
- `Satellite → GroundStation → Satellite` is unreachable even if both
  undirected access Connections exist.

The built-in HopCount model uses reverse BFS over that eligible graph,
minimizes hops, and chooses the structural-NodeId-minimum next hop on a tie.
Directional parameters may differ but this algorithm uses edge existence only.

A missing sparse entry is legal current unreachability unless the source is
already the terminal. `Slicer.resolve_path(slice_id, source, target)` first
loads the retained Slice and exact owner Topology, resolves the G terminal or
current Z service Satellite, then follows formal next hops over current
Connections. It returns the source-through-terminal tuple without storing it
in RouteSlice and never accepts an arbitrary caller-supplied Topology. The
sibling Planner calls this same formal query, converts each path to immutable
Connection-identity facts, and passes that narrow context to its forwarding
algorithm for timing and minimal direct downstream-first precedences. Tuple
order remains deterministic but non-authoritative. The replaceable
ForwardingPlanningModel never receives Slicer, invokes RouteModel, or
establishes a second path computation.

Unknown node or G/Zone identity, known but currently unserved Zone, current
unreachability, incomplete/corrupt route chain, loop, wrong terminal, absent
Connection, and ineligible GroundStation transit remain distinct outcomes.

## Invariants

- every source/next hop belongs to exact Topology;
- every next-hop direction has a current effective Connection;
- no terminal route entry exists;
- every reachable required source/target pair has a complete entry chain;
- route chains are loop-free, terminal-correct, and transit-eligible;
- insertion/Connection enumeration order cannot change the result.

## Failure semantics

Unknown retained Slice/node/G target/Zone raises `KeyError`; a known unserved
Zone or currently unreachable source-target raises `LookupError`; malformed
targets, foreign next hops/sources, duplicate RouteKey, missing current edge,
omitted reachable route, incomplete chain, loop, wrong terminal, or prohibited
GroundStation transit raises `TypeError` or `ValueError`.

## Prohibited behavior

No GroundStation backbone relay, implicit G-to-G routes, terminal entries,
path storage, metric/alternate/backend state, visibility rerun, Tool-side
routing, or fallback route algorithm.

## Extension points

Alternative RouteModel algorithms may use different eligible metrics only as
an explicitly chosen Model. Ground relay requires an explicit capability and
a different approved Model; it is never inferred from access Connections.

## Maintenance

Update `slicing/route.py`, `_routing_policy.py`, Slicer validation/path code,
Route Model tests, path failure tests, Dynamic Tool projection, public imports,
and this reference.
