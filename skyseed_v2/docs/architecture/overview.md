# Skyseed v2 Architecture Overview

## 1. Status

The static Emulator core, Simulator-owned static input contract, formal
examples and Compose materialization tooling are implemented. Simulator
algorithms and engine, dynamic topology, planning, Runner and UT are not
implemented in the current main slice. Their restored conceptual maps preserve
future responsibility directions but do not freeze public APIs.

## 2. System division

### Emulator

The Emulator owns:

- static Skyseed entities and ownership;
- narrow seedemu lifecycle integration;
- static Network, Router, IX and routing-layer construction;
- static deployment compilation into a Compose artifact;
- approved entity facts copied by an external adapter for future Simulator
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

The Simulator already owns its pure static-input contract under
`simulator/static_input/`. Future Simulator work may own time, positions,
visibility, topology, semantic planning, execution and observation. None of
those algorithms or engine concepts is implemented by the current repository.
The approved direction uses a thin Simulator coordination shell, keeps Zone
independent from the Topology coordination shell, and separates semantic
planning, backend planning, executable-form compilation, execution and
observation. Candidate names and public contracts remain open.

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
Orbit reference and no copy of altitude, inclination or right ascension.

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
results. It does not retain entity, Registry, container, namespace, Compose,
persistence or runtime Simulator objects.

No global or public `NodeId → entity` mapping exists. Satellite and
GroundStation do not have a `node_id` property because the value identifies a
node at a cross-layer boundary rather than inside the Emulator entity model.
`NodeId` is a pure value and exposes no lookup or resource method.

Dependencies point from the generation adapter toward `entities`,
`seedemu_integration`, `node_id` and `simulator.static_input`. The
Simulator-owned input package itself imports none of the Emulator/SEED layers
and uses only neutral `node_id`. `node_id` in turn uses the root identifier
validator. Static input is data, not a facade over Emulator objects. A future
Simulator receives that completed input and never invokes the entity-aware
adapter itself.

## 5. Configuration and creation

`configuration/` contains frozen backend-independent Config values:

- `satellite.py`: generated and explicit Shell/Orbit/Satellite Config;
- `ground_station.py`: GroundStation Config and private stable-ID derivation;
- `attachments.py`: node-to-existing-network relation values;
- `ground_network.py`: the minimal ordinary Network, Router, IX, AS and eBGP
  Config adapter required by the complete static example;
- `configuration/static_emulator_config.py`: Skyseed AS and complete static Emulator
  Config;
- `yaml.py`: strict versioned YAML reading.

`load_static_emulator_config(path)` returns one `StaticEmulatorConfig`.
Top-level `static_emulator.py` owns `create_static_emulator(config)`, which
sequences public entity and seedemu create/join calls and directly returns the
upstream `seedemu.core.Emulator`. `Emulator.getLayer("Base")` retrieves the
real `SatelliteBase` owner, whose narrow
`get_skyseed_autonomous_systems()` query supplies the AS collection for the
separate `create_simulator_static_input(...)` operation.
Handwritten callers bypass the file-driven operations.

## 6. seedemu integration

`seedemu_integration/` is Skyseed-owned code that extends external seedemu:

- `SkyseedAutonomousSystem` owns Skyseed entities and bridges their lifecycle;
- `SatelliteBase` incorporates Satellite nodes into Base render behavior;
  it also exposes only the Skyseed AS values it truly owns;
- `SkyseedDockerCompiler` handles isolated Satellite output and normalized
  metadata while ordinary nodes retain upstream paths.

The package is not a vendored seedemu copy and not data seeding code.

## 7. Compilation stages and materialization

`SkyseedDockerCompiler` lowers one rendered Emulator to a Compose project.
After all current generators finish, the formal example entrypoint adds
project-scoped `output/start.sh` and `output/cleanup.sh`. The former validates
and starts the exact generated project; the latter stops it without deleting
the output. `ComposeProjectLauncher` remains the programmatic, state-verifying
consumer of the same Compose artifact. Materialization is outside entities,
static Simulator input and both compilation stages.

The conceptual dynamic chain distinguishes backend-neutral semantic planning,
backend-specific execution planning, executable-form lowering and Runner
execution. `ExecutionCompiler` is a candidate label for the lowering step; it
is not implemented, does not inherit from `SkyseedDockerCompiler` and has no
required shared public parent. The earlier `SimulationPlanCompiler` wording is
retired/corrected as an over-compressed description of those three separate
responsibilities. Static Docker/Compose compilation does not consume dynamic
plans, and `ComposeProjectLauncher` is not a dynamic Runner.

## 8. Formal examples

- `isolated_satellites`: direct entity construction,
  `SkyseedDockerCompiler` and generated output controls.
- `complete_static_emulator`: direct entities plus ordinary Network/Router,
  IX, eBGP, Simulator static input, `SkyseedDockerCompiler` and generated
  output controls.
- `large_static_network`: YAML → Config → Emulator creation → static Simulator
  input generation → `SkyseedDockerCompiler` → generated output controls.
  Validation remains Docker/Compose compile/config-only until a user explicitly
  runs `output/start.sh`.

The examples are executable architecture records; their calls lead directly
to the owning public methods.

## 9. Deferred boundaries

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
resources, computes positions or visibility, selects links, plans routes,
emits BPF changes or runs a Simulator.

The detailed status labels, design evolution and open questions are in
[`../design/simulator-conceptual-design.md`](../design/simulator-conceptual-design.md).
The three restored conceptual class maps are in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-future-conceptual-map).
