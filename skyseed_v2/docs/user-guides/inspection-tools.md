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
complete realization owned by the separate checksum handoff. Neither Tool runs
its owning algorithm.
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
Route planning starts from a source selected on either map, lists only that
source's concise `RouteDemand` destinations, and loads one deterministic route
timeline from a hashed shard, showing its exact complete path or unreachable
reason. Exact-ID search remains a collapsed advanced shortcut.
The drawer shows concrete implementation/configuration descriptors assembled
from the same public constants and construction arguments that instantiate the
maintained example Models.

Physical World uses Position, Physical Link Observation, and Zone perspectives
over one linked 3D/2D workspace. It is the sole exact PhysicalFrame inspection
surface: selecting a Zone highlights its real rectangular boundary on both
maps, feasible Satellites, and exact candidate relations at that timestamp.
Both maps support drag navigation, wheel zoom, reset/focus controls, empty-space
clear, repeated-selection clear, and Escape.

Compiler aligns all 121 formal boundaries and 30 realization coverage windows
on one simulation-time cursor. Clicking the shared timeline or any of four
canvas trends updates the owning realization, boundary workload, lineage, and
measured compile timing. The machine JSON remains a separate artifact; the
default human view has no giant realization table or raw JSON dump.

## Observation

Observation groups the actual chain: physical projection, physical-link
observation, ISL selection, lifecycle, GroundStation selection/ranking,
validation, evidence projection and rollup, Zone/parameter/Route planning,
transition application, precedence, publication, Compiler, producer, handoff
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

## Failure isolation

Each Tool owns only its output. Missing, malformed, stale, or mixed-invocation
records produce a conspicuous failure artifact or exception. A Tool failure
does not invalidate the persisted realization handoff or rerun computation. If
the generated runtime itself succeeded, `run.sh` warns about detached Tool
failure and retains that successful exit outcome.
The exact PhysicalLink candidate sink is attempted only after Planner consumes
the authoritative observation. Its duration and failure are recorded
separately; the first sink failure disables further capture while accepted
domain progression continues.
