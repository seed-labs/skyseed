# Dynamic Slice Public Contract

## Status and authority

Status: **ACCEPTED / FROZEN — A–E AND F IMPLEMENTED AND ACCEPTED; G NOT
IMPLEMENTED**

Accepted against baseline `cfaa4930cba2d1a6953617b274ef528f9ae4059a`.
This document is the detailed authoritative contract for the approved A
through G implementation dependency order. `accepted-design.md` is its short
architecture anchor and `CURRENT.md` records implementation status.

The contract freezes public concepts, ownership, data flow, validation and
failure semantics. Suggested final file placement may be adjusted during
implementation without changing those contracts. Candidate investigations,
v1 references and Model diagnostics are not architecture authorities.

## 1. Composition and ownership

```text
SimulationDefinition
├── SimulatorStaticInput
└── SimulationTimeline
          │
          ▼
      Simulator
      ├── Topology
      │   ├── SkyseedAutonomousSystem
      │   ├── Shell
      │   ├── Orbit
      │   ├── Satellite
      │   ├── GroundStation
      │   └── ZoneLayout
      └── Slicer
          ├── PositionModel
          ├── ConnectionModel
          ├── RouteModel
          └── published TopologySlice sequence
```

`Simulator` remains the composition root. It retains one exact
`SimulationDefinition`, explicitly and atomically creates at most one
`Topology`, and only after successful Topology creation explicitly and
atomically creates at most one `Slicer`.

```python
topology = simulator.create_topology()
slicer = simulator.create_slicer(
    position_model=position_model,
    connection_model=connection_model,
    route_model=route_model,
)
```

Neither Topology nor Slicer can be replaced. Failed creation publishes no
partial object. Simulator supplies no hidden Model, hidden default algorithm
or fallback, does not create TopologySlice directly, and owns no Connection,
Route, Difference or Plan data.

`Topology` is the immutable static world and unified static query root. It
owns the static hierarchy, `NodeId` index, static Orbit definitions and at
most one `ZoneLayout`. It owns no Timeline, Slicer, Position, Connection,
Route, Slice, Difference, Planner or backend state.

`Slicer` retains exact, non-replaceable references to Topology, Timeline and
the three Models. It owns dynamic generation order, Model invocation,
validation, atomic Slice publication, published ordering, retention-window
queries and adjacent Difference creation. It owns neither static Topology nor
Model algorithms, Planning, labels or backend resources.

## 2. Time, Orbit epoch and GroundStation height

`SimulationTimeline` accepts only timezone-aware `datetime` values, normalizes
start and end immediately to UTC, stores and yields UTC timestamps, and
remains the only authority for real Slice timestamps. Equivalent instants in
different offsets normalize to the same value. Users cannot ask Slicer to
create a Slice for an arbitrary non-Timeline timestamp.

`OrbitParameters` adds `epoch_utc`:

```text
OrbitParameters
├── altitude_km
├── inclination_deg
├── right_ascension_deg
└── epoch_utc
```

Epoch is timezone-aware and UTC-normalized static Orbit definition data. It is
not Timeline start and not Slicer state. Each Satellite's
`mean_anomaly_deg` means its mean anomaly at its parent Orbit's epoch. Epoch
enters the first configuration version once per Shell: YAML and programmatic
Shell configuration must explicitly supply one common `epoch_utc`, and
per-Orbit epoch override within that Shell is unsupported. Shell construction
copies that common epoch into every independently owned Emulator
`OrbitParameters`. Direct programmatic `OrbitParameters(...)` construction
must also supply it explicitly. Epoch then follows the complete static chain:

```text
Programmatic/YAML Shell Config
→ Emulator OrbitParameters
→ SimulatorStaticInput
→ Simulator Topology Orbit
```

Conflicting repeated epoch projection for one Orbit fails Topology
materialization.

GroundStation elevation means WGS84 ellipsoidal height in metres. A missing
construction/configuration value may be resolved to `0.0 m` at that boundary;
formal Emulator and Simulator domain values are finite real numbers and never
`None`.

## 3. Coordinates and Position

`EarthFixedPosition` is the only authoritative Position:

```text
EarthFixedPosition
├── x_m
├── y_m
└── z_m
```

It is frozen, slotted, finite, WGS84 Earth-fixed geocentric Cartesian,
equivalent to EPSG:4978 semantics, in metres.

WGS84 geodetic `latitude_deg`, `longitude_deg` and
`ellipsoidal_height_m` are deterministic read-only derived views, never a
second authoritative Position state.

The project has exactly one fixed coordinate capability for:

- WGS84 geodetic to ECEF;
- ECEF to WGS84 geodetic;
- ECEF distance;
- Earth line-of-sight/obstruction;
- elevation.

PositionModel, ConnectionModel and Tools reuse it. Tools do not propagate
Satellites or implement coordinate conversions; ConnectionModel does not use
a second spherical model. The selected third-party library, version and
Python support are implementation-spike decisions, not domain contracts.

## 4. Replaceable Model Protocols and lifecycle

The three top-level replaceable Models are structural public Protocols:

```text
PositionModel
ConnectionModel
RouteModel
```

They do not require a shared abstract base class, have no `ModelBundle`, and
do not create a second Slicer invocation chain.

Conceptual signatures:

```text
PositionModel:
Topology + timestamp
→ readonly NodeId → EarthFixedPosition

ConnectionModel:
Topology + timestamp + current positions + previous ConnectionSlice
→ ConnectionSlice

RouteModel:
Topology + timestamp + current ConnectionSlice + previous RouteSlice
→ RouteSlice
```

PositionModel receives no previous Slice, is deterministic for the same formal
input, returns all required spatial nodes—initially all Satellites and
GroundStations—and never publishes partial results or owns formal Position
history.

ConnectionModel owns the complete connection-formation algorithm: structural
candidates, visibility/coverage geometry, actual selection, Zone service
selection and bidirectional effective parameters. Candidate scores and
geometry evidence are not ConnectionSlice data.

RouteModel consumes only current effective Connections. It does not repeat
visibility/coverage decisions, own labels/interfaces/backend state or store
full paths in RouteSlice.

Each Model instance belongs to one Slicer running context. It cannot be
replaced or concurrently reused by another running Slicer. It may retain
immutable algorithm configuration and immutable prepared state
deterministically derived from exact Topology, Timeline and formal inputs. It
may not retain authoritative Slice results, unbounded hidden history, mutable
dwell/handover time, Planning or backend state. Failure never switches Models.

## 5. Built-in CircularOrbitPositionModel

`CircularOrbitPositionModel` is an explicit built-in choice, never a hidden
default. It supports only planned circular Orbits using altitude, inclination,
RAAN, Orbit epoch and Satellite mean anomaly at epoch.

It explicitly excludes TLE/SGP4, eccentricity, argument of perigee, velocity,
perturbation, precise ephemeris and a public continuous-trajectory object.

For each real timestamp it returns a complete read-only Position mapping for
all Satellites and GroundStations in one ECEF contract. Private
implementation may precompute GroundStation ECEF, precompute per Orbit,
vectorize, cache or batch, but the public semantic operation remains exactly
one timestamp and all optimizations must be equivalent to it.

## 6. SphericalCap and Zone range queries

The first version adds no generic polygon or geometry framework.

```text
SphericalCap
├── center
└── angular_radius_deg
```

It is a frozen, slotted spherical Earth-surface circle whose center reuses the
formal geographic-point value and whose radius is in `[0, 180]`.

`ZoneLayout.find_intersecting_zones(spherical_cap)` returns every intersecting
Zone in deterministic order and handles the date line and poles. It neither
selects service Satellites nor stores dynamic candidates. ZoneLayout remains
the owner of static Zones and static surface geometry only.

## 7. Connection identity and values

Initial `ConnectionKind` members are:

```text
INTER_SATELLITE
GROUND_STATION_ACCESS
```

Same-plane and cross-plane are internal candidate sources, not separate kinds.
`USER_TERMINAL_ACCESS` is deferred to the UT stage.

Every deterministic ordering uses the structural `NodeId` key:

```text
(asn, entity_kind, entity_id)
```

`NodeId` itself does not gain natural ordering. Set order, dictionary
insertion order, object identity and YAML order are forbidden tie-breaks.

```text
ConnectionId
├── kind
├── endpoint_a
└── endpoint_b
```

Endpoints are distinct and normalized by structural key, belong to the exact
Topology, and form at most one Connection per kind/pair in one Slice.
ConnectionId is stable across Slices and contains no temporary resource ID.

```text
DirectionalConnectionParameters
├── bandwidth_bps: int > 0
├── delay_us: int >= 0
└── packet_loss_ppm: int in 0..1_000_000
```

```text
Connection
├── connection_id
├── endpoint_a_to_b
└── endpoint_b_to_a
```

Both directions are forwarding-capable. Parameters may differ, while the
built-in first Model may produce equal values.

## 8. ConnectionSlice

```text
ConnectionSlice
├── connections: tuple[Connection, ...]
└── zone_service_assignments:
      readonly Mapping[ZoneId, Satellite NodeId]
```

It is frozen, slotted and deeply immutable. Connections use ConnectionId
canonical order; Zone service entries use stable ZoneId order. It contains
only currently effective connections and a partial Zone assignment mapping.
It contains no candidate, visibility, coverage, distance, elevation, score or
independent Slice ID.

Each Zone has at most one service Satellite; one Satellite may serve several
Zones; an absent Zone is legally unserved. Queries cover ConnectionId, node
connections, kind-specific endpoint connection, Zone service Satellite and
Zones served by a Satellite.

## 9. Built-in GeometricConnectionModel

`GeometricConnectionModel` is an explicit built-in choice. Its immutable
configuration explicitly supplies maximum ISL distance, GroundStation minimum
elevation, maximum access count per GroundStation, optional GroundStation
maximum distance, Zone coverage minimum elevation, and bandwidth/packet-loss
values per ConnectionKind. No v1 constant is implicit.

### Same-Orbit candidates

Each Orbit is ordered by unique `mean_anomaly_deg`, never `orbit_index`.
One Satellite yields no candidate, two yield one undirected candidate, and
three or more form a closed ring. Duplicate mean anomaly fails Model
preparation.

### Adjacent-Orbit candidates

Within one AS and Shell, unique RAAN ascending order forms a circular
adjacency. Two Orbits form one undirected pair. Duplicate RAAN fails; orbit ID
does not resolve geometric ambiguity. Multi-Orbit Shell inclination `0°` or
`180°` fails because RAAN plane semantics degenerate. Cross-Shell and cross-AS
ISLs are unsupported.

For adjacent equal-size Orbits, each side is its mean-anomaly cyclic sequence:

```text
A[i] ↔ B[(i + k) mod N],  k = 0..N-1
```

Index sets need not match or be continuous. At `Timeline.start_time`, the
Model uses the already computed reference ECEF Positions and chooses the
lexicographically minimum score:

```text
(
    maximum_reference_distance,
    total_reference_distance,
    k,
)
```

LOS, the maximum-distance threshold, active count, Route connectivity and Zone
coverage are not pairing objectives. The chosen NodeId pairs are private
immutable prepared Model state, not Topology, ConnectionSlice, previous-Slice
or authoritative-history data.

The first real Slice uses one Position path:

```text
Timeline.start_time
→ PositionModel once
→ ConnectionModel prepares pairing from those Positions
→ ConnectionModel activates from the same Positions
→ RouteModel
→ atomic publication
```

ConnectionModel never invokes PositionModel.

Every Slice activates fixed ISL candidates only when Positions are complete
and finite, Earth line-of-sight holds, and distance does not exceed the
configured maximum. Obstruction and excessive distance are legal
non-activation; missing, non-finite or foreign-node Position is input failure.

For each GroundStation, eligible Satellites satisfy elevation/LOS/optional
distance limits, sort by descending elevation then structural NodeId key, and
the first configured N are selected. Zero access is legal; no capacity, load
balancing or hysteresis is used.

For Zone assignment, each Satellite's Position yields a subpoint and temporary
SphericalCap coverage. ZoneLayout intersection is only a coarse filter. A
Satellite becomes a formal candidate for a Zone only when the Zone's
deterministic representative point is inside that Satellite's service
conditions and its representative-point elevation meets the configured
minimum, using the one shared coordinate capability. For each Zone, eligible
candidate Satellites are ranked by representative-point elevation followed by
structural NodeId tie-break; at most one is selected. This means service of
the representative point, not guaranteed coverage of every future UT in the
Zone. Gateway hop, Route evidence, capacity, mutable dwell and v1 composite
scoring are forbidden.

Bandwidth and packet loss come from explicit per-kind configuration. Delay is
current ECEF endpoint distance divided by fixed propagation speed, converted
deterministically to integer microseconds. Both directions are equal in the
built-in first Model, while the Connection contract remains asymmetric.

The Model accepts but ignores previous ConnectionSlice. It does not implement
dwell, handover margin, distance relaxation, per-Slice nearest neighbour,
global matching or mutable service history.

## 10. Route targets, keys and RouteSlice

Route targets are two explicit frozen tagged values:

```text
GroundStationRouteTarget(ground_station_id: NodeId)
ZoneRouteTarget(zone_id: ZoneId)
```

Their public union is `RouteTarget`. A G payload is a GroundStation NodeId; a
Z payload is always ZoneId. Service Satellite, UT, label, FEC and interface do
not enter RouteTarget.

```text
RouteKey
├── source: NodeId
└── target: RouteTarget
```

The key excludes next hop, service Satellite, timestamp, path, label and
backend generation.

`RouteSlice` is a frozen, slotted, deeply immutable sparse table:

```text
NodeId
└── RouteTarget → next-hop NodeId
```

Its source universe is all Topology Satellites and GroundStations. It stores no
entry at a terminal and no full path, metric, candidate, alternate, label,
interface or independent Slice ID. An empty read-only table is a valid state.

## 11. Built-in HopCountRouteModel

`HopCountRouteModel` is an explicit built-in choice. Every current Connection
becomes two directed edges, including GroundStation access. Directional
parameters may differ but this Model uses only edge existence.

G targets are all Topology GroundStations. Z targets are only currently served
Zones. Satellites receive routes to reachable G and served Z targets.
GroundStations receive no G-to-G routes and do receive routes to reachable
served Z targets. GroundStation self G targets and the current Zone service
Satellite are terminals without entries.

For each target, a reverse BFS tree minimizes hop count and uses structural
NodeId tie-breaking independent of Connection enumeration or insertion order.

Missing source-target entry is legal unreachability unless the source is
already the terminal. Foreign source/next-hop, absent current direction,
duplicate RouteKey, loop or wrong terminal fails validation.

The first Model accepts and ignores previous RouteSlice; its result depends
only on exact Topology, current ConnectionSlice and immutable configuration.
Delay-weighted and previous-stable Models are separate future choices, never
silent behavior.

## 12. TopologySlice and path resolution

```text
TopologySlice
├── slice_id
├── timestamp
├── positions
├── connection_slice
└── route_slice
```

It is frozen, slotted and deeply immutable, with no Topology, Model, previous
Slice, Planner or backend reference.

Synthetic Slice 0 is immediately available after successful Slicer creation:

```text
slice_id = 0
timestamp = None
positions = empty
ConnectionSlice = empty
RouteSlice = empty
```

It is a formal queryable baseline, is not a Timeline instant, has no extra
kind field, and makes first change `0 → 1`.

Real Slices have `slice_id >= 1` and Timeline timestamps. IDs are continuous
and monotonic; each timestamp has at most one published Slice; failure
consumes neither ID nor timestamp; replacing Models requires a new Slicer.

TopologySlice exposes Position, ConnectionSlice and RouteSlice queries only.
It retains no Topology reference and does not resolve a full path.

`Slicer.resolve_path(slice_id, source, target)` looks up the specified retained
Slice and combines exact Topology, ConnectionSlice, RouteSlice and Zone
service assignment. It distinguishes unknown node or Zone, a known but
currently unserved Zone, current unreachability, corrupt route chain, loop and
wrong terminal, and returns source through terminal without writing the path
into RouteSlice. Callers cannot supply an arbitrary Topology.

## 13. Slicer generation, queries and retention

`create_next_slice()` is the only core generation operation:

```text
take next Timeline timestamp
→ PositionModel
→ validate positions
→ ConnectionModel
→ validate ConnectionSlice
→ RouteModel
→ validate RouteSlice
→ assemble TopologySlice
→ atomically publish
```

Any failure publishes no partial Slice, consumes neither ID nor timestamp,
keeps the published prefix and retries the same logical Slice next time. There
is no fallback.

`create_slices(count)` and `create_all_slices()` may only loop over the core
operation. The latter is bounded-Timeline only; an unbounded Timeline advances
through repeated finite requests.

Queries cover Slice ID, timestamp, latest Slice, latest ID/timestamp, next
timestamp, exhaustion, retained ID range and exact Topology/Timeline/Model
references, plus retained-Slice full-path resolution. No ambiguous `current`
API is accepted.

Long-term contract promises only the retained window. The first implementation
may retain all generated Slices, but permanent complete retention is not
frozen; eviction policy belongs to future runtime policy, not Timeline or
TopologySlice.

## 14. TopologyDifference

`TopologyDifference` is an independent frozen adjacent-stage contract. It is
not retained by Slicer, created by TopologySlice or owned by Planner.

```text
slicer.create_difference(previous_slice_id, current_slice_id)
```

Slicer finds retained Slices, validates same lineage and strict `N → N+1`
adjacency, invokes one fixed pure comparison algorithm and returns the
independent Difference.

Difference contains:

- previous/current Slice IDs and timestamps;
- full added Connections;
- removed ConnectionIds;
- directional changed-new-value-only Connection parameter patches;
- Zone service assignment/unassignment patches;
- Route next-hop/removal patches.

It excludes complete Slices, Position changes, old values, candidates,
visibility/geometry, Model configuration, labels, interfaces, resources,
Planning thresholds and rollback. `0 → 1` has identical semantics and carries
the complete initial dynamic-network change.

## 15. Future Planning boundary

`TopologyDifferenceWindow` is deferred. Its approved future role is a finite
container with one base TopologySlice and ordered adjacent Differences,
validating continuity and lineage.

```text
TopologyDifferenceWindow
+ explicit planning context
→ future Planner
→ future Plan
```

Planner—not current Slice implementation—owns thresholds, merge, batching,
horizon, rollback, labels, interfaces/resources and backend operations.
Planner, Plan, DifferenceWindow and backend execution are not part of Slices A
through F.

## 16. Empty states and fail-closed rules

Legal complete states include no same-Orbit connection for a one-Satellite
Orbit, obstructed/too-distant candidates, a GroundStation with no access, an
unserved Zone, an unreachable source-target and sparse/empty ConnectionSlice
or RouteSlice content.

The following fail closed rather than being skipped:

- missing/invalid Model configuration;
- missing or non-finite required Position;
- foreign/invalid NodeId;
- duplicate ConnectionId;
- duplicate RAAN for built-in pairing;
- unequal adjacent-Orbit Satellite counts;
- duplicate mean anomaly for built-in cyclic order;
- degenerate built-in multi-Orbit inclination;
- partial Model output;
- next hop absent from the current effective direction;
- duplicate RouteKey, Route loop or wrong terminal;
- hidden Model fallback.

## 17. Tool boundaries

A Dynamic Slice Inspection Tool may read frozen Position, ISL,
GroundStation-access, Zone-service, G/Z path, unreachability, hop count and
adjacent Difference results. It never reruns a Model.

A future Zone design/inspection Tool may explicitly run finite Timeline plus
approved Models/configuration against a candidate ZoneLayout for
non-authoritative design analysis. It never writes accepted ZoneLayout,
becomes Connection fallback, replaces ConnectionSlice or silently changes a
Model/configuration.

Concrete GeometricConnectionModel diagnostics may expose Orbit pair,
reference timestamp, chosen offset, maximum/total reference distance and
prepared pairs. This is optional implementation-specific, read-only,
non-authoritative diagnostics—not ConnectionSlice and not a required
ConnectionModel Protocol result.

## 18. Public exposure and module direction

Dynamic Simulator types are public primarily from `skyseed_v2.simulator` and
explicit submodules, not automatically from the package root. Root API changes
require a separate audit.

The accepted conceptual module direction is:

```text
simulator/
├── timeline
├── definition
├── simulator
├── topology
├── zone
├── position
├── connection
├── route
├── slice
├── slicer
├── difference
└── models/
```

Final file splitting may follow existing code organization, but must preserve
the frozen concepts, ownership and dependency direction.

## 19. Architecture Delta from the implemented baseline

1. `OrbitParameters` adds `epoch_utc`.
2. Timeline changes from naive/aware preservation to aware-only UTC
   normalization.
3. GroundStation elevation becomes required finite WGS84 ellipsoidal height.
4. Simulator explicitly creates and owns Slicer.
5. Topology no longer has any proposed Slice creation/comparison role.
6. ZoneLayout adds SphericalCap intersection query.
7. Position, Connection, Route, Slice, Slicer and Difference contracts become
   accepted public design.
8. Tools may read formal dynamic results without recomputation.
9. Same-Orbit rings use mean-anomaly order, not orbit index.
10. Cross-Orbit links use stable adjacent-Orbit cyclic pairing, not same-index
    pairing.

## 20. Deferred content

Not frozen in this stage:

- TLE/SGP4, non-circular Orbits, velocity, perturbation, precise ephemeris or
  public continuous trajectory;
- dynamic cross-Orbit matching, hysteresis, dwell, capacity/load balancing,
  delay-weighted routing, ECMP/multipath or UT;
- Planner, Plan, DifferenceWindow implementation or backend execution;
- qdisc/BPF, runtime resource policy or retention-window policy;
- concrete pairing-diagnostics API;
- final third-party library/Python version;
- private performance-optimization structure.

## 21. Approved implementation sequence

The labels A through G express dependency order, not seven mandatory
authorization pauses:

```text
Slice A: time, epoch, coordinate capability and Position
Slice B: Zone range plus Connection values and Protocol
Slice C: GeometricConnectionModel
Slice D: Route values, Protocol, HopCountRouteModel and path resolution
Slice E: Slicer and TopologySlice
Slice F: TopologyDifference
Slice G: Tool, canonical example and final documentation/audit
```

The engineering authorization groups are:

1. **A–E: complete Dynamic Slice main chain**;
2. **F: TopologyDifference**;
3. **G: Tool, canonical example and final synchronization**.

Each engineering group requires explicit authorization, implementation,
tests, audit and acceptance. A through E may proceed as one authorized
engineering objective without mechanically stopping between their internal
dependency steps. This architecture-contract synchronization authorizes none
of the three groups.

## 22. Sole dynamic state chain

```text
Static Topology
+ Timeline timestamp
→ PositionModel
→ readonly Position mapping
→ ConnectionModel
→ ConnectionSlice
→ RouteModel
→ RouteSlice
→ TopologySlice
→ TopologyDifference
→ future Planner
```

There is no Topology/Slicer ownership overlap, Zone/service state overlap,
second Position authority, Connection/Route responsibility overlap,
RouteTarget/dynamic-endpoint ambiguity, Difference/full-Slice duplication or
Model/Planner/backend fallback path.
