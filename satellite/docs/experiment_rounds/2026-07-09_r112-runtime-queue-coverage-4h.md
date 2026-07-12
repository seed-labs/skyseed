# r112: 4H runtime queue coverage rerun

Date: 2026-07-09
Status: failed before first runtime epoch

## Goal

- Run the canonical 4H operational-clean BPF profile after making frozen
  runtime queue coverage a standard precompose gate.
- Validate the live worker/runtime boundary that cannot be fully proven from
  frozen artifacts alone.

## Rerun Gate

- Previous failed run/stage:
  r111 failed closed at runtime epoch 4 because prepared GW local return route
  evidence was not available to the runtime consumer.
- Cheap checks that now catch the offline-detectable failure class:
  `satellite/apps/audit_runtime_queue_coverage.py` checks frozen queue context
  coverage, BPF generation slot coverage, and route identity producer/consumer
  coverage before compose.
- Adjacent lifecycle states covered:
  frozen queue entry to context, BPF physical generation slot assignment,
  planned/actual route identity evidence, profile artifact contract surfacing,
  and session hygiene.
- Command source:
  `operational_iteration_profile.run_command` from
  `skyseed_session_check.py --format json`.
- Run id:
  `r112-runtime-queue-coverage-4h`
- Forbidden-work expectations:
  no full reload, full scan, live dump/diff, hidden repair, Linux forwarding
  fallback, mini-host fallback, or runtime route self-repair.
- Remaining live risks:
  horizon maintenance worker ready artifacts, real runtime boundary consumption,
  runtime timing, and ping observations still require live evidence.

## Validation Before Launch

- Compile/import checks passed for touched runtime queue, runner, profile, and
  runtime modules.
- Focused runtime queue/profile contract tests passed: 9 tests.
- Adjacent regression passed: 440 tests.
- Combined runtime queue replay passed for r109, r110, and r111:
  25 runtime-applicable entries, 25 BPF generation slot records, slot width 32,
  route identity missing count 0, blockers 0.
- `git diff --check` passed.
- `skyseed_session_check.py --format text` reports
  `long_real_run_ready=True`, `operational_iteration_ready=True`, and no
  operational warnings.

## Watch Next

- Confirm precompose writes
  `runtime_queue_route_identity_coverage.json` with schema
  `skyseed-runtime-queue-coverage-v1` and no blockers.
- Confirm r111 epoch-4 route evidence point is passed.
- At each horizon boundary, check worker prepared artifacts are consumed
  without active context drift, fallback, or hidden repair.
- Compare runtime epoch timing, node-local apply, network mutation, and ping
  observation results against the latest clean baseline.

## Result

- Compose completed and current-horizon host install completed, but the run
  failed before epoch execution.
- First failure:
  `NameError: name 'normal_runtime_inputs' is not defined` inside
  `_run_live_temporal_runtime`.
- Runtime epochs completed: 0.
- This was a code-boundary mismatch, not a container/runtime-only failure. The
  live runtime function reached back to a precompose/freeze variable that is not
  in scope after handoff.
- The pre-run runtime queue coverage artifact reported 25 generation records.
  That is only the current frozen queue/H0 scope; it does not prove future
  H1/H2/H3 maintenance-ready artifacts. This is a coverage-scope gap, not a
  successful full-horizon proof.
- The active validation profile had `loop_count=4`, but the generated shell
  runner command did not pass `--continuous`; `events.jsonl` recorded
  `runtime_started.continuous=false`. Therefore `loop_count` was not actually
  driving a rolling 4H runtime and `frozen_prepared_horizon_segments.json`
  contained only the initial segment.

## Fix After Failure

- Replaced the live pre-runtime route-baseline context source with
  `segment_queue + context_by_key`, the state actually held by live runtime.
- Added a focused contract test that forbids `_run_live_temporal_runtime` from
  reaching back to `normal_runtime_inputs`.
- Added focused tests for queue/context route-baseline construction and
  fail-closed missing context behavior.
- Made the active profile explicitly set `continuous=true`, taught the profile
  command builder to emit `--continuous`, and made long-run profile policy
  reject multiple-horizon contracts without continuous mode.
- Extended runtime queue coverage with `horizon_scope`; replaying r112 with
  expected horizon count 4 now fails locally with
  `PREPARED_HORIZON_SCOPE_COUNT_MISMATCH` instead of waiting for compose/runtime.

## Next Required Gate Update

- Re-run the standard gate with the fixed command shape and confirm the next
  prepared artifact reports four prepared horizon segments before another long
  run.
