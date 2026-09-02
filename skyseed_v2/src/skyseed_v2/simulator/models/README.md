# Built-in physical Models

This package exports two built-in implementations:

## Local assembly / ownership map

Legend: `..|>` is Protocol realization. Each built-in Model implements exactly
one physical-domain role and acquires no planning authority.

```mermaid
classDiagram
    class PositionModel
    class PhysicalLinkModel
    class CircularOrbitPositionModel {
        +compute_positions()
    }
    class GeometricPhysicalLinkModel {
        +compute_observation()
    }
    CircularOrbitPositionModel ..|> PositionModel
    GeometricPhysicalLinkModel ..|> PhysicalLinkModel
```

`CircularOrbitPositionModel` computes complete deterministic positions for the
exact requested timestamp and expected node set. It is owned and called only by
PhysicalWorld.

`GeometricPhysicalLinkModel` consumes one exact PhysicalWorld, PhysicalFrame,
and bounded PhysicalLinkRequest. It evaluates range, Earth-clear LOS, distance,
propagation delay, GroundStation/Zone elevation, and deterministic work counts.
Only feasible candidate facts are returned.

The Model does not choose candidate scope, select operational relationships,
advance acquisition, assign Zone service, plan parameters, compute Routes, or
own history. Canonical K3 and 2+2 are injected planning policy; maximum 4 is a
PhysicalWorld capability.

`_geometric_physical.py` is a private shared optimized calculation core. It
uses the single geometry-owned elevation primitive and static indexes. It owns
no operational state.

Operational reference Models live in `../planning/models.py` because Planner
owns their contracts.

## Detailed public class/member diagrams

These blocks and the exact table below are the maintenance source for every
class owned by this public package. Re-exporting facades do not duplicate the
owner detail.

Legend: `<<...>>` states each public class role and `+` lists its
complete direct public member contract. No connector means no ownership
relationship is asserted by this inventory block.

```mermaid
classDiagram
    class CircularOrbitPositionModel {
        <<reference Position Model>>
        +compute_positions()
    }
    class GeometricPhysicalLinkModel {
        <<reference physical-link Model>>
        +compute_observation()
    }
```

| Diagram class | Public contract | Required public members |
|---|---|---|
| `CircularOrbitPositionModel` | `skyseed_v2.simulator.models.CircularOrbitPositionModel` | `compute_positions()` |
| `GeometricPhysicalLinkModel` | `skyseed_v2.simulator.models.GeometricPhysicalLinkModel` | `compute_observation()` |
