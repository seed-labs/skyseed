# Skyseed v2 Architecture Overview

## 1. Status

The static Emulator core, static input boundary, formal examples and Compose
materialization tooling are implemented. Simulator, dynamic topology,
planning, Runner and UT are not implemented in the current main slice.

## 2. System division

### Emulator

The Emulator owns:

- static Skyseed entities and ownership;
- narrow seedemu lifecycle integration;
- static Network, Router, IX and routing-layer construction;
- compilation into a Compose artifact;
- immutable static data exported for future Simulator consumption.

The source packages make those responsibilities explicit:

```text
entities/
seedemu_integration/
simulator_input/
configuration/
static_emulator.py
simulator_input_generation.py
materialization/
```

### Simulator

Future Simulator work may own time, positions, visibility, topology, semantic
planning, backend planning, execution and observation. None of those concepts
is implemented or authorized by the current package refactor.

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

Each per-entity input copies only approved static scalars. The aggregate sorts
by `SimulationNodeId.value`, rejects duplicate complete logical IDs and
provides immutable typed query results. It does not retain entity, Registry,
container, namespace, Compose, persistence or runtime Simulator objects.

Dependencies point from the generation module toward `entities`,
`seedemu_integration` and `simulator_input`. The input package itself imports
none of those Emulator/SEED layers and uses only the neutral root identifier
validator. Static input is data, not a facade over Emulator objects.

## 5. Configuration and creation

`configuration/` contains frozen backend-independent Config values:

- `satellite.py`: generated and explicit Shell/Orbit/Satellite Config;
- `ground_station.py`: GroundStation Config and stable-ID derivation;
- `network.py`: Network, Router, IX attachment, ordinary AS and eBGP Config;
- `static_emulator.py`: Skyseed AS and complete static Emulator Config;
- `yaml.py`: strict versioned YAML reading.

`load_static_emulator_config(path)` returns one `StaticEmulatorConfig`.
`create_static_emulator(config)` sequences public entity and seedemu
create/join calls, returning only the Emulator and both AS collections.
`create_simulator_static_input(...)` is a separate explicit operation.
Handwritten callers bypass the file-driven operations.

## 6. seedemu integration

`seedemu_integration/` is Skyseed-owned code that extends external seedemu:

- `SkyseedAutonomousSystem` owns Skyseed entities and bridges their lifecycle;
- `SatelliteBase` incorporates Satellite nodes into Base render behavior;
- `SkyseedDockerCompiler` handles isolated Satellite output and normalized
  metadata while ordinary nodes retain upstream paths.

The package is not a vendored seedemu copy and not data seeding code.

## 7. Compiler and materialization

`SkyseedDockerCompiler` lowers one rendered Emulator to an existing Compose
project. `ComposeProjectLauncher` consumes that artifact using plugin-based
Compose v2. Materialization is outside entities, static Simulator input and
Compiler model ownership; it never recompiles or rewrites the project.

## 8. Formal examples

- `isolated_satellites`: direct entity construction and compile.
- `complete_static_emulator`: direct entities plus ordinary Network/Router,
  IX, eBGP, Simulator static input and compile.
- `large_static_network`: YAML → Config → Emulator creation → static Simulator
  input generation → compile. This example remains compile/config-only.

The examples are executable architecture records; their calls lead directly
to the owning public methods.

## 9. Deferred boundaries

No current type resolves `SimulationNodeId` to execution resources, computes
positions or visibility, selects links, plans routes, emits BPF changes or runs
a Simulator. These are future design questions, not hidden responsibilities of
the static input or Compiler.
