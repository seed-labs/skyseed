# Accepted design: physical state to realized runtime

This document is the highest repository-local architecture anchor for the
current Skyseed v2 production chain.

```text
SimulationTimeline
→ PhysicalWorld.frame_at(timestamp)
→ PhysicalFrame
→ PhysicalLinkModel.compute_observation(...)
→ PhysicalLinkObservation
→ Planner
→ PlannedOperationalState
→ Plan
→ compile_bpf(plan, physical_world, previous_realization)
→ BpfCompiledRealization
→ BpfRunner
→ RealizedRuntimeState
```

There is one executable architecture. The Simulator composes the Timeline,
PhysicalWorld, physical-link Model, and one public Planner. Cross-domain
experiment orchestration owns the incremental realization producer. The
Compiler and Runner remain downstream backend boundaries.

## State separation

The system keeps four different truths explicit:

1. physical truth: `PhysicalWorld`, `PhysicalFrame`, and sparse
   `PhysicalLinkObservation` candidates;
2. planned truth: `PlannedOperationalState`, including operational
   Connections, Zone service, effective parameters, and explicit Route
   outcomes;
3. compiled intended truth: `BpfCompiledRealization`, including semantic
   lineage, formal activation boundaries, and backend intentions;
4. realized truth: `RealizedRuntimeState`, observed and owned by `BpfRunner`.

No object in one layer silently substitutes for another. A physically feasible
candidate is not an operational Connection. A compiled intention is not proof
of realized kernel state.

## Physical ownership

`PhysicalWorld` is the authoritative physical aggregate. It owns static
Satellite, GroundStation, orbit, shell, Zone-layout, capability, and
PositionModel identity. `frame_at(timestamp)` returns exactly one immutable
`PhysicalFrame` containing the requested timestamp and immutable positions.

`PhysicalLinkModel` consumes an exact PhysicalWorld, frame, and bounded request.
It returns sparse feasible facts only: inter-satellite candidates,
GroundStation access candidates, Zone service candidates, and deterministic
work counts. It does not select operational links, advance acquisition, assign
Zone service, plan parameters, compute Routes, or own time progression.

Canonical S3 retains these frozen assumptions:

- 720 Satellites, 355 GroundStations, and 732 Zones;
- maximum simultaneous ISLs per Satellite: 4;
- desired policy mix: 2 same-plane plus 2 cross-plane;
- bounded cross-plane candidate scope: K3;
- deterministic retain-first selection;
- configurable ISL acquisition reference: 20 seconds;
- bounded sampled prehistory for warm initialization;
- accepted warm-oracle comparison: 714 cross-plane selections and 12
  underfilled Satellites at its defined boundary.

The value `4` is an explicit scenario capability assumption. The 2+2/K3 rule is
planning policy, not physical hardware identity.

## Planner ownership

`Planner` is the sole operational state owner. Constructor injection supplies
the exact PhysicalWorld; ISL, GroundStation access, Zone service, parameter,
and Route Models; a precedence-only CoordinationPolicy; a
PlanningWindowPolicy; Route demands; and optional execution observation.

The Planner creates the bounded physical-link request, accepts ordered matching
frame/observation pairs, advances acquisition and operational state, and
publishes self-contained Plans. It owns distinct physical-observation,
planning, and publication frontiers, bounded unpublished transitions, compact
inspection evidence, and Route worker shutdown.

GroundStation access policy remains separate from ISL acquisition. Zone
serving is a Planner decision. Routes are planned only over operational
Connections, and failure is represented explicitly by a reason such as
`ZONE_UNSERVED` or `NO_OPERATIONAL_PATH`.

## Operational values and Plans

`PlannedOperationalState` contains only timestamped complete operational
outputs. It contains no physical frame, candidate set, lifecycle timer, Model
cache, worker pool, publication buffer, or Tool schema.

Every `PlanTransition` represents exactly one formal Timeline timestamp. It may
be empty and carries replay-complete changes for Connection establishment and
withdrawal, complete effective-parameter updates, Zone service changes, and
Route outcome changes.

Canonical semantic event references are derived from transitions. Coordination
stores precedence between deterministic immutable references; it does not own a
second editable event payload.

A `Plan` contains `plan_id`, `previous_plan_id`, the physical-world
fingerprint, input and output state fingerprints, one exact positive
`observation_interval`, start and end timestamps, ordered transitions, and
semantic event precedences.

The bootstrap Plan begins at the formal Timeline start, has no previous Plan or
input fingerprint, and replays from the explicit empty operational state.
Every later Plan names its immediate predecessor and consumes the previous
output fingerprint. Adjacent transitions are exactly one observation interval
apart, and a later Plan begins exactly one interval after the prior Plan ends.
`apply_plan_transition` and `apply_plan` are the common semantic replay contract
used by Planner publication, Compiler validation, detached inspection, and
qualification.

Fingerprints are schema-tagged, deterministic across processes, canonical with
respect to tuple ordering, and based only on semantic content.

## Window and publication policy

The canonical policy combines a 5-second formal observation interval, a
50-second planning horizon, and a 20-second publication horizon.

Before normal publication, the Planner plans up to 50 seconds from the current
publication frontier, then publishes the earliest contiguous prefix of at most
20 seconds. Event timestamps retain 5-second resolution. Publication does not
quantize events to a Plan edge.

At Timeline end the window shrinks and the remaining prefix is flushed once.
No formal timestamp is skipped, duplicated, or shared by two Plans. Canonical
10-minute S3 therefore has 121 formal transitions, 30 Plans, 30 compiled
realizations, and 121 compiled activation timestamps.

## One incremental producer

`_IncrementalRealizationProducer` is the sole progression seam. It owns the
Timeline iterator and cursor, bounded prehistory sampling, frame and physical-
link requests, Planner prime/observe/finish/release/shutdown, the bounded
pending Plan queue, incremental Compiler calls, previous-realization lineage,
and per-invocation evidence.

`produce_next_realization()` advances only as far as needed to publish and
compile the next result. Precomputed mode drains this exact producer. There is
no separate batch planning or compilation algorithm.

The Plan is the only Planner semantic value crossing into compilation. Planner
may retain the output state computed while creating a Plan solely as its own
next-publication input; Producer neither retrieves nor stores that state, and
Compiler reconstructs it by replaying the Plan against the previous compiled
realization.

The maintained runtime is persisted-only. Generation drains the producer into
a format-version-3 manifest whose entries name checksum-verified
`deterministic-gzip-json-v1` `.json.gz` records. Before `BpfRunner`
construction, the persisted realization supplier eagerly verifies,
decompresses, structurally decodes, and lineage-validates the complete bounded
sequence. Runner pulls only from the resulting immutable tuple; maintenance
never performs persistence, decoding, planning, or compilation synchronously.

A later live-rolling stage will add a bounded concurrent coordinator/buffer and
Runner consumer around this same producer, with explicit backlog and frontier
evidence. Current documentation does not claim that future qualification.

## Compiler boundary

The sole public Compiler entry is:

```python
compile_bpf(
    plan: Plan,
    physical_world: PhysicalWorld,
    previous_realization: BpfCompiledRealization | None,
) -> BpfCompiledRealization
```

The Compiler validates world provenance, Plan lineage, input fingerprints, and
semantic replay. It then lowers Connection, parameter, Zone, Route, and
precedence changes into backend intentions. It does not run physical,
selection, acquisition, Zone, parameter, or Route algorithms.

`BpfCompiledRealization` carries source Plan lineage fingerprints, exact source
observation cadence, the output `PlannedOperationalState`, a compact activation
schedule of timestamp plus transition fingerprint, backend intentions,
constraints, and aggregate semantic-precedence folding evidence. Semantic
precedence is represented by activation frontiers, Runner phases, or atomic
forwarding generations when those existing mechanisms are sufficient; exact
pairwise constraints are retained only as a fail-closed fallback. Zone lowering
separates readiness, forwarding publication, and retirement. Empty semantic
transitions still create activation boundaries.

The authoritative Plan remains in Planner history rather than being copied into
every realization. Persisted realization readers verify checksums, schema,
references, state fingerprints, lineage, equal cadence, and exact next starts;
they do not reconstruct Plans or repeat Compiler lowering. Full semantic
qualification and Traffic inspection use the aligned authoritative Plan stream.

## Runner and Traffic

`BpfRunner` consumes compiled realizations and owns live BPF preparation,
activation, maintenance, cleanup, and `RealizedRuntimeState`. It is unaware of
Planner internals.

Traffic consumes aligned authoritative Plans, explicit compiled planned
reachability, plus Runner
frontier/binding facts. It distinguishes planned unreachable, activation-time
transience, unexplained loss of a planned-reachable path, and successful
reachability. Missing forwarding state is diagnostic evidence, not an implicit
replacement for the planned Route outcome.

## Durable evidence and Tools

One producer invocation may write:

- `domain-history/physical-frames.jsonl`: formal timestamps and ECEF positions;
- `domain-history/planning.jsonl.gz`: streaming compressed canonical published
  planning evidence with compact precedence endpoint indexes and one RouteDemand
  copy per Route transition;
- `domain-history/compiler.jsonl`: compact Plan/realization lineage, world/state
  fingerprints, activation timestamps, and intent counts;
- `domain-history/domain-history-manifest.json`: terminal Timeline coverage,
  counts, frontiers, lineage, and byte/hash seals for all three streams.

The sealed domain-history streams are authoritative for those compact facts.
They do not by themselves contain a complete compiled realization:
`compiler.jsonl` explicitly identifies the canonical realization handoff as
the semantic payload owner. The independent
`runtime/realizations/manifest.json` and checksum-verified `.json.gz` records
are authoritative for complete planned output, activation boundaries, backend
intentions, constraints, and intended state. `runtime/execution.json` anchors
that handoff's format, encoding, count, sequence hash, and manifest hash.
Neither authority is inferred from the other.

These artifacts are semantically owned, deterministic, and detached. They
contain no live objects or pickles. Tools read them; they never run domain
Models or become a production dependency. The terminal domain-history manifest
is published atomically only after the producer has been drained through its
terminal `None`; incomplete, truncated, cadence-gapped, cross-stream-mismatched,
or coherently relocated histories fail closed.

The private generated-runtime wrapper emits actual `runner-total` and
`traffic-total` Observation rollups and merges them with generation records in
the invocation-owned Observation stream. Detached Tool rendering remains side
work: when domain runtime succeeds, a Runner, Traffic, Observation, or aggregate
Tool failure produces a warning and does not replace that successful outcome.

The maintained inspection surfaces are Physical World, Zone, Planner, BPF
Compiler, Runner, Observation, and Traffic. Exact `PhysicalFrame` facts remain
part of the Physical World inspection; there is no standalone PhysicalFrame
Tool. Planner inspection
separates candidate, selected/acquiring, operational, routed/unreachable,
compiled, and realized meanings.

## Validation and scope

Canonical S3 remains the scale and semantic qualification target. Round B
requires offline replay and compilation proof before any full-scale runtime
deployment. The full S3 persisted Runner/Traffic run has not yet been rerun
after this cutover and must not be inferred from offline compilation.

No full S3 Compose deployment belongs to this implementation round. Small
runtime integration may validate the Plan-to-Compiler-to-Runner boundary.

Changes to PhysicalFrame fields, S3 inputs, K3/2+2/20-second policy,
GroundStation acquisition, traffic-aware resource allocation, Runner
activation semantics, or backend selection require separate approval.
