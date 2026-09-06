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
The synchronous candidate-evidence sink follows authoritative Planner
consumption. Its elapsed time is attributed to inspection capture, and an
exception is retained as a distinct sink failure before the sink is disabled;
the already-valid Planner result is not rolled back.
