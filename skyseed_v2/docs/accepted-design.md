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
- `Planner` is the persistent sibling of Slicer, incrementally consumes
  already-generated Differences, and publishes immutable backend-independent
  semantic `Plan` intervals without advancing Slicer; published intervals are
  continuous/non-overlapping while private planned state and visible lookahead
  continue across their boundaries;
- the top-level `backend` domain is downstream of Simulator; its BPF Compiler
  is a one-shot deterministic lowering from `Plan`, approved read-only
  Simulator facts, and an explicit previous intended baseline into an
  immutable symbolic realization;
- BPF compilation owns no Simulator semantics, deployment, runtime/kernel
  binding, hidden progression, or lifecycle service;
- Tools are repository-local, read-only, and non-authoritative.
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
→ Topology
→ Slicer
→ PositionModel → ConnectionModel → RouteModel
→ TopologySlice / TopologyDifference
→ Planner(Connection / Parameter / Forwarding planning + Coordination)
→ Plan
→ compile_bpf(Plan + approved read-only Simulator facts
  + explicit previous baseline)
→ BpfCompiledRealization
```

Static deployment is separate:

```text
Emulator → upstream SEED Docker compiler → Compose project
→ ComposeProjectLauncher
```

`ComposeProjectLauncher` is not a dynamic simulation Runner.

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
