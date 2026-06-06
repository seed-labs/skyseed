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
