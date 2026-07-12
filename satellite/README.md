# Satellite Directory Guide

This directory is organized around the active Skyseed operational-clean
satellite Internet platform. Generated runtime artifacts are intentionally kept
out of the source tree.

The validated pre-refactor Satellite Internet 1.0 checkpoint and reproducible
prepare/replay/run/audit workflow are recorded in
[`docs/skyseed_1_0_baseline.md`](docs/skyseed_1_0_baseline.md).

## Main Areas

- `design_principle.md`
  - current Iteration 2 source-of-truth design
- `status.md`
  - running project log and milestone history
- `FILE_MAP.md`
  - concise file-by-file role guide for the current directory layout
- `apps/`
  - supported runnable entrypoints for the current workflow
- `docs/`
  - active architecture notes and supporting investigations

## Active Mainline

The current end-to-end path is centered on:

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
- `apps/run_skyseed_validation_profile.py`
- `apps/run_starlink_shell_experiment.py`

## Current Operational-Clean Route

The current representative Skyseed runtime route is the `S4`
`rich_s4_operational_clean_bpf` validation profile. It uses the visualization
orbit catalog at `apps/visualization/planned_shell_orbit_data_s1_s5.json`,
the visualization gateway catalog at `apps/visualization/starlink_gateways.json`,
the rich G/Z UT and zone catalogs, event-time RuntimeEpoch/ParameterEpoch
queueing, the BPF-owned clean data plane, and 4 rolling horizons at 240 seconds.

Use these entry points in order:

1. `apps/skyseed_session_check.py` - read the runbook, inspect the worktree,
   and copy the single active validation-profile command.
2. `apps/skyseed_preflight.py` - verify no stale runner, compose process,
   scoped container/network, direct-backend interface, or runtime lock remains.
3. `apps/prepare_host_mpls.py` or runner `--host-prepare-policy auto` - repair
   host MPLS/sysctl/bridge readiness after reboot.
4. `apps/run_skyseed_validation_profile.py` - run
   `--profile rich_s4_operational_clean_bpf` for real-runtime validation unless
   the objective explicitly says preview-only.
5. `apps/inspect_temporal_run.py`, `apps/compare_temporal_runs.py`,
   `apps/summarize_starlink_long_run.py`, and
   `apps/generate_gz_6zone_2h_figures.py` - inspect finalized artifacts and
   build analysis bundles after runtime completes.

Generated runtime directories under `output/` are evidence artifacts, not
source files.

## Source Hygiene Classification

- Stable source code: root runtime modules such as `contracts.py`,
  `experiment_topology.py`, `planner.py`, `network_runtime.py`,
  `linux_mpls_runtime.py`, `ground_model.py`, `orbit_data.py`,
  `temporal_planning.py`, `starlink_shell*.py`, and `apps/run_starlink_shell_experiment.py`.
- Stable docs/runbook: `README.md`, `README.zh.md`, `status.md`,
  `docs/known_issues_runbook.md`, `docs/skyseed_node_role_boundary.md`,
  and `apps/README.md`.
- Stable tests: root `test_*.py` files and the flow-level
  `apps/pre_long_run_contract_check.py` companion check.
- Generated output artifacts: `output/`, preview render directories under
  `configs/starlink_shell_preview*`, TLE cache payloads, and `__pycache__/`.
  These stay ignored.
- Exploratory/prototype support: preview builders, deterministic 24/32-zone
  catalog generation, TLE fetching, and goal/return-path/distance inspectors.
  They are useful evidence tools but are not canonical startup commands.
- Temporary/debug residue: Python bytecode caches and one-off generated
  runtime logs. Do not commit them.
Generated directories such as `output/` and `__pycache__/` are not source
files.
