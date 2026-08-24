# Model contracts and built-in algorithms

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

The three replaceable Protocols, their lifecycle, and the exact supported
semantics of `CircularOrbitPositionModel`, `GeometricConnectionModel`, and
`HopCountRouteModel`.

## Owners and consumers

One Slicer owns one distinct instance for each Model role and is their sole
formal caller. A Model may own immutable configuration and immutable prepared
state derived from its exact Slicer context. TopologySlice owns published
results; Models do not.

## Contract

| Model | Inputs | Complete output | Lifecycle / prohibited state |
|---|---|---|---|
| `PositionModel` | exact `Topology`, one Timeline timestamp | read-only `NodeId → EarthFixedPosition` for every spatial node | no previous Slice, Position history, trajectory, publication, or fallback |
| `ConnectionModel` | exact `Topology`, timestamp, current complete Positions, previous `ConnectionSlice` | one complete effective `ConnectionSlice` | owns candidate/geometry/selection/Zone-service algorithm; no Route, publication, authoritative results, handover history, or fallback |
| `RouteModel` | exact `Topology`, timestamp, current `ConnectionSlice`, previous `RouteSlice` | one complete sparse `RouteSlice` | consumes effective Connections only; no visibility/coverage rerun, full-path store, backend/label/interface state, publication, or fallback |

Each instance belongs to one running Slicer context, cannot fill two roles,
cannot be replaced, and cannot be concurrently reused by another live Slicer.
Prepared state must be deterministic from exact Topology, Timeline, and formal
inputs. No Model may retain unbounded hidden history, mutable dwell/handover
time, Planning state, backend state, or authoritative Slice results. Failure
never switches Models.

`CircularOrbitPositionModel` is explicit, stateless, and supports planned
circular Orbits only: altitude, inclination, RAAN, Orbit epoch, and Satellite
mean anomaly at epoch. It advances circular mean motion using exact UTC
elapsed seconds, interprets angles against declared GCRS axes, converts the
timestamp's GCRS Cartesian positions to ITRS/ECEF, and adds fixed
GroundStation ECEF. It returns one complete read-only mapping per timestamp.
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

At `Timeline.start_time`, already-computed reference ECEF Positions choose the
lexicographically minimum `(maximum_reference_distance,
total_reference_distance, k)`. LOS, configured distance threshold, active
count, route connectivity, and Zone coverage are not pairing objectives. The
selected NodeId pairs are private immutable prepared state. The first Slice
uses PositionModel once for both pairing preparation and activation;
ConnectionModel never invokes PositionModel. Downstream failure retains this
same prepared start-time pairing for retry.

Each Slice activates fixed ISL candidates only with complete finite Positions,
Earth LOS, and distance within the configured maximum. For each GroundStation,
eligible Satellites meet elevation/LOS/optional-distance bounds and sort by
descending elevation then structural NodeId; the first configured N are
selected. Zero access is legal.

Zone selection uses each Satellite subpoint and temporary `SphericalCap` for a
coarse Topology/ZoneLayout intersection query. Formal eligibility additionally
requires the Zone representative point to satisfy service conditions and
minimum elevation. Eligible Satellites rank by representative-point elevation
then structural NodeId; at most one is selected. This represents service at
the representative point, not every possible terminal in the Zone. Gateway
hops, Route evidence, capacity/load balancing, mutable dwell, hysteresis, and
composite scoring are excluded.

Built-in Connection bandwidth/loss come from explicit per-kind configuration;
delay is current ECEF distance divided by fixed propagation speed and
deterministically converted to integer microseconds. Both directions are equal
in this implementation although the public value is asymmetric. The previous
ConnectionSlice input is accepted and ignored.

`HopCountRouteModel` is explicit and stateless. It treats every current
Connection as bidirectional for eligible edge existence, applies the
GroundStation transit rule in
[routing contracts](routing-path-contracts.md), computes reverse-BFS minimum
hop next hops, and breaks ties by structural NodeId independently of
Connection enumeration. It accepts and ignores previous RouteSlice; it does
not use directional metric values. Delay-weighted, previous-stable, ground
relay, ECMP, and multipath algorithms are separate future choices.

## Invariants

- Slicer calls Position → Connection → Route exactly once per candidate;
- every Model output is complete before the next Model runs;
- stable adjacent-Orbit pairing is prepared only from the exact first
  timestamp Positions of the owning Slicer;
- prepared pairing remains fixed across Slices and retries;
- every configured parameter is explicit, finite, and in its valid range;
- built-in results are independent of input insertion/enumeration order.

## Failure semantics

Wrong Model role/type/reuse, invalid configuration, missing/non-finite/foreign
Position, duplicate RAAN/mean anomaly, unequal adjacent Orbit counts,
degenerate inclination, corrupt prepared context, or partial/foreign output
fails closed. Obstructed/too-distant candidates, zero access, and unserved
Zones are legal non-activation.

## Prohibited behavior

No shared base class/Model bundle, hidden default, Model fallback, second
invocation chain, Model-owned publication/history, dynamic re-pairing,
per-Slice nearest-neighbour/global matching, GroundStation relay hidden in
access, or Tool-side Model execution.

## Extension points

The three structural Protocols are the only approved Model extension points.
An alternative implementation must preserve its role's formal input/output,
owner validation, atomicity, and failure semantics.

## Maintenance

Update `simulator/{slicing,models}/`, focused Model/main-chain tests, routing
contract when transit changes, Slice / Difference Tool projections, Simulator diagrams,
public imports, and this reference.
