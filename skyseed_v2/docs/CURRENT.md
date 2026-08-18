# Current implementation status

This is the only source for current implementation state. Architecture
principles live in [`accepted-design.md`](accepted-design.md); candidate and
provenance material is non-authoritative.

## Consolidation status

- audit baseline for this completed round: clean commit `4d1d173`;
- production package consolidation: **implemented and accepted**;
- repository-local Inspection Tools consolidation: **implemented and
  accepted**;
- documentation identity/checking consolidation: **implemented and accepted**;
- compatibility forwarding and dual import paths: absent.

## Accepted implementation

- static Emulator configuration, YAML loading, owner construction, SEED Base /
  AS extensions, Docker compiler hook, metadata, and assets;
- Emulator owner tree and explicit `create_static_emulator()` entry;
- immutable `SimulatorStaticInput` and
  `create_simulator_static_input(...)`;
- `SimulationDefinition`, `SimulationTimeline`, and `Simulator`;
- Simulator-owned `Topology`, optional `ZoneLayout`, `Zone`, geometry, and
  membership/resolution contracts;
- Simulator-owned `Slicer`, Position / Connection / Route contracts and
  built-in Models;
- `TopologySlice`, retained-slice lookup, path resolution, and
  `TopologyDifference`;
- Dynamic Slice A–G: **implemented and accepted**;
- Simulator-owned semantic Planner first vertical slice: **implemented and
  accepted**; it selects visible future and a publication prefix before
  cross-Difference Models, incrementally preserves private planned state across
  continuous non-overlapping Plans, performs replaceable cross-domain
  Coordination with lookahead, and publishes immutable backend-independent
  typed transitions plus minimal same-time Forwarding precedences;
- top-level BPF Compiler minimal vertical slice: **implemented and accepted**;
  `compile_bpf` deterministically lowers one Plan plus approved read-only
  Simulator facts and an explicit previous intended baseline into immutable
  symbolic resource/qdisc/forwarding/Zone intent, ordering constraints, and
  interval-end intended state;
- read-only Topology, Zone, Dynamic Slice, and Planner Inspection Tools; the
  Planner Tool has one real complex-scenario entry and directly displays
  formal Slicer, Difference, Plan, and retained PlanningDecision results;
- `mini_satellite_internet` is the single richer small-scale reference
  scenario and its `main.py` emits those four views plus the BPF Compiler
  Inspection Tool candidate from one formal assembly;
- static Compose project launch/verification through
  `ComposeProjectLauncher`.

## Deferred

runtime binding, BPF preparation/load/activation/maintenance, and Runner;
dynamic execution remains deferred. The offline read-only BPF Compiler
Inspection Tool is implemented in the current worktree and remains under
page-level human acceptance review as part of the uncommitted Compiler + Tool
milestone.
Their future names, owners, packages, inputs, outputs, and
execution chains are not frozen and require separate approval.

No package, empty directory, public type, fallback, or execution path for those
deferred capability areas exists in the current implementation. The BPF
Compiler emits no command, container mutation, kernel object, live attachment,
or physical generation.

## Current production paths

```text
src/skyseed_v2/
├── backend/bpf/
├── shared/
├── configuration/{emulator,simulator}/
├── emulator/{entities,seedemu}/
├── emulator/construction.py
├── emulator_to_simulator.py
├── simulator/{input,topology,geometry,slicing,models,planning}/
├── simulator/{definition,timeline,simulator}.py
└── compose/launcher.py
```

Flat predecessor paths such as `entities/`, `seedemu_integration/`,
`materialization/`, `static_emulator.py`,
`simulator_input_generation.py`, `simulator/static_input/`, and flat
Simulator slicing/geometry modules are retired and absent.

## SEED extnode integration baseline

The accepted baseline for this milestone is SEED commit
`23a565a63d4ff50532f4313ca9ecc6afbfc22e16` (`feat: add generic extension node
support`). Skyseed uses that commit's upstream `ExtensionNode` lifecycle,
upstream `Base` plus upstream `Docker`, public Node metadata projection,
standard Satellite/Router compilation, and extnode-specific zero-interface
services using `network_mode: none`. Satellite extnodes do not inherit legacy
Global/AS generic options; Skyseed explicitly supplies only the Base
`sysctl_netipv4_conf_rp_filter` default required by the current Docker
compiler. Skyseed locally projects its constructed GroundStation Router
subclasses into the standard rnode/brdnode lifecycle and does not require a
new upstream Router-registration API.

Canonical examples enable the standard published InternetMap image and no
longer stage a caller-provided source tree. `--no-internet-map` is retained
only for fault isolation, not as formal runtime-success evidence. A canonical
InternetMap-enabled runtime validation with isolated Satellites,
GroundStation, IX and an ordinary Router succeeded against the published
image; the Map page and container/network APIs loaded without errors, so this
candidate contains no InternetMap compatibility shim or custom image path.
