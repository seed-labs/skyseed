# Inspection Tools

Inspection is detached from semantic computation. Generate an example first,
then open `examples/<example>/output/inspection/index.html` or the individual
Tool output. That default is anchored to the example source directory, not the
shell's current directory; `--output PATH` overrides it.

## Output map

| Surface | Directory or file | Meaning |
|---|---|---|
| Physical World | `inspection/physical-world/` | map-first linked 3D/2D world, layer perspectives, exact focused candidates, concrete Model descriptors |
| Zone | `inspection/zone/` | static geography and membership |
| Planner | `inspection/planner/` and `planner-inspection.json` | persistent 3D/2D world with selectable planning-stage overlays and focused drill-down |
| BPF Compiler | `inspection/bpf-compiler/` and `bpf-compiler-inspection.json` | trend dashboard, realization/boundary drill-down, folding, timing, and cardinalities |
| Observation | `inspection/observation/` | real monotonic component/stage spans, overlap diagnostics, work/resources, and outer orchestration phases |
| Runner | `inspection/runner/` | post-run activation and realized-state evidence |
| Traffic | `inspection/traffic/` and `traffic-replay.json` | probes, explicit planned reachability, runtime classification |

## Producer evidence

Generated Mini and canonical Compiler mode write:

```text
output/domain-history/
├── physical-frames.jsonl
├── planning.jsonl.gz
├── compiler.jsonl
└── domain-history-manifest.json

output/inspection-evidence/
├── physical-link-observations.jsonl.gz
└── physical-link-observations-manifest.json
```

Planner Tool streams `PublishedPlanningEvidence` from `planning.jsonl.gz` into
per-frame and Route shards without retaining an all-S3 projection.
Compiler Tool consumes producer-owned compact Compiler history for trends,
operation breakdowns, folding, boundary workload, intended-state cardinality,
and lineage/activation/Zone-prefix evidence. Its compact
Compiler-history reader validates only lineage, fingerprints, activation
timestamps, and intent counts. The compact stream does not reconstruct the
normalized runtime payload owned by the sealed finite Runner artifact. Neither
Tool runs its owning algorithm.
Durable reads require the terminal sibling manifest and verify exact byte/hash,
Timeline cadence, stream counts, cross-stream lineage, replay fingerprints, and
final frontiers before rendering.

The inspection-evidence stream records exact compact
ISL/GroundStation/Zone candidate tuples and work counts immediately after each
authoritative `PhysicalLinkObservation` is used. It neither reruns a Model nor
adds candidates to Plan or realization payloads. Its manifest verifies bytes,
SHA-256, formal timestamps, inventory, and candidate totals.

Traffic Tool accepts telemetry plus:

```text
--physical-frames output/domain-history/physical-frames.jsonl
--planning        output/domain-history/planning.jsonl.gz
```

It also consumes full or compact Compiler evidence. Its classification keeps
planned unreachable, activation-time transience, unexplained reachable loss,
and success separate.

## Reading Planner inspection

Follow the lifecycle labels in order:

```text
PHYSICAL CANDIDATE
→ SELECTED / ACQUIRING
→ OPERATIONAL CONNECTION
→ ROUTED / UNREACHABLE
→ COMPILED
→ REALIZED
```

Candidate counts are feasibility evidence, not operational state. Acquisition
records show start, remaining time, and reference duration. Route failures show
explicit reasons. Plan panels show transitions, derived semantic events,
precedence, frontiers, and fingerprints.

Use the formal-boundary slider to move the linked 3D and 2D planned-network
views. The world stays visible while the stage selector changes only its
overlay and focused drawer. Physical input, ISL selection, lifecycle,
GroundStation allocation/ranking with its subordinate validation gate, Zone
service, parameters, Routes, coordination, and publication follow the accepted processing order.
Candidate, selected, acquiring, operational, and routed relations remain
visually distinct.

GroundStation and Zone focus reveal only their exact relevant candidates,
ranks/selections, and capacity or service context; the browser does not draw
the global candidate graph. Parameter focus reports previous → current values.
Route planning defaults to stable Zone–GS pairs. Search the Zone or GS identity,
select one pair, then move the time cursor or play through boundaries. The same
pair stays selected while its globally unique serving satellite changes. The
current-serving Shell/Orbit filters the list; an unserved-pair toggle keeps gaps
inspectable. Cyan GS→Zone and dashed-orange Zone→GS paths resolve from their own
recorded demands, with explicit unreachable/absent states. Expand raw evidence to
inspect demand identities, changes and entity search. The example's geographic
three-GS association is unchanged; it does not create every GS→every Zone Route.
The drawer shows concrete implementation/configuration descriptors assembled
from the same public constants and construction arguments that instantiate the
maintained example Models.

Physical World uses Position, Physical Link Observation, and Zone perspectives
over one linked 3D/2D workspace. It is the sole exact PhysicalFrame inspection
surface: selecting a Zone highlights its real rectangular boundary on both
maps, feasible Satellites, and exact candidate relations at that timestamp.
Both maps support drag navigation, wheel zoom, reset/focus controls, empty-space
clear, repeated-selection clear, and Escape.

Compiler aligns every recorded formal boundary and realization coverage window
on one simulation-time cursor: 121/four for the 5-second matrix conditions and
601/19 for S1 at 1 second. Clicking the shared timeline or any of four
canvas trends updates the owning realization, boundary workload, lineage, and
measured compile timing. The machine JSON remains a separate artifact; the
default human view has no giant realization table or raw JSON dump.

## Observation

Observation groups the actual chain: physical projection, physical-link
observation, ISL selection, lifecycle, GroundStation selection/ranking,
validation, evidence projection and rollup, Zone/parameter/Route planning,
transition application, precedence, publication, Compiler, producer, artifact
writer, Runner, and Traffic. Each generated runtime invocation adds actual
`runner-total` and `traffic-total` rollups to the completed generation records
in its own `inspection/runtime-runs/<invocation-id>/observation/` stream.
Records are diagnostic and do not change domain truth.
The primary view is a hierarchical wall-time chart made from recorded
run-relative monotonic spans and outer orchestration phases. Click a component
to expand its stages; use wheel zoom, horizontal drag, reset, and focus-selected
controls for long traces. Busy union, wall time with at least two distinct
components active, cross-component overlap ratio, maximum active distinct
components, per-component active wall, and recorded pipeline gaps are computed
from interval geometry rather than record sequence. Raw nested span overlap is
collapsed under advanced diagnostics.

The Starlink example also writes `inspection/orchestration-timing.json` and its
browser `.js` companion. These monotonic outer records separate assembly,
semantic generation, sealing/persistence, runtime packaging, detached Tool
generation, and final hashing. Category totals and residual are labelled as
outer diagnostic wall time; nested Planner/Compiler rollups are not added to
their child stages. Interleaved accumulated durations remain numeric aggregate
rows and are never drawn as fabricated contiguous spans.

## Runner and Traffic runtime evidence

Preparation includes complete artifact decoding, finite delta construction,
semantic prewrites and physical B0 activation. Runner views keep B0 in startup
evidence and exclude it from formal Main counts and timing distributions. Main
begins at B1; its duration includes the current boundary's joined physical
deletion. Concurrent deletion task-duration sums are not elapsed Main time.

Generation publication and completed formal ACTIVE are separate facts. Reused
generations have no fabricated publication interval. Traffic preserves each
packet's kernel generation, interface incarnation and completed/in-flight
boundary attribution separately from the ideal presentation clock. Logical ports
retain endpoint identity across recreation; a new live ifindex has its own
generation identity record. Asynchronous identity expansion belongs to existing
observation facilities and does not create a network lifecycle or gate Main.

## Failure isolation

Each Tool owns only its output. Missing, malformed, stale, or mixed-invocation
records produce a conspicuous failure artifact or exception. A Tool failure
does not invalidate the sealed Runner artifact or rerun computation. If
the generated runtime itself succeeded, `run.sh` warns about detached Tool
failure and retains that successful exit outcome.
The exact PhysicalLink candidate sink is attempted only after Planner consumes
the authoritative observation. Its duration and failure are recorded
separately; the first sink failure disables further capture while accepted
domain progression continues.

Generation writes `generation-status.json` when runtime materials are ready,
then updates it after all optional Tools finish or fail. `materials_ready_seconds`
and `tools_completion_seconds` share the main-entry origin; `tools_wall_seconds`
is the later Tool interval. Use the generated package once materials are ready;
Tools are not an execution prerequisite. Qualification still collects every Tool
result. Planner preparation evidence includes GS reservations and physical contact
proof; it is not an operational connection before preparation completes.

For continuous deliveries, open the Compiler Tool’s **Sealed deliveries** table
and select **Focus first boundary** at the delivery seam. Open Runner Tool’s
**Delivery and Maintenance** section to compare readiness margin with actual
Main timing; the graph shows overlapping load/prewrite and reclaim work.
The latest normal output retains its evidence after runtime reclamation. The
[five-Shell portal](../../examples/starlink_multi_shell_satellite_internet/output/inspection/index.html)
opens that runtime; [retained measurements](../../examples/starlink_multi_shell_satellite_internet/output/qualification/README.md)
contain selected historical summaries. Superseded full portals/raw archives were
deleted, so those summaries do not provide a runnable historical environment.
S3 retains a generation-only portal; Mini retains partial historical data without
a current Tool portal. Normal outputs stay ignored and future runs may replace them.

## Shell/orbit focus and Traffic progression

Physical World, Planner and Traffic share a compact Shell → Orbit dropdown, All
orbits and Reset. AS ownership disambiguates repeated Shell/orbit labels. Existing
Shell multi-select and shared GS/Zone visibility remain independent. Hidden
focused endpoints are marked; visibility does not change the network evidence.
Compiler's optional Spatial context drawer lazily embeds the same Physical World
view, linked to its formal-boundary cursor.

In Traffic, select a communication episode first, then use Play/Pause, speed and
Seek time. The selected episode/probe continues across its recorded segments.
Geometry follows retained formal samples; cyan request and orange reply markers
show only ingress-supported arrivals. Each exchange is illustrated for up to one
simulated second after its last observation, with schematic timing and motion.
Idle gaps, absent replies and ambiguity remain visible. The optional dashed-purple
request Plan is separate from observed passage. Expand transaction/hop evidence for
manual inspection; Return to flow replay restores the continuous time cursor.
No full replay or world-per-packet data is loaded into the page.

The September 10 presentation refresh uses the existing successful experiment.
Runtime identity, raw evidence, sealed materials, WARN findings and original
measurements remain preserved. Updated Tools carry separate source provenance.
This round performs nonvisual functional/data checks only; visual/layout review
belongs to the user and has **not** been marked passed.
