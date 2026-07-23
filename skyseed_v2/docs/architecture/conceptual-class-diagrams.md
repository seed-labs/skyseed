# Conceptual Class Diagrams

## Status

The diagrams describe implemented Simulator Iterations 1 and 2 plus the
implemented and accepted Dynamic Slice A–E main chain. TopologyDifference and
G remain accepted/frozen but not implemented, so the full Dynamic Slice stage
is not complete; Planning/execution remains conceptual.

Status stereotypes are normative: `implemented` means current code,
`implemented and accepted` marks the accepted A–E implementation,
`approved / not implemented` means a frozen public contract awaiting its
authorized implementation Slice, and `conceptual/open` means deferred design.
The detailed frozen dynamic contract is
[`dynamic-slice-public-contract.md`](dynamic-slice-public-contract.md).

## Emulator entities and seedemu integration

```mermaid
classDiagram
    class Emulator {
        <<upstream seedemu>>
        +render()
        +compile(compiler, output)
    }
    class SatelliteBase {
        <<seedemu integration + AS owner>>
        +get_skyseed_autonomous_systems() tuple
    }
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
        +create_satellites(...) tuple
        +get_satellite(satellite_id) Satellite
    }
    class OrbitParameters {
        <<entity-bound value>>
        +altitude_km float
        +inclination_deg float
        +right_ascension_deg float
        +epoch_utc UTC-aware datetime
    }
    class Satellite {
        <<Emulator entity + SEED Node>>
    }
    class GroundStationLocation {
        <<entity-bound value>>
        +latitude_deg float
        +longitude_deg float
        +elevation_m finite WGS84 metres
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
    }
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }

    Emulator --> SatelliteBase : renders
    SatelliteBase *-- SkyseedAutonomousSystem : contains
    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "*" Satellite : owns
    Orbit "1" *-- "1" OrbitParameters : owns
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    GroundStation *-- GroundStationLocation : uses
    Emulator --> SkyseedDockerCompiler : compiles with
```

`Shell`, `Orbit`, `Satellite` and `GroundStation` each have a defining file
with the same primary name under `entities/`. Splitting those files does not
change ownership or create a parent above Shell.

## Emulator → direct generation → Simulator static input

```mermaid
flowchart LR
    SAT[Satellite] --> CS[create_satellite_simulation_input]
    OP[owning OrbitParameters] --> CS
    GS[GroundStation] --> CG[create_ground_station_simulation_input]
    AS[Iterable of SkyseedAutonomousSystem] --> WALK[shared private traversal]
    WALK --> DERIVE[one private NodeId derivation]
    DERIVE --> CA[create_simulator_static_input]
    CS --> SI[SatelliteSimulationInput]
    CG --> GI[GroundStationSimulationInput]
    CA --> SI
    CA --> GI
    SI --> AGG[SimulatorStaticInput]
    GI --> AGG
```

```mermaid
classDiagram
    class NodeId {
        <<cross-layer contract value>>
        +asn
        +entity_kind
        +entity_id
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
        +get(node_id)
        +list_nodes() tuple
    }

    SatelliteSimulationInput "1" *-- "1" NodeId : identifies
    GroundStationSimulationInput "1" *-- "1" NodeId : identifies
    SimulatorStaticInput "1" *-- "*" SatelliteSimulationInput : contains
    SimulatorStaticInput "1" *-- "*" GroundStationSimulationInput : contains
```

The dependency direction is intentional: `simulator_input_generation` depends
on entities, seedemu integration, neutral `node_id` and
`simulator.static_input`; the receiving-side input package depends on none of
the Emulator/SEED layers. The copied objects retain no entity, Registry,
container or Compose reference. `NodeId` is a pure value with no resolution
method, and there is no public/global mapping from it to Emulator entities or
runtime resources.

## Configuration, Emulator creation and input generation

```mermaid
classDiagram
    class StaticEmulatorConfig {
        <<immutable configuration>>
    }
    class Emulator {
        <<upstream seedemu>>
    }
    class SatelliteBase {
        <<seedemu integration + AS owner>>
        +get_skyseed_autonomous_systems() tuple
    }
    class SkyseedAutonomousSystem {
        <<entity owner + seedemu integration>>
    }

    StaticEmulatorConfig --> Emulator : create_static_emulator
    Emulator *-- SatelliteBase : layer
    SatelliteBase *-- SkyseedAutonomousSystem : owns
```

`load_static_emulator_config(path)` creates Config values.
`create_static_emulator(config)` sequences existing public construction calls
and directly returns the upstream Emulator. The caller obtains SatelliteBase
through `Emulator.getLayer("Base")` and invokes the owner's narrow
`get_skyseed_autonomous_systems()` query before the separate
`create_simulator_static_input(...)` call. No operation-result class or new
ownership level exists, and handwritten examples do not require the
file-driven operations.

Any future dynamic-execution resolution will reuse the implemented owner
methods rather than add an index: Base `getAutonomousSystem(asn)`, Skyseed AS
`get_satellite()` / `get_ground_station()` / `get_shell()`, Shell
`get_orbit()` and Orbit `get_satellite()`. No resolution path exists yet. The
conceptual dynamic execution chain is independent from the implemented
`SkyseedDockerCompiler`, which lowers only the rendered static Emulator/SEED
model to Docker/Compose deployment configuration.

## Materialization boundary

```mermaid
classDiagram
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }
    class ComposeProjectLauncher {
        <<materialization integration>>
    }
    class ComposeServiceExpectations {
        <<materialization value>>
    }
    class ComposeMaterializationResult {
        <<materialization operation result>>
    }

    ComposeProjectLauncher --> ComposeServiceExpectations : reads
    ComposeProjectLauncher --> ComposeMaterializationResult : returns
```

The launcher consumes an existing Compose artifact. It is outside entity
construction, Simulator static input, `SkyseedDockerCompiler` lowering and
the conceptual dynamic planning/execution chain. It is not the conceptual
`SimulationRunner`.

## Simulator implemented class map through Iteration 2

```mermaid
classDiagram
    class Simulator {
        <<implemented composition root>>
        +Simulator(definition)
        +get_definition() SimulationDefinition
        +get_timeline() SimulationTimeline
        +create_topology(zone_layout_definition=None, zone_membership_model=None) Topology
        +get_topology() Topology
    }
    class SimulationDefinition {
        <<implemented frozen value>>
        +static_input SimulatorStaticInput
        +timeline SimulationTimeline
    }
    class SimulationTimeline {
        <<implemented frozen value>>
        +start_time datetime
        +end_time datetime or None
        +slice_interval timedelta
        +is_bounded bool
        +iter_timestamps() Iterator
    }
    class SimulatorStaticInput {
        <<implemented immutable boundary input>>
        +get(node_id) SimulationEntityInput
        +get_satellite(node_id) SatelliteSimulationInput
        +get_ground_station(node_id) GroundStationSimulationInput
        +list_nodes() tuple
    }
    class Topology {
        <<implemented immutable aggregate root>>
        +autonomous_systems tuple
        +get_autonomous_system(asn) SkyseedAutonomousSystem
        +get_node(node_id) Satellite or GroundStation
        +get_satellite(node_id) Satellite
        +get_ground_station(node_id) GroundStation
        +get_shell(asn, shell_id) Shell
        +get_orbit(asn, shell_id, orbit_id) Orbit
        +get_zone_layout() ZoneLayout
        +get_zone(zone_id) Zone
        +find_zones(latitude_deg, longitude_deg) tuple
        +resolve_zone(latitude_deg, longitude_deg) Zone
    }
    class SkyseedAutonomousSystem {
        <<implemented Simulator domain object>>
        +asn int
        +shells tuple
        +ground_stations tuple
        +get_shell(shell_id) Shell
        +get_ground_station(node_id) GroundStation
    }
    class Shell {
        <<implemented Simulator domain object>>
        +asn int
        +shell_id str
        +orbits tuple
        +get_orbit(orbit_id) Orbit
    }
    class Orbit {
        <<implemented Simulator domain object>>
        +asn int
        +shell_id str
        +orbit_id str
        +satellites tuple
        +get_satellite(node_id) Satellite
    }
    class Satellite {
        <<implemented Simulator domain object>>
        +node_id NodeId
        +orbit_index int
        +mean_anomaly_deg float
    }
    class GroundStation {
        <<implemented Simulator domain object>>
        +node_id NodeId
        +latitude_deg float
        +longitude_deg float
    }
    class LatitudeLongitudeRectangleDefinition {
        <<implemented frozen construction definition>>
    }
    class ZoneDefinition {
        <<implemented frozen construction definition>>
        +zone_id str
        +priority int
        +geometry LatitudeLongitudeRectangleDefinition
    }
    class ZoneLayoutDefinition {
        <<implemented frozen construction definition>>
        +zones tuple
        +allow_overlap bool
    }
    class LatitudeLongitudeRectangle {
        <<implemented immutable Zone geometry>>
        +contains(latitude_deg, longitude_deg) bool
        +representative_center tuple
    }
    class Zone {
        <<implemented immutable Simulator domain object>>
        +zone_id str
        +priority int
        +contains(latitude_deg, longitude_deg) bool
    }
    class ZoneMembershipModel {
        <<implemented public Protocol>>
        +find_zones(zones, latitude_deg, longitude_deg) Iterable
        +resolve_zone(matches, latitude_deg, longitude_deg) Zone
    }
    class DefaultZoneMembershipModel {
        <<implemented stateless algorithm>>
    }
    class ZoneLayout {
        <<implemented Topology-owned aggregate>>
        +zones tuple
        +allow_overlap bool
        +get_zone(zone_id) Zone
        +find_zones(latitude_deg, longitude_deg) tuple
        +resolve_zone(latitude_deg, longitude_deg) Zone
    }

    Simulator *-- SimulationDefinition : retains
    SimulationDefinition *-- SimulatorStaticInput : static_input
    SimulationDefinition *-- SimulationTimeline : timeline
    Simulator *-- Topology : creates atomically
    Topology "1" *-- "*" SkyseedAutonomousSystem : owns
    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "*" Satellite : owns
    ZoneLayoutDefinition "1" *-- "1..*" ZoneDefinition : construction input
    ZoneDefinition "1" *-- "1" LatitudeLongitudeRectangleDefinition : geometry input
    Simulator --> ZoneLayoutDefinition : consumes during create_topology
    Simulator --> ZoneMembershipModel : optional algorithm injection
    Topology "1" *-- "0..1" ZoneLayout : owns
    ZoneLayout "1" *-- "1..*" Zone : owns
    ZoneLayout o-- ZoneMembershipModel : fixed algorithm
    Zone "1" *-- "1" LatitudeLongitudeRectangle : geometry
    DefaultZoneMembershipModel ..|> ZoneMembershipModel : implements
    Topology --> ZoneLayout : facade delegation
```

`Topology` stores its AS tuple, immutable ASN lookup, one immutable `NodeId`
lookup and at most one ZoneLayout. Node-type facade methods use the unified
node lookup and check the result type. Shell and Orbit queries delegate down
their owner chain; Zone queries delegate to the layout. There are no
Satellite-parent reverse APIs or indexes. Every public tuple and private
`MappingProxyType` lookup references the same object instances. The Definition
is consumed during materialization and is not retained.

The implemented field lists above show current code, including UTC-only
Timeline normalization, Orbit `epoch_utc` and finite GroundStation ellipsoidal
height.

## Approved static/time contract deltas

```mermaid
classDiagram
    class SimulationTimeline {
        <<implemented UTC value>>
        +start_time UTC-aware datetime
        +end_time UTC-aware datetime or None
        +slice_interval timedelta
    }
    class OrbitParameters {
        <<implemented value>>
        +altitude_km float
        +inclination_deg float
        +right_ascension_deg float
        +epoch_utc UTC-aware datetime
    }
    class Orbit {
        <<implemented object>>
        +epoch_utc UTC-aware datetime
    }
    class Satellite {
        <<implemented object>>
        +mean_anomaly_deg float
    }
    class GroundStation {
        <<implemented object>>
        +elevation_m finite WGS84 ellipsoidal metres
    }
    class SphericalCap {
        <<implemented frozen value>>
        +center geographic point
        +angular_radius_deg float
    }
    class ZoneLayout {
        <<implemented aggregate>>
        +find_intersecting_zones(cap) tuple
    }

    OrbitParameters --> Orbit : static projection
    Orbit "1" *-- "*" Satellite : epoch interprets anomaly
    ZoneLayout --> SphericalCap : range query input
```

The displayed `epoch_utc`, aware-only UTC normalization, required
GroundStation height and cap query are implemented and accepted in A–E.

## Simulator approved dynamic-slice boundary map

Nodes marked `implemented` or `implemented and accepted` belong to the
accepted current implementation. TopologyDifference remains
`approved / not implemented` under the frozen contract. Planning and backend
execution remain `conceptual/open`.

```mermaid
classDiagram
direction LR

class Simulator {
  <<implemented composition root>>
}

class SimulationTimeline {
  <<implemented and accepted UTC value>>
}

class Topology {
  <<implemented static aggregate root>>
}

class ZoneLayout {
  <<implemented static Zone aggregate>>
}

class ZoneMembershipModel {
  <<implemented membership Protocol>>
}

class Slicer {
  <<implemented and accepted>>
  +create_next_slice() TopologySlice
  +create_slices(count) tuple
  +create_all_slices() tuple
  +resolve_path(slice_id, source, target) tuple
}

class TopologySlice {
  <<implemented and accepted>>
  +slice_id int
  +timestamp datetime or None
  +positions readonly mapping
  +connection_slice ConnectionSlice
  +route_slice RouteSlice
}

class EarthFixedPosition {
  <<implemented and accepted>>
  +x_m float
  +y_m float
  +z_m float
}

class PositionModel {
  <<implemented and accepted public Protocol>>
}

class ConnectionModel {
  <<implemented and accepted public Protocol>>
}

class RouteModel {
  <<implemented and accepted public Protocol>>
}

class ConnectionSlice {
  <<implemented and accepted>>
}

class RouteSlice {
  <<implemented and accepted>>
}

class TopologyDifference {
  <<approved / not implemented>>
}

class SphericalCap {
  <<implemented and accepted>>
}

class Planning {
  <<conceptual/open>>
}

class TopologyDifferenceWindow {
  <<conceptual/open deferred>>
}

Simulator *-- SimulationTimeline : exact via Definition
Simulator *-- Topology : creates at most one
Simulator *-- Slicer : creates at most one
Topology "1" *-- "0..1" ZoneLayout
ZoneLayout o-- ZoneMembershipModel
ZoneLayout --> SphericalCap : approved range query

Slicer o-- Topology : exact static input
Slicer o-- SimulationTimeline : exact timestamp authority
Slicer o-- PositionModel : exact fixed Model
Slicer o-- ConnectionModel : exact fixed Model
Slicer o-- RouteModel : exact fixed Model
Slicer *-- TopologySlice : atomically publishes
Slicer --> TopologyDifference : approved F comparison

PositionModel --> EarthFixedPosition : produces mapping
ConnectionModel --> ConnectionSlice : produces
RouteModel --> RouteSlice : produces
TopologySlice *-- EarthFixedPosition : positions
TopologySlice *-- ConnectionSlice
TopologySlice *-- RouteSlice

TopologyDifferenceWindow o-- TopologySlice : future base
TopologyDifferenceWindow o-- TopologyDifference : future ordered changes
TopologyDifferenceWindow --> Planning : future input
```

`Topology` does not create TopologySlice or TopologyDifference. Slicer is the
implemented and accepted sole dynamic generation, validation and publication
facade and resolves full paths from exact Topology and one retained Slice;
TopologySlice retains no Topology reference. The accepted F contract assigns
adjacent comparison to Slicer but is not implemented. PositionModel,
ConnectionModel and RouteModel form one fixed call chain and do not own
authoritative Slice history.

## Approved dynamic values and built-in Models

```mermaid
classDiagram
direction LR

class CircularOrbitPositionModel {
  <<implemented and accepted built-in>>
}
class GeometricConnectionModel {
  <<implemented and accepted built-in>>
}
class HopCountRouteModel {
  <<implemented and accepted built-in>>
}
class PositionModel {
  <<implemented and accepted public Protocol>>
}
class ConnectionModel {
  <<implemented and accepted public Protocol>>
}
class RouteModel {
  <<implemented and accepted public Protocol>>
}
class EarthFixedPosition {
  <<implemented and accepted frozen value>>
}
class ConnectionKind {
  <<implemented and accepted enum>>
}
class ConnectionId {
  <<implemented and accepted frozen value>>
}
class DirectionalConnectionParameters {
  <<implemented and accepted frozen value>>
}
class Connection {
  <<implemented and accepted frozen value>>
}
class ConnectionSlice {
  <<implemented and accepted immutable aggregate>>
}
class GroundStationRouteTarget {
  <<implemented and accepted frozen tagged target>>
}
class ZoneRouteTarget {
  <<implemented and accepted frozen tagged target>>
}
class RouteTarget {
  <<implemented and accepted frozen tagged union>>
}
class RouteKey {
  <<implemented and accepted frozen value>>
}
class RouteSlice {
  <<implemented and accepted immutable aggregate>>
}
class TopologySlice {
  <<implemented and accepted immutable aggregate>>
}
class TopologyDifference {
  <<approved immutable change / not implemented>>
}

CircularOrbitPositionModel ..|> PositionModel
GeometricConnectionModel ..|> ConnectionModel
HopCountRouteModel ..|> RouteModel
PositionModel --> EarthFixedPosition : produces readonly mapping
ConnectionId *-- ConnectionKind
Connection *-- ConnectionId
Connection *-- DirectionalConnectionParameters : two directions
ConnectionSlice *-- Connection
GroundStationRouteTarget ..|> RouteTarget
ZoneRouteTarget ..|> RouteTarget
RouteKey *-- RouteTarget
RouteSlice *-- RouteKey
TopologySlice *-- EarthFixedPosition
TopologySlice *-- ConnectionSlice
TopologySlice *-- RouteSlice
TopologyDifference --> ConnectionId : removal and patch identity
TopologyDifference --> RouteKey : route patch identity
```

The built-in Models are explicit choices, never hidden defaults. Same-Orbit
ordering is unique mean anomaly; cross-Orbit structure is unique-RAAN
adjacency plus stable cyclic-offset pairing at Timeline start. Candidate and
geometry evidence remain private ConnectionModel state. RouteSlice stores
next hops, not full paths.

Planning, `TopologyDifferenceWindow`, plan types, backend lowering,
`ExecutionCompiler`, `ExecutableBundle` and `SimulationRunner` remain
conceptual/open and are deliberately omitted from the accepted public-value
diagram.
