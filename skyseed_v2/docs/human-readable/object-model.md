# High-level object ownership and cardinality

This is the only global object model. It shows roles, cardinalities, and a few
navigation members. Complete class/member diagrams live only in the
corresponding production package README files listed below. Current
implementation state is maintained only in [`CURRENT.md`](../CURRENT.md).

The accepted architecture is `Topology / PhysicalFrame → Network Planner →
PlannedOperationalState → Plan → Compiler / Runner → RealizedRuntimeState`.
Round 1 implements the first boundary only. The diagrams below therefore show
the current explicit temporary seam from `PhysicalFrame` through legacy
Slicer/TopologySlice/TopologyDifference and the old Planner. Those legacy
objects are scheduled for deletion or replacement when Connection, effective
Parameter/resource, Route, and Coordination ownership moves into the Network
Planner; they are not a second accepted architecture.

## Ownership and composition

Legend: `A *-- B` is owner/composition. Labels describe the owned value;
endpoint numbers are cardinalities.

```mermaid
classDiagram
    direction LR

    class UpstreamEmulator {
        <<upstream composition root>>
    }
    class UpstreamBase {
        <<upstream SEED Base layer>>
        +getAutonomousSystem()
    }
    class EmulatorAS["SkyseedAutonomousSystem"]
    class EmulatorAS {
        <<Emulator AS owner>>
        +get_shell()
        +get_ground_station()
    }
    class EmulatorShell["Shell"]
    class EmulatorShell {
        <<Emulator Shell owner>>
        +shell_id
        +get_orbit()
    }
    class EmulatorOrbit["Orbit"]
    class EmulatorOrbit {
        <<Emulator Orbit owner>>
        +orbit_id
        +get_satellite()
    }
    class EmulatorSatellite["Satellite"]
    class EmulatorSatellite {
        <<Emulator leaf entity>>
        +satellite_id
        +max_simultaneous_ground_station_accesses
    }
    class EmulatorGroundStation["GroundStation"]
    class EmulatorGroundStation {
        <<Emulator leaf entity>>
        +ground_station_id
        +max_simultaneous_satellite_accesses
    }

    UpstreamEmulator "1" *-- "1" UpstreamBase : layer
    UpstreamBase "1" *-- "0..*" EmulatorAS : Skyseed AS
    EmulatorAS "1" *-- "0..*" EmulatorShell : shells
    EmulatorAS "1" *-- "0..*" EmulatorGroundStation : stations
    EmulatorShell "1" *-- "0..*" EmulatorOrbit : orbits
    EmulatorOrbit "1" *-- "0..*" EmulatorSatellite : satellites
```

Legend: `<|--` is Python inheritance; `o--` is a same-instance lifecycle
reference and does not transfer domain ownership.

```mermaid
classDiagram
    direction LR

    class SeedNode["SEED Node"] {
        <<upstream lifecycle base>>
    }
    class SeedRouter["SEED Router"] {
        <<upstream router>>
    }
    class SeedExtensionNode["SEED ExtensionNode"] {
        <<upstream extension node>>
    }
    class SatelliteEntity["Satellite"] {
        <<Emulator entity and ExtensionNode>>
        +max_simultaneous_ground_station_accesses
    }
    class GroundStationEntity["GroundStation"] {
        <<Emulator entity and Router>>
        +max_simultaneous_satellite_accesses
    }

    SeedNode <|-- SeedRouter
    SeedRouter <|-- GroundStationEntity
    SeedNode <|-- SeedExtensionNode
    SeedExtensionNode <|-- SatelliteEntity
```

Legend: `*--` means Orbit owns Satellite in domain organization.

```mermaid
classDiagram
    class OrbitOwner["Orbit"] {
        <<Satellite domain owner>>
    }
    class SatelliteEntity["Satellite"] {
        <<Emulator entity and ExtensionNode>>
    }
    OrbitOwner "1" *-- "0..*" SatelliteEntity : domain organization
```

Legend: `o--` means the AS lifecycle inventory references that same instance.

```mermaid
classDiagram
    class SkyseedAS["SkyseedAutonomousSystem"] {
        <<Emulator AS owner>>
    }
    class SatelliteEntity["Satellite"] {
        <<Emulator entity and ExtensionNode>>
    }
    SkyseedAS "1" o-- "0..*" SatelliteEntity : lifecycle index, same instance
```

Legend: `A --> B` is a static lowering or future injection data flow; it does
not express ownership.

```mermaid
flowchart LR
    ENTITY["Entity / construction"] --> IDENTITY["static identity labels / geo"]
    FUTURE["Future Skyseed functional Layer<br/>name, count, API unfrozen"] --> FUNCTION["software / files / commands / daemons"]
    IDENTITY --> NODE["public SEED Node APIs"]
    FUNCTION --> NODE
    NODE --> COMPILER["standard SEED compiler"]
    COMPILER --> ARTIFACT["Dockerfile / Compose serialization"]
```

Legend: `A *-- B` is owner/composition. Labels describe the owned value;
endpoint numbers are cardinalities.

```mermaid
classDiagram
    direction LR

    class SimulatorStaticInput {
        <<immutable boundary DTO>>
        +list_satellites()
        +list_ground_stations()
    }
    class SimulationTimeline {
        <<timestamp authority>>
        +start_time
        +iter_timestamps()
    }
    class SimulationDefinition {
        <<immutable definition>>
        +static_input
        +timeline
    }
    class Simulator {
        <<composition root>>
        +create_topology()
        +create_slicer()
        +create_planner()
    }
    class Topology {
        <<physical aggregate and projection owner>>
        +get_node()
        +frame_at()
        +get_position_model()
    }
    class PositionModel {
        <<physical Protocol role>>
        +compute_positions()
    }
    class PhysicalFrame {
        <<immutable physical-domain value>>
        +timestamp
        +positions
        +get_position()
    }
    class SimulatorAS["SkyseedAutonomousSystem"]
    class SimulatorAS {
        <<Topology AS owner>>
        +asn
        +get_shell()
    }
    class SimulatorShell["Shell"]
    class SimulatorShell {
        <<Topology Shell owner>>
        +shell_id
        +get_orbit()
    }
    class SimulatorOrbit["Orbit"]
    class SimulatorOrbit {
        <<Topology Orbit owner>>
        +orbit_id
        +get_satellite()
    }
    class SimulatorSatellite["Satellite"]
    class SimulatorSatellite {
        <<static Satellite>>
        +node_id
        +max_simultaneous_ground_station_accesses
    }
    class SimulatorGroundStation["GroundStation"]
    class SimulatorGroundStation {
        <<static GroundStation>>
        +node_id
        +max_simultaneous_satellite_accesses
    }
    class ZoneLayout {
        <<Zone owner>>
        +zones
        +resolve_zone()
    }
    class Zone {
        <<service area value>>
        +zone_id
        +contains()
    }
    class Slicer {
        <<temporary legacy operational facade>>
        +create_next_slice()
        +create_difference()
    }
    class TopologySlice {
        <<legacy mixed-state published aggregate>>
        +slice_id
        +timestamp
    }
    class TopologyDifference {
        <<legacy immutable on-request value; not retained>>
        +previous_slice_id
        +current_slice_id
        +is_empty
    }
    class EarthFixedPosition {
        <<position value>>
        +x_m
        +y_m
        +z_m
    }
    class ConnectionSlice {
        <<connection aggregate>>
        +connections
    }
    class RouteSlice {
        <<route aggregate>>
        +routes
    }
    class Planner {
        <<current legacy Difference-based planner>>
        +synchronize()
        +get_plan()
    }
    class Plan {
        <<immutable semantic update plan>>
        +start_slice_id
        +start_timestamp
        +end_slice_id
        +end_timestamp
        +connection_transitions
        +parameter_transitions
        +forwarding_transitions
        +forwarding_precedences
        +temporal_index
    }
    class ForwardingTransitionPrecedence {
        <<immutable semantic ordering relation>>
        +predecessor
        +successor
    }

    SimulationDefinition "1" *-- "1" SimulatorStaticInput : static input
    SimulationDefinition "1" *-- "1" SimulationTimeline : timeline
    Simulator "1" *-- "1" SimulationDefinition : exact definition
    Simulator "1" *-- "1" Topology : created physical aggregate
    Topology "1" *-- "1" PositionModel : bound physical projection role
    Simulator "1" *-- "1" Slicer : temporary legacy seam
    Simulator "1" *-- "0..1" Planner : current Planner after Slicer
    Planner "1" *-- "0..1" Plan : current snapshot
    Plan "1" *-- "0..*" ForwardingTransitionPrecedence : semantic ordering
    Topology "1" *-- "0..*" SimulatorAS : autonomous systems
    SimulatorAS "1" *-- "0..*" SimulatorShell : shells
    SimulatorAS "1" *-- "0..*" SimulatorGroundStation : stations
    SimulatorShell "1" *-- "0..*" SimulatorOrbit : orbits
    SimulatorOrbit "1" *-- "0..*" SimulatorSatellite : satellites
    Topology "1" *-- "0..1" ZoneLayout : configured layout
    ZoneLayout "1" *-- "1..*" Zone : zones
    PhysicalFrame "1" *-- "0..*" EarthFixedPosition : exact Topology-node Positions
    Slicer "1" *-- "1..*" TopologySlice : retained legacy Slice 0..N
    TopologySlice "1" *-- "0..*" EarthFixedPosition : legacy duplicated positions
    TopologySlice "1" *-- "1" ConnectionSlice : connections
    TopologySlice "1" *-- "1" RouteSlice : routes
```

Topology owns the physical PositionModel and produces `PhysicalFrame` values
without retaining a history or advancing time. A frame's timestamp is its
physical identity; it has no sequence ID or operational state. The temporary
legacy Slicer and current Planner remain peer-level components inside the
Simulator domain only for Round 1. The one `Simulator` creates and retains all
three current components, while the Planner references the exact sibling
Slicer rather than owning it.

Legend: `A ..> B` is a boundary projection that creates a receiver-owned B
value; it retains no A object or cross-domain ownership.

```mermaid
classDiagram
    class UpstreamEmulator
    class SimulatorStaticInput
    UpstreamEmulator ..> SimulatorStaticInput : projected by boundary function
```

## Exact references

Legend: `A --> B` means A retains the exact B instance. It is neither
composition nor a copied lookup.

```mermaid
classDiagram
    class Slicer
    class Topology
    class SimulationTimeline
    class Planner

    Slicer "1" --> "1" Topology : exact Topology
    Slicer "1" --> "1" SimulationTimeline : exact Timeline
    Planner "1" --> "1" Slicer : exact Simulator sibling
```

Legend: `A ..> B` means A consumes separately supplied immutable B values; it
does not express composition, ownership transfer, or lifecycle control.

```mermaid
classDiagram
    class BpfCompiledRealization {
        <<immutable symbolic handoff>>
        +start_slice_id
        +end_slice_id
    }
    class BpfRunner {
        <<sole live BPF lifecycle owner>>
        +prepare()
        +run()
        +cleanup()
    }
    BpfRunner ..> BpfCompiledRealization : consumes supplied future work
```

## Creation, projection, calls, and value handoff

Legend: every edge below is labeled with its exact relationship. No edge implies
retained ownership unless it explicitly says `creates and owns`; the ownership
cardinalities remain in the class diagram above.

```mermaid
flowchart LR
    EMU["upstream Emulator"] -->|projected into| INPUT["SimulatorStaticInput"]
    INPUT -->|supplied through SimulationDefinition to construct| SIM["Simulator"]
    SIM -->|creates and owns| TOP["Topology"]
    SIM -->|temporarily creates and owns| SLI["legacy Slicer"]
    SIM -->|temporarily creates and owns after Slicer| PLA["current Planner"]

    REQUEST["requested timestamp"] -->|supplied to frame_at| TOP
    TOP -->|calls owned physical role| POS["PositionModel"]
    TOP -->|produces; does not retain history| FRAME["PhysicalFrame<br/>timestamp + Positions"]
    SLI -->|temporarily calls frame_at(timestamp)| TOP
    FRAME -->|Positions consumed by| SLI
    SLI -->|1 calls legacy role| CON["ConnectionModel"]
    SLI -->|2 calls legacy role| ROUTE["RouteModel"]
    SLI -->|produces and retains| SLICE["legacy TopologySlice"]
    PLA -->|calls create_difference on exact sibling| SLI
    SLI -->|produces on request; does not retain| DIFF["legacy TopologyDifference"]
    DIFF -->|consumed by synchronize| PLA
    PLA -->|synchronize produces; Planner retains current| PLAN["Plan"]

    PLAN -->|semantic input consumed by| COMPILE["compile_bpf"]
    SLI -->|exact read-only get_slice / get_topology access| COMPILE
    PREVIOUS["optional previous BpfCompiledRealization"] -->|explicit baseline consumed by| COMPILE
    COMPILE -->|creates| REAL["BpfCompiledRealization"]
    SUPPLIER["caller-owned future-work supplier"] -->|delivers value| RUNNER["BpfRunner"]
    REAL -->|returned through supplier; consumed by| RUNNER
    CALLER["runtime caller"] -->|calls prepare / run or activate_next / teardown via cleanup| RUNNER
    CALLER -->|"after teardown attempt calls inspection_snapshot() on"| RUNNER
    RUNNER -->|returns detached projection without lifecycle side effect| SNAP["detached Runner evidence"]
```

`compile_bpf` has no separate `SlicerFacts` DTO. Its architectural input is
`Plan + approved read-only Simulator facts + optional previous baseline`; the
current public API exposes those Simulator facts through the exact `Slicer`'s
`get_slice()` and `get_topology()` methods. The caller/supplier, not an immutable
`BpfCompiledRealization` value, invokes or controls `BpfRunner`.

The projection carries no Emulator/SEED reference. No Model calls another
Model. In the current progression, legacy Slicer still obtains the requested
timestamp from its Timeline iterator; that cursor is visible migration debt,
not accepted physical ownership.

Legend: `A ..> Model` is a direct call to an injected Protocol role; it does
not express ownership and no Model calls another Model.

```mermaid
classDiagram
    class Slicer
    class Topology
    class PositionModel {
        <<Protocol role>>
        +compute_positions()
    }
    class ConnectionModel {
        <<Protocol role>>
        +compute_connection_slice()
    }
    class RouteModel {
        <<Protocol role>>
        +compute_route_slice()
    }
    Topology ..> PositionModel : physical projection call
    Slicer ..> ConnectionModel : 1 legacy call
    Slicer ..> RouteModel : 2 legacy call
```

## Protocol realization

Legend: `Implementation ..|> Protocol` is UML Protocol realization; it is not
call order.

```mermaid
classDiagram
    class PositionModel
    class ConnectionModel
    class RouteModel
    class CircularOrbitPositionModel {
        <<built-in implementation>>
        +compute_positions()
    }
    class GeometricConnectionModel {
        <<built-in implementation>>
        +compute_connection_slice()
    }
    class HopCountRouteModel {
        <<built-in implementation>>
        +compute_route_slice()
    }
    class GatewayHopCountRouteModel {
        <<built-in implementation>>
        +compute_route_slice()
    }

    CircularOrbitPositionModel ..|> PositionModel
    GeometricConnectionModel ..|> ConnectionModel
    HopCountRouteModel ..|> RouteModel
    GatewayHopCountRouteModel ..|> RouteModel
```

## Public-contract and local-detail mapping

| Diagram object | Public contract |
|---|---|
| `EmulatorAS` | `skyseed_v2.emulator.seedemu.SkyseedAutonomousSystem` |
| `EmulatorShell` | `skyseed_v2.emulator.entities.Shell` |
| `EmulatorOrbit` | `skyseed_v2.emulator.entities.Orbit` |
| `EmulatorSatellite` | `skyseed_v2.emulator.entities.Satellite` |
| `EmulatorGroundStation` | `skyseed_v2.emulator.entities.GroundStation` |
| `SimulatorStaticInput` | `skyseed_v2.simulator.input.SimulatorStaticInput` |
| `SimulationDefinition` | `skyseed_v2.simulator.SimulationDefinition` |
| `SimulationTimeline` | `skyseed_v2.simulator.SimulationTimeline` |
| `Simulator` | `skyseed_v2.simulator.Simulator` |
| `Topology` | `skyseed_v2.simulator.topology.Topology` |
| `PhysicalFrame` | `skyseed_v2.simulator.topology.PhysicalFrame` |
| `SimulatorAS` | `skyseed_v2.simulator.topology.SkyseedAutonomousSystem` |
| `SimulatorShell` | `skyseed_v2.simulator.topology.Shell` |
| `SimulatorOrbit` | `skyseed_v2.simulator.topology.Orbit` |
| `SimulatorSatellite` | `skyseed_v2.simulator.topology.Satellite` |
| `SimulatorGroundStation` | `skyseed_v2.simulator.topology.GroundStation` |
| `ZoneLayout` | `skyseed_v2.simulator.topology.ZoneLayout` |
| `Zone` | `skyseed_v2.simulator.topology.Zone` |
| `Slicer` | `skyseed_v2.simulator.slicing.Slicer` |
| `PositionModel` | `skyseed_v2.simulator.topology.PositionModel` |
| `ConnectionModel` | `skyseed_v2.simulator.slicing.ConnectionModel` |
| `RouteModel` | `skyseed_v2.simulator.slicing.RouteModel` |
| `TopologySlice` | `skyseed_v2.simulator.slicing.TopologySlice` |
| `EarthFixedPosition` | `skyseed_v2.simulator.geometry.EarthFixedPosition` |
| `ConnectionSlice` | `skyseed_v2.simulator.slicing.ConnectionSlice` |
| `RouteSlice` | `skyseed_v2.simulator.slicing.RouteSlice` |
| `TopologyDifference` | `skyseed_v2.simulator.slicing.TopologyDifference` |
| `CircularOrbitPositionModel` | `skyseed_v2.simulator.models.CircularOrbitPositionModel` |
| `GeometricConnectionModel` | `skyseed_v2.simulator.models.GeometricConnectionModel` |
| `HopCountRouteModel` | `skyseed_v2.simulator.models.HopCountRouteModel` |
| `GatewayHopCountRouteModel` | `skyseed_v2.simulator.models.GatewayHopCountRouteModel` |
| `BpfCompiledRealization` | `skyseed_v2.backend.bpf.BpfCompiledRealization` |
| `BpfRunner` | `skyseed_v2.backend.bpf.BpfRunner` |

Each mapped class has one detailed maintenance source: the README in the
package that owns its implementation. See
[`maintenance-map.md`](../codex-reference/maintenance-map.md). Lifecycle and
failure contracts are in
[`ownership-lifecycle.md`](../codex-reference/ownership-lifecycle.md) and
[`dynamic-slicing-contracts.md`](../codex-reference/dynamic-slicing-contracts.md).
