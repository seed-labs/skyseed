# Detached inspection Tools

Tools are read-only observers of completed semantic or runtime records. They do
not advance the Timeline, call Models, publish Plans, compile realizations, or
change Runner state.

## Maintained surfaces

| Tool | Input authority | Primary output |
|---|---|---|
| Physical World | exact PhysicalWorld, sealed PhysicalFrame history, exact Tool-owned candidate stream, and construction descriptors | chunked map-first `inspection/physical-world/` |
| Zone | static Zone layout | `inspection/zone/` |
| Planner | `PublishedPlanningEvidence`, aligned sealed PhysicalFrames, exact candidate stream, and descriptors | compact root index, per-frame/per-route chunks, and persistent staged 3D/2D HTML |
| BPF Compiler | Plan/realization values plus optional Observation records | compact chart/dashboard and drill-down JSON plus HTML |
| Runner | Runner events and snapshots | Runner inspection bundle |
| Observation | generic raw component/stage/duration/work/resource records | Observation bundle |
| Traffic | telemetry plus physical, planning, and Compiler evidence | `traffic-replay.json` plus HTML |

All output roots are invocation-owned. Readers validate schema and provenance;
renderers consume detached projection values only. The separate
`inspection-evidence/physical-link-observations.jsonl.gz` is a deterministic,
checksum-verified stream of facts already produced by the authoritative
execution. The producer's optional sink is called only after Planner consumes
the observation. It has no semantic ownership and is not a second pipeline.

## Meaning separation

Planner inspection must visibly distinguish:

```text
PHYSICAL CANDIDATE
→ SELECTED / ACQUIRING
→ OPERATIONAL CONNECTION
→ ROUTED / UNREACHABLE
→ COMPILED
→ REALIZED
```

Candidate counts and rejection/underfill summaries are evidence, not planned
state. Acquisition start, remaining time, and reference duration remain
Planner lifecycle evidence. Operational parameters, Zone assignment, Route
outcomes, transitions, semantic events, precedence, Plan lineage, and
fingerprints come from canonical planning records.

The Physical World Tool links a 3D ECEF Earth view and a Tool-private 2D
subpoint map across the sealed formal timeline. Position, Physical Link, and
Zone perspectives share one focus. Candidate overlays are loaded per timestamp
and only the focused adjacency is drawn, always as dashed physical input.
Exact frame inspection stays inside PhysicalWorld. It copies authoritative
distance, elevation, delay, feasibility, and work counts from
`PhysicalLinkObservation`; it does not recalculate geometry. Zone focus changes
the map itself, highlighting geometry, feasible Satellites, and relations.
Static Zone inspection shows geography, while operational service appears in
Planner inspection.

Planner renders one persistent world with a compact selector for the accepted
processing order. A stage changes overlays and one detail drawer; it does not
materialize ten payload cards. Frame data is stored in deterministic
timestamp-indexed JS chunks. Route histories are addressed by a stable SHA-256
key in fixed shards and loaded only after a `RouteDemand` is selected. The root
HTML/JSON retains compact inventory, frame indexes, publications, descriptors,
and route indexes rather than all frames, all Routes, or replayed state.
Concrete descriptors are derived from maintained example construction values,
not arbitrary private Model caches.
The renderer shares Tool-local drag/zoom/focus policy across PhysicalWorld and
Planner. Focused ISL and GroundStation candidates are individually selectable;
same-plane and cyclic cross-plane Δ1/Δ2/Δ3 remain explicit. Stage changes keep
the accumulated operational network faint where relevant and clear stale
candidate relationship focus.

With full detached Plan/realization values, the Compiler Tool projects compact
per-realization trends, operation breakdowns, semantic-precedence folding,
per-boundary workload, intended-state cardinalities, and lineage. When supplied,
Observation provides Compiler total and stage timing without conflating
rollups. Four canvas trends make workload, deltas, folding modes, and Compiler
timing primary; one shared boundary cursor derives all structured detail, and
the realization selector is only a synchronized jump shortcut. The Tool retains the small lineage/activation/Zone-prefix contract
needed by Traffic but does not duplicate every raw intent and intended-state
object. The sealed Runner artifact remains semantic payload owner. It never
recompiles.

Traffic accepts `--physical-frames` and `--planning` alongside Compiler
evidence. It uses explicit planned Route outcomes to classify success, planned
unreachable, activation-time transience, and unexplained reachable loss.
Direct collection reads and replays the sealed Plan semantics once, without
materializing unused Planner Model inspection objects. Its immediate projection
reuses reader validation; public mutable-snapshot projection still validates
replay. Compact Traffic JSON streams complete records without changing fields,
event retention, viewer behavior or scoring. Planner inspection retains its
complete Model evidence. Large JSON writing and file hashing avoid full-file
string/byte copies.

## Observation taxonomy

Current semantic groups are physical projection, physical-link observation,
ISL selection, acquisition/lifecycle, GroundStation selection/ranking,
validation, evidence projection and planning rollup, Zone planning,
parameter planning, Route planning, transition/application,
coordination/precedence, Plan publication, Compiler, realization producer,
Runner artifact writer, Runner, and Traffic.

Observation records preserve invocation, component, stage, scope, duration
role, real run-relative monotonic start/end, work/resource counts, Tool wall
time, RSS, and input/output accounting. Its primary projection unions exact
intervals, measures wall time with at least two distinct components active,
and supports zoom/pan plus component-to-stage drill-down. Raw overlap including
nested parent/child spans is advanced diagnostic evidence. It never
reconstructs time from sequence order.
They are diagnostic and do not add semantic authority.

The maintained Starlink orchestration additionally emits a Tool/example-owned
coarse timing artifact. Independent outer phases use monotonic wall time;
explicit category totals distinguish core semantic production, persistence,
detached inspection side-work, and other orchestration. Non-additive nested
records are labelled so Planner/Compiler and their leaf stages are not summed
twice. Interleaved aggregate counters retain their numeric totals but carry no
timeline geometry; only measured monotonic spans are drawn.

Generation writes its completed Observation stream under
`inspection/observation/`. For each generated runtime invocation, the private
runtime wrapper emits real one-shot `runner-total` and `traffic-total` rollups,
then merges them with those generation records into
`inspection/runtime-runs/<invocation-id>/observation/execution-observations.jsonl`.
The Observation Tool reads that combined, invocation-owned stream; it does not
synthesize Runner or Traffic completion from detached projections. This
diagnostic merge does not introduce a public runtime-state contract.

## Failure isolation

A Tool reads only existing records and writes only its own output. Tool failure
must not invalidate a sealed Runner artifact or cause semantic computation
to rerun. Generated `run.sh` preserves a successful runtime outcome when any
detached Runner, Traffic, Observation, or aggregate-index Tool fails, reports a
warning, and exits with the domain execution status. Missing, stale, malformed,
mixed-invocation, or incomplete records fail explicitly within the affected
Tool.
The bounded asynchronous candidate-evidence sink follows authoritative Planner
consumption. Its elapsed time is attributed to inspection capture, and an
exception is retained as a distinct sink failure before the sink is disabled;
the already-valid Planner result is not rolled back.

Current finite generation seals required inputs and publishes MATERIALS_READY
before optional Tools execute. Domain-history v2 permits physical-only evidence
with zero Plans; semantic Tools require their own cutoff. Planner evidence v3
retains future contact proofs and GS preparation. The Planner Tool distinguishes
operational GS assignments from pending preparations and their reserved capacity,
including the original start and remaining preparation time. Historical schema readers stay
available. Tool projection performs its own single replay; readers avoid duplicate
projection replay. Compiler observation input is parsed once per inspection.
Worker durations overlap other stages and must not be added to rollup wall time.
Observation distinguishes sampled initialization history from additional physical
prediction queries. Extra queries do not increase the formal transition count or
extend the displayed experiment interval; physical-only output has zero Planner
transitions while retaining every formal PhysicalFrame.

## Sealed deliveries and retained history

Compiler Tool reads the continuous sealed compiler history plus immutable
execution-definition delivery metadata, displays each delivery range/count and
lets the reader focus its first boundary on the existing timeline. It does not
decode runtime binary data to manufacture a lifecycle. Runner Tool reads actual
batch loading, readiness, activation and reclaim events and draws Maintenance
spans alongside Main, preserving overlap. The known session boundary count is
separate from the first artifact’s count and the current resident plan count.
Planner/physical/Traffic histories retain every formal timestamp and identity
after runtime maps/plans are reclaimed. Aggregate output navigation only links
original portals, with dated comparisons clearly distinguished.

Traffic raw v2 generation deltas and Tool v6 compact incarnation ranges preserve
legacy raw v1 evidence; field definitions and bounded processing are in the
[Traffic README](../../tools/traffic/README.md#focused-correction-compact-traffic-identity-evidence).

## Final finishing Tool data

Shared Shell/orbit filtering uses recorded `(asn, shell_id, orbit_id)` metadata;
no display-name parsing establishes ownership. Planner's additive route-index
fields contain a source node index, typed GS/Zone target and compact activity
status changes. Stable pairs derive from typed GS→Zone demands, with the current Zone assignment
selecting the reverse demand. Both outcomes resolve independently at/before the
cursor; full paths stay in at most two retained shards. Raw demand navigation is
loaded only when its secondary drawer is expanded.
The duplicate `showSpecial()` and shadowed drawer code have been removed.

Traffic private browser schema v2 stores static node/flow/segment metadata once,
positions in per-formal-frame JS chunks and 25 transactions per focused shard.
The episode/probe selection persists across segments while play/speed/seek advance
sampled geometry and schematic request/reply markers. Every frame selection fences
obsolete callbacks, including cache hits. Each observed step references its packet
index and records ingress-supported
arrival, direction, sampled frame, unresolved association and explicit gaps.
Redirect intent does not establish peer arrival; repeated node visits survive.
The full replay remains v6 and raw telemetry v2. External request timestamps use
a unique episode/segment/sequence association and retain null on ambiguity or
missing observation. Compiler's optional spatial context embeds Physical World
and forwards only its existing boundary cursor. No Tool executes Models.

Offline refresh provenance is separate from the retained runtime source. Old
browser/visual gate evidence applies to its original Tool source only; the final
finishing changes have nonvisual validation and pending user visual review.

Zone service is globally unique across the world: one Zone has at most one current
serving satellite, not one per Shell. In the current example, ISLs stay within
Shells and GSs cannot be transit nodes. Geographic three-GS associations and
independent GS allocation do not guarantee those GSs have operational access to
the Zone's serving Shell. Stable logical pairs therefore differ from the underlying
GS→Zone and changing serving-satellite→GS demands. Coordinated Zone/GS selection
and cross-Shell communication research are deferred; this Tool correction changes
neither allocation nor routing or topology.
