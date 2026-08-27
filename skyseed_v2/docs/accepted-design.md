# Accepted design anchor

本文件只保存已接受的核心架构。当前实现状态唯一见
[`CURRENT.md`](CURRENT.md)；详细合同按任务读取
[`codex-reference/`](codex-reference/)。

## Core principles

- upstream SEED `Emulator` owns the static Emulator composition;
- Emulator owner tree is Skyseed AS → Shell → Orbit → Satellite, plus Skyseed
  AS → GroundStation;
- `Satellite(ExtensionNode)` remains Orbit-owned while the same instance is
  indexed by its AS for the standard SEED lifecycle; `GroundStation(Router)`
  remains on the standard rnode/brdnode path;
- `Simulator` is the Simulator composition root and creates exactly one
  `Topology`, then exactly one `Slicer`, then at most one formal `Planner`;
- `SimulatorStaticInput` is immutable receiver-owned boundary data;
- Models execute Position → Connection → Route;
- `Slicer` owns retained `TopologySlice` values and creates
  `TopologyDifference` only from retained formal slices;
- `Planner` is the persistent Simulator-owned peer of Topology and Slicer. Its
  `synchronize()` method requests newly available adjacent Differences from the
  exact sibling Slicer over already-generated retained Slices, consumes those
  immutable values, and publishes backend-independent semantic `Plan` intervals
  without advancing Slicer; published intervals are
  continuous/non-overlapping while private planned state and the unpublished
  portion of the planning window continue across their boundaries;
- the top-level `backend` domain is downstream of Simulator; its BPF Compiler
  is a one-shot deterministic lowering from `Plan`, approved read-only
  Simulator facts, and an explicit previous intended baseline into an
  immutable symbolic realization;
- BPF compilation owns no Simulator semantics, deployment, runtime/kernel
  binding, hidden progression, or lifecycle service;
- `BpfRunner` is the sole live BPF backend lifecycle owner. Callers hand it an
  already-started Compose project identity and future `BpfCompiledRealization`
  work. It independently discovers, validates, and freezes its required live
  runtime foundation, then owns concrete NodeId→container/PID/netns bindings,
  dynamic physical resources, READY/active frontiers, runtime timing/failure
  evidence, and scoped cleanup, but not Compose compilation/startup/teardown;
- generated BPF output records one small execution definition naming the same
  example progression builder used at generation time, its backend/runtime
  bounds, output identity, bounded/unbounded Timeline facts, compact
  source-scope fingerprints, and generated-input fingerprints. The private root
  generated-BPF-execution application composition uses SHA256 only as a deterministic
  source/output consistency guard: definition drift or stale generated output
  fails clearly with an instruction to rerun `main.py`. It then composes lazy
  Slicer → Planner → Compiler input with the unchanged BpfRunner lifecycle; it
  is not part of the BPF backend, a security mechanism, or a new public runtime
  authority;
- rolling owners retain only facts still needed beyond their successful
  handoff. Slicer and Planner release obsolete owner-local working entries,
  Runner and its private realization reclaim retired input/boundary/generation
  state, and complete semantic/runtime history is written sequentially under
  `output/inspection/` before release. Tools read that evidence but neither
  control retention nor become authoritative writers;
- Runner Preparation establishes all preparation-safe runtime state required
  for READY without prematurely changing current active network semantics. It
  performs necessary runtime capacity/readiness checks before formal execution;
  capacity or resource optimization must not alter upstream simulation
  semantics. Main
  alone changes current active semantics and advances the active frontier. Main
  does not create resources, rediscover bindings, compile/attach BPF,
  bulk-populate forwarding, or use a fallback path;
- an ACTIVE GroundStation-access Connection includes bidirectional direct-peer
  reachability between those two endpoints. The current private tc-BPF runtime
  realization provides the GroundStation-to-Satellite half as Main-owned
  node-local Linux route/neighbour state; it is not a new backbone target,
  public Compiler concept, or constellation-wide Satellite routing rule;
- simulation semantics take priority over control-plane optimization: resource
  lifetime, parameter, forwarding, Zone-edge, and explicit ordering decisions
  already made upstream remain observable at their exact nominal boundaries;
- Runner performance is part of simulation correctness: it must keep pace with
  the nominal semantic transition schedule required by the target simulation
  profile. Simulation sampling resolution, Planner publication cadence, Plan
  nominal transition timing, and Runner execution cadence are separate;
- Preparation/Main/Maintenance are internal Runner capabilities, not separate
  public authorities. Exact-next readiness is the minimum correctness
  requirement. In rolling mode Maintenance extends one gap-free READY prefix
  until either the explicit nominal-time preparation lookahead or the explicit
  positive future-boundary depth is reached; bounded full-range mode instead
  uses `None` for both bounds and requires finite input. Maintenance also
  advances input and reclaims retired state/resources. Main alone validates and
  activates the exact next READY boundary, publishes the active frontier, and
  schedules retirement. Manual stepping and relative monotonic-clock execution
  are mutually exclusive modes of the same Runner incarnation;
- shared Runner lifecycle is isolated from the private default tc-BPF
  dataplane mechanism; there is no public dataplane plugin contract. The
  default fixed-map implementation derives its private maximum resident
  generations from the public boundary depth and sizes generation-keyed maps
  from topology-stable per-generation bounds rather than startup occupancy;
- traffic telemetry is a non-authoritative Runner-private capability of the
  current default tc-BPF runtime realization, not a fourth Runner phase or
  public backend subsystem. Existing ingress programs may emit bounded packet
  evidence to one Runner-owned shared ring; output/loss/collector failure must
  not change forwarding action or grant scheduling authority. BpfRunner
  supervises collection and ordinary-user durable recording across Preparation
  and cleanup/final drain. Offline replay is a current consumer; any future
  live visualization remains a consumer, and neither is a collection or
  forwarding authority;
- Tools are repository-local, read-only, and non-authoritative. BpfRunner hands
  a detached summary, scalar projections of frozen runtime bindings, and a
  bounded recent event window to callers. Standard generated execution appends
  the complete Runner event history to durable JSONL, which the post-run Runner
  Inspection Tool reads directly; the Tool gains no lifecycle, kernel,
  dataplane, or traffic-observation authority.
- the S4 example has one formal Simulator/Slicer composition and one offline
  rolling application path. `main.py` advances Slicer far enough for the
  accepted planning lookahead, asks the Simulator-owned Planner to publish a
  continuous prefix, and optionally calls public `compile_bpf(...)` immediately
  with the explicit previous realization. Stage and Slice-limit controls are
  early-stop/execution bounds on this path, not alternate Simulator or domain
  algorithms. This S4 path stops before Runner;
- heavy generation-time S4 Inspection materialization runs in a small bounded
  set of independent processes after each required domain evidence boundary is
  immutable. Slicer → Planner → Compiler remains the domain dependency chain:
  Tool work never becomes a prerequisite for a later domain stage, and a task
  is deferred until domain completion when no worker is available. Each worker
  first persists a deterministic detached handoff under neutral
  `inspection/_execution/` metadata, then projects and renders in a staging
  directory, audits its inventory/checksums, and atomically publishes. The
  final CLI join is an artifact-completion gate, not a domain dependency.
  Domain and Inspection statuses remain separate, and Tool time is excluded
  from domain Observation rollups. Repository-produced gzip/pickle handoffs are
  trusted local rebuild evidence, not portable/untrusted interchange or a
  public serialization API;
- candidate and provenance material is non-authoritative and never a default
  implementation source.

## Formal chains

```text
Emulator configuration
→ create_static_emulator()
→ upstream Emulator + Skyseed entities
→ create_simulator_static_input()
→ SimulatorStaticInput
→ SimulationDefinition
→ Simulator
→ creates/owns peer components Topology + Slicer + optional Planner
Slicer → calls PositionModel → ConnectionModel → RouteModel
       → produces/retains TopologySlice
Planner.synchronize() → calls Slicer.create_difference()
                     → consumes on-request TopologyDifference values
                     → produces Plan
compile_bpf(Plan + approved read-only Simulator facts exposed through exact
  Slicer get_slice()/get_topology() access + explicit previous baseline)
→ BpfCompiledRealization
→ BpfRunner Preparation
→ READY
→ BpfRunner Main activation ∥ Maintenance future preparation/reclaim
→ live kernel forwarding state
```

`Slicer` retains the adjacent `TopologySlice` values needed for these calls;
it does not retain the immutable `TopologyDifference` results. The Planner is
the Difference consumer and `Planner.synchronize()` is the Plan producer. The
BPF Compiler consumes the Plan, not the Difference.

Static deployment is separate:

```text
Emulator → upstream SEED Docker compiler → Compose project
→ caller-selected deployment startup → live Compose project
→ caller hands the live project identity to BpfRunner
→ BpfRunner independently validates and freezes its runtime foundation
```

`ComposeProjectLauncher` remains an optional programmatic deployment utility;
it is not a Runner prerequisite or dynamic simulation Runner. `BpfRunner` does
not own `docker compose up` or `docker compose down`.

Static identity is projected by entities/construction through public Node
labels and geo APIs. A future Skyseed functional Layer may inject shared
software, files, commands, and daemons, but its name, count, and API are not
frozen. It will not create entities, register nodes, serialize metadata, or
dispatch compiler subtypes.

## Prohibited behavior

- no Simulator core dependency on Emulator or SEED;
- no Simulator dependency on backend; backend may read approved public
  Simulator facts/contracts without taking ownership or invoking Models;
- no SEED integration dependency on Simulator, Tools, Compose launcher, or
  examples;
- no compatibility forwarding or two import paths for retired modules;
- no second authoritative computation path in Tools;
- no Tool-side Model execution, Position propagation, Connection/Route
  recomputation, or Slice comparison;
- no implicit backend fallback, runtime fallback, or global
  cross-owner lookup;
- no unapproved Manager, Context, Builder, Catalog, Coordinator, or Result
  abstraction.

## Detailed accepted contracts

- [public imports](codex-reference/public-imports.md)
- [ownership and lifecycle](codex-reference/ownership-lifecycle.md)
- [Emulator–Simulator boundary](codex-reference/emulator-simulator-boundary.md)
- [Topology and Zone](codex-reference/topology-zone-contracts.md)
- [Dynamic slicing](codex-reference/dynamic-slicing-contracts.md)
- [Position and Connection values](codex-reference/position-connection-values.md)
- [Model contracts and built-in algorithms](codex-reference/model-contracts.md)
- [routing and path contracts](codex-reference/routing-path-contracts.md)
- [Timeline, Slice, retention, and Difference](codex-reference/timeline-slice-difference-contracts.md)
- [Simulator Planner](codex-reference/planning-contracts.md)
- [BPF Compiler](codex-reference/bpf-compiler.md)
- [invariants and failures](codex-reference/invariants-failures.md)
- [extension points](codex-reference/extension-points.md)
- [maintenance map](codex-reference/maintenance-map.md)
