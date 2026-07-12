# r95 Worker/Ping Route Fail-Closed

## Scope

- Run id: `s4-full-h3-240s-runtime-20260708-r95-worker-ping-route`
- Profile: `s4_h3_240s_bpf_owned_clean`
- Purpose: validate process-owned horizon maintenance and classify the
  UT-to-selected-GW ping observer after the UT edge-route lifecycle change.

## Outcome

- `aggregate_runtime_succeeded=False`.
- H0 completed 25/25 runtime epochs; H1 boundary failed closed before consuming
  a ready maintenance artifact.
- Runtime clean invariants held through the completed epochs:
  `fallback_count=0`, `linux_gz_fallback_command_count=0`,
  `eligible_core_fallback_to_linux=0`.
- Post-run cleanup was clean: compose containers/networks, direct backend host
  interfaces, BPF pins, and runtime locks were all 0.

## Findings

- Horizon maintenance process worker failed before H1:
  `InProcessHostRuntimeManager` did not expose
  `restore_prepared_runtime_state`, even though the outer executor and installer
  did. The failure artifact was published and no `ready` artifact was faked.
- Process worker input was only partly compact:
  `horizon_maintenance_process_work.json` was 560MB because it copied full
  `bpf_owned_readiness`; the JSONL work items were 45MB.
- Ping observer split into two semantic buckets:
  233 `Network is unreachable` observations showed UT namespaces without a
  route to the selected GW access IP.
  91 `100% packet loss` observations occurred after a UT route existed, so those
  packets reached the route-selection layer and need GW/BPF return-path
  diagnosis.
- Follow-up artifact audit found that r95 compiled 0 gateway-access
  ingress/egress classifiers for GW-local replies. All 1,712 gateway-side
  reverse classifier rows were on NAT/IX ingress, e.g. `netns:gw207:ix206`
  forwarding to `netns:gw207:ga-g207s0`. This confirms the remaining ping loss
  is a missing GW-local-origin return handoff, not a reason to add NAT/FORWARD,
  mini-host, or Linux MPLS fallback.

## Fixes After Run

- Added `restore_prepared_runtime_state` to `InProcessHostRuntimeManager` and
  refreshed manager cached state after restore.
- Changed process worker input to use compact attach-capability readiness
  instead of copying full `bpf_owned_readiness`.
- Changed BPF-owned node-local edge route preparation to cover unique active UT
  edge contexts, not only changed/reapplied flows.
- Added a clean GW-local-origin return path for selected-GW ping replies:
  BPF map compilation now emits a `z_to_g_local_origin` classifier on GW access
  egress for `dst=<UT access IP>`, the BPF program allows same-interface egress
  after label push/rewrite, and runtime installs a non-MPLS gateway edge route
  only to send local replies into that BPF egress handoff.

## Validation

- `PYTHONPATH=satellite python -m unittest satellite.test_horizon_maintenance satellite.test_runtime_validation_defaults`
  passed: 198 tests.
- `PYTHONPATH=satellite python -m unittest satellite.test_event_timeline_runtime_queue satellite.test_skyseed_validation_profiles`
  passed: 175 tests.
- Follow-up ping-return fix validation:
  `PYTHONPATH=satellite python -m unittest satellite.test_runtime_validation_defaults satellite.test_rollback_cleanup_optimization satellite.test_event_timeline_runtime_queue`
  passed: 343 tests.
- `compile_mpls_bpf` compiled
  `satellite/experimental/bpf/skyseed_tc_mpls_dataplane.c` successfully after
  the same-interface egress change.
- `python -m py_compile` passed for the edited runtime, worker, app, and test
  files.
- `git diff --check` passed.
- `skyseed_preflight.py --format json` reported `ready=true` after cleanup.
- `skyseed_session_check.py --format json` reported
  `operational_iteration_gate.ready=true`.

## Rerun Gate

- Previous failed stage:
  H1 process maintenance failed before boundary because the real manager layer
  lacked the restore API.
- Cheap checks now covering it:
  process-worker restore test now exercises the real manager layer, compact
  readiness elision is tested, and active UT edge-route dedupe is tested.
- Forbidden-work expectations:
  no fallback, full reload, full scan, live dump/diff, future qdisc preapply, or
  future host visible apply.
- r96 command source:
  `operational_iteration_profile.run_command` from
  `skyseed_session_check.py --format json`.
- r96 run id:
  `s4-full-h3-240s-runtime-20260708-r96-worker-local-origin`.
- r96 exact command:
  `PYTHONPATH=/home/seed/skyseed/satellite /home/seed/miniconda3/envs/seed310/bin/python /home/seed/skyseed/satellite/apps/run_starlink_shell_experiment.py --run-mode real-runtime --run-id s4-full-h3-240s-runtime-20260708-r96-worker-local-origin --run-root satellite/output/validation_profiles --operational-profile s4_h3_240s_bpf_owned_clean --format json`.
- r96 cheap gate:
  `satellite.test_runtime_validation_defaults`,
  `satellite.test_rollback_cleanup_optimization`, and
  `satellite.test_horizon_maintenance` passed 247 tests; `py_compile`,
  `git diff --check`, and `compile_mpls_bpf` passed. The tests cover the real
  manager restore API, compact worker input, active UT edge-route coverage,
  GW-local-origin BPF classifier compilation, same-clean-scope route
  classification, and rollback preservation of active BPF edge routes.
- r96 environment gate:
  `skyseed_session_check.py --format json` reported
  `operational_iteration_gate.ready=true`; scoped preflight was clean with 0
  compose/runtime processes, containers, networks, BPF pins, direct backend host
  interfaces, and runtime locks.
- Accepted remaining risks:
  ping observer remains nonblocking raw observation. The run should validate the
  new GW-local-origin handoff live; if ping still fails, failure evidence should
  move to BPF counters/miss samples rather than UT no-route or missing
  gateway-access classifier artifacts.

## Remaining

- The next run should validate that H1/H2 process maintenance reaches `ready`
  with compact artifacts.
- Ping observer is still nonblocking. After the active UT edge-route fix, any
  remaining failure is expected to move toward `100% packet loss`, which should
  be diagnosed as GW-local return import / BPF handoff rather than UT route
  absence.
