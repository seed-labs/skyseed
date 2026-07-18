# Current Design State

## Current phase

- Static Emulator core excluding UT: **COMPLETE**
- Static Emulator configuration loading: **COMPLETE**
- Examples self-contained and reorganized: **COMPLETE**
- Large static network compile validation: **PASS**
- Compose v2 migration and materialization tooling: **COMPLETE**
- Native Ubuntu 24.04 migration acceptance: **PASS**
- Package structure, naming and Simulator static-input boundary: **IMPLEMENTED**
- Pure cross-layer NodeId and owner-directed lookup contract: **IMPLEMENTED**
- Generated example output controls: **IMPLEMENTED**
- Simulator static input contract: **AVAILABLE**
- Simulator algorithms and engine: **NOT STARTED**

## Current objective

Accept the unified Emulator–Simulator–future `SimulationPlanCompiler`
logical-node identity contract: static Simulator data contains
deterministically derived `NodeId` values, while future plan compilation lookup
follows existing owner APIs on demand and never creates a global
NodeId-to-entity/resource index. This work does not authorize Simulator
algorithms, plans, runtime updates or `SimulationPlanCompiler` implementation.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator/                # Simulator-owned contracts; no engine
│   └── static_input/         # immutable Simulator static input types
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── configuration/            # immutable Config values and YAML loading
├── node_id.py                # neutral cross-layer logical node identity
├── simulator_input_generation.py # entity traversal and pure input generation
├── static_emulator_creation.py # Config-to-Emulator creation operation
└── materialization/          # Compose project launch tooling
```

The root package exports only the six high-frequency entity/value types.
Simulator-input, integration, configuration, creation, generation and
materialization APIs remain public from their owning modules. Root entity
exports are resolved lazily so importing `skyseed_v2.simulator.static_input`
does not load SEED-backed entities through package initialization.

## Confirmed entity and ownership model

```text
SkyseedAutonomousSystem
├── Shell
│   └── Orbit
│       └── Satellite
└── GroundStation
```

- `Satellite` is the same SEED Node instance created by Orbit, registered by
  its AS and handled by `SkyseedDockerCompiler`.
- `OrbitParameters` is defined beside `Orbit` in `entities/orbit.py`; each
  Orbit owns exactly one value. Satellite has no Orbit reference and no copy
  of altitude, inclination or right ascension.
- `GroundStation` is the same SEED Router instance created by its AS,
  registered as `rnode`/border router and handled by the inherited upstream
  Router Docker-compilation path.
- Entity modules do not import `simulator.static_input`.
- No new entity or ownership level was introduced by the package split.

## Emulator → Simulator static-input boundary

- `simulator_input_generation.create_satellite_simulation_input()` receives a
  Satellite and its owning Orbit's parameters, then combines their approved
  static scalar values.
- `simulator_input_generation.create_ground_station_simulation_input()` copies
  approved scalar values from one GroundStation.
- `simulator_input_generation.create_simulator_static_input()` accepts only an
  iterable of `SkyseedAutonomousSystem` values, fails closed on every other
  value and uses one private AS → Shell → Orbit → Satellite / AS →
  GroundStation traversal.
- `SimulatorStaticInput` receives already copied input values, validates their
  types, sorts deterministically, rejects duplicate `NodeId` values
  and builds its read-only lookup index. It does not traverse or recognize AS
  objects.
- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput` are owned semantically and structurally by
  `skyseed_v2.simulator.static_input`. This available contract does not imply a
  Simulator engine.
- The resulting snapshot is frozen and deterministic. It retains no Emulator
  entity, Registry, container, namespace, Compose, persistence or runtime
  Simulator object.
- `NodeId(asn, entity_kind, entity_id)` is defined in the neutral
  `skyseed_v2.node_id` module and deterministically derived only in
  `simulator_input_generation.py`.
- Satellite and GroundStation do not construct, accept or retain `NodeId`;
  the value belongs to the Emulator–Simulator–future
  `SimulationPlanCompiler` cross-layer contract.
- No public or global `NodeId → entity` mapping exists. `NodeId` has no resolve,
  entity, service, container, namespace or interface access capability.
- Future `SimulationPlanCompiler` lookup will call existing owners on demand:
  `SatelliteBase.getAutonomousSystem(asn)` →
  `SkyseedAutonomousSystem.get_satellite()` / `get_ground_station()` and, when
  hierarchy context is needed, `get_shell()` → `Shell.get_orbit()` →
  `Orbit.get_satellite()`.
- `simulator.static_input` imports only standard-library modules, its own data
  modules and neutral `skyseed_v2.node_id`; `node_id` uses the general
  identifier validator. Neither has an entities, seedemu-integration or
  upstream seedemu dependency. The external `simulator_input_generation`
  adapter alone understands both Emulator entities and Simulator input types.

## Construction paths

- `isolated_satellites`: direct `Emulator → SatelliteBase → AS → Shell → Orbit
  → Satellite → SkyseedDockerCompiler → output controls`; no YAML or
  file-driven construction operations.
- `complete_static_emulator`: direct entity plus ordinary seedemu
  Network/Router/IX/eBGP construction, then
  `create_simulator_static_input((skyseed_as,))`, `SkyseedDockerCompiler` and
  output controls.
- `large_static_network`: YAML → `load_static_emulator_config(path)` →
  `create_static_emulator(config)` →
  `create_simulator_static_input(creation.skyseed_autonomous_systems)` →
  `SkyseedDockerCompiler` → output controls. Its acceptance baseline remains
  compile/config-only; generated start is an explicit user action.

Every formal Python entrypoint generates `output/start.sh` and
`output/cleanup.sh` after the Compose artifact is complete. Source-level shell
wrappers no longer generate the experiment. The generated start validates the
exact Compose file and runs `up -d --build`; cleanup runs scoped
`down --remove-orphans` and retains the output.

`StaticEmulatorCreationResult` has exactly three fields: the Emulator, the
Skyseed AS tuple and the ordinary AS tuple. Simulator input is deliberately a
separate cross-layer generation output.

## Distinct compilation stages

- `SkyseedDockerCompiler` is the implemented seedemu integration that lowers a
  rendered Emulator/SEED static model to Dockerfiles, Compose services and
  networks.
- `SimulationPlanCompiler` is the reserved name for a future, independent stage
  that would lower an abstract Simulation Plan to concrete dynamic network
  update operations. No such type, module, interface or implementation exists.
- The two stages have different inputs, outputs, ownership and lifecycles. They
  do not inherit from one another and have no shared public parent.

## Deferred work

- Simulator time, position, visibility, topology, planning and execution.
- Dynamic GroundStation–Satellite links and service selection.
- Future `SimulationPlanCompiler` consumption of plans and lowering from
  `NodeId` to runtime resource operations.
- UT integration and any new entity kind at the static-input boundary.
- Any future split or composition of Emulator and Simulator generator
  entrypoints; no placeholder or filename contract exists yet.

Future plans must reference `NodeId`; `SimulationPlanCompiler` will resolve its
ASN through the Base owner and call the resulting Skyseed AS/entity owners'
narrow query methods rather than guess from strings or retain a global index.
No plan type, plan-compilation resolution/update path, epoch, dynamic topology,
Runner, runtime resource locator or Simulator engine exists.

## Acceptance state

- Satellite slice: completed
- GroundStation slice: completed
- Unified AS and logical identity: completed
- Simulator static input contract: available
- Static Emulator configuration construction: completed
- Examples and public import paths: implemented
- Native Ubuntu 24.04 migration baseline: validated
- Simulator algorithms and engine: **NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
