# r101: Preflight Slim Freeze Gate

Date: 2026-07-09
Status: validated

## Goal

- Remove obvious prepared-artifact duplication before the next 4H live run.
- Keep the fix outside the live epoch path and preserve operational-clean
  boundaries.
- Re-run a 4H no-compose gate that exercises rolling horizon preparation
  without starting Docker compose.

## Findings

- The r99/r100 prepare-only artifacts showed that size was a process symptom,
  not the whole issue. After preview, the runner spent a long time in hidden
  freeze/materialization work while progress still reported `preview_completed`.
- `normal_runtime_inputs.json` duplicated the same canonical context multiple
  ways:
  - `context_by_segment_key`
  - `runtime_update_plan_by_segment_key`
  - `distribution_batch_by_segment_key`
  - `topology_snapshot_by_segment_key`
  - top-level `context_by_slice`
- The full `prepare_only_no_compose_summary.json` also embedded large gate
  payloads that already existed as separate artifacts.

## Changes

- New normal runtime artifacts now keep `context_by_slice_by_segment` as the
  scoped canonical context and keep the real
  `host_network_mutation_plan_by_segment_key`.
- Per-segment context/runtime-plan/batch/snapshot indexes are derived in memory
  when needed instead of being persisted.
- Top-level `context_by_slice` is no longer written when scoped contexts are
  present; old artifacts remain readable through the fallback path.
- Prepare-only summaries now contain compact counts and artifact references
  instead of copying full gates.
- Minimal progress phases were added for prepare/freeze/materialization so
  expensive prepare work is not mislabeled as preview time.

## Validation

- Focused and adjacent regression tests:
  `445` tests passed across frozen bundle contracts, event timeline runtime
  queue, validation profiles, runtime validation defaults, horizon maintenance,
  and rollback cleanup optimization.
- `py_compile` passed for touched runtime/test files.
- `git diff --check` passed.
- 4H no-compose gate:
  `s4-full-h4-240s-runtime-20260709-r101-preflight-prep-only-slim-context`
  passed with `blocked_before_compose_up=false`, H count `4`, selected/current
  queue count `25`, and blocker count `0`.
- r101 artifact sizes:
  - `normal_runtime_inputs.json`: `318,753,384` bytes
  - `executable_runtime_inputs.json`: `547,641` bytes
  - `prepare_only_no_compose_summary.json`: `3,663` bytes
- r101 timing split:
  - preview: `18.86s`
  - prepare frozen horizon segments: `92.01s`
  - build normal runtime inputs: `33.48s`
  - freeze prepared bundle: `6.12s`
  - externalize executable inputs: `5.07s`
- BPF live role repro passed. Direct, core, and return probes all succeeded
  with miss/drop counters at `0`.
- Session and preflight gates passed after cleaning obsolete failed r97/r98
  output directories. r93 success baseline and r101 gate artifacts were kept.

## Rerun Gate

- Previous failed/wasteful stage: prepared input materialization and prepare-only
  report duplication.
- Cheap checks that now catch it:
  - tests assert normal inputs no longer persist derived context indexes;
  - tests assert compact prepare-only summary keeps counts without event detail;
  - 4H no-compose gate verifies rolling H0-H3 prepared input shape.
- Adjacent lifecycle states covered: freeze producer, externalized executable
  input consumer, normal-runtime thaw, frozen-bundle verification, and
  one-horizon proof path.
- Forbidden-work expectations: no live runtime guard, no full reload, no full
  scan, no fallback, no future active flip, and no report path on the epoch
  update critical path.

## Remaining

- `prepare_frozen_horizon_segments` is now the largest prepare-only phase and
  still deserves structural review. It should be optimized by removing or
  sharing repeated preparation work, not by adding more runtime gates.
- The next live 4H run is still needed to validate process-owned maintenance
  cleanup/preparation across real H boundaries.
