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
`source → target → next-hop NodeId` table. A Model may publish sources from the
Topology Satellite/GroundStation universe according to its own demand policy.
It stores no entry at a terminal and no
full path, metric, candidate, alternate, label, interface, or Slice ID. Empty
is legal.

G targets identify Topology GroundStations. Z targets identify Zones currently
assigned a service Satellite. A G target GroundStation and a Z service
Satellite are terminals and therefore have no self entry. Which source/target
pairs are demanded is RouteModel policy, not a universal Slicer matrix.
GroundStations get no G-to-G routes in the built-in policies.

GroundStation access does not grant backbone-transit capability:

- for G, the source is a Satellite, the target GroundStation is the only
  permitted GroundStation and terminal, and every intermediate is a
  Satellite;
- for Z, a GroundStation source may take one of its own access Connections
  into the Satellite network; every subsequent intermediate and the
  service-Satellite terminal is a Satellite;
- `Satellite → GroundStation → Satellite` is unreachable even if both
  undirected access Connections exist.

The built-in `HopCountRouteModel` is the full-reachability baseline/small-scale
reference. It emits every reachable Satellite-to-G and
Satellite/GroundStation-to-served-Z forwarding tree allowed by the transit
rule. Reverse BFS minimizes hops and chooses the structural-NodeId-minimum next
hop on a tie. Directional parameters may differ but this algorithm uses edge
existence only.

`GatewayHopCountRouteModel` is the satellite-Internet default. Once per exact
Topology it ranks GroundStations for each Zone representative point by
great-circle distance and stable GroundStation identity, applies optional
distance and candidate-count limits, and retains that ordered candidate list
as RouteModel policy. Each Slice independently materializes currently
reachable Zone-to-candidate-G and candidate-G-to-Z paths using shared
deterministic hop-count trees. It does not dynamically substitute a different
Gateway, force path reversal, or materialize unrelated reachable pairs.

A missing sparse entry can mean current unreachability or intentional absence
from the selected Model's demand policy. `Slicer.resolve_path(slice_id, source, target)` first
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
- every published source/target entry has a complete chain;
- route chains are loop-free, terminal-correct, and transit-eligible;
- insertion/Connection enumeration order cannot change the result.

## Failure semantics

Unknown retained Slice/node/G target/Zone raises `KeyError`; a known but
currently unserved Zone or currently unreachable source-target (including an
undemanded pair) raises `LookupError`;
malformed targets, foreign next hops/sources, duplicate RouteKey, missing
current edge, incomplete chain, loop, wrong terminal, or prohibited
GroundStation transit raises `TypeError` or `ValueError`.

## Prohibited behavior

No Slicer-owned demand matrix, GroundStation backbone relay, implicit G-to-G
routes, terminal entries, path storage, metric/alternate/backend state,
visibility rerun, Tool-side routing authority, or fallback route algorithm.

## Extension points

Alternative RouteModel algorithms may choose a different demand universe or
eligible metric only as an explicitly chosen Model. Slicer validates every
published entry structurally but does not reproduce Model policy. Ground relay
requires an explicit capability and a different approved Model; it is never
inferred from access Connections.

## Maintenance

Update `slicing/route.py`, `_routing_policy.py`, Slicer validation/path code,
Route Model tests, path failure tests, Slice / Difference Tool projection, public imports,
and this reference.
