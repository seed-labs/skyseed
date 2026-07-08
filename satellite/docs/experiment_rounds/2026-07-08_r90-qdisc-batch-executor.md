# r90: Qdisc Batch Executor

Date: 2026-07-08
Status: validated by r93

## Goal

- Reduce runtime hot-path qdisc command churn in the current
  `operational_clean` / `bpf_owned` 3H profile.
- Batch the direct netns host-runtime `tc qdisc` writes without changing the
  runtime boundary semantics:
  - no future-horizon qdisc preapply
  - no live full scan, live qdisc diff, hidden reconcile, or fallback path
  - failure is fail-closed rather than single-command fallback
- Report shell/process command count separately from qdisc sub-update count so
  r90 can be compared against the r88 baseline.

## Implementation

- Added a direct-host qdisc batch executor using `tc -batch`.
- Parameter update groups can now submit multiple qdisc-required
  `update_slot_parameters` interface updates through one batch.
- Structural lifecycle qdisc work is batched at the existing safe stage:
  - deactivate batches root qdisc deletes before link down / flush / nomaster
  - activate batches root netem replaces after bridge/netns/interface up and
    before the structural probe
  - rebind remains two ordered stages: source delete batch, then target replace
    batch
- The controller-backed transitional installer is not connected to the batch
  executor. The operational-clean host route guard rejects a transitional
  transport if it appears in a real host runtime envelope.

## Reporting

- `qdisc_command_count` now represents shell/process invocations.
- New qdisc accounting fields distinguish sub-update scale from command churn:
  - `qdisc_sub_update_count`
  - `qdisc_batch_command_count`
  - `qdisc_batch_sub_update_count`
  - `qdisc_replace_sub_update_count`
  - `qdisc_delete_sub_update_count`
- Runtime epoch timing now exposes structural qdisc wall and batch counters via
  `runtime_qdisc_*` fields.
- Parameter epoch timing now exposes `parameter_qdisc_*` batch and sub-update
  fields.

## Validation

- `py_compile` passed for touched runtime/report/test modules.
- Focused suite passed:
  - `satellite.test_network_runtime_qdisc_lifecycle`
  - `satellite.test_runtime_validation_defaults`
  - `satellite.test_rollback_cleanup_optimization`
  - `satellite.test_cost_attribution_instrumentation`
  - `satellite.test_timing_analysis`
- r93 completed the full 3H/240s operational validation:
  - 75/75 runtime epochs completed
  - qdisc batch commands: 189
  - qdisc sub-updates: 17,908
  - qdisc wall total: 3.491481s
  - fallback counters: 0

See `2026-07-08_r93-clean-runtime-qdisc-success.md` for the final long-run
evidence and remaining non-qdisc follow-up items.
