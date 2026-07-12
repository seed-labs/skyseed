# r104-r105: Worker-Owned Future Freeze After Additive Prepare

Date: 2026-07-09

## Summary

r104 attempted the 4H `operational_clean` / `bpf_owned` runtime after the
dynamic worker budget fix. It reached runtime, completed the first H0 runtime
epoch successfully, then failed during H1 replenishment before any H1 boundary
consumption.

The failure was not ping-related and not a qdisc regression. The main runtime
loop tried to freeze H1 compiled BPF runtime inputs by live-reading future
interfaces before the process worker had prepared those interfaces:

```text
rolling_horizon_bpf_frozen_compiled_inputs_missing:H1
missing canary interface netns:stl-940225:ua-u2025s0:
Device "ua-u2025s0" does not exist.
```

This violated the intended process-worker split. Future physical interfaces are
owned by horizon maintenance, so future generation BPF map freezing cannot be a
main-runtime live namespace lookup.

## r104 Evidence

- Run directory:
  `satellite/output/validation_profiles/s4-full-h4-240s-runtime-20260709-r104-clean-frozen-worker-budget`
- Result: `aggregate_runtime_succeeded=false`
- Runtime progress: 1 runtime epoch completed, then `runtime_exception`
  immediately after `horizon_replenishment_started`.
- Failure artifact:
  `rolling_horizon_bpf_frozen_compiled_inputs_failure.json`
- Failed records: 25/25 H1 records failed with the same missing future
  interface.
- First runtime epoch ping observer succeeded, so the blocker was maintenance
  freeze ordering, not the selected-GW ping path.
- Cleanup completed cleanly, but was expensive: runtime cleanup about `458s`,
  compose down about `364s`.

## Fix

- In process-worker mode, the main runtime loop no longer live-freezes future
  generation compiled inputs.
- The main loop writes a deferred frozen-input manifest and queues process work
  items with explicit `process_worker_compile_from_context_allowed=true` only
  when frozen inputs are intentionally deferred.
- The process worker now owns the order:
  additive host prepare, context freeze/compile, attach capability prepare,
  inactive generation preload, retired cleanup.
- The worker still fails closed when frozen inputs are missing and context
  compilation is not explicitly allowed.
- The dynamic resident attach budget remains required and is injected into
  worker-frozen inputs and guarded in attach prepare.
- `_json_safe` now serializes prepared context objects via `to_dict()` so worker
  context handoff uses the existing contract shape instead of ad hoc payloads.

## Validation

Passed:

- `py_compile` for the runner, process worker, and updated tests.
- Focused worker tests covering:
  - rejecting missing frozen inputs when context compile is forbidden
  - compiling from context when explicitly allowed
  - publishing ready from already frozen inputs
  - additive prepare before worker-owned context freeze
- Full `satellite.test_horizon_maintenance`: 29 tests.
- Full `satellite.test_event_timeline_runtime_queue` plus horizon maintenance:
  150 tests.
- Docker post-run cleanup check: compose containers `0`, compose networks `0`.

r105 prepare-only gate:

- Run directory:
  `satellite/output/validation_profiles/s4-full-h4-240s-runtime-20260709-r105-preflight-worker-deferred-freeze`
- Command mode: `--prepare-only-no-compose`, loop count 4.
- Result: `ok=true`, `prepare_only_no_compose=true`.
- Coverage: 4H prepared bundle and precompose artifacts still build after the
  worker freeze-order change.
- Limitation: prepare-only does not execute runtime H1 replenishment or create
  live process-worker artifacts. The direct coverage for the r104 failure is the
  focused process-worker and runner helper tests above.

## Next Run Readiness

The r104 blocker has a rule-level fix and focused tests. A new long run should
be a fresh run id, not a continuation of r104. Before launching, restate that
the remaining first-live risk is the H1 process worker after real additive host
prepare; do not treat r105 prepare-only as proof of that live boundary.
