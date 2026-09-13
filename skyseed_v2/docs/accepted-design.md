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

`SimulationTimeline` is finite: construction requires `start_time`, `end_time`,
and a positive `observation_interval`. Its iterator remains the sole formal
timestamp authority; there is no unbounded form or `is_bounded` member.

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
- current example cross-plane candidate scope: K1 (historical S3 used K3);
- deterministic retain-first selection;
- ISL and GS preparation: 5 simulated seconds;
- minimum useful service after preparation: ISL 180 seconds, GS 60 seconds;
- bounded sampled prehistory for warm initialization;
- the prior-policy warm-oracle record (714 cross-plane selections and 12
  underfilled Satellites) remains historical evidence, not a new-policy oracle.

The value `4` is an explicit scenario capability assumption. The 2+2/K rule is
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

GroundStation and ISL selection policies remain separate; both reserve endpoint
capacity during preparation. Zone
serving is a Planner decision. Routes are planned only over operational
Connections, and failure is represented explicitly by a reason such as
`ZONE_UNSERVED` or `NO_OPERATIONAL_PATH`. The existing Route engine may reuse
paths for current requests while their source-reachable satellite components and
relevant GS source/terminal accesses are unchanged. Complete component identity
covers additions, shorter paths, merges and splits. Its bounded derived cache does not own route truth.

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
used by public Plan construction, Compiler lowering, detached inspection, and
qualification. Planner constructs its validated output once; publication reuses
that state instead of rebuilding and replaying an expected state.

Fingerprints are schema-tagged, deterministic across processes, canonical with
respect to tuple ordering, and based only on semantic content.

## Admission, preparation, and parameter memory

`PhysicalContactWindow` carries a proven usable interval from the physical link
Model to Planner. Its finite end bounds prediction coverage and does not assert
physical disappearance; `end_timestamp=None` is an analytical all-future proof.
Missing proof means unknown future feasibility. The circular-orbit Model uses
all-phase bounds where valid and conservative continuous-motion bounds around
sample cells elsewhere. Unknown cells never count as proved service. Predictions
cover the finite experiment and only each connection kind's required admission
tail. They do not run future Planner, Route, Compiler, or Tool work.

A new connection is admissible only if usable contact covers preparation plus
its minimum post-preparation service. This is a forward-looking test, not a
180/60-second elapsed-selection timer. Preparation retains its original start
across samples and reserves capacity at both endpoints. Cancellation or physical
loss releases reservations. Operational incumbents survive while physically
feasible. A full-port replacement incurs a break-before-make service gap and
new preparation; overlap requires genuine spare endpoint capacity.

The current Starlink examples use retain-first 2+2/K1, cyclic same-plane
predecessors/successors in authoritative mean-anomaly order, and four nearest
admission-eligible cross-plane candidates per endpoint/neighbor plane. Wider
same-plane scope, K3 and unbounded cross-plane neighbor counts remain configurable.
Pending/operational incumbents are queried regardless of their current rank.

GS allocation lexicographically maximizes covered stations, distinct satellites,
and total selected connections, then minimizes changed valid assignments and
applies deterministic quality ranking. Reservations receive the same continuity
value as operational incumbents but provide no service until preparation ends.
The exact integer residual solver seeds feasible retained assignments and cancels
negative circulation cycles until residual optimality certifies all five objectives.
Loose cardinality bounds remain a cheap sufficient shortcut; coverage alone is
insufficient. Cold allocation retains successive shortest paths. The fifth cost is
the existing sum of deterministic per-station ranks and satellite indices; equal
totals can admit several assignments. Canonical residual traversal can choose a
different equal-objective assignment from cold SSP. Formal comparisons retain
those ties and their subsequent continuity/lifecycle effects explicitly.
The optimum is over the admitted search graph, not an unqueried physical graph.

`default_ground_station_limit=4` is the example's engineering fallback for
unspecified device capability. Explicit 2/4/8 capacities override that fallback;
optional `ground_station_policy_limit` is a separate ceiling (default None).
Satellite-side capacity remains two in these inputs. The initial GS query budget
32 is a search count, not a hardware range or connection limit. Physical queries
include retained/pending pairs and use proven contact-duration predicates; the
Producer repeats the same query with a wider count when Planner's Model detects
candidate/endpoint competition. It does not own selection or lifecycle.
Primitive geometry still evaluates the configured elevation/distance envelope;
only selected query rows become candidate objects. Full contact proofs are
retained independently of rank. `*_pairs_not_returned` counts physically feasible
rows excluded by query scope/contact predicates, not physical disappearance.
Zone highest-elevation service semantics remain unchanged.
Zone service identity is `(network_scope, zone_id)`. The empty scope retains
unscoped behavior. The five-Shell example explicitly supplies satellite scope
membership to the GS, Zone and Route Models: every shared GS reserves two
preparing-or-operational slots per Shell (ten total), every geographic Zone has
one explicit assignment per Shell, and the geographic three-GS association is
applied separately in each scope. The scoped Route Model excludes other-scope
satellites and GS transit. No cross-Shell communication Model is implemented.
One Compiler/Runner owns shared interfaces, forwarding and cleanup. Distinct
scoped Zone IPv4 destinations prevent shared-GS forwarding collisions. Physical
feasibility, satellite capacities and preparation/handover rules still apply;
association does not promise an operational path. Isolation is configured in
Models, not imposed as a permanent PhysicalWorld rule.

Delay updates compare `abs(current-effective)/abs(effective) >= 0.10`, including
equality, increases and decreases. The adopted value becomes the new baseline;
a zero reference changes on any nonzero value. At the first formal sample at
least 120 seconds after the last adoption/refresh, current delay is refreshed.
An unchanged refresh advances memory without a parameter event. The old fixed
integer delay target persistence requirement is removed. Bandwidth, loss, and
directional semantics remain as configured.

These durations and the threshold are reference-scenario engineering choices.
ISL preparation abstracts known-ephemeris setup; GS preparation is a 5-second engineering assumption (one sample at the
reference 5-second cadence), not a measured commercial gateway acquisition time.
The authorized S1 qualification also uses 1-second observations; preparation,
minimum service and parameter-refresh durations remain measured in seconds.

## Frame batching and publication policy

The canonical policy uses a 5-second formal interval and `frames_per_batch=32`.
`FrameBatchPlanningWindowPolicy` removes the independent publication lookahead.
It publishes each complete 32-frame prefix immediately and flushes the remaining
prefix once at Timeline end. Batch size is not a control interval. No formal
timestamp is skipped, duplicated, quantized, or shared by two Plans.

Prehistory is the maximum preparation dependency rounded up to formal cadence;
per-kind prediction tail is rounded preparation plus minimum effective service.
Canonical S3 therefore needs 5 seconds of preparation history, at most 185
seconds of ISL tail and 65 seconds of GS tail. Stable all-future ISL proofs need
no extra sampled ISL tail. The 10-minute S3 has 121 formal transitions across
4 Plans (32/32/32/25), 4 realizations, and 121 activation timestamps.
The authorized 600-second S1/1-second condition uses 601 transitions and 19
Plans/realizations (18 full batches plus 25 frames), with the same durations
in seconds and no independent publication lookahead.

## One incremental producer

`_IncrementalRealizationProducer` is the sole progression seam. It owns the
Timeline iterator and cursor, bounded prehistory sampling, frame and physical-
link requests, Planner prime/observe/finish/release/shutdown, the bounded
pending Plan queue, incremental Compiler calls, previous-realization lineage,
and per-invocation evidence.

`produce_next_realization()` advances only as far as needed to publish and
compile the next result. Physical, Planner and Compiler cutoffs consume this
same cursor; physical cutoff computes prediction and every formal observation
without invoking Planner progression. Precomputed mode drains this exact producer. There is
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
validates the full finite sequence, and publishes the artifact-v4 terminal
manifest seal. Before `BpfRunner`
construction, generated execution verifies source/input identity, manifest and
binary hashes, version/seal and timeline metadata. During Preparation, Runner
opens the binary with read-only `mmap` and checks its header and index bounds;
it does not reconstruct the rich `BpfCompiledRealization` or
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
one chronological semantic replay during lowering. It then lowers Connection, parameter, Zone, Route, and
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

Compiler retains immutable backend indexes in the previous realization and
updates them from Plan deltas. These are derived private indexes, not new state
owners or runtime planning input. The artifact normalizer indexes operations and constraints once per batch.
Capacity derivation maintains resource/qdisc/forwarding/Zone indexes and
reference-counted per-node destinations, preserving shared-route counts,
add-before-withdraw peaks and collision checks. The same private boundary
materializer supports initial Preparation and admitted batch construction; it creates no runtime
lifecycle authority. The authoritative Plan remains in Planner history rather than being copied into
every realization. During generation, the artifact writer validates predecessor
and state-fingerprint continuity, equal cadence, exact next starts, and boundary
references before sealing the sequence. Execution verifies identity, hashes and
metadata; Preparation or Maintenance checks the opened batch’s binary
header/index bounds and decodes every record with its hash and operation-count checks. Mandatory encode/full-decode/
full-object comparison is qualification and codec-test work, removed from normal
generation; sequence, identity, reference and integrity checks remain required. Readers do not repeat the
writer's sequence-relationship validation or Compiler lowering. Full semantic
qualification and Traffic inspection use the aligned authoritative Plan stream.

## Consecutive finite delivery

The approved continuation mode keeps the node set fixed and accepts sequential,
append-only finite sealed batches. Each optional v4 `delivery` manifest extension
carries its batch index, prior manifest hash, prior end/Plan/state/generation,
cadence and final flag. It extends the same artifact format; existing single
final artifacts remain readable. No loaded future may be replaced.
`terminal: true` seals one artifact; `delivery.final` independently identifies
the last delivery in the finite session. Neither creates a contact ending.
Writer continuation validates sequence relationships at generation time. Execution
checks source/input identity, seals, hashes and timeline metadata. Runner append
checks the compact chain identity; artifact open and decoding remain batch work.

`Planner.flush()` publishes an unpublished prefix without finishing, advancing
physical time or resetting Planner retention, acquisition or parameter memory.
The existing Producer pauses at a cadence-aligned delivery endpoint and resumes
with the same Timeline cursor, Planner and Compiler predecessor. Prediction
covers the whole known finite horizon plus policy tails. The first version does
not extend that horizon online, recalibrate, change nodes or create artificial
contact endings at a seam. Sampling cadence, internal publication frame count,
delivered coverage and resident capacity are independent quantities.

`BpfRunner.append_batch(runtime_artifact: Path)` registers at most one pending
successor. A ready successor can coexist with that pending registration; runtime
loads at most one successor to the current batch. Initial Preparation initializes
deployment bindings, helper, sockets, maps and programs once, fully prepares the
first batch and activates B0. Automatic `run()` uses one fixed origin and executes
each boundary once. Continued execution requires automatic scheduling; the
single-delivery manual API remains available.

For a delivery starting at T and covering D, Maintenance begins at T − αD.
`batch_lead_fraction` defaults to 0.75 and must be in (0,1]; it is an engineering
configuration, not an optimum. The qualification seam is `[0,300)` followed by
`[300,600]`: 60 plus 61 five-second timestamps, B0 once and 120 Main boundaries.
The five-Shell scenario sets the lead fraction to 1.0: the second batch starts
loading when Main begins, with a 300 s budget. The following older estimates
describe the historical 0.75 scenario, not a new performance guarantee. The bounded
planning estimate scales dated S1 decode/construction/prewrite by successor
boundary count and runtime node count. S1/1 s gives about 101 s; the same-cadence
S1/5 s reference gives about 141 s. The 225 s budget exceeds both, with about
84 s margin over the larger estimate. This estimate does not establish linear
scaling or replace measured READY margin and Main-overlap evidence.

Resident admission uses fixed reservations derived from the first batch and
`batch_capacity_factor` (default 2, integer 1–16). In continuation mode each
node's entry envelope uses the first batch's peak among nodes of the same typed
entity kind, multiplied by the factor. This allows forwarding work to move to a
previously lightly used peer. The exact sealed per-node demand stays unchanged;
the larger reservation has an explicit memory cost. Active/route maps reserve
twice that per-batch envelope for overlap, while live bindings use the envelope
directly. Boundary/generation limits retain the first batch's counts and factor.
Boundary/generation and per-node entry demand are checked before decoding/prewriting; existing resident
reservations include retained generations. No map is sized from the assumed
complete future session. A successor exceeding this envelope fails admission.
Generation IDs increase monotonically and exhaustion is explicit; they are not
silently recycled. Immutable evidence and artifacts remain on disk for historical
analysis; the runtime releases retired plans and mappings after actual activation.

The existing privileged helper writes at most 64 nodes / 2048 semantic entries
per prewrite request, and bounded reclaim requests consume at most 1024 work
units. Main has channel priority; background requests yield near the next
deadline and between units. Equal node content shares one immutable version;
global generations map to node versions. Reclaim first checks actual active
generation and old-generation readers, and decrements retained version
references before deleting unreferenced table entries. Packet readers increment
a generation counter, recheck the stable single-entry active-generation ARRAY,
and release on every exit. Monotonic identities prevent ABA; a reader that raced
publication retries before touching generation tables. Retries are bounded and
failure drops with recorded dataplane evidence. These private Linux mechanisms
do not introduce a new public type, dataplane or lifecycle authority.

Maintenance neither publishes network state nor delays formal withdrawals.
Main creates current-boundary resources, publishes generation selection and
joins all owned withdrawal/deletion work before advancing formal ACTIVE. Stable
per-connection retirement groups preserve cross-batch live ownership without
assuming future withdrawal cohorts. Published generations remain immutable;
only future versions are prewritten. Missing READY at the deadline or failed
Maintenance makes the session failed/incomplete without waiting, resetting time
or skipping a boundary. Terminal Cleanup retains its session-ending ownership.

## Runner and Traffic

The public constructor is `BpfRunner(*, compose_project_name: str,
runtime_artifact: Path, batch_lead_fraction: float = 0.75,
batch_capacity_factor: int = 2)`. The `pathlib.Path` names the sealed finite artifact
manifest; the realization-object/supplier constructor input and old experiment
realization handoff are removed. The reader supports indexed access, but Runner
decodes and constructs each delivery incrementally during Preparation or
Maintenance, never on demand during Main. READY still requires the entire
delivery, including all semantic prewrites, to succeed.

`BpfRunner` owns initial Preparation, formal Main, optional continued-batch
Maintenance, cleanup, and realized-state snapshots. Preparation opens the first sealed finite delivery, checks its metadata and
binary structure, and decodes one boundary at a time while constructing all
immutable delta plans and activation JSON commands. Initial and successor
deliveries use the same preparation path, releasing decoded boundary scratch
data incrementally. Single-delivery execution prepares the whole sequence;
continued execution does not initially decode or prewrite its successor.
Artifact-local caches reuse decoded identities/timestamps; Preparation reuses
previous route/access state, stable names/slots, and route-by-interface indexes.
It discovers container incarnations once, retains
namespace descriptors and rtnetlink sockets, initializes maps/programs at the
sealed cumulative capacity for one delivery, or fixed resident reservations
based on the first delivery and configured capacity factor for continuation.
Continued per-node entry envelopes use first-batch peaks of the same typed
entity kind; they allow peer work migration while retaining fixed limits
and exact sealed-demand accounting. The extra reservation consumes memory.
It prewrites the first delivery's generation semantics. Equal per-node content
shares an immutable node version across generations; unchanged entries are not
written again. The static demand index is built once per admitted batch.
Preparation then physically executes boundary 0, from no network to the initial
network, before returning. The formal clock begins in `run()`, whose first
possible activation is boundary 1. A one-boundary experiment has no formal
network activation.

Main consumes a prepared immutable delta: changed connections, affected nodes,
and normalized operation nodes. Each veth creation includes both deterministic
MACs, one RX/TX queue per endpoint, and its owned retirement group.
Single finite execution groups known future retirement cohorts; continuation
uses stable per-connection groups, preserving ownership across unknown future
withdrawals and re-establishment.
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
also contain previously live pairs. Published generation content is immutable;
Maintenance writes only future content and reclaims only eligible retired
versions. Future veths remain absent until their own boundary.

Single-delivery execution retains finite semantic entries until Cleanup.
For append-only continuation, Runner/backend Maintenance loads and prewrites the
next sealed batch, then marks it READY. It reclaims eligible older plans and
map versions after actual activation, preserving in-flight readers and retained
references. Main alone executes boundaries, including timely withdrawals and
joined physical deletion. It never waits for READY past the nominal deadline:
missing/late READY or Maintenance failure produces an explicit failed/incomplete
run, without clock shifts, skipped boundaries or a successful fallback.
Capacity and identity failures are fail-closed. Formal Main handles actual
mutation results without full-network validation or PID/netns rediscovery.

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
references with live binding deltas. A background writer serializes changed endpoint identities and generation membership
deltas; retained endpoints are shared. Observation never gates the next boundary. Cleanup owns final
network/BPF teardown, telemetry drain, privileged facility closure, and artifact
handle closure. Automatic deadlines use one fixed monotonic runtime origin plus
each nominal offset. Lateness consumes available idle time; an overrun executes
the next boundary when available and reports actual lag. All transitions remain
sequential and complete, with no skipped boundaries or reset origin. The helper
arrival clock is observational. Manual activation uses the same ordered plans.

The persistent helper owns its deletion workers and retirement ledger. Every
current-boundary deletion job joins before Main completes; worker reuse does
not create a cross-boundary deletion service. Asynchronous identity writing
belongs to existing observation facilities, not Maintenance or a new network
lifecycle. `_runner_artifact.py` packages and reads backend artifacts;
`kernel_netlink.py` implements private Linux execution; `process_incarnation.py`
captures startup identity. Prepared/derived records and capacity accumulation
remain private backend mechanics, with no new domain component or lifecycle
authority. Simulator, Planner and Compiler ownership is unchanged.

The private BPF logical port is a non-zero uint32 allocated by the finite
artifact writer for one node-side connection endpoint. The manifest stores each
connection and both endpoint IDs explicitly; IDs survive withdrawal/recreation
across generations and continued artifacts in the same fixed-node session. They are not public domain values or
root exports. Live ifindexes occur only in small per-node binding maps.

| Map | Key | Value |
|---|---|---|
| `active_generation` ARRAY | `0` | generation |
| per-node `generation_versions` | generation | immutable node content version |
| continued-session `generation_readers` | generation | in-flight reader count |
| per-node `active_links` | `(node_version, logical_ingress_port_id)` | `1` |
| per-node `routes` | `(node_version, destination_ipv4_u32)` | logical egress port; `0` for local |
| per-node `ingress_bindings` | live ifindex | logical port |
| per-node `egress_bindings` | logical port | live ifindex |

The packet program reads the active generation and its node content version,
translates ingress ifindex, checks the version’s active logical ingress, resolves
the destination, passes
local routes, and translates a non-local logical egress before redirecting.
Missing generation, active link, route, or required live binding drops the
packet. Prewritten future entries remain invisible until generation publication.
Binding capacities use the add-before-withdraw interface high-water; semantic
map capacities use finite cumulative demand for one delivery or fixed admitted
resident reservations for continuation. Continued generations use bounded
reader accounting and retained version references for safe runtime reclaim;
single-delivery entries remain until Cleanup.

Traffic consumes aligned authoritative Plans, explicit compiled planned
reachability, plus Runner
frontier/binding facts. It distinguishes planned unreachable, activation-time
transience, unexplained loss of a planned-reachable path, and successful
reachability. Missing forwarding state is diagnostic evidence, not an implicit
replacement for the planned Route outcome.

Traffic's ideal wall-clock deadlines are pacing evidence and action eligibility,
not live-state authority. Episode arm, reconcile, migration, and completion wait
for the corresponding completed Runner ACTIVE frontier, so a slowed
Runner neither exposes future actions early nor truncates an episode merely
because its ideal deadline passed.

Generator control retries one Docker RPC timeout with the same unique process
identity, recording both attempts. Identity mismatch and exhausted retries remain
execution failures. The executor worker completes its bounded generator teardown
before the caller's final check; teardown does not race the worker's state or IO.

## Durable evidence and Tools

For Mini and Starlink's Compiler mode, `main.py` completes semantic generation,
compiled realizations, the sealed artifact, output and control scripts before
deployment. `start.sh` deploys; `run.sh` starts Runner/Traffic; `launch.sh`
combines start and run; `cleanup.sh` is independent. Detached Tools render the
already-produced evidence without participating in network lifecycle.
Generated deployment cleanup snapshots exact project-labelled immutable IDs,
reverses metadata-defined deployment groups and actual declared/namespace
dependencies within the same deployment owner. Each whole group uses one
Docker CLI native bounded removal queue before the next group is admitted. After executor/Runner evidence and
cleanup complete, explicitly marked generated disposable network services can
be force-removed without an idle grace period. Explicit stop signal/grace or
pre-stop hooks retain graceful behavior; hooks address immutable IDs. Failure
stops subsequent dependency removal. External networks and volumes/images are
retained; retries inventory resources still present. Startup uses whole groups with one fixed configurable Compose concurrency
limit; failures retain matching successful groups for explicit deployment retry.
The group strategy and its actual qualification status are documented in the
[Compose owner README](../src/skyseed_v2/compose/README.md).
The deployable `complete_static_emulator` teaching Example keeps its own
start/cleanup controls; it does not package this Runner start/run/launch chain.

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
Models or become a production dependency. Planner/Traffic collection performs one required semantic replay. Traffic
reconstructs the Plan portion of sealed history without materializing unused
Model inspection objects; the Planner Tool retains complete inspection. Public
projection of a detached mutable Traffic snapshot still validates its replay. Planner browser records are projected directly
from validated selections, with invocation-local RouteDemand display identities.
Runner projection detaches caller inputs once; bundle inventory and checksum
publication reuse the same payload hashes. The terminal domain-history manifest
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

Changes to PhysicalFrame fields, S3 inputs, configured ISL neighborhoods/2+2 targets or the approved admission policy,
GroundStation acquisition, traffic-aware resource allocation, Runner phase
authority, observable runtime-state semantics, Runner activation semantics, or
backend selection require explicit approval from both the user and ChatGPT.
