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
- Simulator Iteration 1 composition, time and immutable static Topology:
  **ACCEPTED / FROZEN**
- Simulator Iteration 2 static Zone domain: **ACCEPTED / FROZEN**
- Satellite position, service-Satellite selection, dynamic topology, Planning
  and dynamic execution: **NOT STARTED**

## Current objective

Simulator Iteration 1 is accepted and frozen. Iteration 2 implements the static
Zone Definition/YAML path, atomic Topology materialization, ZoneLayout
ownership, rectangle geometry, positive-area overlap validation, replaceable
membership algorithm contract, deterministic default Model and Topology
facade. The independent architecture audit, canonical example, tests,
documentation and class diagrams passed final user review; Iteration 2 is now
accepted and frozen. No later iteration has started or been named.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator/                # Simulator composition, time, static Topology and Zones
│   └── static_input/         # immutable cross-layer boundary input types
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── configuration/            # immutable Config/Zone Definitions and YAML loading
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
  and builds its read-only lookup index. Its node queries accept only complete
  `NodeId` values. It does not traverse or recognize AS objects, scan local
  string IDs or provide a string-ID fallback.
- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput` are implemented in the receiving-side
  `skyseed_v2.simulator.static_input` package. They are immutable cross-layer
  construction data, not the complete or long-lived Simu domain model.
- The resulting input is frozen and deterministic. It retains no Emulator
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
- Any future Emulator-side dynamic-execution lookup will call existing owners
  on demand:
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
- `complete_static_emulator/emu.py`: shared `build_emulator()` helper containing
  direct entity plus ordinary seedemu Network/Router/IX/eBGP construction and
  returning only the upstream `Emulator`.
- `complete_static_emulator/main.py`: canonical full-output entrypoint; it
  builds one Emulator, creates one Simulator/Topology from that same object,
  renders and compiles with `SkyseedDockerCompiler`, writes output controls,
  then atomically publishes the repository-local offline Topology and Zone
  tool snapshot.
- `complete_static_emulator/simu.py`: external assembly entrypoint; it reuses
  `emu.py`, queries the `SatelliteBase` owner for all Skyseed AS values, calls
  `create_simulator_static_input(...)`, creates the Timeline and Definition,
  loads `zones.yaml` directly as `ZoneLayoutDefinition`, then atomically
  materializes the initial static Simulator Topology and its ZoneLayout.
- `large_static_network`: YAML → `load_static_emulator_config(path)` →
  `create_static_emulator(config)` →
  `Emulator.getLayer("Base")` →
  `SatelliteBase.get_skyseed_autonomous_systems()` →
  `create_simulator_static_input(skyseed_autonomous_systems)` →
  `SkyseedDockerCompiler` → output controls. Its acceptance baseline remains
  compile/config-only; generated start is an explicit user action.

Every formal example supports direct-file execution from its own directory
with the caller's selected interpreter and never searches for or restarts
through another environment. Local development uses the canonical
`skyseed_dev` Conda environment documented in the top-level README.
Compilation entrypoints replace their owned default output unless
`--no-override` is requested. Every formal compilation
entrypoint generates `output/start.sh` and `output/cleanup.sh` after the
Compose artifact is complete. Source-level shell wrappers no longer generate
the experiment. The generated start validates the exact Compose file and runs
`up -d --build`; cleanup runs scoped `down --remove-orphans` and retains the
output.

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

## Simulator Iteration 1 accepted/frozen boundary

The following contracts are implemented:

- `SimulationDefinition(static_input, timeline)` is the complete input to
  `Simulator`; it retains the exact immutable values.
- `SimulationTimeline` validates explicit time rules and lazily yields bounded
  or unbounded timestamps. It does not read current time, cache timestamps or
  create topology state.
- `Simulator` is the current composition root and facade. It consumes only the
  Definition, exposes the Definition and Timeline, and creates exactly one
  Topology on explicit request. Creation is atomic: a failed materialization
  leaves the Simulator without a Topology.
- `Topology` owns immutable Simulator-side `SkyseedAutonomousSystem`, `Shell`,
  `Orbit`, `Satellite` and `GroundStation` objects. The tree is
  `Topology → AS → Shell → Orbit → Satellite`, with GroundStations owned by the
  AS. All public tuple enumeration and read-only indexes reference the same
  instances.
- Topology's static-node side keeps its AS tuple, ASN index and unified `NodeId`
  index. Node queries use the unified index with explicit type checks; Shell
  and Orbit facade queries delegate along their owner chain. No Satellite-to-
  parent reverse API or duplicate typed/global Shell/Orbit index is present.
- The Simulator core imports neither Emulator entities, upstream seedemu nor
  `simulator_input_generation.py`. The external adapter is the only component
  that knows both sides.

This Iteration 1 boundary is accepted and frozen; it is not reopened by the
Zone increment.

## Simulator Iteration 2 Zone state

Status: **ACCEPTED / FROZEN** after implementation, architecture audit PASS,
documentation sync and final user acceptance.

The implemented creation chain is:

```text
zones.yaml / programmatic construction
→ ZoneLayoutDefinition
→ Simulator.create_topology(...)
→ Topology-owned ZoneLayout
→ Zone[]
→ fixed ZoneMembershipModel
→ Topology facade
```

The YAML prefix is `raw YAML mapping/list →
LatitudeLongitudeRectangleDefinition → ZoneDefinition →
ZoneLayoutDefinition`. The loader returns that Definition directly; there is
no file-specific Zone type. Definition values are materialization input and
are not retained by Topology.

Completed in Iteration 2:

- Definition types and strict YAML loading;
- atomic one-shot Topology materialization;
- optional Topology-owned ZoneLayout and sole tuple/ID-index ownership;
- canonical `LatitudeLongitudeRectangle` geometry, including date-line and
  pole behavior;
- positive-area overlap validation that permits zero-area shared boundaries;
- public `ZoneMembershipModel` Protocol and stateless deterministic
  `DefaultZoneMembershipModel`;
- delegating Topology Zone facade;
- canonical complete-static-emulator Zone example;
- focused Zone tests, full tests and independent focused architecture audit
  **PASS**.

The default Model distinguishes all physical matches from one authoritative
Zone, resolving by maximum priority, minimum haversine central angle to the
representative center and lexicographic `zone_id` within `1e-12` radians.

Non-blocking notes: a Protocol cannot prohibit internal mutable state in a
third-party Model, so determinism and no previous/service-Satellite state are
extension behavior contracts. Zone YAML currently reports failures with
`StaticEmulatorConfigLoadError`; the name is configuration-boundary debt, and
this iteration adds no exception. YAML and programmatic construction enforce
the same Definition failures, while YAML wraps them as the loader exception.

Planning, Slice, differences, Satellite position and dynamic execution remain
future conceptual design space, not Iteration 2 implementation. The full record
is
[`design/simulator-conceptual-design.md`](design/simulator-conceptual-design.md),
and the Mermaid map is in
[`architecture/conceptual-class-diagrams.md`](architecture/conceptual-class-diagrams.md#simulator-future-conceptual-boundary-map).

## Explicitly unstarted work

- Satellite position and visibility.
- Service-Satellite selection and dynamic GroundStation–Satellite links.
- Slicer, Connection, Route and TopologySlice.
- Planning and backend/runtime execution.
- Future dynamic planning/execution consumption of `NodeId` and lowering to
  runtime resource operations.
- UT integration and any new entity kind at the static-input boundary.
- Any future composition beyond the explicit external assembly demonstrated by
  `complete_static_emulator/simu.py`.

Future plans must reference `NodeId`; the later approved execution boundary
must resolve its ASN through the Base owner and call the resulting Skyseed
AS/entity owners' narrow query methods rather than guess from strings or retain
a global index. No plan type, plan-compilation resolution/update path, epoch,
dynamic topology, `SimulationRunner`, runtime resource locator or Simulator
dynamic-execution engine exists. The next iteration awaits a user/GPT design
decision and is not named or started here.

## Acceptance state

- Satellite slice: completed
- GroundStation slice: completed
- Unified AS and logical identity: completed
- Simulator static input contract: implemented
- Static Emulator configuration construction: completed
- Examples and public import paths: implemented
- Native Ubuntu 24.04 migration baseline: validated
- Simulator Iteration 1 composition/time/static Topology: **ACCEPTED / FROZEN**
- Simulator Iteration 2 Zone: **ACCEPTED / FROZEN**
- Satellite position, service-Satellite selection, Slicer, Connection, Route,
  TopologySlice, Planning and backend/runtime execution: **NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
