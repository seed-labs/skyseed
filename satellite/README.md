# Satellite Directory Guide

This directory is now organized around the active Iteration 2 satellite
Internet platform. Older Phase1/controller-driven material is isolated under
`legacy_phase1/` and generated runtime artifacts are intentionally kept out of
the source tree.

## Main Areas

- `design_principle.md`
  - current Iteration 2 source-of-truth design
- `status.md`
  - running project log and milestone history
- `FILE_MAP.md`
  - concise file-by-file role guide for the current directory layout
- `apps/`
  - supported runnable entrypoints for the current Iteration 2 workflow
- `legacy_phase1/`
  - isolated old Phase1/controller-driven scripts, docs, and baseline configs
- `docs/`
  - active Iteration 2 architecture notes and supporting investigations
- `test_bundle_current_network/`
  - current automated validation scripts for host preflight, offline checks,
    and full real-runtime validation

## Active Iteration 2 Mainline

The current end-to-end Iteration 2 path is centered on:

- `experiment_topology.py`
- `planner.py`
- `contracts.py`
- `runtime_projection.py`
- `agent_envelope.py`
- `distribution_store.py`
- `node_local_agent.py`
- `installer_runtime.py`
- `network_mutation.py`
- `network_runtime.py`
- `linux_mpls_runtime.py`
- `seed_integration.py`
- `apps/build_iteration2_example_topology.py`
- `apps/iteration2_satellite_experiment.py`

## Current Stable Full-Shell Route

The current representative Skyseed runtime route is the planned-shell `S5`
full `1-shell` path with `172` satellites, the 6-zone metadata catalog, the
12UT/6GW valid6 ground catalogs, catalog ground placement, event-time
RuntimeEpoch/ParameterEpoch queueing, the G/Z FEC diff planner, and the direct
backend with dynamic satellite links disabled in SEED (`dynamic_satellite_link=0`).

Use these entry points in order:

1. `apps/skyseed_session_check.py` - read the runbook, inspect the worktree,
   and copy the current canonical command profile.
2. `apps/skyseed_preflight.py` - verify no stale runner, compose process,
   scoped container/network, direct-backend interface, or runtime lock remains.
3. `apps/prepare_host_mpls.py` or runner `--host-prepare-policy auto` - repair
   host MPLS/sysctl/bridge readiness after reboot.
4. `apps/run_starlink_shell_experiment.py` - run only the 6-zone metadata
   catalog for real-runtime validation unless the objective explicitly says
   preview-only.
5. `apps/inspect_temporal_run.py`, `apps/compare_temporal_runs.py`,
   `apps/summarize_starlink_long_run.py`, and
   `apps/generate_gz_6zone_2h_figures.py` - inspect finalized artifacts and
   build analysis bundles after runtime completes.

The 24-zone and 32-zone catalogs are preview-only exploration for now. Do not
use them for canonical real-runtime runs until the required-service coverage
and churn issues documented in `docs/known_issues_runbook.md` are resolved.
Generated runtime directories under `output/` and
`test_bundle_current_network/logs/` are evidence artifacts, not source files.

## Source Hygiene Classification

- Stable source code: root runtime modules such as `contracts.py`,
  `experiment_topology.py`, `planner.py`, `network_runtime.py`,
  `linux_mpls_runtime.py`, `ground_model.py`, `orbit_data.py`,
  `temporal_planning.py`, `starlink_shell*.py`, and `apps/run_starlink_shell_experiment.py`.
- Stable docs/runbook: `README.md`, `README.zh.md`, `status.md`,
  `docs/known_issues_runbook.md`, `docs/skyseed_node_role_boundary.md`,
  `docs/iteration2_contract_boundaries.md`, and `apps/README.md`.
- Stable tests: root `test_*.py` files and
  `test_bundle_current_network/scripts/run_full_runtime_validation.sh`.
- Generated output artifacts: `output/`, `legacy_phase1/output/`,
  `test_bundle_current_network/logs/`, preview render directories under
  `configs/starlink_shell_preview*`, runtime-attempt render directories, TLE
  cache payloads, and `__pycache__/`. These stay ignored.
- Exploratory/prototype support: preview builders, deterministic 24/32-zone
  catalog generation, TLE fetching, and goal/return-path/distance inspectors.
  They are useful evidence tools but are not canonical startup commands.
- Temporary/debug residue: Python bytecode caches and one-off generated
  runtime logs. Do not commit them.
- Obsolete/deviated attempts: legacy Phase1 material and old runtime-attempt
  paths are retained as isolated reference or compatibility material, not as
  the stable runtime route.

## Shared / Transitional Files

Some root-level files are still kept here because Iteration 2 currently reuses
them as shared or transitional components:

- `build_topology.py`
  - older broad SEED topology builder retained for compatibility/reference
- `controller.py`
  - shared runtime/control helper used by transitional host-side mutation
- `mpls_control.py`
  - legacy MPLS/control logic still used as reference and by some transitional
    helpers
- `legacy_contract_adapters.py`
  - old controller/link-state projection helpers, isolated from canonical
    contract schemas
- `runtime_update_agent.sh`
  - old runtime-agent-side helper kept for controller compatibility

These are not the desired long-term Iteration 2 architecture, but they are not
purely dead files yet, so they remain at the root for now.

## Output / Config Notes

- `configs/iteration2_small_scale/`
  - clean Iteration 2 topology inventory and link-state baseline
- `output/iteration2_small_scale/`
  - generated on demand by `apps/build_iteration2_example_topology.py`
- `legacy_phase1/configs/`
  - older broad baseline inventory and link-state files

Generated directories such as `output/`, `legacy_phase1/output/`,
`test_bundle_current_network/logs/`, and `__pycache__/` are not source files.

## Legacy Material

If you specifically need the older demo/smoke/controller workflow, start from:

- `legacy_phase1/README.md`

If you specifically need the active Iteration 2 runtime demo, start from:

- `apps/iteration2_satellite_experiment.py`
- `docs/guides/iteration2_mpls_observation_guide.md`
- `docs/network_lower_layer_inventory.md`

For a full clean-environment validation run:

- `test_bundle_current_network/scripts/run_full_runtime_validation.sh`
