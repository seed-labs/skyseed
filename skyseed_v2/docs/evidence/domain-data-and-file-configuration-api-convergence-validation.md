# Domain Data and File-Configuration API Convergence Validation

## Scope

This record validates the bounded ownership and API convergence that keeps the
Emulator entity graph unchanged while making three boundaries explicit:

- Orbit owns common orbital parameters; Satellite owns per-Satellite data;
- versioned YAML loads immutable Config values through a direct function;
- Config-to-Emulator creation and Emulator-to-Simulator-input generation are
  separate direct operations.

Simulator remained **NOT STARTED**.

## Ownership and dependency assertions

- `OrbitParameters` and `Orbit` are defined in `entities/orbit.py`.
- `Satellite` has no `orbit_parameters` constructor input, property or private
  field, and it retains no Orbit reference.
- `simulator_input` contains only immutable data and neutral identifier
  validation; it imports neither entities nor seedemu.
- `create_satellite_simulation_input(satellite, orbit_parameters)` explicitly
  combines entity-owned and Orbit-owned values.
- `create_simulator_static_input(...)` accepts only Skyseed ASes and traverses
  AS → Shell → Orbit → Satellite. The input aggregate owns sorting, duplicate
  rejection and read-only indexing.
- `create_static_emulator(config)` does not import or create Simulator input.
- `StaticEmulatorCreationResult` has exactly three fields: Emulator, Skyseed AS
  tuple and ordinary AS tuple.
- No compatibility aliases exist for the removed class-based file operations,
  `*Specification` types or retired packages.

## Public file-driven call chain

```python
config = load_static_emulator_config(path)
creation = create_static_emulator(config)
simulator_input = create_simulator_static_input(
    creation.skyseed_autonomous_systems
)
```

The isolated and complete examples remain handwritten. The large example
shows all three calls directly.

## Automated validation

From `skyseed_v2/`:

```text
python -m compileall -q src/skyseed_v2 examples tests
focused architecture/API/config/entity/example tests: 74 passed in 12.26s
full test suite: 142 passed in 13.56s
```

A wheel was built with `pip wheel . --no-deps`. Its SHA-256 was
`98febed6201ee6c503079b114e920634b7437e0061c1d4e545e69c92d61f69df`.
Archive inspection confirmed the new modules are present and
`application/`, `serialization/`, `specifications/` and
`entities/orbit_parameters.py` are absent. A package-root and
seedemu-independent `simulator_input` import smoke test passed from the wheel.

## Formal entrypoint validation

Each entrypoint compiled into its own temporary directory. Every generated
Compose file passed plugin-based `docker compose config --quiet`:

- isolated: two isolated Satellite services compiled;
- complete: two Satellite inputs, one GroundStation input and three unique
  logical IDs compiled;
- large: 348 Satellites, 12 GroundStations and 360 static Simulator inputs
  compiled; `docker compose config --services` reported 365 services.

No image build, Compose up or container start was performed. The large path
remained compile/config-only.

## Acceptance boundary

This evidence supports unchanged entity relationships and Emulator behavior,
clarified ownership, direct file-configuration APIs, pure Simulator input data
and distributable-package correctness. It does not validate or imply runtime
Simulator behavior. Simulator remains **NOT STARTED**.
