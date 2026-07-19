# Simulator Slice 1 Convergence Validation

## Scope

This evidence records the restricted convergence correction based on baseline
`d2fa76c` on branch `feature/skyseed-v2`. The correction restores the approved
Simulator input names, separates the complete example's construction and
entrypoint responsibilities, returns Topology queries to their owners, removes
speculative reverse APIs/indexes and keeps node lookup qualified by `NodeId`.

No position, connection, Zone, route, Planning, dynamic compiler, Runner or
runtime-resource path was added. No container build or Compose up was run.

## Architecture Delta

- The public boundary aggregate is `SimulatorStaticInput`; the only aggregate
  adapter is `create_simulator_static_input(...)`.
- `SimulationDefinition` has `static_input: SimulatorStaticInput` and
  `timeline: SimulationTimeline`. It has no `emulator_input` compatibility
  keyword or property.
- `EmulatorStaticInput` and `create_emulator_static_input(...)` do not exist as
  aliases or exports.
- `examples/complete_static_emulator/emu.py` defines only
  `build_emulator() -> Emulator`; it returns the upstream Emulator and neither
  adapts input nor renders/compiles.
- `main.py` reuses `emu.py` as the pure Emulator render/compile entrypoint.
- `simu.py` separately reuses `emu.py`, queries the `SatelliteBase` owner,
  performs Emulator-to-Simulator adaptation, creates Definition/Simulator and
  atomically materializes the initial static Topology. It does not import
  `main.py` or describe the result as a Slice.
- Bounded Timeline output may be consumed completely. Unbounded example output
  uses `islice` to display exactly four timestamps without changing the lazy
  Timeline contract.
- Topology retains only `_autonomous_systems`,
  `_autonomous_systems_by_asn` and `_nodes_by_id`.
- `_satellites_by_id`, `_ground_stations_by_id`, `_shells_by_key`,
  `_orbits_by_key`, `_shells_by_satellite_id` and
  `_orbits_by_satellite_id` are absent.
- `get_satellite()` and `get_ground_station()` query `_nodes_by_id` and then
  fail closed on a mismatched object type.
- `get_shell()` delegates through `get_autonomous_system(...).get_shell(...)`;
  `get_orbit()` then delegates through `Shell.get_orbit(...)`.
- `get_shell_for_satellite()` and `get_orbit_for_satellite()` are absent.
- `SimulatorStaticInput.get()`, `get_satellite()` and
  `get_ground_station()` accept only complete `NodeId`; there is no
  `entity_id` scan, ambiguity fallback or string lookup.
- Simulator core imports neither Emulator entities, seedemu integration,
  upstream seedemu nor the external generation adapter. Root package exports
  remain the six existing Emulator entity/value names; the nine Simulator
  names are public only from `skyseed_v2.simulator`.
- `SimulationTimeline` remains frozen and lazy, validates a positive interval
  and matching timezone awareness, and supports bounded/unbounded iteration.
- `Simulator.create_topology()` remains explicit, single-use and atomic; a
  failed materialization does not publish a partial Topology.
- The static object tree remains frozen. Public enumeration is tuple-based;
  each owner's private lookup and Topology's two private lookups are
  `MappingProxyType` values referencing the same instances as the tuples.

## Automated contract evidence

Commands were run from `skyseed_v2/` with the repository virtual environment.

```text
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m compileall -q \
  src/skyseed_v2 examples tests
result: PASS
```

Focused Simulator, architecture, import and example suite:

```text
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest \
  -p no:cacheprovider -q \
  tests/test_package_import.py \
  tests/unit/test_simulator_static_input.py \
  tests/unit/test_simulator_static_input_construction.py \
  tests/unit/test_simulation_timeline.py \
  tests/unit/test_simulator_topology.py \
  tests/unit/test_architecture_boundaries.py \
  tests/unit/test_architecture_traceability.py \
  tests/integration/test_examples_layout.py \
  tests/integration/test_complete_static_emulator.py \
  tests/integration/test_construction_path_equivalence.py
result: 79 passed in 1.79s
```

Complete unexcluded suite:

```text
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -m pytest \
  -p no:cacheprovider -q
result: 178 passed in 14.23s
```

The focused tests explicitly prove old names are absent, Definition uses
`static_input`, Simulator core dependencies stay one-way, both complete
entrypoints reuse `emu.py`, the AS collection comes from `SatelliteBase`,
unbounded display is finite, Topology has only three aggregate fields, owner
delegation occurs, reverse APIs are absent, NodeId-only input lookup fails
closed, atomic creation survives failure, all private lookup maps are immutable
and tuple/map paths preserve instance identity.

## Canonical example evidence

```text
PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python \
  -m examples.complete_static_emulator.simu

created Simulator static topology: AS65001/shell-1/orbit-1/satellite-0, gs-new-york
timeline timestamps: 2026-01-01T00:00:00+00:00, 2026-01-01T00:00:10+00:00, 2026-01-01T00:00:20+00:00, 2026-01-01T00:00:30+00:00
```

## Packaging and documentation evidence

```text
../.venv/bin/python -m pip wheel . --no-deps --no-build-isolation
result: PASS
wheel: skyseed_v2-0.1.0-py3-none-any.whl
size: 60,216 bytes
SHA-256: 48c9c62cf77947494764fda996568e8dd04237a9971263d0c77d9f9615303e49
```

With site packages disabled, the wheel directly imported
`SimulationDefinition`, `SimulationTimeline`, `Simulator`, `Topology` and
`SimulatorStaticInput`; the root package did not expose `Simulator`. Archive
inspection also confirmed `simulator_input_generation.py` is packaged while
remaining outside Simulator core.

```text
git diff --check
result: PASS

runtime old-name/API scan over src and examples Python
result: PASS; no old input names, removed indexes or reverse APIs

Simulator core import/dependency scan
result: PASS; no Emulator/SEED/generation-adapter import

Markdown local-link check
result: PASS; 16 local links checked

Mermaid render check
result: PASS; all 25 Mermaid blocks rendered to SVG
```

The Mermaid check covered all six Markdown files containing diagrams. Current
architecture, design, API, terminology, CURRENT, example README and governance
records were synchronized to the same implementation boundary.
