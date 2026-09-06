# Precomputed producer, Runner artifact, and runtime

Skyseed v2 supports one execution contract: finite precomputed generation
followed by live replay. `skyseed_v2.experiment._realization_producer` owns the
private cross-domain compute cursor; it never exists inside `BpfRunner`.

## Producer contract

`_IncrementalRealizationProducer` is constructed with one finite
`SimulationTimeline`, `PhysicalWorld`, `PhysicalLinkModel`, and public
`Planner`. `produce_next_realization()` incrementally advances formal frames,
observations, Plan publication, and `compile_bpf`; callers must drain it through
terminal `None`. Failures close it and stop Planner-owned workers.

The producer may be incremental internally so compute stays bounded, but the
runtime contract is not incremental. There is no online supplier, replay input
frontier, exhaustion protocol, Runner backpressure, or live planning seam.

## Compute/runtime boundary

Generation drains all compiled results into `_RunnerArtifactWriter`. The writer
losslessly normalizes already-compiled backend facts, packs deterministic typed
records, incrementally derives exact mechanical runtime-capacity demand while
each normalized boundary is already in memory, validates the complete sequence,
and seals a small format-v3 manifest. It does
not replan Connections, choose Routes, reinterpret Plans, or perform new backend
policy lowering.

The artifact consists of:

| Artifact | Authority |
|---|---|
| `runtime/runner-artifact/manifest.json` | version, encoding, terminal seal, PhysicalWorld/Zone metadata, timeline, lineage, exact counts, binary length/hashes, explicit logical endpoint ports, and strict mechanical `runtime_capacity_demand` |
| `runtime/runner-artifact/runner-runtime.bin` | fixed header and index followed by typed packed symbolic boundary records |
| `runtime/execution.json` | generated identity, source/input/environment fingerprints, timeline, artifact metadata, and `manifest_sha256` |

The binary stores stable identities and symbolic interface references. It never
persists a guessed ifindex for a future Connection. The manifest identifies the
sealed Runner artifact as the semantic payload owner.

Before `BpfRunner` construction, generated execution verifies
`runtime/execution.json`, source and input fingerprints, manifest hash, version,
seal, binary length/hash, and timeline metadata. Runner opens the verified file
read-only with `mmap` during Preparation, validates table bounds, and decodes all
indexed boundaries before formal runtime. It does not reconstruct rich
`BpfCompiledRealization` or `PlannedOperationalState` objects. The complete
finite sequence is materialized into private immutable activation delta plans
only during Preparation.

## Durable domain evidence

The separate producer evidence remains:

| Stream | Authority |
|---|---|
| `physical-frames.jsonl` | formal timestamp and authoritative ECEF positions |
| `planning.jsonl.gz` | canonical published Plan evidence and inspection facts |
| `compiler.jsonl` | compact Plan/realization lineage, fingerprints, timestamps, and intent counts |
| `domain-history-manifest.json` | terminal cadence/count/frontier and stream byte/hash seals |

The domain-history manifest is published only after `produce_next_realization()`
returns `None`. The compact Compiler stream names the artifact through its
`semantic_payload_owner`; it is not a duplicate runtime payload. Detached
readers reject incomplete or mixed evidence.

## Runner lifecycle

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

Boundary time and generation lifetime are distinct. Every completed nominal
boundary produces one compact `boundary_activation` telemetry record, including
parameter-only and no-op boundaries. The record carries its actual Main start
and completion clocks, selected generation, predecessor, startup marker, and
whether publication occurred. Only a real publication carries generation-switch
start/end timestamps; reuse carries neither switch timestamps nor a fabricated
publication duration. Interface snapshots are emitted once per generation and
are keyed by generation, node slot, and live ifindex with stable logical ports.
A background writer performs snapshot expansion and serialization.

Detached Traffic inspection verifies the completed boundary prefix against
formal history and maps each generation to its actual publication boundary.
Packet records preserve the kernel generation, exact interface identity, latest
completed formal boundary, and any in-flight boundary separately from the ideal
clock-derived presentation timestamp. Logical endpoint identity survives
withdrawal and recreation; the new live ifindex has its own generation record.
Startup B0 stays visible in Preparation evidence and is excluded from formal
Main counts, timing distributions, and work views.

Initialization dispatch retains cleanup responsibility before any response is
received. The persistent helper records its owned pin root and node-address
mutations; rejected startup cannot delete conflicting residue. A known absence
of mutation, confirmed cleanup, and uncertain/partial initialization are distinct
outcomes. Lost initialization or cleanup responses retain ownership until
teardown is confirmed; absence of a successful initialization response is never
proof that resources were removed.

Main records compact boundary/barrier timing and enqueues telemetry template
references with live binding deltas. A background writer expands and serializes
identity records; observation never gates the next boundary. Cleanup owns final
network/BPF teardown, telemetry drain, privileged facility closure, and artifact
handle closure. Automatic deadlines use one fixed monotonic runtime origin plus
each nominal offset. Lateness consumes available idle time; an overrun executes
the next boundary when available and reports actual lag. All transitions remain
sequential and complete, with no skipped boundaries or reset origin. The helper
arrival clock is observational. Manual activation uses the same ordered plans.

### Private detailed timing evidence

`activation_end.detailed_timing_version: 2` retains bounded
`operation_timings.<category>.{duration_seconds,operation_count}`. Ordinary
categories measure attempted call wall time. Physical deletion instead measures
joined critical wall time and confirmed retired pairs. Its separate `retirement`
aggregate reports `retired_pairs`, actual `deletion_requests`, confirmed
`grouped_batches`, `workers`, `wall_seconds`, and `task_duration_seconds_sum`.
Concurrent task-duration sums are never elapsed critical-path time. The
`withdrawal` and `physical_deletion` barriers are nested inside `post_publication`.
Two monotonic reads surround ordinary measured calls, without per-call records.
Publication reuses its timestamp pair. Version 1 historical evidence retains
its original serial attempted-deletion definition.

| Private category | Existing call/block measured |
|---|---|
| `pre_veth_creation` | create one veth pair |
| `pre_ifindex_resolution` | resolve one newly created live interface |
| `pre_mac_link_configuration` | historical standalone MAC calls; absent in v2 |
| `pre_qdisc_creation` / `pre_existing_qdisc_update` | new netem / update retained netem |
| `pre_bpf_attachment` | attach ingress BPF (can issue multiple netlink requests) |
| `pre_ingress_binding_update` / `pre_egress_binding_update` | update each live binding map |
| `pre_route_replacement` / `pre_neighbour_replacement` | replace route / neighbour |
| `pre_zone_address_addition` / `pre_link_up` | add address / bring interface up |
| `pre_local_bookkeeping` | historical route-set construction; normalized in Preparation in v2 |
| `active_generation_update` | actual publication attempt at the existing switch clocks |
| `post_route_deletion` / `post_neighbour_deletion` | delete route / neighbour |
| `post_zone_address_removal` | remove Zone address |
| `post_ingress_binding_deletion` / `post_egress_binding_deletion` | delete live binding |
| `post_interface_down` / `post_veth_deletion` | DOWN absent in v2 / joined physical deletion wall time and confirmed pair count |
| `post_local_bookkeeping` | historical route-set and interface-ledger blocks; v2 retirement includes ledger completion |

An absent category in this version means no attempted calls. Generation reuse
has zero publication attempts and no publication duration sample. A failed
publication retains its attempted duration but no successful switch end.
Failure evidence retains completed/attempted aggregates; incomplete barriers
remain unavailable. Rollback is not separately timed: it remains inside helper
activation/dispatch and actual syscall counts. Failed activations do not enter
successful steady distributions.

`command_timing` travels in the existing helper response and activation/cleanup
events (or attached failure evidence). Parent monotonic-nanosecond stamps name
`command_start`, `encode_start/end`, `write_start/end`, `read_end`,
`decode_start/end`, and `command_end`. Their scalar spans are
`command_seconds`, `json_encode_seconds`, `channel_write_seconds`,
`channel_wait_seconds` and `response_decode_seconds`. Main reuses its immutable
wire line, so `json_encode_seconds` is known zero; helper decode remains measured.
Request/response byte
counts describe the existing ASCII JSON lines. `command_seconds` includes lock
acquisition, helper execution and surrounding local work; write/wait includes
helper execution and cannot be added to it. Missing partial-response spans are
null; error types and cleanup ownership remain authoritative.

`command_timing.helper` supplies `line_received_monotonic_ns` (after the full
line is received), `request_decoded_monotonic_ns`, dispatch start/end stamps,
`request_decode_seconds`, `dispatch_seconds`, and `kernel_calls` including joined retirement-worker deltas.
The counters are actual netlink request attempts and BPF lookup/update/delete/
batch-update syscall attempts, including failures, incremented at existing
execution sites using thread-local counters without a per-operation global lock. Concurrent initialization reports null counts. External
process counts are unavailable. No extra queries, namespace entries, scans or
monitoring processes are used. Response encode/write and transport/scheduling
cannot be isolated exactly and remain in the exchange residual.

`helper_activation_start_monotonic_ns` and
`helper_activation_evidence_monotonic_ns` bound activation through evidence
construction; dispatch includes that span plus surrounding work.
`parent_runtime_timing` separates `request_preparation_seconds` from
`result_ledger_seconds`. The original `main_start_monotonic_seconds`,
`main_end_monotonic_seconds`, generation switch stamps and formal ACTIVE commit
position are retained. `helper_pacing_anchor_monotonic_ns` is observational;
`boundary_scheduled.pacing_mode` is `fixed-origin`, with equal ideal and paced
deadlines. Offline pacing reports actual start/completion lag and the
parent-to-helper prefix. The historical previous-anchor carry residual is null
for fixed-origin records; elapsed time is never subtracted from observations.

One asynchronous `activation_observation_end` event per completed boundary
measures `formal_commit_seconds`, `identity_enqueue_seconds`,
`activation_event_record_seconds`, and their inclusive
`post_main_observation_seconds`. Its own record call and the final condition
notification remain outside that span. Identity expansion stays in the existing
background writer. No Main flush, report, percentile computation, or accounting
assertion is added.

Preparation stage events separate artifact loading/validation, finite-boundary
materialization, identity discovery, helper startup, runtime initialization, and
finite-plan construction/prewrite. The last stage's `phases` separates exclusive
`finite_plan_construction_seconds` (including materialization and nested
`activation_wire_encoding_seconds`) from inclusive
`semantic_prewrite_roundtrip_seconds`; nested `semantic_prewrite_helper_seconds`
and prewrite/boundary counts describe successful completed preparation only.
Partial failed preparation does not invent missing stage durations. Physical B0
remains an activation inside Preparation and outside steady Main statistics.

Cleanup retains its original end boundary. Parent `parent_cleanup_timing`
separates request construction and result-ledger handling. Helper
`cleanup_phases` separates remaining network deletion, address removal, BPF
unpinning and retained descriptor/socket closure; nested `cleanup_operations`
retains actual call/block durations and attempts. `cleanup_end.phases` records
inclusive runtime teardown, telemetry drain, helper shutdown and artifact-handle
closure. Other owner work is a residual. `cleanup_finalization` in the detached
snapshot records event-history drain after `cleanup_end`. Partial teardown
evidence never overrides uncertain ownership or a reported cleanup failure.

The Runner Tool computes linear-interpolated percentiles and signed residuals
offline. Its hierarchy is Main → command → helper dispatch → activation →
barriers → operation families; sibling durations can be reconciled, nested
durations cannot be summed. Main residual subtracts parent preparation,
inclusive command and parent ledger. Exchange residual subtracts request
decode, pre-dispatch and dispatch from parent write-start to read-end. Barrier
residuals subtract their exclusive operation aggregates. Residuals include
unmeasured local work and observation costs and are not pure IPC or kernel CPU
measurements. The sum of phase percentiles is not the percentile of total Main.

## BPF representation

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

## Runtime observations and Tools

The generated wrapper records `runner-total` and `traffic-total` rollups in the
combined, invocation-owned stream. Runner, Traffic, Observation, and other Tools
are detached non-authoritative consumers. Tool failure cannot replace a
successful runtime outcome.

There is deliberately no hypothetical online execution seam in the v1 contract.
