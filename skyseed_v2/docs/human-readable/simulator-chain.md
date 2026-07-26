# Simulator, Topology, Slicer, and Models

Legend: `A --> B` means A directly creates B. It does not express a Model
call or retained reference.

```mermaid
flowchart TD
    SIM["Simulator"] -->|creates exactly once| TOP["Topology"]
    TOP --> OWNER["AS → Shell/GS → Orbit → Satellite"]
    TOP --> ZL["optional ZoneLayout → Zone"]
    SIM -->|creates after Topology, exactly once| SLI["Slicer"]
    SLI --> TS["TopologySlice"]
    SLI --> DIFF["TopologyDifference on request"]
```

Legend: every `A ->> B` message is a direct call from A to B; returns are
omitted. Slicer calls all three independent Model roles in numbered order; no
Model calls another Model.

```mermaid
sequenceDiagram
    participant User
    participant Simulator
    participant Slicer
    participant Position
    participant Connection
    participant Route
    User->>Simulator: create_topology()
    User->>Simulator: create_slicer(models)
    User->>Slicer: create_next_slice()
    Slicer->>Position: compute_positions
    Slicer->>Connection: compute_connection_slice
    Slicer->>Route: compute_route_slice
```

Code entry: `src/skyseed_v2/simulator/simulator.py`; readable assembly:
`examples/dynamic_slice_network/simu.py`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
