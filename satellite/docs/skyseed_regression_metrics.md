# Skyseed Regression Metrics Contract

This contract protects the current canonical Skyseed runtime route:
planned-shell `S5` full `1-shell`, `12UT/6GW` valid6 catalogs, 6-zone metadata
catalog, catalog ground placement, event-time RuntimeEpoch/ParameterEpoch
queueing, G/Z FEC diff planning, and direct backend with
`dynamic_satellite_link=0`.

The baseline is extracted from:

```bash
satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824
```

No new real-runtime run is required to use the checker.

## Commands

Extract metrics from a finalized run:

```bash
PYTHONPATH=satellite:satellite/apps \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/extract_skyseed_regression_metrics.py \
  --run-dir satellite/output/starlink_shell_experiments/<run_id> \
  --format text
```

Check against the committed baseline:

```bash
PYTHONPATH=satellite:satellite/apps \
  /home/seed/miniconda3/envs/seed310/bin/python \
  satellite/apps/check_skyseed_regression.py \
  --run-dir satellite/output/starlink_shell_experiments/<run_id> \
  --baseline satellite/baselines/skyseed_1shell_6z_2h_regression_baseline.json \
  --format text
```

The checker returns nonzero only for hard failures. Warnings are performance or
artifact-growth regressions that should be investigated before treating the run
as a new baseline.

## Timing Terms

- Event-time is the simulated schedule time carried by the temporal plan. It
  decides when a RuntimeEpoch or ParameterEpoch should activate in the scenario,
  but it is not host elapsed time.
- Wall/monotonic time is real execution duration measured on the host while the
  run applies state, waits for readiness, writes artifacts, validates, and
  rolls back. All p50/p95/max regression timing metrics use this duration
  family.
- `RuntimeEpoch` means a topology or forwarding-changing epoch. Its steady
  timing excludes the first runtime epoch because the first epoch includes
  initial materialization work.
- `ParameterEpoch` means a metadata/parameter refresh epoch that must stay
  lightweight: no topology mutation, forwarding mutation, node-local full apply,
  hidden recovery fallback, or escalation to RuntimeEpoch semantics.
- Initial materialization is the first real data-plane realization after cold
  startup. It is reported separately as `initial_materialization_seconds` and
  is not part of steady RuntimeEpoch p50/p95/max.
- A steady epoch is any RuntimeEpoch after initial materialization, or any
  ParameterEpoch row whose `epoch_type` is explicitly `parameter_epoch`.
- Scheduled activation lag is `actual_epoch_start - scheduled_activation_time`.
  The raw elapsed scheduled activation mark may be negative because it is stored
  relative to actual epoch start; the contract checks that this event-time mark
  matches the positive wall/monotonic lag instead of treating it as duration.
- Data-plane ready lag is the real elapsed time from epoch start until the data
  plane is ready for that epoch.
- Post-data-plane scheduler blocking is real elapsed time spent after data-plane
  readiness before the scheduler can complete the epoch bookkeeping path.
- Artifact write blocking is real elapsed time spent writing epoch reporting
  artifacts on the critical path.
- Rollback/cleanup combines node-local rollback, host rollback, host artifact
  cleanup, and post-run preflight wall time. It is a final teardown metric, not
  a steady-epoch metric.

## Hard Failures

- semantic success gates fail: real apply, ping, rollback, temporal audit, G/Z
  audit mode, `dynamic_satellite_link=0`
- ParameterEpoch hides full runtime apply, topology/forwarding mutation,
  node-local full apply, or recovery fallback
- full-core reinstall appears in the G/Z FEC diff
- timing marks are missing, negative, or mix event-time with monotonic elapsed
  duration
- RuntimeEpoch and ParameterEpoch timing rows cannot be distinguished

## Warnings

- steady RuntimeEpoch p95 exceeds the baseline by more than 25%
- host apply p95 exceeds the baseline by more than 25%
- final rollback/cleanup exceeds the baseline by more than 50%
- run directory size or artifact count exceeds the baseline by more than 25%

Cold startup, initial materialization, RSS, and total wall-clock are reported as
info because they vary with host state and reboot readiness.
