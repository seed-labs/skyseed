# Public imports

## Authority

Normative mapping under `../accepted-design.md`. `__all__` in code and
`tests/test_package_import.py` are executable contract truth. Implementation
state is recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

This file records intentionally exported imports. Explicit private-module
imports used only by repository tests, examples, or Tools are not promoted to
long-term public promises.

## Contract

| Import owner | Exported names |
|---|---|
| `skyseed_v2` | `GroundStation`, `GroundStationLocation`, `Orbit`, `OrbitParameters`, `Satellite`, `Shell` |
| `skyseed_v2.backend.bpf` | `BpfCompiledRealization`, `BpfRunner`, `compile_bpf` |
| `skyseed_v2.configuration` | `emulator`, `simulator` |
| `skyseed_v2.configuration.emulator` | `EbgpPeeringConfig`, `EbgpRouterReference`, `GeneratedOrbitLayout`, `GROUND_STATIONS_SCHEMA_VERSION`, `GroundStationConfig`, `InOrbitPhaseDistribution`, `InternetExchangeAttachment`, `InternetExchangeConfig`, `LocalNetworkAttachment`, `OrbitConfig`, `OrdinaryAutonomousSystemConfig`, `NetworkConfig`, `RouterConfig`, `RightAscensionDistribution`, `SCENARIO_SCHEMA_VERSION`, `SatelliteConfig`, `ShellConfig`, `SkyseedAutonomousSystemConfig`, `StaticEmulatorConfig`, `StaticEmulatorConfigLoadError`, `load_static_emulator_config` |
| `skyseed_v2.configuration.simulator` | `LatitudeLongitudeRectangleDefinition`, `ZoneDefinition`, `ZoneLayoutDefinition`, `ZoneLayoutDefinitionLoadError`, `load_zone_layout_definition` |
| `skyseed_v2.emulator` | `create_static_emulator`, `entities`, `seedemu` |
| `skyseed_v2.emulator.entities` | `GroundStation`, `GroundStationLocation`, `Orbit`, `OrbitParameters`, `Satellite`, `Shell` |
| `skyseed_v2.emulator.seedemu` | `SkyseedAutonomousSystem`, `get_skyseed_autonomous_systems` |
| `skyseed_v2.emulator_to_simulator` | `create_ground_station_simulation_input`, `create_satellite_simulation_input`, `create_simulator_static_input` |
| `skyseed_v2.shared` | `NodeId` |
| `skyseed_v2.compose` | `ComposeBatchResult`, `ComposeLaunchStrategy`, `ComposeMaterializationError`, `ComposeMaterializationResult`, `ComposeProjectLauncher`, `ComposeServiceExpectations` |
| `skyseed_v2.simulator` | `AccumulatedDeadbandParameterPlanningModel`, `AllAvailablePlanningWindowPolicy`, `CircularOrbitPositionModel`, `Connection`, `ConnectionDirection`, `ConnectionId`, `ConnectionKind`, `ConnectionModel`, `ConnectionPlanningModel`, `ConnectionSlice`, `ConnectionTransition`, `ConnectionTransitionOperation`, `CoordinationPolicy`, `DefaultCoordinationPolicy`, `DependencyAwareForwardingPlanningModel`, `DirectionalConnectionParameters`, `DirectionalConnectionParametersPatch`, `DifferenceOperation`, `EarthFixedPosition`, `GeometricConnectionModel`, `GroundStation`, `GroundStationRouteTarget`, `ForwardingPlanningModel`, `ForwardingTransition`, `ForwardingTransitionPrecedence`, `HopCountRouteModel`, `DefaultZoneMembershipModel`, `LatitudeLongitudeRectangle`, `Orbit`, `MidpointConnectionPlanningModel`, `ParameterPlanningModel`, `ParameterTransition`, `Plan`, `Planner`, `PlanningDecision`, `PlanningWindowPolicy`, `PositionModel`, `RouteKey`, `RouteModel`, `RoutePatch`, `RouteSlice`, `RouteTarget`, `Satellite`, `Shell`, `SimulationDefinition`, `SimulationTimeline`, `Simulator`, `Slicer`, `SampleBoundaryConnectionPlanningModel`, `SphericalCap`, `SkyseedAutonomousSystem`, `Topology`, `TopologyDifference`, `TopologySlice`, `Zone`, `ZoneLayout`, `ZoneMembershipModel`, `ZoneRouteTarget`, `ZoneServiceAssignmentPatch` |
| `skyseed_v2.simulator.input` | `GroundStationSimulationInput`, `SatelliteSimulationInput`, `SimulatorStaticInput` |
| `skyseed_v2.simulator.geometry` | `EarthFixedPosition`, `ecef_distance_m`, `ecef_to_geodetic`, `elevation_deg`, `geodetic_to_ecef`, `has_earth_line_of_sight` |
| `skyseed_v2.simulator.models` | `CircularOrbitPositionModel`, `GeometricConnectionModel`, `HopCountRouteModel` |
| `skyseed_v2.simulator.planning` | `AccumulatedDeadbandParameterPlanningModel`, `AllAvailablePlanningWindowPolicy`, `ConnectionPlanningModel`, `ConnectionTransition`, `ConnectionTransitionOperation`, `CoordinationPolicy`, `DefaultCoordinationPolicy`, `DependencyAwareForwardingPlanningModel`, `ForwardingPlanningModel`, `ForwardingTransition`, `ForwardingTransitionPrecedence`, `MidpointConnectionPlanningModel`, `ParameterPlanningModel`, `ParameterTransition`, `Plan`, `Planner`, `PlanningDecision`, `PlanningWindowPolicy`, `SampleBoundaryConnectionPlanningModel` |
| `skyseed_v2.simulator.topology` | `DefaultZoneMembershipModel`, `GroundStation`, `LatitudeLongitudeRectangle`, `Orbit`, `Satellite`, `Shell`, `SkyseedAutonomousSystem`, `SphericalCap`, `Topology`, `Zone`, `ZoneLayout`, `ZoneMembershipModel` |
| `skyseed_v2.simulator.slicing` | `Connection`, `ConnectionDirection`, `ConnectionId`, `ConnectionKind`, `ConnectionModel`, `ConnectionSlice`, `DifferenceOperation`, `DirectionalConnectionParameters`, `DirectionalConnectionParametersPatch`, `GroundStationRouteTarget`, `PositionModel`, `RouteKey`, `RouteModel`, `RoutePatch`, `RouteSlice`, `RouteTarget`, `Slicer`, `TopologyDifference`, `TopologySlice`, `ZoneRouteTarget`, `ZoneServiceAssignmentPatch` |

`skyseed_v2.emulator.create_static_emulator` is the user-facing facade; the
function object remains defined and owned by
`skyseed_v2.emulator.construction`.

`skyseed_v2.simulator` re-exports the public composition, topology, geometry,
slicing, planning, and built-in Model names from the five owning Simulator
packages. It
does not export static-input DTOs or `NodeId`.

`skyseed_v2.backend.bpf` is the only current backend public facade. The parent
`backend` package does not define a generic framework or root-level facade.
`BpfRunner.inspection_snapshot()` is its single read-only inspection handoff;
its event payload is a bounded recent window, while standard generated
execution preserves the complete event history in durable JSONL for the
repository-local Tool. It returns detached plain
summary/frozen-binding scalars/events without adding
an exported result type, event iterator, subscription, or metrics API.

## Invariants

- one public path per moved production symbol;
- retired modules do not exist and have no `find_spec`;
- root exports remain the six high-frequency Emulator entities/values only;
- Tools are repository-local and never become `skyseed_v2` exports.

## Failure semantics

Import tests fail on missing/extra `__all__`, retired aliases, or a second
module path.

## Prohibited behavior

No forwarding module, `sys.modules` alias, lazy alias for a retired path, or
test-only path exemption.

## Extension points

Changing any package export or public import is a public-boundary decision and
requires explicit approval.

## Maintenance

Update package `__init__.py`, this table, import tests, wheel checks, examples,
and `maintenance-map.md` together.
