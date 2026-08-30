# Simulator physical foundation and temporary operational seam

The accepted destination separates physical, planned-operational, and realized
runtime state:

```text
Topology / PhysicalFrame
→ Network Planner
→ PlannedOperationalState
→ Plan
→ Compiler / Runner
→ RealizedRuntimeState
```

Round 1 establishes only the physical foundation. Legend: every edge in the
diagram carries its exact current relationship. The legacy Slicer and current
Planner remain Simulator-owned peer components only as an explicit temporary
seam. Planner is not a Slicer child, and Difference is an immutable on-request
legacy value rather than a retained component.

```mermaid
flowchart TD
    SIM["Simulator"] -->|creates and owns exactly once| TOP["Topology"]
    TOP -->|owns| OWNER["AS → Shell/GS → Orbit → Satellite"]
    TOP -->|owns when configured| ZL["ZoneLayout → Zone"]
    TOP -->|owns physical projection role| POS["PositionModel"]
    TIME["requested Timeline timestamp"] -->|supplied to frame_at| TOP
    TOP -->|deterministically produces| FRAME["PhysicalFrame<br/>timestamp + Positions"]
    SIM -->|temporarily creates and owns after Topology| SLI["legacy Slicer"]
    FRAME -->|Positions consumed by| SLI
    SLI -->|calls legacy Connection/Route Models; produces and retains| TS["legacy TopologySlice"]
    SIM -->|temporarily creates and owns after Slicer| PLA["current Planner"]
    PLA -->|calls create_difference on exact sibling| SLI
    SLI -->|produces on request; does not retain| DIFF["legacy TopologyDifference"]
    DIFF -->|consumed by synchronize| PLA
    PLA -->|owns| STATE["continuous private planned state"]
    PLA -->|synchronize produces; retains current| PLAN["continuous non-overlapping immutable Plan"]
    PLAN -->|owns| ORDER["minimal Forwarding precedence"]
```

Legend: every `A ->> B` message is a direct call from A to B; returns are
omitted. Topology calls the physical PositionModel. The temporary Slicer then
calls only the still-legacy Connection and Route Model roles; no Model calls
another Model.

```mermaid
sequenceDiagram
    participant User
    participant Simulator
    participant Timeline
    participant Topology
    participant Position
    participant Slicer as legacy Slicer
    participant Connection
    participant Route
    participant Planner as current Planner
    participant WindowPolicy
    participant PlanningModels
    participant CoordinationPolicy
    User->>Simulator: create_topology(position_model)
    Simulator->>Topology: bind PositionModel during materialization
    User->>Simulator: create_slicer(connection_model, route_model)
    User->>Simulator: optionally create_planner(algorithms) before real Slices
    Simulator->>Planner: bind exact sibling Slicer
    loop Slice generation and Planner synchronization may interleave
        User->>Slicer: create_next_slice()
        Slicer->>Timeline: request next legacy progression timestamp
        Slicer->>Topology: frame_at(timestamp)
        Topology->>Position: compute_positions(topology, timestamp)
        Slicer->>Connection: compute_connection_slice(PhysicalFrame.positions)
        Slicer->>Route: compute_route_slice
        User->>Planner: synchronize()
        Planner->>Slicer: create_difference / resolve_path
        Planner->>WindowPolicy: select planning window / publication window
        Planner->>PlanningModels: plan with full planning window
        Planner->>CoordinationPolicy: coordinate planning-window transitions
    end
```

The strict current construction prerequisite is `Topology(PositionModel) →
legacy Slicer(ConnectionModel, RouteModel) → optional current Planner`.
`Topology.frame_at(timestamp)` is independently callable and does not advance a
time cursor. `PhysicalFrame.timestamp` is its physical domain identity; it has
no Slice/frame sequence ID and contains no Connection, Route, Zone-serving,
Difference, planning, Plan, or runtime state. The Slicer's timeline iterator
and old Slice IDs remain visible migration debt in this round.

A current Planner may exist while the legacy Slicer retains only synthetic
Slice 0; `synchronize()` cannot publish a Plan until at least one real adjacent
legacy Slice Difference is available.

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

These Planner semantics are preserved during Round 1; they do not grant the
legacy seam long-term ownership. Connection operational planning, effective
Parameter/resource planning, Route planning, Coordination, and
`PlannedOperationalState` move into the Network Planner in later rounds. Once
that move is complete, Slicer and the old mixed `TopologySlice` /
`TopologyDifference` path are deleted completely.

Code entry: `src/skyseed_v2/simulator/simulator.py`; readable assembly:
`examples/mini_satellite_internet/simu.py`.
Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
