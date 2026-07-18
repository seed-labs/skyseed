# Candidate Package Layouts

These are proposals for review, not adopted architecture. All keep the current
class names and behavior. Paths show likely defining locations; each proposal
still needs an explicit decision about aggregate `__init__` exports.

## Scheme A — minimal mechanical organization

Keep current architectural directories and root-level layer modules. Split the
three core entity files and make generic SEED integration filenames match their
public classes. Keep `contracts` and `seed` directory names for minimum import
change; only improve leaf names where the current file hides the symbol.

```text
src/skyseed_v2/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── shell.py
│   ├── orbit.py
│   ├── orbit_parameters.py
│   ├── satellite.py
│   ├── ground_station.py
│   └── validation.py
├── contracts/
│   ├── __init__.py
│   ├── identity.py
│   ├── satellite_descriptor.py
│   ├── ground_station_descriptor.py
│   └── catalog.py
├── seed/
│   ├── __init__.py
│   ├── skyseed_autonomous_system.py
│   ├── satellite_base.py
│   ├── skyseed_docker_compiler.py
│   ├── metadata.py
│   └── assets/
├── specifications.py
├── scenario_loader.py
├── scenario_application.py
├── projection.py
├── catalog_builder.py
└── materialization/
```

Effects:

- Root user imports can remain unchanged if the same ten exports are retained.
- `skyseed_v2.domain`, `contracts` and `seed` aggregate imports can remain
  spelled the same.
- Direct defining-module imports change, including the current internal
  `catalog_builder → seed.autonomous_system` edge and tests/docs that name leaf
  paths.
- `specifications.py` and Loader remain large; root-level discoverability is
  only partially improved.
- A separate `orbit_parameters.py` gives an acyclic Shell → Orbit → Satellite
  dependency direction.

## Scheme B — organize for user discoverability and explicit boundaries

Use concrete entity and directional boundary names. Put the hybrid
`SkyseedAutonomousSystem` with entities because users discover Shell/GS
ownership from it; keep lifecycle-only Base/Compiler in SEED integration.

```text
src/skyseed_v2/
├── __init__.py
├── entities/
│   ├── __init__.py
│   ├── skyseed_autonomous_system.py
│   ├── shell.py
│   ├── orbit.py
│   ├── orbit_parameters.py
│   ├── satellite.py
│   ├── ground_station.py
│   └── validation.py
├── simulator_input/
│   ├── __init__.py
│   ├── identity.py
│   ├── satellite_descriptor.py
│   ├── ground_station_descriptor.py
│   └── simulation_entity_catalog.py
├── specifications/
│   ├── __init__.py
│   ├── satellite.py
│   ├── ground_station.py
│   ├── ground_network.py
│   └── static_emulator_scenario.py
├── application/
│   ├── __init__.py
│   ├── static_emulator_specification_applier.py
│   ├── descriptor_projection.py
│   └── simulation_entity_catalog_builder.py
├── serialization/
│   ├── __init__.py
│   └── yaml_static_emulator_scenario_loader.py
├── seedemu_integration/
│   ├── __init__.py
│   ├── satellite_base.py
│   ├── skyseed_docker_compiler.py
│   ├── metadata.py
│   └── assets/
└── materialization/
    ├── __init__.py
    └── compose_project_launcher.py
```

Effects:

- Class-to-file discovery is high, and imports communicate producer/consumer
  direction.
- Moving `SkyseedAutonomousSystem` to `entities` favors ownership discovery but
  makes that directory explicitly framework-aware because AS, Satellite and GS
  all subclass SEED types. Calling it a pure domain package would be inaccurate.
- `simulator_input` is clear about the consumer, but can be mistaken for
  dynamic scenario input; this semantic question remains for review.
- Specifications split into four families. Their `__init__` can provide a
  deliberate aggregate surface, but retaining the exact old module path would
  require a compatibility decision.
- Public paths for every formal example change unless root/subpackage facades
  deliberately preserve some imports.
- This has the cleanest future place for additional approved Simulator-facing
  read models, without starting Simulator itself.

## Scheme C — fewer architecture terms, direct core concepts

Place the approved core types in same-named root modules. Use subpackages only
for the two boundaries whose context is essential. Give the remaining
application/serialization files task-oriented names rather than layer names.

```text
src/skyseed_v2/
├── __init__.py
├── skyseed_autonomous_system.py
├── shell.py
├── orbit.py
├── orbit_parameters.py
├── satellite.py
├── ground_station.py
├── simulator_api/
│   ├── __init__.py
│   ├── identity.py
│   ├── satellite_descriptor.py
│   ├── ground_station_descriptor.py
│   └── simulation_entity_catalog.py
├── scenario_inputs/
│   ├── __init__.py
│   ├── satellite.py
│   ├── ground_station.py
│   ├── ground_network.py
│   └── static_emulator_scenario.py
├── scenario_files.py
├── apply_static_scenario.py
├── descriptor_projection.py
├── simulation_entity_catalog_builder.py
├── seedemu/
│   ├── __init__.py
│   ├── satellite_base.py
│   ├── skyseed_docker_compiler.py
│   ├── metadata.py
│   └── assets/
└── materialization/
```

Effects:

- A reader can usually infer `Shell → shell.py` and
  `SkyseedAutonomousSystem → skyseed_autonomous_system.py` without knowing the
  architecture vocabulary.
- The package root contains many modules and can become visually noisy as the
  domain grows.
- `simulator_api` is concise but may overstate stability/callable API while
  Simulator remains unimplemented.
- `scenario_files.py` is easy to understand but less precise than a dedicated
  serialization package; it could again become a large file.
- Public defining paths change broadly, though the ten root class imports can
  still be retained if approved.
- Future non-core entities have no obvious directory policy unless one is
  added later.

## Comparative matrix

| Criterion | Scheme A: minimal | Scheme B: discoverability/boundaries | Scheme C: concept-direct root |
| --- | --- | --- | --- |
| User discoverability | medium; fixes Shell/Orbit and generic SEED filenames | high; concrete entities plus directional boundaries | highest for current core types; lower once root grows |
| Import readability | familiar existing aggregate paths; leaf paths improve | explicit but long (`application`, `serialization`, `seedemu_integration`) | concise task/concept imports; less layer information |
| File fragmentation | low–medium | highest; mitigated by family files rather than one class per Specification | medium; root has many small modules |
| Circular-import risk | low with separate OrbitParameters | low with same dependency direction; AS placement needs careful exports | low with separate OrbitParameters; root facade import order still needs tests |
| Public API change | lowest; root/subpackage facades can stay | high; all examples' submodule imports move | high for defining paths; root class imports can stay |
| Class diagram clarity | improved entity ownership, existing boundary terms remain | strongest mapping from diagram category to directory | strongest mapping from core class name to file, weaker layer mapping |
| Future Simulator extension | retains ambiguous `contracts` | clearest reserved read-only input boundary | clear `simulator_api`, but name may imply Simulator already exists |
| Migration work | low–medium | high | medium–high |

## Technical inclination, not a decision

The evidence separates two decisions that need not be made together:

1. Splitting `domain/satellite.py` and giving generic SEED integration files
   class-matching names is mechanically justified and comparatively low risk.
2. Renaming architectural namespaces (`domain`, `contracts`, `seed`) and
   regrouping root modules is a public API/terminology decision with much wider
   documentation and example impact.

Scheme A is the smallest reversible migration. Scheme B gives the most explicit
long-term boundaries. Scheme C optimizes immediate class discovery. This report
does not select among them; the user/GPT review must decide the preferred
trade-off and alias policy before any source move.
