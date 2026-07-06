# Skyseed App Entry Points

Use this file as the quick boundary between the current stable runtime route
and supporting preview/inspection tools.

## Canonical Runtime Route

- `skyseed_preflight.py` - pre-run and post-run scoped environment check.
- `prepare_host_mpls.py` - host MPLS/sysctl/bridge preparation after reboot
  when preflight reports readiness blockers.
- `skyseed_session_check.py` - durable new-session handoff. Its
  `canonical_fast_long_run_profile.run_command` is the starting point for the
  current representative full-shell route.
- `run_starlink_shell_experiment.py` - canonical planned-shell S5 real-runtime
  runner. The stable real-runtime profile is full S5 1-shell, 6-zone metadata
  catalog, 12UT/6GW valid6 catalogs, catalog ground placement, event-time
  queue, G/Z FEC diff planner, direct backend, ping/timing/audit/rollback, and
  post-run preflight.

Do not use 24-zone or 32-zone catalogs for real-runtime validation from this
route. They are preview-only until their required-service coverage gaps and
multi-zone churn are fixed.

## Preview-Only Planning

- `build_starlink_shell_preview.py` - non-real preview artifact builder.
- `visualization/` - 给外部可视化模块使用的原型包，主工具输出
  `groundLinks` payload，默认使用 `starlink_gateways.json`，另带可选的
  `satelliteLinks` companion tool。
- `build_starlink_shell_preview_matrix.py` - exploratory matrix runner for
  planned-shell sizes and catalogs.
- `generate_starlink_zone_catalog.py` - deterministic preview catalog generator
  for 24/32-zone studies.
- `plan_realistic_skyseed_workload.py` - Goal 18 preview-only realistic
  workload/catalog generator for the 32UT/12GW/16-zone larger-shell preparation
  plan. It writes compact config/summary JSON under
  `configs/starlink_shell_ground/` and does not run real-runtime. Its default
  `S1` analysis is a high-pressure upper-tier preview; staged acceptance should
  try the intermediate S4/S3 tier before any 1000+ satellite runtime.
- `fetch_starlink_tle.py` - optional data-cache helper for real-catalog preview
  studies. Cached TLE payloads under `satellite/data/tle/` are ignored.

These tools may write large artifacts under `satellite/output/` or ignored
preview config directories. Keep their outputs out of commits unless a future
task explicitly asks for a lightweight summary artifact.

## Analysis And Inspection

- `inspect_temporal_run.py`, `compare_temporal_runs.py`, and
  `summarize_starlink_long_run.py` are the normal artifact inspection path.
- `extract_skyseed_regression_metrics.py` and `check_skyseed_regression.py`
  extract/check compact regression metrics from finalized run artifacts. They
  are offline guards for timing, ParameterEpoch, FEC-diff, rollback/cleanup,
  and artifact-growth regressions.
- `generate_gz_6zone_2h_figures.py` builds the current 6-zone 2h analysis
  figure bundle from finalized run artifacts. It is offline and must not be run
  during real runtime.
- `inspect_return_path_*`, `inspect_gz_return_semantics.py`,
  `inspect_goal_readiness.py`, `inspect_goal_evidence.py`, and
  `inspect_starlink_distance_matrix.py` are evidence/debug inspectors. Use them
  to explain planner/runtime behavior, not as canonical startup commands.
  They intentionally remain in `apps/` because tests and
  `skyseed_session_check.py` import them directly.

## Legacy Or Transitional

- `iteration2_satellite_experiment.py` and
  `run_starlink_shell_runtime_attempt.py` remain useful for focused validation
  and compatibility paths, but they are not the preferred full-shell
  real-runtime entry point.
