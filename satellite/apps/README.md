# Skyseed App Entry Points

Use this file as the quick boundary between the current stable runtime route
and supporting preview/inspection tools.

## Canonical Runtime Route

- `skyseed_preflight.py` - pre-run and post-run scoped environment check.
- `prepare_host_mpls.py` - host MPLS/sysctl/bridge preparation after reboot
  when preflight reports readiness blockers.
- `skyseed_session_check.py` - durable new-session handoff. Its
  `operational_iteration_profile.run_command` is the starting point for the
  current representative validation route.
- `run_skyseed_validation_profile.py` - canonical validation-profile entry.
  The active profile is `rich_s4_operational_clean_bpf`.
- `run_starlink_shell_experiment.py` - underlying planned-shell runtime runner
  used by the active validation profile.

## Preview-Only Planning

- `build_starlink_shell_preview.py` - non-real preview artifact builder.
- `visualization/` - 给外部可视化模块使用的原型包，主工具输出
  `groundLinks` payload，默认使用 `starlink_gateways.json`，另带可选的
  `satelliteLinks` companion tool。
- `build_starlink_shell_preview_matrix.py` - exploratory matrix runner for
  planned-shell sizes and catalogs.
- `fetch_starlink_tle.py` - optional data-cache helper for real-catalog preview
  studies. Cached TLE payloads under `satellite/data/tle/` are ignored.

These tools may write large artifacts under `satellite/output/` or ignored
preview config directories. Keep their outputs out of commits unless a future
task explicitly asks for a lightweight summary artifact.

## Analysis And Inspection

- `pre_long_run_contract_check.py` is the companion pre-long-run contract
  check for the active operational-clean flow. It reads a prepared/run artifact
  directory and verifies real module handoffs before another expensive run:
  command shape, frozen horizon scope, runtime queue coverage, worker payload
  replay, and cleanup lifecycle semantics.
- `replay_r134_shared_gz_generations.py` streams the frozen r134 4H corpus,
  replans every snapshot with the current immutable UT policy and shared-G/Z
  compiler, and emits the required 100-generation offline gate report. It never
  consumes historical compiled v1 maps.
- `inspect_temporal_run.py`, `compare_temporal_runs.py`, and
  `summarize_starlink_long_run.py` are the normal artifact inspection path.
- `extract_skyseed_regression_metrics.py` and `check_skyseed_regression.py`
  extract/check compact regression metrics from finalized run artifacts. They
  are offline guards for timing, ParameterEpoch, FEC-diff, rollback/cleanup,
  and artifact-growth regressions.
- `inspect_return_path_*`, `inspect_gz_return_semantics.py`,
  and `inspect_starlink_distance_matrix.py` are evidence/debug inspectors. Use them
  to explain planner/runtime behavior, not as canonical startup commands.
