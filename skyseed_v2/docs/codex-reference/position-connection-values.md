# Position and Connection values

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Orbit time data, authoritative Position/geometry capability, `PhysicalFrame`,
Connection identity/parameters, and `ConnectionSlice`.

## Owners and consumers

Orbit owns its normalized epoch. Topology owns/calls PositionModel and creates
one timestamp's physical-only `PhysicalFrame`. ConnectionModel creates the
complete effective `ConnectionSlice` inside the temporary legacy Slicer;
legacy `TopologySlice` temporarily retains both Positions and operational
results. Route and read-only Tools consume formal values without
recomputation.

## Contract

`OrbitParameters.epoch_utc` is timezone-aware, UTC-normalized static Orbit
definition data. It is not Timeline start or Slicer state.
`Satellite.mean_anomaly_deg` is defined at its parent Orbit epoch. Emulator
Shell configuration supplies one common explicit epoch and construction copies
it through `SimulatorStaticInput` into each independently owned Topology Orbit.

GroundStation elevation is finite WGS84 ellipsoidal height in metres. A
configuration/construction default may resolve a missing input to `0.0 m`;
formal Emulator and Simulator values are never `None`.

`EarthFixedPosition(x_m, y_m, z_m)` is the only authoritative Position. It is
frozen, slotted, finite WGS84 Earth-fixed geocentric Cartesian data in metres,
equivalent to EPSG:4978 semantics. Latitude, longitude, and ellipsoidal height
are deterministic derived views. `simulator/geometry/` is the one capability
for WGS84 geodetic↔ECEF conversion, ECEF distance, Earth LOS/obstruction, and
elevation. Models and Tools reuse it; Tools do not propagate nodes or maintain
a second coordinate implementation.

`PhysicalFrame` is frozen, slotted, and deeply immutable. Its aware
UTC-normalized timestamp is its identity, and its defensively snapshotted
mapping contains exactly one `EarthFixedPosition` for every Topology Satellite
and GroundStation. It has no integer frame/Slice ID, Connections, Routes, Zone
service assignment, effective parameter, forwarding, Difference, planning, or
runtime state. `Topology.frame_at(timestamp)` is projection only: it does not
advance, retain, or expose a current frame.

`ConnectionKind` contains only `INTER_SATELLITE` and
`GROUND_STATION_ACCESS`. Same-plane/cross-plane are private candidate sources,
not kinds. Every deterministic ordering uses
`(asn, entity_kind, entity_id)`; `NodeId` has no natural ordering.

`ConnectionId(kind, endpoint_a, endpoint_b)` normalizes two distinct endpoints
by that structural key, contains no timestamp/resource ID, and is stable
across Slices. `DirectionalConnectionParameters` contains positive
`bandwidth_bps`, non-negative `delay_us`, and `packet_loss_ppm` in
`0..1_000_000`. `Connection` owns one parameter value for each direction; the
directions may differ.

`ConnectionSlice` is frozen, slotted, and deeply immutable. It owns canonical
ordered effective Connections plus a stable ZoneId-ordered partial mapping
from Zone to service-Satellite `NodeId`. A Zone has at most one service
Satellite; one Satellite may serve multiple Zones; absence means legally
unserved. It stores no candidates, visibility, coverage, geometry evidence,
distance, elevation, scores, or independent Slice ID.

## Invariants

- each PhysicalFrame Position mapping covers every and only Topology
  Satellites and GroundStations;
- Position keys are exact `NodeId` values and values are
  `EarthFixedPosition`;
- Connection endpoints belong to the exact Topology and are unique per
  kind/pair;
- INTER_SATELLITE joins two Satellites; GROUND_STATION_ACCESS joins one
  Satellite and one GroundStation;
- Zone assignments name an owned Zone and an exact Topology Satellite;
- published tuple/mapping order is deterministic and defensively snapshotted.

## Failure semantics

Naive/non-finite time or coordinates, incomplete/foreign Position, invalid
parameters, duplicate ConnectionId, wrong endpoint kinds, foreign endpoints,
unknown Zone, or non-Satellite service assignment fail with `TypeError`,
`ValueError`, or the owning lookup's `KeyError`. Obstruction, excessive
distance, no GroundStation access, and an unserved Zone are legal complete
states.

## Prohibited behavior

No second producer or projection path for Position, operational state in
PhysicalFrame, Tool-side propagation/conversion, natural `NodeId` ordering,
insertion/YAML/object-identity tie-break, candidate evidence in a formal Slice,
or backend/interface/resource state. The temporary Positions copy in legacy
TopologySlice is migration debt, not a second authority.

## Extension points

Alternative Position and Connection Models may use different private
algorithms but must return the same complete formal values. PositionModel is a
Topology extension with no slicing alias; ConnectionModel remains a temporary
legacy Slicer extension.

## Maintenance

Update topology/geometry, temporary slicing values, model validation, focused
main-chain tests, Tools projections, public imports, and this reference
together.
