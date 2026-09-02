# Physical and operational values

Skyseed keeps physical candidates and operational decisions in different
immutable value families.

## Physical values

`EarthFixedPosition` is the authoritative immutable ECEF/geodetic position.
`PhysicalFrame` contains exactly a normalized timestamp and complete immutable
node positions.

`PhysicalLinkObservation` contains sparse feasible candidate values and work
counts for an exact frame/request:

- `InterSatelliteLinkCandidate`;
- `GroundStationAccessCandidate`;
- `ZoneServiceCandidate`;
- `PhysicalLinkWorkCounts`.

Candidate facts may include distance, elevation, propagation delay, and
quality. They are not operational selections.

## Planned operational values

The public planning package exports immutable values for:

- `OperationalConnectionKind`, `OperationalConnectionId`,
  `EffectiveConnectionParameters`, and `OperationalConnection`;
- `ZoneServiceAssignment`, including explicit unserved state;
- `GroundStationRouteDemandTarget`, `ZoneRouteDemandTarget`, and `RouteDemand`;
- `ReachableRouteOutcome`, `UnreachableRouteOutcome`, and
  `RouteUnreachableReason`;
- `PlannedOperationalState`.

Operational identity and ordering are canonical. Reachable Route paths contain
only operational Connection edges. Unreachable outcomes retain explicit
reasons rather than relying on missing forwarding state.

`PlannedOperationalState` contains one timestamp, the complete operational
Connection set with effective parameters, the complete Zone-service assignment
set, and complete Route outcomes. It intentionally excludes physical frames,
candidates, selection evidence, acquisition memory, Model caches, workers,
publication buffers, and Tool records.

## Transition values

`PlanTransition` contains established complete Connections, withdrawn
Connection identities, parameter-updated complete Connections, Zone service
changes, and Route outcome changes. An empty transition represents a no-op
formal boundary.

`ZoneServiceTransition` carries previous and current serving Satellite or
`None`. `RouteOutcomeTransition` carries previous and current explicit outcome
or `None`. These payloads are replay complete and independent of Planner
memory.

All collections are normalized to deterministic order, reject duplicate
semantic identity, and are copy-stable. Semantic fingerprints use versioned
canonical content rather than Python object identity or randomized hashes.
