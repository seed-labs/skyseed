# Inspection Tools contract

## Authority

Normative detail under `../accepted-design.md`. Implementation state is
recorded only in [`CURRENT.md`](../CURRENT.md).

## Scope

Repository-local Topology, Zone, Physical Frame, temporary legacy Slice /
Difference, Planner, System Observation, BPF Compiler, post-run BPF Runner,
and post-run Traffic / Path Replay inspection pipelines.
Tools are read-only consumers and are not installed `skyseed_v2` product API.

The accepted destination keeps physical inspection separate from future
planned-operational inspection. The current Slice Tool is only a consumer of
the Round-1 legacy mixed-state seam and is scheduled for removal with Slicer;
it is not the accepted successor to the Physical Frame Tool.

## Owners and consumers

Each Tool preserves this responsibility boundary:

```text
reader → projection → display_analysis → renderer → artifact_writer
```

The Topology and Zone formal readers consume one exact `Topology`. The
Physical Frame reader consumes that exact `Topology` together with one exact,
already-created `PhysicalFrame` supplied by its caller. It reads the frame's
timestamp and Position mapping plus static Topology interpretation; it never
calls `Topology.frame_at()`, invokes the Topology-owned `PositionModel`, or
requests another timestamp. The temporary Slice reader consumes one exact
legacy `Slicer`; the Planner reader additionally consumes consecutive
immutable `Plan` publications, exact consumed `TopologyDifference` values,
and retained `PlanningDecision` values supplied by scenario assembly.
Projection and later responsibilities consume only Tool-owned detached
values. A small Tool may colocate adjacent responsibilities when the boundary
remains explicit; file count and filenames are not part of the contract.
Examples call the Tool facades; browser smoke consumes emitted HTML from the
validation side.

The BPF Compiler reader instead consumes consecutive immutable Plans and the
aligned `BpfCompiledRealization` values already produced by an explicit
backend-side caller. Its facade never invokes `compile_bpf`, queries Simulator
facts, or binds runtime/kernel resources.

The Runner Tool needs no formal-object reader: private generated execution
supplies the plain detached result of `BpfRunner.inspection_snapshot()` plus
its plain invocation-outcome mapping after cleanup. Projection owns event
classification, stable monotonic-time ordering, timing derivation, the frozen
runtime-binding view, and the current-default-dataplane recorded-evidence view.
The two evidence domains remain separate. The Tool never retains a live
Runner, invokes lifecycle methods, or performs fresh Docker/kernel reads.

Traffic Replay instead reads three required already-written artifacts:
Runner-owned traffic JSONL, the Slice / Difference manifest plus offline Slice
chunks, and the BPF Compiler manifest plus exact evidence chunks. An
example-owned planned workload is optional context. Legacy monolithic Compiler
JSON remains a reader compatibility case, not the generated canonical layout.
Its reader validates timeline, NodeId, Zone prefix, nominal boundary, and
generation identity; it reconstructs exact ACTIVE-generation physical network
context only from reciprocal telemetry interface identities. Projection pairs
only unambiguous supported ICMP request/reply records, maps kernel monotonic
time through the persisted simulation clock anchor, and uses retained ECEF
Position samples. It derives observed ICMP flows directly from telemetry; a
workload never supplies the primary traffic identity. A Zone remains a stable logical endpoint and its current
service satellite is the physical path endpoint. Linear ECEF interpolation is
explicitly display-only; the Tool never invokes a Compiler or calls/copies a
PositionModel. Scheduled slots without tc-BPF observation remain qualified
gaps, not claimed timeouts or packet loss.

## Contract

- the formal-reader responsibility performs every Topology/PhysicalFrame/
  temporary-Slicer read and returns a Tool-owned read snapshot with no retained
  formal object;
- the projection responsibility converts only that snapshot into private
  serializable view data and never imports or invokes a formal domain object;
- display analysis adds explicitly non-authoritative display values,
  including orbit-plane fitting/eigendecomposition, rectangle-boundary
  sampling, focus visibility, guides, and classifications;
- rendering accepts final view data and returns standalone display text;
- artifact writing writes supplied data/page bytes and owns no rendering or
  formal reads;
- the Tool facade composes the responsibilities and preserves its Tool-facing
  function.

The Physical Frame Tool reads one caller-supplied `Topology + PhysicalFrame`
pair. Its detached snapshot contains the one timestamp, exact formal
`EarthFixedPosition` ECEF coordinates, formal WGS84 geodetic views, and only
static node/orbit identity and configured access-capability facts needed to
interpret those positions. Tool-private counts, map placement, and display
notes are non-authoritative. The Tool has no history and no Slice/frame ID,
Connection, Route, Zone-serving assignment, effective resource allocation,
Difference, Planner decision, Plan, or realized-runtime field. In particular,
the facade performs no physical projection call: the caller, not the Tool,
decides whether and when to call `Topology.frame_at()`.

The old Slice Tool is a temporary legacy mixed-state consumer. It reads
retained legacy `TopologySlice` next-hop, Position, Connection, Route,
Zone-serving, and adjacent Difference facts only because existing
Planner/Compiler consumers have not yet migrated. It is scheduled for removal
with Slicer and the old Slice/Difference path. Until then, it does not eagerly
resolve every route. It writes one small manifest plus one offline script
chunk per retained Slice so normal page load materializes only the selected
Slice. Slice, Planner, and Compiler exact chunks use deterministic
`gzip-base64-json` envelopes with uncompressed byte-count/SHA-256 metadata;
their browsers decode only selected chunks and retain direct `file://`
operation. Its renderer may preserve selected object identities, visualize
contextual Zone service and Difference facts, and restore one explicitly
selected path for display, but does not acquire routing authority.
The generation facade may receive a detached scenario/model-context mapping
for compact human explanation. Display analysis combines that mapping with
read Topology/Slice facts to produce current counts and physical summaries;
derived envelope/separation values remain Tool-private and are labeled as
such. No Tool-context field enters a formal Simulator value or public schema.
Planner inspection may adapt the same compact read facts for its existing
focused view and reads actual Plan transitions, temporal indexes, precedence
edges, and retained decisions. It uses an external bootstrap manifest plus
hierarchical lazy catalog/data chunks, never calls `create_difference()`,
never eagerly restores all paths, and never reconstructs rejected candidates
or an expected Plan. Zone inspection reads
only the formal static layout/geometry. Its browser hit testing is
display-only navigation and is not a membership result.

System Observation is a detached-evidence pipeline. A private optional
recorder buffers component/stage/scope/duration/work/resource records while
real execution occurs and persists them afterward. The Tool imports no domain
execution package and performs only parse, validate, aggregate, and render.
Bootstrap records are excluded from steady percentiles. Current component
vocabulary covers Slicer and Planner; future Compiler/Runner emitters extend
records rather than domain values or public Simulator APIs. Leaf stages,
overlapping rollups, and zero-duration resource samples remain separate
aggregation roles; no component or pipeline duration is inferred by summing
them, and time-per-work uses positive work on leaf-stage records only.

Topology, Zone, and Physical Frame rendering may embed the frozen Tool-private
Natural Earth 1:110m coastline coordinate asset for offline orientation. The
asset never enters a reader snapshot, formal geometry, membership, or public
data contract. Topology orbit rings and legacy Slice Satellite positions share
a Tool-private spherical display projection using unit Earth and the IUGG mean
Earth radius. The Physical Frame map instead displays each formal WGS84
geodetic view while keeping the exact ECEF and geodetic values visible. These
rendering choices do not change formal altitude/Position facts; marker
offsets, marker sizes, and hit-test radii remain non-authoritative.

Planner display aliases, lifetime/state lanes, and spatial selection are
Tool-owned projections. Its spatial panel uses exact retained ECEF positions
from the formal Before/After Slice around a nominal transition; it does not
interpolate sub-slice geometry.

Normal stage-bounded Starlink Gen1 execution may run the reader/projection/render/write
pipeline in a bounded independent worker after the relevant immutable domain
boundary. The worker persists a deterministic detached snapshot under the
short-lived generated `.inspection-control/` directory before projection and
builds to a same-parent staging directory; checksum audit precedes atomic
publication. Tool work is side work and cannot delay a later domain stage;
when no worker slot is available, its submission is deferred until domain
completion. The final join gates artifact completeness only and deletes the
temporary control directory. The private gzip/pickle snapshot is runtime
bookkeeping, not durable evidence or a public serialization contract.

## Invariants

- Tools never mutate formal values, synchronize Planner, or invoke Models;
- the Observation Tool never executes a component and observation fields never
  enter formal semantic values;
- the BPF Compiler Tool never invokes the Compiler or accepts responsibility
  for producing compiled realizations;
- the Runner Tool receives only detached summary/frozen-binding scalars/events
  and never invokes `prepare()`, `run()`, `activate_next()`, or `cleanup()`;
- Runner lifecycle/control-plane inspection, fresh kernel/dataplane
  inspection, traffic telemetry, and active validation remain separate
  evidence domains;
- Traffic Replay does not collect packets or inspect live state; inferred
  source/first-link facts remain visibly distinct from tc-ingress observations;
- Traffic Replay excludes future-prepared links from ACTIVE context and never
  renders a logical Zone as a geographic packet hop;
- Traffic Replay keeps shared Position/network/Zone facts global, uses one
  simulation timeline, and confines visualization-expanded packet playback to
  a selected-Ping drill-down;
- only readers import or access formal Topology/PhysicalFrame/temporary-Slicer
  APIs;
- read snapshots contain detached Tool-owned primitives/values, not Topology,
  PhysicalFrame, Slicer, Slice, Difference, or entity instances;
- projection, display analysis, renderer, and writer cannot become a second
  authoritative computation path;
- renderer receives final view data only;
- writer performs filesystem output only;
- browser smoke remains outside the pipeline.
- detached Tool workers never record inside domain Observation rollups, and
  scheduling or worker failure cannot rewrite an already completed domain
  status;

## Failure semantics

Wrong formal input, missing retained data, foreign identity, or formal lookup
failure propagates conspicuously; readers do not invent empty/fallback data.
Projection/analysis/render validation failures stop artifact success. The
writer creates output only from values supplied for that invocation and does
not reuse stale success artifacts.

## Prohibited behavior

No Model execution, `Topology.frame_at()` or other position propagation,
authoritative connection/routing/membership/Difference computation, formal
object retained past the reader, formal API call outside a reader, browser
driver inside a Tool, generic registry/plugin framework, renderer-side read,
writer-side render, stale artifact fallback, production-to-Tools dependency,
live Runner control, packet injection, or fresh kernel reconstruction in the
Runner Tool.
Traffic Replay additionally prohibits live collection, packet injection,
Compiler or PositionModel execution, hidden packet identity guesses, and
treating visual animation duration as recorded network time.

## Extension points

A Tool may add private snapshot fields, display analysis, rendering, or
artifacts while preserving the responsibility boundary. It may merge
low-complexity adjacent implementation files when the snapshot/formal-read,
final-view/render, and file-only-write boundaries remain testable. A new
formal read belongs in the formal-reader responsibility and requires a
boundary test. Shared abstractions require a concrete repeated contract and
cannot introduce a registry or authority.

Runner Tool v1 is a post-run view. It obtains current/last-known scalar facts
and the bounded recent window from `inspection_snapshot()`, while standard
generated execution supplies the complete append-only `runner-events.jsonl`
directly to its reader. Large-history indexing/streaming presentation remains
future Tool work; the Runner does not materialize full durable history merely
for this Tool.

## Maintenance

Update the affected Tool package, its local README, Tool/user guide,
traceability map, boundary and focused Tool tests, browser smoke when UI
changes, and this contract. A formal domain change also updates its owning
Simulator contract first.
