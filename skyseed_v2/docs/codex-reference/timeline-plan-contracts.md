# Timeline, formal boundaries, and Plan publication

`SimulationTimeline` owns only a normalized inclusive interval and formal
observation cadence. `iter_timestamps()` is the sole timestamp sequence
authority. `PhysicalWorld.frame_at(timestamp)` is a stateless requested-time
projection and does not own progression.

## Formal timestamps

The Timeline validates aware UTC inputs, positive observation interval,
inclusive start/end alignment, and deterministic iteration. Canonical S3 uses
5-second spacing over ten minutes, yielding 121 formal timestamps.

Every accepted frame/observation pair must match the next formal timestamp.
The producer owns the iterator and calls `frame_at` exactly once for each
formal input. Bounded prehistory timestamps precede the formal start and do not
become published formal transitions.

## Plan transitions

Every formal timestamp produces exactly one immutable `PlanTransition`, even
when no semantic value changes. A transition is the complete payload authority
for Connection, parameter, Zone-service, and Route-outcome changes. Canonical
semantic event references and precedence are derived and validated against it.

`apply_plan_transition(previous_state, transition)` is the authoritative
single-boundary replay operation.
`apply_plan(previous_state_or_none, plan, *, physical_world_fingerprint=...)`
replays the ordered transition set and verifies world lineage plus the output
fingerprint.

## Publication windows

`TimeHorizonPlanningWindowPolicy` defaults to a 50-second planning horizon and
a 20-second publication horizon. The Planner distinguishes:

- physical-observation frontier: last accepted physical input;
- planning frontier: last fully planned formal timestamp;
- publication frontier: last published formal timestamp.

Normal publication requires up to the next 50 seconds of planned context and
emits the earliest contiguous prefix of at most 20 seconds. Exact 5-second
event times are retained. The final incomplete planning window is flushed once
at Timeline end.

Transition sets in consecutive Plans are disjoint and temporally contiguous;
Plan lineage and state fingerprints are continuous. Canonical S3 produces 30
Plans while preserving all 121 formal activation timestamps for Compiler and
Runner.

## Failure behavior

Out-of-order, duplicate, mismatched, or non-formal observations fail before
publication. Invalid Model results, replay mismatch, malformed precedence, or
broken lineage fail rather than publishing partial state. Producer failure
triggers deterministic Planner worker shutdown.
