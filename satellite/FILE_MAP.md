# Satellite File Map

This is a concise role guide for the current `satellite/` layout.

## Active Operational-Clean Files

- `design_principle.md`
  - Iteration 2 architecture source of truth
- `status.md`
  - current status snapshot, validation commands, current risks
- `docs/skyseed_1_0_baseline.md`
  - validated pre-refactor 1.0 checkpoint, reproducible prepare/replay/run/audit
    workflow, success criteria, and timing baseline
- `apps/run_skyseed_validation_profile.py`
  - canonical validation-profile runner for the current
    `rich_s4_operational_clean_bpf` real-runtime path
- `apps/run_starlink_shell_experiment.py`
  - underlying planned-shell runtime runner used by the active validation
    profile
- `apps/pre_long_run_contract_check.py` and
  `apps/replay_prepared_runtime_consumer_contracts.py`
  - current expensive-run companion gate and offline ordered replay of frozen
    inputs through the actual runtime/maintenance consumer contracts
- `apps/skyseed_preflight.py`, `apps/prepare_host_mpls.py`, and
  `apps/skyseed_session_check.py`
  - durable startup hygiene, host readiness repair, and new-session handoff
- `apps/inspect_temporal_run.py`, `apps/compare_temporal_runs.py`, and
  `apps/summarize_starlink_long_run.py`
  - normal observation and post-run analysis path
- `contracts.py`
  - contract schemas and validation for `TopologySnapshot`,
    `RuntimeUpdatePlan`, `NetworkMutationPlan`, and related objects
- `seed_integration.py`
  - thin SEED/seedemu integration helpers for mini Internet construction,
    bootstrap routers, UT placeholders, satellite links, MPLS readiness, and
    compiler platform resolution
- `topology_pipeline.py`
  - reusable topology/visibility/zone pipeline abstractions
- `experiment_topology.py`
  - semi-real Iteration 2 experiment scenario, slice generation, and
    Mini Internet integration hints
- `planner.py`
  - planner abstractions, default planner, and UT Internet experiment planner
- `planner_validation.py`
  - planner input/output/flow validation helpers
- `runtime_projection.py`
  - backend-neutral projection from `RuntimeUpdatePlan` to installer intent
- `agent_envelope.py`
  - agent/install envelope abstraction
- `distribution_store.py`
  - distribution/store abstraction for node delivery views
- `node_local_agent.py`
  - node-local agent abstraction
- `installer_runtime.py`
  - dry-run installer backends and installer validation
- `network_mutation.py`
  - `NetworkMutationPlan` building and summarization
- `network_runtime.py`
  - real host-side mutation executor for `NetworkMutationPlan`
- `linux_mpls_runtime.py`
  - strict BPF-owned runtime realization, shared G/Z consumer accounting,
    batching, path observation, and rollback
- `experimental/bpf/skyseed_tc_mpls_dataplane.c` and `bpf_mpls_live.py`
  - active tc-BPF dataplane, kernel-5.4 manual MPLS-pop compatibility path,
    loader, map ABI, counters, and live inspection helpers
- `ground_model.py`, `orbit_data.py`, `starlink_shell.py`,
  `starlink_shell_topology.py`, and `temporal_planning.py`
  - planned-shell, catalog, orbit, zone, and event-time planning support

## Active Config / Output

- `configs/validation_profiles/rich_s4_operational_clean_bpf.json`
  - active operational-clean BPF-owned real-runtime validation profile
- `apps/visualization/planned_shell_orbit_data_s1_s5.json`
  - active planned-shell orbit data source
- `apps/visualization/starlink_gateways.json`
  - active gateway/base-station data source
- `configs/starlink_shell_ground/s4_gz_rich_validation_ut_catalog_32.json`
  and `s4_gz_rich_validation_zone_catalog_16.json`
  - active rich G/Z UT and zone semantic catalogs
- `configs/starlink_shell_ground/deterministic_*_24.json` and
  `deterministic_*_32.json`
  - preview-only catalog studies; not canonical real-runtime input
- `configs/starlink_shell_ground/realistic_preview_*`
  - Goal 18 preview-only realistic workload catalogs and compact summary:
    32UT/12GW/16-zone larger-shell preparation; not canonical real-runtime
    input
## Other Files Still Present

- `docs/skyseed_platform_abstraction_plan.md`
  - Goal 2 boundary audit for semantic intent, IR candidates, backend leakage,
    and future backend attachment points
- `docs/opensn_investigation_report.md`
  - investigation material used as architecture reference
- `docs/archive/satellite_phase2_constellation_design.zh.md`
  - older constellation discussion material; not the current runtime source of truth
- `.skills/`
  - local skill/reference notes used during development
- `AGENTS.md`
  - local development rules for work under `satellite/`

## Temporary / Generated / Residual

- `__pycache__/`
  - Python bytecode cache; safe to ignore
- `output/`
  - SEED compiler output; regenerated on demand
- `configs/starlink_shell_preview*/`
  - generated preview render outputs; ignored
- `data/tle/*`
  - fetched TLE cache payloads; ignored except for the local `.gitignore`

## Practical Reading Order

If you want the current system:

1. `design_principle.md`
2. `README.md`
3. `docs/skyseed_1_0_baseline.md`
4. `FILE_MAP.md`
5. `apps/run_skyseed_validation_profile.py`
6. `apps/run_starlink_shell_experiment.py`
7. `apps/pre_long_run_contract_check.py`
