# Model contracts and built-in algorithms

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

The three replaceable Protocols, their split physical/temporary-operational
lifecycle, and the exact supported semantics of
`CircularOrbitPositionModel`, `GeometricConnectionModel`,
`HopCountRouteModel`, and `GatewayHopCountRouteModel`.

## Owners and consumers

One Topology owns one distinct PositionModel and is its sole formal caller.
The temporary legacy Slicer owns one distinct ConnectionModel and RouteModel
and is their sole formal caller. A Model may own immutable configuration and
immutable prepared state derived from its exact owner context. PhysicalFrame
owns the immutable physical Positions; legacy TopologySlice owns the temporary
operational results. Models do not own either value.

## Contract

| Model | Inputs | Complete output | Lifecycle / prohibited state |
|---|---|---|---|
| `PositionModel` | exact owning `Topology`, one requested timestamp | read-only `NodeId → EarthFixedPosition` for every Satellite and GroundStation | no previous frame/Slice, Position history, trajectory, publication, progression, or fallback |
| `ConnectionModel` | exact `Topology`, timestamp, current complete Positions, previous `ConnectionSlice` | one complete effective `ConnectionSlice` | owns candidate/geometry/selection/Zone-service algorithm; no Route, publication, authoritative results, handover history, or fallback |
| `RouteModel` | exact `Topology`, timestamp, current `ConnectionSlice`, previous `RouteSlice` | one complete sparse `RouteSlice` | consumes effective Connections only; no visibility/coverage rerun, full-path store, backend/label/interface state, publication, or fallback |

Each PositionModel instance belongs to one live Topology and cannot be reused
by another. Each ConnectionModel and RouteModel belongs to one running legacy
Slicer context, cannot fill both roles, and cannot be concurrently reused by
another live Slicer. All three bound role instances are distinct and cannot be
replaced. Prepared state must be deterministic from exact Topology, Timeline
where applicable, and formal inputs. No Model may retain unbounded hidden
history, mutable dwell/handover time, Planning state, backend state, or
authoritative frame/Slice results. Failure never switches Models.

`CircularOrbitPositionModel` is explicit, stateless, and supports planned
circular Orbits only: altitude, inclination, RAAN, Orbit epoch, and Satellite
mean anomaly at epoch. It advances circular mean motion using exact UTC
elapsed seconds, interprets angles against declared GCRS axes, converts the
timestamp's GCRS Cartesian positions to ITRS/ECEF, and adds fixed
GroundStation ECEF. Topology validates its one complete read-only mapping and
publishes it as a PhysicalFrame for the requested timestamp.
It excludes TLE/SGP4, eccentricity, argument of perigee, velocity,
perturbations, precise ephemeris, and a public continuous trajectory.
Precomputation/vectorization is private and must remain equivalent to the
one-timestamp operation.

`GeometricConnectionModel` has explicit immutable configuration for maximum
ISL distance; GroundStation minimum elevation, maximum access count, optional
maximum distance; Zone minimum elevation; per-kind bandwidth and packet loss;
and propagation speed. No v1 constant is implicit.

Same-Orbit candidates use unique ascending mean anomaly: one Satellite yields
none, two yield one undirected pair, and three or more form a closed ring.
Duplicate mean anomaly fails preparation.

Within one AS and Shell, unique ascending RAAN forms circular adjacent-Orbit
pairs. Two Orbits produce one undirected Orbit pair. Duplicate RAAN fails;
Orbit ID cannot resolve it. A multi-Orbit Shell at inclination `0°` or `180°`
fails because RAAN plane semantics degenerate. Cross-Shell/cross-AS ISLs are
unsupported. Adjacent Orbits must contain equal Satellite counts. Each side is
ordered by mean anomaly and candidates use:

```text
A[i] ↔ B[(i + k) mod N], k = 0..N-1
```

At `Timeline.start_time`, the already-computed PhysicalFrame Positions choose
the lexicographically minimum `(maximum_reference_distance,
total_reference_distance, k)`. LOS, configured distance threshold, active
count, route connectivity, and Zone coverage are not pairing objectives. The
selected NodeId pairs are private immutable prepared state. The first Slice
requests one PhysicalFrame and uses its Positions for both pairing preparation
and activation; ConnectionModel never invokes PositionModel. Downstream
failure retries the same timestamp and retains this same prepared start-time
pairing.

Each Slice activates fixed ISL candidates only with complete finite Positions,
Earth LOS, and distance within the configured maximum. For each GroundStation,
eligible Satellites meet elevation/LOS/optional-distance bounds and sort by
descending elevation then structural NodeId. The built-in model's configured
maximum remains algorithm/experiment policy. Its effective per-site selection
limit is that model maximum when the GroundStation capability is `None`, or
the minimum of the model maximum and
`GroundStation.max_simultaneous_satellite_accesses` otherwise. Zero model
access is legal.

Satellite `max_simultaneous_ground_station_accesses` is a separate optional
hard cap on GroundStation-access Connections terminating at that Satellite.
It does not constrain ISLs or total Satellite degree. If no Satellite has an
explicit cap, each GroundStation independently keeps its highest-elevation
eligible Satellites exactly as before. Otherwise, the built-in model performs
a deterministic two-phase capacitated bipartite assignment over the unchanged
eligible candidates. Primary allocation gives each GroundStation at most one
request, maximizes covered GroundStations, and then minimizes aggregate
candidate rank among all maximum-cardinality primary matchings. Candidate rank
is exactly descending elevation followed by ascending structural Satellite
NodeId. The resulting primary assignments are frozen.

Redundancy allocation then uses only residual Satellite capacity and each
covered site's remaining effective limit. It maximizes additional access
links with the same deterministic candidate traversal and augmenting fallback,
but frozen primaries are never movable occupants: redundancy cannot displace
or degrade one to admit another link. Requests within each redundant slot use
descending structural GroundStation NodeId, and movable redundant occupants
use descending `(slot, GroundStation NodeId)` order. This is a simple
deterministic Skyseed baseline, not an optimal operational constellation
scheduler, active/standby declaration, or handover-state model.

The temporary legacy Slicer validates every complete ConnectionModel output
before RouteModel runs or a Slice is published. A custom model may select
Satellites differently, but formal `GROUND_STATION_ACCESS` Connections may not
exceed an explicit GroundStation site capability or an explicit Satellite
GroundStation-access capability. Over-cap output fails closed without
publishing the candidate Slice. Neither capability is a physical antenna
count; ISLs are excluded.

Zone selection uses each Satellite subpoint and temporary `SphericalCap` for a
coarse Topology/ZoneLayout intersection query. Formal eligibility additionally
requires the Zone representative point to satisfy service conditions and
minimum elevation. Eligible Satellites rank by representative-point elevation
then structural NodeId; at most one is selected. This represents service at
the representative point, not every possible terminal in the Zone. Gateway
hops, Route evidence, capacity/load balancing, mutable dwell, hysteresis, and
composite scoring are excluded.

GroundStation and Zone spatial indexes are private conservative candidate
pruning only. Every retained candidate still passes the exact formal
ECEF/LOS/elevation/distance predicates, and exhaustive-reference equivalence
tests cover representative S4 timestamps.

Built-in Connection bandwidth/loss come from explicit per-kind configuration;
delay is current ECEF distance divided by fixed propagation speed and
deterministically converted to integer microseconds. Both directions are equal
in this implementation although the public value is asymmetric. The previous
ConnectionSlice input is accepted and ignored.

`HopCountRouteModel` is the explicit, stateless full-reachability baseline and
small-scale reference. It treats every current
Connection as bidirectional for eligible edge existence, applies the
GroundStation transit rule in
[routing contracts](routing-path-contracts.md), computes reverse-BFS minimum
hop next hops, and breaks ties by structural NodeId independently of
Connection enumeration. It accepts and ignores previous RouteSlice; it does
not use directional metric values. Delay-weighted, previous-stable, ground
relay, ECMP, and multipath algorithms are separate future choices.

`GatewayHopCountRouteModel` is the satellite-Internet default. Its positive
`gateway_candidate_count` and optional positive finite
`gateway_candidate_max_distance_m` are explicit routing-policy parameters.
Once for one exact Topology, it ranks each Zone representative point's
GroundStations by static great-circle distance then structural NodeId, applies
the optional cutoff, and retains the first configured count. Each Slice uses
shared deterministic hop-count trees to materialize only currently reachable
Zone-to-candidate-GroundStation and independently computed reverse-direction
paths with demand-bounded graph traversal. It neither dynamically substitutes
another Gateway nor stores full paths; UT/traffic endpoint selection is
outside Simulator progression.

## Invariants

- Topology calls PositionModel exactly once per `frame_at()` projection and
  validates the complete result before publishing PhysicalFrame;
- the temporary legacy Slicer consumes one PhysicalFrame and calls Connection
  → Route exactly once per candidate;
- every Model output is complete before the next Model runs;
- stable adjacent-Orbit pairing is prepared only from the exact first
  PhysicalFrame Positions of the owning legacy Slicer;
- prepared pairing remains fixed across Slices and retries;
- every configured parameter is explicit, finite, and in its valid range;
- built-in results are independent of input insertion/enumeration order;
- Slicer validates every published Route chain but does not prescribe or
  reconstruct a RouteModel demand universe.

## Failure semantics

Wrong Model role/type/reuse, invalid configuration, incomplete, non-finite, or
foreign PhysicalFrame Position, duplicate RAAN/mean anomaly, unequal adjacent
Orbit counts, degenerate inclination, corrupt prepared context, or partial or
foreign output fails closed. Obstructed/too-distant candidates, zero access,
and unserved Zones are legal non-activation.

## Prohibited behavior

No shared base class/Model bundle, hidden default, Model fallback, second
invocation chain, Slicer-side PositionModel call or alias, Model-owned
publication/history, dynamic ISL re-pairing, dynamic Gateway-candidate
substitution, GroundStation relay hidden in access, or Tool-side Model
execution.

## Extension points

The three structural Protocols are the only approved Model extension points.
PositionModel is owned and called only through Topology; it has no slicing
alias. ConnectionModel and RouteModel remain temporary legacy Slicer
extensions pending their Network Planner migration. An alternative
implementation must preserve its role's formal input/output, owner validation,
atomicity, and failure semantics.

## Maintenance

Update `simulator/{topology,slicing,models}/`, focused Model/main-chain tests,
routing contract when transit changes, Slice / Difference Tool projections,
Simulator diagrams, public imports, and this reference.
