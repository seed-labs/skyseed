# Replaceable Model contracts

Models are constructor-injected policies or algorithms. There is no registry,
plugin system, global lookup, or runtime fallback.

## Physical roles

`PositionModel.compute_positions(...)` is invoked only by PhysicalWorld for an
exact requested timestamp and complete expected node set.

`PhysicalLinkModel.compute_observation(physical_world, physical_frame, request)`
evaluates only the bounded requested relationship families and returns sparse
feasible physical candidates plus work counts. It performs no operational
selection or Route planning.

Built-in implementations:

- `CircularOrbitPositionModel`;
- `GeometricPhysicalLinkModel`.

## Planning roles

The public planning Protocols are:

| Role | Responsibility |
|---|---|
| `InterSatelliteConnectionPlanningModel` | create bounded candidate pairs; select retain-first relationship mix and lifecycle inputs |
| `GroundStationAccessPlanningModel` | choose operational GroundStation accesses independently of ISL acquisition |
| `ZoneServicePlanningModel` | choose one serving Satellite or explicit unserved state per Zone |
| `ParameterPlanningModel` | produce effective Connection parameters and bounded memory |
| `RoutePlanningModel` | produce complete reachable/unreachable outcomes and own any worker lifecycle |
| `CoordinationPolicy` | return semantic precedence only |
| `PlanningWindowPolicy` | define planning, publication, and required-prehistory horizons |

Reference implementations are:

- `RetainFirstInterSatelliteConnectionPlanningModel` with
  `RetainFirstInterSatelliteConfiguration`;
- `CoverageFirstGroundStationAccessPlanningModel`;
- `HighestElevationZoneServicePlanningModel`;
- `AccumulatedDeadbandParameterPlanningModel` with
  `ParameterKindConfiguration`;
- `GatewayHopCountRoutePlanningModel`;
- `DefaultCoordinationPolicy`;
- `FrameBatchPlanningWindowPolicy`.

Replacing only the ISL Model does not change Position projection, physical-link
geometry, GroundStation or Zone observations, the remaining planning roles,
Compiler, or Runner.

## Frozen policy separation

PhysicalWorld capability expresses the maximum simultaneous ISLs. The ISL
Model owns candidate scope, desired relationship mix, deterministic selection,
and acquisition lifecycle. Canonical S3 uses maximum 4, desired 2 same-plane
plus 2 cross-plane (historical S3 K3; current Starlink examples K1), and a 5-second preparation reference with 180-second future useful service.

GroundStation access has 5-second preparation and 60-second future-service
admission. Valid incumbent and pending assignments receive continuity priority
after station coverage, distinct satellite coverage and connection count. Zone service is planned
after operational accesses. Route planning receives only operational
Connections and explicit demands.

## Model output validation

Planner validates plausible boundary misuse: wrong identities, unrequested or
physically infeasible candidates, capability overflow, incomplete Zone or Route
coverage, invalid operational paths, nondeterministic duplicate identity,
malformed transition claims, and worker failure. Validation does not rerun an
independent competing planner.

Models must return immutable, canonically ordered semantic values. They must not
retain frames without a documented bound, mutate PhysicalWorld, publish Plans,
compile backend data, or write Tool schemas.

## Route workers

The built-in Route Model reuses a bounded eight-worker compute pool. It does not
claim Python threads provide CPU parallelism. It skips recomputation for
position-only and parameter-only changes, and `shutdown(wait=...)` is required
on success and failure.

## Physical prediction and derived data

`GeometricPhysicalLinkModel.predict_contacts(world, request, *, start_timestamp,
end_timestamp, observation_interval, inter_satellite_tail, ground_station_tail)`
prepares a compact physical contact index and returns work counts. The existing
physical Model owns prediction; Producer supplies bounds derived from Planner
Model dependencies. Later candidate observations carry immutable
`PhysicalContactWindow` values. An arbitrary PositionModel without a proved
continuous-motion bound supplies unknown future feasibility; observed samples
alone are not proof of contact continuity.

Circular elements and fixed station coordinates are reused inside the Position
Model. Batched exact geometry shares geometry-owned elevation arithmetic; safe
range/elevation/ellipsoid bounds prune expensive tests. Prediction builds no
future rich candidate graph, Planner state or routing work. Zone IDs, satellite
membership, unchanged demand tuples and unchanged routing graph/indexes are
reused in their existing Models. Route workers receive compact integer adjacency
and request tuples, preserve deterministic minimum-hop semantics, and use spawn
to avoid inheriting active serialization threads. The bounded worker limit is
unchanged; small jobs still execute locally.

## Current candidate query configuration

`RetainFirstInterSatelliteConfiguration.same_plane_neighbor_span` selects cyclic
mean-anomaly neighbors (`None` enumerates the full same-plane graph).
`cross_plane_neighbor_count` bounds current nearest eligible neighbors in each
queried plane; `maximum_cross_plane_separation` remains the cyclic plane scope.
The example uses span 1, count 4, K1. GS `initial_candidate_count=32` expands under
competition; `default_ground_station_limit=4` is fallback device capability and
`ground_station_policy_limit=None` leaves explicit 2/4/8 capabilities effective.
These fields do not change the physical distance/elevation envelope.
