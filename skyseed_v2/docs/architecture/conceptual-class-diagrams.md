# Conceptual Class Diagrams

## Status

These diagrams describe only implemented Skyseed v2 code. Simulator runtime
types are deliberately absent because Simulator is **NOT STARTED**.

## Emulator entities and seedemu integration

```mermaid
classDiagram
    class Emulator {
        <<upstream seedemu>>
        +render()
        +compile(compiler, output)
    }
    class SatelliteBase {
        <<seedemu integration>>
    }
    class SkyseedAutonomousSystem {
        <<entity owner + seedemu integration>>
        +create_shell(shell_id) Shell
        +create_ground_station(...) GroundStation
    }
    class Shell {
        <<Emulator entity>>
        +create_orbit(orbit_id, parameters) Orbit
    }
    class Orbit {
        <<Emulator entity>>
        +create_satellite(...) Satellite
        +create_satellites(...) tuple
    }
    class OrbitParameters {
        <<entity-bound value>>
    }
    class Satellite {
        <<Emulator entity + SEED Node>>
    }
    class GroundStationLocation {
        <<entity-bound value>>
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
    }
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }

    Emulator --> SatelliteBase : renders
    SatelliteBase *-- SkyseedAutonomousSystem : contains
    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "*" Satellite : owns
    Orbit *-- OrbitParameters : uses
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    GroundStation *-- GroundStationLocation : uses
    Emulator --> SkyseedDockerCompiler : compiles with
```

`Shell`, `Orbit`, `Satellite` and `GroundStation` each have a defining file
with the same primary name under `entities/`. Splitting those files does not
change ownership or create a parent above Shell.

## Emulator → application conversion → Simulator static input

```mermaid
flowchart LR
    SAT[Satellite] --> CS[application.create_satellite_simulation_input]
    GS[GroundStation] --> CG[application.create_ground_station_simulation_input]
    AS[Iterable of SkyseedAutonomousSystem] --> CA[application.create_simulator_static_input]
    CA --> CS
    CA --> CG
    CS --> SI[SatelliteSimulationInput]
    CG --> GI[GroundStationSimulationInput]
    SI --> AGG[SimulatorStaticInput]
    GI --> AGG
```

```mermaid
classDiagram
    class SimulationNodeId {
        <<Simulator-input value>>
        +asn
        +entity_kind
        +entity_id
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
        +get(node_id)
        +list_nodes() tuple
    }

    SatelliteSimulationInput "1" *-- "1" SimulationNodeId : identifies
    GroundStationSimulationInput "1" *-- "1" SimulationNodeId : identifies
    SimulatorStaticInput "1" *-- "*" SatelliteSimulationInput : contains
    SimulatorStaticInput "1" *-- "*" GroundStationSimulationInput : contains
```

The dependency direction is intentional: application conversion depends on
entities, seedemu integration and `simulator_input`; the input package depends
on none of those Emulator/SEED layers. The copied objects retain no entity,
Registry, container or Compose reference.

## Specification, serialization and application

```mermaid
classDiagram
    class StaticEmulatorScenarioSpecification {
        <<immutable input specification>>
    }
    class StaticEmulatorScenarioLoader {
        <<YAML serialization adapter>>
        +load(path) StaticEmulatorScenarioSpecification
    }
    class StaticEmulatorSpecificationApplier {
        <<application service>>
        +apply(specification) StaticEmulatorApplyResult
    }
    class StaticEmulatorApplyResult {
        <<operation result>>
        +emulator
        +skyseed_autonomous_systems
        +ordinary_autonomous_systems
        +simulator_input
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
    }
    class Emulator {
        <<upstream seedemu>>
    }

    StaticEmulatorScenarioLoader --> StaticEmulatorScenarioSpecification : creates
    StaticEmulatorSpecificationApplier --> StaticEmulatorScenarioSpecification : reads
    StaticEmulatorSpecificationApplier --> SimulatorStaticInput : creates through application conversion
    StaticEmulatorSpecificationApplier --> StaticEmulatorApplyResult : returns
    StaticEmulatorApplyResult *-- Emulator : exposes
    StaticEmulatorApplyResult *-- SimulatorStaticInput : exposes
```

The Loader is a file adapter. The Applier sequences existing public
construction calls. Neither type is an entity, ownership level or facade
required by handwritten examples.

## Materialization boundary

```mermaid
classDiagram
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }
    class ComposeProjectLauncher {
        <<materialization integration>>
    }
    class ComposeServiceExpectations {
        <<materialization value>>
    }
    class ComposeMaterializationResult {
        <<materialization operation result>>
    }

    ComposeProjectLauncher --> ComposeServiceExpectations : reads
    ComposeProjectLauncher --> ComposeMaterializationResult : returns
```

The launcher consumes an existing Compose artifact. It is outside entity
construction, Simulator static input and Compiler lowering.
