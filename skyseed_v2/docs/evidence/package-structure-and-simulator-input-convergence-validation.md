# Package Structure and Simulator Static-Input Convergence Validation

## Scope

This record validates the mechanical package, naming and static-input boundary
refactor based on the review snapshot archived under
`docs/reviews/2026-07-18-package-structure/`. Emulator entity relations and
core behavior were not changed. Simulator was not started.

## Commit sequence

| Commit | Boundary |
| --- | --- |
| `fb77214` | archived the pre-change package review |
| `f3a2d6f` | replaced copying services and the entity-catalog boundary with direct immutable Simulator inputs |
| `c5649da` | split Emulator entities into discoverable files under `entities/` |
| `c503f37` | renamed the Skyseed-owned seedemu integration package and files |
| `7c234f5` | split specifications, serialization and application responsibilities |

## Static-input assertions

- Entity modules do not import `simulator_input`.
- `SatelliteSimulationInput.from_satellite()` and
  `GroundStationSimulationInput.from_ground_station()` copy scalars without
  retaining entities.
- `SimulatorStaticInput.from_autonomous_systems()` creates a frozen,
  deterministic snapshot and fails closed on duplicate logical IDs.
- Static inputs contain no Registry, container, namespace, Compose,
  persistence or runtime Simulator reference.

## Automated validation

From `skyseed_v2/`:

```bash
PYTHONPATH=src:. ../.venv/bin/pytest -q
```

Result:

```text
137 passed in 9.65s
```

This includes unit, integration, package-import, example-layout, architecture
boundary and Mermaid/type traceability tests.

## Formal entrypoint validation

Each entrypoint compiled to its own temporary directory, followed by
plugin-based Compose validation:

```text
python -m examples.isolated_satellites.main ...
docker compose ... config --quiet

python -m examples.complete_static_emulator.main ...
docker compose ... config --quiet

python -m examples.large_static_network.main ...
docker compose ... config --quiet
```

Results:

- isolated: two Satellite services compiled; Compose config valid;
- complete: two Satellite inputs, one GroundStation input and three unique
  logical IDs; Compose config valid;
- large: 348 Satellites, 12 GroundStations and 360 static Simulator inputs;
  Compose config valid.

The large example remained compile/config-only. No Compose project was started.
All three temporary validation directories were removed after validation.

## Acceptance boundary

The evidence supports package/import convergence, unchanged static Emulator
behavior and the immutable static-data handoff. It does not validate or imply
Simulator runtime behavior. Simulator remains **NOT STARTED**.
