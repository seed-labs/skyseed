# r114: process worker frozen PID cache

Date: 2026-07-09
Status: fixed; rerun gated

## Goal

- Validate the single operational-clean BPF path for four 240-second horizons.
- Exercise process-owned H1 maintenance without blocking H0 runtime epochs.

## Findings

- Startup, compose, H0 BPF preparation, and all 25 H0 runtime epochs succeeded.
- H0 transitions were about 2.5-5.5 seconds; Linux G/Z fallback, eligible Linux
  fallback, and total fallback stayed at zero.
- The primary failure was `horizon_maintenance_ready_missing_before_boundary`
  at H0 -> H1. The process worker did not finish during the 240-second H0
  window.
- The later `missing canary interface netns:stl-940041:fs0040` was secondary:
  the main process had already begun fail-closed teardown while the worker was
  still running.
- Unlike the successful r93 in-process maintenance, the exec-based worker lost
  the parent's populated container PID/netns caches and repeated Docker PID
  discovery across the physical prepare and BPF canary modules.

## Changes

- Process work schema v2 carries the parent's frozen container PID map.
- The host additive-prepare executor and BPF canary consume the same map.
- A missing or malformed PID entry fails directly. The worker cannot perform a
  Docker lookup to supplement its frozen input.
- The pre-long-run companion gate now uses r114 for worker/cache regression
  evidence and r113 for retired-generation lifecycle evidence.

## Validation

- 80 worker/cache/cleanup/host-prepare companion tests pass.
- 445 adjacent runtime queue, profile, host mutation, and preflight tests pass.
- The seven-check companion gate passes against the current canonical 4H
  command while positively identifying the r113 and r114 failures.
- Post-run preflight is clean: zero containers, compose networks, BPF pins,
  direct host interfaces, and runtime locks; about 40 GiB is free.

## Rerun Gate

```bash
PYTHONPATH=satellite:satellite/apps \
python satellite/apps/pre_long_run_contract_check.py \
  --candidate-profile-run-dir /tmp/skyseed-r115-gate/r115-canonical-dry-run \
  --worker-failed-run-dir satellite/output/validation_profiles/r114-4h-single-path-clean-worker \
  --cleanup-failed-run-dir satellite/output/validation_profiles/r113-final-continuous-4h \
  --expected-horizon-count 4 \
  --expected-entries-per-horizon 25
```

## Watch Next

- Confirm the H1 worker publishes ready before the boundary and records a
  nonempty frozen PID count without Docker PID discovery.
- Confirm cleanup starts only after the corresponding H retires.
- Keep all Linux MPLS/fallback counters at zero.
