# Dynamic slicing contracts and values

This package owns the three replaceable Model Protocols, the `Slicer`
lifecycle, published `TopologySlice` values, route/connection values, and
the exact `TopologyDifference` value contract. Slicer creates each Difference
on request from retained adjacent Slices but does not retain it; its caller
owns the returned immutable value. Built-in algorithms are owned by `../models/`.

## Local assembly / ownership map

The local assembly map is split by UML relation so each connector has one
meaning. Legend: `*--` is retained immutable composition.

```mermaid
classDiagram
    class Slicer
    class TopologySlice
    class EarthFixedPosition
    class ConnectionSlice
    class RouteSlice

    Slicer "1" *-- "1..*" TopologySlice : retains Slice 0..N
    TopologySlice "1" *-- "0..*" EarthFixedPosition : positions
    TopologySlice "1" *-- "1" ConnectionSlice
    TopologySlice "1" *-- "1" RouteSlice
```

Legend: `-->` is an exact retained reference, not ownership.

```mermaid
classDiagram
    class Slicer
    class Topology
    class SimulationTimeline

    Slicer "1" --> "1" Topology : exact Topology
    Slicer "1" --> "1" SimulationTimeline : exact Timeline
```

Legend: `..>` is a numbered call to an injected Protocol role. The names are
Protocols, not built-in implementations.

```mermaid
classDiagram
    class Slicer
    class PositionModel
    class ConnectionModel
    class RouteModel

    Slicer ..> PositionModel : 1 calls
    Slicer ..> ConnectionModel : 2 calls
    Slicer ..> RouteModel : 3 calls
```

Legend: `..>` is an on-request creation/read dependency; it does not retain a
`TopologyDifference`.

```mermaid
classDiagram
    class Slicer
    class TopologySlice
    class TopologyDifference

    Slicer ..> TopologySlice : creates and reads retained adjacent slices
    Slicer ..> TopologyDifference : creates on request
```

`Slicer` creates every complete `TopologySlice`, retains Slice 0..N, and
reads retained `TopologySlice` N and N+1 when creating a requested
`TopologyDifference`.

Standalone local values expanded below: `Connection`, `ConnectionId`,
`ConnectionKind`, `DirectionalConnectionParameters`, `RouteKey`,
`GroundStationRouteTarget`, `ZoneRouteTarget`,
`DirectionalConnectionParametersPatch`, `ZoneServiceAssignmentPatch`,
`RoutePatch`, `DifferenceOperation`, and `ConnectionDirection`.

## Detailed public class/member diagrams

Legend: this class inventory has no connector; it expands the owner facade and
the three Protocol roles from the local assembly map.

```mermaid
classDiagram
    class Slicer {
        <<owner facade>>
        +create_next_slice()
        +create_slices()
        +create_all_slices()
        +get_slice()
        +get_slice_at()
        +get_latest_slice()
        +get_latest_slice_id()
        +get_latest_timestamp()
        +get_next_timestamp()
        +is_exhausted()
        +get_retained_slice_ids()
        +get_retained_slice_id_range()
        +create_difference()
        +get_topology()
        +get_timeline()
        +get_position_model()
        +get_connection_model()
        +get_route_model()
        +resolve_path()
    }
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
```

Legend: `*--` is immutable Slice composition.

```mermaid
classDiagram
    class TopologySlice {
        <<published aggregate>>
        +slice_id
        +timestamp
        +positions
        +connection_slice
        +route_slice
        +get_position()
    }
    class EarthFixedPosition
    class ConnectionSlice {
        <<connection aggregate>>
        +connections
        +zone_service_assignments
        +get_connection()
        +get_connections_for_node()
        +get_connection_for_endpoints()
        +get_service_satellite()
        +get_served_zones()
    }
    class RouteSlice {
        <<route aggregate>>
        +routes
        +get_next_hop()
        +get_routes_from()
    }

    TopologySlice "1" *-- "0..*" EarthFixedPosition : positions
    TopologySlice "1" *-- "1" ConnectionSlice
    TopologySlice "1" *-- "1" RouteSlice
```

Legend: `*--` is immutable Connection-value composition.

```mermaid
classDiagram
    class ConnectionSlice
    class Connection {
        <<effective connection value>>
        +connection_id
        +endpoint_a_to_b
        +endpoint_b_to_a
        +parameters_from()
    }
    class ConnectionId {
        <<stable value identity>>
        +kind
        +endpoint_a
        +endpoint_b
    }
    class ConnectionKind {
        <<enumeration>>
        +INTER_SATELLITE
        +GROUND_STATION_ACCESS
    }
    class DirectionalConnectionParameters {
        <<directional value>>
        +bandwidth_bps
        +delay_us
        +packet_loss_ppm
    }

    ConnectionSlice "1" *-- "0..*" Connection
    Connection "1" *-- "1" ConnectionId
    ConnectionId "1" *-- "1" ConnectionKind
    Connection "1" *-- "2" DirectionalConnectionParameters
```

`RouteTarget` is the public type alias
`GroundStationRouteTarget | ZoneRouteTarget`.

Legend: this route-value inventory has no connector; target alternatives are
expressed by the public type alias.

```mermaid
classDiagram
    class RouteKey {
        <<route identity value>>
        +source
        +target
    }
    class GroundStationRouteTarget {
        <<route target value>>
        +ground_station_id
    }
    class ZoneRouteTarget {
        <<route target value>>
        +zone_id
    }
```

Legend: `*--` is immutable Difference patch-value composition.

```mermaid
classDiagram
    class TopologyDifference {
        <<adjacent-slice difference>>
        +previous_slice_id
        +previous_timestamp
        +current_slice_id
        +current_timestamp
        +added_connections
        +removed_connection_ids
        +connection_parameter_patches
        +zone_service_assignment_patches
        +route_patches
        +is_empty
    }
    class DirectionalConnectionParametersPatch {
        <<sparse connection patch>>
        +connection_id
        +direction
        +bandwidth_bps
        +delay_us
        +packet_loss_ppm
    }
    class ZoneServiceAssignmentPatch {
        <<sparse assignment patch>>
        +zone_id
        +operation
        +service_satellite_id
    }
    class RoutePatch {
        <<sparse route patch>>
        +route_key
        +operation
        +next_hop
    }
    class DifferenceOperation {
        <<enumeration>>
        +ADDED
        +CHANGED
        +REMOVED
    }
    class ConnectionDirection {
        <<enumeration>>
        +ENDPOINT_A_TO_B
        +ENDPOINT_B_TO_A
    }

    TopologyDifference "1" *-- "0..*" DirectionalConnectionParametersPatch
    TopologyDifference "1" *-- "0..*" ZoneServiceAssignmentPatch
    TopologyDifference "1" *-- "0..*" RoutePatch
    DirectionalConnectionParametersPatch "1" *-- "1" ConnectionDirection
    ZoneServiceAssignmentPatch "1" *-- "1" DifferenceOperation
    RoutePatch "1" *-- "1" DifferenceOperation
```

| Diagram name | Public contract | Members maintained here |
|---|---|---|
| `Slicer` | `skyseed_v2.simulator.slicing.Slicer` | `create_next_slice()`, `create_slices()`, `create_all_slices()`, `get_slice()`, `get_slice_at()`, `get_latest_slice()`, `get_latest_slice_id()`, `get_latest_timestamp()`, `get_next_timestamp()`, `is_exhausted()`, `get_retained_slice_ids()`, `get_retained_slice_id_range()`, `create_difference()`, `get_topology()`, `get_timeline()`, `get_position_model()`, `get_connection_model()`, `get_route_model()`, `resolve_path()` |
| `PositionModel` | `skyseed_v2.simulator.slicing.PositionModel` | `compute_positions()` |
| `ConnectionModel` | `skyseed_v2.simulator.slicing.ConnectionModel` | `compute_connection_slice()` |
| `RouteModel` | `skyseed_v2.simulator.slicing.RouteModel` | `compute_route_slice()` |
| `TopologySlice` | `skyseed_v2.simulator.slicing.TopologySlice` | `slice_id`, `timestamp`, `positions`, `connection_slice`, `route_slice`, `get_position()` |
| `ConnectionSlice` | `skyseed_v2.simulator.slicing.ConnectionSlice` | `connections`, `zone_service_assignments`, `get_connection()`, `get_connections_for_node()`, `get_connection_for_endpoints()`, `get_service_satellite()`, `get_served_zones()` |
| `RouteSlice` | `skyseed_v2.simulator.slicing.RouteSlice` | `routes`, `get_next_hop()`, `get_routes_from()` |
| `Connection` | `skyseed_v2.simulator.slicing.Connection` | `connection_id`, `endpoint_a_to_b`, `endpoint_b_to_a`, `parameters_from()` |
| `ConnectionId` | `skyseed_v2.simulator.slicing.ConnectionId` | `kind`, `endpoint_a`, `endpoint_b` |
| `ConnectionKind` | `skyseed_v2.simulator.slicing.ConnectionKind` | `INTER_SATELLITE`, `GROUND_STATION_ACCESS` |
| `DirectionalConnectionParameters` | `skyseed_v2.simulator.slicing.DirectionalConnectionParameters` | `bandwidth_bps`, `delay_us`, `packet_loss_ppm` |
| `RouteKey` | `skyseed_v2.simulator.slicing.RouteKey` | `source`, `target` |
| `GroundStationRouteTarget` | `skyseed_v2.simulator.slicing.GroundStationRouteTarget` | `ground_station_id` |
| `ZoneRouteTarget` | `skyseed_v2.simulator.slicing.ZoneRouteTarget` | `zone_id` |
| `TopologyDifference` | `skyseed_v2.simulator.slicing.TopologyDifference` | `previous_slice_id`, `previous_timestamp`, `current_slice_id`, `current_timestamp`, `added_connections`, `removed_connection_ids`, `connection_parameter_patches`, `zone_service_assignment_patches`, `route_patches`, `is_empty` |
| `DirectionalConnectionParametersPatch` | `skyseed_v2.simulator.slicing.DirectionalConnectionParametersPatch` | `connection_id`, `direction`, `bandwidth_bps`, `delay_us`, `packet_loss_ppm` |
| `ZoneServiceAssignmentPatch` | `skyseed_v2.simulator.slicing.ZoneServiceAssignmentPatch` | `zone_id`, `operation`, `service_satellite_id` |
| `RoutePatch` | `skyseed_v2.simulator.slicing.RoutePatch` | `route_key`, `operation`, `next_hop` |
| `DifferenceOperation` | `skyseed_v2.simulator.slicing.DifferenceOperation` | `ADDED`, `CHANGED`, `REMOVED` |
| `ConnectionDirection` | `skyseed_v2.simulator.slicing.ConnectionDirection` | `ENDPOINT_A_TO_B`, `ENDPOINT_B_TO_A` |

## Entry points

Code entries are `slicer.py`, `position.py`, `connection.py`, `route.py`,
`slice.py`, `difference.py`, and `_routing_policy.py`.

## Dependencies

`Slicer` references `../topology/` and `../timeline.py`, uses values from
`../geometry/`, and calls only the three Protocol roles declared here.
Built-in realizations live in `../models/`; no Model calls another Model.

## Examples and tests

The readable assembly is `examples/mini_satellite_internet/simu.py`. Focused
tests are `tests/unit/test_slice_difference_main_chain.py` and
`tests/unit/test_topology_difference.py`; Tool tests verify read-only
consumption.

## Maintenance checklist

When a contract, retained-state rule, Difference rule, or Protocol changes,
update this README, the Dynamic Slicing/failure contracts, the global object
model when cross-package relations change, public imports, Tool projections,
and the focused tests above.
