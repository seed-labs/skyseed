# Current Architecture Diagrams

All diagrams on this page reflect implemented code. Simulator remains
**NOT STARTED**; the right-hand box is only a consumer boundary.

## 1. Package dependency direction

```mermaid
flowchart LR
    ENT[entities]
    SEED[seedemu_integration]
    INPUT[simulator_input]
    SPEC[specifications]
    SER[serialization]
    APP[application]
    MAT[materialization]

    SEED --> ENT
    INPUT --> ENT
    INPUT --> SEED
    SER --> SPEC
    APP --> SPEC
    APP --> ENT
    APP --> SEED
    APP --> INPUT
```

There is no arrow from `entities` to `simulator_input`. `materialization` is
independent of entities, static input and seedemu Compiler implementation.

## 2. Entity ownership

```mermaid
classDiagram
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
    }
    class Satellite {
        <<Emulator entity + SEED Node>>
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
    }

    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "*" Satellite : owns
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
```

## 3. Isolated example call chain

```mermaid
sequenceDiagram
    participant U as caller
    participant B as SatelliteBase
    participant A as SkyseedAutonomousSystem
    participant S as Shell
    participant O as Orbit
    participant E as Emulator
    participant C as SkyseedDockerCompiler

    U->>B: create Base layer
    U->>A: construct AS
    U->>B: setAutonomousSystem(A)
    U->>A: create_shell(...)
    A-->>U: S
    U->>S: create_orbit(...)
    S-->>U: O
    U->>O: create_satellite(...)
    U->>E: addLayer(B), render()
    U->>E: compile(C, output)
```

## 4. Complete example call chain

```mermaid
flowchart TD
    A[SkyseedAutonomousSystem]
    A --> S[Shell → Orbit → Satellites]
    A --> G[GroundStation]
    B[SatelliteBase] --> IX[Internet exchange]
    B --> R[ordinary AS → Network → Router]
    G --> IX
    R --> IX
    IX --> P[explicit eBGP]
    A --> I[SimulatorStaticInput.from_autonomous_systems]
    B --> C[Emulator.render → SkyseedDockerCompiler]
```

## 5. File-driven large call chain

```mermaid
flowchart LR
    YAML[scenario.yaml + ground_stations.yaml]
    YAML --> L[StaticEmulatorScenarioLoader.load]
    L --> SPEC[immutable specifications]
    SPEC --> APP[StaticEmulatorSpecificationApplier.apply]
    APP --> CREATE[public create/join methods]
    CREATE --> INPUT[SimulatorStaticInput.from_autonomous_systems]
    APP --> COMP[render + SkyseedDockerCompiler]
```

## 6. Static Simulator-input boundary

```mermaid
classDiagram
    class Satellite {
        <<Emulator entity + SEED Node>>
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
        +from_satellite(satellite)$
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
        +from_ground_station(ground_station)$
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
        +from_autonomous_systems(autonomous_systems)$
    }
    class SimulationNodeId {
        <<Simulator-input value>>
    }

    SatelliteSimulationInput --> Satellite : copies
    GroundStationSimulationInput --> GroundStation : copies
    SatelliteSimulationInput *-- SimulationNodeId : identifies
    GroundStationSimulationInput *-- SimulationNodeId : identifies
    SimulatorStaticInput *-- SatelliteSimulationInput : contains
    SimulatorStaticInput *-- GroundStationSimulationInput : contains
```

## 7. Compiler artifact and materialization

```mermaid
flowchart LR
    E[rendered Emulator] --> C[SkyseedDockerCompiler]
    C --> D[docker-compose.yml + build contexts]
    D --> L[ComposeProjectLauncher]
    L --> P[docker compose v2 project]
```

`ComposeProjectLauncher` never reconstructs entities, reads Simulator input or
recompiles the Emulator.
