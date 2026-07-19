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

Preserve the implemented Emulator–Simulator logical-node identity and static
input contracts while the restored future Simulator design baseline awaits
architecture review. The restored baseline is conceptual/open: it authorizes
no Simulator algorithm, Topology, Planner, dynamic compiler, Runner, backend,
public export or runtime implementation.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator/                # Simulator-owned contracts; no engine
│   └── static_input/         # immutable Simulator static input types
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

- `SkyseedDockerCompiler` is the implemented seedemu integration that lowers a
  rendered Emulator/SEED static model to Dockerfiles, Compose services and
  networks.
- The approved dynamic direction distinguishes backend-neutral semantic
  planning, backend-specific execution planning, executable-form lowering and
  execution. `ExecutionCompiler` is only the current conceptual diagram label
  for the lowering step; no such type, module, interface or implementation
  exists and its final public name is open.
- The static compiler and conceptual dynamic compiler have different inputs,
  outputs, ownership and lifecycles. They do not inherit from one another and
  have no required shared public parent.
- `abstract Simulation Plan → SimulationPlanCompiler → concrete dynamic
  operations` is **retired/corrected** as an over-compressed description. It
  mixed semantic planning, backend planning and executable-form compilation;
  `SimulationPlanCompiler` is not a frozen future public contract.

## Simulator future design baseline

The following are **approved directions**, not implementations:

- the implemented static Emulator model feeds both
  `simulator_input_generation → SimulatorStaticInput → future Simulator` and
  the independent `SkyseedDockerCompiler → Docker/Compose →
  ComposeProjectLauncher` path;
- a future `Simulator` coordinates Zone/Timeline, Topology, Planning, dynamic
  compilation, Runner and Observation responsibilities instead of absorbing
  every algorithm;
- Topology is a coordination shell, Zone remains independent from it, and
  candidate discovery differs from connection selection;
- semantic network planning precedes backend-specific execution planning;
- an execution compiler only lowers an approved backend plan and a Runner only
  executes the resulting bundle;
- `ComposeProjectLauncher` is not the dynamic Runner.

`Simulator`, `SimulationTimeline`, `ZoneModel`, `Topology`, the position,
connection, routing, slice/difference/store objects, both Planner levels,
backend-specific planners/plans, `ExecutionCompiler`, `ExecutableBundle`,
`Runner` and `Observation` are **conceptual candidates**. None is implemented
or a frozen public name.

Public visibility, final naming, time/slice semantics, state retention,
position and connection decomposition, supported backends, execution-bundle
shape, Runner/materialization relationship and Observation ownership remain
**open questions**. The full baseline is
[`design/simulator-conceptual-design.md`](design/simulator-conceptual-design.md),
and the restored Mermaid maps are in
[`architecture/conceptual-class-diagrams.md`](architecture/conceptual-class-diagrams.md#simulator-future-conceptual-map).

## Deferred work

- Simulator time, position, visibility, topology, planning and execution.
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
dynamic topology, Runner, runtime resource locator or Simulator engine exists.

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
