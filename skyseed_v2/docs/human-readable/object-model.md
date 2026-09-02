# Object model

## Core ownership

Legend: `*--` is lifecycle composition and `..>` is creation. Each
`<<...>>` names the public role; `+` shows only 1–3 key members whose complete
inventory lives in the owner package README.

```mermaid
classDiagram
    class Simulator {
        <<composition root>>
        +create_physical_world()
        +create_planner()
        +get_planner()
    }
    class SimulationDefinition {
        <<immutable simulation definition>>
        +static_input
        +timeline
    }
    class SimulationTimeline {
        <<immutable formal timestamp authority>>
        +observation_interval
        +iter_timestamps()
    }
    class PhysicalWorld {
        <<immutable physical aggregate>>
        +frame_at()
        +semantic_fingerprint()
    }
    class PhysicalFrame {
        <<immutable physical frame>>
        +timestamp
        +positions
        +get_position()
    }
    class Planner {
        <<stateful semantic Planner owner>>
        +observe()
        +finish()
        +shutdown()
    }

    SimulationDefinition "1" *-- "1" SimulationTimeline
    Simulator "1" *-- "1" SimulationDefinition
    Simulator "1" *-- "1" PhysicalWorld
    Simulator "1" *-- "1" Planner
    PhysicalWorld ..> PhysicalFrame : creates
```

## Physical and planned values

Legend: `*--` is immutable value composition. Physical candidates and planned
operational values remain separate authority families.

```mermaid
classDiagram
    class PhysicalLinkRequest
    class PhysicalLinkObservation {
        <<immutable physical observation>>
        +timestamp
        +inter_satellite_candidates
        +work_counts
    }
    class InterSatelliteLinkCandidate
    class GroundStationAccessCandidate
    class ZoneServiceCandidate
    class PlannedOperationalState {
        <<immutable complete operational state>>
        +timestamp
        +operational_connections
        +route_outcomes
    }
    class OperationalConnection
    class ZoneServiceAssignment
    class ReachableRouteOutcome
    class UnreachableRouteOutcome

    PhysicalLinkObservation "1" *-- "0..*" InterSatelliteLinkCandidate
    PhysicalLinkObservation "1" *-- "0..*" GroundStationAccessCandidate
    PhysicalLinkObservation "1" *-- "0..*" ZoneServiceCandidate
    PlannedOperationalState "1" *-- "0..*" OperationalConnection
    PlannedOperationalState "1" *-- "0..*" ZoneServiceAssignment
    PlannedOperationalState "1" *-- "0..*" ReachableRouteOutcome
    PlannedOperationalState "1" *-- "0..*" UnreachableRouteOutcome
```

Candidate values are physical facts. The state values are operational decisions.
Neither family owns the other.

## Plan and realization

Legend: `*--` is immutable composition, `-->` is an exact semantic reference,
and `..>` is the intended-to-live handoff. RealizedRuntimeState is a
Runner-owned concept, not a separate public import.

```mermaid
classDiagram
    class Plan {
        <<immutable replay-complete plan>>
        +plan_id
        +transitions
        +semantic_event_precedences
    }
    class PlanTransition {
        <<immutable formal semantic transition>>
        +timestamp
        +established_connections
        +route_outcome_transitions
    }
    class SemanticEventPrecedence {
        <<immutable semantic ordering edge>>
        +predecessor
        +successor
    }
    class BpfCompiledRealization {
        <<immutable Compiler handoff>>
        +source_plan_id
        +compiled_activation_boundaries
        +semantic_precedence_folding
    }
    class BpfCompiledActivationBoundary {
        <<immutable activation schedule value>>
        +timestamp
        +transition_fingerprint
    }
    class BpfRunner {
        <<stateful live lifecycle owner>>
        +prepare()
        +run()
        +inspection_snapshot()
    }
    class RealizedRuntimeState

    Plan "1" *-- "1..*" PlanTransition
    Plan "1" *-- "0..*" SemanticEventPrecedence
    BpfCompiledRealization "1" *-- "1..*" BpfCompiledActivationBoundary
    BpfCompiledRealization "1" *-- "1" PlannedOperationalState
    BpfCompiledActivationBoundary ..> PlanTransition : fingerprint provenance
    BpfCompiledRealization ..> BpfRunner : intended work
    BpfRunner ..> RealizedRuntimeState : owns and observes live truth
```

Every formal transition creates a compiled activation boundary, including an
empty transition. A realization owns intended state; Runner owns realized
state.

## Model realization

Legend: `..|>` is implementation of exactly one replaceable public Protocol
role; it does not transfer Planner or physical aggregate ownership.

```mermaid
classDiagram
    class PositionModel
    class PhysicalLinkModel
    class InterSatelliteConnectionPlanningModel
    class GroundStationAccessPlanningModel
    class ZoneServicePlanningModel
    class ParameterPlanningModel
    class RoutePlanningModel
    class CoordinationPolicy
    class PlanningWindowPolicy

    class CircularOrbitPositionModel
    class GeometricPhysicalLinkModel
    class RetainFirstInterSatelliteConnectionPlanningModel
    class QualityFirstGroundStationAccessPlanningModel
    class HighestElevationZoneServicePlanningModel
    class AccumulatedDeadbandParameterPlanningModel
    class GatewayHopCountRoutePlanningModel
    class DefaultCoordinationPolicy
    class TimeHorizonPlanningWindowPolicy

    CircularOrbitPositionModel ..|> PositionModel
    GeometricPhysicalLinkModel ..|> PhysicalLinkModel
    RetainFirstInterSatelliteConnectionPlanningModel ..|> InterSatelliteConnectionPlanningModel
    QualityFirstGroundStationAccessPlanningModel ..|> GroundStationAccessPlanningModel
    HighestElevationZoneServicePlanningModel ..|> ZoneServicePlanningModel
    AccumulatedDeadbandParameterPlanningModel ..|> ParameterPlanningModel
    GatewayHopCountRoutePlanningModel ..|> RoutePlanningModel
    DefaultCoordinationPolicy ..|> CoordinationPolicy
    TimeHorizonPlanningWindowPolicy ..|> PlanningWindowPolicy
```

## Exact high-level public mapping

The high-level class blocks above map to these executable public contracts.
Their 1–3 displayed members must be a subset of the complete owner README
inventory.

| Diagram class | Public contract |
|---|---|
| `Simulator` | `skyseed_v2.simulator.Simulator` |
| `SimulationDefinition` | `skyseed_v2.simulator.SimulationDefinition` |
| `SimulationTimeline` | `skyseed_v2.simulator.SimulationTimeline` |
| `PhysicalWorld` | `skyseed_v2.simulator.PhysicalWorld` |
| `PhysicalFrame` | `skyseed_v2.simulator.PhysicalFrame` |
| `Planner` | `skyseed_v2.simulator.Planner` |
| `PhysicalLinkObservation` | `skyseed_v2.simulator.PhysicalLinkObservation` |
| `PlannedOperationalState` | `skyseed_v2.simulator.PlannedOperationalState` |
| `Plan` | `skyseed_v2.simulator.Plan` |
| `PlanTransition` | `skyseed_v2.simulator.PlanTransition` |
| `SemanticEventPrecedence` | `skyseed_v2.simulator.SemanticEventPrecedence` |
| `BpfCompiledRealization` | `skyseed_v2.backend.bpf.BpfCompiledRealization` |
| `BpfCompiledActivationBoundary` | `skyseed_v2.backend.bpf.BpfCompiledActivationBoundary` |
| `BpfRunner` | `skyseed_v2.backend.bpf.BpfRunner` |

Public import paths are listed in
[`../codex-reference/public-imports.md`](../codex-reference/public-imports.md).
