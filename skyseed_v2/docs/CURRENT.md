# Current Design State

## Current phase

- Static Emulator core excluding UT: **COMPLETE**
- Static Emulator configuration loading: **COMPLETE**
- Examples self-contained and reorganized: **COMPLETE**
- Large static network compile validation: **PASS**
- Compose v2 migration and materialization tooling: **COMPLETE**
- Native Ubuntu 24.04 migration acceptance: **PASS**
- Package structure, naming and Simulator static-input boundary: **IMPLEMENTED**
- Cross-layer NodeId and Emulator node index contract: **IMPLEMENTED**
- Generated example output controls: **IMPLEMENTED**
- Simulator: **NOT STARTED**

## Current objective

Accept the unified Emulator–Simulator–Compiler logical-node identity contract:
static Simulator data and the Compiler-facing Emulator entity index are built
from the same Skyseed entity collection and deterministic `NodeId` rule. This
work does not authorize Simulator algorithms, plans, runtime updates or
resource-resolution implementation.

## Implemented package boundaries

```text
skyseed_v2/
├── entities/                 # Shell, Orbit, Satellite, GroundStation
├── simulator_input/          # immutable future-Simulator static inputs
├── seedemu_integration/      # Skyseed-owned seedemu extensions
├── configuration/            # immutable Config values and YAML loading
├── node_id.py                # neutral cross-layer logical node identity
├── simulator_input_generation.py # entity traversal, input and node index
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
- `simulator_input_generation.create_simulator_static_input()` and
  `create_emulator_node_index()` accept only an iterable of
  `SkyseedAutonomousSystem` values, fail closed on every other value and share
  one private AS → Shell → Orbit → Satellite / AS → GroundStation traversal.
- `SimulatorStaticInput` receives already copied input values, validates their
  types, sorts deterministically, rejects duplicate `NodeId` values
  and builds its read-only lookup index. It does not traverse or recognize AS
  objects.
- The resulting snapshot is frozen and deterministic. It retains no Emulator
  entity, Registry, container, namespace, Compose, persistence or runtime
  Simulator object.
- `NodeId(asn, entity_kind, entity_id)` is defined in the neutral
  `skyseed_v2.node_id` module and deterministically derived only in
  `simulator_input_generation.py`.
- Satellite and GroundStation do not construct, accept or retain `NodeId`;
  the value belongs to the Emulator–Simulator–Compiler cross-layer contract.
- `create_emulator_node_index()` returns a read-only mapping from those same
  IDs to the exact original Satellite/GroundStation instances. It stores no
  Registry, container, service, namespace or interface information.
- `simulator_input` imports only standard-library modules, its own data modules
  and neutral `skyseed_v2.node_id`; `node_id` uses the general identifier
  validator. Neither has an entities, seedemu-integration or upstream seedemu
  dependency.

## Construction paths

- `isolated_satellites`: direct `Emulator → SatelliteBase → AS → Shell → Orbit
  → Satellite → SkyseedDockerCompiler → output controls`; no YAML or
  file-driven construction operations.
- `complete_static_emulator`: direct entity plus ordinary seedemu
  Network/Router/IX/eBGP construction, then
  static input + Emulator node index generation, identity-set validation,
  Compiler and output controls.
- `large_static_network`: YAML → `load_static_emulator_config(path)` →
  `create_static_emulator(config)` →
  static input + Emulator node index generation → identity-set validation →
  Compiler → output controls. Its acceptance baseline remains
  compile/config-only; generated start is an explicit user action.

Every formal Python entrypoint generates `output/start.sh` and
`output/cleanup.sh` after the Compose artifact is complete. Source-level shell
wrappers no longer generate the experiment. The generated start validates the
exact Compose file and runs `up -d --build`; cleanup runs scoped
`down --remove-orphans` and retains the output.

`StaticEmulatorCreationResult` has exactly three fields: the Emulator, the
Skyseed AS tuple and the ordinary AS tuple. Simulator input and the Emulator
node index are deliberately separate cross-layer generation outputs.

## Deferred work

- Simulator time, position, visibility, topology, planning and execution.
- Dynamic GroundStation–Satellite links and service selection.
- Compiler consumption of future plans and lowering from `NodeId` to runtime
  resource operations.
- UT integration and any new entity kind at the static-input boundary.
- Any future split or composition of Emulator and Simulator generator
  entrypoints; no placeholder or filename contract exists yet.

Future plans must reference `NodeId`; a Compiler will look up the real entity
through the current index rather than guess from strings. No plan type,
Compiler update path, epoch, dynamic topology, Runner, runtime resource locator
or Simulator implementation exists.

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
