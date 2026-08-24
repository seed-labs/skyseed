# Simulator, Topology, Slicer, Models, and Planner

Legend: every edge in this diagram carries its exact relationship. Topology, Slicer,
and Planner are peer-level components inside the Simulator domain; `Simulator`
creates and owns each of them. Planner is not a Slicer child, and Difference is
an immutable on-request value rather than a retained component.

```mermaid
flowchart TD
    SIM["Simulator"] -->|creates and owns exactly once| TOP["Topology"]
    TOP -->|owns| OWNER["AS → Shell/GS → Orbit → Satellite"]
    TOP -->|owns when configured| ZL["ZoneLayout → Zone"]
    SIM -->|creates and owns after Topology, exactly once| SLI["Slicer"]
    SIM -->|creates and owns after Slicer, at most once| PLA["Planner"]
    SLI -->|calls Models; produces and retains| TS["TopologySlice"]
    PLA -->|calls create_difference on exact sibling| SLI
    SLI -->|produces on request; does not retain| DIFF["TopologyDifference"]
    DIFF -->|consumed by synchronize| PLA
    PLA -->|owns| STATE["continuous private planned state"]
    PLA -->|synchronize produces; retains current| PLAN["continuous non-overlapping immutable Plan"]
    PLAN -->|owns| ORDER["minimal Forwarding precedence"]
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
    participant WindowPolicy
    participant PlanningModels
    participant CoordinationPolicy
    User->>Simulator: create_topology()
    User->>Simulator: create_slicer(models)
    User->>Simulator: optionally create_planner(algorithms) before real Slices
    Simulator->>Planner: bind exact sibling Slicer
    loop Slice generation and Planner synchronization may interleave
        User->>Slicer: create_next_slice()
        Slicer->>Position: compute_positions
        Slicer->>Connection: compute_connection_slice
        Slicer->>Route: compute_route_slice
        User->>Planner: synchronize()
        Planner->>Slicer: create_difference / resolve_path
        Planner->>WindowPolicy: select planning window / publication window
        Planner->>PlanningModels: plan with full planning window
        Planner->>CoordinationPolicy: coordinate planning-window transitions
    end
```

The strict construction prerequisite is `Topology → Slicer → optional
Planner`. A Planner may therefore exist while the Slicer retains only synthetic
Slice 0; `synchronize()` cannot publish a Plan until at least one real adjacent
Slice Difference is available.

Planner selects a planning window from available Differences and then its
publication window before invoking any planning Model. Models and Coordination
see the unpublished planning-window portion beyond the Plan end;
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
