# Planner state and Plans

Planner changes need to preserve coherent state adoption, formal time and semantic
replay. This reference describes those obligations and route results, followed by
the replaceable policies used in the default assembly.

## Current state and formal time

Planner alone owns operational state, acquisition reservations, adopted parameter memory,
frontiers and bounded unpublished changes. Position/candidate facts, selections in
preparation, operational Connections, compiled intentions and realized kernel state remain
separate. A selected connection reserves endpoint capacity during acquisition but provides
service only after preparation completes. Disappearance releases its reservation; a retained
selection keeps its original start. Replacement with full endpoints requires break-before-make.

`SimulationTimeline` is finite and immutable, with aware start/end and positive cadence.
Every formal timestamp produces one `PlanTransition`, including empty transitions.
Prehistory warms preparation without becoming formal history; it is the maximum acquisition
dependency rounded to cadence. Prediction tails are separate [Model capabilities](model-interfaces.md).

`PlannerConfiguration` owns acquisition durations (default five seconds each) and
`frames_per_batch` (default 32). Batch size changes publication granularity, not observation
cadence. `flush()` publishes the remaining prefix without finishing or resetting state;
`finish(end_timestamp=...)` ends planning. Retained requests use flush between ranges.

## Plans and dependencies

A Transition owns both the changes and their event dependencies. Required ordering is
derived from those changes before adoption. It preserves readiness and withdrawal semantics
at the same timestamp; it does not choose a new timestamp or invoke an external policy.

`PlannedOperationalState` is a complete timestamped operational value: Connections,
effective parameters, Zone assignments and explicit route outcomes. It excludes physical
frames, lifecycle timers, algorithm caches, processes and Tool schemas.

A transition carries replay-complete establishment/withdrawal, parameter, Zone-service and
route changes. Canonical event references derive from its payload. Transition-owned semantic
precedences order those references within the existing timestamp. Explicit dependency values, including indexed codec
input, must preserve that ordering and pass structural checks; they are not a policy hook.

A Plan identifies its world, immediate predecessor and input/output state fingerprints,
cadence, exact start/end and ordered transitions. Bootstrap has no predecessor/input
fingerprint and applies to empty state. Every successor begins one cadence after the prior
end. `apply_plan_transition` and `apply_plan` define semantic replay for construction,
Compiler and readers. Semantic fingerprints are schema-tagged and order-canonical.
Publication reuses validated state; it does not reconstruct another authoritative state.

## Routes

Demands name a source and GroundStation or Zone target. Reachable outcomes carry the next
hop and ordered path over operational edges. GSs are terminals, not incidental backbone
transit. Zone targets resolve through the current serving satellite. Unreachable outcomes
are explicit: `ZONE_UNSERVED` when service is absent, otherwise `NO_ELIGIBLE_PATH` under the
chosen Model's rules. No optimality proof is required. Duplicate/foreign identities,
invalid paths/next hops and incomplete outcomes fail before publication.

`route_demands=None` permits the Model's optional demand factory; an explicit empty sequence
stays empty. Demand creation precedes optional reuse. Custom routing runs every formal step
unless it explicitly opts into reuse; parameter dependence defaults to conservative.
The [Model contract](model-interfaces.md#routing-and-retry) defines those capabilities.

## Default policies

The defaults use retain-first ISL selection and coverage-first GS allocation. The latter
optimizes GS coverage, distinct satellite coverage and total assignments, then continuity and
deterministic quality ranking, over the admitted query graph. Equal-objective ties can differ
from cold allocation and affect later continuity; this is not full-physical-graph optimality.
Default admission prefers known preparation-plus-service coverage (ISL 180 seconds, GS 60
seconds beyond preparation) but unknown future coverage preserves current feasibility.

Default delay adoption compares accumulated relative change against the last effective value:
`abs(current-effective)/abs(effective) >= 0.10`. A zero baseline changes for any nonzero value.
A 120-second refresh adopts the current value on the first eligible formal sample; unchanged
refresh updates memory without a spurious event. These are simulation settings, not measured
commercial hardware timings. Model replacement need not retain these algorithms.
