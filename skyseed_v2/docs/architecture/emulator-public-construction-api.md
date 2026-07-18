# Emulator Public Construction API

## Status and scope

The static Emulator construction API and package layout are implemented. This
document records the current code after the package-structure review. It does
not define or start Simulator behavior.

The ownership model is unchanged:

```text
SkyseedAutonomousSystem
├── Shell
│   └── Orbit
│       └── Satellite
└── GroundStation
```

## Package ownership

| Package | Responsibility | Main public imports |
| --- | --- | --- |
| `skyseed_v2.entities` | Emulator entities and tightly bound values | `Shell`, `Orbit`, `OrbitParameters`, `Satellite`, `GroundStation`, `GroundStationLocation` |
| `skyseed_v2.simulator_input` | Immutable static data supplied to the future Simulator | `SatelliteSimulationInput`, `GroundStationSimulationInput`, `SimulationNodeId`, `SimulatorStaticInput` |
| `skyseed_v2.seedemu_integration` | Skyseed-owned extensions of external seedemu | `SkyseedAutonomousSystem`, `SatelliteBase`, `SkyseedDockerCompiler` |
| `skyseed_v2.specifications` | Immutable construction inputs split by subject | all qualified `*Specification` types |
| `skyseed_v2.serialization` | Versioned YAML parsing into specifications | `StaticEmulatorScenarioLoader` |
| `skyseed_v2.application` | Application sequencing over public create/join methods | `StaticEmulatorSpecificationApplier`, `StaticEmulatorApplyResult` |
| `skyseed_v2.materialization` | Launch an already compiled Compose project | `ComposeProjectLauncher` |

These are ownership packages, not compatibility facades. The defining files
are discoverable from the primary class names.

## Simulator static-input design

The boundary is a frozen scalar snapshot:

```text
Satellite
  → SatelliteSimulationInput.from_satellite()

GroundStation
  → GroundStationSimulationInput.from_ground_station()

one or more AS objects
  → SimulatorStaticInput.from_autonomous_systems()
```

The entity types never import `simulator_input`. Input conversion copies
approved values and retains no entity reference. `SimulatorStaticInput`
contains immutable tuples and a read-only logical-ID index; it retains no
Registry, container, namespace, Compose, persistence or runtime Simulator
object. Ordinary seedemu AS objects are accepted in a mixed input iterable and
ignored because they own no supported Skyseed entities.

The direct classmethods replace copying-only conversion services. The
multi-AS classmethod replaces a separately instantiated aggregate builder.

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
```

`examples/isolated_satellites/main.py` contains this path directly. It imports
no specification, serialization or application type.

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
    A --> I[SimulatorStaticInput.from_autonomous_systems]
    B --> C[render + SkyseedDockerCompiler]
```

`examples/complete_static_emulator/main.py` shows Satellite, GroundStation,
ordinary Router/Network, IX, eBGP, static Simulator input and Compiler in one
file without YAML or application orchestration.

### File-driven large path

```mermaid
flowchart LR
    Y[versioned YAML] --> L[StaticEmulatorScenarioLoader.load]
    L --> S[StaticEmulatorScenarioSpecification]
    S --> A[StaticEmulatorSpecificationApplier.apply]
    A --> E[public entity create/join methods]
    E --> I[SimulatorStaticInput.from_autonomous_systems]
    A --> C[render + SkyseedDockerCompiler]
```

`examples/large_static_network/main.py` is the only formal YAML example. The
Loader owns serialization; the Applier owns heterogeneous sequencing. Neither
is required for handwritten construction.

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
        +satellites() tuple
        +ground_stations() tuple
    }
    class Shell {
        <<Emulator entity>>
        +create_orbit(orbit_id, parameters) Orbit
    }
    class Orbit {
        <<Emulator entity>>
        +create_satellite(...) Satellite
        +create_satellites(...) tuple
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
        +orbit_parameters
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
    class SimulationNodeId {
        <<Simulator-input value>>
        +asn
        +entity_kind
        +entity_id
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
        +from_satellite(satellite)$
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
        +from_ground_station(ground_station)$
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
        +from_autonomous_systems(autonomous_systems)$
        +list_nodes() tuple
    }
    class SatelliteBase {
        <<seedemu integration>>
    }
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }
    class StaticEmulatorScenarioSpecification {
        <<input specification>>
    }
    class StaticEmulatorScenarioLoader {
        <<serialization adapter>>
        +load(path) StaticEmulatorScenarioSpecification
    }
    class StaticEmulatorSpecificationApplier {
        <<application service>>
        +apply(specification) StaticEmulatorApplyResult
    }
    class StaticEmulatorApplyResult {
        <<operation result>>
        +emulator
        +simulator_input
    }
    class ComposeProjectLauncher {
        <<materialization integration>>
        +launch(...) result
    }

    Emulator --> SatelliteBase : renders
    SatelliteBase *-- SkyseedAutonomousSystem : contains
    SkyseedAutonomousSystem "1" *-- "*" Shell : owns
    Shell "1" *-- "*" Orbit : owns
    Orbit "1" *-- "*" Satellite : owns
    Orbit *-- OrbitParameters : uses
    SkyseedAutonomousSystem "1" *-- "*" GroundStation : owns
    GroundStation *-- GroundStationLocation : uses
    SatelliteSimulationInput --> Satellite : copies static values from
    GroundStationSimulationInput --> GroundStation : copies static values from
    SatelliteSimulationInput *-- SimulationNodeId : identifies
    GroundStationSimulationInput *-- SimulationNodeId : identifies
    SimulatorStaticInput *-- SatelliteSimulationInput : contains
    SimulatorStaticInput *-- GroundStationSimulationInput : contains
    StaticEmulatorScenarioLoader --> StaticEmulatorScenarioSpecification : creates
    StaticEmulatorSpecificationApplier --> StaticEmulatorScenarioSpecification : reads
    StaticEmulatorSpecificationApplier --> StaticEmulatorApplyResult : returns
    Emulator --> SkyseedDockerCompiler : compiles with
```

### Retained public-type proof

- Entities correspond to user-visible ownership and creation points.
- Entity-bound values are required by direct constructors.
- Static input types are the explicit, immutable future-Simulator boundary.
- Loader and Applier remain outside the root because handwritten callers do
  not need them.
- `StaticEmulatorApplyResult` remains application-only because no existing
  object owns all returned values.
- `ComposeProjectLauncher` remains outside Emulator construction and Compiler
  behavior.

### Root export list

```python
from skyseed_v2 import (
    GroundStation,
    GroundStationLocation,
    GroundStationSimulationInput,
    Orbit,
    OrbitParameters,
    Satellite,
    SatelliteSimulationInput,
    Shell,
    SimulationNodeId,
    SimulatorStaticInput,
)
```

## Rename and removal record

| Previous name/path | Current name/path | Disposition |
| --- | --- | --- |
| per-entity `*Descriptor` types | per-entity `*SimulationInput` types | renamed to state consumer and direction |
| stateless per-entity projector classes | `from_satellite()` / `from_ground_station()` | removed; conversion belongs to the input type |
| entity-catalog aggregate | `SimulatorStaticInput` | renamed from collection metaphor to actual boundary role |
| separate catalog builder | `SimulatorStaticInput.from_autonomous_systems()` | removed; one direct aggregate constructor |
| `domain/` | `entities/` | renamed because the objects also carry SEED capability |
| `seed/` | `seedemu_integration/` | renamed to identify external integration ownership |
| root scenario loader/application modules | `serialization/` and `application/` | moved to explicit responsibility packages |
| monolithic specifications module | `specifications/{satellite,ground_station,network,scenario}.py` | split by input family |

No compatibility aliases remain for deleted classes or old import paths.

## API change summary

- Entity `create_*` methods and ownership relations are unchanged.
- Root imports keep entity/value types and expose the newly named static input
  types; deleted boundary names are not aliased.
- Integration imports now come from `skyseed_v2.seedemu_integration`.
- Loader and Applier imports now come from `skyseed_v2.serialization` and
  `skyseed_v2.application`.
- Specification package imports remain `skyseed_v2.specifications`; defining
  modules are now discoverable by subject.

## Review checklist

- [ ] The three examples expose the intended call chains.
- [ ] Entity ownership still matches AS → Shell → Orbit → Satellite and AS →
  GroundStation.
- [ ] Static Simulator inputs contain no entity or runtime-resource reference.
- [ ] Current imports and filenames are understandable without source search.
- [ ] Simulator remains NOT STARTED.
