# Shared values

This package owns narrow cross-production primitives. `NodeId` is the only
public class; `identifier.py` and `time.py` remain internal validation/
normalization boundaries and own no lookup or lifecycle.

## Local assembly / ownership map

`shared` is not a destination for unrelated helpers: a value belongs here only
when multiple production boundaries need the same neutral contract and no
domain owner can own it.

Legend: `-->` means the named production boundary consumes the neutral shared
value or normalization capability; it is not ownership.

```mermaid
flowchart LR
    NODE["NodeId"] --> SAT_INPUT["SatelliteSimulationInput"]
    NODE --> GS_INPUT["GroundStationSimulationInput"]
    NODE --> TOPOLOGY["Topology node identities"]
    NODE --> SLICING["Connection / Route / Slice values"]
```

Legend: `-->` means an internal validation/normalization primitive supplies
the public/shared consumer; it creates no public owner.

```mermaid
flowchart LR
    IDENTIFIER["identifier.py"] --> NODE["NodeId"]
    TIME["time.py"] --> TIME_USERS["configuration / geometry / Simulator"]
```

## Detailed public class/member diagrams

Legend: the class block is the complete local public member contract; no
relationship is implied.

```mermaid
classDiagram
    class NodeId {
        <<immutable structural identity>>
        +asn
        +entity_kind
        +entity_id
        +value
    }
```

| Diagram class | Public contract | Required public members |
|---|---|---|
| `NodeId` | `skyseed_v2.shared.NodeId` | `asn`, `entity_kind`, `entity_id`, `value` |

## Entry points

`node_id.py` owns the public `NodeId`; `identifier.py` and `time.py` remain
private validation/normalization boundaries.

## Dependencies

This package depends only on standard-library value types. Configuration,
Simulator input/topology/slicing, and geometry consume its narrow primitives.

## Examples and tests

All Simulator examples carry `NodeId` through the boundary.
`test_simulation_node_identity.py`, static-input tests, Timeline tests, and
architecture-boundary tests cover it.

## Maintenance checklist

Update the global object model only if `NodeId`'s system role changes; update
the local map and detailed diagram, public imports, identity/time contracts,
and owner tests when its public members change.
