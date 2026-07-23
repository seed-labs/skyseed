# Current Architecture Diagrams

Solid implemented nodes and arrows reflect current code. Accepted/frozen
Simulator Iteration 1 and Iteration 2 static Zones are **IMPLEMENTED**; dashed
dynamic-Slice nodes are **APPROVED / NOT IMPLEMENTED**, while dashed Planning
and backend nodes remain conceptual/open. The top-level map is in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-approved-dynamic-slice-boundary-map).

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
    CORE --> CONFIG
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

    MAIN[main.py canonical full output] --> BUILD
    MAIN --> BASE
    MAIN --> RENDER[Emulator render and SkyseedDockerCompiler]
    MAIN --> TOOLS[repository-local offline tools after compile]

    SIMU[simu.py external assembly] --> BUILD
    SIMU --> BASE[query SatelliteBase-owned Skyseed AS]
    UP --> BASE
    BASE --> ADAPT[create_simulator_static_input]
    ADAPT --> INPUT[SimulatorStaticInput]
    INPUT --> DEF[SimulationDefinition]
    TIMELINE[SimulationTimeline] --> DEF
    DEF --> SIM[Simulator]
    ZYAML[zones.yaml] --> ZLOAD[load_zone_layout_definition]
    ZLOAD --> ZDEF[ZoneLayoutDefinition]
    ZDEF --> SIM
    SIM -->|create_topology with ZDEF| TOP[Topology]
    TOP --> ZLAYOUT[Topology-owned ZoneLayout]
    ZLAYOUT --> ZONES[Zone tuple/index]
    ZLAYOUT --> MODEL[fixed ZoneMembershipModel]
```

`emu.py` only constructs and returns the Emulator. `main.py` is the canonical
full-output entrypoint: it creates the Emulator and Topology once, preserves
render/compile, then gives that same Topology to repository-local offline
tools. `simu.py` remains the independent readable assembly entrypoint and does
not import `main.py` or generate tool artifacts. Simulator core never imports
back across that boundary or into `tools`.

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
    ZINPUT[zones.yaml or programmatic construction] --> ZDEF[ZoneLayoutDefinition]
    ZDEF --> SIM
    TOPOLOGY --> ZLAYOUT[optional Topology-owned ZoneLayout]
    ZLAYOUT --> ZONES[Zone tuple/index]
    ZLAYOUT --> ZMODEL[fixed ZoneMembershipModel]
    SIM -. approved ownership .-> SLICER[approved Slicer]
    SLICER -. exact reference .-> TOPOLOGY
    SLICER -. ordered generation .-> SLICE[approved TopologySlice]
    SLICER -. adjacent comparison .-> DIFF[approved TopologyDifference]
    DIFF -. future finite window .-> PLANNING[future Planning]
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
static nodes, never Emulator entities or runtime resources, plus at most one
ZoneLayout. Simulator, Timeline, Definition, Topology, the static node objects
and static Zone domain are implemented. Slicer, TopologySlice and
TopologyDifference are accepted contracts but not implemented; Planning,
dynamic resource-resolution and update structures remain conceptual/open.

## 7. Implemented Zone construction and ownership

```mermaid
classDiagram
    class LatitudeLongitudeRectangleDefinition {
        <<implemented frozen construction definition>>
    }
    class ZoneDefinition {
        <<implemented frozen construction definition>>
    }
    class ZoneLayoutDefinition {
        <<implemented frozen construction definition>>
    }
    class Simulator {
        <<implemented composition root>>
        +create_topology(zone_layout_definition=None, zone_membership_model=None) Topology
    }
    class Topology {
        <<implemented immutable aggregate root>>
        +get_zone_layout() ZoneLayout
        +get_zone(zone_id) Zone
        +find_zones(latitude_deg, longitude_deg) tuple
        +resolve_zone(latitude_deg, longitude_deg) Zone
    }
    class ZoneLayout {
        <<implemented Topology-owned aggregate>>
    }
    class Zone {
        <<implemented immutable Simulator domain object>>
    }
    class LatitudeLongitudeRectangle {
        <<implemented immutable Zone geometry>>
    }
    class ZoneMembershipModel {
        <<implemented public Protocol>>
    }
    class DefaultZoneMembershipModel {
        <<implemented stateless algorithm>>
    }

    ZoneLayoutDefinition *-- ZoneDefinition
    ZoneDefinition *-- LatitudeLongitudeRectangleDefinition
    Simulator --> ZoneLayoutDefinition : consumes during create_topology
    Simulator --> ZoneMembershipModel : optional algorithm injection
    Simulator --> Topology : creates atomically
    Topology "1" *-- "0..1" ZoneLayout : owns
    ZoneLayout *-- Zone : owns tuple/index
    ZoneLayout o-- ZoneMembershipModel : fixed algorithm
    Zone *-- LatitudeLongitudeRectangle : owns
    DefaultZoneMembershipModel ..|> ZoneMembershipModel : implements
    Topology --> ZoneLayout : facade delegation
```

The Definition is materialization input and is not retained. ZoneLayout is the
only owner of its Zone tuple and ID index; the Model receives those same Zone
instances and does not own another collection. A valid Topology may omit the
layout, and the algorithm reference cannot be replaced after construction.

## 8. Static deployment, approved Slice state and future execution

```mermaid
flowchart TD
    E[Emulator / SEED static model]
    E --> ADAPT[create_simulator_static_input]
    ADAPT --> INPUT[SimulatorStaticInput]
    INPUT --> DEF[SimulationDefinition]
    DEF --> SIM[Simulator]
    SIM --> TOP[Topology]
    SIM -. creates after Topology .-> SLICER[approved Slicer]
    TOP -. exact static input .-> SLICER
    SLICER -. PositionModel .-> POS[approved Position mapping]
    POS -. ConnectionModel .-> CONN[approved ConnectionSlice]
    CONN -. RouteModel .-> ROUTE[approved RouteSlice]
    ROUTE -. atomic publication .-> SLICE[approved TopologySlice]
    SLICER -. adjacent comparison .-> DIFF[approved TopologyDifference]
    DIFF -. future DifferenceWindow .-> PLAN[future Planner]
    PLAN -. future plan .-> BACK[future BackendExecutionPlan]
    BACK -. lowered by .-> EXEC[future ExecutionCompiler]
    EXEC -. creates .-> BUNDLE[future ExecutableBundle]
    BUNDLE -. consumed by .-> RUN[future SimulationRunner]

    E --> DOCKER[SkyseedDockerCompiler]
    DOCKER --> DEPLOY[Dockerfile / Compose / services / networks]
    DEPLOY --> LAUNCH[ComposeProjectLauncher]
```

Solid arrows remain current implementation. Dashed Position through Difference
arrows are accepted public contracts awaiting Slices A through F; dashed
Planner/backend arrows remain deferred. Topology does not create or compare
Slices.

The implemented static deployment compiler and conceptual dynamic
`ExecutionCompiler` have different inputs, outputs, ownership and lifecycles;
they have no inheritance relationship or required shared public parent.
`ComposeProjectLauncher` is not the conceptual `SimulationRunner`. The old
`SimulationPlanCompiler` label is retained only as a **retired/corrected**
over-compression, not as a frozen master contract.

## 9. Generated experiment artifact and materialization

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
