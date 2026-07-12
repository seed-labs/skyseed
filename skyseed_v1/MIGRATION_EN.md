# Satellite Internet 1.0 Migration

## Governing baseline

- Skyseed tag: `satellite-internet-v1.0-baseline`
- Skyseed commit: `fbb7ff849606b95d2c7ab4399be35656153d5b1c`
- SEED dependency repository: `/home/seed/seed-emulator-upstream-dev`
- SEED branch: `master`
- SEED commit at migration start: `61acdf4c9699b25b06843ec4aad747108b05924e`
- Python: `/home/seed/miniconda3/envs/seed310/bin/python` (3.10.20)

The exact successful route and acceptance facts are recorded in
`docs/migration/baseline_EN.md`.

## Migration rule

Move the still-required implementation into the new ownership boundary. Do
not clean the old tree by deleting files one at a time, and do not preserve a
second active implementation as a compatibility fallback. During construction,
`satellite/` is the baseline executor and `skyseed_v1/` is not canonical. At
cutover, the new route becomes canonical and superseded active code is removed
from the live surface.

## Initial ownership manifest

This is a responsibility-level manifest. Each row is expanded to exact symbols
and tests immediately before that migration slice.

| Current source | Destination ownership | Migration gate |
|---|---|---|
| `contracts.py` | `core/ir/` | serialization, fingerprints, validation results |
| `topology_pipeline.py` | `core/interfaces/`, `models/`, `topology/` | topology contract unit tests |
| `orbit_data.py`, `ground_model.py` | `models/orbit/`, `models/ground/` | catalog and coordinate equivalence |
| `starlink_shell*.py`, `experiment_topology.py` | `topology/`, `models/` | snapshot and preview equivalence |
| `planner.py`, `planner_validation.py` | `planning/routing/` | planner intent and failure tests |
| `temporal_planning.py` | `planning/temporal.py`, `planning/gz_fec/` | ordered event and FEC diff tests |
| `network_mutation.py`, `runtime_projection.py` | `planning/mutations.py`, `compilers/` | plan projection equivalence |
| `distribution_store.py`, `agent_envelope.py`, `node_local_agent.py`, `installer_runtime.py` | `runtime/`, later `services/` | delivery/install contract tests |
| `horizon_maintenance*.py` | `runtime/horizons.py`, `runtime/maintenance.py`, `runtime/worker.py` | worker handoff and lifecycle replay |
| `linux_mpls_runtime.py` | `planning/`, `compilers/`, `runtime/`, `backends/linux/`, `backends/bpf/` | vertical-slice tests; never move whole |
| `network_runtime.py`, `mpls_host.py` | `backends/linux/`, `operations/` | host lifecycle and rollback tests |
| `bpf_mpls_live.py`, active BPF C source | `backends/bpf/` | offline compile, loader, map ABI tests |
| `seed_integration.py` | `adapters/seedemu/` | SEED master adapter tests |
| runtime trace/metrics/inspectors | `observability/` | artifact and timing contract tests |
| return path, queue, regression audits | `audit/` | audit truth-table tests |
| preflight/session/cleanup apps | `operations/`, thin `cli/` | environment detection tests |
| validation profile and catalogs | `examples/s4_operational_clean/`, `data/` | generated command and selection equivalence |
| supported app entrypoints | thin `cli/` plus public use cases | CLI contains no domain/runtime implementation |
| preview/analysis/benchmark apps | `tools/` or example-owned helpers | not imported by core runtime |
| root `test_*.py` | matching `tests/` tier with migrated owner | stable behavior assertion, not old path assertion |
| experiment-round documents | `docs/history/` provenance | never imported or executed |
| ignored output/cache/TLE payloads | remain generated/ignored | no tracked runtime residue |

## Stages

0. Freeze baseline, manifest, validation matrix, and successful route evidence.
1. Establish the installable package skeleton and architecture rules.
2. Migrate core IR, interfaces, and side-effect-free modules with tests.
3. Migrate topology/planning, build the S4 example, and issue acceptance report 1.
4. Split compiler/runtime/backend by vertical execution slices.
5. Land the pure CLI, switch the canonical example, and issue acceptance report 2.
6. Organize audit, observability, operations, tools, data, and test tiers.
7. Pass offline gates, run only required system/long validation, cut over, and
   issue final acceptance report.

Long validation is forbidden before the stage 7 offline gate is green.
