# Topology and Zone contracts

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Topology materialization, owner facade, Zone geometry/layout, membership, and
resolution.

## Owners and consumers

Simulator creates and owns Topology. Topology owns its new AS tree and optional
ZoneLayout. ZoneLayout owns exact Zone instances and one fixed
ZoneMembershipModel. Slicer Models and read-only Tools consume these objects.

## Contract

Topology materializes:

```text
SimulatorStaticInput
→ Topology
  → SkyseedAutonomousSystem
    → Shell → Orbit → Satellite
    → GroundStation
  → optional ZoneLayout → Zone
```

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
- Simulator nodes are not Emulator/SEED objects;
- Zone geometry supports wrapped longitude rectangles and poles;
- custom membership output must contain distinct exact ZoneLayout-owned
  objects;
- authoritative winner must be one physical match;
- overlap behavior is explicit in `ZoneLayoutDefinition`;
- `SphericalCap` is a query value, not a service assignment.

## Failure semantics

Invalid coordinate/value/type raises `TypeError` or `ValueError`; missing
owner lookup raises `KeyError`; no Zone match raises `LookupError`; missing
ZoneLayout access raises `RuntimeError`; foreign/duplicate custom Model output
raises `ValueError`.

## Prohibited behavior

No hidden global index, foreign Zone return, mutable membership replacement,
dynamic Position in Topology, or Zone service assignment in ZoneLayout.

## Extension points

`ZoneMembershipModel` is the approved stateless policy extension. It does not
own Zones and cannot change Topology ownership.

## Maintenance

Update topology/zone code, configuration definitions/YAML, focused tests,
Simulator README/diagram, Tools projections, public imports, and failure
reference.
