# Current Design State

## Current phase

- Static Emulator core excluding UT: **COMPLETE**
- Generic static scenario loading: **COMPLETE**
- Examples self-contained and reorganized: **COMPLETE**
- Large static network compile validation: **PASS**
- Compose v2 migration and materialization tooling: **COMPLETE**
- Native Ubuntu 24.04 migration acceptance: **PASS**
- Package structure, naming and Simulator static-input boundary: **IMPLEMENTED**
- Simulator: **NOT STARTED**

## Current objective

Review and accept the seedemu-independent Simulator static-input package,
application-owned conversion functions and the three traceable example call
paths. This work does not authorize Simulator development.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator_input/          # immutable future-Simulator static inputs
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── specifications/           # immutable construction inputs by family
├── serialization/            # versioned YAML loading
├── application/              # specification application sequencing
└── materialization/          # Compose project launch tooling
```

The root package exports only the six high-frequency entity/value types.
Simulator-input, integration, specification, serialization, application and
materialization APIs remain public from their owning packages. Root entity
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
- `GroundStation` is the same SEED Router instance created by its AS,
  registered as `rnode`/border router and handled by the inherited Router
  Compiler path.
- Entity modules do not import `simulator_input`.
- No new entity or ownership level was introduced by the package split.

## Emulator → Simulator static-input boundary

- `application.create_satellite_simulation_input()` and
  `application.create_ground_station_simulation_input()` copy approved static
  scalar values from concrete Emulator entities.
- `application.create_simulator_static_input()` accepts only an iterable of
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
  → Satellite → SkyseedDockerCompiler`; no YAML, Loader or Applier.
- `complete_static_emulator`: direct entity plus ordinary seedemu
  Network/Router/IX/eBGP construction, then
  `application.create_simulator_static_input((skyseed_as,))` and Compiler.
- `large_static_network`: YAML → specifications → application → entities →
  Simulator static input → Compiler. It remains compile/config-only.

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
- Generic static scenario construction: completed
- Examples and public import paths: implemented
- Native Ubuntu 24.04 migration baseline: validated
- Simulator: **NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
