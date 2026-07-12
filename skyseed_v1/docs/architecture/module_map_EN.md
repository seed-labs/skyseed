# Module Ownership Map

This map describes the canonical post-cutover source of truth. The v1 project
is standalone: no owner imports or resolves an implementation, input, cache,
lock, preview producer, or output directory from the migration baseline tree.

| Responsibility | Owner |
|---|---|
| stable IR and interfaces | `core/ir`, `core/interfaces` |
| orbit and ground input models | `models` |
| logical topology construction | `topology` |
| route, temporal, and mutation intent | `planning` |
| pure backend-plan translation | `compilers` |
| transaction and experiment lifecycle | `runtime` |
| Linux and BPF mechanisms | `backends` |
| SEED object and layer mapping | `adapters/seedemu` |
| raw execution evidence and metrics | `observability` |
| pass/fail and equivalence evaluation | `audit` |
| host readiness and session lifecycle | `operations` |
| visualization interchange and physical-link exports | `visualization` |
| argument parsing and exit codes | `cli` |

The canonical S4 and S5 compositions are under `examples/`; their reusable
orbit and shell-specific ground catalogs are in `data/`. Neither location
defines platform core behavior. The visualization interchange contract is
documented under `docs/interfaces/visualization/`.

## Import rules

- package code uses absolute `skyseed_v1.*` imports across ownership boundaries;
- no package module imports or resolves the retired migration implementation;
- audits may read IR and observations but may not repair runtime state;
- observability records facts but does not decide acceptance;
- external SEED imports are isolated behind the adapter except where the
  topology builder must instantiate SEED primitives;
- tools and tests are never imported by production modules.
