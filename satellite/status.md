# Satellite Iteration 2 Status

## Current Baseline

- The active system is the Iteration 2 small-scale satellite Internet platform.
- Network construction is still based on SEED/seedemu:
  `apps/build_iteration2_example_topology.py` builds the SEED topology,
  satellite/gateway/UT inventory, and baseline `link_state.json`; shared
  seedemu-facing helpers live in `seed_integration.py`.
- Runtime execution follows the Iteration 2 contract path:
  `TopologySnapshot -> RuntimeUpdatePlan -> NetworkMutationPlan`
  plus node-local Linux/MPLS realization.
- Phase1/controller-driven material is isolated under `legacy_phase1/`.
- Generated outputs, test logs, and Python bytecode caches are not source.

## Active Entry Points

- Build the clean topology:
  `python3 satellite/apps/build_iteration2_example_topology.py`
- Run the Iteration 2 experiment preview or real execution:
  `python3 satellite/apps/iteration2_satellite_experiment.py`
- Run full clean-environment validation:
  `bash satellite/test_bundle_current_network/scripts/run_full_runtime_validation.sh`
- Observe MPLS paths manually:
  `satellite/docs/guides/iteration2_mpls_observation_guide.md`

## Latest Validation

The latest completed full validation was:

- run id: `full_runtime_20260606_093505`
- result: `pass`
- transition count: `5`
- real apply: `success`
- ping: `success`
- rollback: `success`
- cleanup: compose stack stopped and no project-labeled containers/networks
  remained

Important observed `ut1` paths:

- `small-scale-slice-0001`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0002`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0003`: `ut1 -> sat-a2 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0004`: `ut1 -> sat-a2 -> gw2 -> as160-host1`
- `small-scale-slice-0005`: `ut1 -> sat-a1 -> sat-a2 -> gw2 -> as160-host1`

## Current Risks / Cleanup Targets

- `controller.py` remains a transitional host-side mutation boundary used by
  `network_runtime.py`; it should not be deleted until host mutation is moved
  behind a smaller runtime manager.
- `mpls_control.py` remains a transitional/legacy MPLS helper used by
  `controller.py` and legacy scripts.
- `build_topology.py` is now an older broad SEED topology builder retained for
  compatibility/reference; the active small-scale app uses `seed_integration.py`
  for shared SEED helpers.
- `legacy_contract_adapters.py` isolates old controller/link-state projection
  helpers from the canonical contract schema module.
- `docs/network_lower_layer_inventory.md` tracks lower-layer Linux/Docker/MPLS
  mechanics that may belong in seedemu or a shared runtime layer later.

## Minimal Verification After Code Changes

Use these checks after ordinary code cleanup:

```bash
python3 -m py_compile \
  satellite/apps/build_iteration2_example_topology.py \
  satellite/apps/iteration2_satellite_experiment.py \
  satellite/contracts.py \
  satellite/legacy_contract_adapters.py \
  satellite/seed_integration.py \
  satellite/topology_pipeline.py \
  satellite/experiment_topology.py \
  satellite/planner.py \
  satellite/planner_validation.py \
  satellite/runtime_projection.py \
  satellite/agent_envelope.py \
  satellite/distribution_store.py \
  satellite/node_local_agent.py \
  satellite/installer_runtime.py \
  satellite/network_mutation.py \
  satellite/network_runtime.py \
  satellite/linux_mpls_runtime.py
```

Use the full validation after runtime/MPLS/topology changes:

```bash
bash satellite/test_bundle_current_network/scripts/run_full_runtime_validation.sh
```
