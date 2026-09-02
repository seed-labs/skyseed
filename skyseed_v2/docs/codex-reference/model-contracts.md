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
- `QualityFirstGroundStationAccessPlanningModel`;
- `HighestElevationZoneServicePlanningModel`;
- `AccumulatedDeadbandParameterPlanningModel` with
  `ParameterKindConfiguration`;
- `GatewayHopCountRoutePlanningModel`;
- `DefaultCoordinationPolicy`;
- `TimeHorizonPlanningWindowPolicy`.

Replacing only the ISL Model does not change Position projection, physical-link
geometry, GroundStation or Zone observations, the remaining planning roles,
Compiler, or Runner.

## Frozen policy separation

PhysicalWorld capability expresses the maximum simultaneous ISLs. The ISL
Model owns candidate scope, desired relationship mix, deterministic selection,
and acquisition lifecycle. Canonical S3 uses maximum 4, desired 2 same-plane
plus 2 cross-plane, K3 cross-plane candidate scope, and a 20-second acquisition
reference.

GroundStation access has no acquisition lifecycle. Zone service is planned
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
