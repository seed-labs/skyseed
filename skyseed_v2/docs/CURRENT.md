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

Review and accept the package/import restructuring and the three traceable
example call paths. This work does not authorize Simulator development.

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

The root package exports only the six high-frequency entity/value types and
the four stable Simulator-input types. Integration, specification,
serialization, application and materialization types remain public from their
owning packages.

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

- `SatelliteSimulationInput.from_satellite()` copies approved static scalar
  values from one Satellite.
- `GroundStationSimulationInput.from_ground_station()` copies approved static
  scalar values from one GroundStation.
- `SimulatorStaticInput.from_autonomous_systems()` collects supported inputs
  from one or more Skyseed AS objects, ignores ordinary seedemu AS objects and
  fails closed on duplicate `SimulationNodeId` values.
- The resulting snapshot is frozen and deterministic. It retains no Emulator
  entity, Registry, container, namespace, Compose, persistence or runtime
  Simulator object.
- The entities do not construct or retain `SimulationNodeId`; the input layer
  owns that boundary identity.

## Construction paths

- `isolated_satellites`: direct `Emulator → SatelliteBase → AS → Shell → Orbit
  → Satellite → SkyseedDockerCompiler`; no YAML, Loader or Applier.
- `complete_static_emulator`: direct entity plus ordinary seedemu
  Network/Router/IX/eBGP construction, then
  `SimulatorStaticInput.from_autonomous_systems()` and Compiler.
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
