# Initial Architecture Diagrams

这些图只表达当前确认的模块关系和信息流，不代表最终类图或最终 API。

Detailed conceptual class diagrams with responsibilities, key attributes and
methods are maintained in
[conceptual-class-diagrams.md](./conceptual-class-diagrams.md).

## 1. System boundary

```mermaid
flowchart LR
    User[User Configuration]

    subgraph Emulator
        Emu[Emulator]
        Seed[SEED Components]
        Catalog[Implemented immutable SimulationEntityCatalog]
        RuntimeNodes[Container and Node Configuration]
    end

    subgraph Simulator
        Simu[Simulator]
        Zone[Zone Subsystem]
        Topology[Topology Subsystem]
        Planner[Planning Subsystem]
        Compiler[Execution Compiler]
        Runner[Runner]
        Observer[Observation]
    end

    User --> Emu
    Seed --> Emu
    Emu --> RuntimeNodes
    Emu --> Catalog
    Catalog --> Simu
    Simu --> Zone
    Simu --> Topology
    Topology --> Planner
    Planner --> Compiler
    Compiler --> Runner
    Runner --> Observer
```

## 2. Simulator flow

```mermaid
flowchart TD
    Catalog[Read-only Entity View]
    Zone[Zone Definition and Selection]
    Time[Simulation Time Model]
    Connection[Connection Generation]
    Route[Route Generation]
    Slice[Immutable Topology Slice]
    Diff[Topology Difference]
    Semantic[Semantic Planner]
    Backend[Selected Backend Planner]
    Compiler[Execution Compiler]
    Bundle[Executable Bundle]
    Runner[Runner]
    Observation[Observation and Trace]

    Catalog --> Connection
    Zone --> Connection
    Time --> Connection
    Connection --> Route
    Connection --> Slice
    Route --> Slice
    Slice --> Diff
    Diff --> Semantic
    Semantic --> Backend
    Backend --> Compiler
    Compiler --> Bundle
    Bundle --> Runner
    Runner --> Observation
```

## 3. Planning layers

```mermaid
flowchart LR
    Difference[Topology Difference]
    SemanticPlanner[Backend-independent Semantic Planner]
    SemanticPlan[Semantic Transition Plan]

    Difference --> SemanticPlanner
    SemanticPlanner --> SemanticPlan

    SemanticPlan --> BPF[BPF Backend Planner]
    SemanticPlan --> MPLS[Linux MPLS Backend Planner]
    SemanticPlan --> IP[IP Routing Backend Planner]

    BPF --> ExecutionPlan[Backend Execution Plan]
    MPLS --> ExecutionPlan
    IP --> ExecutionPlan
```

## 4. Emulator static hierarchy concept

```mermaid
flowchart TD
    Emulator --> SkyseedAS[SkyseedAutonomousSystem]
    SkyseedAS --> Shell
    Shell --> Orbit
    Orbit --> Satellite

    SkyseedAS --> GroundStation
    SkyseedAS --> StandardNode[Inherited Network / Host / Router]
    GroundStation --> GroundNetwork[SEED Ground Network]

    SkyseedAS -->|read by| Builder[SimulationEntityCatalogBuilder<br/>application service]
    Satellite --> SatelliteProjector[SatelliteDescriptorProjector]
    GroundStation --> GroundProjector[GroundStationDescriptorProjector]
    SatelliteProjector --> SatelliteDescriptor[SatelliteDescriptor]
    GroundProjector --> GroundDescriptor[GroundStationDescriptor]
    Builder --> SatelliteProjector
    Builder --> GroundProjector
    Builder --> Catalog[SimulationEntityCatalog<br/>cross-layer contract]
```

## 5. Implemented isolated Satellite boundary

```mermaid
flowchart LR
    subgraph Domain[Static Emulator Domain]
        Shell --> Orbit --> Satellite
    end

    subgraph SeedIntegration[SEED Integration]
        Satellite -->|inherits| SeedNode[SEED Node]
        Container[network_mode: none]
        Satellite --> Container
    end

    subgraph Contract[Simulator Contract]
        LogicalId[SimulationNodeId]
        Projector[SatelliteDescriptorProjector]
        Descriptor[Immutable SatelliteDescriptor]
        Satellite -->|approved scalar values| Projector
        Projector --> Descriptor
        Descriptor -->|identified by| LogicalId
    end
```

```mermaid
classDiagram
    class Shell {
        <<domain entity>>
    }
    class Orbit {
        <<domain entity>>
    }
    class Satellite {
        <<domain entity + integration capability>>
    }
    class Node {
        <<external integration>>
    }
    class SatelliteDescriptorProjector {
        <<application service>>
    }
    class SatelliteDescriptor {
        <<cross-layer descriptor>>
    }
    class SimulationNodeId {
        <<value object + cross-layer contract>>
    }

    Shell "1" *-- "*" Orbit
    Orbit "1" *-- "*" Satellite
    Satellite --|> Node
    SatelliteDescriptorProjector --> Satellite : reads restricted values
    SatelliteDescriptorProjector --> SatelliteDescriptor : produces
    SatelliteDescriptor "1" *-- "1" SimulationNodeId : identifies
```

```text
Satellite(Node) -> SatelliteDescriptorProjector -> SatelliteDescriptor
```

## 6. Implemented static GroundStation boundary

该图记录当前已验证的单对象静态纵向路径；动态卫星侧能力仍未实现。

```mermaid
classDiagram
    class GroundStation {
        <<domain entity + integration capability>>
        +ground_station_id
        +location
        +station_type
        +operator
        +service_config
        +registry_type
        +joinNetwork(network)
    }

    class Router {
        <<external integration>>
    }

    class GroundStationDescriptorProjector {
        <<application service>>
        +project(ground_station)
    }

    class GroundStationDescriptor {
        <<cross-layer descriptor>>
        +node_id
        +ground_station_id
        +latitude_deg
        +longitude_deg
        +elevation_m
        +station_type
        +operator
    }

    class SimulationNodeId {
        <<value object + cross-layer contract>>
        +asn
        +entity_kind
        +entity_id
        +value
    }

    GroundStation --|> Router
    GroundStationDescriptorProjector --> GroundStation : reads
    GroundStationDescriptorProjector --> GroundStationDescriptor : produces
    GroundStationDescriptor "1" *-- "1" SimulationNodeId : identifies
```

```text
Satellite(Node) -> SatelliteDescriptor
GroundStation(Router) -> GroundStationDescriptor
```

Simulator 只接收受限的 Descriptor。Router 继承方向已由标准 rnode、IX、
Routing/eBGP、Compiler 和真实容器验证；未来动态 GS 能力仍需独立设计。
`SimulationNodeId` 只表达 Simulator 逻辑身份，不表达图中 SEED Router 的
Registry、容器或执行目标身份。

## 7. Implemented unified AS and Compiler boundaries

```mermaid
classDiagram
    class SkyseedAutonomousSystem {
        <<domain owner + integration>>
        +create_shell(shell_id)
        +create_ground_station(id, location, station_type, operator)
        +createNetwork(name)
        +createHost(name)
        +createRouter(name)
    }
    class Shell {
        <<domain entity>>
    }
    class Orbit {
        <<domain entity>>
    }
    class Satellite {
        <<domain entity + integration capability>>
    }
    class GroundStation {
        <<domain entity + integration capability>>
    }
    class Node {
        <<external integration>>
    }

    SkyseedAutonomousSystem "1" *-- "*" Shell
    Shell "1" *-- "*" Orbit
    Orbit "1" *-- "*" Satellite
    SkyseedAutonomousSystem "1" *-- "*" GroundStation
    SkyseedAutonomousSystem "1" o-- "*" Node
```

```mermaid
flowchart LR
    Emulator --> Compiler[SkyseedDockerCompiler]
    Compiler -->|satnode only| SatelliteBoundary[_SatelliteCompilerBoundary]
    Compiler -->|rnode, hnode and networks| Upstream[SEED Docker Compiler flow]
    SatelliteBoundary --> Accumulator[Single fail-closed private accumulator access]
    Upstream --> Compose[docker-compose.yml]
    Accumulator --> Compose
```

Compiler 只读取已经完成构造并 render 的 Emulator/Registry 状态，产生 Compose
artifact；它不创建 Shell、Orbit、Satellite 或 GroundStation。

`SkyseedDockerCompiler` 是唯一的公开 Skyseed Docker Compiler。Satellite 的
零接口特殊行为仍集中在内部边界；GroundStation 和普通 SEED 节点继续复用
上游路径。不存在按旧节点种类命名的公开 Compiler 别名。

## 8. Implemented static Emulator output boundary

```mermaid
classDiagram
    class SimulationEntityCatalog {
        <<cross-layer contract>>
        +get(node_id)
        +get_satellite(node_id_or_id)
        +get_ground_station(node_id_or_id)
        +list_satellites()
        +list_ground_stations()
        +list_nodes()
    }
    class SimulationEntityCatalogBuilder {
        <<application service>>
        +build(autonomous_systems)
    }
    class SkyseedAutonomousSystem {
        <<domain owner + integration>>
    }
    class SatelliteDescriptorProjector {
        <<application service>>
    }
    class GroundStationDescriptorProjector {
        <<application service>>
    }
    class SatelliteDescriptor {
        <<cross-layer descriptor>>
    }
    class GroundStationDescriptor {
        <<cross-layer descriptor>>
    }

    SimulationEntityCatalog "1" *-- "*" SatelliteDescriptor
    SimulationEntityCatalog "1" *-- "*" GroundStationDescriptor
    SimulationEntityCatalogBuilder --> SkyseedAutonomousSystem : reads
    SimulationEntityCatalogBuilder --> SatelliteDescriptorProjector : delegates
    SimulationEntityCatalogBuilder --> GroundStationDescriptorProjector : delegates
    SimulationEntityCatalogBuilder --> SimulationEntityCatalog : produces
```

Builder 不读取 SEED Registry 或 Compiler output；Catalog 不包含 AS、Node、Router、
容器或可修改集合。普通 SEED AS 不会被解释成 Skyseed 实体。

## 9. Compiler artifact and materialization boundary

```mermaid
flowchart LR
    Emulator[Rendered Emulator<br/>domain + SEED integration]
    Compiler[SkyseedDockerCompiler<br/>integration]
    Compose[Compose artifact]
    Launcher[ComposeProjectLauncher<br/>materialization integration]
    Project[Running Compose project]

    Emulator -->|compile; no entity creation| Compiler
    Compiler -->|produces| Compose
    Compose -->|consumed unchanged| Launcher
    Launcher -->|materializes; outside domain| Project
```

`ComposeProjectLauncher` 不进入 Emulator 所有权图、Descriptor、Catalog 或
Simulator 合同，也不反向调用 Loader、Applier 或 Compiler。
