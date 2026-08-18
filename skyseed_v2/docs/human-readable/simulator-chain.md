# Simulator, Topology, Slicer, Models, and Planner

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
    SIM -->|creates after Slicer, at most once| PLA["Planner"]
    DIFF --> PLA
    PLA --> STATE["continuous private planned state"]
    PLA --> PLAN["continuous non-overlapping immutable Plan"]
    PLAN --> ORDER["minimal Forwarding precedence"]
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
    participant Planner
    participant HorizonPolicy
    participant PlanningModels
    participant CoordinationPolicy
    User->>Simulator: create_topology()
    User->>Simulator: create_slicer(models)
    User->>Slicer: create_next_slice()
    Slicer->>Position: compute_positions
    Slicer->>Connection: compute_connection_slice
    Slicer->>Route: compute_route_slice
    User->>Simulator: create_planner(algorithms)
    Simulator->>Planner: bind exact sibling Slicer
    User->>Planner: synchronize()
    Planner->>Slicer: create_difference / resolve_path
    Planner->>HorizonPolicy: select visible future / publication prefix
    Planner->>PlanningModels: plan with full visible future
    Planner->>CoordinationPolicy: coordinate visible transitions
```

Planner selects visible future and its publication prefix before invoking any
planning Model. Models and Coordination see lookahead beyond the Plan end;
fixed validation then publishes a continuous non-overlapping `(start, end]`
Plan while private planned state continues across the boundary. Reusable
unpublished overlap is extended rather than replaying settled Differences, and
the baseline never resets to raw Slice state. Planner performs formal Slicer
queries itself; the Forwarding Model receives only immutable route-path facts.
Plan's temporal index is an inspection view, while Plan-owned Forwarding
precedences carry only genuine same-time semantic constraints and are not an
execution schedule.

Code entry: `src/skyseed_v2/simulator/simulator.py`; readable assembly:
`examples/mini_satellite_internet/simu.py`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
