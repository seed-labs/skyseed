# Actual Module Dependencies

The companion Mermaid file is `reports/module-dependencies.mmd`. It is derived
from current Python imports, with the three formal examples added as callers.
Arrows point from importer to imported module; it is not an idealized target
architecture.

## Current import direction by layer

| Importing layer/module | Actual internal dependencies | External dependencies |
| --- | --- | --- |
| root package facade | `contracts`, `domain` | none |
| entities/domain | shared `domain.validation` | SEED `Node`, `Router`, Scope/role values |
| specifications | none | standard library only |
| serialization/Loader | `specifications` | PyYAML, pathlib |
| application/Applier | specifications, domain values, contracts, CatalogBuilder, `seed` facade | SEED Emulator/routing layers |
| application/Projectors | domain entities, contracts and identity | none |
| application/CatalogBuilder | Projectors, Catalog contract, Skyseed AS | upstream `AutonomousSystem` type |
| Simulator-facing contracts | Descriptor modules and identity; identity imports `domain.validation` | standard library only |
| SEED integration AS/Base | domain entities/constants/validation | SEED core/layers |
| SEED Compiler integration | domain entities/constants, `seed.metadata`, packaged asset | SEED Docker Compiler internals, PyYAML/stdlib |
| materialization | no Skyseed Python module | subprocess to Docker Compose CLI |
| isolated example | root API, seed facade | SEED Emulator |
| complete example | root API, CatalogBuilder, seed facade | SEED Emulator/routing layers |
| large example | Loader, Applier/result, seed Compiler | none directly beyond those APIs |

## Current cross-layer imports

1. `contracts.identity → domain.validation` crosses from the Simulator-facing
   data boundary into the domain directory for a backend-independent string
   validator. It is not a runtime/container leak, but a reorganizer must move
   or retarget it consciously.
2. Domain entity modules import upstream SEED because `Satellite` and
   `GroundStation` intentionally are the same Node/Router instances. A new
   directory name cannot make these types framework-independent without a
   design change.
3. `catalog_builder → seed.autonomous_system` needs the concrete AS type to
   ignore ordinary upstream AS objects. That is an application-to-integration
   dependency, not a contract-to-integration dependency.
4. `scenario_application` is the intended top-level application composition
   point and therefore imports specifications, application projection,
   contracts, domain values, Skyseed integration and upstream SEED layers.
5. Compiler and materialization have no Python import edge between them.
   Launcher accepts an already-generated Compose path and invokes the CLI.

## Cycle analysis

Static analysis of current internal module imports finds no unavoidable
strongly connected component among defining modules. Package `__init__` files
add re-export edges, but imports and the test suite currently initialize
successfully.

The main potential cycle appears only if `domain/satellite.py` is split poorly:

```text
orbit.py → satellite.py      # Orbit constructs Satellite at runtime
satellite.py → orbit.py      # if OrbitParameters is placed in orbit.py
```

The current code needs runtime classes, not just annotations. `TYPE_CHECKING`
and forward references alone cannot resolve constructor calls and
`isinstance(orbit_parameters, OrbitParameters)`. An acyclic mechanical split is:

```text
orbit_parameters.py
satellite.py → orbit_parameters.py
orbit.py → satellite.py + orbit_parameters.py
shell.py → orbit.py + orbit_parameters.py
```

Alternatively, OrbitParameters can remain with Satellite, preserving one-way
parent-to-child imports at the cost of weaker conceptual placement.

## Import work required by file moves

- Splitting entity files requires updating `domain.__init__`, root
  `skyseed_v2.__init__`, `seed` integration imports, Projectors, application
  code, tests and path assertions. Root user imports can remain spelled the
  same only if root exports are deliberately retained.
- Renaming `contracts` requires Projectors, CatalogBuilder, Applier, root facade,
  tests and documentation to change; root Descriptor imports could remain
  stable independently of the old subpackage.
- Renaming `seed` affects every formal example, Applier, CatalogBuilder, tests
  and docs. Explicit SEED module filenames mainly affect facade/internal direct
  imports, but direct external submodule imports cannot be ruled out.
- Splitting specifications requires a new aggregation decision: either callers
  import each family module, or `specifications.__init__` retains a deliberate
  aggregate surface. The Loader and Applier need all aggregate/family types.
- Moving Loader/Application modules changes the large example's visible public
  paths. Moving Projector/CatalogBuilder changes the complete example only for
  CatalogBuilder.

## Reverse dependencies that should not be introduced by organization alone

The following edges do not exist and a mechanical directory refactor should
not add them:

- domain/entities → contracts, specifications, Loader, application,
  Compiler or materialization;
- specifications → domain entities, SEED, contracts or materialization;
- Loader/serialization → domain entities, SEED, application or Compiler;
- contracts/Simulator-facing data → SEED integration, Compiler,
  materialization, Loader or application;
- SEED integration → specifications, Loader, application, contracts or
  materialization;
- Compiler → materialization/Launcher;
- materialization → domain entities, Descriptors/Catalog, Loader, Applier,
  Compiler or future Simulator.

Moving files while preserving responsibilities should require import-path
updates only. If a candidate layout needs one of these new reverse edges, it is
not a mechanical reorganization and needs separate design approval.
