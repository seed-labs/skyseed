# Current implementation state

Skyseed v2 retains the Round-B atomic Network Planner architecture and the
accepted finite Runner correction and performance-optimization implementation.
The current tree has one intended production chain:

```text
SimulationTimeline
→ PhysicalWorld / PhysicalFrame
→ PhysicalLinkObservation
→ Planner / PlannedOperationalState
→ Plan
→ BPF Compiler / BpfCompiledRealization
→ sealed Runner artifact
→ BpfRunner / RealizedRuntimeState
```

The public Planner, immutable operational values, self-contained timestamped
Plan, transition replay, 50-second planning horizon, 20-second publication
horizon, Compiler source boundary, incremental producer on the compute side, and finite
precomputed-only Runner artifact are the current contracts. The Simulator owns
one PhysicalWorld and one Planner; experiment orchestration owns the producer;
the BPF backend owns compilation and runtime realization.

The former semantic slicing and Topology-as-physical-owner paths are retired
completely. They are neither fallbacks nor alternate production routes.

## Runner milestone accepted after Work review

The cumulative finite Runner correction and performance-optimization
implementation and its S3 evidence have passed independent Work review at
checkpoint
`6e8652a01b7e796a5a901bf45387fcad3d353a9e` on `feature/skyseed-v2`, relative to
implementation baseline `1b3d14cb296caefd2e87d1811cea8dcff08fab51`.
The historical scope, measured comparison and archived raw evidence remain in
[`provenance/runner-optimization-20260907.md`](provenance/runner-optimization-20260907.md).
Traffic's retained WARN classification is accepted for this milestone; it is
not relabelled PASS. No additional S3 run is required for the closeout.

Work independently reviewed and accepted the documentation in `7a6d9a3`,
including the corrected artifact-validation stage responsibilities, the Mini /
Starlink Compiler scope of the Runner controls, and the adopted optimization
and host-configuration decision records. Implementation, S3 evidence and
documentation review are complete; this closeout only synchronizes final status
and publishes the reviewed milestone.

The selected milestone branch is
`backup/skyseed-v2-runner-optimization-20260907`, at the same final documentation
commit as `feature/skyseed-v2`. Remote ref equality is a separate post-push
verification, not a claim made by this status record.

The compute-side producer and the modules it advances remain a subsequent
discussion/investigation direction after this closeout. This direction does not
authorize changes to Simulator, Planner, Compiler, or producer ownership, or
restart the completed Runner investigation.

## Canonical behavior

Canonical S3 remains unchanged: 720 Satellites, 355 GroundStations, 732 Zones,
a 10-minute Timeline at 5-second resolution, K3 cross-plane candidate scope,
2+2 ISL policy, maximum 4 ISLs per Satellite, and a 20-second acquisition
reference. The publication contract yields 121 formal transitions, 30 Plans,
30 compiled realizations, and 121 activation timestamps.

The maintained canonical entry is
`starlink_gen1_satellite_internet`; it preserves the full scenario rather than
substituting a smaller fixture.

Each Plan replays against the preceding published state and carries continuous
Plan/world/state lineage. `compile_bpf` consumes only the Plan, exact
PhysicalWorld, and previous compiled realization. Empty transitions preserve
formal time.

## Architecture delta: cumulative implementation and documentation closeout

This documentation-only closeout changes no executable architecture. The
cumulative implementation delta is non-empty:

- `SimulationTimeline` requires a finite `end_time`; the optional unbounded
  form and `is_bounded` member are removed.
- `BpfRunner(*, compose_project_name: str, runtime_artifact: Path)` consumes a
  sealed finite backend artifact. The old realization-object/supplier handoff
  and `experiment/_realization_handoff.py` are removed.
- Compute drains the producer and seals typed packed runtime tables before
  execution. Runner decodes the complete sequence during one-shot Preparation,
  physically activates B0, then executes prepared B1-and-later Main deltas.
  Maintenance, READY production/waits, deferred generation completion and
  runtime generation reclaim are removed.
- Mini and Starlink Compiler mode generate the complete artifact and controls
  before deployment;
  Traffic and detached Tools consume the aligned persisted evidence and
  distinguish generation publication from completed formal boundaries.

Simulator/Planner/Compiler ownership and public package exports remain unchanged;
there is no new public domain owner or independent lifecycle authority.
Artifact packaging/reading lives in private BPF backend code. Logical ports,
live bindings, prepared records and capacity accumulation are backend
representations. Retained privileged resources, current-boundary deletion
workers and their retirement ledger, and fixed-origin pacing are private Runner
execution details; asynchronous identity writing stays in existing observation
facilities. There is no cross-boundary deletion service or new network lifecycle.

## Maintained execution mode

Precomputed execution drains the producer, writes compact domain evidence, and
seals `runtime/runner-artifact/{manifest.json,runner-runtime.bin}`.
`runtime/execution.json` anchors source/input identity and artifact hashes.
Detached readers reject incomplete, corrupt, relocated, or mixed evidence.
Runner artifact format v3 stores explicit stable logical endpoint ports together
with strict cumulative semantic map demand, generation count, and the
add-before-withdraw physical/interface high-water.

`BpfRunner` owns one-shot Preparation, formal Main, cleanup, and realized-state
snapshots. Preparation opens the sealed finite artifact, validates its metadata
and binary structure, decodes every record, and builds the complete sequence of
immutable delta plans and their activation JSON wire representation.
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

Main records compact boundary/barrier timing, fixed operation-family duration
and attempted-call aggregates, and enqueues telemetry template
references with live binding deltas. A background writer expands and serializes
identity records; observation never gates the next boundary. Cleanup owns final
network/BPF teardown, telemetry drain, privileged facility closure, and artifact
handle closure. Automatic deadlines use one fixed monotonic runtime origin plus
each nominal offset. Lateness consumes available idle time; an overrun executes
the next boundary when available and reports actual lag. All transitions remain
sequential and complete, with no skipped boundaries or reset origin. The helper
arrival clock is observational. Manual activation uses the same ordered plans.

The approved work reduction keeps the same lifecycle and changes private
execution: Preparation owns normalized operations, cached finite derivations,
activation wire encoding and retirement-cohort assignment; the persistent helper
owns bounded workers and exact physical group cleanup responsibility. Main uses
MAC/queue/group attributes at creation and joined grouped deletion of UP pairs.
Pacing now uses fixed nominal deadlines, with actual elapsed lag retained.
Version 2 timing separates retirement critical wall time from concurrent task
sums and actual requests. The previous detailed-timing S3 remains the comparison
baseline. The work-reduction canonical invocation completed all finite
boundaries, detached inspection and scoped teardown in its own output; Runner
completed and Observation passed, while Traffic retained transient and isolated
loss warnings.

Focused regressions connect Runner-produced boundary/publication and interface
records to Traffic readers and projections, separate B0 from formal timing, and
retain uncertain initialization ownership. Canonical live S3 qualification uses
the generated output controls under explicit authorization; its raw invocation
evidence is the authority for completion and performance claims.

The Planner history remains streaming gzip evidence with indexed precedence
endpoints. Semantic precedence is folded onto existing Runner phases and atomic
forwarding generations, with explicit pairwise fallback only where required.
GroundStation planning consumes the sparse feasible observation directly,
reuses immutable PhysicalWorld capacity indexes, and solves one global indexed
sparse capacitated assignment while preserving the accepted rank, Satellite
order, and successive-shortest-path tie identity.

## Accepted offline performance baseline

The accepted normal S3 offline generation completed in 847.79 seconds external
wall time. Within that qualified run, runtime packaging fell from 380.484 to
17.932 seconds, Planner Tool generation from 315.473 seconds / about 4.11 GB to
218.610 seconds / about 546 MB, and Compiler Tool generation from 351.112
seconds / about 8.46 GB to 0.640 seconds / about 462 MB. Detached candidate
capture was 10.911 seconds and domain-evidence persistence was 26.650 seconds.
These are retained milestone conclusions, not promises for a future runtime
deployment.

## Qualification boundary

Round-B completion requires focused, integration, full-suite, documentation,
import, browser, and canonical offline S3 evidence. Those exact results belong
in the external implementation evidence directory and completion report, not
as hard-coded claims in this living status document.

Offline compilation is not full runtime qualification. Full S3 persisted Runner/Traffic qualification verifies the complete
121-boundary sequence, 120 formal Main activations, Traffic outcomes, detached
Tools and scoped teardown. Historical artifacts and raw comparison evidence
remain separate from each fresh owned output.

The earlier synchronous rolling failure is historical performance evidence.
The corrected precomputed-only Runner has completed a canonical S3 invocation
through the terminal frontier, detached runtime Tools, and scoped teardown.
Traffic retains warning classifications for transient and isolated losses;
the persisted invocation and qualification output remain the authority for
those outcomes. No hypothetical online execution seam is part of the v1
architecture.

automatic rollback and a public dataplane plugin API are deferred.
realtime/live traffic visualization is also deferred and is not implied by
Round B.

## Authoritative references

- [accepted design](accepted-design.md)
- [system overview](human-readable/system-overview.md)
- [planning contracts](codex-reference/planning-contracts.md)
- [Compiler contract](codex-reference/bpf-compiler.md)
- [producer and persisted runtime](codex-reference/producer-runtime-contracts.md)
- [public imports](codex-reference/public-imports.md)
- [inspection guide](user-guides/inspection-tools.md)
- [已采用优化与宿主机配置](human-readable/optimization-decisions.md)
- [evidence-linked decision history](provenance/optimization-decision-history.md)
