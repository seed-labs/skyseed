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
- S4 Runner scale-safety and controlled optimization: **implemented,
  full-width validated through R20, and accepted**;
- Round-1 Traffic and canonical S4 runtime path: **implemented and validated by
  full-width execution plus the bounded canonical smoke**;
- S3 persisted/precomputed Runner and Traffic qualification: **completed** at
  the retained 10-second runtime profile after the host-scale configuration
  changes documented in
  [`human-readable/system-overview.md`](human-readable/system-overview.md);
- S3 5-second compute checkpoint: **completed through Planner and Compiler**.
  Its deployed Runner has not yet been qualified, and the attempted live
  rolling S3 run failed because the exact 30-second boundary was not READY by
  its deadline;
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
- Forwarding precedences remain exact backend-independent semantic facts. Their
  implementation indexes compatible transitions by dependency key and time,
  avoiding unrelated global candidate pairs without changing the result.

### Generated BPF execution

- `compile_bpf(...)` is a one-shot, stateless lowering from one `Plan`, approved
  read-only Simulator facts exposed by the exact Slicer's `get_slice()` /
  `get_topology()` access, and an explicit previous intended baseline to one
  immutable `BpfCompiledRealization`. There is no separate Slicer-facts DTO.
- Runtime generation identity is derived from complete backend-local BPF
  generation state. Equivalent BPF state may reuse a resident generation while
  Main still applies exact non-BPF runtime changes; this classification remains
  outside Planner and the one-shot Compiler.
- Normal generated Mini execution is
  `output/run.sh` → `skyseed_v2.experiment.execution` → rolling Slicer →
  Planner → BPF Compiler → `BpfRunner`. This experiment-side runtime
  composition is outside `backend/bpf`; the BPF backend does not own Simulator
  or Planner progression.
- Generated `runtime/execution.json` identifies the progression builder,
  Timeline, project, Runner bounds, and the source/input fingerprints relevant
  to normal network execution. SHA256 is only a deterministic stale-output and
  definition-drift detector. A mismatch fails closed with an instruction to
  rerun `main.py`; it is not a security mechanism.
- The same experiment-side runtime composition can instead validate and stream
  a trusted local gzip/pickle handoff of the exact immutable Compiler outputs.
  This is a consumption strategy over the same canonical progression, not a
  second Slicer/Planner/Compiler implementation. The manifest and every
  realization carry deterministic interval/checksum evidence and corruption
  fails before Runner preparation.
- Successful rolling handoff releases bounded Slicer, Planner, Compiler, and
  Runner working state. Upstream owner history remains append-only JSONL;
  Runner history uses bounded nonblocking persistence and explicitly marks
  incomplete evidence if its writer cannot keep up.

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
- Generation reuse does not suppress qdisc, route/neighbour, Zone-address, or
  physical-resource work, and generation changes are not mechanically forced
  by fields that do not affect the BPF map/program state.
- Rolling ingestion normalizes only each newly supplied realization and
  retains the already-normalized future boundary suffix. Runtime realization
  freezes interface ifindex with its owned interface/container incarnation,
  groups each boundary once by node, batches node-local `ip`/`tc` work, and
  schedules independent node batches through a bounded hardware-adaptive
  worker pool. Full deployment-incarnation validation remains fail closed.
- Runner event persistence uses a bounded nonblocking queue and detached
  writer. Critical paths retain compact counters/cardinalities and bounded
  recent events; overflow or writer failure is explicit and makes detached
  qualification evidence incomplete without changing network state.
- Maintenance exposes produced/submitted/completed work, READY/required
  frontiers, pending age/depth, resident generations, reclaim backlogs, and
  retire/reclaim counts. Sparse checkpoints record Runner RSS/thread and owned
  runtime counts after Preparation, selected early activations, failure, and
  teardown.
- `cleanup()` now implements bounded Runner teardown: Maintenance join is
  limited to 30 seconds, partial privileged teardown retains controller
  ownership for retry, and successful residue evidence is required before the
  private inventory is cleared.
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
  service geometry. The Starlink Gen1 example materializes heavy Slice,
  Planner, Compiler, and Observation Tools from durable
  `output/domain-history/` evidence in bounded independent processes.
  Checksum-audited staging is published atomically, the runtime
  handoff is authoritative before Tool completion, and Tool work is excluded
  from domain Observation timings.
- There are three maintained runnable examples:
  `complete_static_emulator` is the minimal two-Satellite teaching/contract
  smoke example, `mini_satellite_internet` is the canonical dynamic rolling
  BPF example, and `starlink_gen1_satellite_internet` is the configurable
  single-shell Starlink Gen1 example. `shells.yaml` contains the five formal
  S1–S5 choices, while compact `scenario.yaml` selects exactly one; the current
  canonical selection is S3 with 720 Satellites, 355 frozen evidence-backed
  Starlink gateway sites, approximately 1,075 runtime nodes, and a 732-Zone
  global candidate. Its one formal Timeline is 10 minutes at a 5-second
  interval. Normal `main.py` runs through Compiler;
  `main.py --through topology|slicer|planner|compiler` selects an early-stop
  boundary on the same rolling progression, while `--slice-limit` only bounds
  execution. Compiler mode persists the exact realizations and produces
  `start.sh` / `run.sh` / `launch.sh` / `cleanup.sh` plus static Compose output.
  `main.py` itself does not start the deployment or enter Runner. Generated
  `start.sh` starts Compose only, `run.sh` consumes that already-started
  deployment, `launch.sh` only composes those two, and `cleanup.sh` tears the
  deployment down. The generated build plan groups byte-identical contexts;
  canonical startup validates and reuses an exact matching image set, uses
  `--no-build`, and fails closed rather than rebuilding a live matching project.
  The 25-degree Zone-service minimum and other numeric policy remain scenario
  choices, not generic defaults.
  `examples/reference` is explicitly non-runnable historical provenance, and
  repository-level `research/` remains persistent non-runtime evidence.

## S3 qualification checkpoints

- The full S3 persisted/precomputed 10-second Runner and Traffic qualification
  completed successfully at 720 Satellites, 355 GroundStations, and
  approximately 1,075 runtime nodes. That run used the retained host-scale
  configuration: D-Bus `max_connections_per_user = 512`,
  `fs.inotify.max_user_instances = 1024`, cgroup v2, and Docker's `cgroupfs`
  driver. It was not an unmodified-host run.
- The S3 5-second precomputed generation completed 121 Slices, 121 Differences,
  30 Plans/realizations, and 121 activation timestamps. The aligned producer
  represented 600 seconds in 418.107022 seconds, or 1.435039 simulated seconds
  per wall second. The 5-second deployed Runner runtime has not been qualified.
- The attempted live rolling S3 qualification failed when the exact 30-second
  boundary was not READY by its deadline. The failed run is preserved evidence,
  not an accepted rolling success.
- Historical Round-2 evidence remains distinct from this checkpoint:
  **Round 2 is implemented and qualified** on S4. Its formal profile covered
  10 minutes at a 10-second interval, with 61 Slices/Differences and 61 READY
  publications.
  Traffic completed 17/17.
  Its earlier S3 deployment stopped at 1,003 Up / 73 Created; none of those
  figures is a current S3 5-second runtime qualification claim.

## Deferred

- Planner numeric stability tuning remains scenario- and scale-dependent
  experimental research; the accepted Starlink Gen1 composition is a scenario
  candidate, not a new generic numeric default.
- The current Simulator/Planner implementation is a stable pre-refactor
  checkpoint; further architecture work remains pending.
- A public dataplane plugin API, an automatic rollback after a partially failed
  activation, packet-drain consistency, fresh kernel/dataplane inspection, and
  realtime/live traffic visualization remain future work.
- The synchronous future-realization callable returns an ordered value or
  terminal `None`; it has no separate temporarily-unavailable result.

## Current milestone

The current repository is the stable pre-refactor S3 checkpoint. Its
persisted/precomputed 10-second Runner and Traffic path is qualified with the
retained host-scale configuration, and its 5-second Planner+Compiler producer
is qualified above real-time aggregate throughput. The 5-second deployed
Runner remains unqualified, and the preserved live rolling failure is not a
success claim. Slicer remains the exact fact producer, Planner preserves
dependency semantics with bounded indexes, Compiler remains one-shot, and
further Simulator/Planner architecture work is pending.

## Current production paths

```text
src/skyseed_v2/
├── experiment/{execution,_realization_handoff,traffic/}
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
