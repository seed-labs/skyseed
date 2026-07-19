# Simulator Conceptual Design Baseline

## Status and purpose

This document restores the Simulator design space that had already been
discussed but was compressed or removed while the corresponding code was still
unimplemented. It is a conceptual baseline for later vertical slices, not an
implementation specification, public API freeze or instruction to create empty
types.

The status labels used here are:

- **implemented**: present in the current repository;
- **approved direction**: an accepted responsibility or dependency direction;
- **conceptual candidate**: a name or object useful for exploring later slices;
- **open question**: requires design and explicit approval in the relevant
  slice;
- **retired/corrected**: an earlier description that over-compressed or
  misrepresented the design.

Candidate names in this document, including `Simulator`, `Topology`,
`SemanticPlanner`, `BackendPlanner`, `ExecutionCompiler` and `Runner`, are not
final public names. The canonical conceptual class maps are in
[`../architecture/conceptual-class-diagrams.md`](../architecture/conceptual-class-diagrams.md#simulator-future-conceptual-map).

## Current implemented foundation

The following foundation is **implemented**:

- `NodeId`;
- `SatelliteSimulationInput`;
- `GroundStationSimulationInput`;
- `SimulatorStaticInput`;
- the entity-aware functions in `simulator_input_generation.py`;
- the static Emulator domain model;
- `SkyseedDockerCompiler` and its Docker/Compose static-deployment lowering;
- `ComposeProjectLauncher`, which consumes an already compiled Compose
  project.

No Simulator engine, dynamic Topology, Planner, dynamic execution compiler,
Runner or Observation subsystem is implemented by this baseline.

## Overall responsibility and dependency direction

The following relationship is an **approved direction**:

```text
Emulator static domain model
        │
        ├── simulator_input_generation
        │           │
        │           ▼
        │   SimulatorStaticInput
        │           │
        │           ▼
        │      future Simulator
        │
        └── SkyseedDockerCompiler
                    │
                    ▼
          Docker / Compose static deployment
                    │
                    ▼
          ComposeProjectLauncher
```

The candidate dynamic-simulation decomposition is:

```text
SimulatorStaticInput
        │
        ▼
Simulator
        │
        ├── Zone / Timeline
        ├── Topology
        ├── Planning
        ├── ExecutionCompiler
        ├── Runner
        └── Observation
```

Within that direction:

- `Simulator` is a candidate top-level coordination shell, not the owner of
  every algorithm.
- `Topology` is a candidate internal coordination shell, not a mega-class.
- Zone semantics are independent of Topology. Topology queries Zone semantics;
  it does not copy or own a Zone map.
- `SkyseedDockerCompiler` belongs only to the implemented static deployment
  chain.
- `ExecutionCompiler` is a candidate in the dynamic execution chain.
- The two compiler roles have no required common parent and occur at different
  lifecycle stages.
- `ComposeProjectLauncher` is not the dynamic Runner.

These responsibility and dependency directions are approved; the candidate
objects, their final names, visibility, methods and module placement remain
subject to later slice-level design.

## Topology decomposition

The conceptual Topology map preserves these **approved directions**:

1. Topology coordinates; it does not personally implement orbit propagation,
   visibility, connection selection or routing algorithms.
2. Position calculation, candidate discovery, connection selection and route
   generation are separate responsibilities.
3. A candidate connection is not a selected connection.
4. `ConnectionSlice` represents selected logical connections at one instant.
5. The authoritative tendency for `RouteSlice` is node-local forwarding
   state.
6. A complete end-to-end path tends to be a derived query rather than
   authoritative stored state.
7. `ConnectionSlice`, `RouteSlice` and `TopologySlice` tend to be immutable
   after generation.

The following are **conceptual candidates**, not frozen contracts:
`SimulationTimeline`, `ZoneModel`, `PositionModel`, `ConnectionModel`,
`ConnectionCandidateSet`, `ConnectionSlice`, `RouteModel`, `RouteSlice`,
`TopologySlice`, `SliceStore`, `TopologyDiffer` and `TopologyDifference`.

The following remain **open questions**:

- whether `SliceStore`, `PositionModel` and `ConnectionCandidateSet` should be
  public types at all;
- how an initial empty state or `slice0` should be represented;
- whether continuous operation, a sliding window or ahead-of-time calculation
  is needed;
- whether `PositionModel` exists independently and whether it is internal to
  Topology;
- whether `ConnectionModel` should be split further;
- the precise time, slice identity, retention and comparison contracts;
- final names, ownership, module placement and public exposure for every
  candidate.

## Two-level planning and dynamic execution

The candidate dynamic chain is:

```text
TopologyDifference
→ SemanticPlanner
→ SemanticTransitionPlan
→ BackendPlanner
→ BackendExecutionPlan
→ ExecutionCompiler
→ ExecutableBundle
→ Runner
```

This chain records an **approved direction**: cross-backend network semantics
and backend-specific execution strategy are different planning levels. It does
not require their two Plan forms to share a schema, parent type or lifecycle.

### SemanticPlanner

`SemanticPlanner` is a **conceptual candidate** responsible for turning a raw
`TopologyDifference` into backend-neutral network changes. It may express:

- logical connection enablement, disablement or parameter changes;
- changes to node-local forwarding semantics;
- G/Z, FEC, anchor or logical-next-hop changes.

It does not express BPF maps or generations, tc attachment, Linux routes,
namespaces, shell commands, or backend batching and barrier details.

### SemanticTransitionPlan

`SemanticTransitionPlan` is a **conceptual candidate** describing what the
network must change semantically. It is neither a directly executable plan nor
a low-level operation schema shared by every backend.

### BackendPlanner and BackendExecutionPlan

`BackendPlanner` is a **conceptual interface candidate** that turns a
`SemanticTransitionPlan` into one explicitly selected backend's
`BackendExecutionPlan`. It is responsible for:

- capability support;
- backend operations;
- batching and dependencies;
- parallel and serial constraints;
- barriers and synchronization;
- preload and activation;
- failure policy;
- cleanup or rollback policy.

A BackendPlanner must not recompute Topology, silently change the semantic
plan, or disguise success by falling back to another backend.

A candidate BPF execution plan may contain BPF map writes, generation preload,
generation-visibility proof, active-generation flip, tc-BPF attach/detach,
map-only/delta-attach/bootstrap classification, per-node batches, preload and
activation barriers, serial/parallel relations, failure and cleanup behavior,
and old-generation reclamation. None belongs in `SemanticTransitionPlan`.

A candidate Linux MPLS execution plan may instead contain interface and qdisc
operations, MPLS/LFIB routes, neighbor or next-hop updates, Netlink or
namespace operations, its own ordering and lock constraints, and its own
cleanup and failure semantics. A shared schema must not force a Linux plan to
pretend to be a BPF plan or vice versa.

`BpfBackendPlanner`, `LinuxMplsBackendPlanner` and `IpRoutingBackendPlanner`
are illustrative **conceptual candidates** only. Supported backends and the
BackendPlanner extension boundary remain **open questions**.

### ExecutionCompiler

`ExecutionCompiler` is a **conceptual candidate** that only lowers an already
approved `BackendExecutionPlan` into an `ExecutableBundle` that a Runner can
consume directly. It does not re-plan, re-select a backend, recompute Topology,
or change batching, barriers or serial/parallel semantics.

### Runner

`Runner` is a **conceptual candidate** that only executes an
`ExecutableBundle`. It does not decide what the network should change, select
a backend, repeat planning, silently fall back at runtime, or manufacture a
different plan because execution is difficult.

### Observation

`Observation` is a **conceptual candidate** that records compile evidence,
operation start and end, success and failure, time, state transitions, cleanup
results and audit facts. It does not make Planner or Runner control decisions.

## Design evolution

### First stage

The initial model was close to:

```text
TopologyDifference
→ backend-aware Planner
→ backend-specific actions
→ Compiler
→ Runner
```

That stage already recognized that IP routing, Linux MPLS and BPF require
different actions; BPF introduces maps, generations, preload, attachment,
synchronization and cleanup; and each backend may impose different batching,
parallel and serial rules.

### Second stage

To avoid binding all network semantics directly to one backend, the model was
refined to:

```text
TopologyDifference
→ SemanticPlanner
→ SemanticTransitionPlan
→ BackendPlanner
→ BackendExecutionPlan
→ ExecutionCompiler
→ ExecutableBundle
→ Runner
```

The first plan level preserves stable cross-backend network semantics. The
second owns fully backend-specific execution strategy. They intentionally need
not have the same structure.

### Retired/corrected compression

The earlier wording:

```text
abstract Simulation Plan
→ SimulationPlanCompiler
→ concrete dynamic operations
```

is **retired/corrected** as an over-compressed description, not the one
approved master contract. It conflated semantic network planning,
backend-specific implementation planning and executable-form compilation.
`SimulationPlanCompiler` therefore remains only a historical shorthand in
documents that explain the correction; it is not a frozen future public name.

## Slice governance

The conceptual diagrams preserve design space; they are not implementation
task lists. A later vertical slice may refine, rename, split, merge or remove
candidates when its real contract is approved. It must not create empty classes
to make the diagram look implemented, and it freezes only the public concepts
needed for that slice.

The current vertical-slice workflow and approval gates are normative in
[`../../AGENTS.md`](../../AGENTS.md#1-标准纵向开发流程). In particular, a
passing test suite is not a substitute for real execution evidence; v1 may be
investigated only in a targeted way; and Planner, Compiler and Runner must not
form hidden alternative success paths.

## Remaining architecture questions

In addition to the Topology questions above, later slices must decide:

- the smallest public Simulator operation and its lifecycle;
- Zone and Timeline representations and ownership;
- exact semantic-difference and semantic-plan contracts;
- backend selection, capability and extension contracts;
- `ExecutableBundle` contents and ownership;
- Runner initialization and its relationship, if any, to static
  materialization;
- resource resolution below logical `NodeId` owner lookup;
- Observation persistence, querying and audit boundaries;
- which candidates remain private implementation details;
- all final public names and exports.

No answer to these questions is implied by this recovery slice.
