# Current implementation status

This is the only source for current implementation state. Architecture
principles live in [`accepted-design.md`](accepted-design.md); candidate and
provenance material is non-authoritative.

## Consolidation status

- production package consolidation: **implemented and accepted**;
- repository-local Inspection Tools consolidation: **implemented and
  accepted**;
- documentation identity/checking consolidation: **implemented and accepted**;
- Runner milestone: **implemented, runtime-validated, and manually accepted**;
- S4 Planner and BPF Compiler scale milestone: **implemented, qualified, and
  accepted**;
- compatibility forwarding and dual import paths: absent.

## Accepted current implementation

### Emulator, Simulator, Slicer, and Planner

- Static Emulator configuration, YAML loading, owner construction, SEED Base /
  AS extensions, Docker compiler hook, metadata, and assets are implemented.
- `SimulatorStaticInput` and `create_simulator_static_input(...)` form the
  explicit Emulator → Simulator boundary. `SimulationDefinition`,
  `SimulationTimeline`, `Simulator`, `Topology`, `ZoneLayout`, geometry, and
  the Position → Connection → Route model chain remain Simulator-owned.
- Slice / Difference semantics: **implemented and accepted**. `Slicer`
  publishes and retains immutable `TopologySlice` values, creates immutable
  `TopologyDifference` values on request from retained adjacent Slices without
  retaining those Difference objects, and advances its private Slice release
  frontier after successful downstream handoff.
- `Simulator` creates and owns peer-level Topology, Slicer, and optional Planner
  components. The backend-independent semantic `Planner` may exist before the
  first real Slice; `synchronize()` requests available Differences from its
  exact sibling Slicer and selects planning/publication windows. Its connection,
  parameter, forwarding, and coordination models publish immutable `Plan`
  transitions; private continuous state and release frontiers remain
  Planner-owned.

### Generated rolling BPF execution

- `compile_bpf(...)` is a one-shot, stateless lowering from one `Plan`, approved
  read-only Simulator facts exposed by the exact Slicer's `get_slice()` /
  `get_topology()` access, and an explicit previous intended baseline to one
  immutable `BpfCompiledRealization`. There is no separate Slicer-facts DTO.
- Normal generated execution is
  `output/run.sh` → `skyseed_v2._generated_bpf_execution` → rolling Slicer →
  Planner → BPF Compiler → `BpfRunner`. This private application-composition
  root is outside `backend/bpf`; the BPF backend does not own Simulator or
  Planner progression.
- Generated `inputs/execution.json` identifies the progression builder,
  Timeline, project, Runner bounds, and the source/input fingerprints relevant
  to normal network execution. SHA256 is only a deterministic stale-output and
  definition-drift detector. A mismatch fails closed with an instruction to
  rerun `main.py`; it is not a security mechanism.
- Successful rolling handoff releases bounded Slicer, Planner, Compiler, and
  Runner working state. Complete owner-produced history is retained in
  append-only JSONL for read-only post-run inspection.

### BPF Runner and runtime evidence

- `BpfRunner` is the sole live BPF lifecycle owner. Preparation binds the exact
  generated deployment and establishes the runtime foundation; Main alone
  changes ACTIVE network semantics; Maintenance prepares the gap-free future
  READY prefix and reclaims retired generation and physical resources.
- Exact-next READY remains mandatory. Rolling preparation stops when its
  nominal-time lookahead or future-boundary depth is first satisfied. The
  private default tc-BPF realization uses `K = boundary depth + 1` resident
  generations and topology-stable per-generation map bounds.
- Main uses monotonic 1:1 pacing for nominal transitions. Maintenance performs
  rolling preparation, capacity-pressure generation reclaim, and physical
  reclaim without acquiring Main's active-semantic authority.
- The default tc-BPF ingress path records bounded packet/decision telemetry to
  one Runner-owned ring buffer. Telemetry failure never changes forwarding.
  The supervised collector persists detached records, identity facts, loss
  counters, clock anchor, drain, and completeness evidence in
  `traffic-telemetry.jsonl`.
- ACTIVE GroundStation-access connections provide direct-peer GroundStation ↔
  Satellite reachability without creating constellation-wide Satellite route
  targets. Zone forwarding retains stable logical Zone identity across service
  Satellite changes.

## Accepted canonical Mini evidence

- The canonical profile covers `2026-01-01T00:00:30Z` through
  `2026-01-01T00:11:00Z` at 10-second Simulator observation resolution.
- Runner preparation uses a separate 60-second preparation lookahead and an
  eight-boundary future depth (`K = 9` for the private default realization).
- The formal chain produced **64 Slices / 64 Differences**, **16 Plans / 16
  `BpfCompiledRealization` values**, and **75 nominal boundaries / 75 Main
  activations**.
- Final canonical Mini runtime: **PASS**. Preparation, repeated Main
  activation, rolling Maintenance preparation, generation reclaim, physical
  reclaim, input exhaustion, normal automatic completion, and cleanup were
  observed without a deadline miss in that run. This is run evidence, not a
  general performance guarantee.
- Traffic telemetry and the Traffic / Path Replay artifact preserve measured
  packet/path evidence separately from the example-owned planned S1–S7
  acceptance workload. Normal generated `run.sh` neither reads nor starts that
  workload.

## Inspection Tools and maintained examples

- Manual Tool acceptance is complete for eight formal repository-local,
  read-only Inspection Tools: Topology, Zone, Slice / Difference, Planner, BPF
  Compiler, BPF Runner, Traffic / Path Replay, and System Observation.
- Tools read owner-produced facts and may project/render them; they do not
  control runtime, own fact retention, query the live kernel as an authority,
  or generate traffic. The lazy Slice Tool keeps one browser-resident Slice,
  restores only a selected sparse next-hop path, and now presents compact
  projected current-Slice/model context plus clearly labeled Tool-derived
  service geometry. The large S4 example materializes heavy Slice, Planner,
  Compiler, and Observation Tools in bounded independent processes from
  immutable persisted handoffs under `inspection/_execution/`. The domain chain
  and detached Inspection work have separate statuses; checksum-audited staging
  is published atomically, and Tool work is excluded from domain Observation
  timings.
- There are three maintained runnable examples:
  `complete_static_emulator` is the minimal two-Satellite teaching/contract
  smoke example, `mini_satellite_internet` is the canonical dynamic rolling
  BPF example, and `s4_satellite_internet` is the large static-by-default S4
  input over 348 Satellites, 355 frozen evidence-backed Starlink gateway sites,
  703 total dynamic nodes, and a 732-Zone global candidate. Its ground body
  includes supported current, planned, and historical Ka/E sites; it is not a
  simultaneous operational-network snapshot. The example has one 30-minute,
  10-second Simulator/Slicer composition. `main.py --through
  topology|slicer|planner|compiler` selects an early-stop boundary on one
  rolling progression, while `--slice-limit` only bounds execution. Compiler
  mode calls public `compile_bpf(...)` for each Plan as it is published and
  never enters Runner or runtime.
  Its final S4-specific Zone-service minimum is 25 degrees at the Zone
  representative point; generic model configuration behavior is unchanged.
  This isolated shell is not expected to serve all global Zones continuously,
  and served percentage is not a coverage-quality target. The accepted short
  baseline retains Slice 0 plus 20 real Slices/20 Differences; warm complete
  Slice p50/p95 was 0.8263/0.8952 seconds and Difference p50/p95 was
  0.0401/0.0674 seconds on the scale-evidence host. The accepted long-horizon
  run retained Slice 0 plus **181 real Slices / 181 Differences**, published
  **90 Plans**, and compiled **90 `BpfCompiledRealization` values**. Fixed
  Planner and Compiler validation both passed. Domain execution took 459.85
  seconds (Planner 249.46 seconds; Compiler 25.12 seconds), while detached Tool
  materialization overlapped the chain and joined afterward. The historical
  stage-batched harness that produced those accepted measurements has been
  removed from normal execution; its semantic output remains the oracle for the
  one rolling application path. These figures are evidence from that host and
  are not general performance guarantees.
  `examples/reference` is explicitly non-runnable historical provenance, and
  repository-level `research/` remains persistent non-runtime evidence.

## Deferred

- Planner numeric stability tuning remains scenario- and scale-dependent
  experimental research; the accepted S4 composition is a scenario candidate,
  not a new generic numeric default.
- A public dataplane plugin API, an automatic rollback after a partially failed
  activation, packet-drain consistency, large-network performance
  qualification for Runner/runtime, fresh kernel/dataplane inspection, and
  realtime/live traffic visualization remain future work.
- The synchronous future-realization callable returns an ordered value or
  terminal `None`; it has no separate temporarily-unavailable result.

## Current milestone

The **S4 Planner + BPF Compiler scale stage** is accepted. Its maintained normal
entry is now one obvious `emu.py` → `simu.py` → `main.py` call chain with one
Simulator composition and rolling Slicer → Planner → Compiler progression.
The accepted scale target remains 181 Differences, 90 continuous Plan
publications, and 90 immutable compiled realizations. Normal output uses
`inspection/` plus small neutral detached-handoff/status metadata; there is no
example-local qualification application owner. Frozen GroundStations and
explicit Zone YAML remain independent inputs, with separate offline refresh and
Zone-authoring scripts. The next scale stage is S4 Runner/runtime; this S4
example path does not enter Runner.

## Current production paths

```text
src/skyseed_v2/
├── _generated_bpf_execution.py
├── emulator_to_simulator.py
├── backend/bpf/{compiler,compiled_realization,runner,_runner}/
├── shared/
├── configuration/{emulator,simulator}/
├── emulator/{entities,seedemu}/
├── simulator/{input,topology,geometry,slicing,models,planning}/
└── compose/launcher.py
```

The accepted SEED extnode baseline is commit
`23a565a63d4ff50532f4313ca9ecc6afbfc22e16`. Skyseed uses its upstream
`ExtensionNode`, Base, Docker, metadata, Router, and Compose lifecycle rather
than carrying an InternetMap compatibility shim or custom image path.
