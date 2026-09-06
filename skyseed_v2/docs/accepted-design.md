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
→ sealed Runner artifact
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

The maintained runtime is persisted-only and precomputed-only. Generation
completely drains the producer, losslessly normalizes already-compiled backend
facts into deterministic typed packed runtime tables, incrementally derives the
exact mechanical runtime-capacity demand while each normalized boundary is hot,
validates the full finite sequence, and publishes the artifact-v3 terminal
manifest seal. Before `BpfRunner`
construction, generated execution verifies source/input identity, manifest and
binary hashes, version, bounds, and timeline. Runner opens the binary with
read-only `mmap`; it does not reconstruct the rich `BpfCompiledRealization` or
`PlannedOperationalState` graph. No producer, supplier, input frontier,
exhaustion protocol, or online planning path exists during replay.

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

`BpfRunner` owns one-shot Preparation, formal Main, cleanup, and realized-state
snapshots. Preparation opens and validates the sealed finite artifact, decodes
all boundaries, materializes and validates the complete finite sequence, and
builds immutable delta plans and their activation JSON wire representation.
Artifact-local caches reuse decoded identities/timestamps; Preparation reuses
previous route/access state, stable names/slots, and route-by-interface indexes.
It discovers container incarnations once, retains
namespace descriptors and rtnetlink sockets, initializes maps/programs at the
sealed cumulative capacity, and prewrites every generation-semantic entry.
Preparation then physically executes boundary 0, from no network to the initial
network, before returning. The formal clock begins in `run()`, whose first
possible activation is boundary 1. A one-boundary experiment has no formal
network activation.

Main consumes a prepared immutable delta: changed connections, affected nodes,
and normalized operation nodes. Each veth creation includes both deterministic
MACs, one RX/TX queue per endpoint, and its exact future retirement cohort.
Interfaces start down; Main configures qdisc/BPF and readiness, changes
routes/neighbours and live port bindings, then commits
`active_generation`, then withdraws obsolete routes/bindings and deletes
withdrawn UP veths directly. One nonzero-group RTM_DELLINK per namespace cohort
collects devices into kernel grouped unregister, including their implicit peers.
Exactly one endpoint per pair carries the nonzero group. Independent namespace
batches use bounded persistent helper workers initialized in Preparation; one
worker owns each socket and all batches join before Main completes. Cleanup
uses the same owned cohorts, retains uncertain ownership, and closes the workers.
Candidate rollback deletes individual new pairs because their future groups can
also contain previously live pairs. Generation-semantic maps remain immutable throughout formal
runtime. Future veths remain absent until their own boundary.

There is no Maintenance thread, READY producer/wait, deferred generation
completion, or generation reclaim. All finite semantic entries stay resident
until cleanup. Capacity or identity failures stop generation/Preparation;
formal Main handles the return status of actual mutations without full-network
validation or PID/netns rediscovery.

One privileged command stream is established in Preparation. Its long-lived
process uses retained namespace rtnetlink sockets and BPF file descriptors.
Main does not launch Docker, namespace, shell, `ip`, or `tc` subprocesses.
Namespace operations are grouped by affected namespace within pre-publication,
active-generation publication, and post-publication barriers. Candidate rollback
uses the same sockets and syscalls. If a retained resource becomes unusable, its
operation fails; there is no container-restart recovery.

BPF publication is a forwarding subphase. Formal ACTIVE advances only after the
whole transition, including withdrawals and veth deletion, succeeds. Failure
preserves the last completed formal frontier and conservative cleanup ownership.
Before publication, newly allocated candidates may be removed; any remaining
partial live state is reported. After publication, selected-generation resources
are retained for final cleanup. Generation reuse does not invent a publication.

Main records compact boundary/barrier timing and enqueues telemetry template
references with live binding deltas. A background writer expands and serializes
identity records; observation never gates the next boundary. Cleanup owns final
network/BPF teardown, telemetry drain, privileged facility closure, and artifact
handle closure. Automatic deadlines use one fixed monotonic runtime origin plus
each nominal offset. Lateness consumes available idle time; an overrun executes
the next boundary when available and reports actual lag. All transitions remain
sequential and complete, with no skipped boundaries or reset origin. The helper
arrival clock is observational. Manual activation uses the same ordered plans.

The private BPF logical port is a non-zero uint32 allocated by the finite
artifact writer for one node-side connection endpoint. The manifest stores each
connection and both endpoint IDs explicitly; IDs survive withdrawal/recreation
across every generation of that artifact. They are not public domain values or
root exports. Live ifindexes occur only in small per-node binding maps.

| Map | Key | Value |
|---|---|---|
| `active_generation` | `0` | generation |
| per-node `active_links` | `(generation, logical_ingress_port_id)` | `1` |
| per-node `routes` | `(generation, destination_ipv4_u32)` | logical egress port; `0` for local |
| per-node `ingress_bindings` | live ifindex | logical port |
| per-node `egress_bindings` | logical port | live ifindex |

The packet program reads the active generation, translates ingress ifindex,
checks the generation's active logical ingress, resolves the destination, passes
local routes, and translates a non-local logical egress before redirecting.
Missing generation, active link, route, or required live binding drops the
packet. Prewritten future entries remain invisible until generation publication.
Binding capacities use the add-before-withdraw interface high-water; semantic
map capacities use complete finite cumulative demand. No generation is reclaimed
during formal runtime.

Traffic consumes aligned authoritative Plans, explicit compiled planned
reachability, plus Runner
frontier/binding facts. It distinguishes planned unreachable, activation-time
transience, unexplained loss of a planned-reachable path, and successful
reachability. Missing forwarding state is diagnostic evidence, not an implicit
replacement for the planned Route outcome.

Traffic's ideal wall-clock deadlines are pacing evidence and action eligibility,
not live-state authority. Episode arm, reconcile, migration, and completion wait
for the corresponding actually published Runner ACTIVE frontier, so a slowed
Runner neither exposes future actions early nor truncates an episode merely
because its ideal deadline passed.

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
`compiler.jsonl` explicitly identifies the sealed Runner artifact as the
semantic payload owner. The independent
`runtime/runner-artifact/manifest.json` and `runner-runtime.bin` are
authoritative for normalized symbolic runtime boundary tables.
`runtime/execution.json` anchors artifact format, encoding, counts, sequence
hash, and manifest hash. Neither authority is inferred from the other.

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
deployment. Full S3 persisted Runner/Traffic qualification must not be inferred from
offline compilation.

Full S3 Compose deployment uses generated controls under explicit live
qualification authorization. Scoped teardown preserves unrelated resources.

Changes to PhysicalFrame fields, S3 inputs, K3/2+2/20-second policy,
GroundStation acquisition, traffic-aware resource allocation, Runner phase
authority, observable runtime-state semantics, Runner activation semantics, or
backend selection require explicit approval from both the user and ChatGPT.
