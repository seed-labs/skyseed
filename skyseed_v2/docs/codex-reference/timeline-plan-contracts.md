# Timeline, formal boundaries, and Plan publication

`SimulationTimeline` owns only a normalized finite interval and formal
observation cadence. Both `start_time` and `end_time` are required datetimes;
there is no optional unbounded end or `is_bounded` member.
`iter_timestamps()` is the sole timestamp sequence authority.
`PhysicalWorld.frame_at(timestamp)` is a stateless requested-time
projection and does not own progression.

## Formal timestamps

The Timeline normalizes aware datetime inputs to UTC, requires a positive
observation interval and an end no earlier than the start, and iterates
deterministically. It yields `start_time + k * observation_interval` while the
timestamp is at most `end_time`; an off-cadence end does not add an extra
timestamp. Canonical S3 has an aligned end and uses 5-second spacing over ten
minutes, yielding 121 formal timestamps.

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

## Publication batches

`FrameBatchPlanningWindowPolicy(frames_per_batch=32)` publishes a complete
batch immediately. Physical-observation, planning and publication frontiers
retain their distinct meanings; no independent 50-second wait remains.
The final short batch is flushed once. Canonical S3 produces four disjoint
Plans of 32/32/32/25 transitions, preserving all 121 exact activation times.

Preparation history and physical prediction tail are dependency-derived:
round each preparation duration up to formal cadence, take the largest as
history, and add each kind's minimum useful service for its admission tail.
Only physical queries extend past the formal end. Stable all-future proofs do
not invent disappearance at that end. Planner publication reuses its constructed
state; replay functions remain authoritative for external construction and
independent verification.

## Failure behavior

Out-of-order, duplicate, mismatched, or non-formal observations fail before
publication. Invalid Model results, replay mismatch, malformed precedence, or
broken lineage fail rather than publishing partial state. Producer failure
triggers deterministic Planner worker shutdown.
