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
- Simulator static input contract: **IMPLEMENTED**
- Simulator algorithms and engine: **NOT STARTED**

## Current objective

After this design synchronization is reviewed, the first Simulator vertical
slice will begin with a fact investigation into how immutable
Emulator-to-Simulator boundary data will materialize Simulator-owned and
Topology-owned domain objects. No investigation or implementation is started
by this record. The Simulator/Timeline/Topology/Planning structure below is an
approved direction or conceptual/open design, not current code.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator/                # receiving-side Simulator contracts; no engine
│   └── static_input/         # immutable cross-layer boundary input types
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── configuration/            # immutable Config/auxiliary values and YAML loading
├── node_id.py                # neutral cross-layer logical node identity
├── simulator_input_generation.py # entity traversal and pure input generation
├── static_emulator.py        # Config-to-upstream-Emulator operation
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
  `SimulatorStaticInput` are implemented in the receiving-side
  `skyseed_v2.simulator.static_input` package. They are immutable cross-layer
  construction data, not the complete or long-lived Simu domain model. This
  implemented contract does not imply a Simulator engine.
- The resulting snapshot is frozen and deterministic. It retains no Emulator
  entity, Registry, container, namespace, Compose, persistence or runtime
  Simulator object.
- `NodeId(asn, entity_kind, entity_id)` is defined in the neutral
  `skyseed_v2.node_id` module and deterministically derived only in
  `simulator_input_generation.py`.
- Satellite and GroundStation do not construct, accept or retain `NodeId`;
  the value belongs to the Emulator–Simulator–future dynamic-execution
  cross-layer contract.
- No public or global `NodeId → entity` mapping exists. `NodeId` has no resolve,
  entity, service, container, namespace or interface access capability.
- Any future dynamic-execution lookup will call existing owners on demand:
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
  `Emulator.getLayer("Base")` →
  `SatelliteBase.get_skyseed_autonomous_systems()` →
  `create_simulator_static_input(skyseed_autonomous_systems)` →
  `SkyseedDockerCompiler` → output controls. Its acceptance baseline remains
  compile/config-only; generated start is an explicit user action.

Every formal Python entrypoint generates `output/start.sh` and
`output/cleanup.sh` after the Compose artifact is complete. Source-level shell
wrappers no longer generate the experiment. The generated start validates the
exact Compose file and runs `up -d --build`; cleanup runs scoped
`down --remove-orphans` and retains the output.

`create_static_emulator(config)` returns the actual upstream
`seedemu.core.Emulator`. It has no creation-result wrapper and no Skyseed
Emulator subtype. `SatelliteBase`, reached through `Emulator.getLayer("Base")`,
is the real AS owner and exposes the narrow read-only
`get_skyseed_autonomous_systems()` query used by static-input generation.

The file-input schema and keys remain v1. `configuration/ground_network.py`
contains only the minimal ordinary SEED ground-network Config needed by the
complete static example; `configuration/attachments.py` separates node-to-
network relations from GroundStation attributes and ground-network objects.
`StaticEmulatorConfig` is defined and validated in the unambiguous
`configuration/static_emulator_config.py`; top-level `static_emulator.py`
remains the Config-to-Emulator operation module.

## Static deployment and conceptual dynamic execution stages

- The implemented static chain is `Emulator / SEED static model →
  SkyseedDockerCompiler → compiled Docker / Compose project →
  ComposeProjectLauncher`.
- The conceptual/open dynamic chain is `BackendExecutionPlan →
  ExecutionCompiler → ExecutableBundle → SimulationRunner`. No type, module,
  interface or implementation in that chain exists, and final public names
  remain open.
- The static and dynamic chains have different inputs, outputs, ownership,
  lifecycles and execution semantics. Their compiler and launcher/executor
  roles do not inherit from one another and have no required shared public
  parents.
- `abstract Simulation Plan → SimulationPlanCompiler → concrete dynamic
  operations` is **retired/corrected** as an over-compressed description. It
  mixed semantic planning, backend planning and executable-form compilation;
  `SimulationPlanCompiler` is not a frozen future public contract.
- `SkyseedDockerCompiler`, `ComposeProjectLauncher`, conceptual
  `ExecutionCompiler` and conceptual `SimulationRunner` are external
  application/infrastructure services, not Emulator or Simulator domain state.

## Simulator future design baseline

The following are **approved directions**, not implementations:

- Emulator entities become immutable boundary data through
  `simulator_input_generation`; Simulator construction uses that data and user
  parameters to materialize its own domain objects.
- `Simulator` is the Simu composition root and facade. It creates and owns
  peer `SimulationTimeline`, `Topology` and `Planning` subsystems.
- `SimulationTimeline` owns time-range intent; Simulator passes explicit
  timestamps to Topology's conceptual single-Slice creation entry.
- `Topology` owns or manages Simu Shell, Orbit, Satellite, GroundStation,
  future Zone definitions, future Slice states and internal indexes. It
  exposes thin query and Slice-comparison facades while internal objects own
  the algorithms.
- Topology produces authoritative state and differences. Planning consumes
  those public results, may use a read-only Topology facade when necessary,
  and does not own, modify or recompute Topology data.
- Semantic planning precedes backend-specific execution planning; only then
  may conceptual dynamic lowering and execution occur.

All mentioned Simulator, Timeline, Topology, Planning, Slice, difference,
Planner, plan, dynamic compiler, bundle and `SimulationRunner` structures are
**conceptual/open**. None is implemented or a frozen public name. Input
renaming, distinct Simu entity types, exact facade/query return types,
time/Slice/retention/continuing-run semantics, internal algorithm objects,
backends and executable-bundle shape remain open questions. The full baseline
is
[`design/simulator-conceptual-design.md`](design/simulator-conceptual-design.md),
and the Mermaid map is in
[`architecture/conceptual-class-diagrams.md`](architecture/conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## Deferred work

- Simulator construction, time, position, visibility, topology, planning and
  execution.
- Dynamic GroundStation–Satellite links and service selection.
- Future dynamic planning/execution consumption of `NodeId` and lowering to
  runtime resource operations.
- UT integration and any new entity kind at the static-input boundary.
- Any future split or composition of Emulator and Simulator generator
  entrypoints; no placeholder or filename contract exists yet.

Future plans must reference `NodeId`; the later approved execution boundary
must resolve its ASN through the Base owner and call the resulting Skyseed
AS/entity owners' narrow query methods rather than guess from strings or retain
a global index. No plan type, plan-compilation resolution/update path, epoch,
dynamic topology, `SimulationRunner`, runtime resource locator or Simulator
engine exists.

## Acceptance state

- Satellite slice: completed
- GroundStation slice: completed
- Unified AS and logical identity: completed
- Simulator static input contract: implemented
- Static Emulator configuration construction: completed
- Examples and public import paths: implemented
- Native Ubuntu 24.04 migration baseline: validated
- Simulator algorithms and engine: **NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
