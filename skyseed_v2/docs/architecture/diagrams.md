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
        Catalog[Read-only Simulation Entity View]
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

    Emulator --> ReadOnlyView[Read-only Simulator View]
    Satellite --> ReadOnlyView
    GroundStation --> ReadOnlyView
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
    class Shell
    class Orbit
    class Satellite
    class SeedNode
    class SatelliteDescriptorProjector
    class SatelliteDescriptor
    class SimulationNodeId

    Shell "1" *-- "*" Orbit
    Orbit "1" *-- "*" Satellite
    Satellite --|> SeedNode
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
        <<implemented / evolving>>
        +ground_station_id
        +location
        +station_type
        +operator
        +service_config
        +registry_type
        +joinNetwork(network)
    }

    class SeedRouter {
        <<SEED>>
    }

    class GroundStationDescriptorProjector {
        <<implemented>>
        +project(ground_station)
    }

    class GroundStationDescriptor {
        <<implemented immutable contract>>
        +node_id
        +ground_station_id
        +latitude_deg
        +longitude_deg
        +elevation_m
        +station_type
        +operator
    }

    class SimulationNodeId {
        <<public immutable logical identity>>
        +asn
        +entity_kind
        +entity_id
        +value
    }

    GroundStation --|> SeedRouter
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
        <<implemented>>
        +create_shell(shell_id)
        +create_ground_station(config)
        +createNetwork(name)
        +createHost(name)
        +createRouter(name)
    }
    class Shell
    class Orbit
    class Satellite
    class GroundStation
    class StandardSeedNode

    SkyseedAutonomousSystem "1" *-- "*" Shell
    Shell "1" *-- "*" Orbit
    Orbit "1" *-- "*" Satellite
    SkyseedAutonomousSystem "1" o-- "*" GroundStation
    SkyseedAutonomousSystem "1" o-- "*" StandardSeedNode
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

`SkyseedDockerCompiler` 是唯一的公开 Skyseed Docker Compiler。Satellite 的
零接口特殊行为仍集中在内部边界；GroundStation 和普通 SEED 节点继续复用
上游路径。不存在按旧节点种类命名的公开 Compiler 别名。
