# Current Architecture Diagrams

Solid implemented nodes and arrows reflect current code. The Simulator static
input contract is **IMPLEMENTED**; dashed Simulator/dynamic nodes are conceptual
boundaries, not implementations or frozen public APIs. The top-level map is in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## 1. Package dependency direction

```mermaid
flowchart LR
    ENT[entities]
    SEED[seedemu_integration]
    INPUT[simulator/static_input]
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

There is no arrow from `simulator/static_input` to entities, seedemu integration
or upstream seedemu. `node_id` is the neutral cross-layer logical identity and
uses the general `identifier` validator. `materialization` is independent of
entities, static input and the `SkyseedDockerCompiler` implementation.

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
    CREATE --> EMU[upstream Emulator]
    EMU --> BASE[getLayer Base]
    BASE --> ASE[get_skyseed_autonomous_systems]
    ASE --> CONVERT[create_simulator_static_input]
    CONVERT --> INPUT[immutable SimulatorStaticInput]
    EMU --> COMP[render + SkyseedDockerCompiler]
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
    INPUT --> SIM[future Simulator construction]
    SIM -. owns .-> TIMELINE[future SimulationTimeline]
    SIM -. owns .-> TOPOLOGY[future Topology]
    SIM -. owns .-> PLANNING[future Planning]
    TOPOLOGY -. creates and compares .-> SLICE[future Slice / Difference citing NodeId]
    SLICE -. consumed by .-> PLANNING
    PLANNING -. later approved plan .-> DYNAMIC[dynamic execution resolution]
    DYNAMIC -.->|NodeId.asn| BASE[SatelliteBase.getAutonomousSystem]
    BASE -. require Skyseed AS .-> OWNER[SkyseedAutonomousSystem]
    OWNER -. kind + entity_id .-> ENTITY[Satellite or GroundStation]
```

`SimulatorStaticInput` performs type validation, deterministic sorting,
duplicate-ID rejection, immutable snapshotting and read-only indexing only. It
does not traverse AS objects or call entity conversion and contains no entity
reference. It is construction boundary data, not the future Simulator's
long-lived domain graph. No unified NodeId-to-entity/resource mapping exists.
The dashed paths show approved ownership direction and future owner-directed
execution-boundary calls only; none of the Simulator, Timeline, Topology,
Planning, Slice, plan, resolution or dynamic update structures is implemented.

## 7. Static deployment and conceptual dynamic execution chains

```mermaid
flowchart TD
    E[Emulator / SEED static model]
    E --> ADAPT[create_simulator_static_input]
    ADAPT --> INPUT[SimulatorStaticInput]
    INPUT -. future constructor input .-> SIM[future Simulator]
    SIM -. owns .-> TOP[future Topology]
    SIM -. owns .-> PLAN[future Planning]
    TOP -. compares authoritative slices .-> DIFF[future TopologyDifference]
    DIFF -. SemanticPlanner .-> SEM[future SemanticTransitionPlan]
    SEM -. BackendPlanner .-> BACK[future BackendExecutionPlan]
    BACK -. lowered by .-> EXEC[future ExecutionCompiler]
    EXEC -. creates .-> BUNDLE[future ExecutableBundle]
    BUNDLE -. consumed by .-> RUN[future SimulationRunner]

    E --> DOCKER[SkyseedDockerCompiler]
    DOCKER --> DEPLOY[Dockerfile / Compose / services / networks]
    DEPLOY --> LAUNCH[ComposeProjectLauncher]
```

The implemented static deployment compiler and the conceptual dynamic
`ExecutionCompiler` have different inputs, outputs, ownership and lifecycles;
they have no inheritance relationship or required shared public parent.
`ComposeProjectLauncher` is not the conceptual `SimulationRunner`. The old
`SimulationPlanCompiler` label is retained only as a **retired/corrected**
over-compression of semantic planning, backend planning and executable-form
compilation, not as a frozen master contract.

## 8. Generated experiment artifact and materialization

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
