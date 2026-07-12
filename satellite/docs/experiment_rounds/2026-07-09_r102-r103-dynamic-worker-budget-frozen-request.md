# r102-r103: Dynamic Worker Budget And Frozen Requests

Date: 2026-07-09
Status: fixed

## Goal

- Use the r102 4H failure evidence to fix the H1 process-maintenance worker
  before spending another live run.
- Keep operational-clean semantics: no fallback, no full reload, no full scan,
  no future active flip, and no worker-side context recompilation in clean mode.

## Findings

- r102 reached H0 runtime and completed 24 epochs, then failed closed before the
  H1 boundary.
- Runtime cleanliness stayed intact: fallback counters were zero, post-run
  preflight was clean, and H0 steady transition timing was near the clean
  baseline.
- The H1 process work input already carried a dynamic resident attach budget
  with `max_attach_interfaces=708`.
- The actual attach prepare report consumed a default budget of `256`, saw
  `302` attach-eligible interfaces, and failed with
  `bpf_resident_attach_coverage_budget_exceeded`.
- The worker work-items artifact still carried context/snapshot/runtime-plan
  payloads instead of frozen compiled inputs, so the process worker could
  reconstruct generation work and touch live namespace/ifindex state.

## Changes

- Frozen manifest proofs now carry `resident_attach_coverage_budget` and
  `bpf_attach_budget`, so frozen attach prepare consumes the dynamic budget.
- The process worker now fails closed if a prepared run has a resident budget
  but attach prepare reports a default or smaller budget.
- The process worker no longer compiles from context when clean work items mark
  `process_worker_compile_from_context_allowed=false`.
- The runner now freezes generation compiled inputs before building process
  preload work items, and the work item producer requires frozen inputs instead
  of serializing context for worker-side recompilation.
- Offline horizon-maintenance audit now flags terminal ready/failure artifacts
  where process work had a dynamic budget but attach prepare used the default.

## Validation

- `py_compile` passed for the touched runner, worker, runtime, audit, and tests.
- Focused tests passed:
  - horizon maintenance contracts: `27`
  - horizon/event queue contracts: `147`
- Broader related regression passed: `451` tests across frozen bundle, event
  queue, validation profiles, runtime defaults, horizon maintenance, and
  rollback cleanup optimization.
- `git diff --check` passed.
- r102 offline audit now catches the old failure as
  `process_worker_attach_prepare_resident_budget_not_consumed`.
- r103 4H no-compose gate passed with blockers `0` and dynamic rolling budget
  `max_attach_interfaces=633`. This validates precompose budget derivation and
  4H prepared-input shape, but it does not exercise the live H1 process worker.

## Rerun Gate

- Previous failed run/stage: r102 H1 process-owned horizon maintenance,
  `bpf_attach_capability_prepare`.
- Cheap checks that now catch it:
  - unit test proves frozen attach prepare receives dynamic resident budget;
  - unit test proves process work items require frozen inputs and omit context;
  - offline audit catches r102's work-budget/default-report mismatch.
- Adjacent lifecycle states covered: budget producer, frozen manifest proof,
  process work item producer, process worker consumer, failure audit.
- Command source and run id:
  `s4-full-h4-240s-runtime-20260709-r103-preflight-frozen-worker-budget`
  used `--prepare-only-no-compose`.
- Forbidden-work expectations: worker remains fail-closed; no fallback, no full
  reload, no live full map dump/diff, no active flip before boundary.

## Remaining

- A live 4H run is still required to validate process-owned maintenance across
  the H1/H2/H3 boundaries.
- r103 no-compose did not create live process worker artifacts, so the next live
  run should explicitly inspect `horizon_maintenance_process_work_items.jsonl`
  for frozen inputs and no context payloads.
- Ping observer rows are still not proven fixed; runtime `ping_success` and the
  raw observer artifact should be evaluated separately after the next live run.
