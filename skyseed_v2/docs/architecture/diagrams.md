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
    Emulator --> AS
    AS --> Shell
    Shell --> Orbit
    Orbit --> Satellite

    AS --> GroundStation
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
        RuntimeRef[RuntimeNodeReference]
        Projector[SatelliteDescriptorProjector]
        Descriptor[Immutable SatelliteDescriptor]
        Satellite -->|approved scalar values| Projector
        Projector --> Descriptor
        Satellite --> RuntimeRef
        RuntimeRef --> Descriptor
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

    Shell "1" *-- "*" Orbit
    Orbit "1" *-- "*" Satellite
    Satellite --|> SeedNode
    SatelliteDescriptorProjector --> Satellite : reads restricted values
    SatelliteDescriptorProjector --> SatelliteDescriptor : produces
```

```text
Satellite(Node) -> SatelliteDescriptorProjector -> SatelliteDescriptor
```

## 6. GroundStation approved conceptual direction

GroundStation 尚未实现。该图表达单对象方向，不预先决定最终 Router 继承或
能力组合细节。

```mermaid
classDiagram
    class GroundStation {
        <<conceptual>>
        +ground_station_id
        +location
        +station_type
        +operator
        +service_config
        +join_ground_network(network)
        +configure_ground_routing(policy)
        +install_service(service)
    }

    class SeedRouter {
        <<SEED>>
    }

    class GroundStationDescriptorProjector {
        <<conceptual>>
        +project(ground_station)
    }

    class GroundStationDescriptor {
        <<conceptual immutable contract>>
        +ground_station_id
        +location
        +station_type
        +runtime_node_reference
    }

    GroundStation --|> SeedRouter
    GroundStationDescriptorProjector --> GroundStation : reads
    GroundStationDescriptorProjector --> GroundStationDescriptor : produces
```

```text
Satellite(Node) -> SatelliteDescriptor
GroundStation(Router, conceptual) -> GroundStationDescriptor
```

Simulator 只接收受限的 Descriptor。Router 继承方向必须在 GroundStation
切片中重新验证，不能据此图提前实现。
