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

These logical results are common to both execution modes. At assembly only, omitted
RouteModel selection is the BPF gateway default or empty Linux default. Explicit Models
retain empty, partial or complete arrangements unchanged. Planner does not infer coverage,
complete missing routes or choose daemon policy. Backend representability is checked by
[Compiler](compiler-and-runner.md#supported-route-expressions), not imposed as a universal
Model result restriction.

`RoutePlanningResult.outcomes` is a **replacement snapshot**. Omission removes a
previous outcome, including when the requested demand remains. An empty tuple is
an explicit empty arrangement. Reuse occurs only through the existing optional
Model capability; omission never means “keep the old route”. Duplicate identities
are invalid. `get_route_demands()` exposes the latest evaluated demand inputs.

Demands name a source and `GroundStationRouteDemandTarget`,
`SatelliteRouteDemandTarget(satellite_id, network_scope="")`, or `ZoneRouteDemandTarget`.
The Satellite target validates a satellite-kind `NodeId`; Planner validates actual World
membership even when the requested outcome is omitted, and Compiler independently
validates membership when consuming a detached Plan. `network_scope` participates in
demand identity and deterministic ordering, as for the existing targets. Scope-aware
Models own scope eligibility. A Satellite target names the existing stable node /32;
a Zone target still names its separately allocated service address and current server.
No new addresses, default Satellite demands or all-pairs calculation are introduced.
The supplied hop-count, simple template and computed S3 Models accept explicitly
requested Satellite targets using their existing path algorithms. Other Models remain
responsible for the demands they support.

Supported outcomes are:

- `ReachableRouteOutcome`: next hop and complete ordered, loop-free path over
  operational edges, terminating at the target/current Zone server. GS transit
  restrictions remain. Shared source/target entries must agree: constrained paths
  cannot silently become combinations of arbitrary local alternatives.
- `UnreachableRouteOutcome`: explicit `ZONE_UNSERVED` or `NO_ELIGIBLE_PATH` under
  the selected algorithm's rules. This is a calculation fact, not explicit DROP.
- `LocalRouteOutcome`: one source/target `RouteDisposition` (`FORWARD`, `DROP`,
  `PASS`), ordered `RouteCandidate` values and per-rule `PolicyRecord` data. A
  candidate holds `LogicalExit(connection, source)` and its own `PolicyRecord`.
  FORWARD may have no candidates; DROP/PASS have none. PASS requests Linux
  processing; it is neither confirmed delivery nor protocol installation.

Local arrangements may cover any known node/target and need no complete path or
full demand coverage. Their operational exit references must belong to the source;
foreign nodes, unavailable connections and duplicate candidate exits fail. Empty
or partial arrangements are not repaired. No optimality, connectivity or algorithm
quality proof is required. A record contains integer/boolean leaves, logical
node/exit references and nested records; arbitrary Python objects are excluded.
Plan/evidence codecs use evidence v7, semantic state v4 and Plan/transition v6.
The closed target union change advances these strict schemas and fingerprints.
Regenerate older planning evidence for reading with this contract; retained files
are not rewritten in place.

Layout/range validation belongs to the selected
[DataPlane declaration](dataplane.md#supported-representation), not the RouteModel.

All values participate in state/transition identity and detached Plan serialization.
A policy-only edit produces a change even when candidate next hops stay the same.
Required event edges include referenced exit readiness and withdrawal. Models see
logical planning inputs/results only, never map layouts, live interface IDs,
publication keys, Compiler internals or runtime installation.

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
