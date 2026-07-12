# r107 follow-up: visualization sources and cap removal

Date: 2026-07-09
Status: fixed, not rerun

## Goal

- Reconstruct why r107 did not validate the intended 4H run.
- Remove the stale capped route that cut the run before H-boundary worker
  evidence.
- Make active S4 orbit and gateway inputs come from `apps/visualization/`.
- Delete or update active-surface references to retired realistic/canonical
  launch paths.

## Findings

- r107 exited with aggregate runtime success, but it is not valid 4H evidence:
  `profile_run_summary.json` shows the underlying command still carried
  `--max-parameter-epochs 1` and the old gateway catalog.
- r107 wrote only 22 `epoch_execution_details.jsonl` rows, all for segment
  `initial`. It did not enter H1/H2/H3, so it did not prove rolling maintenance
  process handoff, retired-generation cleanup, or loop-general H4 behavior.
- `runtime_ping_observation.jsonl` was empty in r107, so this run adds no ping
  observer evidence.
- The active session handoff and some docs still exposed retired canonical,
  realistic preview, and 1-shell goal-readiness paths, which could seed another
  wrong launch.

## Changes

- The only validation profile is `rich_s4_operational_clean_bpf`; it now has no
  runtime, parameter, or all-epoch caps and the long-run contract forbids those
  caps.
- Profile command rendering now passes
  `satellite/apps/visualization/planned_shell_orbit_data_s1_s5.json` as
  `--orbit-data` and `satellite/apps/visualization/starlink_gateways.json` as
  `--gateway-catalog`.
- Planned-shell preview construction reads the visualization orbit catalog and
  filters S4 from its 348 S4 records instead of regenerating planned-shell
  records through a retired local path.
- `ground_model.load_gateway_catalog` normalizes visualization gateway entries
  to the existing `gw201..` runtime IDs so current G/Z zone references remain
  intact while coordinates/source data come from visualization.
- `skyseed_session_check.py` now exposes only the current 4H validation-profile
  command and reads live `skyseed_preflight.py` directly. The old 1-shell
  canonical profile and old goal-readiness import path are removed from the
  active session handoff.
- Active docs were updated to point to the S4 4H validation profile and
  visualization data sources. The retired realistic workload plan doc and
  retired generator/catalog references were removed from the active surface.
- Old 1-shell goal evidence/readiness apps and their obsolete tests were
  deleted instead of left as hidden alternatives.

## Validation

- `py_compile` passed for the touched runner, session-check, preview, and
  ground-model modules.
- Focused tests passed: `287 tests OK` for session check, validation profile,
  replay equivalence, runtime defaults, and export link tests.
- Profile list returns only `rich_s4_operational_clean_bpf`.
- Profile dry-run renders S4, `loop-count 4`, visualization orbit/gateway
  inputs, and no `--max-runtime-epochs`, `--max-parameter-epochs`, or
  `--max-epochs`.
- Active-surface scans for retired launch/profile/generator names are clean for
  the current handoff, profile, README, runbook, and file map.
- `skyseed_session_check.py --format json` now reports
  `operational_iteration_gate.ready=true` with the current 4H profile command
  and no 1-shell goal-readiness payload.
- `git diff --check` passes.

## Rerun Gate

- Previous failed run/stage: r107 was a launch/profile correctness failure, not
  a data-plane runtime failure.
- Cheap check that now catches it: profile dry-run shows the exact underlying
  command, including visualization sources and absence of epoch caps; profile
  contract tests reject any reintroduced long-run cap.
- Adjacent lifecycle states covered: profile selection, command rendering,
  visualization orbit parse/filter, visualization gateway normalization,
  session handoff, and replay-equivalence labels.
- Command source: use `skyseed_session_check.py` or
  `run_skyseed_validation_profile.py --profile rich_s4_operational_clean_bpf`
  with a new run id.
- Forbidden-work expectations: no capped profile, no retired realistic gateway
  catalog, no planned-shell preset regeneration path, no old canonical session
  command, no old 1-shell goal-readiness handoff.

## Remaining

- No new long run was started in this round by request.
- A real 4H compose/runtime run is still required to validate H1/H2/H3 worker
  maintenance, retired-generation cleanup, and post-fix ping behavior.
- Generic non-current runtime modes such as `fast-demo` still exist outside the
  current validation-profile route; broader module refactoring can decide
  whether to delete those modes entirely.
