# Planner and Plan contracts

`skyseed_v2.simulator.planning.Planner` is the sole authoritative operational
planner. It is a real state owner, not a forwarding facade.

## Construction

The public constructor receives keyword-only roles equivalent to:

```python
Planner(
    *,
    physical_world,
    observation_interval,
    inter_satellite_model,
    ground_station_model,
    zone_service_model,
    parameter_model,
    route_model,
    coordination_policy,
    route_demands=(),
    planning_window_policy=TimeHorizonPlanningWindowPolicy(),
    execution_observer=None,
)
```

`Simulator.create_planner(...)` is the composition-root path. It also retains
the exact `PhysicalLinkModel` used by the external realization producer. Each
Model/policy role must be a distinct instance.

## Lifecycle

The Planner exposes these semantic operations:

- `required_prehistory`: bounded warm-input duration;
- `create_physical_link_request()`: candidate scope for the next frame;
- `prime(frame, observation, formal_start=...)`: warm lifecycle state without
  publishing a formal transition;
- `observe(frame, observation)`: accept one ordered formal pair and return zero
  or more newly publishable Plans;
- `finish(end_timestamp=...)`: flush the final contiguous prefix exactly once;
- `get_state()`, `get_frontiers()`, and bounded inspection/evidence access;
- `release_published_through(plan_id)`: discard releasable publication data;
- Route-demand replacement at the defined boundary;
- `shutdown(wait=True)`: terminate owned Route workers deterministically.

The external producer owns time progression. Planner never advances the
PhysicalWorld by itself.

## Operational planning order

For each formal frame the Planner:

1. validates matching frame and physical-link observation provenance;
2. selects deterministic ISL candidates under PhysicalWorld capability;
3. advances selected/acquiring/operational ISL lifecycle;
4. selects GroundStation accesses independently;
5. assigns or unassigns Zone service;
6. computes effective parameters with accumulated deadband memory;
7. recomputes Route outcomes only when the operational graph, service targets,
   or Route demands change;
8. derives one replay-complete `PlanTransition`;
9. obtains precedence only from CoordinationPolicy;
10. applies the common transition function and advances frontiers;
11. publishes when the window policy permits.

Candidates and acquisition evidence remain bounded inspection facts. Only
operational outputs enter `PlannedOperationalState` and transitions.

The reference GroundStation Model consumes only sparse feasible candidates from
`PhysicalLinkObservation`; it never reconstructs the GroundStation×Satellite
universe or geometry. Immutable capacity lookups are prepared once per exact
PhysicalWorld. Quality-ranked allocation solves one global sparse capacitated
assignment. Its indexed residual representation retains the accepted aggregate
rank, Satellite-order, and successive-shortest-path tie identity.
Planner observation separates selection/ranking, fail-closed candidate
validation, and evidence projection costs.

## Public values

`PlannedOperationalState` is one immutable complete output at an exact
timestamp. It contains operational Connections with effective parameters,
Zone service assignments including unserved state, and complete reachable or
explicitly unreachable Route outcomes.

`PlanTransition` is the only semantic change payload and contains:

- `timestamp`;
- `established_connections`;
- `withdrawn_connection_ids`;
- `parameter_updated_connections`;
- `zone_service_transitions`;
- `route_outcome_transitions`.

An empty transition is valid and preserves a formal activation timestamp.

`Plan` contains:

```text
plan_id
previous_plan_id
physical_world_fingerprint
input_state_fingerprint
output_state_fingerprint
observation_interval
start_timestamp
end_timestamp
transitions
semantic_event_precedences
```

The first Plan has no predecessor or input fingerprint and bootstraps from the
explicit empty state at formal start. Every adjacent transition is separated by
exactly the positive, versioned `observation_interval`. Later Plans form an
immediate predecessor chain, consume the preceding output fingerprint, and
start exactly one observation interval after the preceding Plan ends.

`semantic_event_references(transition)` derives event identity from the payload.
`SemanticEventPrecedence` refers to those immutable identities. Independent
event payload storage is not part of the contract.

## Replay and fingerprints

`apply_plan_transition` performs the authoritative one-boundary semantic
operation. `apply_plan` replays a complete Plan and verifies its interval and
lineage. Planner publication, Compiler validation, detached Tools, and
qualification use this same defined contract.

`create_plan(...)` rejects invalid world/predecessor fingerprints, invalid
bootstrap lineage, non-positive cadence, non-UTC-normalizable timestamps,
empty or cadence-gapped transitions, precedence endpoints outside the derived
event set, duplicate precedences, and cyclic precedence graphs before it
publishes a canonical content-derived Plan identifier.

`planned_operational_state_fingerprint` and
`plan_transition_fingerprint` are versioned deterministic semantic hashes.
Canonical construction normalizes ordering and rejects duplicate identities.

## Window semantics

`TimeHorizonPlanningWindowPolicy` defaults to:

- `planning_horizon = 50 seconds`;
- `publication_horizon = 20 seconds`;
- `maximum_prehistory = 20 seconds`.

Normal publication first plans up to the 50-second lookahead and then publishes
the earliest contiguous prefix of at most 20 seconds. Physical-observation,
planning, and publication frontiers have distinct meanings. The final short
window is flushed once. Exact 5-second event timestamps remain unchanged.

Canonical 10-minute S3 has 121 disjoint formal transitions across 30 Plans.

## Evidence

`PublishedPlanningEvidence` binds one Plan to transition fingerprints, physical
work counts, bounded candidate quality/count/reason histograms, exact selected
ISL/GS/Zone facts, compact inspection summaries, and frontiers. It deliberately
does not retain every feasible or rejected candidate identity. Versioned codecs
in `planning.evidence` serialize only semantic values. They do not persist live
Models, workers, or publication buffers.

## Failure and boundedness

Out-of-order timestamps, frame/observation mismatch, candidate spoofing,
capability violation, malformed Model output, invalid precedence, broken
lineage, or replay mismatch fails atomically. Unpublished transitions,
inspection evidence, candidate state, and Plan queues remain bounded. Route
workers are reused and shut down after success or failure.
