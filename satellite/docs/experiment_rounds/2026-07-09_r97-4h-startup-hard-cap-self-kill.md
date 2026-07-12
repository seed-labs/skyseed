# 2026-07-09 r97 4H Startup Hard-Cap Self-Kill

## Purpose

- Try a 4H/240s `operational_clean` / `bpf_owned` run to catch rules that were
  accidentally specialized to the earlier 3H shape.
- Exercise the newly process-owned horizon maintenance cleanup path and keep
  ping observation out of the runtime gate.

## Run

- Run id:
  `s4-full-h4-240s-runtime-20260709-r97-worker-cleanup-cycle`
- Command shape:
  `run_starlink_shell_experiment.py --run-mode real-runtime --operational-profile s4_h3_240s_bpf_owned_clean --loop-count 4 --format json`

## Outcome

- The run did not reach runtime epochs, so worker cleanup and H boundary
  consumption were not validated.
- The 4H preview/replenishment path did get past the initial loop-general
  entry point and produced the extra replenished segments.
- Compose startup was still making progress when the startup contract killed
  the run:
  - stage: `satellites-27`
  - completed batches: `81/84`
  - progress ratio: `0.964286`
  - latest container-count delta: `+12`
  - `docker_responsive=True`
  - `seconds_since_progress=0.473615`
  - `resource_deadlock_detected=False`
  - blocker: `STARTUP_HARD_CAP_EXCEEDED`
- Post-run compose cleanup completed; compose project containers/networks were
  `0/0` after cleanup.

## Diagnosis

- This was a self-inflicted startup watchdog failure, not a clean data-plane
  failure and not a 4H horizon-maintenance failure.
- The old startup contract treated `absolute_max_seconds=1500` as a hard kill
  even when Docker was responsive and startup batches were still advancing.
- That violates the expensive-run rule: a guard may fail closed on no progress,
  resource deadlock, Docker non-responsiveness, or operator abort, but it must
  not kill an expensive run solely because a cold-starting compose path is still
  progressing past a fixed wall-clock cap.

## Fix

- `absolute_max_exceeded` remains recorded as an observation.
- `hard_cap_exceeded` is now blocking only when the absolute max is exceeded
  and there is no recent responsive progress.
- Stage-timeout classification no longer maps any nonblocking
  `absolute_max_exceeded` observation to `STARTUP_HARD_CAP_EXCEEDED`.
- The operational-clean profile now disables whole-startup elapsed caps:
  `compose_up_timeout_seconds=0`, `startup_absolute_max_seconds=0`, and
  `startup_stall_warning_seconds=0`. Fixed `900s`/`1500s` startup budgets are no
  longer warning or failure evidence; compose startup is supervised by
  per-stage watchdogs, compose/docker failures, resource-deadlock evidence, and
  explicit no-progress evidence.
- `_satellite_subprocess_env` now removes `/home/seed/seed-emulator` from child
  `PYTHONPATH` while preserving the satellite and repo paths, so maintenance
  workers do not inherit the legacy environment override.
- Prepared bundle executable runtime inputs are externalized to
  `executable_runtime_inputs.json`; `prepared_update_bundle.json` and its frozen
  alias now remain a manifest/handoff instead of embedding the large executable
  input payload.

## Validation

- Focused startup contract/profile/env/externalized-bundle tests:
  `PYTHONPATH=/home/seed/skyseed/satellite python -m unittest ...` ran 9 tests,
  OK.
- Adjacent profile/runtime/bundle regression:
  `PYTHONPATH=/home/seed/skyseed/satellite python -m unittest satellite.test_event_timeline_runtime_queue.FrozenPreparedBundleContractTests satellite.test_event_timeline_runtime_queue.EventTimelineRuntimeQueueTests satellite.test_skyseed_validation_profiles.SkyseedValidationProfileTests satellite.test_runtime_validation_defaults.RuntimeValidationDefaultTests`
  ran 369 tests, OK.
- Broader related gate:
  `PYTHONPATH=/home/seed/skyseed/satellite python -m unittest satellite.test_runtime_validation_defaults satellite.test_horizon_maintenance satellite.test_rollback_cleanup_optimization`
  ran 258 tests, OK.
- `py_compile` and `git diff --check` passed for the touched files.
- Clean preflight after cleanup:
  environment clean, compose containers/networks `0/0`, runtime locks `0`.
- Session check with clean `PYTHONPATH=/home/seed/skyseed/satellite`:
  operational iteration gate ready with no blockers.

## Remaining

- r97 did not validate process-owned horizon cleanup because runtime never
  started.
- Prepared bundle top-level executable inputs are externalized, but the
  executable input artifact itself can still be large. Per-horizon/per-plan
  artifact sharding and faster hash/materialization remain a follow-up
  scalability optimization.
- Final failure output was printed but no `real_runtime_report.json` file was
  left for this pre-runtime blocker, so blocker persistence should be improved.

## Rerun Gate

- Do not start another long run until the startup/profile/bundle tests above
  remain green and the command is launched with a clean `PYTHONPATH`:
  `PYTHONPATH=/home/seed/skyseed/satellite`.
- The next 4H run should specifically confirm that compose startup continues
  without fixed whole-startup warnings or hard-cap blockers, then reaches
  runtime and exercises H-boundary worker cleanup.
