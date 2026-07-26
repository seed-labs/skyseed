# Skyseed v2 Architecture Overview

## 1. Status

The static Emulator core, receiving-side Simulator static-input contract,
accepted/frozen Simulator Iteration 1 composition/time/static Topology,
Iteration 2 static Zone domain, formal examples and Compose materialization
tooling are implemented. The complete dynamic public contract is accepted and
frozen; the Dynamic Slice A–E main chain is implemented and accepted.
TopologyDifference is implemented and accepted. G has a repository-local
read-only Tool and richer example implementation candidate under final review;
the complete Dynamic Slice stage is not yet accepted. Planning,
`SimulationRunner` and UT remain deferred and unimplemented.

## 2. System division

### Emulator

The Emulator owns:

- static Skyseed entities and ownership;
- narrow seedemu lifecycle integration;
- static Network, Router, IX and routing-layer construction;
- static deployment compilation into a Compose artifact;
- approved entity facts copied by an external adapter for Simulator
  consumption.

The source packages make those responsibilities explicit:

```text
entities/
seedemu_integration/
node_id.py
simulator/
└── static_input/
configuration/
static_emulator.py
simulator_input_generation.py
materialization/
```

### Simulator

The receiving-side immutable boundary types are implemented under
`simulator/static_input/`; they are construction data rather than the complete
or long-lived Simu domain model. Iteration 1 implements this construction
direction:

```text
Emulator entities
→ simulator_input_generation
→ immutable boundary data
→ Simulator construction
→ Simulator-owned domain objects
```

`SimulationDefinition(static_input, timeline)` is the immutable construction
input. `Simulator` retains it, exposes the same Timeline instance and explicitly
materializes at most one Topology. Creation is atomic and does not occur in the
constructor. Topology owns a distinct immutable object tree, including at most
one static Zone layout:

```text
Simulator
└── Topology
    ├── SkyseedAutonomousSystem[]
    │   ├── GroundStation[]
    │   └── Shell[]
    │       └── Orbit[]
    │           └── Satellite[]
    └── ZoneLayout [0..1]
        ├── Zone[]
        │   └── LatitudeLongitudeRectangle
        └── ZoneMembershipModel
```

Topology stores its AS tuple, immutable ASN index, unified immutable `NodeId`
node index and optional `ZoneLayout`. Shell and Orbit facade queries delegate
down the owner chain; Zone facade queries delegate to the layout. A Topology
without a Zone layout remains valid. After Topology creation, Simulator may
explicitly create at most one fixed-Model Slicer while keeping Topology
static. No speculative Satellite-parent reverse lookup is present.

Iteration 2 freezes this Zone construction chain:

```text
zones.yaml / programmatic construction
→ ZoneLayoutDefinition
→ Simulator.create_topology(...)
→ Topology-owned ZoneLayout
→ Zone[]
→ fixed ZoneMembershipModel
→ Topology facade
```

For file input, the loader path is `raw YAML mapping/list →
LatitudeLongitudeRectangleDefinition → ZoneDefinition →
ZoneLayoutDefinition`. YAML is only an input source: the loader returns the
same definition type that programmatic callers construct, and no YAML-specific
Zone type exists. The Definition is materialization input and is not retained
in the Topology object tree. ZoneLayout's read-only Model query returns the
exact fixed membership Model; it neither copies nor replaces it.

## 3. Static entity ownership

The implemented relation is:

```text
SkyseedAutonomousSystem
├── Shell
│   └── Orbit
│       └── Satellite
└── GroundStation
```

`Satellite` inherits seedemu `Node`; `GroundStation` inherits seedemu `Router`.
Each object is unique across user construction, AS ownership, Registry
registration and compilation. The integration-bearing nature of these objects
is why the package is named `entities`, not `domain`.

`OrbitParameters` is defined beside and owned by `Orbit`. Satellite retains no
Orbit reference and no copy of altitude, inclination, right ascension or
epoch. Orbit-owned UTC `epoch_utc` is implemented and accepted in A–E;
Satellite mean anomaly
means its value at that epoch.

## 4. Emulator → Simulator static boundary

`simulator_input_generation.py` produces immutable input data through three
direct functions:

- `create_satellite_simulation_input()`;
- `create_ground_station_simulation_input()`;
- `create_simulator_static_input()`.

One private traversal covers only AS → Shell → Orbit → Satellite and AS →
GroundStation. One private derivation rule constructs
`NodeId(asn, entity_kind, entity_id)` for all input-generation paths. Each per-entity input
copies only approved static scalars. The aggregate sorts by `NodeId.value`,
rejects duplicate complete logical IDs and provides immutable typed query
results only for complete `NodeId` values. It does not scan local string IDs or
retain entity, Registry, container, namespace, Compose,
persistence or runtime Simulator objects.

No global or public `NodeId → entity` mapping exists. Satellite and
GroundStation do not have a `node_id` property because the value identifies a
node at a cross-layer boundary rather than inside the Emulator entity model.
`NodeId` is a pure value and exposes no lookup or resource method.

Dependencies point from the generation adapter toward `entities`,
`seedemu_integration`, `node_id` and `simulator.static_input`. The
receiving-side input package itself imports none of the Emulator/SEED layers
and uses only neutral `node_id`. `node_id` in turn uses the root identifier
validator. Static input is data, not a facade over Emulator objects or a
long-lived Simulator domain graph. Simulator receives that completed input
through its Definition, materializes its own domain objects and never invokes
the entity-aware adapter itself. Simulator core imports none of the Emulator
entities, seedemu integration, upstream seedemu or the generation adapter.

## 5. Configuration and creation

`configuration/` contains frozen backend-independent Config and Definition
values:

- `satellite.py`: generated and explicit Shell/Orbit/Satellite Config;
- `ground_station.py`: GroundStation Config and private stable-ID derivation;
- `attachments.py`: node-to-existing-network relation values;
- `ground_network.py`: the minimal ordinary Network, Router, IX, AS and eBGP
  Config adapter required by the complete static example;
- `configuration/static_emulator_config.py`: Skyseed AS and complete static Emulator
  Config;
- `zone.py`: immutable Zone layout construction definitions;
- `yaml.py`: strict YAML reading for both static Emulator Config and Zone layout
  definitions.

`load_static_emulator_config(path)` returns one `StaticEmulatorConfig`.
Top-level `static_emulator.py` owns `create_static_emulator(config)`, which
sequences public entity and seedemu create/join calls and directly returns the
upstream `seedemu.core.Emulator`. `Emulator.getLayer("Base")` retrieves the
real `SatelliteBase` owner, whose narrow
`get_skyseed_autonomous_systems()` query supplies the AS collection for the
separate `create_simulator_static_input(...)` operation.
Handwritten callers bypass the file-driven operations.

`load_zone_layout_definition(path)` directly returns a
`ZoneLayoutDefinition`. Programmatic construction and file loading converge on
that exact type before `Simulator.create_topology(...)` materializes the Zone
domain graph.

## 6. seedemu integration

`seedemu_integration/` is Skyseed-owned code that extends external seedemu:

- `SkyseedAutonomousSystem` owns Skyseed entities and bridges their lifecycle;
- `SatelliteBase` incorporates Satellite nodes into Base render behavior;
  it also exposes only the Skyseed AS values it truly owns;
- `SkyseedDockerCompiler` handles isolated Satellite output and normalized
  metadata while ordinary nodes retain upstream paths.

The package is not a vendored seedemu copy and not data seeding code.

## 7. Static deployment, dynamic Slice state and future execution

`SkyseedDockerCompiler` lowers one rendered Emulator to a Compose project.
After all current generators finish, the formal example entrypoint adds
project-scoped `output/start.sh` and `output/cleanup.sh`. The former validates
and starts the exact generated project; the latter stops it without deleting
the output. `ComposeProjectLauncher` remains the programmatic, state-verifying
consumer of the same Compose artifact. Materialization is outside entities,
static Simulator input and both compilation stages.

The implemented static chain is:

```text
Emulator / SEED static model
→ SkyseedDockerCompiler
→ compiled Docker / Compose project
→ ComposeProjectLauncher
```

The accepted/frozen A–E dynamic state chain is implemented and accepted:

```text
Static Topology + Timeline timestamp
→ PositionModel
→ readonly EarthFixedPosition mapping
→ ConnectionModel
→ ConnectionSlice
→ RouteModel
→ RouteSlice
→ TopologySlice
```

The following `TopologyDifference` stage is implemented and accepted.

`Simulator` creates one Slicer only after Topology exists. Slicer retains exact
Topology, Timeline and explicit Position/Connection/Route Models, then
validates and atomically publishes ordered Slices. Topology never creates or
compares Slices.

The conceptual/open backend execution chain is:

```text
BackendExecutionPlan
→ ExecutionCompiler
→ ExecutableBundle
→ SimulationRunner
```

Earlier design records separated backend-neutral and backend-specific
planning, but detailed Planner/Plan public names remain deferred.
`ExecutionCompiler` only lowers a future approved backend plan;
`SimulationRunner` only executes its bundle. They do not re-plan, recompute
Topology, select another backend or hide failure with fallback.

The two chains have distinct inputs, outputs, lifecycles and execution
semantics. Their services are external to Emulator and Simulator domain state,
have no required common compiler/executor parent, and remain separate even if
a future Simulator facade invokes them. `ComposeProjectLauncher` is not
`SimulationRunner`. The earlier `SimulationPlanCompiler` wording is
retired/corrected as an over-compressed description.

## 8. Formal examples

- `isolated_satellites`: direct entity construction,
  `SkyseedDockerCompiler` and generated output controls.
- `complete_static_emulator/emu.py`: shared construction helper returning only
  the upstream Emulator.
- `complete_static_emulator/main.py`: canonical compile plus offline-tooling
  entrypoint using one Emulator and one Topology.
- `complete_static_emulator/simu.py`: external assembly entrypoint that reuses
  `emu.py`, queries Skyseed AS values from `SatelliteBase`, adapts them into
  `SimulatorStaticInput`, loads `zones.yaml` as a `ZoneLayoutDefinition`, creates
  the Definition/Simulator and materializes the Topology with its Zone layout,
  then explicitly creates three Models and the Slicer, queries Slice 0 and
  generates two real Slices.
- `large_static_network`: YAML → Config → Emulator creation → static Simulator
  input generation → `SkyseedDockerCompiler` → generated output controls.
  Validation remains Docker/Compose compile/config-only until a user explicitly
  runs `output/start.sh`.

The examples are executable architecture records; their calls lead directly
to the owning public methods.

## 9. Accepted next boundary and deferred work

The detailed frozen contract is
[`dynamic-slice-public-contract.md`](dynamic-slice-public-contract.md).
Its approved implementation dependency order is:

```text
Slice A time/epoch/coordinates/Position
→ Slice B Zone range/Connection values
→ Slice C GeometricConnectionModel
→ Slice D Route
→ Slice E Slicer/TopologySlice
→ Slice F TopologyDifference
→ Slice G Tool/example/final audit
```

The engineering authorization groups are A–E for the complete Dynamic Slice
main chain, F for TopologyDifference and G for Tool/example/final
synchronization. A–E is implemented and accepted. F TopologyDifference is the
implemented and accepted adjacent comparison; G is an implementation
candidate under final review. Automated validation does not accept the
complete Dynamic Slice stage.

Future Simulator state and plans will cite `NodeId`. Any later approved
dynamic-execution resolution path will call
`SatelliteBase.getAutonomousSystem(node_id.asn)`, require a
`SkyseedAutonomousSystem`, then dispatch the supported kind to
`get_satellite(node_id.entity_id)` or
`get_ground_station(node_id.entity_id)`. More specific hierarchical lookup can
use existing `get_shell()`, `Shell.get_orbit()` and `Orbit.get_satellite()`.
It must fail closed on a missing ASN/ID, wrong AS, unsupported or mismatched
kind, and duplicate ownership; it must not build a global index or parse names
to guess nodes. No plan/resolution/update type currently exists, and no current
code resolves `NodeId` to containers, services, interfaces or other runtime
resources, computes Satellite positions or visibility, selects a service
Satellite or link, plans routes, emits BPF changes or executes a dynamic
simulation.

The detailed status labels, design evolution and open questions are in
[`../design/simulator-conceptual-design.md`](../design/simulator-conceptual-design.md).
The top-level conceptual class map is in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-approved-dynamic-slice-boundary-map).
