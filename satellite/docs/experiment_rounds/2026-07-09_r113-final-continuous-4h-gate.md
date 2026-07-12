# r113: final continuous 4H clean rerun gate

Date: 2026-07-09
Status: failed before H1 boundary; fix applied and cheap replay added

## Goal

- Run the active 4H operational-clean BPF profile after fixing r112's
  offline-detectable command/queue contract failures.
- Validate the real continuous horizon lifecycle: H0 startup, H1/H2/H3 worker
  prepare/ready consumption, runtime queue coverage, and clean BPF runtime.

## Command

```bash
env PYTHONNOUSERSITE=1 \
  PYTHONPATH=/home/seed/skyseed/satellite:/home/seed/skyseed \
  /home/seed/miniconda3/envs/seed310/bin/python \
  /home/seed/skyseed/satellite/apps/run_skyseed_validation_profile.py \
  --run-root satellite/output/validation_profiles \
  --run-id r113-final-continuous-4h \
  --profile rich_s4_operational_clean_bpf \
  --format json
```

## Gate

- r112 failure 1: `_run_live_temporal_runtime` referenced out-of-scope
  `normal_runtime_inputs`. Now covered by a focused test that forbids live
  runtime from reaching back to that freeze/precompose variable.
- r112 failure 2: `loop_count=4` did not imply `--continuous`, so only one
  25-entry segment was frozen. Now active profile sets `continuous=true`,
  command generation emits `--continuous`, multi-horizon long-run profiles
  require continuous mode, and runtime queue coverage checks horizon scope.
- r112 artifact replay with `--expected-horizon-count 4` now fails locally as
  `PREPARED_HORIZON_SCOPE_COUNT_MISMATCH`, proving this class is caught before
  compose.
- Session check reports `long_real_run_ready=True`,
  `operational_iteration_ready=True`, no operational warnings, and contract
  shape includes `continuous=true`, `loop_count=4`.

## Validation Before Launch

- Compile/import checks passed for touched runtime queue, runner, profile,
  session, and audit modules.
- Focused/profile/session/runtime queue tests passed.
- Adjacent regression passed: 445 tests.
- `git diff --check` passed.
- Direct command rendering confirms `--continuous` and `--loop-count 4`.

## Watch Points

- Precompose must report four prepared horizon segments before compose.
- Runtime queue coverage must pass horizon scope, queue context, BPF generation
  slot, and route identity coverage.
- H1/H2/H3 maintenance ready artifacts must be consumed only at runtime
  boundaries.
- Forbidden work remains zero: fallback, full reload, full scan, live diff,
  hidden repair, runtime route self-repair.
- Ping observer remains observation only.

## Result

- The run used the intended 4H continuous command and passed the r112 class of
  precompose checks:
  - `frozen_prepared_horizon_segments.json` contained `H0..H3`.
  - `runtime_queue_route_identity_coverage.json` reported 4 expected horizons,
    100 runtime-applicable records, 100 generation-slot records, and no
    blockers.
- Compose completed and container readiness passed with 458 running containers.
- H0 startup consumed the fresh prepared handoff, completed host prewarm,
  current-horizon host install, and pre-runtime BPF prepare.
- Runtime completed 6 H0 runtime transactions successfully with fallback,
  Linux G/Z fallback, and eligible fallback counters at 0.
- Runtime also exposed a cleanup lifecycle bug before the H1 failure:
  H0 generation cleanup was considered after intra-H0 generation flips. The
  first candidate, H0 generation 0, was blocked by active/preloaded overlay
  references; H0 generations 1..4 were queued. These should not be cleanup
  candidates while H0 is still the current active horizon.
- The run failed closed before the H1 boundary:

```text
horizon_maintenance_failed_before_boundary
H1 horizon_maintenance_process_worker
IsADirectoryError: [Errno 21] Is a directory: '.'
```

## Diagnosis

- This was not a dataplane, container, BPF semantic, or ping failure.
- H1 process worker work items had no item-local `context.topology_path`.
  That is acceptable only if the worker has a single authoritative physical
  path source.
- The worker's context compile path previously built `Path("")`, which becomes
  `.`. That let a missing path contract escape focused tests and fail only
  after compose/runtime startup.
- The right contract is: process worker physical files come from the process
  work payload (`topology_path`, `link_state_path`) only. Work items do not
  choose their own topology path, and an absent payload path fails immediately.
- The profile wrapper also exposed a reporting gap: the underlying runner wrote
  `aggregate_runtime_succeeded=false` but returned process exit code 0.
- The retired generation cleanup bug was a horizon lifecycle error: cleanup was
  keyed to generation flip, but it must be keyed to horizon retirement. A
  previous generation inside the current active H is not retired.

## Fix Applied After r113

- `horizon_maintenance_process_worker.py` now requires payload
  `topology_path` and `link_state_path` to be existing files before running.
- Worker compile-from-context now uses the payload topology path as its only
  physical path source.
- Worker tests now create real topology/link-state files instead of using
  placeholder strings.
- Added coverage for the r113 shape: item context has snapshot/runtime plan but
  no item-local path; worker must use the payload topology path.
- Added negative coverage for missing payload physical paths.
- Replayed the actual r113 H1 process work artifact offline with execution
  functions mocked: 25 preload work items produced a ready result and used
  `replenished_segments/segment_001/config/topology.json`, not `.`.
- `run_starlink_shell_experiment.py` now returns nonzero for real-runtime runs
  that produce a runtime report with `aggregate_runtime_succeeded=false`.
- Retired BPF generation cleanup enqueue now first checks the candidate's
  horizon against the active readiness horizon. If the candidate belongs to the
  current active horizon, it is not retired and is not queued or recorded as
  blocked.
- Removed the unused live-loop retired cleanup consumer so cleanup has one
  active consumer path: horizon maintenance process workers.

## Validation After Fix

- `python -m py_compile` passed for the worker, runner, and profile wrapper.
- Regression suites across horizon maintenance, event timeline runtime queue,
  runtime queue coverage audit, validation profile, and session check still
  pass, but they are not readiness evidence for this failure class by
  themselves.
- The meaningful gate for this failure is the worker process contract coverage:
  payload physical paths must be existing files, compile-from-context must use
  the payload topology path as its only path source, and missing payload paths
  fail immediately.
- The meaningful replay for this failure is the r113 H1 process work artifact
  replay without starting containers: 25 preload work items produced a ready
  result and used
  `replenished_segments/segment_001/config/topology.json`, not `.`.
- The meaningful cleanup lifecycle check is:
  candidate horizon `H0` with active horizon `H0` is not retired; candidate
  horizon `H0` with active horizon `H1` is retired.

## Companion Contract Check

- Added `satellite/apps/pre_long_run_contract_check.py` as the offline
  pre-long-run companion check for the current operational-clean flow. It reads
  a prepared/run directory and checks the actual handoffs instead of reporting a
  broad unit-test count.
- The current checks cover: canonical command shape, frozen horizon scope,
  runtime queue route-identity coverage, process-worker payload replay, cleanup
  events against the active H boundary, and cleanup lifecycle semantics.
- Running it against the old r113 artifact intentionally fails on the old H0
  cleanup events while passing worker replay:

```bash
PYTHONPATH=/home/seed/skyseed/satellite \
python satellite/apps/pre_long_run_contract_check.py \
  --candidate-profile-run-dir /tmp/skyseed-r114-gate/r114-canonical-dry-run \
  --previous-failed-run-dir satellite/output/validation_profiles/r113-final-continuous-4h \
  --expected-horizon-count 4 \
  --expected-entries-per-horizon 25 \
  --output satellite/output/validation_profiles/r113-final-continuous-4h/pre_long_run_contract_check.json
```

- This is the desired shape for a rerun gate: the old contaminated artifact is
  positively identified and rejected, while the current command and fixed
  producer/consumer contracts pass without starting containers.

## Active-Path Removal After r113

- Deleted the standalone runtime-attempt entry and moved its required snapshot,
  slice-context, and compose-cleanup responsibilities into the canonical runner.
- Deleted the Iteration 2 demo runner, example-topology builder, small-scale
  config, old validation bundle, and their current README/file-map references.
- Deleted prepare-only, stop-after-container, stop-after-pre-start, and bounded
  bundle-proof CLI routes from both the canonical runner and profile wrapper.
- Deleted the production proof implementations and the tests that existed only
  to exercise those retired routes. Frozen-bundle tests now cover the normal
  operational-clean consumer: identity/hash/queue integrity, typed thaw,
  source binding, missing inputs, and tamper failure.
- Removed the legacy gateway Linux-route readiness branch, Mini Internet NAT
  report field, Seed inventory reuse field, and legacy demo-ID selection guard
  from the active profile path.
- Canonical profile dry-run renders one complete 4H command with continuous
  execution, operational-clean BPF ownership, explicit visualization links,
  and no epoch cap or retired stop/proof flag.
