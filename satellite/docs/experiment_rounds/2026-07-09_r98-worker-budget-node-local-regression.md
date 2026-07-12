# 2026-07-09 r98 Worker Budget and Node-Local Regression

## Purpose

- Run a 4H/240s `operational_clean` / `bpf_owned` validation to exercise
  loop-general horizon maintenance rather than another 3H-specialized path.
- Validate the process-owned maintenance worker, retired-generation cleanup
  lifecycle, and clean runtime behavior after r97 removed fixed startup hard
  caps.

## Run

- Run id:
  `s4-full-h4-240s-runtime-20260709-r98-clean-no-startup-caps-worker-cycle`
- Command shape:
  `run_starlink_shell_experiment.py --run-mode real-runtime --operational-profile s4_h3_240s_bpf_owned_clean --loop-count 4 --format json`

## Outcome

- Compose completed and post-run cleanup left compose containers/networks at
  `0/0`.
- Runtime reached H0 and completed `16` runtime epochs, then failed closed
  before the H1 boundary.
- Fallback stayed clean: Linux GZ fallback and eligible fallback were both `0`.
- `runtime_ping_observation.jsonl` had `0` rows, so r98 did not validate the
  UT-to-selected-GW observer.

## Failure

- H1 process worker produced `horizon_maintenance_failure.json` and summary
  `status=failed`, `ready=false`.
- Blockers:
  - `bpf_resident_attach_coverage_budget_exceeded`
  - `inactive_generation_preload` failed for all 25 queued records
- Root cause:
  the precompose rolling budget was correct (`max_attach_interfaces=773` in
  `precompose_bpf_role_union_gate.json`), but
  `horizon_maintenance_process_work.json` did not carry that budget into the
  process worker. The worker therefore fell back to the default attach budget
  `256` and rejected a valid H1 attach scope of `288`.

## Runtime Regression

- r93 baseline steady runtime:
  - transition avg about `6.60s`
  - node-local apply avg about `0.235s`
- r98 H0 runtime:
  - transition avg about `52.09s`
  - node-local apply avg about `41.19s`
  - qdisc stayed small, avg about `0.19s`
- Root cause:
  BPF-owned edge route preflight state was not persisted in the active session.
  The clean BPF preflight intentionally avoided live probing, but it always
  reported `ut_default_route_present=false`; runtime therefore generated UT
  default route and gateway local-return route replacements on every epoch.

## Startup Regression Evidence

- Compose was no longer self-killed by a fixed hard cap, but pre-compose
  materialization was still heavy.
- r98 wrote `executable_runtime_inputs.json` at about `+873s` from preview
  completion and the file was about `1.76GB`.
- The first mitigation wrote the external executable runtime inputs in compact
  JSON, but that only reduced formatting overhead.
- The structural fix is to keep `normal_runtime_inputs` as a separate prepared
  artifact and store only a hash/path reference in `executable_runtime_inputs`.
  Loaders hydrate the reference for existing consumers, and repeated loads in
  one process use a path/mtime/size cache.

## Fix

- Process worker compact manifests now receive and preserve
  `resident_attach_coverage_budget` / `bpf_attach_budget`.
- Frozen worker inputs and unioned attach inputs inject the resident attach
  budget before attach capability prepare and inactive preload.
- The offline horizon-maintenance process audit now blocks a process compact
  manifest that has queued preload work but no resident attach budget.
- BPF-owned node-local edge state is now persisted after a successful apply:
  UT default route and gateway local-return route are represented in the next
  session preflight, so subsequent clean epochs do not regenerate the same
  route replacements.
- Clean BPF node-local command scope now distinguishes
  `edge_bpf_endpoint_route` from generic `ip route` work. In BPF-owned clean
  runtime, retained host-required node-local commands are limited to that edge
  scope; satellite-core Linux routes/MPLS work remain BPF-owned/skipped.
- The external `executable_runtime_inputs.json` artifact is now compact JSON
  and no longer embeds `normal_runtime_inputs`; it references the separate
  `normal_runtime_inputs.json` artifact by hash/path.

## Validation

- Focused tests:
  - process worker frozen inputs keep resident attach budget
  - process audit blocks compact manifest missing attach budget
  - BPF edge routes are not reapplied after session state
- Related regression gate:
  `PYTHONPATH=/home/seed/skyseed/satellite:/home/seed/skyseed/satellite/apps python -m unittest satellite.test_horizon_maintenance satellite.test_rollback_cleanup_optimization satellite.test_event_timeline_runtime_queue.FrozenPreparedBundleContractTests satellite.test_event_timeline_runtime_queue.EventTimelineRuntimeQueueTests`
  ran `186` tests, OK.
- Broader clean/runtime gate:
  `PYTHONPATH=/home/seed/skyseed/satellite:/home/seed/skyseed/satellite/apps python -m unittest satellite.test_event_timeline_runtime_queue.FrozenPreparedBundleContractTests satellite.test_event_timeline_runtime_queue.EventTimelineRuntimeQueueTests satellite.test_skyseed_validation_profiles.SkyseedValidationProfileTests satellite.test_runtime_validation_defaults.RuntimeValidationDefaultTests satellite.test_skyseed_session_check satellite.test_horizon_maintenance satellite.test_rollback_cleanup_optimization`
  ran `444` tests, OK.
- `py_compile` passed for the touched runtime, worker, audit, and test files.
- Offline audit against the old r98 artifact now reports the new cheap blocker:
  `process_worker_compact_manifest_missing_resident_attach_budget`.
- Additional focused tests cover clean edge-only node-local scope, skipped
  serving-satellite per-UT Linux delivery routes, disallowed non-edge retained
  host-required routes, and externalized executable inputs that verify/thaw via
  the prepared normal-runtime-input reference.

## Remaining

- Do not start another expensive run until the normal pre-run gate also runs
  the horizon-maintenance process audit on newly prepared artifacts.
- The `normal_runtime_inputs` reference removes the duplicate executable copy
  and one giant executable hash, but it does not yet shard the canonical normal
  input itself. Future work should split it by horizon/epoch if startup remains
  dominated by this artifact after the next cheap prepare audit.
- The next run should prove that H1 worker ready is published before boundary
  and that H0 steady node-local apply returns near the r93 baseline rather than
  the r98 `~41s` regression.
