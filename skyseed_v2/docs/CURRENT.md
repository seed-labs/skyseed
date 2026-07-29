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
- read-only Topology, Zone, and Dynamic Slice Inspection Tools;
- static Compose project launch/verification through
  `ComposeProjectLauncher`.

## Deferred

Planning, backend preparation/compilation, and dynamic execution capabilities
remain deferred. Their future names, owners, packages, inputs, outputs, and
execution chains are not frozen and require separate approval.

No package, empty directory, public type, fallback, or execution path for
those deferred capability areas exists in the current implementation.

## Current production paths

```text
src/skyseed_v2/
├── shared/
├── configuration/{emulator,simulator}/
├── emulator/{entities,seedemu}/
├── emulator/construction.py
├── emulator_to_simulator.py
├── simulator/{input,topology,geometry,slicing,models}/
├── simulator/{definition,timeline,simulator}.py
└── compose/launcher.py
```

Flat predecessor paths such as `entities/`, `seedemu_integration/`,
`materialization/`, `static_emulator.py`,
`simulator_input_generation.py`, `simulator/static_input/`, and flat
Simulator slicing/geometry modules are retired and absent.

## Candidate under review

The current unstaged SEED/Skyseed integration candidate is not accepted yet.
It proposes upstream `ExtensionNode` lifecycle support, a static deployment
path using upstream `Base` plus upstream `Docker`, public Node metadata
projection, standard Satellite/Router compilation, removal of the retired
Satellite Base/compiler/metadata/assets, and generic zero-interface services
using `network_mode: none`.

The standard SEED InternetMap path remains the published image. Skyseed
canonical examples explicitly require a caller-provided repaired InternetMap2
source/build context when InternetMap is enabled; this opt-in path is still
under review. Compose configuration alone does not prove that the
backwards-compatible published image handles zero-interface containers at
runtime. The Map-disabled zero-interface path is unaffected.
