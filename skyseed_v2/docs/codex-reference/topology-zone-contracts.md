# Topology and Zone contracts

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Topology materialization, timestamp-addressed physical projection,
PhysicalFrame, owner facade, Zone geometry/layout, membership, and resolution.

## Owners and consumers

Simulator creates and owns Topology. Topology owns its exact PositionModel,
new AS tree, and optional ZoneLayout. ZoneLayout owns exact Zone instances and
one fixed ZoneMembershipModel. Physical readers and read-only Tools consume
Topology/frames; the temporary legacy Slicer consumes each frame and passes
its Positions to the operational Models.

## Contract

Topology materializes:

```text
SimulatorStaticInput + PositionModel
→ Topology
  → SkyseedAutonomousSystem
    → Shell → Orbit → Satellite
    → GroundStation
  → optional ZoneLayout → Zone

requested aware timestamp
→ Topology.frame_at(timestamp)
→ PhysicalFrame(timestamp, exact immutable node Positions)
```

`Simulator.create_topology(position_model=...)` binds one explicit
PositionModel to the Topology physical lifecycle. Topology is its sole owner
and caller. `Topology.frame_at(timestamp) -> PhysicalFrame` normalizes one
aware timestamp to UTC, calls that Model once, validates exact Satellite and
GroundStation coverage and value types, and returns a new immutable frame.
Repeated projection of the same Topology/timestamp is deterministic.

When execution observation is enabled, the Topology component reports
`position-compute`, `position-validation`, and
`physical-frame-publication` one-shot stages plus the
`physical-projection` rollup. Observation records do not add progression or
retained frame state.

PhysicalFrame identity is its timestamp. It has no `slice_id` or `frame_id`,
and Topology does not expose `next_frame`, `advance`, `current_frame`, or any
equivalent progression state. A frame contains only timestamp plus
`NodeId → EarthFixedPosition`; it contains no Connection, Route, Zone-serving,
effective parameter, forwarding, Planner, Difference, Plan, or realized
runtime state. Deterministic geometry may be derived from Topology plus the
frame without eagerly materializing pairwise physical facts.

Facade lookups delegate through real owners and return the same object
identity. `find_zones` returns physical matches; `resolve_zone` returns one
authoritative winner. The default winner order is highest priority, shortest
central angle to representative center (within tolerance), then `zone_id`.

`SphericalCap` is a frozen/slotted Earth-surface query value with an owned
center and angular radius in `[0, 180]`. `ZoneLayout.find_intersecting_zones`
returns every physically intersecting owned Zone in deterministic order,
including date-line and pole cases. `Topology.find_intersecting_zones`
delegates unchanged to its exact ZoneLayout. This is a coarse query only:
neither owner selects service Satellites, stores candidates, or applies
elevation.

## Invariants

- immutable tuples/mappings and unique stable IDs;
- each PositionModel instance belongs to one exact Topology;
- each PhysicalFrame has an aware UTC timestamp and exactly the Topology node
  set;
- physical projection has no mutable current-frame or Timeline cursor;
- Simulator nodes are not Emulator/SEED objects;
- Zone geometry supports wrapped longitude rectangles and poles;
- custom membership output must contain distinct exact ZoneLayout-owned
  objects;
- authoritative winner must be one physical match;
- overlap behavior is explicit in `ZoneLayoutDefinition`;
- `SphericalCap` is a query value, not a service assignment.

## Failure semantics

Invalid coordinate/value/type or PositionModel output raises `TypeError` or
`ValueError`; PositionModel reuse by another live Topology raises `ValueError`;
missing owner lookup raises `KeyError`; no Zone match raises `LookupError`;
missing ZoneLayout access raises `RuntimeError`; foreign/duplicate custom
membership output raises `ValueError`.

## Prohibited behavior

No hidden global index, foreign Zone return, mutable membership replacement,
Topology-owned time progression, operational state in PhysicalFrame, eager
pairwise physical-state aggregate, or Zone service assignment in ZoneLayout.

## Extension points

`PositionModel` is the approved Topology-side physical projection extension.
`ZoneMembershipModel` is the approved stateless membership-policy extension.
Neither can change Topology ownership or introduce operational decisions.

## Maintenance

Update topology/zone code, configuration definitions/YAML, focused tests,
Simulator README/diagram, Tools projections, public imports, and failure
reference.
