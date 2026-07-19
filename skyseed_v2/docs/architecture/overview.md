# Skyseed v2 Architecture Overview

## 1. Status

The static Emulator core, receiving-side Simulator static-input contract,
formal examples and Compose materialization tooling are implemented.
Simulator algorithms and engine, dynamic topology, planning,
`SimulationRunner` and UT are not implemented. Their conceptual maps preserve
approved future responsibility directions but do not freeze public APIs.

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

The receiving-side immutable boundary types are implemented under
`simulator/static_input/`; they are construction data rather than the complete
or long-lived Simu domain model. The approved construction direction is:

```text
Emulator entities
→ simulator_input_generation
→ immutable boundary data
→ Simulator construction
→ Simulator-owned domain objects
```

The future `Simulator` is the Simu composition root and facade. It creates and
owns peer `SimulationTimeline`, `Topology` and `Planning` subsystems.
`Topology` uniquely owns or manages the Simu static network world, future Zone
definitions, dynamic Slice state and its internal indexes. `Planning` consumes
Topology's public results and may use a read-only Topology facade, but never
owns or modifies Topology data. None of these classes, algorithms or engine
concepts is implemented; exact contracts and public names remain open.

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
receiving-side input package itself imports none of the Emulator/SEED layers
and uses only neutral `node_id`. `node_id` in turn uses the root identifier
validator. Static input is data, not a facade over Emulator objects or a
long-lived Simulator domain graph. A future Simulator receives that completed
input, materializes its own domain objects and never invokes the entity-aware
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

## 7. Static deployment and dynamic execution

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

The conceptual/open dynamic chain is:

```text
BackendExecutionPlan
→ ExecutionCompiler
→ ExecutableBundle
→ SimulationRunner
```

Before dynamic lowering, a `SemanticPlanner` and a `BackendPlanner` have
separate responsibility for backend-neutral meaning and backend-specific
execution strategy. `ExecutionCompiler` only lowers an approved backend plan;
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
The top-level conceptual class map is in
[`conceptual-class-diagrams.md`](conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).
