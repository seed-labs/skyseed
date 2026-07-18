# Current Architecture Diagrams

All diagrams on this page reflect implemented code. Simulator remains
**NOT STARTED**; the right-hand box is only a consumer boundary.

## 1. Package dependency direction

```mermaid
flowchart LR
    ENT[entities]
    SEED[seedemu_integration]
    INPUT[simulator_input]
    CONFIG[configuration]
    CREATE[static_emulator]
    GENERATE[simulator_input_generation]
    MAT[materialization]
    NODEID[node_id]
    ID[identifier]

    SEED --> ENT
    ENT --> ID
    SEED --> ID
    NODEID --> ID
    INPUT --> NODEID
    CONFIG --> ID
    CREATE --> CONFIG
    CREATE --> ENT
    CREATE --> SEED
    GENERATE --> ENT
    GENERATE --> SEED
    GENERATE --> NODEID
    GENERATE --> INPUT
```

There is no arrow from `simulator_input` to entities, seedemu integration or
upstream seedemu. `node_id` is the neutral cross-layer logical identity and
uses the general `identifier` validator. `materialization` is independent of
entities, static input and seedemu Compiler implementation.

## 2. Entity ownership

```mermaid
classDiagram
    class SkyseedAutonomousSystem {
        <<entity owner + seedemu integration>>
        +create_shell(shell_id) Shell
        +create_ground_station(...) GroundStation
        +get_shell(shell_id) Shell
        +get_satellite(satellite_id) Satellite
        +get_ground_station(ground_station_id) GroundStation
    }
    class Shell {
        <<Emulator entity>>
        +create_orbit(orbit_id, parameters) Orbit
        +get_orbit(orbit_id) Orbit
    }
    class Orbit {
        <<Emulator entity>>
        +parameters OrbitParameters
        +create_satellite(...) Satellite
        +get_satellite(satellite_id) Satellite
    }
    class OrbitParameters {
        <<entity-bound value>>
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
    Orbit "1" *-- "1" OrbitParameters : owns
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
    A --> F[create_simulator_static_input]
    F --> I[SimulatorStaticInput]
    B --> C[Emulator.render → SkyseedDockerCompiler]
```

## 5. File-driven large call chain

```mermaid
flowchart LR
    YAML[scenario.yaml + ground_stations.yaml]
    YAML --> L[load_static_emulator_config]
    L --> CONFIG[immutable StaticEmulatorConfig]
    CONFIG --> CREATE[create_static_emulator]
    CREATE --> ENTITIES[public create/join methods]
    CREATE --> RESULT[StaticEmulatorCreationResult]
    RESULT --> CONVERT[create_simulator_static_input]
    CONVERT --> INPUT[immutable SimulatorStaticInput]
    RESULT --> COMP[render + SkyseedDockerCompiler]
```

## 6. Static Simulator-input boundary

```mermaid
flowchart LR
    AS[Iterable of SkyseedAutonomousSystem] --> WALK[shared private entity traversal]
    SAT[Satellite + OrbitParameters] --> WALK
    GS[GroundStation] --> WALK
    WALK --> DERIVE[one private NodeId derivation]
    DERIVE --> CA[create_simulator_static_input]
    CA --> SI[SatelliteSimulationInput + NodeId]
    CA --> GI[GroundStationSimulationInput + NodeId]
    SI --> INPUT[SimulatorStaticInput]
    GI --> INPUT
    INPUT --> SIM[future Simulator consumer boundary]
    SIM -. future plan cites NodeId .-> COMP[future Compiler plan consumer]
    COMP -. NodeId.asn .-> BASE[SatelliteBase.getAutonomousSystem]
    BASE -. require Skyseed AS .-> OWNER[SkyseedAutonomousSystem]
    OWNER -. kind + entity_id .-> ENTITY[Satellite or GroundStation]
```

`SimulatorStaticInput` performs type validation, deterministic sorting,
duplicate-ID rejection, immutable snapshotting and read-only indexing only. It
does not traverse AS objects or call entity conversion and contains no entity
reference. No unified NodeId-to-entity/resource mapping exists. The dashed
path documents future owner-directed Compiler calls only; no Simulator, plan,
resolution or Compiler update path is implemented.

## 7. Generated experiment artifact and materialization

```mermaid
flowchart LR
    E[rendered Emulator] --> C[SkyseedDockerCompiler]
    C --> D[docker-compose.yml + build contexts]
    D --> F[example output finalization]
    F --> S[output/start.sh + output/cleanup.sh]
    S --> H[human start / cleanup]
    D --> L[ComposeProjectLauncher]
    L --> P[docker compose v2 project]
    H --> P
```

The Python entrypoint finalizes controls only after the current output is
complete. Generated scripts use the exact Compose file/project and never
reconstruct entities. `ComposeProjectLauncher` remains the programmatic,
state-verifying materialization adapter; it never recompiles the Emulator.
