# Traffic Tool

This detached Tool combines formal PhysicalFrame-history ECEF evidence, canonical
planning evidence with explicit Route outcomes, Compiler intended-state
lineage, runtime traffic telemetry, and optional probe/Runner evidence.

The CLI emits a `traffic-tool-completion` JSON line with its returned artifact
metrics. Its wall time covers the Tool function and excludes interpreter startup;
these measurements stay in the invocation log, outside the visualization export.

Generated execution invokes it with `--physical-frames`, `--planning`, and
`--physical-links` paths. The last supplies sealed typed Shell membership;
older direct invocations may omit it and retain incomplete coverage evidence.
Compiler inspection v3 supplies compact lineage, activation timestamps, intent
counts, and stable Zone prefixes. Compact `compiler.jsonl` supplies sealed
lineage, fingerprints, activation timestamps, and intent counts without Zone
prefixes; the separate sealed Runner artifact owns the normalized backend
payload.

Each ICMP transaction retains all request/reply hop indices. Fixed and Zone
endpoints use recorded local delivery; generator lifetime, identifier, sequence
and direction establish association. Reuse, wrap or late replies that cannot
be resolved remain explicitly uncertain. Hop observations, transactions and
executor sent/received totals are different counts.

Each request is matched to the actual completed or in-flight published state. Output
shows source/destination direction and its explicit planned path or unreachable
reason, then distinguishes:

- explicit planned unreachable;
- observed planned-reachable traffic;
- activation-associated loss awaiting recovery evidence;
- expected handover disturbance with bounded, observed recovery;
- unexplained loss while planned reachable;
- unclassified evidence.

Missing forwarding is diagnostic and is never treated as proof of planned
unreachability.

Raw sends, replies and loss are reported unchanged. Executor conformance counts
distinct generator-segment/sequence cases, reconciling repeated reports and late
replies. A missing send timestamp is bounded conservatively by recorded `ping
-D/-O` outstanding reports and host clocks, or by generator launch and receipt
when those clocks are unavailable. Planned reachability must agree throughout
the interval; a report timestamp alone cannot identify the send's planned state.
Unaccounted sends and unresolved association remain incomplete evidence.

Planned unreachability is informational. Normal handover disturbance requires
an actual relevant path transition or recorded source migration, plus recovery
of the logical probe within two seconds by default. Mere proximity to Main is
insufficient. The plan's `qualification_policy.maximum_transition_disruption_seconds`
can set a positive finite bound; the Tool invocation can override it with
`SKYSEED_TRAFFIC_MAX_TRANSITION_DISRUPTION_SECONDS`. Excessive or unexplained loss
remains WARN; persistent reachable blackholes and infrastructure/evidence
failures remain FAIL. Coverage is assessed separately from conformance.

The experiment-side builders accept authoritative `physical_world` metadata.
Their default global limits are 20 episodes, eight concurrent generators and
3,600 estimated echo requests for the whole world. Selection prioritizes both
directions and stable/transition cases for each Shell with eligible bilateral
planned paths, then existing diversity criteria. `global_budget`, estimates,
per-Shell eligibility and actual observed coverage are displayed. Overlapping
path coverage is not a per-Shell packet total; historical zero selection does
not establish that no eligible path existed.

Physical and Planner share Shell multi-select, solo/all controls, stable colors,
independent GS/Zone visibility and one time cursor. Filtering preserves ECEF
coordinates and altitude. Traffic applies the same typed membership to paths,
transactions and coverage, with an all-times option and at most 25 transaction
rows per page. Visible/global counts and hidden path endpoints remain explicit.

The Tool writes `traffic-replay.json`, standalone HTML, summary, inventory,
checksums, and wall/RSS/byte accounting. It neither controls Runner nor
recomputes physical or Route semantics.

The optional Runner-observer summary consumes complete Main duration from
current `activation_end.main_seconds`. It keeps `runtime_apply_seconds` and
`activation_switch_seconds` semantically distinct and does not substitute
either for Main duration. Missing or invalid optional Main timing produces an
empty distribution with `null` percentiles/max rather than a fabricated zero.

## Boundary and generation evidence

The maintained telemetry writer records every completed nominal boundary in
`boundary_activation`, including startup, parameter-only and no-op transitions.
Interface identities describe one generation lifetime, not one boundary index.
The reader checks the complete activation prefix against formal history, maps
interface snapshots to actual publication boundaries, and retains stable logical
ports across withdrawal/recreation with fresh ifindexes. It keeps packet kernel
generation and actual completed/in-flight boundary attribution separate from the
ideal wall-clock presentation timestamp. No repeated interface snapshot is
needed for a reused generation.
Within an actual in-flight Main interval, adjacent snapshots also identify
new or retiring physical interfaces whose selected generation excludes them.
This physical attribution does not add those links to the completed semantic
graph. Unrelated past/future incarnations are never used as a fallback.

Qualification associates transition disturbance only with actual relevant Main
or source-migration evidence and bounded recovery. Reused generations carry no
switch timestamps. Formal Main
statistics exclude Preparation's boundary 0; all boundaries remain in the raw
and projected history.

The direct collection path reads the validated semantic Plan portion of sealed
planning history and performs one replay, checking input/output fingerprints
and exact coverage. It does not reconstruct Model inspection objects unused by
Traffic; those remain intact in the sealed source and are consumed by Planner
inspection. Its immediate projection reuses this validation. Public projection
of a caller-supplied mutable snapshot still validates the complete replay.

`traffic-replay.json` keeps the same complete fields in compact JSON. Writing
streams sections and individual records through the standard C JSON encoder;
no whole-history formatted string is assembled. File hashes are streamed and
payload digests are shared by inventory/checksum publication. The source hashes
and disclosed qualification policy identify the projection; filtering does not
alter the underlying evidence or scoring.

The offline `python -m tools.planner.export` entry consumes sealed formal history
and completed Tool outputs to write versioned full network snapshots and compact
observed traffic outside the repository. Network and Traffic remain explicitly
separate; no internal deltas, runtime timing, host resource traces or bulk hop
telemetry enter this temporary delivery. Chinese READMEs describe all fields.

## Focused correction: compact Traffic identity evidence

Raw telemetry version 2 retains all packet and loss rows. The existing background
Recorder emits `interface_update` endpoint facts (logical port, node/peer, connection,
ifindex and validity) only when changed; each update merges the supplied fields into the logical port's
previous record. Generation-varying route destination metadata is retained in
shared sets and ranges by the Reader. An `interface_generation` record commits
those updates and withdrawn logical ports to one generation and nominal boundary.
Unchanged generation reuse emits only the existing boundary activation. No new owner
or runtime mode is introduced. Version 1 full `interface_identity` rows remain readable.

The Reader scans JSONL twice without keeping raw identity rows: one pass retains
packet/loss/clock/boundary evidence; the second validates at most one expanded
generation at a time. Its compact interface representation has `format_version: 2`,
shared `endpoints`, `route_destination_sets` (lists of address strings), each endpoint's
`route_destination_ranges` as inclusive `[first_generation, last_generation, set_index]`,
and `incarnations` as inclusive
`[first_generation, last_generation, ifindex]` ranges, a shared `generations` table
of `[generation, boundary_timestamp]`, and `identity_observations`. A missing generation
or changed ifindex splits a range. Logical identities and peer/capacity-independent
endpoint validation remain exact across ranges. Packet lookup includes the two
neighboring generation memberships only during an evidenced transition.

Traffic projection version 6 carries this compact representation through Tool
serialization; it does not recreate a dictionary for every generation endpoint.
Packet, executor request, reconstructed transaction and endpoint-delivery counts
remain independent. `raw_record_count` counts actual serialized rows;
`identity_observations` counts expanded generation endpoint facts. Incomplete
recorders, lost events and uncertain associations retain their explicit states.

## Continuous communication replay and external request times

The rotatable/zoomable globe defaults to a communication identified by the recorded
`episode_id` and `probe_id`. Its segment list and transactions preserve continuity
through generator rearming or physical-source handover. Display names never merge
flows. Transactions lacking a complete authoritative association remain separate;
qualification-only episodes stay selectable with their executor loss counts and
no invented packet path. Individual transactions and ordered request/reply hops
remain in expandable secondary inspection, with at most 25 rows per page.

Play/pause, 0.25×/1×/5×/20× speed and a seconds-based seek control drive a shared
playback clock. Geometry advances to the latest retained formal PhysicalFrame at
or before that clock; an observation's actual timestamp remains visible in detail.
Each exchange is illustrated for up to one simulated second **after** its final
recorded observation, stopping at the next retained exchange. Cyan request and
orange reply markers traverse only ingress-supported arrival edges in recorded
order. This schematic timing and straight-line marker motion improve readability;
they do not measure transit timing or interpolate satellite positions. High speed
may aggregate repetitive exchanges between display ticks.

Repeated node visits, missing ingress/peer observations, final unobserved redirects,
missing replies and ambiguous associations retain their meanings. No marker bridges
an unobserved edge; ambiguous exchanges have no connected animation. Idle intervals
show no current exchange, while the last retained transaction remains available in
details. The dashed-purple overlay is that exchange's recorded **request** Plan,
never observed passage or an invented reverse Route. Executor cases without packet
records remain qualification evidence, not animated success.

Private browser schema v2 has one shared node index, a compact episode/segment and
transaction index, one retained `data/frame-NNN.js` coordinate chunk, and one
`data/transactions-NNN.js` shard of at most 25 transactions. Every explicit frame
selection invalidates pending loads before checking the cache; playback and seek
callbacks cannot install obsolete geometry. Until the requested frame arrives,
its overlays are withheld. Script elements and global payload slots are released,
not accumulated during play. No world is copied per packet and the complete replay
is never embedded in the page. Shell/Orbit controls preserve hidden endpoint labels.

Raw telemetry v2, complete replay projection v6, classification, qualification and
original Tool/runtime accounting remain unchanged. Offline presentation refresh
records current Tool source separately from frozen runtime source. The existing
external export v1 associates executor request cases using episode/segment/sequence;
observed timestamps remain null for missing or ambiguous evidence. This presentation
round does not regenerate or modify that export.

## Scoped endpoints

Probe-plan v2 stores `network_scope` beside geographic `zone_id`. Identity hashes,
request and echo-return Route targets, source migration and destination IPv4
resolution preserve that scope. Tool projection v7 and flow labels retain it;
Shell selection uses recorded scope without substituting another Zone service.
Observed paths and routing discrepancies remain available even when their nodes
fall outside the selected Shell. No visual acceptance is inferred from data tests.
