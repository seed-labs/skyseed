# Inspection Tools

Tools are repository-local read-only views, not installed product APIs.

| Tool | Input | Output subdirectory |
|---|---|---|
| Topology | one created `Topology` | `inspection/topology/` |
| Zone | the same `Topology` | `inspection/zone/` |
| Physical Frame | that exact `Topology` plus one caller-supplied, already-created `PhysicalFrame` | `inspection/physical-frame/` |
| Slice / Difference (temporary legacy) | one exact legacy `Slicer` with retained mixed-state slices | `inspection/slice/` |
| Planner | that exact `Slicer`, consecutive `Plan` publications, retained `PlanningDecision` values | `inspection/planner/` |
| System Observation | detached records emitted during the actual Slicer/Planner execution | `inspection/observation/` |
| BPF Compiler | consecutive `Plan` publications and their already-produced `BpfCompiledRealization` values | `inspection/bpf-compiler/` |
| BPF Runner | post-cleanup detached Runner summary/frozen-binding scalars/events plus runtime invocation outcome | `inspection/runner/` |
| Traffic / Path Replay | durable tc-BPF traffic records, retained Slice Position/on-request Difference evidence, existing BPF Compiler inspection evidence, and optional planned-workload context | `inspection/traffic/` |

The normal imports are:

```python
from tools.topology import write_topology_tool
from tools.zone import write_zone_tool
from tools.physical_frame import write_physical_frame_tool
from tools.slice import write_slice_difference_tool
from tools.planner import write_planner_tool
from tools.observation import write_observation_tool
from tools.bpf_compiler import write_bpf_compiler_tool
from tools.runner import write_runner_tool
from tools.traffic import write_traffic_replay_tool
```

The output contains standalone HTML, private audit data, and summaries. The
Physical Frame Tool receives one `Topology + PhysicalFrame` pair chosen by the
caller and writes the one timestamp, every exact ECEF Position, its formal
WGS84 geodetic view, and static identity/type/orbit/access-capability context.
It has no physical history and no Connection, Route, Zone-serving, effective
resource allocation, Difference, Plan, or runtime fields. It also never calls
`Topology.frame_at()` or a `PositionModel`; creating and selecting the frame is
the caller's responsibility. For example:

```python
frame = topology.frame_at(timestamp)
write_physical_frame_tool(topology, frame, inspection_root / "physical-frame")
```

The old Slice Tool is a temporary legacy mixed-state consumer scheduled for
removal with Slicer and the old Slice/Difference path; it is not the accepted
future planned-operational inspection boundary. While that seam remains, it
uses `slice-manifest.json` plus offline `chunks/slice-*.js`;
opening its page loads only the selected Slice. Its 3D Earth supports direct
node, Connection, and Zone selection, contextual Zone service and Difference
overlays, and a Zone/Gateway/direction Route workflow restored from that
chunk's sparse next-hop tuples. Generic source/target selection is an advanced
fallback. Topology orbit rings and legacy Slice Satellite positions use one
Tool-private spherical display approximation: unit Earth plus formal altitude
divided by the IUGG mean Earth radius. It does not reinterpret Simulator
geometry; marker and hit-test sizes remain independent display values.
The Physical Frame map uses the formal geodetic view and keeps the exact ECEF
and geodetic values in node detail; its map placement and marker sizes are
non-authoritative. The Zone Tool reads static layout geometry and uses
browser-only hit testing for direct selection; it does not execute the Zone
membership Model. Where temporary legacy Tools report formal path,
Difference, or model results, those results are read from production objects
rather than recreated.

The Planner Tool receives the exact consumed Differences explicitly. Its
`planner-manifest.json` and its compact `planner-manifest.js` bootstrap point
to offline Slice, Plan, Difference, and hierarchical entity-catalog/data
chunks. The HTML embeds no manifest or complete run, creates no eager raw
pretty JSON, and restores only the selected Route path from one loaded Slice.
Slice, Planner, and Compiler exact chunks are deterministic compressed script
envelopes. They remain directly usable from `file://`; the browser decompresses
only the selected chunk, while byte-count/SHA-256 metadata and bundle
inventories preserve exact auditability.

In the Starlink Gen1 example, heavy Slice, Planner, BPF Compiler, and Observation bundles
are materialized by at most two independent family-level worker processes by
default. Tool work may overlap later domain execution when worker capacity is
available; otherwise submission is deferred until the domain chain is complete
so a Tool cannot become a Slicer → Planner → Compiler dependency. The CLI joins
only for final artifact completeness. Short-lived control files and frozen
snapshots live under the generated root's `.inspection-control/` directory and
are deleted after join or termination; they are implementation state, not
durable evidence. Each worker builds in a staging directory, verifies the Tool
checksum inventory, and atomically replaces the canonical
`inspection/<tool>/` bundle only on success. Tool CPU/wall time is not included
in Slicer, Planner, or Compiler Observation records.

The Observation Tool reads `execution-observations.jsonl`, not live domain
objects. Its page separates bootstrap from steady p50/p95/max and offers both
stage progression and Per-Slice / Plan breakdown. Progression uses actual
Slice timestamps and Planner publication coverage rather than record order,
with a shared Slicer/Planner duration scale, automatically selected numeric
ticks, a Timeline-derived Slice-interval reference, and an optional fit view.
The visual unit scrubber and bounded stage×unit heatmap navigate the same exact
records; missing records, measured zero, exclusive leaves, and separate rollup
bands remain distinct. A selected unit exposes coverage, phase, work counts,
the primary rollup, exclusive leaf subtotal, and any exact attributable gap.
Resource samples and Slicer Difference work remain visibly separate and are
never added to the leaf subtotal. Clicked chart points retain a detailed
annotation until replaced or cleared. Named work counts, leaf-stage
time-per-unit, component/stage/phase filters, and resource evidence remain
available. These facts are diagnostic and non-authoritative. Slicer, Planner,
and Compiler emit them today; Runner can later reuse the same unit convention
without adding fields to formal values.

Run the rich bundle:

```bash
python examples/mini_satellite_internet/main.py
```

Run only the static Starlink Gen1 review boundary without entering the dynamic or
BPF pipeline:

```bash
python examples/starlink_gen1_satellite_internet/main.py --through topology
```

Its physical-boundary outputs are
`examples/starlink_gen1_satellite_internet/output/inspection/topology/index.html` and
`examples/starlink_gen1_satellite_internet/output/inspection/zone/index.html`,
plus
`examples/starlink_gen1_satellite_internet/output/inspection/physical-frame/index.html`.
Select an orbit ring or GroundStation on the Topology globe, select a Zone
directly on either Zone view, or select a node on the Physical Frame map to
inspect its exact one-timestamp coordinates. The subtle Natural Earth
coastline is embedded for offline orientation only; it is not simulation,
physical-projection, or Zone authority.

Run the same Starlink Gen1 composition through Slicer with:

```bash
python examples/starlink_gen1_satellite_internet/main.py --through slicer
```

That adds the temporary legacy `output/inspection/slice/index.html` and
Observation view alongside the Topology, Zone, and Physical Frame Tools.
`--through planner` adds Planner, and `--through compiler` adds Compiler.
`--slice-limit` bounds execution only; lazy per-Slice scripts prevent the
browser page from becoming a monolithic second simulation authority.

Validate the generated Slice / Difference page in Firefox without keeping a
screenshot:

```bash
python scripts/check_slice_tool_browser.py \
  examples/mini_satellite_internet/output/inspection/slice/index.html
```

Browser smoke validates the UI only and never enters the Tool's formal object
read chain.

Run the real complex Planner inspection from the repository root with one
entry point:

```bash
python -m examples.mini_satellite_internet.main
```

Open `examples/mini_satellite_internet/output/inspection/planner/index.html`.
The page starts with compact whole-run and selected-Plan transformation ratios
and distributions, then exact planning/publication coverage, temporal groups,
and a shared sampled-vs-planned globe. Connection episodes end at the current
artifact boundary when right-censored. Forwarding inspection overlays exact
formal path Q and published planned path P, with clickable shared/formal-only/
planned-only Connection edges. Long identity lists remain secondary exact
drill-down. The Tool does not infer rejected suppression candidates, midpoint
positions, a third Route, or an expected Plan.

Open
`examples/mini_satellite_internet/output/inspection/bpf-compiler/index.html`
to compare each continuous Plan interval with its symbolic resource, qdisc,
forwarding, Zone-edge, ordering, and intended-end-state realization. The
caller compiles first and supplies the results; this Tool never invokes
`compile_bpf`, scans raw Slice evolution, or infers runtime/kernel bindings.

Validate that page in Firefox with:

```bash
python scripts/check_bpf_compiler_tool_browser.py \
  examples/mini_satellite_internet/output/inspection/bpf-compiler/index.html
```

The canonical Mini `main.py` remains generation-only and does not create a
placeholder Runner artifact. After `start.sh`, `run.sh` uses public automatic
Runner execution and writes `inspection/runner/index.html` after teardown for a
completed, controlled-interruption, ordinary-failure, or teardown-failure
outcome when finalization can run. The page shows final/last-known lifecycle
facts, curated history, timing, amplification/batching/workers,
Maintenance/reclaim/backlog, sparse resource health, failure/teardown, and
recorded current-default-tc-BPF evidence. The complete exact event history
remains the adjacent JSONL source rather than being duplicated into the page.
It is static after opening and does
not inspect the kernel, collect traffic telemetry, inject packets, or control
Runner.

The same post-run finalization writes `inspection/traffic/index.html` when its
JSONL simulation clock, exact-generation interface identities,
Slice / Difference timeline, and BPF Compiler nominal boundaries form one consistent
evidence set. The replay pairs only unambiguous ICMP echo request/reply records
into Ping transactions, groups endpoint pairs into observed flows without
requiring a workload, distinguishes inferred facts from observed ingress
hops, and shows logical Zone identity separately from the generation-specific
service satellite used by the physical path. One draggable simulation timeline
scrubs the shared ACTIVE network, Zone state, and moving nodes. Selecting a Ping
pauses at its time and uses its recorded generation; Replay Ping expands its
request then reply without adding a second permanent player. The globe shows
exact current ACTIVE links as muted context and reconstructs recent observed
traffic from a simulation-time window. Exact retained Position samples remain formal evidence; linear
ECEF interpolation between them is visualization-only. The standalone page
never connects to Runner, collector, kernel, Compiler, InternetMap, or a
server. Optional workload schedule IDs and qualified no-observation slots are
shown only in a collapsed comparison.

Implementation state is recorded only in [`CURRENT.md`](../CURRENT.md).
