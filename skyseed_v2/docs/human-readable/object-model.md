# High-level object ownership and cardinality

This is the only global object model. It shows roles, cardinalities, and a few
navigation members. Complete class/member diagrams live only in the
corresponding production package README files listed below. Current
implementation state is maintained only in [`CURRENT.md`](../CURRENT.md).

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
    }
    class EmulatorGroundStation["GroundStation"]
    class EmulatorGroundStation {
        <<Emulator leaf entity>>
        +ground_station_id
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
    }
    class GroundStationEntity["GroundStation"] {
        <<Emulator entity and Router>>
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
        <<static aggregate>>
        +autonomous_systems
        +get_node()
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
    }
    class SimulatorGroundStation["GroundStation"]
    class SimulatorGroundStation {
        <<static GroundStation>>
        +node_id
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
        <<dynamic owner facade>>
        +create_next_slice()
        +create_difference()
    }
    class TopologySlice {
        <<published aggregate>>
        +slice_id
        +timestamp
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
        <<incremental semantic planner>>
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
    Simulator "1" *-- "1" Topology : created static aggregate
    Simulator "1" *-- "1" Slicer : created dynamic owner
    Simulator "1" *-- "0..1" Planner : created after Slicer
    Planner "1" *-- "0..1" Plan : current snapshot
    Plan "1" *-- "0..*" ForwardingTransitionPrecedence : semantic ordering
    Topology "1" *-- "0..*" SimulatorAS : autonomous systems
    SimulatorAS "1" *-- "0..*" SimulatorShell : shells
    SimulatorAS "1" *-- "0..*" SimulatorGroundStation : stations
    SimulatorShell "1" *-- "0..*" SimulatorOrbit : orbits
    SimulatorOrbit "1" *-- "0..*" SimulatorSatellite : satellites
    Topology "1" *-- "0..1" ZoneLayout : configured layout
    ZoneLayout "1" *-- "1..*" Zone : zones
    Slicer "1" *-- "1..*" TopologySlice : retained Slice 0..N
    TopologySlice "1" *-- "0..*" EarthFixedPosition : positions
    TopologySlice "1" *-- "1" ConnectionSlice : connections
    TopologySlice "1" *-- "1" RouteSlice : routes
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

## Creation, projection, and calls

Legend: `A ..> B` is a creation, projection, or call dependency named on the
edge; it does not imply retained ownership.

```mermaid
classDiagram
    class UpstreamEmulator
    class SimulatorStaticInput
    class Simulator
    class Topology
    class Slicer
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
    class TopologyDifference {
        <<adjacent-slice difference>>
        +previous_slice_id
        +current_slice_id
    }

    UpstreamEmulator ..> SimulatorStaticInput : scalar projection
    Simulator ..> Topology : create_topology
    Simulator ..> Slicer : create_slicer
    Slicer ..> PositionModel : 1 calls
    Slicer ..> ConnectionModel : 2 calls
    Slicer ..> RouteModel : 3 calls
    Slicer ..> TopologyDifference : creates on request
```

The projection carries no Emulator/SEED reference. No Model calls another
Model.

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

    CircularOrbitPositionModel ..|> PositionModel
    GeometricConnectionModel ..|> ConnectionModel
    HopCountRouteModel ..|> RouteModel
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
| `SimulatorAS` | `skyseed_v2.simulator.topology.SkyseedAutonomousSystem` |
| `SimulatorShell` | `skyseed_v2.simulator.topology.Shell` |
| `SimulatorOrbit` | `skyseed_v2.simulator.topology.Orbit` |
| `SimulatorSatellite` | `skyseed_v2.simulator.topology.Satellite` |
| `SimulatorGroundStation` | `skyseed_v2.simulator.topology.GroundStation` |
| `ZoneLayout` | `skyseed_v2.simulator.topology.ZoneLayout` |
| `Zone` | `skyseed_v2.simulator.topology.Zone` |
| `Slicer` | `skyseed_v2.simulator.slicing.Slicer` |
| `PositionModel` | `skyseed_v2.simulator.slicing.PositionModel` |
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

Each mapped class has one detailed maintenance source: the README in the
package that owns its implementation. See
[`maintenance-map.md`](../codex-reference/maintenance-map.md). Lifecycle and
failure contracts are in
[`ownership-lifecycle.md`](../codex-reference/ownership-lifecycle.md) and
[`dynamic-slicing-contracts.md`](../codex-reference/dynamic-slicing-contracts.md).
