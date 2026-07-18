# Current Design State

## Current phase

- Static Emulator core excluding UT: **COMPLETE**
- Static Emulator configuration loading: **COMPLETE**
- Examples self-contained and reorganized: **COMPLETE**
- Large static network compile validation: **PASS**
- Compose v2 migration and materialization tooling: **COMPLETE**
- Native Ubuntu 24.04 migration acceptance: **PASS**
- Package structure, naming and Simulator static-input boundary: **IMPLEMENTED**
- Simulator: **NOT STARTED**

## Current objective

Accept the clarified domain-data ownership and direct file-configuration API.
Orbit owns shared orbital parameters, Satellite owns only per-Satellite data,
and the file-driven path is three explicit function calls. This work does not
authorize Simulator development.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator_input/          # immutable future-Simulator static inputs
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── configuration/            # immutable Config values and YAML loading
├── simulator_input_generation.py # entity traversal and snapshot creation
├── static_emulator.py        # Config-to-Emulator creation operation
└── materialization/          # Compose project launch tooling
```

The root package exports only the six high-frequency entity/value types.
Simulator-input, integration, configuration, creation, generation and
materialization APIs remain public from their owning modules. Root entity
exports are resolved lazily so importing `skyseed_v2.simulator_input` does not
load SEED-backed entities through package initialization.

## Confirmed entity and ownership model

```text
SkyseedAutonomousSystem
├── Shell
│   └── Orbit
│       └── Satellite
└── GroundStation
```

- `Satellite` is the same SEED Node instance created by Orbit, registered by
  its AS and handled by the Compiler.
- `OrbitParameters` is defined beside `Orbit` in `entities/orbit.py`; each
  Orbit owns exactly one value. Satellite has no Orbit reference and no copy
  of altitude, inclination or right ascension.
- `GroundStation` is the same SEED Router instance created by its AS,
  registered as `rnode`/border router and handled by the inherited Router
  Compiler path.
- Entity modules do not import `simulator_input`.
- No new entity or ownership level was introduced by the package split.

## Emulator → Simulator static-input boundary

- `simulator_input_generation.create_satellite_simulation_input()` receives a
  Satellite and its owning Orbit's parameters, then combines their approved
  static scalar values.
- `simulator_input_generation.create_ground_station_simulation_input()` copies
  approved scalar values from one GroundStation.
- `simulator_input_generation.create_simulator_static_input()` accepts only an iterable of
  `SkyseedAutonomousSystem` values, fails closed on every other value and
  traverses the owned entities through the two direct conversion functions.
- `SimulatorStaticInput` receives already copied input values, validates their
  types, sorts deterministically, rejects duplicate `SimulationNodeId` values
  and builds its read-only lookup index. It does not traverse or recognize AS
  objects.
- The resulting snapshot is frozen and deterministic. It retains no Emulator
  entity, Registry, container, namespace, Compose, persistence or runtime
  Simulator object.
- The entities do not construct or retain `SimulationNodeId`; the input layer
  owns that boundary identity.
- `simulator_input` imports only standard-library modules, its own data modules
  and the neutral `skyseed_v2.identifier` validator. It has no entities,
  seedemu-integration or upstream seedemu dependency.

## Construction paths

- `isolated_satellites`: direct `Emulator → SatelliteBase → AS → Shell → Orbit
  → Satellite → SkyseedDockerCompiler`; no YAML or file-driven operations.
- `complete_static_emulator`: direct entity plus ordinary seedemu
  Network/Router/IX/eBGP construction, then
  `create_simulator_static_input((skyseed_as,))` and Compiler.
- `large_static_network`: YAML → `load_static_emulator_config(path)` →
  `create_static_emulator(config)` →
  `create_simulator_static_input(creation.skyseed_autonomous_systems)` →
  Compiler. It remains compile/config-only.

`StaticEmulatorCreationResult` has exactly three fields: the Emulator, the
Skyseed AS tuple and the ordinary AS tuple. Simulator input is deliberately a
separate third-stage result.

## Deferred work

- Simulator time, position, visibility, topology, planning and execution.
- Dynamic GroundStation–Satellite links and service selection.
- Runtime resource resolution from `SimulationNodeId`.
- UT integration and any new entity kind at the static-input boundary.

None of these are implied by the package refactor. No epoch, dynamic topology,
Runner, runtime resource locator or Simulator implementation exists.

## Acceptance state

- Satellite slice: completed
- GroundStation slice: completed
- Unified AS and logical identity: completed
- Simulator static input: implemented
- Static Emulator configuration construction: completed
- Examples and public import paths: implemented
- Native Ubuntu 24.04 migration baseline: validated
- Simulator: **NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
