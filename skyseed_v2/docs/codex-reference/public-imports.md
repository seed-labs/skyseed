# Public imports

Public domain contracts are imported from package facades. Private producer,
artifact, observation, and backend intent helpers are intentionally not exported.

## Shared

```python
from skyseed_v2.shared import NodeId
```

## Simulator facade

```python
from skyseed_v2.simulator import (
    Simulator,
    SimulationDefinition,
    SimulationTimeline,
    PhysicalWorld,
    PhysicalFrame,
    PositionModel,
    PhysicalLinkModel,
    PhysicalLinkRequest,
    PhysicalLinkObservation,
    InterSatelliteLinkCandidate,
    GroundStationAccessCandidate,
    ZoneServiceCandidate,
    Planner,
    PlannedOperationalState,
    PlanTransition,
    Plan,
    PlanningHistogramBin,
    PlanningCandidateHistogram,
    PlanningCandidateSummary,
    PlanningFrameEvidence,
    PublishedPlanningEvidence,
)
```

The facade also exports physical entities/capabilities, Zone values,
operational Connection/Zone/Route values, semantic event/precedence values,
Planner evidence, public planning Protocols/reference Models, replay helpers,
fingerprint helpers, and versioned planning codecs.

Common public planning imports include:

```python
from skyseed_v2.simulator import (
    InterSatelliteConnectionPlanningModel,
    GroundStationAccessPlanningModel,
    ZoneServicePlanningModel,
    ParameterPlanningModel,
    RoutePlanningModel,
    CoordinationPolicy,
    PlanningWindowPolicy,
    RetainFirstInterSatelliteConnectionPlanningModel,
    CoverageFirstGroundStationAccessPlanningModel,
    HighestElevationZoneServicePlanningModel,
    AccumulatedDeadbandParameterPlanningModel,
    GatewayHopCountRoutePlanningModel,
    DefaultCoordinationPolicy,
    FrameBatchPlanningWindowPolicy,
    apply_plan_transition,
    apply_plan,
)
```

The authoritative concrete public export inventory is
`skyseed_v2.simulator.__all__`. No alternate public operational state family is
provided.

Frequently reviewed public type inventory: `AccumulatedDeadbandParameterPlanningModel`,
`CircularOrbitPositionModel`, `CoordinationPolicy`, `DefaultCoordinationPolicy`,
`EarthFixedPosition`, `GatewayHopCountRoutePlanningModel`,
`GeometricPhysicalLinkModel`, `OperationalConnection`, `ParameterPlanningModel`,
`PhysicalFrame`, `PhysicalLinkObservation`, `PhysicalWorld`, `Plan`,
`PlanTransition`, `PlannedOperationalState`, `Planner`, `PlanningWindowPolicy`,
`PlanningHistogramBin`, `PlanningCandidateHistogram`,
`PlanningCandidateSummary`, `PositionModel`, `PublishedPlanningEvidence`,
`RoutePlanningModel`,
`SphericalCap`, and `FrameBatchPlanningWindowPolicy`.

## Built-in physical Models

The Simulator facade exports `CircularOrbitPositionModel` and
`GeometricPhysicalLinkModel`. Their implementation modules remain under
`skyseed_v2.simulator.models`.

## BPF backend

`skyseed_v2.backend.bpf` exports the Compiler values and `BpfRunner` lifecycle
facade:

```python
from skyseed_v2.backend.bpf import (
    BpfCompiledActivationBoundary,
    BpfCompiledRealization,
    BpfRunner,
    compile_bpf,
)
```

The Compiler call accepts exactly `Plan`, `PhysicalWorld`, and the previous
realization. Backend intention classes and producer/artifact helpers are private.
`RealizedRuntimeState` names Runner-owned live truth in architecture diagrams;
it is observed through the detached `BpfRunner.inspection_snapshot()` handoff,
not exported as a second public state object.

The Runner constructor is `BpfRunner(*, compose_project_name: str,
runtime_artifact: Path)`, where `Path` is `pathlib.Path` and names the sealed
finite artifact manifest. It replaces `future_realization_supplier` and removes
the `preparation_lookahead` and `preparation_boundary_depth` arguments.
`prepare()` fully prepares the sequence and physically activates B0 once;
`run()` executes B1 onward, or `activate_next()` manually advances the same
ordered prepared deltas. Automatic and manual execution are exclusive.
`cleanup()` owns teardown; `inspection_snapshot()` exposes detached live facts.
See the [BPF owner README](../../src/skyseed_v2/backend/bpf/README.md).

`SimulationTimeline` requires finite start/end datetimes and a positive
observation interval. Its public members are `start_time`, `end_time`,
`observation_interval`, and `iter_timestamps()`; `is_bounded` is removed.
See the [Timeline contract](timeline-plan-contracts.md).

## Tool imports

Tools are repository-local detached observers, imported from their own
top-level packages when an example needs to generate inspection artifacts.
They are not part of `skyseed_v2` public domain exports.

## Exact executable export mapping

This table is checked, in order and without aliases, against every public
module `__all__`. It is the maintenance inventory behind the examples above.

| Import owner | Exported names |
|---|---|
| `skyseed_v2` | `GroundStation`, `GroundStationLocation`, `Orbit`, `OrbitParameters`, `Satellite`, `Shell` |
| `skyseed_v2.backend.bpf` | `BpfCompiledActivationBoundary`, `BpfCompiledRealization`, `BpfRunner`, `compile_bpf` |
| `skyseed_v2.compose` | `ComposeBatchResult`, `ComposeLaunchStrategy`, `ComposeMaterializationError`, `ComposeMaterializationResult`, `ComposeProjectLauncher`, `ComposeServiceExpectations` |
| `skyseed_v2.configuration` | `emulator`, `simulator` |
| `skyseed_v2.configuration.emulator` | `EbgpPeeringConfig`, `EbgpRouterReference`, `GeneratedOrbitLayout`, `GROUND_STATIONS_SCHEMA_VERSION`, `GroundStationConfig`, `InOrbitPhaseDistribution`, `InternetExchangeAttachment`, `InternetExchangeConfig`, `LocalNetworkAttachment`, `OrbitConfig`, `OrdinaryAutonomousSystemConfig`, `NetworkConfig`, `RouterConfig`, `RightAscensionDistribution`, `SCENARIO_SCHEMA_VERSION`, `SHELLS_SCHEMA_VERSION`, `SatelliteConfig`, `ShellConfig`, `SkyseedAutonomousSystemConfig`, `StaticEmulatorConfig`, `StaticEmulatorConfigLoadError`, `load_static_emulator_config` |
| `skyseed_v2.configuration.simulator` | `LatitudeLongitudeRectangleDefinition`, `ZoneDefinition`, `ZoneLayoutDefinition`, `ZoneLayoutDefinitionLoadError`, `load_zone_layout_definition` |
| `skyseed_v2.emulator` | `create_static_emulator`, `entities`, `seedemu` |
| `skyseed_v2.emulator.entities` | `GroundStation`, `GroundStationLocation`, `Orbit`, `OrbitParameters`, `Satellite`, `Shell` |
| `skyseed_v2.emulator.seedemu` | `SkyseedAutonomousSystem`, `get_skyseed_autonomous_systems` |
| `skyseed_v2.emulator_to_simulator` | `create_ground_station_simulation_input`, `create_satellite_simulation_input`, `create_simulator_static_input` |
| `skyseed_v2.shared` | `NodeId` |
| `skyseed_v2.simulator` | `AccumulatedDeadbandParameterPlanningModel`, `CandidateDecision`, `CandidateDecisionKind`, `CircularOrbitPositionModel`, `ConnectionParameterObservation`, `CoordinationPolicy`, `DefaultCoordinationPolicy`, `DefaultZoneMembershipModel`, `EarthFixedPosition`, `EffectiveConnectionParameters`, `GatewayHopCountRoutePlanningModel`, `GeometricPhysicalLinkModel`, `GroundStation`, `GroundStationAccessCandidate`, `GroundStationAccessPlanningModel`, `GroundStationRouteDemandTarget`, `HighestElevationZoneServicePlanningModel`, `InterSatelliteConnectionPlanningModel`, `InterSatelliteLifecycleEvidence`, `GroundStationLifecycleEvidence`, `InterSatelliteLinkCapability`, `InterSatelliteLinkCandidate`, `InterSatelliteSelection`, `LatitudeLongitudeRectangle`, `OperationalConnection`, `OperationalConnectionId`, `OperationalConnectionKind`, `Orbit`, `ParameterKindConfiguration`, `ParameterPlanningMemory`, `ParameterPlanningModel`, `ParameterPlanningResult`, `PhysicalFrame`, `PhysicalLinkModel`, `PhysicalLinkObservation`, `PhysicalLinkRequest`, `PhysicalContactWindow`, `PhysicalLinkWorkCounts`, `PhysicalWorld`, `Plan`, `Planner`, `PlanningCandidateHistogram`, `PlanningCandidateSummary`, `PlanningFrameEvidence`, `PlanningFrontiers`, `PlanningHistogramBin`, `PlanningInspection`, `PlanningWindowPolicy`, `PlanTransition`, `PlannedOperationalState`, `PositionModel`, `PublishedPlanningEvidence`, `CoverageFirstGroundStationAccessPlanningModel`, `ReachableRouteOutcome`, `RetainFirstInterSatelliteConfiguration`, `RetainFirstInterSatelliteConnectionPlanningModel`, `RouteDemand`, `RouteDemandTarget`, `RouteOutcome`, `RouteOutcomeTransition`, `RoutePlanningModel`, `RoutePlanningResult`, `RouteUnreachableReason`, `Satellite`, `SemanticEventKind`, `SemanticEventPrecedence`, `SemanticEventReference`, `Shell`, `SimulationDefinition`, `SimulationTimeline`, `Simulator`, `SkyseedAutonomousSystem`, `SphericalCap`, `FrameBatchPlanningWindowPolicy`, `UnreachableRouteOutcome`, `Zone`, `ZoneLayout`, `ZoneMembershipModel`, `ZoneRouteDemandTarget`, `ZoneServiceAssignment`, `ZoneServiceCandidate`, `ZoneServicePlanningModel`, `ZoneServicePlanningResult`, `ZoneServiceTransition`, `apply_plan`, `apply_plan_transition`, `create_plan`, `plan_from_data`, `plan_to_data`, `plan_transition_from_data`, `plan_transition_fingerprint`, `plan_transition_to_data`, `planned_operational_state_from_data`, `planned_operational_state_fingerprint`, `planned_operational_state_to_data`, `published_planning_evidence_from_data`, `published_planning_evidence_to_data`, `semantic_event_references` |
| `skyseed_v2.simulator.geometry` | `EarthFixedPosition`, `ecef_distance_m`, `ecef_to_geodetic`, `elevation_deg`, `geodetic_to_ecef`, `has_earth_line_of_sight` |
| `skyseed_v2.simulator.input` | `GroundStationSimulationInput`, `SatelliteSimulationInput`, `SimulatorStaticInput` |
| `skyseed_v2.simulator.models` | `CircularOrbitPositionModel`, `GeometricPhysicalLinkModel` |
| `skyseed_v2.simulator.physical` | `DefaultZoneMembershipModel`, `GroundStation`, `GroundStationAccessCandidate`, `InterSatelliteLinkCapability`, `InterSatelliteLinkCandidate`, `LatitudeLongitudeRectangle`, `Orbit`, `PhysicalFrame`, `PhysicalLinkModel`, `PhysicalLinkObservation`, `PhysicalLinkRequest`, `PhysicalContactWindow`, `PhysicalLinkWorkCounts`, `PositionModel`, `Satellite`, `Shell`, `SkyseedAutonomousSystem`, `SphericalCap`, `PhysicalWorld`, `Zone`, `ZoneLayout`, `ZoneMembershipModel`, `ZoneServiceCandidate` |
| `skyseed_v2.simulator.planning` | `AccumulatedDeadbandParameterPlanningModel`, `CandidateDecision`, `CandidateDecisionKind`, `ConnectionParameterObservation`, `CoordinationPolicy`, `DefaultCoordinationPolicy`, `EffectiveConnectionParameters`, `GatewayHopCountRoutePlanningModel`, `GroundStationAccessPlanningModel`, `GroundStationRouteDemandTarget`, `HighestElevationZoneServicePlanningModel`, `InterSatelliteConnectionPlanningModel`, `InterSatelliteLifecycleEvidence`, `GroundStationLifecycleEvidence`, `InterSatelliteSelection`, `OperationalConnection`, `OperationalConnectionId`, `OperationalConnectionKind`, `ParameterKindConfiguration`, `ParameterPlanningMemory`, `ParameterPlanningModel`, `ParameterPlanningResult`, `Plan`, `Planner`, `PlanningCandidateHistogram`, `PlanningCandidateSummary`, `PlanningFrontiers`, `PlanningFrameEvidence`, `PlanningHistogramBin`, `PlanningInspection`, `PlanningWindowPolicy`, `PlanTransition`, `PlannedOperationalState`, `PublishedPlanningEvidence`, `CoverageFirstGroundStationAccessPlanningModel`, `ReachableRouteOutcome`, `RetainFirstInterSatelliteConfiguration`, `RetainFirstInterSatelliteConnectionPlanningModel`, `RouteDemand`, `RouteDemandTarget`, `RouteOutcome`, `RouteOutcomeTransition`, `RoutePlanningModel`, `RoutePlanningResult`, `RouteUnreachableReason`, `SemanticEventKind`, `SemanticEventPrecedence`, `SemanticEventReference`, `FrameBatchPlanningWindowPolicy`, `UnreachableRouteOutcome`, `ZoneRouteDemandTarget`, `ZoneServiceAssignment`, `ZoneServicePlanningModel`, `ZoneServicePlanningResult`, `ZoneServiceTransition`, `apply_plan`, `apply_plan_transition`, `create_plan`, `plan_from_data`, `plan_to_data`, `plan_transition_from_data`, `plan_transition_fingerprint`, `plan_transition_to_data`, `planned_operational_state_from_data`, `planned_operational_state_fingerprint`, `planned_operational_state_to_data`, `published_planning_evidence_from_data`, `published_planning_evidence_to_data`, `semantic_event_references` |

## Maintenance

When a public value or Model changes, update the owning package `__all__`, this
inventory, the object model, import-boundary checker, and direct-import tests in
the same change. Do not add aliases or forwarding modules for removed contracts.

The approved continuation change adds `Planner.flush()` and
`BpfRunner.append_batch(Path)`, and constructor settings `batch_lead_fraction` /
`batch_capacity_factor`. These methods belong to existing exported owners; no
root export or public type is added. Detailed member diagrams remain in their
owner package READMEs.
