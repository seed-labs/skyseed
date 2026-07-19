# Emulator Public Construction API

## Status and scope

Static Emulator configuration loading and construction are implemented. The
public file path is one direct chain; it creates the upstream
`seedemu.core.Emulator`, not a wrapper or Skyseed-specific Emulator subtype.
The Simulator static-input contract and Simulator Slice 1
composition/time/static Topology are **IMPLEMENTED**. Position, dynamic
Topology, Planning and execution remain **NOT STARTED**.

## Package ownership

| Path | Responsibility | Main public imports |
| --- | --- | --- |
| `skyseed_v2.entities` | Emulator entities and tightly bound values | `Shell`, `Orbit`, `OrbitParameters`, `Satellite`, `GroundStation`, `GroundStationLocation` |
| `skyseed_v2.configuration` | Frozen Config and auxiliary values plus strict YAML reading | `*Config`, `GeneratedOrbitLayout`, distribution/attachment/reference values, `load_static_emulator_config` |
| `skyseed_v2.static_emulator` | Stateless Config-to-Emulator operation | `create_static_emulator` |
| `skyseed_v2.seedemu_integration` | Skyseed extensions of upstream seedemu | `SkyseedAutonomousSystem`, `SatelliteBase`, `SkyseedDockerCompiler` |
| `skyseed_v2.node_id` | Neutral logical identity | `NodeId` |
| `skyseed_v2.simulator_input_generation` | Entity traversal and pure input copying | three input-generation functions |
| `skyseed_v2.simulator.static_input` | receiving-side immutable cross-layer boundary input | three input types |
| `skyseed_v2.simulator` | Simulator definition, time, composition root and immutable static Topology domain | `SimulationDefinition`, `SimulationTimeline`, `Simulator`, `Topology` and five static domain types |
| `skyseed_v2.materialization` | Launch an already compiled Compose project | `ComposeProjectLauncher` |

The root package still exports only the six high-frequency entity/value names.
There is no top-level `emulator/` package, Builder, Factory, Loader class,
creation-result wrapper or compatibility facade.

## Configuration meaning and separation

`Config` means a frozen, validated declaration of how to construct the static
Emulator. It is not an entity, runtime state or universal application config.
File input and programmatic input both produce the same Config graph.

The configuration package separates three meanings without changing the YAML
schema:

```text
satellite.py       Shell / Orbit / Satellite declarations and generation values
ground_station.py GroundStation stable attributes and ID derivation rule
ground_network.py thin ordinary SEED AS / Network / Router / IX / eBGP adapter
attachments.py    node-to-existing-network attachment relations
static_emulator_config.py complete validated Config graph
yaml.py            YAML → Config only
```

The ordinary ground-network Config is intentionally narrow. At pinned upstream
seedemu 0.0.7, topology construction is imperative through `Base`,
`AutonomousSystem`, `Router` and routing-layer APIs. `Emulator.dump/load` is a
pickle of the entire Registry and replaces an Emulator's layers; it is not a
declarative, cross-file ground-network loader. Upstream example YAML files are
test-runner manifests, Kubernetes setup input or generated deployment files,
not reusable topology declarations.

GroundStation attributes, ordinary ground-network declarations and attachment
relations therefore remain distinct even though the unchanged v1 YAML nests
`ix_attachments` beside each node.

## Simulator static-input design

`SimulatorStaticInput` contains copied immutable values only. The adapter
accepts an iterable of `SkyseedAutonomousSystem`, walks the real ownership tree
and derives `NodeId`; the receiving-side input package never imports Config,
entities or seedemu.

The file-driven caller obtains the AS collection from its real owner:

```python
base = emulator.getLayer("Base")
skyseed_autonomous_systems = base.get_skyseed_autonomous_systems()
simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
```

`SatelliteBase.get_skyseed_autonomous_systems()` is a narrow read-only query.
It composes upstream `Base.getAsns()` and `Base.getAutonomousSystem()` and keeps
no second collection or global index.

`SimulatorStaticInput.get(...)`, `get_satellite(...)` and
`get_ground_station(...)` accept only complete `NodeId` values. They do not
scan `entity_id` strings or provide a string fallback.

`NodeId` remains a pure cross-layer value. Any future dynamic execution will
resolve its ASN through the Base owner and then use the existing narrow AS,
Shell and Orbit queries. No Simulator algorithm, dynamic plan, resource
resolver or runtime updater exists.

## Static deployment and conceptual dynamic execution stages

```text
rendered Emulator
  → SkyseedDockerCompiler
  → compiled Docker/Compose project
  → ComposeProjectLauncher

future TopologyDifference
  → SemanticPlanner
  → SemanticTransitionPlan
  → BackendPlanner
  → BackendExecutionPlan
  → ExecutionCompiler
  → ExecutableBundle
  → SimulationRunner
```

`SkyseedDockerCompiler` is implemented and does not read Config. The dynamic
names are conceptual/open. `SimulationPlanCompiler` remains only the
retired/corrected label for a formerly over-compressed dynamic chain.

## Public construction paths

### Handwritten isolated path

```mermaid
flowchart LR
    U[caller] --> E[upstream Emulator]
    U --> B[SatelliteBase]
    U --> A[SkyseedAutonomousSystem]
    A -->|create_shell| S[Shell]
    S -->|create_orbit| O[Orbit]
    O -->|create_satellite| T[Satellite]
    B --> A
    E -->|render + compile| C[SkyseedDockerCompiler]
```

### Handwritten complete path

The complete example has three explicit files and no entrypoint-to-entrypoint
import:

```text
emu.py  → build_emulator() → upstream Emulator
main.py → build_emulator() → render/compile only
simu.py → build_emulator()
        → query SatelliteBase-owned Skyseed AS values
        → create_simulator_static_input(...)
        → SimulationDefinition
        → Simulator
        → Topology
```

`build_emulator()` returns only the upstream Emulator. `main.py` is the pure
Emulator entrypoint. `simu.py` is external assembly across the Emulator side,
thin adapter and Simulator side; it does not import `main.py`.

### File-driven large path

```mermaid
flowchart LR
    Y[versioned YAML] -->|load_static_emulator_config| C[StaticEmulatorConfig]
    C -->|create_static_emulator| E[upstream Emulator]
    E -->|getLayer Base| B[SatelliteBase]
    B -->|get_skyseed_autonomous_systems| A[Skyseed AS collection]
    A --> F[create_simulator_static_input]
    F --> I[SimulatorStaticInput]
    E --> K[render + SkyseedDockerCompiler]
    K --> O[Compose output + generated controls]
```

The exact public sequence is:

```python
config = load_static_emulator_config(path)
emulator = create_static_emulator(config)
base = emulator.getLayer("Base")
skyseed_autonomous_systems = base.get_skyseed_autonomous_systems()
simulator_input = create_simulator_static_input(skyseed_autonomous_systems)
emulator.render()
emulator.compile(...)
```

`create_static_emulator(config)` is the only module operation that knows both
Config and real entity/seedemu construction APIs. It creates IX and ordinary
ground networks, creates Skyseed entities, applies attachment relations,
configures routing layers and returns the actual upstream Emulator.

## Implemented class and API map

```mermaid
classDiagram
    class Emulator {
        <<upstream seedemu>>
        +getLayer(name)
        +render()
        +compile(compiler, output)
    }
    class SatelliteBase {
        <<seedemu integration + AS owner>>
        +get_skyseed_autonomous_systems() tuple
        +getAutonomousSystem(asn)
    }
    class SkyseedAutonomousSystem {
        <<entity owner + integration>>
        +create_shell(shell_id) Shell
        +create_ground_station(...) GroundStation
        +get_satellite(id) Satellite
        +get_ground_station(id) GroundStation
    }
    class Shell {
        <<Emulator entity>>
        +create_orbit(...) Orbit
    }
    class Orbit {
        <<Emulator entity>>
        +parameters OrbitParameters
        +create_satellite(...) Satellite
    }
    class OrbitParameters {
        <<entity-bound value>>
    }
    class Satellite {
        <<Emulator entity + SEED Node>>
    }
    class GroundStationLocation {
        <<entity-bound value>>
    }
    class GroundStation {
        <<Emulator entity + SEED Router>>
    }
    class StaticEmulatorConfig {
        <<immutable configuration>>
    }
    class NodeId {
        <<cross-layer contract value>>
    }
    class SatelliteSimulationInput {
        <<immutable Simulator input>>
    }
    class GroundStationSimulationInput {
        <<immutable Simulator input>>
    }
    class SimulatorStaticInput {
        <<immutable Simulator input aggregate>>
    }
    class SkyseedDockerCompiler {
        <<seedemu integration>>
    }
    class ComposeProjectLauncher {
        <<materialization integration>>
    }

    Emulator *-- SatelliteBase : layer
    SatelliteBase *-- SkyseedAutonomousSystem : owns
    SkyseedAutonomousSystem *-- Shell : owns
    Shell *-- Orbit : owns
    Orbit *-- OrbitParameters : owns
    Orbit *-- Satellite : owns
    SkyseedAutonomousSystem *-- GroundStation : owns
    GroundStation *-- GroundStationLocation : owns
    StaticEmulatorConfig --> Emulator : create_static_emulator
    SatelliteSimulationInput *-- NodeId : identifies
    GroundStationSimulationInput *-- NodeId : identifies
    SimulatorStaticInput *-- SatelliteSimulationInput : contains
    SimulatorStaticInput *-- GroundStationSimulationInput : contains
    Emulator --> SkyseedDockerCompiler : compiles with
```

### Retained public-type proof

- Config types are the typed and validated programmatic/file-equivalent input.
- Distribution, attachment and reference values have narrower names because
  they are components of Config rather than independent configuration roots.
- `SatelliteBase` owns AS lifecycle and therefore owns the narrow AS query.
- `create_static_emulator` is a function because it has no state or lifecycle.
- No result wrapper or Emulator subclass is necessary.

## Simulator Slice 1 public contract

```mermaid
classDiagram
    class Simulator {
        <<implemented composition root>>
        +Simulator(definition)
        +get_definition() SimulationDefinition
        +get_timeline() SimulationTimeline
        +create_topology() Topology
        +get_topology() Topology
    }
    class SimulationDefinition {
        <<implemented frozen construction value>>
        +static_input SimulatorStaticInput
        +timeline SimulationTimeline
    }
    class SimulationTimeline {
        <<implemented frozen time value>>
        +iter_timestamps() Iterator
    }
    class SimulatorStaticInput {
        <<implemented immutable boundary input>>
    }
    class Topology {
        <<implemented immutable aggregate root>>
    }
    class SkyseedAutonomousSystem {
        <<implemented Simulator domain object>>
    }
    class Shell {
        <<implemented Simulator domain object>>
    }
    class Orbit {
        <<implemented Simulator domain object>>
    }
    class Satellite {
        <<implemented Simulator domain object>>
    }
    class GroundStation {
        <<implemented Simulator domain object>>
    }

    Simulator *-- SimulationDefinition : retains
    SimulationDefinition *-- SimulatorStaticInput : static_input
    SimulationDefinition *-- SimulationTimeline : timeline
    Simulator *-- Topology : creates atomically
    Topology *-- SkyseedAutonomousSystem : owns
    SkyseedAutonomousSystem *-- GroundStation : owns
    SkyseedAutonomousSystem *-- Shell : owns
    Shell *-- Orbit : owns
    Orbit *-- Satellite : owns
```

The public constructors and facade methods are:

| Type | Public construction / operations |
| --- | --- |
| `SimulationTimeline` | `SimulationTimeline(start_time, end_time, slice_interval)`; `is_bounded`; `iter_timestamps()` |
| `SimulationDefinition` | `SimulationDefinition(static_input, timeline)` |
| `Simulator` | `Simulator(definition)`; `get_definition()`; `get_timeline()`; `create_topology()`; `get_topology()` |
| `Topology` | direct construction is blocked; `autonomous_systems`; `get_autonomous_system(asn)`; NodeId-only `get_node`, `get_satellite`, `get_ground_station`; owner-directed `get_shell(asn, shell_id)` and `get_orbit(asn, shell_id, orbit_id)` |
| Simulator `SkyseedAutonomousSystem` | direct construction is blocked; `asn`; `shells`; `ground_stations`; owner-local `get_shell(shell_id)` and `get_ground_station(node_id)` |
| Simulator `Shell` | direct construction is blocked; `asn`; `shell_id`; `orbits`; owner-local `get_orbit(orbit_id)` |
| Simulator `Orbit` | direct construction is blocked; identity/orbit-parameter properties; `satellites`; owner-local `get_satellite(node_id)` |
| Simulator `Satellite` | `Satellite(node_id, orbit_index, mean_anomaly_deg)`; `satellite_id` |
| Simulator `GroundStation` | `GroundStation(node_id, latitude_deg, longitude_deg, elevation_m, station_type, operator)`; `ground_station_id` |

Topology retains only `_autonomous_systems`, `_autonomous_systems_by_asn` and
`_nodes_by_id`. Each owner retains its own tuple enumeration and private
`MappingProxyType` lookup: Orbit has Satellites by NodeId, Shell has Orbits by
local ID, and the Simulator AS has Shells by local ID and GroundStations by
NodeId. Tuple and mapping paths reference the same immutable instances.
Topology node-kind methods use `_nodes_by_id` and then check the returned type;
Shell and Orbit facade methods delegate through the AS/Shell owner chain. No
Satellite-parent reverse API or index exists.

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

Owner imports remain explicit:

```python
from skyseed_v2.configuration import load_static_emulator_config
from skyseed_v2.static_emulator import create_static_emulator
from skyseed_v2.simulator_input_generation import create_simulator_static_input
from skyseed_v2.simulator import (
    SimulationDefinition,
    SimulationTimeline,
    Simulator,
    Topology,
)
```

## Rename and removal record

| Previous name/path | Current name/path | Reason/disposition |
| --- | --- | --- |
| `static_emulator_creation.py` | `static_emulator.py` | the qualified function already states the operation; the module names its exact product |
| `StaticEmulatorCreationResult` | removed | the function returns the real upstream Emulator; AS values come from `SatelliteBase` |
| `OrbitGenerationConfig` | `GeneratedOrbitLayout` | generated layout value nested in `ShellConfig`, not an independent Config |
| `RaanDistributionConfig` | `RightAscensionDistribution` | expands the acronym and names an auxiliary value |
| `InOrbitPhaseDistributionConfig` | `InOrbitPhaseDistribution` | auxiliary distribution value |
| attachment/reference `*Config` names | `InternetExchangeAttachment`, `LocalNetworkAttachment`, `EbgpRouterReference` | relation/reference values are not independent Config roots |
| `configuration/network.py` | `configuration/ground_network.py` + `attachments.py` | separates ordinary ground declarations from attachment relations |
| `ShellConfig.generation` / `.orbits` | `.generated_orbit_layout` / `.explicit_orbits` | makes the XOR modes explicit in Python |
| generated `.raan` / `.phase` | `.right_ascension_distribution` / `.in_orbit_phase_distribution` | removes ambiguous abbreviations in Python |
| eBGP `.first` / `.second` | `.peer_routers` | current `unfiltered` relationship is symmetric; YAML keys remain unchanged |
| `EmulatorStaticInput` | `SimulatorStaticInput` | input is named for the receiving Simulator contract; the former audit-era name has no alias |
| `create_emulator_static_input(...)` | `create_simulator_static_input(...)` | the external adapter's product is Simulator static input; the former name has no alias |
| `SimulationDefinition.emulator_input` | `SimulationDefinition.static_input` | the field names the input's role in the Simulator definition; no compatibility keyword/property exists |

There are no compatibility aliases. YAML schema version and keys remain
unchanged, including `generation`, `orbits`, `raan`, `phase`, `first` and
`second`.

## API change summary

- `create_static_emulator(config) -> seedemu.core.Emulator` directly.
- `SatelliteBase.get_skyseed_autonomous_systems()` supplies the only required
  AS collection query from the real owner.
- Config remains the public declaration concept; only auxiliary values lose
  misleading `Config` suffixes.
- Ordinary ground-network Config remains a thin adapter because upstream has
  no reusable declarative topology file API at the pinned commit.
- YAML has one complete entry and no fragment loader, schema v2 or TLE path.
- `SimulatorStaticInput`, `create_simulator_static_input(...)` and
  `SimulationDefinition.static_input` are the only current names; old names
  have no compatibility entrypoints.
- `Simulator` explicitly and atomically materializes one immutable Topology;
  it does not create an Emulator or invoke the external adapter.
- Topology uses a unified NodeId node index and owner-directed hierarchy
  queries; duplicate typed/global and Satellite-parent reverse indexes/APIs are
  absent.

## Review checklist

- [ ] YAML → Config → upstream Emulator is one direct path.
- [ ] `create_static_emulator` returns an `Emulator`, with no second `.emulator`.
- [ ] AS collections are queried from `SatelliteBase` without a new index.
- [ ] Config/YAML modules do not depend on entities, seedemu or Simulator.
- [ ] Entities and Simulator static input do not import configuration.
- [ ] No creation result, Emulator subclass, Builder/Factory/Loader class,
      fragment loader or compatibility alias exists.
- [ ] YAML schema version, keys and fixtures remain unchanged.
- [ ] Handwritten, programmatic Config and YAML construction converge on the
      same owner APIs and deployment semantics.
- [ ] `emu.py` only builds and returns the upstream Emulator; both entrypoints
      reuse it and `simu.py` does not import `main.py`.
- [ ] `SimulatorStaticInput` queries accept only `NodeId`.
- [ ] Simulator core has no Emulator, seedemu or generation-adapter dependency.
- [ ] Topology creation is atomic; immutable tuple/mapping paths preserve
      instance identity and owner-directed queries.
- [ ] No old input name, duplicate Topology index or Satellite-parent reverse
      API remains.
- [ ] Position, dynamic Topology, Planning and execution remain NOT STARTED.
