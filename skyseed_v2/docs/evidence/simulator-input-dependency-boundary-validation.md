# Simulator Static-Input Dependency Boundary Validation

## Scope

Validation date: 2026-07-18.

This record covers the dependency-boundary correction based on
`b726169fc5471bd4d90ed6921363856ec65f83a6`. It moves Emulator entity reading
from immutable input types to direct application functions. It does not alter
entity ownership, specifications, Loader, Compiler, Compose behavior or
Simulator runtime behavior. No container command was run.

## Boundary assertions

- `SatelliteSimulationInput`, `GroundStationSimulationInput` and
  `SimulatorStaticInput` have no entity-reading classmethod.
- `simulator_input` imports no concrete entity, `seedemu_integration` or
  upstream `seedemu` module.
- A subprocess gate blocks every `seedemu` import and still imports
  `skyseed_v2.simulator_input` successfully. Root entity exports are lazy so
  parent-package initialization does not invalidate that result.
- Stable identifier validation has one neutral owner:
  `skyseed_v2.identifier.validate_identifier`.
- `application.create_satellite_simulation_input()` and
  `application.create_ground_station_simulation_input()` copy the previously
  approved scalar fields without retaining their source entities.
- `application.create_simulator_static_input()` accepts only an iterable of
  `SkyseedAutonomousSystem`, fails closed on ordinary SEED AS or unknown
  values, traverses owned entities and delegates per-entity conversion.
- `SimulatorStaticInput` retains data-only responsibilities: input type
  validation, deterministic sorting, duplicate logical-ID rejection,
  immutable snapshotting and read-only indexing.
- `StaticEmulatorApplyResult` retains exactly `emulator`,
  `skyseed_autonomous_systems`, `ordinary_autonomous_systems` and
  `simulator_input`.
- The root package exports only `Shell`, `Orbit`, `OrbitParameters`,
  `Satellite`, `GroundStation` and `GroundStationLocation`. Static input types
  remain public from `skyseed_v2.simulator_input`.

## Automated validation

From `skyseed_v2/`:

```bash
PYTHONPATH=src:. ../.venv/bin/python -m compileall -q src tests examples

PYTHONPATH=src:. ../.venv/bin/pytest -q \
  tests/test_package_import.py \
  tests/unit/test_architecture_boundaries.py \
  tests/unit/test_architecture_traceability.py \
  tests/unit/test_simulation_node_identity.py \
  tests/unit/test_satellite_simulation_input.py \
  tests/unit/test_simulator_static_input.py \
  tests/unit/test_simulator_static_input_construction.py \
  tests/unit/test_static_scenario_application.py \
  tests/integration/test_construction_path_equivalence.py \
  tests/integration/test_examples_layout.py

PYTHONPATH=src:. ../.venv/bin/pytest -q

../.venv/bin/python -m pip wheel . --no-deps --no-build-isolation \
  --wheel-dir <temporary-directory>
```

Results:

```text
compileall: PASS
focused tests: 67 passed in 0.78s
full tests: 138 passed in 9.77s
wheel: skyseed_v2-0.1.0-py3-none-any.whl (52,174 bytes)
wheel sha256: 2f8c72e8de813cc4a6a2febfedceb489a34235585d087c6577f5eca7479162ff
```

The temporary wheel directory was removed after checksum capture.

## Acceptance boundary

The evidence proves dependency direction, public imports, conversion field
semantics, fail-closed AS handling, unchanged ApplyResult shape and equivalent
handwritten/YAML static input. It does not claim container execution or any
Simulator implementation. Simulator remains **NOT STARTED**.
