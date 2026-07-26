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
- Dynamic Slice public contract for Position, Connection, Route, Slicer,
  TopologySlice and TopologyDifference: **ACCEPTED / FROZEN DESIGN**
- Dynamic Slice main-chain A–E: **IMPLEMENTED AND ACCEPTED**
- TopologyDifference: **IMPLEMENTED AND ACCEPTED**
- G Dynamic Tool and final synchronization:
  **IMPLEMENTATION CANDIDATE UNDER FINAL REVIEW**
- Planning and backend/runtime execution: **DEFERRED / NOT STARTED**

## Current objective

Simulator Iterations 1 and 2 remain implemented and accepted. The complete
Dynamic Slice public contract is now accepted and frozen in
[`architecture/dynamic-slice-public-contract.md`](architecture/dynamic-slice-public-contract.md).
Its objects, ownership, Model Protocols, built-in Model boundaries, Slice
lifecycle, Difference semantics and implementation dependency order A through
G are approved. The Dynamic Slice main chain A–E is implemented and accepted.
F TopologyDifference is implemented and accepted. G now has a richer dynamic
reference example, repository-local read-only Inspection Tool, integration
tests and final audit evidence as an implementation candidate under final
review. Final integration review additionally fixed the accepted built-in
Route boundary so GroundStations are not implicit backbone transit, restored
Timeline iteration as Slicer's sole timestamp sequence, decoupled generated
progress from retained storage and replaced the Zone Tool's private Model
access with one ZoneLayout-owned read-only query. These are under final
integration review with G; they do not add Planning or backend behavior.
Planning and backend execution remain outside the implemented scope. The
complete Dynamic Slice stage is not yet accepted.

The accepted F implementation publicly exposes `TopologyDifference`,
`ConnectionDirection`, `DifferenceOperation`,
`DirectionalConnectionParametersPatch`, `ZoneServiceAssignmentPatch` and
`RoutePatch` from `skyseed_v2.simulator`; package-root exports remain
unchanged.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator/                # static Topology, accepted A–E and F
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
  of altitude, inclination, right ascension or epoch. Orbit-owned UTC
  `epoch_utc` is implemented and accepted and projected through the static
  boundary.
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
- `complete_static_emulator/simu.py`: canonical Dynamic Slice assembly
  entrypoint; it reuses
  `emu.py`, queries the `SatelliteBase` owner for all Skyseed AS values, calls
  `create_simulator_static_input(...)`, creates the Timeline and Definition,
  loads `zones.yaml` directly as `ZoneLayoutDefinition`, then atomically
  materializes the Simulator Topology and ZoneLayout, explicitly creates all
  three built-in Models and the Slicer, then generates and queries two real
  TopologySlices after synthetic Slice 0.
- `dynamic_slice_network/main.py`: comprehensive richer reference; it builds
  one YAML-defined Emulator with Skyseed/ordinary ASes, IX, static
  GroundStation attachments and routing/eBGP, renders and compiles its static
  Docker/Compose project, then projects that same SatelliteBase owner into one
  formal Simulator/Slicer. It publishes twenty real Slices after Slice 0 and
  gives the Slicer's exact Topology to the static Topology and Zone Tools and
  the exact Slicer to the non-authoritative five-view Dynamic Inspection Tool.
  Static containers are not updated from Slice results.
- `dynamic_slice_network/simu.py`: projection-only helper that requires an
  existing Emulator and creates the same accepted A–F Simulator/Slicer chain;
  it neither constructs the Emulator nor generates Tool output.
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
`output/runtime/docker-compose.yml` artifact is complete. Runnable static
artifacts stay under `runtime/`; repository-local read-only Tool bundles, when
present, stay under `inspection/`. Source-level shell wrappers no longer
generate the experiment. The generated start validates the exact runtime
Compose file and runs `up -d --build`; cleanup runs scoped
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

## Static deployment and future backend execution stages

- The implemented static chain is `Emulator / SEED static model →
  SkyseedDockerCompiler → compiled Docker / Compose project →
  ComposeProjectLauncher`.
- The still-conceptual backend execution chain is `BackendExecutionPlan →
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
- `SimulationTimeline` requires aware datetimes, normalizes them to UTC and
  lazily yields bounded or unbounded timestamps. It does not read current
  time, cache timestamps or create topology state.
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
- delegating Topology Zone facade, including SphericalCap intersection;
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

Iteration 2 remains the static Zone foundation. Position, Connection, Route,
Slicer and TopologySlice are implemented and accepted in A–E.
TopologyDifference is implemented and accepted in F; Planning and backend
execution remain deferred.
The detailed accepted contract is
[`architecture/dynamic-slice-public-contract.md`](architecture/dynamic-slice-public-contract.md),
the design record is
[`design/simulator-conceptual-design.md`](design/simulator-conceptual-design.md),
and the Mermaid map is in
[`architecture/conceptual-class-diagrams.md`](architecture/conceptual-class-diagrams.md#simulator-approved-dynamic-slice-boundary-map).

## Dynamic Slice public contract

Status: **ACCEPTED / FROZEN — A–E AND F IMPLEMENTED AND ACCEPTED; G
IMPLEMENTATION CANDIDATE UNDER FINAL REVIEW; COMPLETE STAGE NOT YET
ACCEPTED**.

Accepted ownership:

```text
Simulator
├── exact SimulationDefinition
├── at most one Topology
└── at most one Slicer
    ├── exact Topology
    ├── exact SimulationTimeline
    ├── exact PositionModel
    ├── exact ConnectionModel
    ├── exact RouteModel
    └── published TopologySlice sequence
```

Topology remains the immutable static world. Slicer alone coordinates the
single dynamic path:

```text
Timeline timestamp
→ PositionModel
→ readonly EarthFixedPosition mapping
→ ConnectionModel
→ ConnectionSlice
→ RouteModel
→ RouteSlice
→ atomically published TopologySlice
→ adjacent TopologyDifference
```

The accepted static/time corrections for Slice A are aware-only
UTC-normalized Timeline values, one explicit common Shell configuration
`epoch_utc` copied into every Orbit, Satellite mean anomaly at its parent Orbit
epoch, finite WGS84 GroundStation ellipsoidal height and one WGS84 ECEF
Position/coordinate capability.

The accepted explicit built-in Models are `CircularOrbitPositionModel`,
`GeometricConnectionModel` and `HopCountRouteModel`; none is a hidden default.
Same-Orbit candidates use mean-anomaly order. Adjacent-Orbit candidates use
unique-RAAN circular order and stable cyclic-offset pairing prepared from the
Timeline start Position. Candidate and geometry evidence remain private Model
state, not ConnectionSlice data. SphericalCap intersection only coarsely
filters Zone candidates; the Zone representative point must also satisfy the
configured service elevation using the shared coordinate capability.

Slicer publishes synthetic Slice 0, then continuous real Slice IDs. Its sole
core generation operation is `create_next_slice()`; failures publish nothing
and consume neither timestamp nor Slice ID. Slicer—not TopologySlice—resolves
full paths from exact Topology and a specified retained Slice.
The public contracts are exported only from `skyseed_v2.simulator`; package
root exports are unchanged. The shared coordinate adapter uses Astropy 8.x
behind domain types, disables automatic IERS downloads and fails when a
timestamp is outside bundled Earth-orientation coverage.
`TopologyDifference` is implemented and accepted.
Planning, DifferenceWindow, backend execution and retention policy remain
deferred.

Approved implementation dependency order:

```text
Slice A time/epoch/coordinates/Position
→ Slice B Zone range/Connection values
→ Slice C GeometricConnectionModel
→ Slice D Route
→ Slice E Slicer/TopologySlice
→ Slice F TopologyDifference
→ Slice G Tool/example/final audit
```

The engineering authorization groups are A–E for the complete Dynamic Slice
main chain, F for TopologyDifference and G for Tool/example/final
synchronization. Each group requires explicit authorization; A through E do
not require mechanical authorization pauses between their internal dependency
steps. A–E and F are implemented and accepted. G is an implementation
candidate under final review and is not yet accepted.

## Current completed and accepted engineering work

- A–E Dynamic Slice main chain: UTC Timeline, Orbit epoch,
  GroundStation height, Position, Zone range, Connection, Route, Slicer and
  TopologySlice.
- F TopologyDifference: immutable sparse comparison of retained adjacent
  TopologySlices through the Slicer-owned entry.

## Current implementation candidate under final review

- G adds the separate comprehensive `dynamic_slice_network` reference plus
  the existing static Topology/Zone inspection outputs and repository-local
  five-view Dynamic browser bundle. The static Tools read one exact Topology;
  the Dynamic Tool reads one exact Slicer, retained Slices, formal resolved
  paths and formal adjacent Differences. None is a domain owner or second
  computation path.
- The minimal `complete_static_emulator/simu.py` remains the small canonical
  A–F smoke and is not enlarged into the Tool scenario.
- G automated validation and the external A–G review bundle do not accept the
  complete Dynamic Slice stage. GPT bundle review and explicit user Tool
  acceptance remain required.

### G Architecture Delta

- Public Simulator types, methods, fields, ownership and package-root exports:
  **none**.
- Formal A–F generation, path and Difference semantics: **unchanged**.
- Repository-local additions: one richer `examples/dynamic_slice_network/`
  Emu+Simu reference, one private projector plus five-view frontend, and one
  dependency-free Firefox WebDriver smoke script.
- Tool flow: exact Slicer → retained public Slice reads +
  `resolve_path(...)` + `create_difference(...)` → private JSON/HTML view
  bundle. No Model or private comparator is called.
- Class/data-flow documentation adds only this external read-only projection;
  it does not add a domain node or owner.

## Explicitly unstarted work

- Planning, DifferenceWindow and backend/runtime execution.
- Future dynamic planning/execution consumption of `NodeId` and lowering to
  runtime resource operations.
- UT integration and any new entity kind at the static-input boundary.
- Any future composition beyond the explicit external assembly demonstrated by
  `complete_static_emulator/simu.py`.

Future plans must reference `NodeId`; the later approved execution boundary
must resolve its ASN through the Base owner and call the resulting Skyseed
AS/entity owners' narrow query methods rather than guess from strings or retain
a global index. No plan type, plan-compilation resolution/update path,
`SimulationRunner`, runtime resource locator or backend execution engine
exists beyond the accepted A–E and F implementations.

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
- Dynamic Slice public contract: **ACCEPTED / FROZEN DESIGN**
- Position, Connection, Route, Slicer and TopologySlice:
  **IMPLEMENTED AND ACCEPTED**
- TopologyDifference implementation: **IMPLEMENTED AND ACCEPTED**
- Dynamic Tool and final synchronization:
  **IMPLEMENTATION CANDIDATE UNDER FINAL REVIEW**
- Full Dynamic Slice stage: **NOT YET ACCEPTED AS COMPLETE**
- Planning and backend/runtime execution: **DEFERRED / NOT STARTED**

Current package/API details and the manual review checklist are recorded in
`docs/architecture/emulator-public-construction-api.md`.
