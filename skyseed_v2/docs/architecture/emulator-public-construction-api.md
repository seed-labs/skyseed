# Emulator Public Construction API

## Status and scope

The static Emulator construction API, domain-data ownership and file-driven
configuration boundary are implemented. The Simulator static-input contract is
**AVAILABLE**; Simulator algorithms and engine remain **NOT STARTED**. No entity
relationship or Emulator behavior changed:

```text
SkyseedAutonomousSystem
├── Shell
│   └── Orbit
│       └── Satellite
└── GroundStation
```

## Package ownership

| Path | Responsibility | Main public imports |
| --- | --- | --- |
| `skyseed_v2.entities` | Emulator entities and tightly bound values | `Shell`, `Orbit`, `OrbitParameters`, `Satellite`, `GroundStation`, `GroundStationLocation` |
| `skyseed_v2.configuration` | Frozen Config values and versioned YAML loading | qualified `*Config` values, `load_static_emulator_config` |
| `skyseed_v2.static_emulator_creation` | Config-to-Emulator creation operation | `create_static_emulator`, `StaticEmulatorCreationResult` |
| `skyseed_v2.node_id` | Neutral Emulator–Simulator–future-dynamic-execution logical identity contract | `NodeId` |
| `skyseed_v2.simulator_input_generation` | Shared NodeId derivation, supported-entity traversal and pure input copying | three input-generation functions |
| `skyseed_v2.simulator.static_input` | Simulator-owned immutable static input contract | `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulatorStaticInput` |
| `skyseed_v2.seedemu_integration` | Skyseed-owned extensions of external seedemu | `SkyseedAutonomousSystem`, `SatelliteBase`, `SkyseedDockerCompiler` |
| `skyseed_v2.materialization` | Launch an already compiled Compose project | `ComposeProjectLauncher` |

These are ownership modules, not compatibility facades. The package root
still exposes only the six high-frequency entity/value names.

## Domain-data ownership

`OrbitParameters` is defined beside `Orbit` in `entities/orbit.py`. One Orbit
owns one frozen value containing altitude, inclination and right ascension.
Every Satellite in that Orbit shares those values through ownership; the
Satellite does not copy them and does not retain an Orbit reference.

`Satellite` owns only its identity, Shell/Orbit identifiers, index, mean
anomaly, payload and inherited SEED Node capability. `GroundStationLocation`
remains beside GroundStation because it is similarly bound to that entity and
is not shared by a separate owner.

## Simulator static-input design

The pure immutable input types are semantically owned by the Simulator under
`skyseed_v2.simulator.static_input`. That package contains no Simulator engine,
algorithm, entity traversal or SEED dependency. Conversion and traversal remain
in the external `simulator_input_generation` adapter:

```text
Satellite + owning OrbitParameters
  → create_satellite_simulation_input(satellite, orbit_parameters)
  → SatelliteSimulationInput

GroundStation
  → create_ground_station_simulation_input(ground_station)
  → GroundStationSimulationInput

Iterable[SkyseedAutonomousSystem]
  → one supported-entity traversal and NodeId derivation
  → create_simulator_static_input(...) → SimulatorStaticInput
```

The aggregate function accepts only `SkyseedAutonomousSystem` values, then
traverses AS → Shell → Orbit → Satellite. That traversal is where the owning
Orbit's parameters and each Satellite's own fields are combined. Ground
Stations are copied directly from each AS. Ordinary SEED ASes and unknown
objects fail closed.

`SimulatorStaticInput` receives already copied values, validates their types,
sorts deterministically, rejects duplicate logical IDs and builds a read-only
index. It does not traverse entities. No input retains an entity, Registry,
container, namespace, Compose, persistence or runtime Simulator object.

`NodeId(asn, entity_kind, entity_id)` is deterministically derived by one
private implementation in `simulator_input_generation.py`. It is a neutral
cross-layer value from `skyseed_v2.node_id`, not a Satellite/GroundStation
attribute, lookup service, entity primary key, SEED Registry key or
runtime-resource name. It exposes no resolve/entity/container method. No
public or global `NodeId → entity/service/container` mapping exists.

Any future dynamic-execution lookup is owner-directed rather than
index-directed:

```text
NodeId.asn
  → SatelliteBase.getAutonomousSystem(asn)
  → require SkyseedAutonomousSystem
NodeId.entity_kind + NodeId.entity_id
  → get_satellite(entity_id) or get_ground_station(entity_id)
  → exact owned entity
```

Existing hierarchy queries remain available when needed:
`SkyseedAutonomousSystem.get_shell()` → `Shell.get_orbit()` →
`Orbit.get_satellite()`. Unknown ASN/IDs and unsupported or mismatched kinds
must fail closed. This documents only a future owner-directed lookup direction;
`SimulationPlanCompiler` is a historical label for the over-compressed dynamic
boundary. No resolver, dynamic plan or update path is implemented.

External composition will eventually pass the already completed pure input to
the Simulator:

```python
simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
simulator = Simulator(simulator_input)
topology = simulator.calculate_topology(...)
```

The latter two lines document only the future dependency direction; `Simulator`
and `Topology` are not implemented. The future Simulator will not accept
Emulator, AS, Satellite, GroundStation or SEED Node objects and will not invoke
the generation adapter itself.

## Static deployment and conceptual dynamic execution stages

```text
rendered Emulator / SEED static model
  → SkyseedDockerCompiler
  → Dockerfile / Compose / service / network deployment configuration

future TopologyDifference
  → SemanticPlanner → SemanticTransitionPlan
  → BackendPlanner → BackendExecutionPlan
  → ExecutionCompiler → ExecutableBundle → Runner
```

`SkyseedDockerCompiler` is implemented in `seedemu_integration` and owns static
deployment compilation. Every dynamic type above is a conceptual candidate;
no class, module, interface or test skeleton exists. Semantic network planning,
backend-specific execution planning and executable-form lowering are separate
responsibilities. `ExecutionCompiler` is not a frozen public name, has no
inheritance relationship or required shared public parent with
`SkyseedDockerCompiler`, and must not absorb its responsibility. The earlier
`SimulationPlanCompiler` label is **retired/corrected** as an over-compressed
description of the three dynamic stages, not a frozen master contract.

## Public construction paths

### Handwritten isolated path

```mermaid
flowchart LR
    U[caller] --> E[seedemu Emulator]
    U --> B[SatelliteBase]
    U --> A[SkyseedAutonomousSystem]
    B --> A
    A -->|create_shell| S[Shell]
    S -->|create_orbit| O[Orbit]
    O -->|create_satellite| T[Satellite]
    E -->|render + compile| C[SkyseedDockerCompiler]
    C --> Q[output + generated controls]
```

`examples/isolated_satellites/main.py` shows this path directly and contains no
YAML/configuration flow.

### Handwritten complete path

```mermaid
flowchart LR
    A[SkyseedAutonomousSystem] -->|create_shell| S[Shell]
    S -->|create_orbit| O[Orbit]
    O -->|create_satellite| T[Satellite]
    A -->|create_ground_station| G[GroundStation]
    B[SatelliteBase] --> X[IX]
    B --> N[ordinary AS / Network / Router]
    G --> X
    N --> X
    X --> P[explicit eBGP]
    A -->|create_simulator_static_input| I[SimulatorStaticInput]
    B --> C[render + SkyseedDockerCompiler]
    C --> Q[output + generated controls]
```

`examples/complete_static_emulator/main.py` exposes Satellite, GroundStation,
ordinary Router/Network, IX, eBGP, static Simulator input and
`SkyseedDockerCompiler` in one file. Only `(skyseed_as,)` enters static-input
generation.

### File-driven large path

```mermaid
flowchart LR
    Y[versioned YAML] -->|load_static_emulator_config| C[StaticEmulatorConfig]
    C -->|create_static_emulator| R[StaticEmulatorCreationResult]
    R --> E[Emulator entities and layers]
    R -->|skyseed_autonomous_systems| F[create_simulator_static_input]
    F --> I[SimulatorStaticInput]
    E --> K[render + SkyseedDockerCompiler]
    K --> O[output + generated controls]
```

The exact public sequence is:

`load_static_emulator_config(path)` → `create_static_emulator(config)` →
`create_simulator_static_input(creation.skyseed_autonomous_systems)`.

```python
config = load_static_emulator_config(path)
creation = create_static_emulator(config)
simulator_input = create_simulator_static_input(
    creation.skyseed_autonomous_systems
)
```

`create_static_emulator(config)` creates only Emulator entities and layers.
Its result has exactly `emulator`, `skyseed_autonomous_systems` and
`ordinary_autonomous_systems`; static-input generation is visibly separate.

Every formal Python entrypoint completes its current output by adding
`output/start.sh` and `output/cleanup.sh` beside the Compose project. These are
generated artifacts, not source-level wrappers:

```text
python entrypoint → complete output → output/start.sh → Docker Compose project
```

This adds no root or package public API. The shared writer is private example
tooling. Future Emulator/Simulator generator composition is not implemented or
named by this boundary.

## Implemented class and API map

```mermaid
classDiagram
    class Emulator {
        <<upstream seedemu>>
        +addLayer(layer)
        +render()
        +compile(compiler, output)
    }
    class SkyseedAutonomousSystem {
        <<entity owner + integration>>
        +create_shell(shell_id) Shell
        +create_ground_station(...) GroundStation
        +get_shell(shell_id) Shell
        +get_satellite(satellite_id) Satellite
        +get_ground_station(ground_station_id) GroundStation
    }
    class Shell {
        <<Emulator entity>>
        +create_orbit(orbit_id, parameters) Orbit
        +get_orbit(orbit_id) Orbit
    }
    class Orbit {
        <<Emulator entity>>
        +parameters OrbitParameters
        +create_satellite(...) Satellite
        +get_satellite(satellite_id) Satellite
    }
    class OrbitParameters {
        <<entity-bound value>>
        +altitude_km
        +inclination_deg
        +right_ascension_deg
    }
    class Satellite {
        <<Emulator entity + SEED Node>>
        +satellite_id
        +mean_anomaly_deg
    }
    class GroundStationLocation {
        <<entity-bound value>>
        +latitude_deg
        +longitude_deg
        +elevation_m
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
        +ground_station_id
        +location
    }
    class StaticEmulatorConfig {
        <<immutable configuration>>
    }
    class StaticEmulatorCreationResult {
        <<operation result>>
        +emulator
        +skyseed_autonomous_systems
        +ordinary_autonomous_systems
    }
    class NodeId {
        <<cross-layer contract value>>
        +asn
        +entity_kind
        +entity_id
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
        +list_nodes() tuple
    }
    class SatelliteBase {
        <<seedemu integration>>
    }
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }
    class ComposeProjectLauncher {
        <<materialization integration>>
        +launch(...) result
    }

    Emulator --> SatelliteBase : renders
    SatelliteBase *-- SkyseedAutonomousSystem : contains
    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "1" OrbitParameters : owns
    Orbit "1" *-- "*" Satellite : owns
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    GroundStation "1" *-- "1" GroundStationLocation : owns
    SatelliteSimulationInput *-- NodeId : identifies
    GroundStationSimulationInput *-- NodeId : identifies
    SimulatorStaticInput *-- SatelliteSimulationInput : contains
    SimulatorStaticInput *-- GroundStationSimulationInput : contains
    StaticEmulatorCreationResult --> Emulator : returns
    StaticEmulatorCreationResult --> SkyseedAutonomousSystem : returns
    Emulator --> SkyseedDockerCompiler : compiles with
```

### Retained public-type proof

- Entities correspond to user-visible ownership and creation points.
- Entity-bound values are required by direct construction.
- Config values make file/programmatic construction explicit and immutable.
- Static input types are the Simulator-owned immutable static-input contract.
- `NodeId` is public from its direct neutral module because static input,
  future Simulator state/plans and dynamic-execution entity lookup
  share it.
- `StaticEmulatorCreationResult` is module-public because the creation
  operation returns three independently owned values; it is not a root export.
- `ComposeProjectLauncher` remains outside Emulator construction and both
  compilation stages.

### Root export list

```python
from skyseed_v2 import (
    GroundStation,
    GroundStationLocation,
    Orbit,
    OrbitParameters,
    Satellite,
    Shell,
)
```

Static-input types and operations use explicit owner imports:

```python
from skyseed_v2.configuration import load_static_emulator_config
from skyseed_v2.node_id import NodeId
from skyseed_v2.static_emulator_creation import create_static_emulator
from skyseed_v2.simulator_input_generation import create_simulator_static_input
from skyseed_v2.simulator.static_input import SimulatorStaticInput
```

## Rename and removal record

| Previous name/path | Current name/path | Reason/disposition |
| --- | --- | --- |
| `Satellite.orbit_parameters` / `_orbit_parameters` | removed | shared data belongs to the owning Orbit |
| `entities/orbit_parameters.py` | `entities/orbit.py` | the value and its sole owner are tightly bound |
| `*Specification` | one-to-one `*Config` | names the actual role without DDD/input ambiguity |
| `specifications/` | `configuration/` | makes the file/programmatic boundary discoverable |
| stateless YAML reader class | `load_static_emulator_config(path)` | one direct operation needs no public class |
| stateless Config application class | `create_static_emulator(config)` | one direct operation needs no public class |
| four-field apply result | `StaticEmulatorCreationResult` | creation has only three outputs; Simulator input is separate |
| `application/simulator_static_input.py` | `simulator_input_generation.py` | direct cross-layer functions, no abstract application package |
| `application/static_scenario_application.py` | `static_emulator_creation.py` | direct operation needs no abstract application package |
| `static_emulator.py` | `static_emulator_creation.py` | distinguishes entity creation from `configuration/static_emulator.py`, which defines only Config values |
| `serialization/static_emulator_yaml_loader.py` | `configuration/yaml.py` | YAML reading belongs with Config values |
| per-entity projector classes | direct `create_*_simulation_input` functions | no state or reusable abstraction existed |
| input `from_*` classmethods | direct generation functions | input types remain pure data |
| `simulator_input/` | `simulator/static_input/` | makes Simulator semantic ownership explicit without adding an engine or moving the entity-aware generation adapter |
| `SimulationNodeId` in historical `simulator_input/identity.py` | `NodeId` in `node_id.py` | one neutral identity is shared by Emulator packaging, future Simulator state/plans and dynamic-execution lookup; no compatibility alias remains |

No compatibility aliases remain for deleted classes, types or old imports.

## API change summary

- Entity ownership remains AS → Shell → Orbit → Satellite and AS →
  GroundStation. Entity `create_*` behavior is unchanged.
- `Satellite` construction no longer accepts or stores `OrbitParameters`.
- All `*Specification` imports change one-to-one to `*Config` from
  `skyseed_v2.configuration`.
- File loading is a function import from `skyseed_v2.configuration`.
- Emulator creation and Simulator-input generation are two separate direct
  module imports and calls.
- Simulator input types move from `skyseed_v2.simulator_input` to
  `skyseed_v2.simulator.static_input`; no compatibility alias remains.
- `SimulationNodeId` is renamed to `NodeId`; its three fields and serialized
  `value` format remain unchanged, while its owner import becomes
  `from skyseed_v2.node_id import NodeId`.
- The previously proposed `create_emulator_node_index()` is removed. Existing
  Base/AS/Shell/Orbit lookup methods remain the only future entity-resolution
  building blocks; no new lookup API was necessary.
- Formal Python entrypoints generate project-scoped controls inside `output/`;
  source-level shell wrappers are removed.
- Root exports remain the same six entity/value names.

## Review checklist

- [ ] Orbit alone owns common orbital parameters.
- [ ] The three examples expose the intended call chains.
- [ ] Static Simulator inputs contain no entity or runtime-resource reference.
- [ ] `NodeId` has no resolution/resource capability.
- [ ] No public/global `NodeId → entity/service/container` mapping exists.
- [ ] Existing owners provide only their narrow AS/Shell/Orbit/entity queries.
- [ ] `simulator.static_input` imports no entity, seedemu integration,
      `SkyseedDockerCompiler` or upstream seedemu module.
- [ ] `StaticEmulatorCreationResult` has exactly three fields.
- [ ] No old class, type or import alias survives.
- [ ] `SkyseedDockerCompiler` and the conceptual dynamic execution chain are
      documented as independent lifecycles with no required common parent.
- [ ] Simulator static input contract is available; algorithms and engine
      remain NOT STARTED.
