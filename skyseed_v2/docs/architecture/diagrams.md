# Current Architecture Diagrams

Solid implemented nodes and arrows reflect current code. Simulator Slice 1 is
**IMPLEMENTED**; dashed Planning/dynamic nodes are conceptual boundaries, not
implementations or frozen public APIs. The top-level map is in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## 1. Package dependency direction

```mermaid
flowchart LR
    ENT[entities]
    SEED[seedemu_integration]
    INPUT[simulator/static_input]
    CORE[simulator core]
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
    CORE --> INPUT
    CORE --> NODEID
    CONFIG --> ID
    CREATE --> CONFIG
    CREATE --> ENT
    CREATE --> SEED
    GENERATE --> ENT
    GENERATE --> SEED
    GENERATE --> NODEID
    GENERATE --> INPUT
```

There is no arrow from `simulator` core or `simulator/static_input` to entities,
seedemu integration, upstream seedemu or `simulator_input_generation`. The
external generation adapter alone points to both the Emulator side and the
Simulator boundary. `node_id` is the neutral cross-layer logical identity and
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
    EMU[emu.py] --> BUILD[build_emulator]
    BUILD --> UP[upstream Emulator]

    MAIN[main.py] --> BUILD
    MAIN --> RENDER[Emulator render and SkyseedDockerCompiler]

    SIMU[simu.py external assembly] --> BUILD
    SIMU --> BASE[query SatelliteBase-owned Skyseed AS]
    UP --> BASE
    BASE --> ADAPT[create_simulator_static_input]
    ADAPT --> INPUT[SimulatorStaticInput]
    INPUT --> DEF[SimulationDefinition]
    TIMELINE[SimulationTimeline] --> DEF
    DEF --> SIM[Simulator]
    SIM --> TOP[Topology]
```

`emu.py` only constructs and returns the Emulator. `main.py` is the pure
Emulator render/compile entrypoint. `simu.py` is the external assembly entrypoint
that crosses from the Emulator side, through the thin adapter, to the Simulator
side; it does not import `main.py`. Simulator core never imports back across
that boundary.

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
    INPUT --> DEF[SimulationDefinition.static_input]
    TIMELINE[SimulationTimeline] --> DEF
    DEF --> SIM[Simulator construction]
    SIM --> TOPOLOGY[Topology materialization]
    TOPOLOGY --> DOMAIN[immutable AS → Shell → Orbit → Satellite tree]
    TOPOLOGY --> GSOWN[AS-owned GroundStation objects]
    SIM -. future ownership .-> PLANNING[future Planning]
    TOPOLOGY -. creates and compares .-> SLICE[future Slice / Difference citing NodeId]
    SLICE -. consumed by .-> PLANNING
    PLANNING -. later approved plan .-> DYNAMIC[dynamic execution resolution]
    DYNAMIC -.->|NodeId.asn| BASE[SatelliteBase.getAutonomousSystem]
    BASE -. require Skyseed AS .-> OWNER[SkyseedAutonomousSystem]
    OWNER -. kind + entity_id .-> ENTITY[Satellite or GroundStation]
```

`SimulatorStaticInput` performs type validation, deterministic sorting,
duplicate-ID rejection, immutable input copying and read-only indexing only. It
does not traverse AS objects or call entity conversion and contains no entity
reference. Its lookups require `NodeId`; no local string-ID fallback exists. It
is construction boundary data, not the Simulator's long-lived domain graph.
The implemented Topology has one private unified `NodeId` index over its own
static nodes, never Emulator entities or runtime resources. Simulator,
Timeline, Definition, Topology and the static domain objects are implemented;
the dashed Planning, Slice, plan, resolution and dynamic-update structures are
not.

## 7. Static deployment and conceptual dynamic execution chains

```mermaid
flowchart TD
    E[Emulator / SEED static model]
    E --> ADAPT[create_simulator_static_input]
    ADAPT --> INPUT[SimulatorStaticInput]
    INPUT --> DEF[SimulationDefinition]
    DEF --> SIM[Simulator]
    SIM --> TOP[Topology]
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
