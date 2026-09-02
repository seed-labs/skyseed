# PhysicalWorld, PhysicalFrame, and Zone contracts

`PhysicalWorld` is the authoritative physical aggregate. It is created once by
`Simulator.create_physical_world(...)` from immutable Simulator input, a
PositionModel, and optional Zone declarations.

## Aggregate and entities

PhysicalWorld owns immutable Autonomous System, shell, orbit, Satellite,
GroundStation, capability, and Zone-layout values. Identity is structural and
uses `NodeId`. Lookups return the exact immutable member or fail on unknown or
wrong-kind identity.

`semantic_fingerprint()` is a cached, versioned, deterministic hash of static
physical semantic content and active Model configuration. Plans and compiled
realizations carry it as provenance.

## Requested-time projection

`frame_at(timestamp)` normalizes an aware timestamp, invokes the exact owned
PositionModel once, validates complete unique node coverage, and returns an
immutable `PhysicalFrame`.

PhysicalFrame contains exactly:

- `timestamp`;
- complete canonically ordered `(NodeId, EarthFixedPosition)` values.

It owns no time cursor, operational Connection, Zone-service decision, Route,
Plan, backend data, or runtime state.

## Physical-link boundary

`PhysicalLinkRequest` is created by Planner policy and bounds the ISL pairs and
GroundStation/Zone relationship families that must be evaluated for one frame.

`PhysicalLinkModel.compute_observation(...)` returns one matching immutable
`PhysicalLinkObservation` with sparse feasible candidate values and work counts.
The reference geometric implementation applies range, Earth-clear LOS,
distance, propagation delay, and the single geometry-owned elevation formula.

Failed pairs are counted but not serialized. The output does no operational
selection, acquisition, serving assignment, parameter planning, or routing.

## Capability and policy

`InterSatelliteLinkCapability.maximum_simultaneous_links` belongs to each
Satellite in PhysicalWorld. Canonical S3 uses 4 as an explicit scenario
assumption. Candidate scope and desired 2+2 relation mix belong to the injected
ISL planning Model.

## Zones

`Zone` contains an identifier, immutable spherical geometry, and optional
metadata. `ZoneLayout` contains canonically ordered Zones, overlap policy, and
one membership Model. Rectangle and spherical-cap geometries handle longitude
normalization and antimeridian behavior deterministically.

Static membership answers geography only. Planner owns which Satellite, if any,
serves each Zone at an operational timestamp.

## Failure behavior

Invalid input identity, duplicate membership, incomplete positions, non-finite
coordinates, invalid capability, wrong frame/world/request provenance, or
malformed Model output fails before a value is published. PhysicalWorld and
published frames remain immutable.
