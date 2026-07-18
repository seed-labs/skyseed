# Documentation, Diagram and Gate Impact

A path-only reorganization does not change approved concepts, but current
documentation and tests intentionally record real import/definition paths.
Those references must move in the same change; otherwise the architecture
drift gates will either fail or, worse, describe paths that no longer exist.

## Copied formal documents

| Document | Current use of names/paths | Required review after a move |
| --- | --- | --- |
| `docs/architecture/terminology.md` | names conceptual categories **and** formal exposures such as `skyseed_v2.specifications`, `skyseed_v2.seed`, `skyseed_v2.materialization` | update every Public exposure cell; keep term meanings unchanged unless separately approved |
| `docs/architecture/emulator-public-construction-api.md` | contains exact import paths, source file paths, API ownership table, root/submodule exposure, manual review checklist and validation record | highest-impact document: update call-path audits, retained-type table, root/module import statements and all “go to file” paths; preserve historical pre-refactor names as history |
| `docs/architecture/conceptual-class-diagrams.md` | Mermaid primarily uses conceptual/class names; prose mentions `contracts.identity`, integration placement and entity files | class relationships should remain unchanged for a mechanical move; update only real module/path prose and category labels if directory terminology changes |
| `docs/architecture/diagrams.md` | Mermaid mostly shows class names and responsibility flow, not Python module paths | conceptual arrows remain; captions describing Builder/Projector/Compiler/materialization directory ownership need review |
| `docs/CURRENT.md` | records current root-vs-owning-module exposure, `contracts.identity`, `seed.metadata`, boundary terminology and pending status | update current import ownership/path facts; do not mark API approved or Simulator started |
| `AGENTS.md` | requires synchronized exports, diagrams, terminology, CURRENT and tests | likely no path edit unless normative directory terms change, but it is a mandatory checklist for the migration |

## Other repository documents found by path/reference search

The archive intentionally omits large unrelated evidence, but a future source
move must search the whole repository. Current direct module/path references
also occur in:

- `docs/architecture/overview.md`;
- `docs/design/complete-static-emulator.md`;
- `docs/design/generic-static-scenario-construction.md`;
- `docs/design/isolated-satellite-emulator.md`;
- `docs/design/static-ground-station-emulator.md`;
- `docs/evidence/static-ground-station-validation.md`;
- `docs/evidence/s4-scale-batched-compose-materialization-validation.md`.

Migration documentation under `docs/migration/` currently focuses on install,
wheel and runtime commands rather than most source module paths. It still needs
a whole-tree search after the final layout is chosen, because a changed public
import example or wheel inventory can make an otherwise historical command
misleading.

## Examples and README impact

| File | Exact path sensitivity |
| --- | --- |
| `examples/isolated_satellites/main.py` | imports root `OrbitParameters` and `skyseed_v2.seed` Base/AS/Compiler |
| `examples/complete_static_emulator/main.py` | imports root values/Catalog, `skyseed_v2.catalog_builder`, and `skyseed_v2.seed` |
| `examples/large_static_network/main.py` | imports `scenario_application`, `scenario_loader` and `seed` directly |
| three example READMEs | explain layer ownership and call-chain merge points; commands may remain, but import explanations/path links must track the chosen layout |
| `examples/large_static_network/_compose_support.py` | imports materialization values and is outside the requested `*/main.py` copies; it must be included in a real migration search |
| `examples/reference/v1_static_input_adapter.py` | imports current specifications and Loader paths; historical/reference status does not exempt it from import repair |

## Tests that intentionally encode current organization

All 21 test files are included because they import current public paths. The
most path-sensitive gates are:

1. `tests/test_package_import.py`: exact root `__all__`, seed facade import and
   root contraction assertions.
2. `tests/unit/test_architecture_boundaries.py`: AST imports, exact module names,
   no-reverse-dependency assertions, defining `__module__` values and absence of
   retired modules.
3. `tests/unit/test_architecture_traceability.py`: enumerates public modules,
   checks docstrings/diagrams and asserts ApplyResult's exact module.
4. `tests/integration/test_examples_layout.py`: contains literal expected import
   statements for complete and large examples plus path-absence assertions.
5. Every entity/contract/application/integration test imports current
   `domain`, `contracts`, `seed`, `projection`, `catalog_builder`, Loader,
   application or specifications paths.

Tests must be updated to assert the approved **new** dependency direction, not
weakened merely to make moved files import. In particular, retain the gates that
entities do not import Descriptors/contracts, Loader only imports
specifications, Compiler does not create entities, and materialization remains
outside Emulator/Simulator contracts.

## Mermaid concept names versus implementation paths

- `docs/architecture/conceptual-class-diagrams.md` and most class diagrams in
  `docs/architecture/diagrams.md` use real **class names and relations**, but
  not defining Python module paths. A mechanical move should not redraw the
  ownership graph.
- `docs/architecture/emulator-public-construction-api.md` mixes class diagrams
  with exact import and source paths; it requires both textual and diagram
  audit.
- Flow diagrams label conceptual responsibilities such as application service,
  cross-layer descriptor and materialization integration. Directory renaming
  must not silently redefine those categories.
- Design-name → implementation-name history should remain historical. Only the
  “current formal implementation path” portion changes.

## Wheel and packaging checks

`pyproject.toml` uses `packages.find where = ["src"]`, so any valid subpackage
under `src/skyseed_v2` remains discoverable when it has the intended package
structure. A migration must nevertheless verify:

- old moved modules are absent from the wheel unless aliases were explicitly
  approved;
- all new modules and the existing `seed/assets/*.sh` resource are present;
- root and subpackage imports work from an isolated wheel installation;
- examples/tests/docs remain excluded;
- no duplicate old/new package paths accidentally ship;
- `__module__`, pickling/qualified names and documentation links reflect the
  new paths.

## Required synchronized migration checklist

1. source paths and all internal imports;
2. root/subpackage `__init__` exports and the explicit no-alias decision;
3. three formal examples plus reference/support modules;
4. example and project READMEs;
5. terminology Public exposure column;
6. API/import tables and manual review links;
7. Mermaid captions and any path-bearing diagrams;
8. CURRENT, while preserving `PENDING USER REVIEW` and `Simulator: NOT STARTED`;
9. AST/path/import architecture gates and ordinary tests;
10. compileall, focused/full pytest and isolated wheel-content/import audit;
11. migration/evidence documents that contain commands or module paths;
12. a whole-repository stale-path scan before commit.
